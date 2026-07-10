import random
from datetime import datetime, timedelta
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction

RECORD_LEN = 168

_TC_MAP = {
    ("ATM", False): "07",
    ("ATM", True):  "27",
    ("POS", False): "05",
    ("POS", True):  "25",
}

# MCCs that trigger quasi-cash classification (special condition indicator " 7")
_QUASI_CASH_MCCS = {"4829", "6050", "6051", "6540", "7995", "7801"}

# Reimbursement fee rates (mirror ep747.py)
_REIMB_RATE_ATM      = 0.00350
_REIMB_RATE_MERCH_CR = 0.00640
_REIMB_RATE_QC       = 0.00004
_FX_RATE             = 0.02
_QUASI_CASH_MCCS_TC46 = _QUASI_CASH_MCCS  # same set, alias for clarity

# Foreign currency codes used for INT transactions (ISO 4217 numeric)
_INT_CURRENCIES = ["840", "978", "826", "036", "124", "392", "702", "458"]

# NFS-internal MCC codes used in x01 records
_NFS_MCC = {"ATM": "6089", "POS": "6088"}


def _tran_kind(tx: Transaction) -> str:
    return "ATM" if tx.mcc == "6011" else "POS"


def _tc(tx: Transaction) -> str:
    # Merchandise credit (TC 06) and its reversal (TC 26) use tran_type "MC"/"MR"
    if tx.tran_type == "MC":
        return "26" if tx.msg_type == "0420" else "06"
    if tx.tran_type == "MR":
        return "26"
    kind = _tran_kind(tx)
    is_rev = tx.msg_type == "0420"
    return _TC_MAP.get((kind, is_rev), "07")


def _gen_arn(tx: Transaction) -> str:
    rng = random.Random(int(tx.rrn) if tx.rrn.isdigit() else hash(tx.rrn) & 0xFFFFFF)
    acquirer_id = rng.randint(100000, 999999)
    arn = f"74{tx.tran_date.strftime('%y%j')}{acquirer_id:06d}{int(tx.rrn):010d}"
    return arn[:23]


def _int_currency(tx: Transaction) -> str:
    """Derive a stable foreign currency code for INT transactions."""
    rng = random.Random(int(tx.rrn) if tx.rrn.isdigit() else hash(tx.rrn) & 0xFFFFFF)
    return rng.choice(_INT_CURRENCIES)


def _place(buf: list, start_1idx: int, value: str, width: int, left: bool = True):
    s = str(value)[:width]
    s = s.ljust(width) if left else s.rjust(width)
    for i, ch in enumerate(s):
        p = start_1idx - 1 + i
        if 0 <= p < RECORD_LEN:
            buf[p] = ch


def _build_tcsn0(tx: Transaction, tc: str, is_dom: bool) -> str:
    """
    Build the x00 base record (168 chars). All field positions verified against
    original EPIN samples.
    """
    buf = [" "] * RECORD_LEN
    p = _place

    setl_flag = "8" if is_dom else "0"
    src_currency = "356" if is_dom else _int_currency(tx)
    dest_currency = "356"
    merchant_country = "IN " if is_dom else "USA"
    mcc = tx.mcc if tx.mcc else ("6011" if _tran_kind(tx) == "ATM" else "5999")
    arn = _gen_arn(tx)

    # Pos 1-4: tc + seq "00"
    p(buf, 1,  tc, 2)
    p(buf, 3,  "0", 1)
    p(buf, 4,  "0", 1)

    # Pos 5-20: PAN (16 chars)
    p(buf, 5,  tx.card_pan[:16].ljust(16), 16)

    # Pos 21-23: member number
    p(buf, 21, "000", 3)

    # Pos 24: transaction indicator — space for TC 06/26 (credit voucher), "Z" otherwise
    p(buf, 24, " " if tc in ("06", "26") else "Z", 1)

    # Pos 25-26: spaces
    p(buf, 25, " ", 1)
    p(buf, 26, " ", 1)

    # Pos 27-49: ARN (23 chars)
    p(buf, 27, arn, 23)

    # Pos 50-57: reserved zeros
    p(buf, 50, "00000000", 8)

    # Pos 58-61: transaction date MMDD
    p(buf, 58, tx.tran_date.strftime("%m%d"), 4)

    # Pos 62-73: dest amount (INR, 12 chars)
    p(buf, 62, f"{tx.amount:012d}", 12)

    # Pos 74-76: dest currency (INR = 356)
    p(buf, 74, dest_currency, 3)

    # Pos 77-88: src amount (12 chars) — same as dest for DOM, foreign for INT
    src_amount = tx.amount
    p(buf, 77, f"{src_amount:012d}", 12)

    # Pos 89-91: src currency
    p(buf, 89, src_currency, 3)

    # Pos 92-116: terminal name / location (25 chars)
    loc = tx.terminal_location or ""
    p(buf, 92, loc[:25].ljust(25), 25)

    # Pos 117-129: terminal city (13 chars)
    city = loc[47:60].strip()[:13] if len(loc) > 47 else ""
    p(buf, 117, city[:13].ljust(13), 13)

    # Pos 130-132: merchant country (3 chars)
    p(buf, 130, merchant_country[:3], 3)

    # Pos 133-136: MCC (4 chars)
    p(buf, 133, mcc[:4].ljust(4), 4)

    # Pos 137-141: 5 zeros
    p(buf, 137, "00000", 5)

    # Pos 142-146: 5 spaces (verified: original has 5 spaces here)
    p(buf, 142, "     ", 5)

    # Pos 147-149: "100" flags
    p(buf, 147, "1", 1)
    p(buf, 148, "0", 1)
    p(buf, 149, "0", 1)

    # Pos 150: settlement flag ("8" DOM, "0" INT)
    p(buf, 150, setl_flag, 1)

    # Pos 151: NFS indicator
    p(buf, 151, "N", 1)

    # Pos 152-157: auth_id (6 chars); pos 158 blank (matches TLF Auth_resp_id / PTLF Approval_Code)
    p(buf, 152, tx.auth_id[:6].ljust(6), 6)

    # Pos 158-159: spaces
    p(buf, 158, " ", 1)
    p(buf, 159, " ", 1)

    # Pos 160: transaction count flag
    p(buf, 160, "0", 1)

    # Pos 161: space
    p(buf, 161, " ", 1)

    # Pos 162-163: POS entry mode (05=POS, 07=ATM)
    pos_mode = "07" if _tran_kind(tx) == "ATM" else "05"
    p(buf, 162, pos_mode, 2)

    # Pos 164-167: cycle/period day (year%10 + 3-digit Julian day)
    cpd = f"{tx.tran_date.year % 10}{tx.tran_date.timetuple().tm_yday:03d}"
    p(buf, 164, cpd, 4)

    # Pos 168: reserved
    p(buf, 168, "0", 1)

    record = "".join(buf)
    assert len(record) == RECORD_LEN
    return record


def _build_tcsn1(tc: str, tx: Transaction, is_dom: bool) -> str:
    """
    Build the x01 authorization-supplement record (168 chars).
    Verified positions against original EPIN DOM/INT samples.
    """
    buf = [" "] * RECORD_LEN
    p = _place

    kind = "ATM" if tc in ("07", "27") else "POS"
    nfs_mcc = _NFS_MCC.get(kind, "6089")

    # Pos 1-4: tc + "01"
    p(buf, 1, tc, 2)
    p(buf, 3, "0", 1)
    p(buf, 4, "1", 1)

    # Pos 5-7: spaces (already blank)

    # Pos 13-16: spaces (already blank)

    # Pos 17-22: sequence counter (6 zeros)
    p(buf, 17, "000000", 6)

    # Pos 23-78: spaces (already blank)

    # Pos 74-75: special condition indicator — " 7" signals quasi-cash
    # (spec: pos 74 = space/1/2/3; pos 75 = 7 or 8 → quasi-cash for National/US)
    # Quasi-cash only applies to TC 05/25/06/26 (POS/merch), never ATM (07/27)
    if tx.mcc in _QUASI_CASH_MCCS and tc not in ("07", "27"):
        p(buf, 74, " ", 1)
        p(buf, 75, "7", 1)

    # Pos 79-80: settlement system indicator
    p(buf, 79, "SY", 2)

    # Pos 81-95: institution identifier (15 chars)
    if is_dom:
        # Use card acceptor / merchant ID padded to 15 chars
        inst = tx.card_acceptor_id[:15].ljust(15)
    else:
        # INT: network routing prefix + foreign country code + zeros
        inst = f"8000{'USD'}{'00000000'}"[:15].ljust(15)
    p(buf, 81, inst, 15)

    # Column 96 (1-indexed): terminal ID, zero-padded to 8 chars, matches Ref6 / terminal_id
    p(buf, 96, tx.auth_id.zfill(8), 8)

    # Pos 104-115: 12 zeros
    p(buf, 104, "000000000000", 12)

    # Pos 116-117: 2 spaces (already blank)

    # Pos 118-121: NFS internal MCC code
    p(buf, 118, nfs_mcc, 4)

    # Pos 122: space (already blank)

    # Pos 123: "0"
    p(buf, 123, "0", 1)

    # Pos 124-127: 4 spaces (already blank)

    # Pos 128: "5"
    p(buf, 128, "5", 1)

    # Pos 129: space (already blank)

    # Pos 130: DOM/INT flag
    p(buf, 130, "1" if is_dom else "0", 1)

    # Pos 131-157: spaces (already blank)

    # Pos 158-166: 9 zeros
    p(buf, 158, "000000000", 9)

    # Pos 167-168: spaces (already blank)

    record = "".join(buf)
    assert len(record) == RECORD_LEN
    return record


def _build_tcsn4(tc: str, tx: Transaction) -> str:
    """
    Build the x04 interchange/routing record (168 chars).
    Verified positions against original EPIN DOM sample.
    """
    buf = [" "] * RECORD_LEN
    p = _place

    # Pos 1-4: tc + "04"
    p(buf, 1, tc, 2)
    p(buf, 3, "0", 1)
    p(buf, 4, "4", 1)

    # Pos 5-10: spaces (already blank)

    # Pos 11-17: routing tag
    p(buf, 11, "SD00025", 7)

    # Pos 18-20: spaces (already blank)

    # Pos 21: transaction count (1 char)
    p(buf, 21, "1", 1)

    # Pos 22-24: spaces (already blank)

    # Pos 25: total transaction count
    p(buf, 25, "1", 1)

    # Pos 26-46: spaces (already blank)

    # Pos 47-54: transaction amount (8 chars, truncated)
    amt8 = f"{tx.amount:08d}"[-8:]
    p(buf, 47, amt8, 8)

    # Pos 55-56: debit/credit indicator
    p(buf, 55, "DB", 2)

    # Pos 57-72: spaces (already blank)

    # Pos 73-96: 24 zeros
    p(buf, 73, "0" * 24, 24)

    # Pos 97-99: spaces (already blank)

    # Pos 100-115: settlement amount (16 chars)
    # Interchange fee at ~0.25% for ATM, ~0.90% for POS
    rate = 0.0025 if tc in ("07", "27") else 0.009
    ic_fee = int(tx.amount * rate)
    p(buf, 100, f"{ic_fee:016d}", 16)

    # Pos 116: Visa indicator
    p(buf, 116, "V", 1)

    # Pos 117-155: routing/batch reference (39 chars)
    # Use RRN + tran_date + zeros to fill
    ref = f"001{tx.rrn.zfill(12)}{tx.tran_date.strftime('%m%d%Y%H%M%S')}00000000"
    p(buf, 117, ref[:39], 39)

    # Pos 156-168: spaces (already blank)

    record = "".join(buf)
    assert len(record) == RECORD_LEN
    return record


def _build_tcsn5(tc: str, tx: Transaction, is_dom: bool) -> str:
    """
    Build the x05 billing/settlement amount record (168 chars).
    Field positions verified against original: src_amount at 20-31, src_currency at 32-34.
    """
    buf = [" "] * RECORD_LEN
    p = _place

    src_currency = "356" if is_dom else _int_currency(tx)

    # Pos 1-4: tc + "05"
    p(buf, 1, tc, 2)
    p(buf, 3, "0", 1)
    p(buf, 4, "5", 1)

    # Pos 5-19: reserved (zeros — exact original field unknown)
    p(buf, 5, "0" * 15, 15)

    # Pos 20-31: src amount (12 chars) — verified position from original
    p(buf, 20, f"{tx.amount:012d}", 12)

    # Pos 32-34: src currency — verified position from original
    p(buf, 32, src_currency, 3)

    # Pos 35-166: mostly zeros and spaces — fill key known zero fields
    p(buf, 35, "0" * 10, 10)     # pos 35-44
    p(buf, 45, "0000", 4)        # pos 45-48
    p(buf, 50, "0" * 12, 12)     # pos 50-61 (12 zeros)
    p(buf, 97, "0" * 12, 12)     # pos 97-108 (billing amount zeros)
    p(buf, 109, "0" * 12, 12)    # pos 109-120

    # Pos 167: debit indicator
    p(buf, 167, "D", 1)

    record = "".join(buf)
    assert len(record) == RECORD_LEN
    return record


def _records_for_tx(tx: Transaction, tc: str, is_dom: bool) -> List[str]:
    r0 = _build_tcsn0(tx, tc, is_dom)
    r1 = _build_tcsn1(tc, tx, is_dom)
    r5 = _build_tcsn5(tc, tx, is_dom)
    if tc in ("07", "27", "06", "26"):
        r4 = _build_tcsn4(tc, tx)
        return [r0, r1, r4, r5]
    return [r0, r1, r5]


def _build_footer(
    record_type: str,   # "91" or "92"
    tx_count: int,
    total_amount: int,  # sum of tx.amount in paise
    total_records: int, # total data records written
    run_ts: datetime,
    tran_date: datetime,
    is_dom: bool,
) -> str:
    """
    Build one 168-char EPIN footer record (91 or 92).

    Known field positions from actual files:
      1-2  : record type ("91"/"92")
      3-6  : "0040" (constant)
      7-10 : HHMM of run timestamp
      11-12: yy (2-digit year of tran_date)
      13-15: ddd (Julian day of settlement date = tran_date + 1)
      16-20: "00000" for 91; "00001" for DOM-92, "00000" for INT-92
      41-42: type-specific 2-digit code (03/22 for 91, 60/96 for 92)
      43-44: "00"
      45-48: tx_count (confirmed from samples)
      49-50: "00"
      61-66: "000000"
      67-74: spaces (separator)
      161  : "0"

    Variable fields (best approximation):
      91 pos 21-30: total_records * 9741  (matches DOM sample; INT differs)
      92 pos 21-30: total_amount          (total paise for this file)
      92 pos 51-60: fee ≈ total_amount * 0.001%
      92 pos 75-84: total_records * 601   (scales with tx_count in samples)
    """
    buf = [" "] * RECORD_LEN
    p = _place

    settlement_date = tran_date + timedelta(days=1)
    hhmm = run_ts.strftime("%H%M")
    yy   = tran_date.strftime("%y")
    ddd  = f"{settlement_date.timetuple().tm_yday:03d}"
    is_91 = record_type == "91"

    p(buf, 1,  record_type, 2)
    p(buf, 3,  "0040", 4)
    p(buf, 7,  hhmm, 4)
    p(buf, 11, yy, 2)
    p(buf, 13, ddd, 3)

    if is_91:
        p(buf, 16, "00000", 5)
        p(buf, 21, f"{total_records * 9741:010d}"[-10:], 10)
        p(buf, 31, "0000000000", 10)
        p(buf, 41, "03" if is_dom else "22", 2)
        p(buf, 43, "00", 2)
        p(buf, 45, f"{tx_count:04d}", 4)
        p(buf, 49, "00", 2)
        p(buf, 51, "0000000000", 10)
        p(buf, 61, "000000", 6)
        # pos 67-74: spaces (default)
        p(buf, 75, "0000000000", 10)
    else:
        p(buf, 16, "00001" if is_dom else "00000", 5)
        p(buf, 21, f"{total_amount:010d}"[-10:], 10)
        p(buf, 31, "0000000000", 10)
        p(buf, 41, "60" if is_dom else "96", 2)
        p(buf, 43, "00", 2)
        p(buf, 45, f"{tx_count:04d}", 4)
        p(buf, 49, "00", 2)
        fee = int(total_amount * 0.00001)
        p(buf, 51, f"{fee:010d}"[-10:], 10)
        p(buf, 61, "000000", 6)
        # pos 67-74: spaces (default)
        p(buf, 75, f"{total_records * 601:010d}"[-10:], 10)

    # pos 85-161: zeros
    for i in range(84, 161):
        buf[i] = "0"

    # pos 161 already set by loop; pos 162-168: spaces (default)

    record = "".join(buf)
    assert len(record) == RECORD_LEN, f"footer len={len(record)}"
    return record


def _collect_tc46_stats(
    groups: List[ScenarioGroup],
    dom: bool,
    bin_prefixes: List[str],
) -> dict:
    """Collect per-category amounts for TC=46 generation."""
    dom_flag = "D" if dom else "I"
    s = {"atm_fwd": 0, "atm_rev": 0, "pur_fwd": 0, "pur_rev": 0,
         "mc_fwd": 0, "qc_fwd": 0}
    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_category != dom_flag:
                continue
            if tx.tran_type == "OW":
                continue
            if bin_prefixes and not any(tx.card_pan.startswith(bp) for bp in bin_prefixes):
                continue
            is_atm = tx.mcc == "6011"
            is_qc  = tx.mcc in _QUASI_CASH_MCCS_TC46
            is_rev = tx.msg_type == "0420"
            is_mc  = tx.tran_type == "MC"
            if is_rev:
                if is_mc:
                    pass
                elif is_atm:
                    s["atm_rev"] += tx.amount
                elif not is_qc:
                    s["pur_rev"] += tx.amount
            elif is_mc:
                s["mc_fwd"] += tx.amount
            elif is_qc and not is_atm:
                s["qc_fwd"] += tx.amount
            elif is_atm:
                s["atm_fwd"] += tx.amount
            else:
                s["pur_fwd"] += tx.amount
    return s


def _tc46(
    service_id: str,     # 3 chars: "025" or "001"
    report_id: str,      # 3 chars: "130", "110", "210"
    biz_mode: str,       # 1 char:  "2" or "9"
    biz_tran_type: str,  # 3 chars: "310", "100", "852", "200", "210", "   "
    summary_lvl: str,    # 2 chars: "06", "08", "  "
    comp_seq: str,       # 1 char:  "1" or " "
    rev_ind: str,        # 1 char:  "N", "Y", or " "
    biz_cycle: str,      # 1 char:  "1" or " "
    amount_type: str,    # 1 char:  "T" or " "
    rep_for: str,        # 10 chars SRE ID
    roll_to: str,        # 10 chars SRE ID
    funds_xfer: str,     # 10 chars SRE ID
    third_amt: int,      # paise
    fifth_amt: int,      # paise
    net_amt: int,        # paise
) -> str:
    buf = [" "] * RECORD_LEN

    def _s(pos, val, width):
        s = str(val)[:width].ljust(width)
        for i, c in enumerate(s):
            if 0 <= pos - 1 + i < RECORD_LEN:
                buf[pos - 1 + i] = c

    _s(1,  "46",                   2)
    _s(3,  service_id,             3)
    _s(6,  report_id,              3)
    _s(9,  biz_mode,               1)
    _s(10, biz_tran_type,          3)
    _s(13, summary_lvl,            2)
    _s(15, comp_seq,               1)
    _s(16, rev_ind,                1)
    _s(17, biz_cycle,              1)
    _s(18, amount_type,            1)
    _s(19, rep_for,               10)
    _s(29, roll_to,               10)
    _s(39, funds_xfer,            10)
    _s(49, f"{third_amt:012d}",   12)
    _s(61, f"{fifth_amt:012d}",   12)
    _s(73, f"{net_amt:012d}",     12)
    # pos 85–168: spaces (already default)

    record = "".join(buf)
    assert len(record) == RECORD_LEN
    return record


def _tc46_dom(groups: List[ScenarioGroup], config: dict) -> List[str]:
    """TC=46 summary records for DOM EPIN file."""
    dom_var  = config["visa"]["domestic"]
    funds_id = dom_var["funds_xfer_entity"]
    all_bins = [e["bin"] for e in dom_var["reporting_for"]]
    s = _collect_tc46_stats(groups, True, all_bins)

    atm_fee = int(s["atm_fwd"] * _REIMB_RATE_ATM)
    mc_fee  = int(s["mc_fwd"]  * _REIMB_RATE_MERCH_CR)
    qc_fee  = int(s["qc_fwd"]  * _REIMB_RATE_QC)
    iss_db  = s["atm_fwd"] + s["pur_fwd"] + s["qc_fwd"]
    iss_cr  = s["mc_fwd"]  + s["atm_rev"] + s["pur_rev"]
    net_ic  = abs(iss_db - iss_cr)

    recs: List[str] = []
    # VSS-130 fee records (ATM, BI=0, MC, QC)
    for btt, fee in [("310", atm_fee), ("852", 0), ("200", mc_fee), ("210", qc_fee)]:
        recs.append(_tc46("025", "130", "2", btt, "06", "1", " ", " ", " ",
                          funds_id, funds_id, funds_id,
                          fee, 0, 0))
    # VSS-110 net settlement
    recs.append(_tc46("025", "110", "9", "   ", "  ", " ", " ", " ", "T",
                      funds_id, funds_id, funds_id,
                      0, 0, net_ic))
    return recs


def _tc46_int(groups: List[ScenarioGroup], config: dict) -> List[str]:
    """TC=46 summary records for INT EPIN file."""
    int_var  = config["visa"]["international"]
    funds_id = int_var["funds_xfer_entity"]
    rollup   = int_var["rollup_to"]
    biz_id   = rollup["biz_id"]
    debit_id = rollup["debit_id"]

    biz_bins   = [e["bin"] for e in int_var["reporting_for"] if e.get("bucket", "debit") == "biz"]
    debit_bins = [e["bin"] for e in int_var["reporting_for"] if e.get("bucket", "debit") != "biz"]
    all_bins   = biz_bins + debit_bins

    biz_s   = _collect_tc46_stats(groups, False, biz_bins)
    debit_s = _collect_tc46_stats(groups, False, debit_bins)
    all_s   = _collect_tc46_stats(groups, False, all_bins)

    recs: List[str] = []

    def add_130(s: dict, rep_for: str) -> None:
        atm_fee = int(s["atm_fwd"] * _REIMB_RATE_ATM)
        mc_fee  = int(s["mc_fwd"]  * _REIMB_RATE_MERCH_CR)
        for btt, fee in [("310", atm_fee), ("852", 0), ("200", mc_fee)]:
            recs.append(_tc46("001", "130", "2", btt, "06", "1", " ", " ", " ",
                              rep_for, rep_for, funds_id,
                              fee, 0, 0))

    def add_210(s: dict, rep_for: str, roll_to: str) -> None:
        fx_pur_fwd  = int(s["pur_fwd"] * _FX_RATE)
        fx_atm_fwd  = int(s["atm_fwd"] * _FX_RATE)
        fx_pur_rev  = int(s["pur_rev"] * _FX_RATE)
        fx_atm_rev  = int(s["atm_rev"] * _FX_RATE)
        # Forward POS
        recs.append(_tc46("001", "210", "2", "100", "08", "1", "N", "1", " ",
                          rep_for, roll_to, funds_id, 0, fx_pur_fwd, 0))
        # Forward ATM
        recs.append(_tc46("001", "210", "2", "310", "08", "1", "N", " ", " ",
                          rep_for, roll_to, funds_id, 0, fx_atm_fwd, 0))
        # Reversal ATM
        recs.append(_tc46("001", "210", "2", "310", "08", "1", "Y", "1", " ",
                          rep_for, roll_to, funds_id, 0, fx_atm_rev, 0))
        # Reversal POS
        recs.append(_tc46("001", "210", "2", "100", "08", "1", "Y", "1", " ",
                          rep_for, roll_to, funds_id, 0, fx_pur_rev, 0))

    # VSS-130 per BIZ and DEBIT buckets
    add_130(biz_s,   biz_id)
    add_130(debit_s, debit_id)

    # VSS-210 per BIZ and DEBIT (for bin-level MINUS formula, rollup_to = bucket SRE)
    add_210(biz_s,   rep_for=biz_id,   roll_to=biz_id)
    add_210(debit_s, rep_for=debit_id, roll_to=debit_id)

    # VSS-210 at root (for VISA_INT_ISS_ATM_CUR_CONV / POS_CUR_CONV queries)
    add_210(all_s, rep_for=funds_id, roll_to=funds_id)

    # VSS-110 net settlement at root
    iss_db = all_s["atm_fwd"] + all_s["pur_fwd"] + all_s["qc_fwd"]
    iss_cr = all_s["mc_fwd"]  + all_s["atm_rev"] + all_s["pur_rev"]
    net_ic = abs(iss_db - iss_cr)
    recs.append(_tc46("001", "110", "9", "   ", "  ", " ", " ", " ", "T",
                      funds_id, funds_id, funds_id, 0, 0, net_ic))

    return recs


def build_epin_file(
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool = True,
    run_ts: datetime = None,
    tran_date: datetime = None,
) -> Tuple[str, str]:
    if run_ts is None:
        run_ts = datetime.now()
    if tran_date is None:
        tran_date = run_ts

    lines: List[str] = []
    target_category = "D" if dom else "I"
    tx_count = 0
    total_amount = 0

    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_category != target_category:
                continue
            tc = _tc(tx)
            records = _records_for_tx(tx, tc, dom)
            for rec in records:
                assert len(rec) == RECORD_LEN, f"EPIN record len={len(rec)}"
                lines.append(rec)
            tx_count += 1
            total_amount += tx.amount

    total_records = len(lines)

    # TC=46 summary records (before footers)
    if "visa" in config:
        tc46_fn = _tc46_dom if dom else _tc46_int
        for rec in tc46_fn(groups, config):
            assert len(rec) == RECORD_LEN, f"TC=46 record len={len(rec)}"
            lines.append(rec)

    footer_91 = _build_footer("91", tx_count, total_amount, total_records, run_ts, tran_date, dom)
    footer_92 = _build_footer("92", tx_count, total_amount, total_records, run_ts, tran_date, dom)
    lines.append(footer_91)
    lines.append(footer_92)

    content = "\n".join(lines)
    date_ddmmyy = tran_date.strftime("%d%m%y")
    filename = (f"SAVE.INCOMING.ASCII.EPIN_DOM_{date_ddmmyy}.txt" if dom
                else f"SAVE.INCOMING.ASCII.EPIN_INT_{date_ddmmyy}.txt")
    return content, filename
