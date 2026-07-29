"""
Reconciliation Test Data Generator
Core generation engine for NFS ATM Issuer use case
"""

import random
import string
import yaml
import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from copy import deepcopy

BASE_DIR = Path(__file__).parent.parent


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class Transaction:
    """A single ATM transaction — shared across all three participant files"""
    rrn: str                    # Retrieval Reference Number (12 digits) — links all files
    stan: str                   # System Trace Audit Number (12 digits)
    card_pan: str               # 16–19 digit PAN
    account_no: str             # Bank account number (16 digits)
    terminal_id: str            # ATM terminal ID
    terminal_location: str
    acquirer_id: str
    card_acceptor_id: str
    branch_no: str
    ge_branch_no: str
    tran_date: datetime
    settlement_date: datetime
    amount: int                 # in paise (100 = ₹1)
    tran_code: str              # CBS transaction code
    tran_type: str              # NFS transaction type (W1, BI, etc.)
    resp_code: str              # Response code (00=approved)
    approval_number: str
    auth_id: str                # Authorization ID from switch
    balance: int                # Account balance after txn (paise)
    journal_no: str
    teller_no: str
    msg_type: str               # 0210, 0420, etc.
    scenario_id: str            # which recon scenario this row is part of
    scenario_group: str         # unique ID for the transaction group
    mcc: str = "6011"
    currency_code: str = "356"
    from_account_type: str = "10"
    to_account_type: str = "  "
    member_number: str = "1"
    network_id: str = "NFS"
    participant_id: str = "356"
    tran_category: str = "D"    # "D" = domestic, "I" = international


@dataclass
class ScenarioGroup:
    """One recon scenario instance = a base tx + per-file row lists"""
    scenario_id: str
    scenario_name: str
    group_id: str
    base_tx: Transaction
    mutation_id: str = ""
    nfs_rows: List[Transaction] = field(default_factory=list)
    switch_rows: List[Transaction] = field(default_factory=list)
    cbs_rows: List[Transaction] = field(default_factory=list)
    t112_rows: List[Transaction] = field(default_factory=list)

    # Legacy property shims so old files_present-based code still works unchanged
    @property
    def nfs_row(self):
        return self.nfs_rows[0] if self.nfs_rows else None

    @nfs_row.setter
    def nfs_row(self, tx):
        if tx is not None:
            if self.nfs_rows:
                self.nfs_rows[0] = tx
            else:
                self.nfs_rows.append(tx)

    @property
    def nfs_extra_row(self):
        return self.nfs_rows[1] if len(self.nfs_rows) > 1 else None

    @nfs_extra_row.setter
    def nfs_extra_row(self, tx):
        if tx is not None:
            if len(self.nfs_rows) > 1:
                self.nfs_rows[1] = tx
            else:
                self.nfs_rows.append(tx)

    @property
    def switch_row(self):
        return self.switch_rows[0] if self.switch_rows else None

    @switch_row.setter
    def switch_row(self, tx):
        if tx is not None:
            if self.switch_rows:
                self.switch_rows[0] = tx
            else:
                self.switch_rows.append(tx)

    @property
    def cbs_row(self):
        return self.cbs_rows[0] if self.cbs_rows else None

    @cbs_row.setter
    def cbs_row(self, tx):
        if tx is not None:
            if self.cbs_rows:
                self.cbs_rows[0] = tx
            else:
                self.cbs_rows.append(tx)

    @property
    def cbs_extra_row(self):
        return self.cbs_rows[1] if len(self.cbs_rows) > 1 else None

    @cbs_extra_row.setter
    def cbs_extra_row(self, tx):
        if tx is not None:
            if len(self.cbs_rows) > 1:
                self.cbs_rows[1] = tx
            else:
                self.cbs_rows.append(tx)


# ─────────────────────────────────────────────
# SYNTHETIC DATA HELPERS
# ─────────────────────────────────────────────

def luhn_checksum(number: str) -> int:
    digits = [int(d) for d in number]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        total += sum(divmod(d * 2, 10))
    return total % 10

def generate_pan(prefix="622018", length=16) -> str:
    """Generate a Luhn-valid PAN"""
    body = prefix + ''.join([str(random.randint(0, 9)) for _ in range(length - len(prefix) - 1)])
    check = (10 - luhn_checksum(body + '0')) % 10
    return body + str(check)

# 12-digit sequential RRN counter — never starts with 0, no zero-padding needed
_rrn_counter = random.randint(100000000000, 499999999999)

def generate_rrn() -> str:
    """12-digit sequential RRN — fits fixed-width fields without leading zeros."""
    global _rrn_counter
    _rrn_counter += 1
    return str(_rrn_counter)   # e.g. "3101", "3102" — matches real file pattern

def generate_stan(rrn: str) -> str:
    """STAN = RRN for ATM-NFS transactions (same sequence reference in both NFS fields)."""
    return rrn

def generate_account_no(branch: str = "04234") -> str:
    """Generate a realistic account number"""
    return f"0000{branch}{str(random.randint(100000000, 999999999))}"[:16]

def generate_terminal_id(branch: str = "04234") -> str:
    return f"S5DP{branch}{str(random.randint(1, 999)).zfill(3)}"

def generate_approval_code() -> str:
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def generate_acq_terminal_id() -> str:
    """Generate 8-char ATM terminal ID for NFS acquirer (e.g. EN401156)"""
    prefix = random.choice(_ACQ_TERMINAL_PREFIXES)
    return prefix + str(random.randint(100000, 999999))

def generate_auth_id() -> str:
    return str(random.randint(10000000, 99999999))  # 8 digits, never starts with 0

def generate_journal_no() -> str:
    return str(random.randint(100000000, 999999999))

def random_amount(min_rs=100, max_rs=20000) -> int:
    """Return amount in paise, multiples of 100 (₹ multiples)"""
    return random.choice([500, 1000, 2000, 3000, 5000, 10000, 15000, 20000]) * 100

def random_balance(amount: int, min_balance: int = 100000) -> int:
    """Return a plausible balance after debit"""
    base = random.randint(min_balance, 5000000)  # ₹1000 to ₹50000
    return base - amount + random.randint(0, 50000)

TERMINAL_LOCATIONS = [
    # Each entry: name(25) + owner(22) + city(13) = 60 chars total
    "ATM SWITCH CENTER        SBI  ATM SWITCH CENTE NAVI MUMBAI  ",
    "SBI ATM NR MAIN MARKET   STATE BANK OF INDIA   PUNE         ",
    "HDFC ATM SECTOR 14       HDFC BANK LTD         GURUGRAM     ",
    "AXIS BANK ATM MAIN RD    AXIS BANK LIMITED     MUMBAI       ",
    "CANARA ATM BRANCH 001    CANARA BANK           BANGALORE    ",
]

# NFS Acquirer terminal locations: name(23) + city(13) + state(2) + country(2) = 40 chars
ACQ_TERMINAL_LOCATIONS = [
    "MUM ARR IN ATM 1       " "MUMBAI       " "MH" "IN",
    "GOREGAON WEST BRANCH AT" "MUMBAI       " "MH" "IN",
    "ANDHERI WEST ATM       " "MUMBAI       " "MH" "IN",
    "ROYAPURAM CHENNAI ATM  " "CHENNAI      " "TN" "IN",
    "IRUMBULIYUR GST ROAD AT" "CHENNAI      " "TN" "IN",
    "T NAGAR BRANCH ATM     " "CHENNAI      " "TN" "IN",
    "VASANT KUNJ ATM        " "NEW DELHI    " "DL" "IN",
    "CONNAUGHT PLACE ATM    " "NEW DELHI    " "DL" "IN",
    "PARADISE SECUNDERABAD  " "HYDERABAD    " "TS" "IN",
    "BANJARA HILLS ATM      " "HYDERABAD    " "TS" "IN",
    "GAUHATI MEDICAL COLLEGE" "GUWAHATI     " "AS" "IN",
    "KORAMANGALA ATM        " "BANGALORE    " "KA" "IN",
    "MG ROAD BRANCH ATM     " "BANGALORE    " "KA" "IN",
    "AUNDH BRANCH ATM       " "PUNE         " "MH" "IN",
    "CIVIL LINES ATM        " "JAIPUR       " "RJ" "IN",
]

# NFS Acquirer transaction type codes (numeric 2-char, per NFS standard)
ACQ_TRAN_TYPE_MAP = {
    "W1": "04",   # Cash withdrawal / disbursement (forward)
    "OW": "04",   # On-us withdrawal (acquirer)
    "BI": "05",   # Balance inquiry
    "RV": "24",   # Reversal of cash disbursement
    "MR": "24",   # Merchandise credit reversal → reversal code
    "TF": "04",   # Funds transfer
    "DP": "05",   # Deposit
    "PC": "83",   # PIN change
}

_ACQ_TERMINAL_PREFIXES = ["EN", "ER", "ES", "EW", "MH", "TN", "DL", "KA", "TS"]

TRAN_CODE_MAP = {
    "W1": "020010",  # Withdrawal
    "BI": "020030",  # Balance Inquiry
    "TF": "020040",  # Transfer
    "DP": "020020",  # Deposit
    "RV": "020099",  # Reversal (CBS side)
    "PC": "020081",  # PIN Change
    "MS": "020030",  # Mini Statement (same code as BI)
    "MC": "020050",  # Merchandise Credit (TC 06) — credit voucher from merchant
    "MR": "020099",  # Merchandise Credit Reversal (TC 26)
}

NFS_TRAN_TYPES = {
    "withdrawal": "W1",
    "balance_inquiry": "BI",
    "transfer": "TF",
    "deposit": "DP",
}


# ─────────────────────────────────────────────
# TRANSACTION FACTORY
# ─────────────────────────────────────────────

def make_base_transaction(scenario_id: str, group_id: str, tran_date: datetime,
                           tran_type: str = "withdrawal") -> Transaction:
    branch = "04234"
    pan = generate_pan()
    amount = random_amount()

    rrn = generate_rrn()
    tx = Transaction(
        rrn=rrn,
        stan=generate_stan(rrn),
        card_pan=pan,
        account_no=generate_account_no(branch),
        terminal_id=generate_terminal_id(branch),
        terminal_location=random.choice(TERMINAL_LOCATIONS),
        acquirer_id="34723282962",
        card_acceptor_id="ATM" + branch + "001",
        branch_no=branch,
        ge_branch_no=branch,
        tran_date=tran_date,
        settlement_date=tran_date,
        amount=amount,
        tran_code=TRAN_CODE_MAP.get(NFS_TRAN_TYPES.get(tran_type, "W1"), "020010"),
        tran_type=NFS_TRAN_TYPES.get(tran_type, "W1"),
        resp_code="00",
        approval_number=generate_approval_code(),
        auth_id=generate_auth_id(),
        balance=random_balance(amount),
        journal_no=generate_journal_no(),
        teller_no="0000000009900001",
        msg_type="0210",
        scenario_id=scenario_id,
        scenario_group=group_id,
    )
    return tx


# ─────────────────────────────────────────────
# SCENARIO PLANNER
# ─────────────────────────────────────────────

def load_use_case(use_case_id: str) -> dict:
    path = BASE_DIR / "use_cases" / f"{use_case_id}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def plan_scenarios(use_case: dict, volume: int) -> List[dict]:
    """
    Given a volume N, allocate transactions to scenarios.
    Guarantees at least 1 of every mandatory scenario type.
    Returns list of (scenario, count) tuples.
    """
    scenarios = [s for s in use_case["scenarios"]]
    n = len(scenarios)
    if volume < n:
        volume = n  # minimum = 1 per scenario

    # Reserve 1 for each scenario (coverage floor)
    remaining = volume - n
    weights = [s.get("weight", 5) for s in scenarios]
    total_weight = sum(weights)

    # Distribute remaining proportionally by weight
    allocations = [1] * n
    for i, w in enumerate(weights):
        extra = round((w / total_weight) * remaining)
        allocations[i] += extra

    # Adjust rounding errors
    diff = volume - sum(allocations)
    if diff > 0:
        allocations[0] += diff
    elif diff < 0:
        allocations[0] = max(1, allocations[0] + diff)

    return [(scenarios[i], allocations[i]) for i in range(n)]


# ─────────────────────────────────────────────
# 64-CASE × MUTATION MATRIX SUPPORT
# ─────────────────────────────────────────────

def _build_rows_from_state(base: Transaction, state) -> List[Transaction]:
    """Build transaction rows for one file based on case_matrix state.
      1  → [forward]   -1 → [reversal]   0 → [forward, reversal]   None → []
    """
    if state is None:
        return []
    is_mc = base.tran_type == "MC"
    fwd = deepcopy(base)
    fwd.msg_type = "0210"
    if not is_mc:
        fwd.tran_type = "W1"
        fwd.tran_code = TRAN_CODE_MAP.get("W1", "020010")
    fwd.resp_code = "00"
    if state == 1:
        return [fwd]
    rev = deepcopy(base)
    rev.msg_type = "0420"
    if is_mc:
        # Keep tran_type="MC"; msg_type="0420" signals reversal to EPIN/PTLF/CBS builders
        rev.tran_code = TRAN_CODE_MAP.get("MR", "020099")
    else:
        rev.tran_type = "RV"
        rev.tran_code = TRAN_CODE_MAP.get("RV", "020099")
    rev.resp_code = "00"
    rev.amount = abs(base.amount)
    if state == -1:
        return [rev]
    # state == 0: both
    return [fwd, rev]


def build_scenario_group_from_states(
    scenario_id: str,
    scenario_name: str,
    group_id: str,
    base: Transaction,
    file_states: dict,
    mutation_id: str = "baseline",
    network_file_key: str = "nfs",
) -> "ScenarioGroup":
    """Build a ScenarioGroup from 64-case file_states format."""
    sg = ScenarioGroup(
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        group_id=group_id,
        base_tx=base,
        mutation_id=mutation_id,
    )
    net_key = network_file_key if network_file_key in file_states else "nfs"
    sg.nfs_rows    = _build_rows_from_state(base, file_states.get(net_key))
    sg.switch_rows = _build_rows_from_state(base, file_states.get("switch_tlf"))
    sg.cbs_rows    = _build_rows_from_state(base, file_states.get("cbs"))
    return sg


def get_mutation_combos(
    mutation_matrix: dict,
    file_states: dict,
    network_file_key: str = "nfs",
) -> List[dict]:
    """Return all applicable mutation combos for the given file_states."""
    present = {k for k, v in file_states.items() if v is not None}
    multi = len(present) >= 2

    def resolve(k):
        return network_file_key if k == "nfs" else k

    def ok_amount(m):
        d = m.get("deltas", {})
        return not d or (multi and all(resolve(k) in present for k in d))

    def ok_date(m):
        o = m.get("offsets", {})
        return not o or (multi and all(resolve(k) in present for k in o))

    def ok_dup(m):
        fs = m.get("dup_files", [])
        return not fs or all(resolve(f) in present for f in fs)

    amounts = [m for m in mutation_matrix.get("amount", []) if ok_amount(m)] or \
              [{"id": "amt_baseline", "deltas": {}}]
    dates   = [m for m in mutation_matrix.get("date",   []) if ok_date(m)]   or \
              [{"id": "date_baseline", "offsets": {}}]
    dups    = [m for m in mutation_matrix.get("duplicate", []) if ok_dup(m)] or \
              [{"id": "dup_none", "dup_files": []}]

    combos = []
    for a in amounts:
        for d in dates:
            for dup in dups:
                combos.append({
                    "id": f"{a['id']}+{d['id']}+{dup['id']}",
                    "amount_deltas": {resolve(k): v for k, v in a.get("deltas", {}).items()},
                    "date_offsets":  {resolve(k): v for k, v in d.get("offsets", {}).items()},
                    "dup_files":     [resolve(f) for f in dup.get("dup_files", [])],
                })
    return combos


def _apply_mutation_combo(
    sg: "ScenarioGroup",
    combo: dict,
    network_file_key: str = "nfs",
) -> "ScenarioGroup":
    """Apply an amount+date+dup mutation combo to a ScenarioGroup in-place."""
    row_map = {
        network_file_key: sg.nfs_rows,
        "switch_tlf":     sg.switch_rows,
        "cbs":            sg.cbs_rows,
    }
    for file_key, delta in combo.get("amount_deltas", {}).items():
        for tx in row_map.get(file_key, []):
            tx.amount = max(0, tx.amount + delta)
    for file_key, days in combo.get("date_offsets", {}).items():
        for tx in row_map.get(file_key, []):
            tx.tran_date = tx.tran_date + timedelta(days=days)
            tx.settlement_date = tx.tran_date
    for file_key in combo.get("dup_files", []):
        rows = row_map.get(file_key, [])
        if rows:
            rows.append(deepcopy(rows[0]))
    return sg


def plan_scenarios_exhaustive(
    use_case: dict,
    volume: int,
    network_file_key: str = "nfs",
) -> List[tuple]:
    """Plan all 64-case × mutation combos. Returns (scenario, combo, count) list.
    Minimum volume is auto-raised to cover every combo at least once.
    """
    mutation_matrix = use_case.get("mutation_matrix", {})
    all_pairs = []
    for sc in use_case["scenarios"]:
        file_states = sc.get("file_states", {})
        combos = get_mutation_combos(mutation_matrix, file_states, network_file_key)
        for combo in combos:
            all_pairs.append((sc, combo))

    n = len(all_pairs)
    if volume < n:
        volume = n

    allocations = [1] * n
    remaining = volume - n
    weights = [p[0].get("weight", 1) for p in all_pairs]
    total_w = sum(weights)
    for i, w in enumerate(weights):
        allocations[i] += round((w / total_w) * remaining)

    diff = volume - sum(allocations)
    if diff > 0:
        allocations[0] += diff
    elif diff < 0:
        allocations[0] = max(1, allocations[0] + diff)

    return [(all_pairs[i][0], all_pairs[i][1], allocations[i]) for i in range(n)]


# ─────────────────────────────────────────────
# SCENARIO BUILDERS
# ─────────────────────────────────────────────

def build_scenario_group(scenario: dict, group_id: str, tran_date: datetime) -> ScenarioGroup:
    base = make_base_transaction(scenario["id"], group_id, tran_date)
    sg = ScenarioGroup(
        scenario_id=scenario["id"],
        scenario_name=scenario["name"],
        group_id=group_id,
        base_tx=base,
    )

    files_present = scenario.get("files_present", ["nfs", "switch_tlf", "cbs"])
    mutations = scenario.get("mutations", {})

    # "all" mutations apply to every file (used for decline/BI scenarios)
    all_mut = mutations.get("all", {})

    # Track how many times each file type appears (for duplicate scenarios)
    switch_count = 0

    for file_id in files_present:
        if file_id.startswith("nfs"):
            tx = deepcopy(base)
            _apply_mutations(tx, {**all_mut, **mutations.get("nfs", {})}, "nfs")
            if sg.nfs_row is None:
                sg.nfs_row = tx
            else:
                sg.nfs_extra_row = tx

        elif file_id == "switch_tlf":
            tx = deepcopy(base)
            _apply_mutations(tx, {**all_mut, **mutations.get("switch_tlf", {})}, "switch_tlf")
            switch_count += 1
            if switch_count == 1:
                sg.switch_row = tx
            # second switch entry goes into nfs_extra_row slot if not used, else ignored
            # (switch duplicates tracked via manifest)

        elif file_id.startswith("cbs"):
            tx = deepcopy(base)
            _apply_mutations(tx, {**all_mut, **mutations.get("cbs", {})}, "cbs")
            if sg.cbs_row is None:
                sg.cbs_row = tx
            else:
                sg.cbs_extra_row = tx

    # ── Reversal group: add original + reversal rows ──────────
    if mutations.get("type") == "reversal":
        sg = _build_reversal_group(sg, base, tran_date, mutations)

    # ── Chargeback extra CBS row (scenario 7.5) ───────────────
    cbs_extra_mut = mutations.get("cbs_extra", {})
    if cbs_extra_mut.get("type") == "chargeback" and sg.cbs_row is not None:
        cb_tx = deepcopy(sg.cbs_row)
        cb_tx.msg_type = "0420"
        cb_tx.tran_type = "RV"
        cb_tx.tran_code = TRAN_CODE_MAP.get("RV", "020099")
        sg.cbs_extra_row = cb_tx

    # ── RRN reuse across cycles (scenario 10.5) ───────────────
    # second_group mutation: handled by caller (generates a second group with offset date)

    return sg


def _apply_mutations(tx: Transaction, mutation: dict, file_id: str):
    """Apply per-file mutations to a transaction copy"""
    if not mutation:
        return

    # Amount delta or override
    if "amount" in mutation:
        val = mutation["amount"]
        if isinstance(val, str) and val.startswith("+"):
            tx.amount += int(val[1:])
        elif isinstance(val, str) and val.startswith("-"):
            tx.amount = max(0, tx.amount + int(val))
        elif val == "0":
            tx.amount = 0
        else:
            tx.amount = int(val)

    # Response code override (decline scenarios)
    if "resp_code" in mutation:
        tx.resp_code = mutation["resp_code"]
        # If declined, zero out amount for CBS (nothing debited)
        if mutation["resp_code"] not in ("00", "000") and file_id == "cbs":
            if not mutation.get("keep_amount"):
                tx.amount = 0

    # NFS settlement date shift (T+1 settlement)
    if "settlement_date" in mutation:
        days = int(mutation["settlement_date"])
        tx.settlement_date = tx.tran_date + timedelta(days=days)

    # Transaction date shift (date boundary scenarios)
    if "tran_date" in mutation:
        days = int(mutation["tran_date"])
        tx.tran_date = tx.tran_date + timedelta(days=days)
        tx.settlement_date = tx.tran_date

    # Reversal flag
    if "type" in mutation and mutation["type"] in ("reversal", "chargeback"):
        tx.msg_type = "0420"
        tx.tran_code = TRAN_CODE_MAP.get("RV", "020099")
        tx.tran_type = "RV"
        tx.amount = abs(tx.amount)   # reversal amount is positive in our model

    # Wrong account — substitute a different account number
    if mutation.get("wrong_account"):
        digits = list(tx.account_no)
        # Flip a middle digit to make it clearly wrong
        digits[8] = str((int(digits[8]) + 3) % 10)
        tx.account_no = "".join(digits)

    # Transaction type override (BI, PC etc.)
    if "tran_type" in mutation:
        tx.tran_type = mutation["tran_type"]
        tx.tran_code = TRAN_CODE_MAP.get(mutation["tran_type"], tx.tran_code)

    # New RRN on reversal (NFS practice — scenario 7.4)
    if mutation.get("new_rrn_on_reversal") and tx.msg_type == "0420":
        tx.rrn = generate_rrn()
        tx.stan = generate_stan(tx.rrn)


def _build_reversal_group(sg: ScenarioGroup, base: Transaction,
                           tran_date: datetime, mutations: dict = None) -> ScenarioGroup:
    """
    Build reversal rows for the appropriate files.
    - Full reversal (7.1): original + reversal in all 3 files
    - NFS-only reversal (7.2): reversal only in NFS; switch/CBS keep original
    - CBS-only reversal (4.3): reversal only in CBS
    - Double CBS reversal (7.3): two reversal rows in CBS
    - Different RRN on NFS reversal (7.4): NFS reversal gets new RRN
    """
    if mutations is None:
        mutations = {}

    def make_reversal(tx: Transaction, new_rrn: bool = False) -> Transaction:
        rev = deepcopy(tx)
        rev.msg_type = "0420"
        rev.tran_type = "RV"
        rev.tran_code = TRAN_CODE_MAP["RV"]
        rev.amount = abs(tx.amount)
        if new_rrn:
            rev.rrn = generate_rrn()
            rev.stan = generate_stan(rev.rrn)
        return rev

    nfs_mut   = mutations.get("nfs", {})
    sw_mut    = mutations.get("switch_tlf", {})
    cbs_mut   = mutations.get("cbs", {})

    nfs_is_reversal    = nfs_mut.get("type") == "reversal"
    sw_is_reversal     = sw_mut.get("type") == "reversal"
    cbs_is_reversal    = cbs_mut.get("type") == "reversal"
    full_reversal      = not (nfs_is_reversal or sw_is_reversal or cbs_is_reversal)
    new_rrn_nfs        = nfs_mut.get("new_rrn_on_reversal", False)
    extra_cbs_reversal = cbs_mut.get("extra_reversal", False)

    if full_reversal or nfs_is_reversal:
        # Add reversal row to NFS
        if sg.nfs_row is not None:
            sg.nfs_extra_row = make_reversal(sg.nfs_row, new_rrn=new_rrn_nfs)
        elif full_reversal:
            # NFS wasn't in files_present but full reversal requested — add original + reversal
            orig = deepcopy(base)
            sg.nfs_row = orig
            sg.nfs_extra_row = make_reversal(orig, new_rrn=new_rrn_nfs)

    if full_reversal or sw_is_reversal:
        if sg.switch_row is not None:
            # For full reversal, switch gets a reversal entry (use extra slot via manifest)
            # We'll tag it on the switch_row itself for simplicity
            pass   # switch reversal noted in manifest; TLF entries are sequential

    if full_reversal or cbs_is_reversal:
        if sg.cbs_row is not None:
            sg.cbs_extra_row = make_reversal(sg.cbs_row)
        elif full_reversal:
            orig = deepcopy(base)
            sg.cbs_row = orig
            sg.cbs_extra_row = make_reversal(orig)

    # Double reversal in CBS (7.3): add a second reversal row
    if extra_cbs_reversal and sg.cbs_extra_row is not None:
        # We'll write two reversal rows; cbs_extra_row already has first,
        # mark the extra on the base tx so build_cbs_file can detect it
        sg.cbs_row.double_reversal = True  # type: ignore

    return sg


# ─────────────────────────────────────────────
# FILE SERIALIZERS
# ─────────────────────────────────────────────

def fmt_amount_cbs(amount_paise: int, width: int = 17) -> str:
    """Format amount for CBS: sign + 16 digits. Amount in paise."""
    sign = "-" if amount_paise < 0 else "-"  # CBS debits are negative
    val = abs(amount_paise)
    return f"{sign}{str(val).zfill(width - 1)}"

def fmt_amount_nfs(amount_paise: int, width: int = 15) -> str:
    """Format amount for NFS: zero-padded numeric"""
    return str(abs(amount_paise)).zfill(width)

def fmt_balance_cbs(balance_paise: int, width: int = 17) -> str:
    sign = "+" if balance_paise >= 0 else "-"
    return f"{sign}{str(abs(balance_paise)).zfill(width - 1)}"

def pad_right(s: str, width: int) -> str:
    return str(s)[:width].ljust(width)

def pad_left(s: str, width: int, char: str = " ") -> str:
    return str(s)[:width].rjust(width, char)

def pad_left_zero(s: str, width: int) -> str:
    return pad_left(s, width, "0")

def fmt_date_cbs(dt: datetime) -> str:
    return dt.strftime("%d%m%Y")

def fmt_date_nfs(dt: datetime) -> str:
    return dt.strftime("%y%m%d")

def fmt_time_nfs(dt: datetime) -> str:
    return dt.strftime("%H%M%S")


def serialize_cbs_row(tx: Transaction) -> str:
    """
    Serialize a transaction to CBS EX3198 format — exactly 158 chars.
    Positions are 1-indexed inclusive (SQL*Loader style):
      ACCT_NO(1:16) | sep(17) | JOURNAL_NO(18:26) | sep(27) | AMOUNT(28:44)
      | sep(45) | CARD_NUM(46:65) | TERMINAL_ID(66:81) | RRN_SEQ_NUM(82:93)
      | spaces(94:96) | TRAN_CODE(97:102) | sep(103) | TELLER_NO(104:119)
      | sep(120) | BRCH_NO(121:125) | sep(126) | GE_BRCH_NO(127:131)
      | sep(132) | TRAN_DATE(133:140) | sep(141) | BALANCE(142:158)
    """
    # ACCT_NO pos 1-16 (16 chars)
    acct_no = pad_right(tx.account_no[:16], 16)
    # sep pos 17
    # JOURNAL_NO pos 18-26 (9 chars, right-aligned)
    journal = pad_left(tx.journal_no[:9], 9)
    # sep pos 27
    # AMOUNT pos 28-44 (17 chars: sign + 16 digits)
    amount = fmt_amount_cbs(tx.amount, 17)
    # sep pos 45
    # CARD_NUM pos 46-65 (20 chars within NARRATIVE)
    card_num = pad_right(tx.card_pan[:20], 20)
    # TERMINAL_ID pos 66-81 (16 chars)
    term_id = pad_right(tx.terminal_id[:16], 16)
    # RRN_SEQ_NUM pos 82-93 (12 chars, space-padded right)
    # tx.rrn is the short ATM sequence number e.g. "3101" — right-pad with spaces to 12
    # This matches the real file pattern "3994        " and links to NFS field 5 & 10
    rrn = pad_right(tx.rrn, 12)
    # spaces pos 94-96 (3 spaces between RRN and TRAN_CODE)
    # TRAN_CODE pos 97-102 (6 chars)
    tran_code = pad_right(tx.tran_code[:6], 6)
    # sep pos 103
    # TELLER_NO pos 104-119 (16 chars)
    teller = pad_right(tx.teller_no[:16], 16)
    # sep pos 120
    # BRCH_NO pos 121-125 (5 chars)
    brch = pad_right(tx.branch_no[:5], 5)
    # sep pos 126
    # GE_BRCH_NO pos 127-131 (5 chars)
    ge_brch = pad_right(tx.ge_branch_no[:5], 5)
    # sep pos 132
    # TRAN_DATE pos 133-140 (8 chars DDMMYYYY)
    tran_date = fmt_date_cbs(tx.tran_date)
    # sep pos 141
    # BALANCE pos 142-158 (17 chars signed)
    balance = fmt_balance_cbs(tx.balance, 17)

    row = (acct_no + " " + journal + " " + amount + " " +
           card_num + term_id + rrn + "   " + tran_code + " " +
           teller + " " + brch + " " + ge_brch + " " +
           tran_date + " " + balance)

    assert len(row) == 158, f"CBS row length {len(row)} != 158\n[{row}]"
    return row


def serialize_nfs_row(tx: Transaction) -> str:
    """Serialize a transaction to NFS interchange format (407 chars)"""
    row = ""
    row += pad_right(tx.participant_id, 3)         # 1-3
    row += pad_right(tx.tran_type, 2)              # 4-5
    row += pad_right(tx.from_account_type, 2)      # 6-7
    row += pad_right(tx.to_account_type, 2)        # 8-9
    row += pad_left_zero(tx.rrn, 12)               # 10-21  Transaction Serial Number = RRN
    row += pad_right(tx.resp_code, 2)              # 22-23
    row += pad_right(tx.card_pan, 19)              # 24-42
    row += tx.member_number[:1]                    # 43
    row += pad_right(tx.approval_number, 6)        # 44-49
    row += pad_left_zero(tx.rrn, 12)               # 50-61  STAN = same RRN (links to Switch TLF)
    row += fmt_date_nfs(tx.tran_date)              # 62-67
    row += fmt_time_nfs(tx.tran_date)              # 68-73
    row += pad_left_zero(tx.mcc, 4)                # 74-77
    row += fmt_date_nfs(tx.settlement_date)        # 78-83
    row += pad_right(tx.card_acceptor_id, 15)      # 84-98
    row += pad_right(tx.terminal_id[:8], 8)        # 99-106
    row += pad_right(tx.terminal_location[:40], 40) # 107-146
    row += pad_right(tx.acquirer_id, 11)           # 147-157
    row += pad_right(tx.network_id, 3)             # 158-160
    row += pad_right(tx.account_no, 19)            # 161-179
    row += pad_right(tx.branch_no, 10)             # 180-189
    row += pad_right("", 19)                       # 190-208 account2
    row += pad_right("", 10)                       # 209-218 branch2
    row += pad_right(tx.currency_code, 3)          # 219-221
    row += fmt_amount_nfs(tx.amount, 15)           # 222-236
    row += fmt_amount_nfs(tx.amount, 15)           # 237-251 actual amount
    row += "0" * 15                                # 252-266 activity fee
    row += pad_right(tx.currency_code, 3)          # 267-269 issuer setl currency
    row += fmt_amount_nfs(tx.amount, 15)           # 270-284 issuer setl amount
    row += "0" * 15                                # 285-299 issuer setl fee
    row += "0" * 15                                # 300-314 issuer processing fee
    row += pad_right(tx.currency_code, 3)          # 315-317 cardholder billing currency
    row += fmt_amount_nfs(tx.amount, 15)           # 318-332 cardholder billing amount
    row += "0" * 15                                # 333-347 billing activity fee
    row += "0" * 15                                # 348-362 billing processing fee
    row += "0" * 15                                # 363-377 billing service fee
    row += "000001000000000"                       # 378-392 issuer conversion rate
    row += "000001000000000"                       # 393-407 cardholder conversion rate

    row = row[:407].ljust(407)
    return row


def serialize_nfs_acq_row(tx: Transaction) -> str:
    """Serialize a transaction to NFS Acquirer interchange format (274 chars).

    Field layout (1-indexed, MID-style positions):
      1-3    Participant ID
      4-5    Trxn Type: "04"=withdrawal, "05"=BI, "24"=reversal
      6-7    From Account Type: "02"=savings
      8-9    To Account Type
      10-21  Seq No (RRN, zero-padded)
      22-23  Resp Code
      24-42  PAN Number (19, space-padded right)
      43     Member Number
      44-49  Approval Number (spaces if declined)
      50-61  STAN (RRN, zero-padded)
      62-67  Trxn Date YYMMDD
      68-73  Transaction Time HHMMSS
      74-77  MCC
      78-83  Card Acceptor Settlement Date YYMMDD
      84-98  Card Acceptor ID (15 zeros)
      99-106 Terminal ID (8 chars)
      107-146 Card Acceptor Terminal Location (40 chars)
      147-157 Acquirer ID (11 chars)
      158-163 Acquirer Settlement Date YYMMDD
      164-166 Transaction Currency Code
      167-181 Transaction Amount (15, paise)
      182-196 Actual Transaction Amount (15, paise; 0 if declined/reversal)
      197-211 Transaction Activity Fee (15 zeros)
      212-214 Acquirer Settlement Currency Code
      215-229 Acquirer Settlement Amount (15, paise; divide by 100 for INR display)
      230-244 Acquirer Settlement Fee (15 zeros)
      245-259 Acquirer Settlement Processing Fee (15 zeros)
      260-274 Transaction/Acquirer Conversion Rate
    """
    is_reversal = tx.msg_type == "0420" or tx.tran_type in ("RV", "MR")
    is_approved = tx.resp_code in ("00", "000")

    acq_ttype = "24" if is_reversal else ACQ_TRAN_TYPE_MAP.get(tx.tran_type, "04")
    actual_amt = 0 if (not is_approved or is_reversal) else tx.amount
    approval = tx.approval_number if is_approved else "      "

    row = ""
    row += pad_right(tx.participant_id[:3], 3)        # 1-3   Participant ID
    row += pad_left_zero(acq_ttype, 2)                # 4-5   Trxn Type
    row += pad_left_zero(tx.from_account_type[:2], 2) # 6-7   From Account Type
    row += pad_right(tx.to_account_type[:2], 2)       # 8-9   To Account Type
    row += pad_left_zero(tx.rrn, 12)                  # 10-21 Seq No
    row += pad_right(tx.resp_code[:2], 2)             # 22-23 Resp Code
    row += pad_right(tx.card_pan[:19], 19)            # 24-42 PAN
    row += tx.member_number[:1]                       # 43    Member Number
    row += pad_right(approval[:6], 6)                 # 44-49 Approval Number
    row += pad_left_zero(tx.rrn, 12)                  # 50-61 STAN
    row += fmt_date_nfs(tx.tran_date)                 # 62-67 Trxn Date
    row += fmt_time_nfs(tx.tran_date)                 # 68-73 Time
    row += pad_left_zero(tx.mcc, 4)                   # 74-77 MCC
    row += fmt_date_nfs(tx.settlement_date)           # 78-83 Card Acceptor Settlement Date
    row += "0" * 15                                   # 84-98 Card Acceptor ID (zeros)
    row += pad_right(tx.terminal_id[:8], 8)           # 99-106 Terminal ID
    row += pad_right(tx.terminal_location[:40], 40)   # 107-146 Terminal Location
    row += pad_right(tx.acquirer_id[:11], 11)         # 147-157 Acquirer ID
    row += fmt_date_nfs(tx.settlement_date)           # 158-163 Acquirer Settlement Date
    row += tx.currency_code[:3]                       # 164-166 Transaction Currency
    row += fmt_amount_nfs(tx.amount, 15)              # 167-181 Transaction Amount
    row += fmt_amount_nfs(actual_amt, 15)             # 182-196 Actual Transaction Amount
    row += "0" * 15                                   # 197-211 Activity Fee
    row += tx.currency_code[:3]                       # 212-214 Acquirer Settlement Currency
    row += fmt_amount_nfs(actual_amt, 15)             # 215-229 Acquirer Settlement Amount
    row += "0" * 15                                   # 230-244 Acquirer Settlement Fee
    row += "0" * 15                                   # 245-259 Acquirer Settlement Proc Fee
    row += "000001000000000"                          # 260-274 Conversion Rate

    assert len(row) == 274, f"NFS ACQ row length {len(row)} != 274\n[{row}]"
    return row


def tandem_juliantimestamp(dt: datetime, subsec_us: int = 0) -> str:
    """
    Generate a Tandem JULIANTIMESTAMP as a 19-char zero-padded numeric string.
    Microseconds based on Julian Day calculation with Tandem-specific epoch offset.
    Verified against real BASE24 TLF files from the production system.
    Offset: -63000000000 microseconds (-17.5 hours) from pure Julian Day calc.
    """
    Y, M, D = dt.year, dt.month, dt.day
    Yc, Mc = Y, M
    if Mc <= 2:
        Yc -= 1
        Mc += 12
    A = Yc // 100
    B = 2 - A + A // 4
    JD = int(365.25 * (Yc + 4716)) + int(30.6001 * (Mc + 1)) + D + B - 1524
    day_us = JD * 86400 * 1_000_000
    time_us = (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1_000_000
    TANDEM_OFFSET = -63000000000  # microseconds
    total = day_us + time_us + TANDEM_OFFSET + subsec_us
    return str(max(0, total)).zfill(19)


# Transaction type code mapping — verified from real file
TRAN_CODE_FULL = {
    "W1": ("10", "11", "01"),   # Withdrawal: t_cde=10, savings->checking
    "BI": ("36", "11", "26"),   # Balance inquiry: "361126" (real file pattern)
    "MS": ("36", "11", "26"),   # Mini statement: same as BI
    "TF": ("40", "11", "11"),   # Transfer savings->savings
    "DP": ("20", "11", "26"),   # Deposit
    "RV": ("10", "11", "01"),   # Reversal uses withdrawal codes (msg_type=0420 distinguishes it)
    "PC": ("81", "00", "00"),   # PIN Change
}

# MultAcct values observed in real file:
# "4" = common for EG/balance type transactions
# "0" = primary account transaction
MULT_ACCT_MAP = {
    "BI": "4",
    "W1": "0",
    "TF": "0",
    "DP": "0",
    "RV": "0",
}


def serialize_switch_tlf_row(tx: Transaction) -> str:
    """
    Serialize a transaction to BASE24 Switch TLF format.
    headx (89 chars) + authx (485 chars) = 574 chars total.
    Validated field-by-field against production TLF records.
    """
    # ── Timestamps ──────────────────────────────────────────────
    # Random sub-second offset for realism (0-999999 microseconds)
    dt_subsec = random.randint(100000, 999999)
    entry_subsec = max(0, dt_subsec - random.randint(50000, 150000))

    datetime_ts  = tandem_juliantimestamp(tx.tran_date, dt_subsec)
    entry_ts     = tandem_juliantimestamp(tx.tran_date, entry_subsec)
    zero_ts      = "0" * 19   # ExitTime and ReEntryTime = zeros when not populated

    # ── headx (89 chars) ────────────────────────────────────────
    headx = (
        datetime_ts +                           #  0-18  DateTime (19)
        "01" +                                  # 19-20  RecordType: customer txn
        "P2AA" +                                # 21-24  AuthPPD
        "PRO2" +                                # 25-28  TerminalLN
        "C001" +                                # 29-32  TerminalFIID
        pad_right(tx.terminal_id[:16], 16) +    # 33-48  TerminalID (16)
        "PRO2" +                                # 49-52  CardLN (real file uses PRO2 not C001)
        "C001" +                                # 53-56  CardFIID
        pad_right(tx.card_pan[:19], 19) +       # 57-75  CardPAN (19)
        pad_left_zero("0", 3) +                 # 76-78  CardMemberNumber (3) -> "000" per real file
        pad_right("1111", 4) +                  # 79-82  BranchID (4) - card issuer branch, real="1111"
        pad_right("MH  ", 4) +                  # 83-86  RegionID (4)
        "  "                                    # 87-88  UserFLD1x (2)
    )
    assert len(headx) == 89, f"headx={len(headx)}"

    # ── authx field derivation ───────────────────────────────────
    tran_date_str   = tx.tran_date.strftime("%y%m%d")                   # YYMMDD
    tran_time_str   = tx.tran_date.strftime("%H%M%S") + "00"            # HHMMSSHH
    post_date_str   = tx.settlement_date.strftime("%y%m%d")
    acq_ichg_setl   = "000000"         # real file: zeros for NFS issuer
    iss_ichg_setl   = "000000"         # real file: zeros

    # SeqNum: the RRN right-padded with spaces to 12 chars (matches real "3100        ")
    # tx.rrn is already a short number e.g. "3101" — just right-pad to 12
    seq_num = pad_right(tx.rrn, 12)

    # TranCodeR: tcode(2) + from_account_type(2) + to_account_type(2)
    tc_map = TRAN_CODE_FULL.get(tx.tran_type, ("10", "11", "01"))
    tran_code_r = tc_map[0] + tc_map[1] + tc_map[2]

    mult_acct = MULT_ACCT_MAP.get(tx.tran_type, "0")

    # AcqInstID / RcvInstID — from real file: NFS routing numbers
    acq_inst_id = pad_left_zero("62201806244", 11)   # NFS acquirer routing
    rcv_inst_id = pad_left_zero("62201800000", 11)   # NFS issuer routing

    # Amounts: Amt1 = transaction amount; Amt2/Amt3 = 0 for standard withdrawal
    amt1 = pad_left_zero(str(abs(tx.amount)), 19)
    amt2 = "0" * 19
    amt3 = "0" * 19

    # RespCode: byte1 (card returned/retained) + byte2 (reason code)
    if tx.resp_code in ("00", "000"):
        resp_byte1 = "0"
        resp_byte2 = "00"
    else:
        resp_byte1 = "0"
        resp_byte2 = tx.resp_code[:2] if len(tx.resp_code) >= 2 else tx.resp_code.zfill(2)

    # Terminal location fields — pre-split as 25+22+13 block (60 chars total)
    # Format: TermNameLoc(25) + TermOwner(22) + TermCity(13)
    loc = pad_right(tx.terminal_location, 60)
    term_name_loc = pad_right(loc[0:25], 25)
    term_owner    = pad_right(loc[25:47], 22)
    term_city     = pad_right(loc[47:60], 13)

    # MultCrncy (41 chars): all zeros for single-currency domestic INR
    # auth_crncy_cde(3) + auth_conv_rate(8) + setl_crncy_cde(3) + setl_conv_rate(8) + conv_dat_tim(19)
    mult_crncy = (
        "000" +                                 # auth_crncy_cde  (not used)
        "00000000" +                            # auth_conv_rate  (not used)
        "000" +                                 # setl_crncy_cde  (not used)
        "00000000" +                            # setl_conv_rate  (not used)
        tandem_juliantimestamp(tx.tran_date, dt_subsec)  # conv_dat_tim (same as DateTime)
    )
    assert len(mult_crncy) == 41, f"mult_crncy={len(mult_crncy)}"

    # ReversalRsn: "01"=timeout for reversals, "00" for all others (real file uses "00" not spaces)
    rvsl_rsn = "01" if tx.msg_type == "0420" else "00"

    # PinOffset (16 chars): real file uses spaces + PIN data like "           20515"
    # The value after spaces is the PIN verification data — use realistic placeholder
    pin_ofst = "           " + "20515"    # 11 spaces + 5 char PIN data (matches real)

    # Refr (9 chars): ImpInd(1)+AvailImp(2)+LedgImp(2)+HldAmtImp(2)+CafRefrInd(1)+UserFLD3(1)
    # Real: "0000000  " = 7 zeros + 2 spaces (CafRefrInd and UserFLD3 are space)
    refr = "0000000  "

    # RefrInd (4 chars): real file = "    " (4 spaces, no refresh in progress)
    refr_ind = "    "

    # FrwdInstID: real file = "00000622018" (NFS forwarding institution)
    frwd_inst_id = "00000622018"

    # CrdAccptID and CrdIssID: zeros in real file for standard ATM NFS transactions
    crd_accpt_id = "00000000000"
    crd_iss_id   = "00000000000"

    # ── authx (485 chars) ────────────────────────────────────────
    authx = (
        "31" +                  #  89-90   TypeCode (no envelope)
        tx.msg_type +           #  91-94   MsgType (0210/0420)
        "00" +                  #  95-96   Status
        "1" +                   #  97      Originator: 1=Device (real file)
        "3" +                   #  98      Responder:  3=Authorization (real file)
        entry_ts +              #  99-117  EntryTime (19)
        zero_ts +               # 118-136  ExitTime (19)
        zero_ts +               # 137-155  ReEntryTime (19)
        tran_date_str +         # 156-161  TranDate YYMMDD
        tran_time_str +         # 162-169  TranTime HHMMSSHH
        post_date_str +         # 170-175  PostDate YYMMDD
        acq_ichg_setl +         # 176-181  AcqIchgSettlDate (000000)
        iss_ichg_setl +         # 182-187  IssIchgSettlDate (000000)
        seq_num +               # 188-199  SeqNum (12, space-padded)
        "22" +                  # 200-201  TerminalType (NCR ATM)
        "00000" +               # 202-206  TimeOffset (5 zeros, real file)
        acq_inst_id +           # 207-217  AcqInstIDNum (11)
        rcv_inst_id +           # 218-228  RcvInstIDNum (11)
        tran_code_r +           # 229-234  TranCodeR (6)
        pad_left_zero(tx.account_no[:19], 19) +  # 235-253  FromAcct (19)
        " " +                   # 254      UserFLD1
        "0" * 19 +              # 255-273  ToAcct (19 zeros)
        mult_acct +             # 274      MultAcct
        amt1 +                  # 275-293  Amt1 (19)
        amt2 +                  # 294-312  Amt2 (19)
        amt3 +                  # 313-331  Amt3 (19)
        "0" * 10 +              # 332-341  DepBalCr (10)
        "0" +                   # 342      DepType
        resp_byte1 +            # 343      RespCodeByte1
        resp_byte2 +            # 344-345  RespCodeByte2
        term_name_loc +         # 346-370  TerminalNameLoc (25)
        term_owner +            # 371-392  TerminalOwnerName (22)
        term_city +             # 393-405  TerminalCity (13)
        "MH " +                 # 406-408  TerminalState (3)
        "IN" +                  # 409-410  TerminalCountry (2)
        "0" * 28 +              # 411-438  Orig (28 zeros)
        tx.currency_code +      # 439-441  OrigCurrencyCode (3) e.g. "356"
        mult_crncy +            # 442-482  MultCrncy (41)
        rvsl_rsn +              # 483-484  ReversalReason (2)
        pin_ofst +              # 485-500  PinOffset (16)
        "0" +                   # 501      ShrgGrp
        "P" +                   # 502      DestOrder
        pad_right(tx.auth_id[:6], 6) +  # 503-508  AuthIDResp (6)
        refr +                  # 509-517  Refr (9)
        "0" +                   # 518      DepSetlImpFlag
        "0" +                   # 519      AdjSetlImpFlag
        refr_ind +              # 520-523  RefrInd (4 spaces)
        "0" * 16 +              # 524-539  UserFLD4 (16 zeros, real file)
        frwd_inst_id +          # 540-550  FrwdInstID (11)
        crd_accpt_id +          # 551-561  CrdAccptID (11 zeros)
        crd_iss_id +            # 562-572  CrdIssID (11 zeros)
        " "                     # 573      UserFLD6
    )
    assert len(authx) == 485, f"authx={len(authx)}"

    full_record = headx + authx
    assert len(full_record) == 574, f"TLF record={len(full_record)}"
    return full_record


# ─────────────────────────────────────────────
# FILE BUILDERS
# ─────────────────────────────────────────────

def build_cbs_file(groups: List[ScenarioGroup], tran_date: datetime) -> tuple:
    """Returns (file_content: str, row_manifest: list)"""
    rows = []
    manifest = []
    for sg in groups:
        for tx in sg.cbs_rows:
            row = serialize_cbs_row(tx)
            rows.append(row)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "cbs",
                "rrn": tx.rrn,
                "amount": tx.amount,
                "tran_date": tx.tran_date.strftime("%d%m%Y"),
                "row_number": len(rows),
            })
    content = "\n".join(rows)
    return content, manifest


def build_nfs_file(groups: List[ScenarioGroup], tran_date: datetime) -> tuple:
    rows = []
    manifest = []
    for sg in groups:
        for tx in sg.nfs_rows:
            row = serialize_nfs_row(tx)
            rows.append(row)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "nfs",
                "rrn": tx.rrn,
                "amount": tx.amount,
                "tran_date": tx.tran_date.strftime("%d%m%Y"),
                "row_number": len(rows),
            })
    content = "\n".join(rows)
    return content, manifest


def build_switch_tlf_file(groups: List[ScenarioGroup], tran_date: datetime) -> tuple:
    rows = []
    manifest = []

    # Header record
    file_seq = str(random.randint(1000, 9999))
    header = f"TH{tran_date.strftime('%y%m%d')}{file_seq}PRO2  TLF{'':40}{file_seq:>10}\n"

    for sg in groups:
        for tx in sg.switch_rows:
            row = serialize_switch_tlf_row(tx)
            rows.append("DR" + row)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "switch_tlf",
                "rrn": tx.rrn,
                "amount": tx.amount,
                "tran_date": tx.tran_date.strftime("%d%m%Y"),
                "row_number": len(rows),
            })

    content = header + "\n".join(rows)
    return content, manifest


# ─────────────────────────────────────────────
# MAIN GENERATION ENTRY POINT
# ─────────────────────────────────────────────

def generate(use_case_id: str, volume: int, tran_date: Optional[datetime] = None,
             output_dir: Optional[Path] = None) -> dict:
    """
    Main generation function.
    Returns dict with file paths, counts, and manifest.
    """
    if tran_date is None:
        tran_date = datetime.today()
    if output_dir is None:
        output_dir = BASE_DIR / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    use_case = load_use_case(use_case_id)

    all_groups: List[ScenarioGroup] = []
    group_counter = 0

    if use_case.get("mutation_matrix"):
        plan_ex = plan_scenarios_exhaustive(use_case, volume, network_file_key="nfs")
        for scenario, combo, count in plan_ex:
            file_states = scenario.get("file_states", {})
            for _ in range(count):
                group_counter += 1
                group_id = f"GRP{group_counter:06d}"
                tx_time = tran_date.replace(
                    hour=random.randint(8, 21),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )
                base = make_base_transaction(scenario["id"], group_id, tx_time)
                sg = build_scenario_group_from_states(
                    scenario["id"], scenario["name"], group_id, base,
                    file_states, combo["id"], network_file_key="nfs",
                )
                _apply_mutation_combo(sg, combo, network_file_key="nfs")
                all_groups.append(sg)
    else:
        plan = plan_scenarios(use_case, volume)
        for scenario, count in plan:
            for _ in range(count):
                group_counter += 1
                group_id = f"GRP{group_counter:06d}"
                tx_time = tran_date.replace(
                    hour=random.randint(8, 21),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )
                sg = build_scenario_group(scenario, group_id, tx_time)
                all_groups.append(sg)

    # Shuffle groups so scenarios are interspersed realistically
    random.shuffle(all_groups)

    # Serialize files
    date_str = tran_date.strftime("%d%m%Y")
    run_id = tran_date.strftime("%Y%m%d%H%M%S")

    cbs_content, cbs_manifest = build_cbs_file(all_groups, tran_date)
    nfs_content, nfs_manifest = build_nfs_file(all_groups, tran_date)
    switch_content, switch_manifest = build_switch_tlf_file(all_groups, tran_date)

    # Write files
    cbs_path = output_dir / f"EX3198_{date_str}.prt1"
    nfs_path = output_dir / f"NFS_INTERCHANGE_{date_str}.txt"
    switch_path = output_dir / f"t{tran_date.strftime('%y%m%d')}001-_SWITCH_TLF"
    manifest_path = output_dir / f"manifest_{run_id}.json"

    cbs_path.write_text(cbs_content, encoding="ascii", errors="replace")
    nfs_path.write_text(nfs_content, encoding="ascii", errors="replace")
    switch_path.write_text(switch_content, encoding="ascii", errors="replace")

    # Build manifest
    all_manifest = cbs_manifest + nfs_manifest + switch_manifest
    # Add summary by scenario
    scenario_summary = {}
    for item in all_manifest:
        sid = item["scenario_id"]
        sname = item["scenario_name"]
        if sid not in scenario_summary:
            scenario_summary[sid] = {"name": sname, "total_rows": 0, "files": {}}
        scenario_summary[sid]["total_rows"] += 1
        f = item["file"]
        scenario_summary[sid]["files"][f] = scenario_summary[sid]["files"].get(f, 0) + 1

    manifest_data = {
        "run_id": run_id,
        "use_case_id": use_case_id,
        "use_case_name": use_case["name"],
        "tran_date": date_str,
        "total_groups": len(all_groups),
        "total_volume": volume,
        "files": {
            "cbs": str(cbs_path.name),
            "nfs": str(nfs_path.name),
            "switch_tlf": str(switch_path.name),
        },
        "scenario_summary": scenario_summary,
        "rows": all_manifest,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # Create zip bundle
    zip_path = output_dir / f"recon_testdata_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(cbs_path, cbs_path.name)
        zf.write(nfs_path, nfs_path.name)
        zf.write(switch_path, switch_path.name)
        zf.write(manifest_path, manifest_path.name)

    return {
        "run_id": run_id,
        "zip_path": str(zip_path),
        "files": {
            "cbs": {"path": str(cbs_path), "rows": len(cbs_manifest)},
            "nfs": {"path": str(nfs_path), "rows": len(nfs_manifest)},
            "switch_tlf": {"path": str(switch_path), "rows": len(switch_manifest)},
        },
        "scenario_summary": scenario_summary,
        "manifest_path": str(manifest_path),
    }


if __name__ == "__main__":
    result = generate("nfs_atm_issuer", volume=50)
    print(json.dumps(result, indent=2))
