"""
Orchestrator for IDFC First Bank — Mastercard POS Issuer test data generation.

Files generated per run:
  CBSMCW   — CBS card-level file  (NETWORK=MDS, TRAN_TYPE=PRDR/PRCR)
  FSSGL    — CBS GL file          (MC POS GL accounts)
  PTLF     — Switch POS log       (TERM_FIID=MDUI, PAN entry mode 051)
  T112     — Mastercard network clearing file (MTI=1240, FC=200)

Switch ↔ CBS matching: Card No (F6+L3) + Auth Code + RRN
Switch ↔ Network (T112): Card No (F6+L3) + Auth Code + Terminal ID
CBS ↔ Network (T112):    Card No (F6+L3) + Auth Code + Terminal ID

Domestic recon: Card No + Auth code + Trans date + Trans Amt
International:  Card No + Auth code + Trans date
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
    generate_pan,
    generate_rrn,
    generate_account_no,
    generate_approval_code,
    generate_auth_id,
    generate_journal_no,
    random_amount,
    TRAN_CODE_MAP,
    TERMINAL_LOCATIONS,
)
from generators.idfc.cbsmcw import build_cbsmcw_file
from generators.idfc.fssgl import build_fssgl_file
from generators.idfc.ptlf import build_ptlf_file
from generators.idfc.t112 import build_t112_file

BASE_DIR = Path(__file__).parent.parent.parent

# Sentinel msg_type for "present but status=0" T112 rows
_T112_STATUS0 = "0000"


def _load_config(bank_id: str) -> dict:
    path = BASE_DIR / "banks" / bank_id / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _load_use_case(use_case_id: str) -> dict:
    path = BASE_DIR / "use_cases" / f"{use_case_id}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _apply_mc_config_overrides(config: dict) -> dict:
    """Inject MC-specific config keys so shared generators (cbsmcw, fssgl, ptlf) use MC values."""
    mc = config.get("mastercard", {})
    config = dict(config)   # shallow copy so we don't mutate the original

    # CBSMCW: network label = MDS
    config["cbsmcw"] = dict(config.get("cbsmcw", {}))
    config["cbsmcw"]["network"] = mc.get("network_label", "MDS")

    # FSSGL: MC POS GL accounts and section names (POS only, no ATM)
    config["fssgl"] = dict(config.get("fssgl", {}))
    config["fssgl"]["pos_section_name"] = mc.get("pos_dom_section_name", "MC POS PAYABLE DOM A/C")
    config["cbs"] = dict(config.get("cbs", {}))
    config["cbs"]["pos_gl_account"] = mc.get("pos_dom_gl_account", "96794102017")

    # PTLF: MDUI terminal FIID + MC tran_orig + POS entry mode
    config["ptlf"] = dict(config.get("ptlf", {}))
    config["ptlf"]["term_fiid"] = mc.get("institution_id", "MDUI")
    config["ptlf"]["tran_orig"] = mc.get("tran_orig", "MCRD")
    config["ptlf"]["pan_entry_mode_pos"] = mc.get("pan_entry_mode_pos", "051")

    return config


def _make_mc_transaction(
    scenario_id: str,
    group_id: str,
    tran_date: datetime,
    tran_category: str,
    config: dict,
    rev_only: bool = False,
) -> Transaction:
    mc = config.get("mastercard", {})
    visa_key = "international" if tran_category == "I" else "domestic"
    bins_conf = mc.get(visa_key, {}).get("reporting_for", mc.get("bin_ranges", []))
    bin_prefix = random.choice([b["bin"] if "bin" in b else b["prefix"] for b in bins_conf]) if bins_conf else "549900"
    pan = generate_pan(prefix=bin_prefix, length=16)

    if rev_only:
        amount = random.choice([200, 500, 1000, 1500, 2000]) * 100
    else:
        amount = random_amount()

    rrn = generate_rrn()
    auth_code = generate_auth_id()
    mcc = "5999"   # generic retail POS; T112 POS only

    tx = Transaction(
        rrn=rrn,
        stan=rrn,
        card_pan=pan,
        account_no=f"000102{random.randint(10000000, 99999999):08d}",
        terminal_id=auth_code.zfill(8),
        terminal_location=random.choice(TERMINAL_LOCATIONS),
        acquirer_id=str(random.randint(100000, 999999)),
        card_acceptor_id="IDFE" + "POS" + f"{random.randint(1000, 9999)}",
        branch_no="10201",
        ge_branch_no="10201",
        tran_date=tran_date,
        settlement_date=tran_date,
        amount=amount,
        tran_code=TRAN_CODE_MAP.get("W1", "020010"),
        tran_type="W1",
        resp_code="00",
        approval_number=generate_approval_code(),
        auth_id=auth_code,
        balance=amount * 5 + random.randint(10000, 100000),
        journal_no=str(random.randint(100000, 999999)) + "000",
        teller_no="0000000009900001",
        msg_type="0210",
        scenario_id=scenario_id,
        scenario_group=group_id,
        mcc=mcc,
        currency_code="356",
        tran_category=tran_category,
    )
    return tx


def _build_t112_rows(base: Transaction, state) -> list:
    """Build T112 rows from state value.
    state=1  → forward row
    state=-1 → reversal row (MRI=R)
    state=0  → "status 0" row (MTI!=1240, use sentinel msg_type 0000)
    state=None → absent
    (f, r) tuple → f forward + r reversal rows
    """
    if state is None:
        return []

    def _fwd():
        t = deepcopy(base)
        t.msg_type = "0210"
        return t

    def _rev():
        t = deepcopy(base)
        t.msg_type = "0420"
        t.amount = abs(base.amount)
        return t

    def _zero():
        t = deepcopy(base)
        t.msg_type = _T112_STATUS0   # sentinel for MTI != 1240
        return t

    if isinstance(state, (list, tuple)):
        fc, rc = int(state[0]), int(state[1])
        return [_fwd() for _ in range(fc)] + [_rev() for _ in range(rc)]
    if state > 0:
        return [_fwd() for _ in range(int(state))]
    if state < 0:
        return [_rev() for _ in range(int(abs(state)))]
    return [_zero()]   # state == 0


def _build_switch_rows(base: Transaction, state) -> list:
    """Build PTLF switch rows."""
    if state is None:
        return []

    def _fwd():
        t = deepcopy(base)
        t.msg_type = "0210"
        t.resp_code = "00"
        return t

    def _rev():
        t = deepcopy(base)
        t.msg_type = "0420"
        t.resp_code = "00"
        t.amount = abs(base.amount)
        return t

    if isinstance(state, (list, tuple)):
        fc, rc = int(state[0]), int(state[1])
        return [_fwd() for _ in range(fc)] + [_rev() for _ in range(rc)]
    if state > 0:
        return [_fwd() for _ in range(int(state))]
    if state < 0:
        return [_rev() for _ in range(int(abs(state)))]
    return [_fwd(), _rev()]   # state == 0


def _build_cbs_rows(base: Transaction, state) -> list:
    """Build CBS rows."""
    if state is None:
        return []

    def _fwd():
        t = deepcopy(base)
        t.msg_type = "0210"
        t.resp_code = "00"
        return t

    def _rev():
        t = deepcopy(base)
        t.msg_type = "0420"
        t.resp_code = "00"
        t.amount = abs(base.amount)
        return t

    if isinstance(state, (list, tuple)):
        fc, rc = int(state[0]), int(state[1])
        return [_fwd() for _ in range(fc)] + [_rev() for _ in range(rc)]
    if state > 0:
        return [_fwd() for _ in range(int(state))]
    if state < 0:
        return [_rev() for _ in range(int(abs(state)))]
    return [_fwd(), _rev()]


def _build_mc_scenario_group(
    scenario_id: str,
    scenario_name: str,
    group_id: str,
    base: Transaction,
    file_states: dict,
    mutation_id: str = "baseline",
) -> ScenarioGroup:
    sg = ScenarioGroup(
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        group_id=group_id,
        base_tx=base,
        mutation_id=mutation_id,
    )
    sg.t112_rows   = _build_t112_rows(base, file_states.get("t112"))
    sg.switch_rows = _build_switch_rows(base, file_states.get("ptlf"))
    sg.cbs_rows    = _build_cbs_rows(base, file_states.get("cbs"))
    # nfs_rows unused for MC (no EPIN)
    sg.nfs_rows = []
    return sg


def _apply_mutations(sg: ScenarioGroup, scenario: dict) -> ScenarioGroup:
    amt_delta  = scenario.get("amt_delta", {})
    date_delta = scenario.get("date_delta", {})
    dup_files  = scenario.get("dup_files", [])

    file_row_map = {
        "t112":   sg.t112_rows,
        "ptlf":   sg.switch_rows,
        "cbsmcw": sg.cbs_rows,
        "fssgl":  sg.cbs_rows,
        "cbs":    sg.cbs_rows,
    }

    for file_key, delta in amt_delta.items():
        for tx in file_row_map.get(file_key, []):
            tx.amount = max(0, tx.amount + delta)

    for file_key, days in date_delta.items():
        for tx in file_row_map.get(file_key, []):
            tx.tran_date = tx.tran_date + timedelta(days=days)
            tx.settlement_date = tx.tran_date

    seen: set = set()
    for file_key in dup_files:
        rows = file_row_map.get(file_key, [])
        if not rows:
            continue
        row_id = id(rows)
        if row_id in seen:
            continue
        seen.add(row_id)
        dup = deepcopy(rows[0])
        if file_key in ("cbsmcw", "fssgl", "cbs"):
            dup.journal_no = generate_journal_no()
        rows.append(dup)

    return sg


def _row_counts(rows) -> dict:
    fwd = sum(1 for t in rows if t.msg_type not in ("0420", _T112_STATUS0))
    rev = sum(1 for t in rows if t.msg_type == "0420")
    return {"total": len(rows), "forward": fwd, "reversal": rev}


def _allocate_counts(sc_list: list, vol: int, total_weight: int, ensure_one_each: bool) -> dict:
    counts = {sc["id"]: max(0, round(vol * sc.get("weight", 5) / total_weight))
              for sc in sc_list}
    if ensure_one_each:
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
                counts[sc["id"]] += 1
                diff -= 1
            elif counts[sc["id"]] > 0:
                counts[sc["id"]] -= 1
                diff += 1
    return counts


def generate_idfc_mc(
    use_case_id: str,
    bank_id: str = "idfc",
    volume: int = 50,
    ok_pct: float = 95.0,
    tran_date: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
    selected_scenarios: Optional[list] = None,
    custom_scenarios: Optional[list] = None,
    gl_accounts: Optional[list] = None,
    no_cbsmcw_duplicates: bool = True,
) -> dict:
    if tran_date is None:
        tran_date = datetime.today()
    if output_dir is None:
        output_dir = BASE_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    raw_config = _load_config(bank_id)
    config = _apply_mc_config_overrides(raw_config)

    # GL account overrides (optional)
    if gl_accounts:
        if len(gl_accounts) >= 1:
            config["cbs"]["pos_gl_account"] = gl_accounts[0]
        if len(gl_accounts) >= 2:
            config["mastercard"]["pos_int_gl_account"] = gl_accounts[1]

    use_case = _load_use_case(use_case_id)
    scenarios = list(use_case.get("scenarios", []))
    if selected_scenarios is not None:
        sel = set(selected_scenarios)
        scenarios = [s for s in scenarios if s["id"] in sel]

    scenario_is_ok   = {sc["id"]: sc.get("is_ok", False) for sc in scenarios}
    scenario_code    = {sc["id"]: sc.get("code", sc["id"]) for sc in scenarios}
    scenario_grp_map = {sc["id"]: sc.get("group", "") for sc in scenarios}

    ok_scenarios  = [s for s in scenarios if s.get("is_ok", False)]
    exc_scenarios = [s for s in scenarios if not s.get("is_ok", False)]

    # Custom scenarios
    if custom_scenarios:
        def _lbl(fwd, rev):
            if fwd is None:
                return "null"
            parts = []
            if fwd:
                parts.append(f"+{fwd}F")
            if rev:
                parts.append(f"-{rev}R")
            return "".join(parts) or "0"

        for idx, cs in enumerate(custom_scenarios[:100]):
            cat_cs = cs.get("tran_category", "D")
            cbs_fwd = cs.get("cbs_fwd")
            cbs_rev = cs.get("cbs_rev") or 0
            sw_fwd  = cs.get("switch_fwd")
            sw_rev  = cs.get("switch_rev") or 0
            t112_fwd = cs.get("t112_fwd")
            t112_rev = cs.get("t112_rev") or 0

            cbs_present  = cbs_fwd is not None
            sw_present   = sw_fwd is not None
            t112_present = t112_fwd is not None

            cbs_tuple  = (cbs_fwd, cbs_rev) if cbs_present else None
            sw_tuple   = (sw_fwd, sw_rev) if sw_present else None
            t112_tuple = (t112_fwd, t112_rev) if t112_present else None

            tuples = [cbs_tuple, sw_tuple, t112_tuple]
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
            else:
                action_cs = "CROSSFIRE_MISMATCH"

            fs_cs: dict = {}
            if cbs_present:
                fs_cs["cbsmcw"] = cbs_tuple
                fs_cs["fssgl"]  = cbs_tuple
                fs_cs["cbs"]    = cbs_tuple
            if sw_present:
                fs_cs["ptlf"] = sw_tuple
            if t112_present:
                fs_cs["t112"] = t112_tuple

            sc_id = f"CUSTOM_{idx + 1:04d}"
            synthetic = {
                "id": sc_id,
                "name": (f"Custom #{idx + 1}: "
                         f"CBS={_lbl(cbs_fwd, cbs_rev)} "
                         f"Sw={_lbl(sw_fwd, sw_rev)} "
                         f"T112={_lbl(t112_fwd, t112_rev)}"),
                "is_ok": is_ok_cs,
                "tran_category": cat_cs,
                "file_states": fs_cs,
                "weight": 5,
                "group": "CUSTOM",
                "code": f"C{idx + 1:03d}",
                "action": action_cs,
                "variant": "baseline",
            }
            if is_ok_cs:
                ok_scenarios.append(synthetic)
            else:
                exc_scenarios.append(synthetic)
            scenario_is_ok[sc_id]   = is_ok_cs
            scenario_code[sc_id]    = f"C{idx + 1:03d}"
            scenario_grp_map[sc_id] = "CUSTOM"

    n_ok  = len(ok_scenarios)
    n_exc = len(exc_scenarios)

    if not exc_scenarios:
        ok_volume, exc_volume = volume, 0
    elif not ok_scenarios:
        ok_volume, exc_volume = 0, volume
    else:
        ok_volume  = max(n_ok, round(volume * ok_pct / 100))
        exc_volume = max(0, volume - ok_volume)

    ok_w  = sum(s.get("weight", 5) for s in ok_scenarios)  or 1
    exc_w = sum(s.get("weight", 5) for s in exc_scenarios) or 1

    all_groups: List[ScenarioGroup] = []
    group_counter = 0
    scenario_summary = []

    def _process(sc_list, vol, total_w, ensure_one_each):
        nonlocal group_counter
        alloc = _allocate_counts(sc_list, vol, total_w, ensure_one_each)
        for sc in sc_list:
            count = alloc[sc["id"]]
            if count == 0:
                continue
            tran_category = sc.get("tran_category", "D")
            file_states = sc.get("file_states", {})

            # Derive cbs state (cbsmcw or fssgl key; prefer explicit "cbs" key)
            cbs_state = file_states.get("cbs",
                        file_states.get("cbsmcw",
                        file_states.get("fssgl")))
            t112_state  = file_states.get("t112")
            ptlf_state  = file_states.get("ptlf")

            resolved = {"t112": t112_state, "ptlf": ptlf_state, "cbs": cbs_state}

            for _ in range(count):
                group_counter += 1
                group_id = f"MC{group_counter:06d}"
                tx_time = tran_date.replace(
                    hour=random.randint(8, 21),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )
                t112_val = file_states.get("t112")
                if isinstance(t112_val, (list, tuple)):
                    rev_only = t112_val[0] == 0 and t112_val[1] > 0
                else:
                    rev_only = isinstance(t112_val, int) and t112_val < 0

                base = _make_mc_transaction(
                    sc["id"], group_id, tx_time, tran_category, config,
                    rev_only=rev_only,
                )
                sg = _build_mc_scenario_group(
                    sc["id"], sc["name"], group_id, base, resolved,
                    mutation_id=sc.get("variant", "baseline"),
                )
                _apply_mutations(sg, sc)
                all_groups.append(sg)

            scenario_summary.append({
                "code": sc.get("code", sc["id"]),
                "group": sc.get("group", ""),
                "id": sc["id"],
                "name": sc["name"],
                "tran_category": sc.get("tran_category", "D"),
                "action": sc.get("action", ""),
                "is_ok": sc.get("is_ok", False),
                "count": count,
            })

    _process(ok_scenarios, ok_volume, ok_w, ensure_one_each=True)
    _process(exc_scenarios, exc_volume, exc_w, ensure_one_each=False)

    random.shuffle(all_groups)

    run_ts = datetime.now()
    run_id = run_ts.strftime("%Y%m%d%H%M%S")

    files_written = {}
    errors = {}

    def _write(key, builder, *args, **kwargs):
        try:
            content, fname = builder(*args, **kwargs)
            if not content:
                return
            p = output_dir / fname
            p.write_text(content, encoding="utf-8", errors="replace")
            files_written[key] = fname
        except Exception as e:
            errors[key] = str(e)

    _write("cbsmcw", build_cbsmcw_file, all_groups, tran_date, config, no_duplicates=no_cbsmcw_duplicates)
    _write("ptlf",   build_ptlf_file,   all_groups, tran_date, config, run_ts)
    _write("t112",   build_t112_file,   all_groups, tran_date, config, run_ts)

    # FSSGL: DOM POS and INT POS use separate GL accounts
    mc_cfg = config.get("mastercard", {})
    dom_gl = mc_cfg.get("pos_dom_gl_account", "96794102017")
    int_gl = mc_cfg.get("pos_int_gl_account", "96800102012")
    dom_section = mc_cfg.get("pos_dom_section_name", "MC POS PAYABLE DOM A/C")
    int_section = mc_cfg.get("pos_int_section_name", "MC POS PAYABLE INT A/C")

    dom_groups = [sg for sg in all_groups if sg.base_tx.tran_category == "D"]
    int_groups_list = [sg for sg in all_groups if sg.base_tx.tran_category == "I"]

    try:
        from generators.idfc.fssgl import build_fssgl_file as _build_fssgl
        all_lines = []
        branch    = config["cbs"]["branch_code"]
        card_pool = config["cbs"]["card_pool_account"]
        network   = config.get("cbsmcw", {}).get("network", "MDS")
        date_str  = tran_date.strftime("%d%m%Y")
        ts_str    = run_ts.strftime("%H%M%S")

        from generators.idfc.fssgl import _build_section, _is_rev
        # DOM POS section
        dom_txs = []
        for sg in dom_groups:
            dom_txs.extend(sg.cbs_rows)
        if dom_txs:
            all_lines.extend(_build_section(dom_txs, dom_gl, card_pool, branch,
                                            date_str, dom_section, network=network))
        # INT POS section
        int_txs = []
        for sg in int_groups_list:
            int_txs.extend(sg.cbs_rows)
        if int_txs:
            all_lines.extend(_build_section(int_txs, int_gl, card_pool, branch,
                                            date_str, int_section, network=network))

        if all_lines:
            fname = f"FSS-GL-OUTFILE_{date_str}_{ts_str}.txt"
            p = output_dir / fname
            p.write_text("\n".join(all_lines), encoding="utf-8", errors="replace")
            files_written["fssgl"] = fname
    except Exception as e:
        errors["fssgl"] = str(e)

    pos_groups = [sg for sg in all_groups if sg.base_tx.mcc != "6011"]
    dom_groups = [sg for sg in all_groups if sg.base_tx.tran_category == "D"]
    int_groups = [sg for sg in all_groups if sg.base_tx.tran_category == "I"]

    # Recon summary
    _MISSING = {"MISSING_NETWORK_FILE", "MISSING_SWITCH_FILE", "MISSING_CBS_FILE",
                "MISSING_NETWORK_AND_CBS", "MISSING_SWITCH_AND_CBS",
                "MISSING_NETWORK_AND_SWITCH", "ALL_MISSING", "MISSING_FILE"}
    _CROSSFIRE = {"CROSSFIRE_MISMATCH", "CROSSFIRE_SWITCH", "REVERSAL_MISMATCH"}

    seg = {k: {"ok": 0, "total": 0} for k in ("dom_pos", "int_pos")}
    exc_groups_summary = {"AMOUNT_MISMATCH": 0, "DATE_MISMATCH": 0, "DUPLICATE": 0,
                          "MISSING_FILE": 0, "CROSSFIRE": 0, "OTHER": 0}

    for ss in scenario_summary:
        di = "dom" if ss.get("tran_category") == "D" else "int"
        key = f"{di}_pos"
        if key in seg:
            seg[key]["total"] += ss["count"]
            if ss["is_ok"]:
                seg[key]["ok"] += ss["count"]
        if not ss["is_ok"]:
            action = ss.get("action", "OTHER")
            if "AMOUNT" in action:
                exc_groups_summary["AMOUNT_MISMATCH"] += ss["count"]
            elif "DATE" in action:
                exc_groups_summary["DATE_MISMATCH"] += ss["count"]
            elif "DUPLICATE" in action:
                exc_groups_summary["DUPLICATE"] += ss["count"]
            elif action in _MISSING:
                exc_groups_summary["MISSING_FILE"] += ss["count"]
            elif action in _CROSSFIRE:
                exc_groups_summary["CROSSFIRE"] += ss["count"]
            else:
                exc_groups_summary["OTHER"] += ss["count"]

    total_ok  = sum(v["ok"]    for v in seg.values())
    total_cnt = sum(v["total"] for v in seg.values())
    actual_ok_pct = round(total_ok / total_cnt * 100, 1) if total_cnt else 0.0

    recon_summary = {
        "ok_count": total_ok,
        "exception_count": total_cnt - total_ok,
        "ok_pct": actual_ok_pct,
        "segments": {
            k: {
                "ok": v["ok"],
                "total": v["total"],
                "exception": v["total"] - v["ok"],
                "ok_pct": round(v["ok"] / v["total"] * 100, 1) if v["total"] else 0.0,
            }
            for k, v in seg.items()
        },
        "exception_breakdown": {k: v for k, v in exc_groups_summary.items() if v > 0},
    }

    transaction_index = []
    for sg in all_groups:
        tx = sg.base_tx
        transaction_index.append({
            "group_id": sg.group_id,
            "scenario_code": scenario_code.get(sg.scenario_id, sg.scenario_id),
            "scenario_group": scenario_grp_map.get(sg.scenario_id, ""),
            "scenario_id": sg.scenario_id,
            "scenario_name": sg.scenario_name,
            "tran_category": "DOM" if tx.tran_category == "D" else "INT",
            "is_ok": scenario_is_ok.get(sg.scenario_id, False),
            "lookup": {
                "card_pan_f6l3": tx.card_pan[:6] + tx.card_pan[-3:],
                "auth_code": tx.auth_id[:6],
                "terminal_id": tx.auth_id.zfill(8),
                "rrn": tx.rrn.zfill(12),
                "tran_date": tx.tran_date.strftime("%d-%m-%Y"),
                "amount_rupees": f"{tx.amount / 100:.2f}",
            },
            "file_rows": {
                "t112":   _row_counts(sg.t112_rows),
                "ptlf":   _row_counts(sg.switch_rows),
                "cbs":    _row_counts(sg.cbs_rows),
            },
        })

    manifest = {
        "run_id": run_id,
        "use_case": use_case_id,
        "bank": bank_id,
        "network": "Mastercard",
        "ok_pct_target": ok_pct,
        "tran_date": tran_date.strftime("%Y-%m-%d"),
        "files": files_written,
        "counts": {
            "total_groups": len(all_groups),
            "pos": len(pos_groups),
            "domestic": len(dom_groups),
            "international": len(int_groups),
        },
        "recon_summary": recon_summary,
        "scenarios": scenario_summary,
        "transaction_index": transaction_index,
        "errors": errors,
        "matching_keys": {
            "switch_cbs":     ["card_no_f6l3", "auth_code", "rrn"],
            "switch_network": ["card_no_f6l3", "auth_code", "terminal_id"],
            "cbs_network":    ["card_no_f6l3", "auth_code", "terminal_id"],
        },
    }

    manifest_fname = f"manifest_idfc_mc_{run_id}.json"
    manifest_path = output_dir / manifest_fname
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    zip_fname = f"idfc_mc_{run_id}.zip"
    zip_path  = output_dir / zip_fname
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, fname in files_written.items():
            fp = output_dir / fname
            if fp.exists():
                zf.write(fp, fname)
        zf.write(manifest_path, manifest_fname)

    return {
        "run_id": run_id,
        "zip_path": str(zip_path),
        "zip_name": zip_fname,
        "files": files_written,
        "counts": manifest["counts"],
        "recon_summary": recon_summary,
        "scenarios": scenario_summary,
        "errors": errors,
        "manifest_path": str(manifest_path),
    }
