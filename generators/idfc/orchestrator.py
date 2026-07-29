import json
import random
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta
from math import ceil
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
from generators.idfc.epin import build_epin_file
from generators.idfc.tlf import build_tlf_file
from generators.idfc.ptlf import build_ptlf_file
from generators.idfc.ep747 import build_ep747_file
from generators.idfc.bgl import build_bgl_file

BASE_DIR = Path(__file__).parent.parent.parent


def _build_rows_idfc(base: Transaction, state) -> list:
    """IDFC-only row builder.
      (F,R) tuple → F forward + R reversal rows
      N>0 → N forward rows   N<0 → |N| reversal rows   0 → [fwd, rev]   None → []
    """
    if state is None:
        return []
    is_mc = base.tran_type == "MC"

    def _fwd():
        f = deepcopy(base)
        f.msg_type = "0210"
        if not is_mc:
            f.tran_type = "W1"
            f.tran_code = TRAN_CODE_MAP.get("W1", "020010")
        f.resp_code = "00"
        return f

    def _rev():
        r = deepcopy(base)
        r.msg_type = "0420"
        if is_mc:
            r.tran_code = TRAN_CODE_MAP.get("MR", "020099")
        else:
            r.tran_type = "RV"
            r.tran_code = TRAN_CODE_MAP.get("RV", "020099")
        r.resp_code = "00"
        r.amount = abs(base.amount)
        r.journal_no = generate_journal_no()
        # auth_id stays the same for BOTH ATM and POS reversals:
        #   ATM (CWDR↔CWRR): same auth_id keeps ref_6 consistent across the pair.
        #   POS (PRDR↔PRCR): same auth_id keeps switch_approval_code = Y[:6] for
        #     both rows so PTLF↔CBS 3-way join resolves correctly; MERGE ON
        #     uniqueness is ensured by "001" vs "000" tran_ref suffix (see cbsmcw.py).
        return r

    if isinstance(state, (list, tuple)):
        fc, rc = int(state[0]), int(state[1])
        return [_fwd() for _ in range(fc)] + [_rev() for _ in range(rc)]
    if state > 0:
        return [_fwd() for _ in range(int(state))]
    if state < 0:
        return [_rev() for _ in range(int(abs(state)))]
    return [_fwd(), _rev()]


def _build_idfc_scenario_group(
    scenario_id: str,
    scenario_name: str,
    group_id: str,
    base: Transaction,
    group_states: dict,
    mutation_id: str = "baseline",
) -> ScenarioGroup:
    sg = ScenarioGroup(
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        group_id=group_id,
        base_tx=base,
        mutation_id=mutation_id,
    )
    sg.nfs_rows    = _build_rows_idfc(base, group_states.get("epin"))
    sg.switch_rows = _build_rows_idfc(base, group_states.get("switch_tlf"))
    sg.cbs_rows    = _build_rows_idfc(base, group_states.get("cbs"))
    return sg


def _load_config(bank_id: str) -> dict:
    path = BASE_DIR / "banks" / bank_id / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_use_case(use_case_id: str) -> dict:
    path = BASE_DIR / "use_cases" / f"{use_case_id}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _filter_scenarios(scenarios: list) -> list:
    return list(scenarios)


def _make_idfc_transaction(
    scenario_id: str,
    group_id: str,
    tran_date: datetime,
    tran_type: str,
    tran_category: str,
    config: dict,
    rev_only: bool = False,
) -> Transaction:
    visa_key = "international" if tran_category == "I" else "domestic"
    reporting_for = config.get("visa", {}).get(visa_key, {}).get("reporting_for", [])
    bin_lengths = {b["prefix"]: b.get("length", 16) for b in config.get("bin_ranges", [])}

    bin_prefix = random.choice(reporting_for)["bin"] if reporting_for else "400000"
    pan = generate_pan(prefix=bin_prefix, length=bin_lengths.get(bin_prefix, 16))
    # Reversal-only scenarios use smaller amounts so reversals don't dominate totals
    if rev_only:
        amount = random.choice([200, 500, 1000, 1500, 2000]) * 100
    else:
        amount = random_amount()
    rrn = generate_rrn()
    auth_code = generate_auth_id()

    is_atm  = tran_type in ("ATM", "ACQ_ATM")
    is_acq  = tran_type == "ACQ_ATM"
    is_mc   = tran_type == "MERCH_CR"   # TC 06 merchandise credit / credit voucher
    mcc     = "6011" if is_atm else "5999"

    # Determine NFS tran_type code stored on the Transaction object
    if is_acq:
        nfs_ttype = "OW"
    elif is_atm:
        nfs_ttype = "W1"
    elif is_mc:
        nfs_ttype = "MC"   # maps to TC 06 in EPIN, MCCR in CBS
    else:
        nfs_ttype = "W1"

    tx = Transaction(
        rrn=rrn,
        stan=rrn,
        card_pan=pan,
        account_no=f"000102{random.randint(10000000, 99999999):08d}",
        # terminal_id carries the zero-padded auth code so TLF/PTLF term_id
        # field matches FSS GL ref-6 (auth code) for reconciliation
        terminal_id=auth_code.zfill(8),
        terminal_location=random.choice(TERMINAL_LOCATIONS),
        acquirer_id=str(random.randint(100000, 999999)),
        card_acceptor_id="IDFE" + ("ATM" if is_atm else "POS") + f"{random.randint(1000, 9999)}",
        branch_no="10201",
        ge_branch_no="10201",
        tran_date=tran_date,
        settlement_date=tran_date,
        amount=amount,
        tran_code=TRAN_CODE_MAP.get(nfs_ttype, "020010"),
        tran_type=nfs_ttype,
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


def _file_state_to_group_states(file_states: dict, tran_type: str) -> dict:
    switch_key = "tlf" if tran_type in ("ATM", "ACQ_ATM") else "ptlf"
    switch_state = file_states.get(switch_key)
    nfs_state = file_states.get("epin")
    cbs_states = [file_states.get("cbsmcw"), file_states.get("fssgl")]
    cbs_state = cbs_states[0] if cbs_states[0] is not None else cbs_states[1]

    return {
        "epin": nfs_state,
        "switch_tlf": switch_state,
        "cbs": cbs_state,
    }


def _apply_scenario_mutations(
    sg: ScenarioGroup,
    scenario: dict,
    tran_type: str,
) -> ScenarioGroup:
    amt_delta = scenario.get("amt_delta", {})
    date_delta = scenario.get("date_delta", {})
    dup_files = scenario.get("dup_files", [])

    switch_key = "tlf" if tran_type == "ATM" else "ptlf"

    file_row_map = {
        "epin": sg.nfs_rows,
        switch_key: sg.switch_rows,
        "tlf": sg.switch_rows,
        "ptlf": sg.switch_rows,
        "cbsmcw": sg.cbs_rows,
        "fssgl": sg.cbs_rows,
    }

    for file_key, delta in amt_delta.items():
        rows = file_row_map.get(file_key, [])
        for tx in rows:
            tx.amount = max(0, tx.amount + delta)

    for file_key, days in date_delta.items():
        rows = file_row_map.get(file_key, [])
        for tx in rows:
            tx.tran_date = tx.tran_date + timedelta(days=days)
            tx.settlement_date = tx.tran_date

    _seen_row_ids: set = set()
    for file_key in dup_files:
        rows = file_row_map.get(file_key, [])
        if not rows:
            continue
        row_id = id(rows)
        if row_id in _seen_row_ids:
            # cbsmcw and fssgl share sg.cbs_rows; only add one duplicate
            continue
        _seen_row_ids.add(row_id)
        dup = deepcopy(rows[0])
        if file_key in ("cbsmcw", "fssgl"):
            # Assign a new journal_no so the duplicate CBS row is not byte-identical
            dup.journal_no = generate_journal_no()
        rows.append(dup)

    return sg


def _build_amount_summary(groups: List[ScenarioGroup]) -> dict:
    keys = ["dom_atm", "dom_pos", "dom_mc", "int_atm", "int_pos", "int_mc"]
    fwd = {k: {"count": 0, "amount_rs": 0.0} for k in keys}
    rev = {k: {"count": 0, "amount_rs": 0.0} for k in keys}

    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_type == "OW":
                continue
            cat = "dom" if tx.tran_category == "D" else "int"
            if tx.mcc == "6011":
                typ = "atm"
            elif tx.tran_type == "MC":
                typ = "mc"
            else:
                typ = "pos"
            key = f"{cat}_{typ}"
            bucket = rev if tx.msg_type == "0420" else fwd
            bucket[key]["count"] += 1
            bucket[key]["amount_rs"] += tx.amount / 100

    def _totals(d):
        return {"count": sum(v["count"] for v in d.values()),
                "amount_rs": round(sum(v["amount_rs"] for v in d.values()), 2)}

    for k in keys:
        fwd[k]["amount_rs"] = round(fwd[k]["amount_rs"], 2)
        rev[k]["amount_rs"] = round(rev[k]["amount_rs"], 2)

    tf = _totals(fwd)
    tr = _totals(rev)
    return {
        "forward":  fwd,
        "reversal": rev,
        "total_forward":  tf,
        "total_reversal": tr,
        "net_amount_rs": round(tf["amount_rs"] - tr["amount_rs"], 2),
    }


def generate_idfc_visa(
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
    num_days: int = 1,
    late_network_mismatches: Optional[list] = None,
) -> dict:
    if tran_date is None:
        tran_date = datetime.today()
    if output_dir is None:
        output_dir = BASE_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    num_days = max(1, min(5, int(num_days)))

    config = _load_config(bank_id)
    if gl_accounts:
        if len(gl_accounts) >= 1:
            config["cbs"]["atm_gl_account"] = gl_accounts[0]
        if len(gl_accounts) >= 2:
            config["cbs"]["pos_gl_account"] = gl_accounts[1]
    use_case = _load_use_case(use_case_id)

    scenarios = _filter_scenarios(use_case["scenarios"])
    if selected_scenarios is not None:
        _sel_set = set(selected_scenarios)
        scenarios = [s for s in scenarios if s["id"] in _sel_set]

    scenario_is_ok = {sc["id"]: sc.get("is_ok", False) for sc in scenarios}
    scenario_code = {sc["id"]: sc.get("code", sc["id"]) for sc in scenarios}
    scenario_group_map = {sc["id"]: sc.get("group", "") for sc in scenarios}

    # Split into OK and exception scenarios
    ok_scenarios = [s for s in scenarios if s.get("is_ok", False)]
    exc_scenarios = [s for s in scenarios if not s.get("is_ok", False)]

    # ── Merge custom entries into OK / EXC pools before volume allocation ─────
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
            tt_cs  = cs.get("tran_type", "ATM")
            cat_cs = cs.get("tran_category", "D")

            cbs_fwd = cs.get("cbs_fwd")      # None = file absent
            cbs_rev = cs.get("cbs_rev") or 0
            sw_fwd  = cs.get("switch_fwd")
            sw_rev  = cs.get("switch_rev") or 0
            ep_fwd  = cs.get("epin_fwd")
            ep_rev  = cs.get("epin_rev") or 0

            cbs_present = cbs_fwd is not None
            sw_present  = sw_fwd  is not None
            ep_present  = ep_fwd  is not None

            cbs_tuple = (cbs_fwd, cbs_rev) if cbs_present else None
            sw_tuple  = (sw_fwd,  sw_rev)  if sw_present  else None
            ep_tuple  = (ep_fwd,  ep_rev)  if ep_present  else None

            tuples = [cbs_tuple, sw_tuple, ep_tuple]
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

            sw_key_cs = "tlf" if tt_cs == "ATM" else "ptlf"
            fs_cs: dict = {}
            if cbs_present:
                fs_cs["cbsmcw"] = cbs_tuple
                fs_cs["fssgl"]  = cbs_tuple
            if sw_present:
                fs_cs[sw_key_cs] = sw_tuple
            if ep_present:
                fs_cs["epin"] = ep_tuple

            sc_id   = f"CUSTOM_{idx + 1:04d}"
            sc_code = f"C{idx + 1:03d}"
            sc_name = (f"Custom #{idx + 1}: "
                       f"CBS={_frlbl(cbs_fwd, cbs_rev)} "
                       f"Sw={_frlbl(sw_fwd, sw_rev)} "
                       f"EPIN={_frlbl(ep_fwd, ep_rev)}")

            synthetic = {
                "id": sc_id, "name": sc_name, "is_ok": is_ok_cs,
                "tran_type": tt_cs, "tran_category": cat_cs,
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

    n_ok = len(ok_scenarios)
    n_exc = len(exc_scenarios)

    # If only one type is present, give it all the volume
    if not exc_scenarios:
        ok_volume = volume
        exc_volume = 0
    elif not ok_scenarios:
        ok_volume = 0
        exc_volume = volume
    else:
        # OK scenarios: every scenario gets at least 1 instance; volume scaled to ok_pct
        ok_volume = max(n_ok, round(volume * ok_pct / 100))
        # EXC scenarios: remaining volume only — no forced minimum per scenario type
        exc_volume = max(0, volume - ok_volume)

    ok_total_weight  = sum(s.get("weight", 5) for s in ok_scenarios)  or 1
    exc_total_weight = sum(s.get("weight", 5) for s in exc_scenarios) or 1

    all_groups: List[ScenarioGroup] = []
    group_counter = 0
    scenario_summary = []

    def _allocate_counts(sc_list, vol, total_weight, ensure_one_each: bool) -> dict:
        """Return {scenario_id: count} allocations summing exactly to vol."""
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
                    counts[sc["id"]] += 1; diff -= 1
                elif counts[sc["id"]] > 0:
                    counts[sc["id"]] -= 1; diff += 1
        return counts

    def _process_scenario_list(sc_list, vol, total_weight, ensure_one_each: bool, day_date=None):
        nonlocal group_counter
        _date = day_date if day_date is not None else tran_date
        alloc = _allocate_counts(sc_list, vol, total_weight, ensure_one_each)
        for sc in sc_list:
            count = alloc[sc["id"]]
            if count == 0:
                continue
            tran_type = sc.get("tran_type", "ATM")
            tran_category = sc.get("tran_category", "D")
            file_states_raw = sc.get("file_states", {})
            group_states = _file_state_to_group_states(file_states_raw, tran_type)

            for _ in range(count):
                group_counter += 1
                group_id = f"IDFC{group_counter:06d}"
                tx_time = _date.replace(
                    hour=random.randint(8, 21),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )
                ep_val   = file_states_raw.get("epin")
                if isinstance(ep_val, (list, tuple)):
                    rev_only = ep_val[0] == 0 and ep_val[1] > 0
                else:
                    rev_only = isinstance(ep_val, int) and ep_val < 0
                base = _make_idfc_transaction(
                    sc["id"], group_id, tx_time, tran_type, tran_category, config,
                    rev_only=rev_only,
                )
                sg = _build_idfc_scenario_group(
                    sc["id"], sc["name"], group_id, base, group_states,
                    mutation_id=sc.get("variant", "baseline"),
                )
                _apply_scenario_mutations(sg, sc, tran_type)

                # companion_original: inject a TC05 (POS) or TC07 (ATM) row into
                # EPIN only, representing the original purchase the MC was issued
                # against.  Recon joins TC06↔TC05 on:
                #   account_number, AUTHORIZATION_CODE, ACQUIRER_REFERENCE_NUMBER,
                #   TERMINAL_ID, MERCHANT_CATEGORY_CODE
                # ARN is derived from rrn; AUTHORIZATION_CODE = auth_id[:6];
                # TERMINAL_ID = terminal_id (= auth_id.zfill(8)).
                # So companion MUST share rrn, auth_id, terminal_id, account_no,
                # mcc, and tran_date with TC06 — only the transaction code differs.
                companion_type = sc.get("companion_original")
                if companion_type and sg.nfs_rows:
                    comp = deepcopy(base)
                    # Keep same: rrn, auth_id, terminal_id, account_no, tran_date
                    # — these are the 5 recon join keys with TC06.
                    comp.tran_type = "W1"
                    comp.msg_type  = "0210"
                    comp.tran_code = TRAN_CODE_MAP.get("W1", "020010")
                    comp.resp_code = "00"
                    comp.mcc       = "6011" if companion_type == "ATM" else "5999"
                    # companion_amt_delta shifts only the TC05/TC07 amount, leaving
                    # TC06/TC26 at the base amount — creates an amount mismatch
                    # between the original purchase and the merchandise credit.
                    comp_delta = sc.get("companion_amt_delta", 0)
                    if comp_delta:
                        comp.amount = max(100, comp.amount + comp_delta)
                    # prepend so original purchase appears before the MC in EPIN
                    sg.nfs_rows.insert(0, comp)

                all_groups.append(sg)

            scenario_summary.append({
                "code": sc.get("code", sc["id"]),
                "group": sc.get("group", ""),
                "id": sc["id"],
                "name": sc["name"],
                "tran_type": tran_type,
                "tran_category": tran_category,
                "action": sc.get("action", ""),
                "is_ok": sc.get("is_ok", False),
                "count": count,
            })

    run_ts = datetime.now()
    run_id = run_ts.strftime("%Y%m%d%H%M%S")

    def _row_counts(rows) -> dict:
        fwd = sum(1 for t in rows if t.msg_type != "0420")
        rev = sum(1 for t in rows if t.msg_type == "0420")
        return {"total": fwd + rev, "forward": fwd, "reversal": rev}

    def _build_day_files_inner(day_groups, day_date, day_run_ts):
        """Build all IDFC VISA files for one day's scenario groups."""
        fw: dict = {}
        errs: dict = {}
        try:
            content, fname = build_cbsmcw_file(
                day_groups, day_date, config,
                no_duplicates=no_cbsmcw_duplicates,
                allowed_tran_types={"CWDR", "CWRR", "PRDR", "PRCR"},
            )
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["cbsmcw"] = fname
        except Exception as e:
            errs["cbsmcw"] = str(e)
        try:
            content, fname = build_fssgl_file(day_groups, day_date, config, day_run_ts)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["fssgl"] = fname
        except Exception as e:
            errs["fssgl"] = str(e)
        try:
            content, fname = build_epin_file(day_groups, config, dom=True, run_ts=day_run_ts, tran_date=day_date)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["epin_dom"] = fname
        except Exception as e:
            errs["epin_dom"] = str(e)
        try:
            content, fname = build_epin_file(day_groups, config, dom=False, run_ts=day_run_ts, tran_date=day_date)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["epin_int"] = fname
        except Exception as e:
            errs["epin_int"] = str(e)
        try:
            content, fname = build_tlf_file(day_groups, day_date, config, day_run_ts)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["tlf"] = fname
        except Exception as e:
            errs["tlf"] = str(e)
        try:
            content, fname = build_ptlf_file(day_groups, day_date, config, day_run_ts)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["ptlf"] = fname
        except Exception as e:
            errs["ptlf"] = str(e)
        try:
            content, fname = build_ep747_file(day_groups, day_date, config, dom=True)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["ep747_dom"] = fname
        except Exception as e:
            errs["ep747_dom"] = str(e)
        try:
            content, fname = build_ep747_file(day_groups, day_date, config, dom=False)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["ep747_int"] = fname
        except Exception as e:
            errs["ep747_int"] = str(e)
        try:
            content, fname = build_bgl_file(day_groups, day_date, config)
            (output_dir / fname).write_text(content, encoding="ascii", errors="replace")
            fw["bgl"] = fname
        except Exception as e:
            errs["bgl"] = str(e)
        return fw, errs

    files_written: dict = {}
    errors: dict = {}

    if num_days <= 1:
        _process_scenario_list(ok_scenarios, ok_volume, ok_total_weight, ensure_one_each=True)
        _process_scenario_list(exc_scenarios, exc_volume, exc_total_weight, ensure_one_each=False)
        random.shuffle(all_groups)
        fw, errs = _build_day_files_inner(all_groups, tran_date, run_ts)
        files_written.update(fw)
        errors.update(errs)
    else:
        # Multi-day: generate `volume` transactions per day.  A single "late
        # network" transactions are planted in CBS/switch on day 0 (EPIN absent)
        # and arrive in EPIN only on the last day, simulating delayed settlement.
        # One independent transaction is created per selected mismatch type.
        _valid_mismatches = ("none", "lower", "higher")
        _mismatches = [m for m in (late_network_mismatches or ["none"]) if m in _valid_mismatches]
        if not _mismatches:
            _mismatches = ["none"]

        _late_names = {
            "none":   "Late Network — EPIN Arrives, Exact Amount Match",
            "lower":  "Late Network — EPIN Amount Lower Than CBS (Partial Settlement)",
            "higher": "Late Network — EPIN Amount Higher Than CBS (Over-Settlement)",
        }

        late_day0_sgs: List[ScenarioGroup] = []
        late_last_sgs: List[ScenarioGroup] = []

        for _i, _mismatch in enumerate(_mismatches):
            _late_tran_time = tran_date.replace(
                hour=14 + _i, minute=15, second=0, microsecond=0,
            )
            _late_base = _make_idfc_transaction(
                f"LATE_EPIN_{_mismatch.upper()}", f"LATE{_i:06d}",
                _late_tran_time, "ATM", "D", config,
            )
            _late_fwd = deepcopy(_late_base)
            _late_fwd.msg_type  = "0210"
            _late_fwd.tran_type = "W1"
            _late_fwd.tran_code = TRAN_CODE_MAP.get("W1", "020010")
            _late_fwd.resp_code = "00"

            _sg0 = ScenarioGroup(
                scenario_id=f"LATE_EPIN_DAY0_{_mismatch.upper()}",
                scenario_name=f"Late Network — CBS/Switch Present, EPIN Missing (Day 0) [{_mismatch}]",
                group_id=f"LATE_DAY0_{_i + 1:03d}",
                base_tx=_late_base,
                mutation_id="baseline",
            )
            _sg0.cbs_rows    = [_late_fwd]
            _sg0.switch_rows = [_late_fwd]
            _sg0.nfs_rows    = []
            late_day0_sgs.append(_sg0)

            # EPIN side: apply amount mismatch if selected
            if _mismatch == "lower":
                _late_fwd_epin = deepcopy(_late_fwd)
                _late_fwd_epin.amount = max(100, int(_late_base.amount * 0.9))
            elif _mismatch == "higher":
                _late_fwd_epin = deepcopy(_late_fwd)
                _late_fwd_epin.amount = int(_late_base.amount * 1.1)
            else:
                _late_fwd_epin = _late_fwd

            _sgN = ScenarioGroup(
                scenario_id=f"LATE_EPIN_LAST_{_mismatch.upper()}",
                scenario_name=_late_names[_mismatch],
                group_id=f"LATE_LAST_{_i + 1:03d}",
                base_tx=_late_base,
                mutation_id="baseline",
            )
            _sgN.cbs_rows    = []
            _sgN.switch_rows = []
            _sgN.nfs_rows    = [_late_fwd_epin]
            late_last_sgs.append(_sgN)

        all_accumulated: List[ScenarioGroup] = []
        for day_idx in range(num_days):
            day_date = tran_date + timedelta(days=day_idx)
            all_groups.clear()
            _process_scenario_list(
                ok_scenarios, ok_volume, ok_total_weight,
                ensure_one_each=True, day_date=day_date,
            )
            _process_scenario_list(
                exc_scenarios, exc_volume, exc_total_weight,
                ensure_one_each=False, day_date=day_date,
            )
            if day_idx == 0:
                all_groups.extend(late_day0_sgs)
            if day_idx == num_days - 1:
                all_groups.extend(late_last_sgs)
            random.shuffle(all_groups)

            day_run_ts = run_ts + timedelta(seconds=day_idx)
            fw, errs = _build_day_files_inner(all_groups, day_date, day_run_ts)
            suffix = f"_d{day_idx}"
            files_written.update({k + suffix: v for k, v in fw.items()})
            errors.update({k + suffix: v for k, v in errs.items() if v})
            all_accumulated.extend(all_groups)

        all_groups.clear()
        all_groups.extend(all_accumulated)

    # Per-group transaction index: lookup keys needed to locate each transaction in every file
    transaction_index = []
    for sg in all_groups:
        tx = sg.base_tx
        transaction_index.append({
            "group_id": sg.group_id,
            "scenario_code": scenario_code.get(sg.scenario_id, sg.scenario_id),
            "scenario_group": scenario_group_map.get(sg.scenario_id, ""),
            "scenario_id": sg.scenario_id,
            "scenario_name": sg.scenario_name,
            "tran_type": "ATM" if tx.mcc == "6011" else "POS",
            "tran_category": "DOM" if tx.tran_category == "D" else "INT",
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
                "epin": _row_counts(sg.nfs_rows),
                "switch": _row_counts(sg.switch_rows),
                "cbs": _row_counts(sg.cbs_rows),
            },
        })

    atm_groups = [sg for sg in all_groups if sg.base_tx.mcc == "6011"]
    pos_groups = [sg for sg in all_groups if sg.base_tx.mcc != "6011"]
    dom_groups = [sg for sg in all_groups if sg.base_tx.tran_category == "D"]
    int_groups = [sg for sg in all_groups if sg.base_tx.tran_category == "I"]

    # Build reconciliation summary from scenario_summary
    _MISSING = {"MISSING_NETWORK_FILE", "MISSING_SWITCH_FILE", "MISSING_CBS_FILE",
                "MISSING_NETWORK_AND_CBS", "MISSING_SWITCH_AND_CBS",
                "MISSING_NETWORK_AND_SWITCH", "ALL_MISSING",
                "FEE_MISSING_CBS", "FEE_MISSING_EPIN", "FEE_MISSING_SWITCH"}
    _CROSSFIRE = {"CROSSFIRE_MISMATCH", "CROSSFIRE_SWITCH", "REVERSAL_MISMATCH"}

    seg = {k: {"ok": 0, "total": 0} for k in ("dom_atm", "int_atm", "dom_pos", "int_pos")}
    exc_groups = {"AMOUNT_MISMATCH": 0, "DATE_MISMATCH": 0, "DUPLICATE": 0,
                  "DOUBLE_DEBIT": 0, "MISSING_FILE": 0, "MISSING_WITH_AMOUNT_MISMATCH": 0,
                  "MC_ORIGINAL_ABSENT": 0, "MC_AMOUNT_MISMATCH": 0,
                  "CROSSFIRE": 0, "OTHER": 0}

    for ss in scenario_summary:
        di = "dom" if ss["tran_category"] == "D" else "int"
        tt = ss["tran_type"].lower()
        key = f"{di}_{tt}"
        if key in seg:
            seg[key]["total"] += ss["count"]
            if ss["is_ok"]:
                seg[key]["ok"] += ss["count"]
        if not ss["is_ok"]:
            action = ss.get("action", "OTHER")
            if action == "AMOUNT_MISMATCH":
                exc_groups["AMOUNT_MISMATCH"] += ss["count"]
            elif action == "DATE_MISMATCH":
                exc_groups["DATE_MISMATCH"] += ss["count"]
            elif action in ("DUPLICATE",):
                exc_groups["DUPLICATE"] += ss["count"]
            elif action == "DOUBLE_DEBIT":
                exc_groups["DOUBLE_DEBIT"] += ss["count"]
            elif action in _MISSING:
                exc_groups["MISSING_FILE"] += ss["count"]
            elif action == "MISSING_WITH_AMOUNT_MISMATCH":
                exc_groups["MISSING_WITH_AMOUNT_MISMATCH"] += ss["count"]
            elif action == "MC_ORIGINAL_ABSENT":
                exc_groups["MC_ORIGINAL_ABSENT"] += ss["count"]
            elif action == "MC_AMOUNT_MISMATCH":
                exc_groups["MC_AMOUNT_MISMATCH"] += ss["count"]
            elif action in _CROSSFIRE:
                exc_groups["CROSSFIRE"] += ss["count"]
            else:
                exc_groups["OTHER"] += ss["count"]

    total_ok_cnt = sum(v["ok"] for v in seg.values())
    total_cnt = sum(v["total"] for v in seg.values())
    actual_ok_pct = round(total_ok_cnt / total_cnt * 100, 1) if total_cnt else 0.0

    recon_summary = {
        "ok_count": total_ok_cnt,
        "exception_count": total_cnt - total_ok_cnt,
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
        "exception_breakdown": {k: v for k, v in exc_groups.items() if v > 0},
    }

    file_lookup_guide = {
        "_note": (
            "Use each group's 'lookup' values to locate its rows in the generated files. "
            "All positions below are 1-indexed unless noted otherwise."
        ),
        "rrn": {
            "cbsmcw":  "pipe col 4 (0-indexed) — 12-char zero-padded",
            "fssgl":   "pipe col 16 (0-indexed) — 12-char zero-padded",
            "ptlf":    "chars 297-308 (Seq_Num)",
            "tlf":     "fplace 203-214 (Seq_num_Normal)",
        },
        "card_pan": {
            "cbsmcw":  "pipe col 3 (0-indexed) — 16 chars",
            "fssgl":   "pipe col 17 (0-indexed) — 16 chars",
            "ptlf":    "chars 44-62 (Card_Num, 19-char field)",
            "tlf":     "fplace 72-90 (Card_num, 19-char field)",
            "epin_x00": "chars 5-20 (AccountNumber, 16 chars) — rows where col3='0', col4='0'",
        },
        "terminal_id": {
            "_note":   "8-char zero-padded auth code; matches Ref6 in FSS GL",
            "cbsmcw":  "pipe col 10 (auth_code)",
            "fssgl":   "pipe col 15 (Ref6)",
            "ptlf":    "chars 101-116 (Retailer_Term_ID, 16-char field — first 8 are auth code)",
            "tlf":     "fplace 48-63 (Term_id, 16-char field — first 8 are auth code)",
            "epin_x01": "chars 96-103 (TerminalID) — rows where col3='0', col4='1'",
        },
        "auth_resp": {
            "_note":   "first 6 chars of auth code; matches AuthorizationCode in EPIN x00",
            "ptlf":    "chars 623-628 (ApprovalCode, 6 non-space chars in 8-char field)",
            "tlf":     "fplace 518-523 (Auth_resp_id, 6 non-space chars in 7-char field)",
            "epin_x00": "chars 152-157 (AuthorizationCode, first 6 of 7-char field) — rows where col3='0', col4='0'",
        },
    }

    manifest = {
        "run_id": run_id,
        "use_case": use_case_id,
        "bank": bank_id,
        "ok_pct_target": ok_pct,
        "tran_date": tran_date.strftime("%Y-%m-%d"),
        "num_days": num_days,
        "files": files_written,
        "counts": {
            "total_groups": len(all_groups),
            "atm": len(atm_groups),
            "pos": len(pos_groups),
            "domestic": len(dom_groups),
            "international": len(int_groups),
        },
        "recon_summary": recon_summary,
        "scenarios": scenario_summary,
        "file_lookup_guide": file_lookup_guide,
        "transaction_index": transaction_index,
        "errors": errors,
    }

    manifest_fname = f"manifest_idfc_{run_id}.json"
    manifest_path = output_dir / manifest_fname
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    zip_fname = f"idfc_visa_{run_id}.zip"
    zip_path = output_dir / zip_fname
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
        "amount_summary": _build_amount_summary(all_groups),
        "scenarios": scenario_summary,
        "errors": errors,
        "manifest_path": str(manifest_path),
    }
