"""
NFS ATM Acquirer generator — IDFC FIRST Bank
Generates NFS Interchange, TLF, CBSMCW, FSSGL, BGL per run.
Customer uses a non-IDFC card at IDFC's ATM; IDFC is the acquirer.
"""

import json
import random
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import yaml

from generators.nfs_atm import (
    ScenarioGroup,
    Transaction,
    serialize_nfs_acq_row,
    generate_pan,
    generate_rrn,
    generate_account_no,
    generate_approval_code,
    generate_auth_id,
    generate_acq_terminal_id,
    generate_journal_no,
    random_amount,
    TRAN_CODE_MAP,
    ACQ_TERMINAL_LOCATIONS,
)
from generators.idfc.cbsmcw import build_cbsmcw_file
from generators.idfc.fssgl import build_fssgl_file
from generators.idfc.tlf import build_tlf_file
from generators.idfc.orchestrator import (
    _build_rows_idfc,
    _build_idfc_scenario_group,
    _load_config,
    _make_idfc_transaction,
)

BASE_DIR = Path(__file__).parent.parent.parent

_MONTH = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ─────────────────────────────────────────────
# TRANSACTION FACTORY
# ─────────────────────────────────────────────

def _make_nfs_acq_transaction(
    scenario_id: str,
    group_id: str,
    tran_date: datetime,
    config: dict,
    rev_only: bool = False,
) -> Transaction:
    tx = _make_idfc_transaction(
        scenario_id, group_id, tran_date, "ACQ_ATM", "D", config,
        rev_only=rev_only,
    )
    nfs_cfg = config.get("nfs_acquirer", {})
    tx.participant_id = nfs_cfg.get("participant_id", "IDF")
    tx.acquirer_id = nfs_cfg.get("acquirer_id", "800084")
    tx.from_account_type = "02"                           # cardholder savings account
    tx.member_number = " "                                # no member number for non-IDFC cards
    tx.terminal_id = generate_acq_terminal_id()          # physical 8-char ATM terminal ID
    tx.terminal_location = random.choice(ACQ_TERMINAL_LOCATIONS)
    return tx


# ─────────────────────────────────────────────
# FILE STATE MAPPING
# ─────────────────────────────────────────────

def _file_state_to_group_states(file_states: dict) -> dict:
    nfs_state = file_states.get("nfs")
    switch_state = file_states.get("tlf")
    cbs_state = (file_states.get("cbsmcw")
                 if file_states.get("cbsmcw") is not None
                 else file_states.get("fssgl"))
    return {
        "epin": nfs_state,       # maps to sg.nfs_rows
        "switch_tlf": switch_state,
        "cbs": cbs_state,
    }


# ─────────────────────────────────────────────
# MUTATION APPLICATION
# ─────────────────────────────────────────────

def _apply_mutations(sg: ScenarioGroup, scenario: dict) -> ScenarioGroup:
    amt_delta = scenario.get("amt_delta", {})
    date_delta = scenario.get("date_delta", {})
    dup_files = scenario.get("dup_files", [])

    file_row_map = {
        "nfs": sg.nfs_rows,
        "epin": sg.nfs_rows,  # alias
        "tlf": sg.switch_rows,
        "cbsmcw": sg.cbs_rows,
        "fssgl": sg.cbs_rows,
    }

    for file_key, delta in amt_delta.items():
        for tx in file_row_map.get(file_key, []):
            tx.amount = max(0, tx.amount + delta)

    for file_key, days in date_delta.items():
        for tx in file_row_map.get(file_key, []):
            tx.tran_date = tx.tran_date + timedelta(days=days)
            tx.settlement_date = tx.tran_date

    _seen: set = set()
    for file_key in dup_files:
        rows = file_row_map.get(file_key, [])
        if not rows:
            continue
        row_id = id(rows)
        if row_id in _seen:
            continue
        _seen.add(row_id)
        dup = deepcopy(rows[0])
        if file_key in ("cbsmcw", "fssgl"):
            dup.journal_no = generate_journal_no()
        rows.append(dup)

    return sg


# ─────────────────────────────────────────────
# NFS INTERCHANGE FILE BUILDER
# ─────────────────────────────────────────────

def _build_nfs_acq_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
) -> tuple:
    lines = [serialize_nfs_acq_row(tx) for sg in groups for tx in sg.nfs_rows]
    fname = f"NFS_ACQ_{tran_date.strftime('%d%m%Y')}.txt"
    return "\n".join(lines), fname


# ─────────────────────────────────────────────
# BGL BALANCE REPORT (NFS labels)
# ─────────────────────────────────────────────

def _build_bgl_nfs(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
) -> tuple:
    cbs = config["cbs"]
    atm_gl = cbs["atm_gl_account"]
    branch = cbs["branch_code"]
    date_str = f"{tran_date.day:02d}-{_MONTH[tran_date.month - 1]}-{str(tran_date.year)[2:]}"
    fname = f"BGL-BALANCE-REPORT-NFS_{tran_date.strftime('%d%m%Y')}.txt"

    atm_paise = 0
    for sg in groups:
        for tx in sg.cbs_rows:
            atm_paise += -tx.amount if tx.msg_type == "0420" else tx.amount

    def _fmt(rupees: float) -> str:
        return f"{'0':>17}" if rupees == 0.0 else f"{rupees:17.2f}"

    header = (
        f"(SELECTSY|"
        f"{'ACCOUNT_NO':<40}|"
        f"{'BGL_NAME':<40}|"
        f"CUR|NATUR|BRANC|"
        f"{'GL_HOME_BRANCH_NAME':<40}|"
        f"  CURRENT_BALANCE|STATUS"
    )
    sep = (f"{'-'*9}|{'-'*40}|{'-'*40}|{'-'*3}|{'-'*5}|{'-'*5}|"
           f"{'-'*40}|{'-'*17}|{'-'*6}")
    lines = ["", header, sep]
    if atm_paise != 0:
        lines.append(
            f"{date_str}|"
            f"{atm_gl:<40}|"
            f"{'NFS ATM PAYABLE A/C':<40}|"
            f"INR|"
            f"{'21001':5}|"
            f"{branch:5}|"
            f"{'Cards Operations':<40}|"
            f"{_fmt(atm_paise / 100)}|"
            f"ACTIVE"
        )
    return "\n".join(lines), fname


# ─────────────────────────────────────────────
# LOADERS
# ─────────────────────────────────────────────

def _load_use_case(use_case_id: str) -> dict:
    path = BASE_DIR / "use_cases" / f"{use_case_id}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────
# MAIN GENERATE FUNCTION
# ─────────────────────────────────────────────

def generate_idfc_nfs_acq(
    use_case_id: str = "idfc_nfs_acquirer",
    bank_id: str = "idfc",
    volume: int = 50,
    ok_pct: float = 95.0,
    tran_date: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
    selected_scenarios: Optional[list] = None,
    custom_scenarios: Optional[list] = None,
    gl_accounts: Optional[list] = None,
) -> dict:
    if tran_date is None:
        tran_date = datetime.today()
    if output_dir is None:
        output_dir = BASE_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    config = _load_config(bank_id)
    if gl_accounts:
        if len(gl_accounts) >= 1:
            config["cbs"]["atm_gl_account"] = gl_accounts[0]
        if len(gl_accounts) >= 2:
            config["cbs"]["pos_gl_account"] = gl_accounts[1]
    use_case = _load_use_case(use_case_id)

    scenarios = list(use_case["scenarios"])
    if selected_scenarios is not None:
        _sel = set(selected_scenarios)
        scenarios = [s for s in scenarios if s["id"] in _sel]

    scenario_is_ok = {sc["id"]: sc.get("is_ok", False) for sc in scenarios}
    scenario_code = {sc["id"]: sc.get("code", sc["id"]) for sc in scenarios}
    scenario_group_map = {sc["id"]: sc.get("group", "") for sc in scenarios}

    ok_scenarios = [s for s in scenarios if s.get("is_ok", False)]
    exc_scenarios = [s for s in scenarios if not s.get("is_ok", False)]

    # ── Merge custom entries ──────────────────────────────────────────
    if custom_scenarios:
        def _frlbl(fwd, rev):
            if fwd is None:
                return "null"
            parts = []
            if fwd:
                parts.append(f"+{fwd}F")
            if rev:
                parts.append(f"-{rev}R")
            return "".join(parts) or "0"

        for idx, cs in enumerate(custom_scenarios[:100]):
            cbs_fwd  = cs.get("cbs_fwd")
            cbs_rev  = cs.get("cbs_rev") or 0
            sw_fwd   = cs.get("switch_fwd")
            sw_rev   = cs.get("switch_rev") or 0
            nfs_fwd  = cs.get("nfs_fwd")
            nfs_rev  = cs.get("nfs_rev") or 0

            cbs_present = cbs_fwd is not None
            sw_present  = sw_fwd  is not None
            nfs_present = nfs_fwd is not None

            cbs_tuple = (cbs_fwd, cbs_rev) if cbs_present else None
            sw_tuple  = (sw_fwd,  sw_rev)  if sw_present  else None
            nfs_tuple = (nfs_fwd, nfs_rev) if nfs_present else None

            tuples = [cbs_tuple, sw_tuple, nfs_tuple]
            is_ok_cs = (
                all(t is not None for t in tuples)
                and len(set(tuples)) == 1
                and tuples[0] != (0, 0)
            )

            n_null = sum(t is None for t in tuples)
            if is_ok_cs:
                action_cs = ""
            elif n_null == 3:
                action_cs = "ALL_MISSING"
            elif n_null == 2:
                action_cs = "MISSING_FILE"
            elif n_null == 1:
                action_cs = ("MISSING_CBS_FILE" if not cbs_present
                             else "MISSING_SWITCH_FILE" if not sw_present
                             else "MISSING_NETWORK_FILE")
            elif any(t == (0, 0) for t in tuples if t is not None):
                action_cs = "EMPTY_FILE"
            elif len(set(t for t in tuples if t is not None)) > 1:
                action_cs = "CROSSFIRE_MISMATCH"
            else:
                action_cs = "OTHER"

            fs_cs: dict = {}
            if cbs_present:
                fs_cs["cbsmcw"] = cbs_tuple
                fs_cs["fssgl"]  = cbs_tuple
            if sw_present:
                fs_cs["tlf"] = sw_tuple
            if nfs_present:
                fs_cs["nfs"] = nfs_tuple

            sc_id   = f"CUSTOM_{idx + 1:04d}"
            sc_code = f"C{idx + 1:03d}"
            sc_name = (f"Custom #{idx + 1}: "
                       f"CBS={_frlbl(cbs_fwd, cbs_rev)} "
                       f"Sw={_frlbl(sw_fwd, sw_rev)} "
                       f"NFS={_frlbl(nfs_fwd, nfs_rev)}")

            synthetic = {
                "id": sc_id, "name": sc_name, "is_ok": is_ok_cs,
                "tran_type": "ACQ_ATM", "tran_category": "D",
                "file_states": fs_cs, "weight": 5,
                "group": "CUSTOM", "code": sc_code,
                "action": action_cs, "variant": "baseline",
            }
            if is_ok_cs:
                ok_scenarios.append(synthetic)
            else:
                exc_scenarios.append(synthetic)
            scenario_is_ok[sc_id]     = is_ok_cs
            scenario_code[sc_id]      = sc_code
            scenario_group_map[sc_id] = "CUSTOM"

    n_ok  = len(ok_scenarios)
    n_exc = len(exc_scenarios)

    if not exc_scenarios:
        ok_volume, exc_volume = volume, 0
    elif not ok_scenarios:
        ok_volume, exc_volume = 0, volume
    else:
        ok_volume  = max(n_ok, round(volume * ok_pct / 100))
        exc_volume = max(0, volume - ok_volume)

    ok_total_weight  = sum(s.get("weight", 5) for s in ok_scenarios)  or 1
    exc_total_weight = sum(s.get("weight", 5) for s in exc_scenarios) or 1

    all_groups: List[ScenarioGroup] = []
    group_counter = 0
    scenario_summary = []

    def _allocate(sc_list, vol, total_w, ensure_one) -> dict:
        counts = {sc["id"]: max(0, round(vol * sc.get("weight", 5) / total_w))
                  for sc in sc_list}
        if ensure_one:
            for sc in sc_list:
                if counts[sc["id"]] == 0:
                    counts[sc["id"]] = 1
        elif vol > 0 and all(c == 0 for c in counts.values()):
            for sc in sorted(sc_list, key=lambda s: s.get("weight", 5), reverse=True)[:vol]:
                counts[sc["id"]] = 1
        diff = vol - sum(counts.values())
        if diff != 0:
            ordered = sorted(sc_list, key=lambda s: s.get("weight", 5), reverse=(diff > 0))
            for sc in ordered:
                if diff == 0:
                    break
                if diff > 0:
                    counts[sc["id"]] += 1; diff -= 1
                elif counts[sc["id"]] > 0:
                    counts[sc["id"]] -= 1; diff += 1
        return counts

    def _process(sc_list, vol, total_w, ensure_one):
        nonlocal group_counter
        alloc = _allocate(sc_list, vol, total_w, ensure_one)
        for sc in sc_list:
            count = alloc[sc["id"]]
            if count == 0:
                continue
            file_states_raw = sc.get("file_states", {})
            group_states = _file_state_to_group_states(file_states_raw)

            for _ in range(count):
                group_counter += 1
                group_id = f"NFSACQ{group_counter:06d}"
                tx_time = tran_date.replace(
                    hour=random.randint(8, 21),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )
                nfs_val = file_states_raw.get("nfs")
                if isinstance(nfs_val, (list, tuple)):
                    rev_only = nfs_val[0] == 0 and nfs_val[1] > 0
                else:
                    rev_only = isinstance(nfs_val, int) and nfs_val < 0
                base = _make_nfs_acq_transaction(
                    sc["id"], group_id, tx_time, config, rev_only=rev_only,
                )
                sg = _build_idfc_scenario_group(
                    sc["id"], sc["name"], group_id, base, group_states,
                    mutation_id=sc.get("variant", "baseline"),
                )
                # _build_rows_idfc overwrites tran_type to "W1"/"RV".
                # Restore "OW" on all nfs_rows/cbs_rows so that:
                #   cbsmcw.py → uses OWDR/OWCR (acquirer) instead of CWDR/CWRR (issuer)
                #   fssgl.py  → uses OWDR/OWCR
                #   NFS file  → positions 4-5 = "OW" (tran_code=020099 identifies reversals)
                # switch_rows: TLF identifies reversals via msg_type, not tran_type.
                for row_list in (sg.nfs_rows, sg.cbs_rows):
                    for tx in row_list:
                        tx.tran_type = "OW"
                _apply_mutations(sg, sc)
                all_groups.append(sg)

            scenario_summary.append({
                "code": sc.get("code", sc["id"]),
                "group": sc.get("group", ""),
                "id": sc["id"],
                "name": sc["name"],
                "tran_type": "ACQ_ATM",
                "tran_category": "D",
                "action": sc.get("action", ""),
                "is_ok": sc.get("is_ok", False),
                "count": count,
            })

    _process(ok_scenarios,  ok_volume,  ok_total_weight,  ensure_one=True)
    _process(exc_scenarios, exc_volume, exc_total_weight, ensure_one=False)

    random.shuffle(all_groups)

    # ── Transaction index ────────────────────────────────────────────
    transaction_index = []
    for sg in all_groups:
        tx = sg.base_tx
        transaction_index.append({
            "group_id": sg.group_id,
            "scenario_code": scenario_code.get(sg.scenario_id, sg.scenario_id),
            "scenario_group": scenario_group_map.get(sg.scenario_id, ""),
            "scenario_id": sg.scenario_id,
            "scenario_name": sg.scenario_name,
            "is_ok": scenario_is_ok.get(sg.scenario_id, False),
            "lookup": {
                "rrn": tx.rrn.zfill(12),
                "card_pan": tx.card_pan[:16],
                "terminal_id": tx.auth_id.zfill(8),
                "auth_resp": tx.auth_id[:6],
                "amount_rupees": f"{tx.amount / 100:.2f}",
                "tran_date": tx.tran_date.strftime("%d-%m-%Y"),
                "tran_time": tx.tran_date.strftime("%H:%M:%S"),
            },
            "file_rows": {
                "nfs": {"total": len(sg.nfs_rows)},
                "switch": {"total": len(sg.switch_rows)},
                "cbs": {"total": len(sg.cbs_rows)},
            },
        })

    run_ts = datetime.now()
    run_id = run_ts.strftime("%Y%m%d%H%M%S")
    files_written = {}
    errors = {}

    # NFS Interchange
    try:
        content, fname = _build_nfs_acq_file(all_groups, tran_date)
        (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
        files_written["nfs"] = fname
    except Exception as e:
        errors["nfs"] = str(e)

    # TLF
    try:
        content, fname = build_tlf_file(all_groups, tran_date, config, run_ts)
        (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
        files_written["tlf"] = fname
    except Exception as e:
        errors["tlf"] = str(e)

    # CBSMCW
    try:
        content, fname = build_cbsmcw_file(all_groups, tran_date, config)
        (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
        files_written["cbsmcw"] = fname
    except Exception as e:
        errors["cbsmcw"] = str(e)

    # FSSGL
    try:
        content, fname = build_fssgl_file(all_groups, tran_date, config, run_ts)
        (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
        files_written["fssgl"] = fname
    except Exception as e:
        errors["fssgl"] = str(e)

    # BGL
    try:
        content, fname = _build_bgl_nfs(all_groups, tran_date, config)
        (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
        files_written["bgl"] = fname
    except Exception as e:
        errors["bgl"] = str(e)

    # ── Summary ─────────────────────────────────────────────────────
    _MISSING = {"MISSING_NETWORK_FILE", "MISSING_SWITCH_FILE", "MISSING_CBS_FILE",
                "MISSING_NETWORK_AND_CBS", "MISSING_SWITCH_AND_CBS",
                "MISSING_NETWORK_AND_SWITCH", "ALL_MISSING",
                "FEE_MISSING_CBS", "FEE_MISSING_EPIN", "FEE_MISSING_SWITCH"}
    _CROSSFIRE = {"CROSSFIRE_MISMATCH", "CROSSFIRE_SWITCH", "REVERSAL_MISMATCH"}

    total_ok = sum(1 for sg in all_groups if scenario_is_ok.get(sg.scenario_id, False))
    total_cnt = len(all_groups)
    actual_ok_pct = round(total_ok / total_cnt * 100, 1) if total_cnt else 0.0

    exc_groups = {"AMOUNT_MISMATCH": 0, "DATE_MISMATCH": 0, "DUPLICATE": 0,
                  "DOUBLE_DEBIT": 0, "MISSING_FILE": 0, "CROSSFIRE": 0, "OTHER": 0}
    for ss in scenario_summary:
        if not ss["is_ok"]:
            action = ss.get("action", "OTHER")
            if action == "AMOUNT_MISMATCH":
                exc_groups["AMOUNT_MISMATCH"] += ss["count"]
            elif action == "DATE_MISMATCH":
                exc_groups["DATE_MISMATCH"] += ss["count"]
            elif action == "DUPLICATE":
                exc_groups["DUPLICATE"] += ss["count"]
            elif action == "DOUBLE_DEBIT":
                exc_groups["DOUBLE_DEBIT"] += ss["count"]
            elif action in _MISSING:
                exc_groups["MISSING_FILE"] += ss["count"]
            elif action in _CROSSFIRE:
                exc_groups["CROSSFIRE"] += ss["count"]
            else:
                exc_groups["OTHER"] += ss["count"]

    recon_summary = {
        "ok_count": total_ok,
        "exception_count": total_cnt - total_ok,
        "ok_pct": actual_ok_pct,
        "exception_breakdown": {k: v for k, v in exc_groups.items() if v > 0},
    }

    nfs_rows  = sum(len(sg.nfs_rows)    for sg in all_groups)
    tlf_rows  = sum(len(sg.switch_rows) for sg in all_groups)
    cbs_rows  = sum(len(sg.cbs_rows)    for sg in all_groups)

    manifest = {
        "run_id": run_id,
        "use_case": use_case_id,
        "bank": bank_id,
        "ok_pct_target": ok_pct,
        "tran_date": tran_date.strftime("%Y-%m-%d"),
        "files": files_written,
        "counts": {
            "total_groups": total_cnt,
            "nfs_rows": nfs_rows,
            "tlf_rows": tlf_rows,
            "cbs_rows": cbs_rows,
        },
        "recon_summary": recon_summary,
        "scenarios": scenario_summary,
        "transaction_index": transaction_index,
        "errors": errors,
    }

    manifest_fname = f"manifest_idfc_nfs_{run_id}.json"
    (output_dir / manifest_fname).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    zip_fname = f"idfc_nfs_{run_id}.zip"
    zip_path  = output_dir / zip_fname
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in files_written.values():
            fp = output_dir / fname
            if fp.exists():
                zf.write(fp, fname)
        zf.write(output_dir / manifest_fname, manifest_fname)

    return {
        "run_id": run_id,
        "zip_path": str(zip_path),
        "zip_name": zip_fname,
        "files": files_written,
        "counts": manifest["counts"],
        "recon_summary": recon_summary,
        "scenarios": scenario_summary,
        "errors": errors,
        "manifest_path": str(output_dir / manifest_fname),
    }
