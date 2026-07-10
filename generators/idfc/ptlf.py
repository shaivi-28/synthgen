import random
from datetime import datetime, timedelta
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction

RECORD_LEN = 1898
ENTRY_LEN = 6 + RECORD_LEN               # 1904 chars per transaction line
HEADER_LEN = 6 + 72 + 76 + RECORD_LEN    # 2052 chars for first line

DATASET_NAME = "TANGO.$IDFEDS.TGPTIDFE.PO"  # 25-char JCL GDS name


def _ts19(ms: int) -> str:
    sec = ms // 1000
    frac = ms % 1000
    return f"{sec:010d}{frac:03d}      "


def _build_ptlf_line(
    tx: Transaction,
    config: dict,
    file_id: str,    # 19-char file-level batch identifier
    entry_ms: int,   # Unix timestamp in milliseconds for entry time
    exit_ms: int,    # Unix timestamp in milliseconds for exit time
) -> str:
    buf = [" "] * ENTRY_LEN

    def place(pos_1idx: int, value: str, width: int):
        s = str(value)[:width].ljust(width)
        for i, ch in enumerate(s):
            p = pos_1idx - 1 + i
            if 0 <= p < ENTRY_LEN:
                buf[p] = ch

    is_rev = tx.msg_type == "0420"
    tran_date = tx.tran_date
    setl_date = tx.settlement_date
    tran_code = config["pos"]["tran_code_prefix"] + "0000"
    setl_flag = "8" if tx.tran_category == "D" else "0"
    rev_code = "20" if is_rev else "00"
    resp = "001"
    approval = tx.auth_id[:6].ljust(8)   # 6 non-space chars then whitespace, matches EPIN AUTHORIZATION_CODE
    loc = tx.terminal_location or ""

    # ── Entry header (positions 1-35) ────────────────────────────────────────
    place(1,  f"{ENTRY_LEN:06d}", 6)         # 001904 entry-length prefix
    place(7,  f"{RECORD_LEN:06d}", 6)        # 001898 record-body length marker
    place(13, "DR", 2)                        # direction prefix
    place(15, file_id[:19], 19)               # file-level batch identifier (positions 15-33)
    # tran_type: "06"=MC forward, "26"=MC reversal, "20"=POS reversal, "01"=POS forward
    if tx.tran_type == "MC":
        _ttype = "26" if is_rev else "06"
    else:
        _ttype = "20" if is_rev else "01"
    place(34, _ttype, 2)                      # tran_type (positions 34-35)

    # ── Card identifiers ──────────────────────────────────────────────────────
    place(36,  "PRO2", 4)                     # Card_Ln
    place(40,  "IDFC", 4)                     # Card_Fiid
    place(44,  tx.card_pan[:19].ljust(19), 19)   # Card_Num
    place(63,  "000", 3)                      # Member_Number

    # ── Retailer / terminal identifiers ──────────────────────────────────────
    term_fiid = config.get("ptlf", {}).get("term_fiid", "IDFE")
    place(66,  term_fiid, 4)                  # Retailer_Ln
    place(70,  term_fiid, 4)                  # Retailer_Fiid
    place(74,  "0000", 4)                     # Retailer_Group
    place(78,  "0000", 4)                     # Retailer_Region
    place(82,  tx.card_acceptor_id[:19].ljust(19), 19)   # Retailer_ID
    place(101, tx.terminal_id[:16].ljust(16), 16)        # Retailer_Term_ID
    place(117, "001", 3)                      # Shift_Number
    place(120, "001", 3)                      # Batch_Number
    place(123, term_fiid, 4)                  # Term_Ln
    place(127, term_fiid, 4)                  # Term_Fiid
    place(131, tx.terminal_id[:16].ljust(16), 16)        # Term_ID
    place(147, tran_date.strftime("%H%M%S") + "00", 8)   # Tran_Time
    place(155, tx.terminal_id[:16].ljust(16), 16)        # Alt_Term_ID
    place(171, "5", 1)                        # Alt_Rec_Format
    place(172, tx.card_acceptor_id[:19].ljust(19), 19)   # Alt_Retailer_ID
    # Clerk_ID (191-196): 6 spaces (default, no place call needed)
    place(197, "0", 1)                        # Data_Flag

    # ── Message type / routing ────────────────────────────────────────────────
    # PTLF records settled responses; convert request/advice → response type
    _msg_type = {"0200": "0210", "0400": "0410", "0420": "0410"}.get(tx.msg_type[:4], tx.msg_type[:4])
    place(198, _msg_type, 4)                  # Msg_Type (response)
    place(202, "00", 2)                       # Msg_Status
    place(204, "7", 1)                        # Originator (constant for IDFC NFS)
    place(205, "5", 1)                        # Respondor
    place(206, "99", 2)                       # Issuer_Code (constant for IDFC NFS)

    # ── Entry timestamps: 3 × 19-char Unix ms (entry + exit + re-entry) ───────
    all_ts = _ts19(entry_ms) + _ts19(exit_ms) + _ts19(exit_ms)  # 57 chars
    place(208, all_ts, 57)

    # ── Transaction date / time / settlement ──────────────────────────────────
    place(265, tran_date.strftime("%y%m%d"), 6)          # Tran_Date
    place(271, tran_date.strftime("%H%M%S") + "00", 8)   # Tran_Time_2
    place(279, tran_date.strftime("%y%m%d"), 6)          # Post_Date
    place(285, setl_date.strftime("%y%m%d"), 6)          # Acq_Interchange_Date (YYMMDD)
    place(291, setl_date.strftime("%y%m%d"), 6)          # Iss_Interchange_Date (YYMMDD)
    place(297, tx.rrn, 12)                               # Seq_Num (RRN, no leading zeros)

    # ── Terminal location ─────────────────────────────────────────────────────
    place(309, loc[:25].ljust(25), 25)        # Term_Name_Loc
    place(334, loc[:22].ljust(22), 22)        # Term_Owner_Name
    place(356, loc[:13].ljust(13), 13)        # Term_City
    place(369, "IND", 3)                      # Term_State
    place(372, "IN", 2)                       # Term_Country
    place(374, "IDFE", 4)                     # Branch_ID
    # User_Field (378-380): 3 spaces (default, no place call needed)
    place(381, "00330", 5)                    # Time_Offset (IST UTC+5:30 = 330 min)
    place(386, tx.acquirer_id[:11].ljust(11), 11)        # Acq_Inst_ID
    place(397, "00000000000", 11)             # Rcv_Inst_ID
    # Term_Type (408-409): spaces (default)
    # Clerk_ID_2 (410-415): spaces (default)
    # CRT_Auth_Group (416-419): spaces (default)
    # Crt_Auth_User (420-427): spaces (default)
    tran_orig = config.get("ptlf", {}).get("tran_orig", "VISA")
    place(428, tx.mcc[:4].ljust(4), 4)        # SIC_Code
    place(432, tran_orig, 4)                  # Tran_Orig
    place(436, "0000", 4)                     # Tran_Dest

    # ── Transaction / account / amounts ──────────────────────────────────────
    place(440, tran_code, 6)                  # Tran_Code
    place(446, "P ", 2)                       # Card_Type
    place(448, tx.account_no[:17].zfill(17), 17)         # Account_Number (17 digits, zero-padded; pos 465-466 remain spaces)
    place(467, resp, 3)                       # Response_Code
    place(470, f"{tx.amount:019d}", 19)       # Amt_1
    place(489, "0" * 19, 19)                  # Amt_2
    place(508, "0000", 4)                     # Expiration_Date
    place(512, "0" * 40, 40)                  # Track2
    # Pin_Offset (552-567): 16 spaces (default, no place call needed)
    place(568, "000000000000", 12)            # Pre_Auth_Seq_Num
    place(580, "0000000000", 10)              # Invoice_Num
    place(590, "0000000000", 10)              # Orig_Invoice_Num
    place(600, tx.auth_id[:6], 6)            # Authorizer (6-digit auth code, no leading zero)
    place(616, "0", 1)                        # Auth_Indicator
    place(617, "001", 3)                      # Shift_Number_2
    place(620, "001", 3)                      # Batch_Seq_Number
    place(623, approval, 8)                   # Approval_Code
    place(631, "1", 1)                        # Approval_Code_Length
    place(632, "00000000", 8)                 # Interchange_Response
    place(640, "0000", 4)                     # Pseudo_ID_Number
    place(644, "0" * 20, 20)                  # Referral_Phone
    place(664, "0", 1)                        # Draft_Capture_Flag
    place(665, setl_flag, 1)                  # Settlement_Flag
    place(666, rev_code, 2)                   # Reversal_Code
    place(668, "00", 2)                       # ChargeBack_Reason
    place(670, "0", 1)                        # ChargeBack_Occurance
    place(671, "00", 2)                       # Transaction_Origin
    pan_entry_mode = config.get("ptlf", {}).get("pan_entry_mode_pos", "000")
    place(673, pan_entry_mode, 3)             # POS_EntryMode
    place(676, "0", 1)                        # Auth_Indicator_2
    place(677, "356", 3)                      # Currency_Code (INR)

    result = "".join(buf)
    assert len(result) == ENTRY_LEN, f"PTLF line len={len(result)}"
    return result


def build_ptlf_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    run_ts: datetime,
) -> Tuple[str, str]:
    yyyymmdd = tran_date.strftime("%Y%m%d")
    ts_str = run_ts.strftime("%y%m%d%H%M%S")
    filename = f"ptlfxIDF{yyyymmdd}_{ts_str}.txt"

    yymmdd = tran_date.strftime("%y%m%d")
    hhmmss = run_ts.strftime("%H%M%S")
    run_id = f"{random.randint(1, 99):02d}"

    # File-level batch identifier: Unix epoch seconds of tran_date + 3-digit frac + 6 zeros
    base_epoch = int(tran_date.timestamp())
    frac = random.randint(100, 999)
    file_id = f"{base_epoch:010d}{frac:03d}000000"  # 19 chars

    # ── Header sub-records ─────────────────────────────────────────────────
    # Sub-record 1 (72 chars total): THA record with settlement date
    setl_yymmdd = (tran_date + timedelta(days=1)).strftime("%y%m%d")
    sub1_content = f"THA{setl_yymmdd}{hhmmss}{run_id}PRO260        PTLF" + " " * 31
    sub1 = f"000072{sub1_content}"
    assert len(sub1) == 72, f"sub1 len={len(sub1)}"

    # Sub-record 2 (76 chars total): FH record with dataset name
    sub2_content = f"FH {yymmdd}{hhmmss}{run_id}PRO260PTLF    {DATASET_NAME}{yymmdd}    D1 1"
    sub2 = f"000076{sub2_content}"
    assert len(sub2) == 76, f"sub2 len={len(sub2)}"

    # ── Collect all POS (non-ATM) transactions ─────────────────────────────
    pos_txns: List[Transaction] = []
    for sg in groups:
        for tx in sg.switch_rows:
            if tx.mcc != "6011":
                pos_txns.append(tx)

    if not pos_txns:
        return "", filename

    lines: List[str] = []
    for i, tx in enumerate(pos_txns):
        entry_ms = int(tx.tran_date.timestamp() * 1000) + random.randint(100, 999)
        exit_ms = entry_ms + random.randint(50, 200)

        entry = _build_ptlf_line(tx, config, file_id, entry_ms, exit_ms)
        assert len(entry) == ENTRY_LEN, f"PTLF entry len={len(entry)}"

        if i == 0:
            # First record is embedded in the header row; strip the 001904 entry-length
            # prefix (6 chars) so the record starts directly with 001898.
            record = entry[6:]  # 1898 chars
            row_len = 6 + len(sub1) + len(sub2) + len(record)  # 2052
            lines.append(f"{row_len:06d}{sub1}{sub2}{record}")
        else:
            lines.append(entry)

    content = "\n".join(lines)
    return content, filename
