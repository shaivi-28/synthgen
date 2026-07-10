"""
VISA Matrix-based Reconciliation Test Data Generator
Generates VISA TC file, Switch TLF and CBS files where every row's
presence/absence and sign (1/-1/0/null) maps to one of the 64 cases.

Usage:
  from generators.visa_matrix_generator import generate_visa_matrix
  result = generate_visa_matrix(volume=500, ok_pct=99, tran_date=datetime.today())
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
from generators.matrix_generator import (
    plan_volume,
    _apply_variant,
    CASE_1_MISMATCH_VARIANTS,
    _MISMATCH_LABEL,
    MIN_VOLUME,
)
from generators.nfs_atm import (
    Transaction,
    serialize_switch_tlf_row,
    serialize_cbs_row,
    TRAN_CODE_MAP,
)
from generators.visa_pos import (
    make_base_visa_transaction,
    serialize_visa_record_group,
    generate_arn,
    POS_MERCHANTS,
    CASH_MERCHANTS,
)
from generators.visa_settlement import generate_visa_settlement

BASE_DIR = _P(__file__).parent.parent


# ─────────────────────────────────────────────────────────────
# VISA-SPECIFIC ROW BUILDERS
# ─────────────────────────────────────────────────────────────

def _visa_make_forward(base: Transaction) -> Transaction:
    """
    VISA forward transaction.
    Randomly POS (TC=05) or Cash (TC=07) to add realism.
    Value = 1 in the matrix.
    """
    tx = deepcopy(base)
    # 70% POS, 30% Cash
    if random.random() < 0.70:
        tx.msg_type  = "05"
        tx.mcc = random.choice(POS_MERCHANTS)[2]
    else:
        tx.msg_type  = "07"
        tx.mcc = "6011"
        merch = random.choice(CASH_MERCHANTS)
        tx.terminal_location = f"{merch[0]}{merch[1]}IN "
    tx.tran_type = "W1"
    tx.tran_code = TRAN_CODE_MAP["W1"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_reversal(base: Transaction) -> Transaction:
    """
    VISA reversal: TC=25 (POS reversal) or TC=27 (Cash reversal).
    Matches the forward TC of the base transaction.
    Value = -1 in the matrix.
    """
    tx = deepcopy(base)
    # If base was cash, use TC=27; otherwise TC=25
    if base.msg_type == "07":
        tx.msg_type = "27"
    else:
        tx.msg_type = "25"
    tx.tran_type = "RV"
    tx.tran_code = TRAN_CODE_MAP["RV"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_switch_forward(base: Transaction) -> Transaction:
    """Switch TLF forward row (value = 1): standard approved authorization."""
    tx = deepcopy(base)
    tx.msg_type  = "0210"   # BASE24 response
    tx.tran_type = "W1"
    tx.tran_code = TRAN_CODE_MAP["W1"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_switch_reversal(base: Transaction) -> Transaction:
    """Switch TLF reversal row (value = -1): reversal authorization."""
    tx = deepcopy(base)
    tx.msg_type  = "0420"   # BASE24 reversal
    tx.tran_type = "RV"
    tx.tran_code = TRAN_CODE_MAP["RV"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_cbs_forward(base: Transaction) -> Transaction:
    """CBS forward row (value = 1): customer debited."""
    tx = deepcopy(base)
    tx.msg_type  = "0210"
    tx.tran_type = "W1"
    tx.tran_code = TRAN_CODE_MAP["W1"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_cbs_reversal(base: Transaction) -> Transaction:
    """CBS reversal row (value = -1): customer credited."""
    tx = deepcopy(base)
    tx.msg_type  = "0420"
    tx.tran_type = "RV"
    tx.tran_code = TRAN_CODE_MAP["RV"]
    tx.resp_code = "00"
    tx.amount    = abs(base.amount)
    return tx


def _visa_make_rows(base: Transaction, value, file_type: str) -> list:
    """
    Translate a case value into Transaction rows for a specific file type.
      1    → [forward]
     -1    → [reversal]
      0    → [forward, reversal]
      None → []
    """
    if value is None:
        return []

    if file_type == "visa":
        fwd_fn = _visa_make_forward
        rev_fn = _visa_make_reversal
    elif file_type == "switch":
        fwd_fn = _visa_make_switch_forward
        rev_fn = _visa_make_switch_reversal
    else:  # cbs
        fwd_fn = _visa_make_cbs_forward
        rev_fn = _visa_make_cbs_reversal

    if value == 1:
        return [fwd_fn(base)]
    if value == -1:
        return [rev_fn(base)]
    if value == 0:
        return [fwd_fn(base), rev_fn(base)]
    return []


# ─────────────────────────────────────────────────────────────
# FILE SERIALISATION
# ─────────────────────────────────────────────────────────────

def _write_visa_tc(rows: list, path: Path) -> int:
    """Write VISA T&E clearing file: 4 lines per transaction."""
    all_lines = []
    for r in rows:
        all_lines.extend(serialize_visa_record_group(r))
    path.write_text("\n".join(all_lines), encoding="ascii", errors="replace")
    return len(rows)  # number of transaction groups


def _write_visa_switch(rows: list, path: Path, tran_date: datetime) -> int:
    """Write Switch TLF file (same format as NFS matrix)."""
    import random as _rnd
    file_seq = str(_rnd.randint(1000, 9999))
    header = f"TH{tran_date.strftime('%y%m%d')}{file_seq}PRO2  TLF{'':40}{file_seq:>10}\n"
    lines = []
    for r in rows:
        # Switch TLF uses BASE24 msg_type (0210/0420) not VISA TC codes
        sw_tx = deepcopy(r)
        if sw_tx.msg_type in ("05", "25"):
            sw_tx.msg_type = "0210" if sw_tx.msg_type == "05" else "0420"
        elif sw_tx.msg_type in ("07", "27"):
            sw_tx.msg_type = "0210" if sw_tx.msg_type == "07" else "0420"
        lines.append("DR" + serialize_switch_tlf_row(sw_tx))
    path.write_text(header + "\n".join(lines), encoding="ascii", errors="replace")
    return len(lines)


def _write_visa_cbs(rows: list, path: Path) -> int:
    """Write CBS EX3198 file (same format as NFS matrix)."""
    lines = []
    for r in rows:
        cbs_tx = deepcopy(r)
        # CBS uses 0210/0420 msg_type codes
        if cbs_tx.msg_type in ("05", "07"):
            cbs_tx.msg_type = "0210"
        elif cbs_tx.msg_type in ("25", "27"):
            cbs_tx.msg_type = "0420"
        lines.append(serialize_cbs_row(cbs_tx))
    path.write_text("\n".join(lines), encoding="ascii", errors="replace")
    return len(lines)


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def generate_visa_matrix(
    volume: int = 500,
    ok_pct: float = 99.0,
    tran_date: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """
    Generate VISA TC, Switch TLF and CBS test files covering all 64 recon cases.

    Parameters
    ----------
    volume    : total number of transaction groups (min 64)
    ok_pct    : percentage of groups that are fully reconciled (OK cases)
    tran_date : base transaction date (defaults to today)
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

    if volume < 64:
        volume = 64

    plan = plan_volume(volume, ok_pct)

    visa_rows: list = []
    sw_rows:   list = []
    cbs_rows:  list = []
    manifest_rows = []
    group_counter = 0

    for case_id, variant in plan:
        case = CASE_MAP[case_id]
        _, nfs_val, sw_val, cbs_val, action, is_ok = case

        group_counter += 1
        group_id = f"GRP{group_counter:06d}"

        tx_time = tran_date.replace(
            hour=random.randint(8, 21),
            minute=random.randint(0, 59),
            second=random.randint(0, 59),
            microsecond=0,
        )

        # Determine tran_type from nfs_val: reversal → the forward companion sets
        # the TC type. We make the base as POS or Cash.
        tran_type = "ATM" if random.random() < 0.30 else "POS"
        base = make_base_visa_transaction(f"case_{case_id}", group_id, tx_time, tran_type)

        # Build rows per file
        g_visa = _visa_make_rows(base, nfs_val, "visa")
        g_sw   = _visa_make_rows(base, sw_val, "switch")
        g_cbs  = _visa_make_rows(base, cbs_val, "cbs")

        # Apply data-quality variants
        # Note: _apply_variant works on nfs_rows/sw_rows/cbs_rows generically
        if variant != "none":
            g_visa, g_sw, g_cbs = _apply_variant(
                g_visa, g_sw, g_cbs, variant, base.amount
            )

        is_111_mismatch  = is_ok and variant in CASE_1_MISMATCH_VARIANTS
        effective_is_ok  = is_ok and not is_111_mismatch
        if is_111_mismatch:
            kind, file_lbl = _MISMATCH_LABEL[variant]
            effective_action = f"{kind} mismatch — {file_lbl} differs (1|1|1) — investigate"
        else:
            # Translate NFS-centric action text to VISA context
            effective_action = action.replace("NFS", "VISA TC")

        # Track row start positions
        visa_start = len(visa_rows) + 1
        sw_start   = len(sw_rows) + 1
        cbs_start  = len(cbs_rows) + 1

        visa_rows.extend(g_visa)
        sw_rows.extend(g_sw)
        cbs_rows.extend(g_cbs)

        # Derive TC for manifest (from the first VISA row if present)
        tc_value = None
        if g_visa:
            tc_value = g_visa[0].msg_type   # "05", "07", "25", "27"

        manifest_rows.append({
            "group_id":        group_id,
            "case_id":         case_id,
            "visa_value":      nfs_val,    # structural matrix position for VISA TC file
            "switch_value":    sw_val,
            "cbs_value":       cbs_val,
            "action":          effective_action,
            "is_ok":           effective_is_ok,
            "variant":         variant,
            "rrn":             base.rrn,
            "card_pan":        base.card_pan,
            "amount_paise":    base.amount,
            "amount_inr":      base.amount / 100,
            "tran_date":       tx_time.strftime("%d%m%Y"),
            "tc":              tc_value,
            "file":            "visa_tc",
            "visa_rows":       len(g_visa),
            "switch_rows":     len(g_sw),
            "cbs_rows":        len(g_cbs),
            "visa_row_start":  visa_start,
            "sw_row_start":    sw_start,
            "cbs_row_start":   cbs_start,
        })

    # Write files
    date_str = tran_date.strftime("%d%m%Y")
    run_id   = tran_date.strftime("%Y%m%d") + datetime.now().strftime("%H%M%S")

    visa_path     = output_dir / f"VISA_MATRIX_{date_str}.txt"
    sw_path       = output_dir / f"t{tran_date.strftime('%y%m%d')}001-_VISA_SWITCH_TLF_MATRIX"
    cbs_path      = output_dir / f"EX3198_VISA_MATRIX_{date_str}.prt1"
    manifest_path = output_dir / f"manifest_visa_matrix_{run_id}.json"

    visa_count = _write_visa_tc(visa_rows, visa_path)
    sw_count   = _write_visa_switch(sw_rows, sw_path, tran_date)
    cbs_count  = _write_visa_cbs(cbs_rows, cbs_path)

    # Summary stats
    ok_groups            = sum(1 for r in manifest_rows if r["is_ok"])
    mismatch_111_groups  = sum(1 for r in manifest_rows
                               if r["variant"] in CASE_1_MISMATCH_VARIANTS and r["case_id"] == 1)
    non_ok_groups        = len(manifest_rows) - ok_groups
    actual_ok_pct        = round(ok_groups / len(manifest_rows) * 100, 2)

    case_dist = {}
    for r in manifest_rows:
        cid = r["case_id"]
        if cid not in case_dist:
            struct = CASE_MAP[cid]
            case_dist[cid] = {
                "case_id":  cid,
                "visa_tc":  r["visa_value"],
                "switch":   r["switch_value"],
                "cbs":      r["cbs_value"],
                "action":   struct[4].replace("NFS", "VISA TC"),
                "is_ok":    struct[5],
                "count":    0,
                "variants": {},
            }
        case_dist[cid]["count"] += 1
        v = r["variant"]
        case_dist[cid]["variants"][v] = case_dist[cid]["variants"].get(v, 0) + 1

    manifest_data = {
        "run_id":              run_id,
        "use_case":            "visa_pos_issuer_matrix",
        "tran_date":           date_str,
        "volume_requested":    volume,
        "volume_actual":       len(manifest_rows),
        "ok_pct_requested":    ok_pct,
        "ok_pct_actual":       actual_ok_pct,
        "ok_groups":           ok_groups,
        "non_ok_groups":       non_ok_groups,
        "mismatch_111_groups": mismatch_111_groups,
        "files": {
            "visa_tc":    visa_path.name,
            "switch_tlf": sw_path.name,
            "cbs":        cbs_path.name,
        },
        "row_counts": {
            "visa_tc":    visa_count * 4,   # 4 lines per transaction group
            "switch_tlf": sw_count,
            "cbs":        cbs_count,
        },
        "case_distribution": dict(sorted(case_dist.items())),
        "rows": manifest_rows,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # VISA Settlement report
    settlement_result = generate_visa_settlement(
        manifest=manifest_data,
        bank_name="TEST BANK LTD",
        output_dir=output_dir,
    )
    settlement_path = Path(settlement_result["path"])

    # Zip bundle
    zip_path = output_dir / f"visa_matrix_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(visa_path, visa_path.name)
        zf.write(sw_path, sw_path.name)
        zf.write(cbs_path, cbs_path.name)
        zf.write(settlement_path, settlement_path.name)
        zf.write(manifest_path, manifest_path.name)

    return {
        "settlement":          settlement_result,
        "run_id":              run_id,
        "zip_path":            str(zip_path),
        "ok_pct_actual":       actual_ok_pct,
        "ok_groups":           ok_groups,
        "non_ok_groups":       non_ok_groups,
        "mismatch_111_groups": mismatch_111_groups,
        "row_counts":          manifest_data["row_counts"],
        "case_distribution":   manifest_data["case_distribution"],
        "manifest_path":       str(manifest_path),
    }


if __name__ == "__main__":
    result = generate_visa_matrix(volume=500, ok_pct=99.0)
    dist = result["case_distribution"]
    print(f"\nRun ID: {result['run_id']}")
    print(f"OK: {result['ok_pct_actual']}%  ({result['ok_groups']} groups)")
    print(f"Non-OK: {result['non_ok_groups']} groups")
    rc = result["row_counts"]
    print(f"Rows — VISA TC: {rc['visa_tc']} lines ({rc['visa_tc']//4} groups), "
          f"Switch: {rc['switch_tlf']}, CBS: {rc['cbs']}")
    print(f"\nAll 64 cases covered: {len(dist) == 64}")
    print(f"\n{'Case':>5} {'NFS':>5} {'SW':>6} {'CBS':>5} {'Count':>6}  Action")
    print("─" * 75)
    for cid, c in dist.items():
        nv  = str(c['nfs'])    if c['nfs']    is not None else "null"
        sv  = str(c['switch']) if c['switch'] is not None else "null"
        cv  = str(c['cbs'])    if c['cbs']    is not None else "null"
        ok  = "OK" if c['is_ok'] else "  "
        print(f"{ok} {cid:>4}  {nv:>5} {sv:>6} {cv:>5}  {c['count']:>6}  {c['action'][:45]}")
