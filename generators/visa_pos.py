"""
VISA POS + Cash — Bank as Issuer
Core generation engine for VISA T&E Clearing use case.

Produces:
  - VISA T&E Clearing File  (4 lines per transaction: TCSN 0/1/5/7)
  - Switch TLF              (BASE24 format — same serializer as NFS flow)
  - CBS Export              (EX3198 format — same serializer as NFS flow)
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
from typing import List, Optional
from copy import deepcopy

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.nfs_atm import (
    Transaction,
    ScenarioGroup,
    serialize_switch_tlf_row,
    serialize_cbs_row,
    generate_pan,
    generate_account_no,
    generate_terminal_id,
    generate_approval_code,
    generate_auth_id,
    generate_journal_no,
    random_amount,
    random_balance,
    generate_rrn,
    generate_stan,
    TRAN_CODE_MAP,
    pad_right,
    pad_left,
    pad_left_zero,
    fmt_date_cbs,
    BASE_DIR,
    build_scenario_group_from_states,
    get_mutation_combos,
    _apply_mutation_combo,
    plan_scenarios_exhaustive,
    _build_rows_from_state,
)

# ─────────────────────────────────────────────
# VISA-SPECIFIC CONSTANTS
# ─────────────────────────────────────────────

# POS merchants: (name 25-char, city 13-char, MCC 4-char)
POS_MERCHANTS = [
    ("RELIANCE RETAIL          ", "MUMBAI       ", "5999"),
    ("BIGBAZAAR                ", "PUNE         ", "5411"),
    ("SWIGGY                   ", "BANGALORE    ", "5812"),
    ("ZOMATO                   ", "HYDERABAD    ", "5812"),
    ("AMAZON IN                ", "NEW DELHI    ", "5999"),
    ("FLIPKART                 ", "BANGALORE    ", "5999"),
    ("DOMINOS PIZZA            ", "MUMBAI       ", "5812"),
    ("MCDONALDS                ", "CHENNAI      ", "5812"),
    ("DMART                    ", "AHMEDABAD    ", "5411"),
    ("MORE SUPERMARKET         ", "KOLKATA      ", "5411"),
    ("BOOKMYSHOW               ", "MUMBAI       ", "7832"),
    ("IRCTC                    ", "NEW DELHI    ", "4112"),
    ("OYO ROOMS                ", "GURUGRAM     ", "7011"),
    ("MAKEMYTRIP               ", "NEW DELHI    ", "4722"),
    ("NYKAA                    ", "MUMBAI       ", "5977"),
    ("CROMA                    ", "PUNE         ", "5734"),
    ("WESTSIDE                 ", "BANGALORE    ", "5651"),
    ("LIFESTYLE                ", "HYDERABAD    ", "5651"),
    ("SPENCERS                 ", "KOLKATA      ", "5411"),
    ("LENSKART                 ", "NEW DELHI    ", "8049"),
]

# ATM / Cash merchants
CASH_MERCHANTS = [
    ("ATM WITHDRAWAL           ", "MUMBAI       ", "6011"),
    ("HDFC BANK ATM            ", "NEW DELHI    ", "6011"),
    ("SBI ATM                  ", "BANGALORE    ", "6011"),
    ("ICICI BANK ATM           ", "PUNE         ", "6011"),
    ("AXIS BANK ATM            ", "HYDERABAD    ", "6011"),
    ("KOTAK MAHINDRA ATM       ", "CHENNAI      ", "6011"),
    ("YES BANK ATM             ", "KOLKATA      ", "6011"),
    ("VISA CASH ADVANCE        ", "AHMEDABAD    ", "6010"),
]

ACQUIRER_ID = "00000000"   # 8-char acquirer business ID (VISA)


# ─────────────────────────────────────────────
# ARN / AUTH CODE GENERATORS
# ─────────────────────────────────────────────

_arn_counter = random.randint(100000000000, 499999999999)

def generate_arn(tran_date: datetime) -> str:
    """
    Generate a 23-char Acquirer Reference Number.
    Format: "74" + MMDD + "6" + YDDD_suffix + seq_10digit
    Total = 2+4+1+6+10 = 23 chars.
    """
    global _arn_counter
    _arn_counter += 1
    mmdd = tran_date.strftime("%m%d")
    yddd = str(tran_date.year % 10) + str(tran_date.timetuple().tm_yday).zfill(3)
    seq = str(_arn_counter)[-10:].zfill(10)
    arn = "74" + mmdd + yddd + seq + str(random.randint(10, 99))
    return arn[:23]


def generate_auth_code() -> str:
    """Generate a 6-char uppercase alphanumeric authorization code."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=6))


# ─────────────────────────────────────────────
# TRANSACTION FACTORY
# ─────────────────────────────────────────────

def make_base_visa_transaction(
    scenario_id: str,
    group_id: str,
    tran_date: datetime,
    tran_type: str = "POS",
) -> Transaction:
    """
    Build a base Transaction for a VISA POS or Cash scenario.
    tran_type: "POS" (TC=05) or "ATM" (TC=07)
    The ARN is stored in tx.rrn (used as the cross-file linking key).
    """
    branch = "04234"
    pan = generate_pan(prefix="458012")   # VISA BIN prefix
    amount = random_amount()
    arn = generate_arn(tran_date)

    # Choose merchant based on type
    if tran_type == "ATM":
        merch_name, merch_city, mcc = random.choice(CASH_MERCHANTS)
        msg_type = "07"
        tran_code = TRAN_CODE_MAP.get("W1", "020010")
        tran_type_code = "W1"
    else:
        merch_name, merch_city, mcc = random.choice(POS_MERCHANTS)
        msg_type = "05"
        tran_code = TRAN_CODE_MAP.get("W1", "020010")
        tran_type_code = "W1"

    tx = Transaction(
        rrn=arn,                          # ARN stored in rrn field
        stan=generate_stan(str(random.randint(1000, 9999))),
        card_pan=pan,
        account_no=generate_account_no(branch),
        terminal_id=generate_terminal_id(branch),
        terminal_location=f"{merch_name}{merch_city}IN ",
        acquirer_id=ACQUIRER_ID,
        card_acceptor_id="VISA" + branch + "001",
        branch_no=branch,
        ge_branch_no=branch,
        tran_date=tran_date,
        settlement_date=tran_date,
        amount=amount,
        tran_code=tran_code,
        tran_type=tran_type_code,
        resp_code="00",
        approval_number=generate_auth_code(),   # VISA uses alpha auth code
        auth_id=generate_auth_id(),
        balance=random_balance(amount),
        journal_no=generate_journal_no(),
        teller_no="0000000009900001",
        msg_type=msg_type,                # "05"=POS fwd, "07"=Cash fwd
        scenario_id=scenario_id,
        scenario_group=group_id,
        mcc=mcc,
        currency_code="356",
        network_id="VISA",
    )
    return tx


# ─────────────────────────────────────────────
# VISA TC FILE SERIALIZERS
# ─────────────────────────────────────────────

def _yddd(dt: datetime) -> str:
    """Central Processing Date in YDDD format: last digit of year + 3-digit day-of-year."""
    return str(dt.year % 10) + str(dt.timetuple().tm_yday).zfill(3)


def serialize_visa_0500_row(tx: Transaction) -> str:
    """
    Serialize the TCSN=0 main financial record (168 chars).

    TC mapping:
      tx.msg_type == "05"  → TC "05" (POS forward)
      tx.msg_type == "07"  → TC "07" (cash forward)
      tx.msg_type == "25"  → TC "25" (POS reversal)
      tx.msg_type == "27"  → TC "27" (cash reversal)
    Declined (resp_code != "00"): TC stays 05/07 but Usage Code = "9"
    """
    tc = tx.msg_type if tx.msg_type in ("05", "07", "25", "27") else "05"
    usage_code = "9" if tx.resp_code not in ("00", "000") else "1"

    # ARN stored in tx.rrn
    arn = pad_right(str(tx.rrn), 23)[:23]

    # Account number: 16 digits, left-padded
    pan = str(tx.card_pan)[:16].ljust(16, "0")

    # Amounts: 12-char zero-padded in minor units (paise)
    dest_amt = str(abs(tx.amount)).zfill(12)
    src_amt  = str(abs(tx.amount)).zfill(12)

    # Merchant info: pre-padded in the tuples already (25 + 13)
    merch_name = pad_right(tx.terminal_location[:25], 25)
    merch_city = pad_right(tx.terminal_location[25:38], 13)

    # Authorization code: 6 chars from approval_number
    auth_code = pad_right(str(tx.approval_number)[:6], 6)

    # Purchase date MMDD
    mmdd = tx.tran_date.strftime("%m%d")

    row = (
        tc                           +   # pos  1- 2   TC (2)
        "0"                          +   # pos  3       TCQ (1)
        "0"                          +   # pos  4       TCSN (1)
        pan                          +   # pos  5-20   Account Number (16)
        "000"                        +   # pos 21-23   Account Number Extension (3)
        "Z"                          +   # pos 24       Floor Limit Indicator (1)
        " "                          +   # pos 25       CRB Indicator (1)
        " "                          +   # pos 26       Reserved (1)
        arn                          +   # pos 27-49   ARN (23)
        ACQUIRER_ID                  +   # pos 50-57   Acquirer Business ID (8)
        mmdd                         +   # pos 58-61   Purchase Date MMDD (4)
        dest_amt                     +   # pos 62-73   Destination Amount (12)
        "356"                        +   # pos 74-76   Destination Currency (3)
        src_amt                      +   # pos 77-88   Source Amount (12)
        "356"                        +   # pos 89-91   Source Currency (3)
        merch_name                   +   # pos 92-116  Merchant Name (25)
        merch_city                   +   # pos 117-129 Merchant City (13)
        "IN "                        +   # pos 130-132 Merchant Country (3)
        str(tx.mcc).zfill(4)         +   # pos 133-136 MCC (4)
        "00000"                      +   # pos 137-141 Merchant ZIP (5)
        "   "                        +   # pos 142-144 Merchant State (3)
        "1"                          +   # pos 145     Requested Payment Service (1)
        "0"                          +   # pos 146     Number of Payment Forms (1)
        usage_code                   +   # pos 147     Usage Code (1)
        "00"                         +   # pos 148-149 Reason Code (2)
        "N"                          +   # pos 150     Settlement Flag (1)
        "0"                          +   # pos 151     Auth Characteristics Indicator (1)
        auth_code                    +   # pos 152-157 Authorization Code (6)
        "4"                          +   # pos 158     POS Terminal Capability (1)
        " "                          +   # pos 159     Reserved (1)
        "1"                          +   # pos 160     Cardholder ID Method (1)
        " "                          +   # pos 161     Collection-Only Flag (1)
        "07"                         +   # pos 162-163 POS Entry Mode (2)
        _yddd(tx.tran_date)          +   # pos 164-167 Central Processing Date YDDD (4)
        "0"                              # pos 168     Reimbursement Attribute (1)
    )

    assert len(row) == 168, f"TCSN=0 row length {len(row)} != 168\n[{row}]"
    return row


def serialize_visa_0501_row(tx: Transaction) -> str:
    """
    Serialize the TCSN=1 authorization record (168 chars).
    Template-based; embeds TC+TCQ+TCSN at start and key auth data.
    """
    tc = tx.msg_type if tx.msg_type in ("05", "07", "25", "27") else "05"
    auth_code = pad_right(str(tx.approval_number)[:6], 6)
    pan_fragment = str(tx.card_pan)[6:10]   # 4-char fragment from middle of PAN
    stan_6 = str(tx.stan)[-6:].zfill(6)
    yddd = _yddd(tx.tran_date)

    # Build a realistic 168-char auth record based on the sample pattern
    # 0501   A1249    000000  ...  (168 chars)
    part1 = tc + "01"                               # pos 1-4:  TC+TCQ+TCSN
    part2 = "   "                                   # pos 5-7:  spaces
    part3 = pan_fragment[:1] + "1"                  # pos 8-9:  partial PAN ref
    part4 = str(random.randint(100,999))            # pos 10-12: 3-digit ref
    part5 = "    "                                  # pos 13-16: spaces
    part6 = "000000"                                # pos 17-22: zeros
    part7 = " " * 37                                # pos 23-59: spaces
    part8 = "   "                                   # pos 60-62
    part9 = pan_fragment                            # pos 63-66: 4-char PAN fragment
    part10 = str(random.randint(10,99))             # pos 67-68: 2-char
    part11 = stan_6                                 # pos 69-74: STAN 6-char
    part12 = pan_fragment                           # pos 75-78
    part13 = "     J"                               # pos 79-84
    part14 = stan_6                                 # pos 85-90: STAN repeat
    part15 = " 000000000000  "                      # pos 91-105
    part16 = yddd                                   # pos 106-109: YDDD
    part17 = " 0    5 0"                            # pos 110-118
    part18 = " " * 27                               # pos 119-145
    part19 = "000000000  "                          # pos 146-156 (11 chars)

    row = part1 + part2 + part3 + part4 + part5 + part6 + part7 + part8 + part9 + part10 + part11 + part12 + part13 + part14 + part15 + part16 + part17 + part18 + part19
    # Ensure exactly 168 chars
    row = row[:168].ljust(168)
    return row


def serialize_visa_0505_row(tx: Transaction) -> str:
    """
    Serialize the TCSN=5 settlement record (168 chars).
    Template-based; contains BIN routing and settlement amount.
    """
    tc = tx.msg_type if tx.msg_type in ("05", "07", "25", "27") else "05"
    bin6 = str(tx.card_pan)[:6]
    amount_12 = str(abs(tx.amount)).zfill(12)
    mmdd = tx.tran_date.strftime("%m%d")
    yddd = _yddd(tx.tran_date)

    # Based on sample: 050539604740453301800000006970041700 ...
    part1 = tc + "05"                               # pos 1-4: TC+TCQ+TCSN
    part2 = bin6                                    # pos 5-10: BIN
    part3 = str(random.randint(100000, 999999))     # pos 11-16: 6-char routing ref
    part4 = str(random.randint(10, 99))             # pos 17-18
    part5 = "00"                                    # pos 19-20
    part6 = "000000"                                # pos 21-26: zeros
    part7 = amount_12[:6]                           # pos 27-32: partial amount
    part8 = mmdd                                    # pos 33-36: MMDD
    part9 = "00"                                    # pos 37-38
    part10 = " " * 8                                # pos 39-46: spaces
    part11 = "0000 "                                # pos 47-51
    part12 = "000000000000"                         # pos 52-63: zeros
    part13 = " " * 14                               # pos 64-77: spaces
    part14 = "000000"                               # pos 78-83
    part15 = amount_12                              # pos 84-95
    part16 = str(random.randint(100000, 999999))    # pos 96-101: seq ref
    part17 = "C"                                    # pos 102: currency ind
    part18 = mmdd[:2]                               # pos 103-104
    part19 = yddd                                   # pos 105-108: YDDD
    part20 = str(random.randint(100, 999))          # pos 109-111
    part21 = mmdd[2:]                               # pos 112-113
    part22 = "0169000000000000"                     # pos 114-129
    part23 = "I"                                    # pos 130: indicator
    part24 = " " * 13                               # pos 131-143: spaces
    part25 = "0000000000000000"                     # pos 144-159: zeros
    part26 = " C "                                  # pos 160-162

    row = part1 + part2 + part3 + part4 + part5 + part6 + part7 + part8 + part9 + part10 + part11 + part12 + part13 + part14 + part15 + part16 + part17 + part18 + part19 + part20 + part21 + part22 + part23 + part24 + part25 + part26
    row = row[:168].ljust(168)
    return row


def serialize_visa_0507_row(tx: Transaction) -> str:
    """
    Serialize the TCSN=7 EMV/cryptogram record (168 chars).
    Template-based; contains chip authentication data.
    """
    tc = tx.msg_type if tx.msg_type in ("05", "07", "25", "27") else "05"
    amount_12 = str(abs(tx.amount)).zfill(12)

    # EMV cryptogram: random hex bytes mimicking ARQC
    cryptogram = ''.join(random.choices('0123456789ABCDEF', k=16))
    atc = str(random.randint(1, 9999)).zfill(4)    # Application Transaction Counter
    iad = ''.join(random.choices('0123456789ABCDEF', k=16))  # Issuer Application Data

    # Based on sample: 0507000012...
    part1 = tc + "07"                               # pos 1-4: TC+TCQ+TCSN
    part2 = "00001"                                 # pos 5-9
    part3 = str(random.randint(100, 999))           # pos 10-12
    part4 = str(random.randint(10000, 99999))[:5]   # pos 13-17
    part5 = "E"                                     # pos 18
    part6 = cryptogram[:8]                          # pos 19-26: partial cryptogram
    part7 = " " * 8                                 # pos 27-34: spaces
    part8 = "C"                                     # pos 35
    part9 = iad[:8]                                 # pos 36-43: partial IAD
    part10= str(random.randint(1000, 9999))         # pos 44-47
    part11= str(random.randint(1000, 9999))         # pos 48-51
    part12= str(random.randint(1000, 9999))[:3]     # pos 52-54
    part13= "0"                                     # pos 55
    part14= "112"                                   # pos 56-58
    part15= "000000000000"                          # pos 59-70: zeros
    part16= atc                                     # pos 71-74: ATC
    part17= "02"                                    # pos 75-76
    part18= "000000"                                # pos 77-82: zeros
    part19= amount_12[:6]                           # pos 83-88
    part20= "000000000000"                          # pos 89-100
    part21= amount_12[6:]                           # pos 101-106
    part22= "0" * 20                                # pos 107-126: zeros
    part23= str(random.randint(100, 999))           # pos 127-129
    part24= "0" * 12                                # pos 130-141: zeros
    part25= "0" * 10                                # pos 142-151: zeros
    part26= "         "                             # pos 152-160: spaces (9)
    part27= "       "                               # pos 161-167: spaces (7)
    part28= " "                                     # pos 168: space

    row = part1 + part2 + part3 + part4 + part5 + part6 + part7 + part8 + part9 + part10 + part11 + part12 + part13 + part14 + part15 + part16 + part17 + part18 + part19 + part20 + part21 + part22 + part23 + part24 + part25 + part26 + part27 + part28
    row = row[:168].ljust(168)
    return row


def serialize_visa_record_group(tx: Transaction) -> List[str]:
    """
    Serialize one transaction into 4 consecutive 168-char VISA records.
    Returns a list of 4 strings.
    """
    return [
        serialize_visa_0500_row(tx),
        serialize_visa_0501_row(tx),
        serialize_visa_0505_row(tx),
        serialize_visa_0507_row(tx),
    ]


# ─────────────────────────────────────────────
# MUTATION HELPERS
# ─────────────────────────────────────────────

def _apply_visa_mutations(tx: Transaction, mutation: dict, file_id: str):
    """Apply per-file mutations to a VISA transaction copy."""
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

    # Response code override
    if "resp_code" in mutation:
        tx.resp_code = mutation["resp_code"]
        if mutation["resp_code"] not in ("00", "000") and file_id == "cbs":
            if not mutation.get("keep_amount"):
                tx.amount = 0

    # Date shifts
    if "tran_date" in mutation:
        days = int(mutation["tran_date"])
        tx.tran_date = tx.tran_date + timedelta(days=days)
        tx.settlement_date = tx.tran_date

    if "settlement_date" in mutation:
        days = int(mutation["settlement_date"])
        tx.settlement_date = tx.tran_date + timedelta(days=days)

    # Reversal flag
    if "type" in mutation and mutation["type"] == "reversal":
        # Flip TC to reversal equivalent
        if tx.msg_type == "05":
            tx.msg_type = "25"
        elif tx.msg_type == "07":
            tx.msg_type = "27"
        else:
            tx.msg_type = "25"
        tx.tran_type = "RV"
        tx.tran_code = TRAN_CODE_MAP.get("RV", "020099")
        tx.amount = abs(tx.amount)

    # Transaction type override (ATM variant)
    if "tran_type" in mutation and mutation["tran_type"] == "ATM":
        tx.msg_type = "07" if tx.msg_type not in ("25", "27") else "27"
        tx.mcc = "6011"
        merch_name, merch_city, _ = random.choice(CASH_MERCHANTS)
        tx.terminal_location = f"{merch_name}{merch_city}IN "

    # Wrong account
    if mutation.get("wrong_account"):
        digits = list(tx.account_no)
        digits[8] = str((int(digits[8]) + 3) % 10)
        tx.account_no = "".join(digits)


# ─────────────────────────────────────────────
# SCENARIO GROUP BUILDER
# ─────────────────────────────────────────────

def build_visa_scenario_group(
    scenario: dict,
    group_id: str,
    tran_date: datetime,
) -> ScenarioGroup:
    """Build a ScenarioGroup for one VISA scenario instance."""
    tran_type = "POS"
    # Check if any mutation sets ATM type
    mutations = scenario.get("mutations", {})
    all_mut = mutations.get("all", {})
    if all_mut.get("tran_type") == "ATM":
        tran_type = "ATM"

    base = make_base_visa_transaction(scenario["id"], group_id, tran_date, tran_type)
    sg = ScenarioGroup(
        scenario_id=scenario["id"],
        scenario_name=scenario["name"],
        group_id=group_id,
        base_tx=base,
    )

    files_present = scenario.get("files_present", ["visa_tc", "switch_tlf", "cbs"])
    is_reversal = mutations.get("type") == "reversal"

    # Track per-file counts for duplicates
    visa_count = 0
    cbs_count = 0

    for file_id in files_present:
        # visa_tc maps to "nfs_row" slot (VISA file plays NFS role)
        if file_id in ("visa_tc", "nfs"):
            tx = deepcopy(base)
            _apply_visa_mutations(tx, {**all_mut, **mutations.get("nfs", {})}, "visa_tc")
            if is_reversal:
                _make_visa_reversal_tx(tx)
            visa_count += 1
            if visa_count == 1:
                sg.nfs_row = tx
            else:
                sg.nfs_extra_row = tx

        elif file_id == "switch_tlf":
            tx = deepcopy(base)
            _apply_visa_mutations(tx, {**all_mut, **mutations.get("switch_tlf", {})}, "switch_tlf")
            if is_reversal:
                _make_visa_reversal_tx(tx)
            sg.switch_row = tx

        elif file_id in ("cbs",):
            tx = deepcopy(base)
            _apply_visa_mutations(tx, {**all_mut, **mutations.get("cbs", {})}, "cbs")
            if is_reversal:
                _make_visa_reversal_tx(tx)
            cbs_count += 1
            if cbs_count == 1:
                sg.cbs_row = tx
            else:
                sg.cbs_extra_row = tx

    return sg


def _make_visa_reversal_tx(tx: Transaction):
    """Convert a forward VISA transaction to its reversal equivalent in-place."""
    if tx.msg_type == "05":
        tx.msg_type = "25"
    elif tx.msg_type == "07":
        tx.msg_type = "27"
    tx.tran_type = "RV"
    tx.tran_code = TRAN_CODE_MAP.get("RV", "020099")
    tx.amount = abs(tx.amount)


def build_visa_scenario_group_from_states(
    scenario_id: str,
    scenario_name: str,
    group_id: str,
    base: Transaction,
    file_states: dict,
    mutation_id: str = "baseline",
    tran_type: str = "POS",
) -> ScenarioGroup:
    """Build a ScenarioGroup for a VISA scenario using 64-case file_states format.
    VISA TC forward: 05 (POS) / 07 (Cash). Reversal: 25 / 27.
    """
    sg = build_scenario_group_from_states(
        scenario_id, scenario_name, group_id, base,
        file_states, mutation_id, network_file_key="visa_tc",
    )
    fwd_tc = "07" if tran_type == "ATM" else "05"
    rev_tc = "27" if tran_type == "ATM" else "25"
    for rows_list in (sg.nfs_rows, sg.switch_rows, sg.cbs_rows):
        for tx in rows_list:
            if tx.msg_type == "0210":
                tx.msg_type = fwd_tc
            elif tx.msg_type == "0420":
                tx.msg_type = rev_tc
    return sg


# ─────────────────────────────────────────────
# FILE BUILDERS
# ─────────────────────────────────────────────

def build_visa_tc_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
) -> tuple:
    """Build the VISA T&E Clearing File. Returns (content, manifest)."""
    lines = []
    manifest = []
    row_group_num = 0

    for sg in groups:
        for tx in sg.nfs_rows:
            row_group_num += 1
            rec_group = serialize_visa_record_group(tx)
            start_line = len(lines) + 1
            lines.extend(rec_group)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "visa_tc",
                "rrn": tx.rrn,
                "tc": tx.msg_type,
                "amount": tx.amount,
                "tran_date": tx.tran_date.strftime("%d%m%Y"),
                "row_group": row_group_num,
                "line_start": start_line,
                "lines": 4,
            })

    content = "\n".join(lines)
    return content, manifest


def _to_tlf_msg_type(visa_msg_type: str) -> str:
    """Translate VISA TC codes to BASE24 msg_type codes for the Switch TLF serializer."""
    _MAP = {
        "05": "0210",   # POS forward → authorization response
        "07": "0210",   # Cash forward → authorization response
        "25": "0420",   # POS reversal → reversal
        "27": "0420",   # Cash reversal → reversal
        # Already in BASE24 format — pass through
        "0210": "0210",
        "0420": "0420",
    }
    return _MAP.get(visa_msg_type, "0210")


def build_visa_switch_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
) -> tuple:
    """Build Switch TLF file (same format as NFS flow)."""
    rows = []
    manifest = []
    file_seq = str(random.randint(1000, 9999))
    header = f"TH{tran_date.strftime('%y%m%d')}{file_seq}PRO2  TLF{'':40}{file_seq:>10}\n"

    for sg in groups:
        for orig_tx in sg.switch_rows:
            tx = deepcopy(orig_tx)
            tx.msg_type = _to_tlf_msg_type(tx.msg_type)
            row = serialize_switch_tlf_row(tx)
            rows.append("DR" + row)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "switch_tlf",
                "rrn": orig_tx.rrn,
                "amount": orig_tx.amount,
                "tran_date": orig_tx.tran_date.strftime("%d%m%Y"),
                "row_number": len(rows),
            })

    content = header + "\n".join(rows)
    return content, manifest


def build_visa_cbs_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
) -> tuple:
    """Build CBS EX3198 file (same format as NFS flow)."""
    rows = []
    manifest = []

    for sg in groups:
        for orig_tx in sg.cbs_rows:
            tx = deepcopy(orig_tx)
            tx.msg_type = _to_tlf_msg_type(tx.msg_type)
            row = serialize_cbs_row(tx)
            rows.append(row)
            manifest.append({
                "group_id": sg.group_id,
                "scenario_id": sg.scenario_id,
                "scenario_name": sg.scenario_name,
                "mutation_id": sg.mutation_id,
                "file": "cbs",
                "rrn": orig_tx.rrn,
                "amount": orig_tx.amount,
                "tran_date": orig_tx.tran_date.strftime("%d%m%Y"),
                "row_number": len(rows),
            })

    content = "\n".join(rows)
    return content, manifest


# ─────────────────────────────────────────────
# SCENARIO PLANNER
# ─────────────────────────────────────────────

def load_use_case(use_case_id: str) -> dict:
    path = BASE_DIR / "use_cases" / f"{use_case_id}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def plan_scenarios(use_case: dict, volume: int) -> list:
    """Allocate transactions to scenarios with weight-based distribution."""
    scenarios = use_case["scenarios"]
    n = len(scenarios)
    if volume < n:
        volume = n

    remaining = volume - n
    weights = [s.get("weight", 5) for s in scenarios]
    total_weight = sum(weights)

    allocations = [1] * n
    for i, w in enumerate(weights):
        extra = round((w / total_weight) * remaining)
        allocations[i] += extra

    diff = volume - sum(allocations)
    if diff > 0:
        allocations[0] += diff
    elif diff < 0:
        allocations[0] = max(1, allocations[0] + diff)

    return [(scenarios[i], allocations[i]) for i in range(n)]


# ─────────────────────────────────────────────
# MAIN GENERATION ENTRY POINT
# ─────────────────────────────────────────────

def generate(
    use_case_id: str,
    volume: int,
    tran_date: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """
    Generate VISA POS+Cash test data files.

    Returns dict with file paths, counts, and manifest — same
    structure as nfs_atm.generate() so app.py can handle it uniformly.
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
        plan_ex = plan_scenarios_exhaustive(use_case, volume, network_file_key="visa_tc")
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
                base = make_base_visa_transaction(scenario["id"], group_id, tx_time)
                sg = build_visa_scenario_group_from_states(
                    scenario["id"], scenario["name"], group_id, base,
                    file_states, combo["id"],
                )
                _apply_mutation_combo(sg, combo, network_file_key="visa_tc")
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
                sg = build_visa_scenario_group(scenario, group_id, tx_time)
                all_groups.append(sg)

    random.shuffle(all_groups)

    date_str = tran_date.strftime("%d%m%Y")
    run_id = tran_date.strftime("%Y%m%d") + datetime.now().strftime("%H%M%S")

    # Build file contents
    visa_content, visa_manifest = build_visa_tc_file(all_groups, tran_date)
    switch_content, switch_manifest = build_visa_switch_file(all_groups, tran_date)
    cbs_content, cbs_manifest = build_visa_cbs_file(all_groups, tran_date)

    # File paths
    visa_path    = output_dir / f"VISA_{date_str}.txt"
    switch_path  = output_dir / f"t{tran_date.strftime('%y%m%d')}001-_VISA_SWITCH_TLF"
    cbs_path     = output_dir / f"EX3198_VISA_{date_str}.prt1"
    manifest_path = output_dir / f"manifest_{run_id}.json"

    visa_path.write_text(visa_content, encoding="ascii", errors="replace")
    switch_path.write_text(switch_content, encoding="ascii", errors="replace")
    cbs_path.write_text(cbs_content, encoding="ascii", errors="replace")

    # Build manifest
    all_manifest = visa_manifest + switch_manifest + cbs_manifest
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
            "visa_tc": str(visa_path.name),
            "switch_tlf": str(switch_path.name),
            "cbs": str(cbs_path.name),
        },
        "scenario_summary": scenario_summary,
        "rows": all_manifest,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    # Zip bundle
    zip_path = output_dir / f"visa_testdata_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(visa_path, visa_path.name)
        zf.write(switch_path, switch_path.name)
        zf.write(cbs_path, cbs_path.name)
        zf.write(manifest_path, manifest_path.name)

    return {
        "run_id": run_id,
        "zip_path": str(zip_path),
        "files": {
            "visa_tc": {"path": str(visa_path), "rows": len(visa_manifest)},
            "switch_tlf": {"path": str(switch_path), "rows": len(switch_manifest)},
            "cbs": {"path": str(cbs_path), "rows": len(cbs_manifest)},
        },
        "scenario_summary": scenario_summary,
        "manifest_path": str(manifest_path),
    }


if __name__ == "__main__":
    result = generate("visa_pos_issuer", volume=50)
    print(json.dumps(result, indent=2))
