import random
from datetime import datetime, timedelta
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction

RECORD_LEN = 864
RECORDS_PER_ROW = 4  # 4 records packed per physical row
FOOTER_LEN = 198    # 6 + 106 (FT record) + 86 (TT record)

DATASET_NAME = "TANGO.$IDFEDS.TGATIDFE.TL"  # 25-char JCL GDS name


def _build_tlf_record(
    tx: Transaction,
    config: dict,
    term_ln: str,
    term_fiid: str,
    term_br_id: str,
    file_id: str,       # 19-char file-level batch identifier
    entry_ms: int,      # Unix timestamp in milliseconds for entry time
    exit_ms: int,       # Unix timestamp in milliseconds for exit/re-entry time
) -> str:
    """Build one 864-char TLF record.

    Physical layout:
      1-6   : '000864'  (record-length constant)
      7-8   : 'DR'      (direction prefix)
      9-27  : file_id   (19-char file-level batch identifier)
      28-29 : Tran_type (field-table pos 34, physical = ft_pos - 6)
      30-33 : 'T2A2'    (fixed routing code)
      34+   : all field-table fields at physical = ft_pos - 6
    """
    buf = [" "] * RECORD_LEN

    def place(phys_1idx: int, value: str, width: int):
        s = str(value)[:width].ljust(width)
        for i, ch in enumerate(s):
            p = phys_1idx - 1 + i
            if 0 <= p < RECORD_LEN:
                buf[p] = ch

    def fplace(ft_pos: int, value: str, width: int):
        """Field-table position (1-indexed from after the 000864 header)."""
        place(ft_pos - 6, value, width)

    is_rev = tx.msg_type == "0420"
    tran_date = tx.tran_date
    setl_date = tx.settlement_date
    tran_code = config["atm"]["tran_code_prefix"] + "0000"

    # ── Positions 1-33 (pre-field-table area) ─────────────────────────────
    place(1,  "000864", 6)                                   # record-length marker
    place(7,  "DR", 2)                                       # fixed direction prefix
    place(9,  file_id[:19], 19)                              # file-level batch identifier (9-27)
    fplace(34, "20" if is_rev else "01", 2)                  # Tran_type (phys 28)
    place(30, "T2A2", 4)                                     # fixed routing code

    # ── Terminal identifiers ───────────────────────────────────────────────
    fplace(40,  term_ln[:4].ljust(4), 4)                     # Term_ln
    fplace(44,  term_fiid[:4].ljust(4), 4)                   # Term_fiid
    fplace(48,  tx.terminal_id[:16].ljust(16), 16)           # Term_id
    fplace(64,  "PRO2", 4)                                   # Card_ln
    fplace(68,  "IDFE", 4)                                   # Card_fiid
    fplace(72,  tx.card_pan[:19].ljust(19), 19)              # Card_num
    fplace(91,  "000", 3)                                    # Mbr_num
    fplace(94,  term_br_id[:4].ljust(4), 4)                  # Term_br_id
    fplace(98,  "0000", 4)                                   # Term_rgn_id

    # ── Message header ─────────────────────────────────────────────────────
    fplace(104, "31", 2)                                     # Env_ind
    fplace(106, tx.msg_type[:4], 4)                         # Msg_type
    fplace(110, "00", 2)                                     # reserved gap (phys 104-105)
    fplace(112, "1", 1)                                      # Tran_org
    fplace(113, "7", 1)                                      # Tran_res

    # ── Timestamps (Unix ms: 10-digit sec + 3-digit ms + 6 spaces = 19ch) ─
    def _ts19(ms: int) -> str:
        sec = ms // 1000
        frac = ms % 1000
        return f"{sec:010d}{frac:03d}      "

    fplace(114, _ts19(entry_ms), 19)   # Entr_time
    fplace(133, _ts19(exit_ms), 19)    # Exit_time
    fplace(152, _ts19(exit_ms), 19)    # Re_entr_time

    # ── Transaction date / time ────────────────────────────────────────────
    fplace(171, tran_date.strftime("%y%m%d"), 6)             # tran_date_Normal
    fplace(177, tran_date.strftime("%H%M%S") + "00", 8)      # Tran_time_Normal
    fplace(185, tran_date.strftime("%y%m%d"), 6)             # Post_date_Normal
    fplace(191, "  " + setl_date.strftime("%m%d"), 6)        # Acq_setl_date
    fplace(197, "  " + setl_date.strftime("%m%d"), 6)        # Iss_setl_date

    # ── Sequence / routing ─────────────────────────────────────────────────
    fplace(203, tx.rrn.zfill(12), 12)                        # Seq_num_Normal (zero-padded to match CBS RRN)
    fplace(215, "14", 2)                                     # Term_type (14=ATM)
    fplace(217, "00000", 5)                                  # Time_ofst
    fplace(222, tx.acquirer_id[:11].ljust(11), 11)           # Acq_inst_id
    fplace(233, "00000000000", 11)                           # Rcv_inst_id
    fplace(244, tran_code, 6)                                # Tran_code

    # ── Account numbers ────────────────────────────────────────────────────
    acct19 = tx.account_no[:19].ljust(19)
    fplace(250, acct19, 19)                                  # From_acct
    fplace(270, "0" * 19, 19)                                # To_acct

    # ── Amounts (paise, integer, 19 chars) ────────────────────────────────
    fplace(289, "0", 1)                                      # Multi_acct_indt
    fplace(290, f"{tx.amount:019d}", 19)                     # Amt_1
    fplace(309, "0" * 19, 19)                                # Amt_2
    fplace(328, "0" * 19, 19)                                # Amt_3

    # ── Response / terminal location ──────────────────────────────────────
    resp = tx.resp_code[:3].zfill(3) if tx.resp_code else "000"
    fplace(358, resp, 3)                                     # Resp_code

    loc = tx.terminal_location or ""
    fplace(361, loc[:25].ljust(25), 25)                      # Term_name_loc
    fplace(386, loc[:22].ljust(22), 22)                      # Term_own_name
    fplace(408, loc[:13].ljust(13), 13)                      # Term_city
    fplace(421, "IND", 3)                                    # Term_stat
    fplace(424, "IN", 2)                                     # Term_Cntry

    # ── Original-transaction reference ────────────────────────────────────
    fplace(426, tx.rrn.zfill(12), 12)                        # Oseq_num (zero-padded to match CBS RRN)
    fplace(437, tran_date.strftime("%m%d"), 4)               # Otran_date
    fplace(441, tran_date.strftime("%H%M%S") + "00", 8)      # Otran_time
    fplace(449, "0000", 4)                                   # B24_post_date

    # ── Currency / conversion ──────────────────────────────────────────────
    fplace(454, "356", 3)                                    # Org_crncy_code (INR)
    fplace(457, "356", 3)                                    # Acq_crncy_code
    fplace(460, "00000001", 8)                               # Acq_conv_rate
    fplace(468, "356", 3)                                    # Iss_crncy_code
    fplace(471, "00000001", 8)                               # Iss_conv_rate
    fplace(479, "0" * 19, 19)                                # Conv_date_time (zeros, no conversion)

    # ── Reversal / PIN / auth ──────────────────────────────────────────────
    fplace(498, "20" if is_rev else "00", 2)                 # Rvsl_code
    fplace(500, "0" * 16, 16)                                # Pin_ofst
    fplace(516, "0", 1)                                      # Shrg_grp
    fplace(517, "0", 1)                                      # Dest_order
    fplace(518, tx.auth_id[:6].ljust(7), 7)                  # Auth_resp_id — 6 non-space chars then whitespace, matches EPIN AUTHORIZATION_CODE
    fplace(539, tx.rrn.zfill(12), 12)                        # AADHAR/RRN (zero-padded to match CBS RRN)

    record = "".join(buf)
    assert len(record) == RECORD_LEN, f"TLF record len={len(record)}"
    return record


def _build_footer(total_records: int, tran_date: datetime, run_ts: datetime) -> str:
    """One 198-char footer row: FT record (106) + TT record (86)."""
    yymmdd = tran_date.strftime("%y%m%d")
    hh3 = run_ts.strftime("%H%M%S")[:3]

    ft_content = (
        "FT"
        + "TLF     "
        + DATASET_NAME[:25].ljust(25)
        + yymmdd
        + "     "
        + "0" * 12
        + f"{total_records:08d}"
        + "0" * 8
        + f"{total_records:08d}"
        + yymmdd
        + hh3
        + "0" * 9
    )
    assert len(ft_content) == 100, f"FT footer content len={len(ft_content)}"

    tt_content = (
        "TT"
        + " " * 8
        + "TLFXT"
        + " " * 31
        + "0" * 28
        + f"{total_records:06d}"
    )
    assert len(tt_content) == 80, f"TT footer content len={len(tt_content)}"

    row = f"000198000106{ft_content}000086{tt_content}"
    assert len(row) == FOOTER_LEN, f"Footer row len={len(row)}"
    return row


def build_tlf_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    run_ts: datetime,
) -> Tuple[str, str]:
    yyyymmdd = tran_date.strftime("%Y%m%d")
    ts_str = run_ts.strftime("%y%m%d%H%M%S")
    filename = f"tlfx{yyyymmdd}_{ts_str}.txt"

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
    sub1_content = f"THA{setl_yymmdd}{hhmmss}{run_id}PRO260        TLF" + " " * 32
    sub1 = f"000072{sub1_content}"
    assert len(sub1) == 72, f"sub1 len={len(sub1)}"

    # Sub-record 2 (76 chars total): FH record with dataset name
    sub2_content = f"FH {yymmdd}{hhmmss}{run_id}PRO260TLF     {DATASET_NAME}{yymmdd}    D1 1"
    sub2 = f"000076{sub2_content}"
    assert len(sub2) == 76, f"sub2 len={len(sub2)}"

    # ── Collect all ATM (mcc=6011) transactions ────────────────────────────
    atm_txns: List[Transaction] = []
    for sg in groups:
        for tx in sg.switch_rows:
            if tx.mcc == "6011":
                atm_txns.append(tx)

    if not atm_txns:
        return "", filename

    # Build one record per transaction (IDFE issuer perspective)
    all_records: List[str] = []
    for tx in atm_txns:
        entry_ms = int(tx.tran_date.timestamp() * 1000) + random.randint(100, 999)
        exit_ms = entry_ms + random.randint(500, 2000)
        r1 = _build_tlf_record(tx, config, "IDFE", "IDFE", "IDFE", file_id, entry_ms, exit_ms)
        all_records.append(r1)

    # Pack into rows of RECORDS_PER_ROW; header sub-records prepended to first row only
    lines: List[str] = []
    for i in range(0, len(all_records), RECORDS_PER_ROW):
        batch = "".join(all_records[i:i + RECORDS_PER_ROW])
        if i == 0:
            row_len = 6 + len(sub1) + len(sub2) + len(batch)
            lines.append(f"{row_len:06d}{sub1}{sub2}{batch}")
        else:
            row_len = 6 + len(batch)
            lines.append(f"{row_len:06d}{batch}")

    lines.append(_build_footer(len(all_records), tran_date, run_ts))

    content = "\n".join(lines)
    return content, filename
