"""
Matrix-based Reconciliation Test Data Generator
Generates NFS, Switch TLF and CBS files where every row's
presence/absence and sign (1/-1/0/null) maps to one of the 64 cases.

Usage:
  from generators.matrix_generator import generate_matrix
  result = generate_matrix(volume=500, ok_pct=99, tran_date=datetime.today())
"""

import random
import json
import zipfile
from datetime import datetime, timedelta
from copy import deepcopy
from pathlib import Path
from typing import Optional

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).parent.parent))
from generators.case_matrix import CASES, CASE_MAP, OK_CASES, NON_OK_CASES
from generators.nfs_settlement import generate_settlement
from generators.nfs_atm import (
    Transaction, make_base_transaction,
    serialize_nfs_row, serialize_switch_tlf_row, serialize_cbs_row,
    fmt_date_cbs, pad_right, pad_left_zero, generate_rrn, generate_stan,
    TRAN_CODE_MAP,
)

BASE_DIR = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────
# ROW BUILDERS — one per (file, sign) combination
# ─────────────────────────────────────────────────────────────

def _make_forward(base: Transaction) -> Transaction:
    """Value=1: standard approved transaction"""
    tx = deepcopy(base)
    tx.msg_type  = "0210"
    tx.tran_type = "W1"
    tx.tran_code = TRAN_CODE_MAP["W1"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _make_reversal(base: Transaction) -> Transaction:
    """Value=-1: reversal entry for this RRN"""
    tx = deepcopy(base)
    tx.msg_type  = "0420"
    tx.tran_type = "RV"
    tx.tran_code = TRAN_CODE_MAP["RV"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)   # stored positive; sign inferred from msg_type
    return tx


def _make_rows(base: Transaction, value) -> list:
    """
    Translate a case value into a list of Transaction rows for one file.
      1    → [forward]
     -1    → [reversal]
      0    → [forward, reversal]
      None → []
    """
    if value is None:
        return []
    if value == 1:
        return [_make_forward(base)]
    if value == -1:
        return [_make_reversal(base)]
    if value == 0:
        return [_make_forward(base), _make_reversal(base)]
    return []


# ─────────────────────────────────────────────────────────────
# AMOUNT / DATE VARIANT HELPERS
# ─────────────────────────────────────────────────────────────

def _apply_variant(nfs_rows, sw_rows, cbs_rows, variant: str, base_amount: int):
    """
    Apply optional data-quality variants to an otherwise case-correct group.
    Variants: 'amount_mismatch_nfs', 'amount_mismatch_cbs', 'amount_mismatch_sw',
              'date_mismatch_nfs', 'date_mismatch_cbs', 'date_mismatch_sw',
              'duplicate_nfs', 'duplicate_cbs'
    """
    if variant == "amount_mismatch_nfs" and nfs_rows:
        nfs_rows[0].amount = base_amount + random.choice([100, 500, 1000, 5000])

    elif variant == "amount_mismatch_cbs" and cbs_rows:
        cbs_rows[0].amount = base_amount + random.choice([100, 500, 1000, 5000])

    elif variant == "amount_mismatch_sw" and sw_rows:
        sw_rows[0].amount = base_amount + random.choice([100, 500, 1000, 5000])

    elif variant == "date_mismatch_nfs" and nfs_rows:
        for r in nfs_rows:
            r.tran_date = r.tran_date + timedelta(days=1)
            r.settlement_date = r.tran_date

    elif variant == "date_mismatch_cbs" and cbs_rows:
        for r in cbs_rows:
            r.tran_date = r.tran_date + timedelta(days=1)

    elif variant == "date_mismatch_sw" and sw_rows:
        for r in sw_rows:
            r.tran_date = r.tran_date + timedelta(days=1)
            r.settlement_date = r.tran_date

    elif variant == "duplicate_nfs" and nfs_rows:
        nfs_rows.append(deepcopy(nfs_rows[0]))

    elif variant == "duplicate_cbs" and cbs_rows:
        cbs_rows.append(deepcopy(cbs_rows[0]))

    return nfs_rows, sw_rows, cbs_rows


# Mismatch variants valid for the 1|1|1 case — all three files present but data differs
CASE_1_MISMATCH_VARIANTS = [
    "amount_mismatch_nfs",
    "amount_mismatch_cbs",
    "amount_mismatch_sw",
    "date_mismatch_nfs",
    "date_mismatch_cbs",
    "date_mismatch_sw",
]

_MISMATCH_LABEL = {
    "amount_mismatch_nfs": ("Amount", "NFS"),
    "amount_mismatch_cbs": ("Amount", "CBS"),
    "amount_mismatch_sw":  ("Amount", "Switch"),
    "date_mismatch_nfs":   ("Date",   "NFS"),
    "date_mismatch_cbs":   ("Date",   "CBS"),
    "date_mismatch_sw":    ("Date",   "Switch"),
}

# Minimum volume: 64 (one per case) + 6 guaranteed 1|1|1 mismatch entries
MIN_VOLUME = 64 + len(CASE_1_MISMATCH_VARIANTS)  # 70

VARIANTS = [
    "none", "none", "none", "none", "none",   # weighted heavily toward clean
    "amount_mismatch_nfs", "amount_mismatch_cbs", "amount_mismatch_sw",
    "date_mismatch_nfs", "date_mismatch_cbs", "date_mismatch_sw",
    "duplicate_nfs", "duplicate_cbs",
]


# ─────────────────────────────────────────────────────────────
# PLANNER — distribute volume across 64 cases
# ─────────────────────────────────────────────────────────────

def plan_volume(volume: int, ok_pct: float = 99.0) -> list:
    """
    Returns list of (case_id, variant) for `volume` transactions.
    ok_pct% go to the 16 OK cases, rest to the 48 non-OK cases.
    All 64 cases guaranteed at least 1 entry (coverage floor).
    Case 1 (1|1|1) also gets one guaranteed entry per mismatch variant (6 extras).

    Strategy: total = max(volume, MIN_VOLUME=70).
    - 1 base row reserved per case (64 rows, coverage floor).
    - 6 guaranteed 1|1|1 mismatch rows (amount/date × NFS/CBS/Switch).
    - Remaining rows split: ok_pct% to OK cases, rest to non-OK cases.
    """
    if volume < MIN_VOLUME:
        volume = MIN_VOLUME

    # Coverage floor: exactly 1 per case = 64 entries
    assignments = {c[0]: 1 for c in CASES}
    # 6 guaranteed 1|1|1 mismatch slots (not counted in ok_pct math)
    guaranteed_111 = [(1, v) for v in CASE_1_MISMATCH_VARIANTS]
    remaining = volume - 64 - len(CASE_1_MISMATCH_VARIANTS)

    extra_entries = []
    if remaining > 0:
        extra_ok  = int(round(ok_pct / 100.0 * remaining))
        extra_nok = remaining - extra_ok
        for _ in range(extra_ok):
            case_id = random.choice(OK_CASES)
            # Case 1 extra volume: 50% clean, 50% across the 6 mismatch variants
            if case_id == 1:
                v = random.choices(
                    ["none"] + CASE_1_MISMATCH_VARIANTS,
                    weights=[6, 1, 1, 1, 1, 1, 1],
                    k=1
                )[0]
            else:
                v = "none"
            extra_entries.append((case_id, v))
        for _ in range(extra_nok):
            case_id = random.choice(NON_OK_CASES)
            v = random.choices(
                ["none", "amount_mismatch_nfs", "amount_mismatch_cbs",
                 "amount_mismatch_sw", "date_mismatch_nfs", "date_mismatch_cbs",
                 "date_mismatch_sw", "duplicate_nfs", "duplicate_cbs"],
                weights=[80, 5, 5, 3, 3, 2, 1, 1, 1],
                k=1
            )[0]
            extra_entries.append((case_id, v))

    # Flatten coverage floor to (case_id, variant) list
    result = []
    for case_id, count in assignments.items():
        for _ in range(count):
            if CASE_MAP[case_id][5]:    # is_ok — base coverage always clean
                result.append((case_id, "none"))
            else:
                v = random.choices(
                    ["none", "amount_mismatch_nfs", "amount_mismatch_cbs",
                     "amount_mismatch_sw", "date_mismatch_nfs", "date_mismatch_cbs",
                     "date_mismatch_sw", "duplicate_nfs", "duplicate_cbs"],
                    weights=[80, 5, 5, 3, 3, 2, 1, 1, 1],
                    k=1
                )[0]
                result.append((case_id, v))

    result.extend(guaranteed_111)
    result.extend(extra_entries)
    random.shuffle(result)
    return result


# ─────────────────────────────────────────────────────────────
# FILE SERIALISATION
# ─────────────────────────────────────────────────────────────

def _write_nfs(rows: list, path: Path) -> int:
    lines = [serialize_nfs_row(r) for r in rows]
    path.write_text("\n".join(lines), encoding="ascii", errors="replace")
    return len(lines)

def _write_switch(rows: list, path: Path, tran_date: datetime) -> int:
    file_seq = str(random.randint(1000, 9999))
    header = f"TH{tran_date.strftime('%y%m%d')}{file_seq}PRO2  TLF{'':40}{file_seq:>10}\n"
    lines = ["DR" + serialize_switch_tlf_row(r) for r in rows]
    path.write_text(header + "\n".join(lines), encoding="ascii", errors="replace")
    return len(lines)

def _write_cbs(rows: list, path: Path) -> int:
    lines = [serialize_cbs_row(r) for r in rows]
    path.write_text("\n".join(lines), encoding="ascii", errors="replace")
    return len(lines)


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def generate_matrix(
    volume: int = 500,
    ok_pct: float = 99.0,
    tran_date: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """
    Generate NFS, Switch TLF and CBS test files covering all 64 recon cases.

    Parameters
    ----------
    volume   : total number of transaction groups to generate (min 64)
    ok_pct   : percentage of groups that are fully reconciled (OK cases)
    tran_date: base transaction date (defaults to today)
    output_dir: where to write output files

    Returns
    -------
    dict with run_id, file paths, row counts, case distribution, manifest path
    """
    if tran_date is None:
        tran_date = datetime.today()
    if output_dir is None:
        output_dir = BASE_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    # Respect user-requested volume exactly. Minimum is 64 (1 per case).
    if volume < 64:
        volume = 64

    plan = plan_volume(volume, ok_pct)

    nfs_rows:    list[Transaction] = []
    sw_rows:     list[Transaction] = []
    cbs_rows:    list[Transaction] = []
    manifest_rows = []
    group_counter = 0

    for case_id, variant in plan:
        case = CASE_MAP[case_id]
        _, nfs_val, sw_val, cbs_val, action, is_ok = case

        group_counter += 1
        group_id = f"GRP{group_counter:06d}"

        # Random time within business hours
        tx_time = tran_date.replace(
            hour=random.randint(8, 21),
            minute=random.randint(0, 59),
            second=random.randint(0, 59),
            microsecond=0,
        )

        base = make_base_transaction(f"case_{case_id}", group_id, tx_time)

        # Build rows per file according to case values
        g_nfs  = _make_rows(base, nfs_val)
        g_sw   = _make_rows(base, sw_val)
        g_cbs  = _make_rows(base, cbs_val)

        # Apply data-quality variant if any
        if variant != "none":
            g_nfs, g_sw, g_cbs = _apply_variant(
                g_nfs, g_sw, g_cbs, variant, base.amount
            )

        # Effective recon outcome: a mismatch variant on an OK case makes it non-OK
        is_111_mismatch = is_ok and variant in CASE_1_MISMATCH_VARIANTS
        effective_is_ok = is_ok and not is_111_mismatch
        if is_111_mismatch:
            kind, file_lbl = _MISMATCH_LABEL[variant]
            effective_action = f"{kind} mismatch — {file_lbl} differs (1|1|1) — investigate"
        else:
            effective_action = action

        # Collect rows
        nfs_start = len(nfs_rows) + 1
        sw_start  = len(sw_rows)  + 1
        cbs_start = len(cbs_rows) + 1

        nfs_rows.extend(g_nfs)
        sw_rows.extend(g_sw)
        cbs_rows.extend(g_cbs)

        # Manifest entry
        manifest_rows.append({
            "group_id":       group_id,
            "case_id":        case_id,
            "nfs_value":      nfs_val,
            "switch_value":   sw_val,
            "cbs_value":      cbs_val,
            "action":         effective_action,
            "is_ok":          effective_is_ok,
            "variant":        variant,
            "rrn":            base.rrn,
            "card_pan":       base.card_pan,
            "amount_paise":   base.amount,
            "amount_inr":     base.amount / 100,
            "tran_date":      tx_time.strftime("%d%m%Y"),
            "nfs_rows":       len(g_nfs),
            "switch_rows":    len(g_sw),
            "cbs_rows":       len(g_cbs),
            "nfs_row_start":  nfs_start,
            "sw_row_start":   sw_start,
            "cbs_row_start":  cbs_start,
        })

    # Write files
    date_str = tran_date.strftime("%d%m%Y")
    run_id   = tran_date.strftime("%Y%m%d") + datetime.now().strftime("%H%M%S")

    nfs_path    = output_dir / f"NFS_MATRIX_{date_str}.txt"
    sw_path     = output_dir / f"t{tran_date.strftime('%y%m%d')}001-_SWITCH_TLF_MATRIX"
    cbs_path    = output_dir / f"EX3198_MATRIX_{date_str}.prt1"
    manifest_path = output_dir / f"manifest_matrix_{run_id}.json"

    nfs_count = _write_nfs(nfs_rows, nfs_path)
    sw_count  = _write_switch(sw_rows, sw_path, tran_date)
    cbs_count = _write_cbs(cbs_rows, cbs_path)

    # Summary stats
    ok_groups          = sum(1 for r in manifest_rows if r["is_ok"])
    mismatch_111_groups = sum(1 for r in manifest_rows if r["variant"] in CASE_1_MISMATCH_VARIANTS and r["case_id"] == 1)
    non_ok_groups      = len(manifest_rows) - ok_groups
    actual_ok_pct      = round(ok_groups / len(manifest_rows) * 100, 2)

    # Case distribution — use structural is_ok/action from CASE_MAP so case 1
    # always shows its base "OK" definition; per-variant counts reveal mismatches.
    case_dist = {}
    for r in manifest_rows:
        cid = r["case_id"]
        if cid not in case_dist:
            struct = CASE_MAP[cid]
            case_dist[cid] = {
                "case_id":   cid,
                "nfs":       r["nfs_value"],
                "switch":    r["switch_value"],
                "cbs":       r["cbs_value"],
                "action":    struct[4],
                "is_ok":     struct[5],
                "count":     0,
                "variants":  {},
            }
        case_dist[cid]["count"] += 1
        v = r["variant"]
        case_dist[cid]["variants"][v] = case_dist[cid]["variants"].get(v, 0) + 1

    manifest_data = {
        "run_id":          run_id,
        "use_case":        "nfs_atm_issuer_matrix",
        "tran_date":       date_str,
        "volume_requested": volume,
        "volume_actual":   len(manifest_rows),
        "ok_pct_requested": ok_pct,
        "ok_pct_actual":   actual_ok_pct,
        "ok_groups":       ok_groups,
        "non_ok_groups":   non_ok_groups,
        "mismatch_111_groups": mismatch_111_groups,
        "files": {
            "nfs":        nfs_path.name,
            "switch_tlf": sw_path.name,
            "cbs":        cbs_path.name,
        },
        "row_counts": {
            "nfs":        nfs_count,
            "switch_tlf": sw_count,
            "cbs":        cbs_count,
        },
        "case_distribution": dict(sorted(case_dist.items())),
        "rows": manifest_rows,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # NFS Settlement file
    settlement_result = generate_settlement(
        manifest=manifest_data,
        bank_name="TEST BANK LTD",
        output_dir=output_dir,
    )
    settlement_path = Path(settlement_result["path"])

    # Zip bundle
    zip_path = output_dir / f"recon_matrix_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(nfs_path, nfs_path.name)
        zf.write(sw_path, sw_path.name)
        zf.write(cbs_path, cbs_path.name)
        zf.write(settlement_path, settlement_path.name)
        zf.write(manifest_path, manifest_path.name)

    return {
        "settlement": settlement_result,
        "run_id":            run_id,
        "zip_path":          str(zip_path),
        "ok_pct_actual":     actual_ok_pct,
        "ok_groups":         ok_groups,
        "non_ok_groups":     non_ok_groups,
        "mismatch_111_groups": mismatch_111_groups,
        "row_counts":        manifest_data["row_counts"],
        "case_distribution": manifest_data["case_distribution"],
        "manifest_path":     str(manifest_path),
    }


if __name__ == "__main__":
    result = generate_matrix(volume=500, ok_pct=99.0)
    dist = result["case_distribution"]
    print(f"\nRun ID: {result['run_id']}")
    print(f"OK: {result['ok_pct_actual']}%  ({result['ok_groups']} groups)")
    print(f"Non-OK: {result['non_ok_groups']} groups")
    print(f"Rows — NFS: {result['row_counts']['nfs']}, "
          f"Switch: {result['row_counts']['switch_tlf']}, "
          f"CBS: {result['row_counts']['cbs']}")
    print(f"\nAll 64 cases covered: {len(dist) == 64}")
    print(f"\n{'Case':>5} {'NFS':>5} {'SW':>6} {'CBS':>5} {'Count':>6}  Action")
    print("─" * 75)
    for cid, c in dist.items():
        nv  = str(c['nfs'])    if c['nfs']    is not None else "null"
        sv  = str(c['switch']) if c['switch'] is not None else "null"
        cv  = str(c['cbs'])    if c['cbs']    is not None else "null"
        ok  = "✓" if c['is_ok'] else " "
        print(f"{ok} {cid:>4}  {nv:>5} {sv:>6} {cv:>5}  {c['count']:>6}  {c['action'][:45]}")
