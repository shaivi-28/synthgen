from datetime import datetime
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup

# ── Layout constants ───────────────────────────────────────────────────────────
_W  = 133   # standard line width
_W1 = 132   # header line 1 (no leading space, historical VISA format)

# VSS-110 column right-edges (1-indexed, inclusive)
_COL_COUNT  = 51
_COL_CREDIT = 77
_COL_DEBIT  = 103
_COL_TOTAL  = 129
_COL_SUFFIX = 130

# VSS-115 column right-edges
_115_CR_COUNT  = 38
_115_CR_AMT    = 57
_115_DB_COUNT  = 73
_115_DB_AMT    = 92
_115_TOT_COUNT = 108
_115_TOT_AMT   = 129
_115_SUFFIX    = 130

# VSS-100-W data column starts
_100W_NAME_COL = 42
_100W_FUNDS_COL = 58
_100W_CLR_COL  = 89
_100W_PROC_COL = 98
_100W_NET_COL  = 112

# VSS-120 column right-edges
_120_COUNT_RE  = 67
_120_CLR_RE    = 91
_120_CLR_SFX   = 92
_120_IC_CR_RE  = 115
_120_IC_DB_RE  = 130

# VSS-130 column right-edges
_130_COUNT_RE  = 59
_130_IC_RE     = 83
_130_IC_SFX    = 84
_130_FEE_CR_RE = 108
_130_FEE_DB_RE = 130

# VSS-300 column right-edges
_300_CNT_RE    = 40
_300_IC_RE     = 63
_300_IC_SFX    = 64
_300_REIMB_RE  = 83
_300_REIMB_SFX = 84
_300_VISA_RE   = 103
_300_NET_RE    = 127
_300_NET_SFX   = 128

# VSS-900 column right-edges
_900_CNT_RE    = 64
_900_CLR_RE    = 89
_900_CLR_SFX   = 90
_900_TCNT_RE   = 102
_900_TCLR_RE   = 127
_900_TCLR_SFX  = 128

# VSS-140 column right-edges
_140_CNT_RE    = 64
_140_IC_RE     = 84
_140_IC_SFX    = 85
_140_VC_CR_RE  = 110
_140_VC_DB_RE  = 130

# VSS-210 column right-edges
_210_SETL_IC_RE  = 44
_210_SETL_IC_SFX = 45
_210_SETL_FEE_RE = 60
_210_CLR_IC_RE   = 78
_210_CLR_IC_SFX  = 79
_210_CLR_FEE_RE  = 94
_210_FX_RE       = 107
_210_FX_SFX      = 108

# VSS-215 column right-edges
_215_SETL_IC_RE  = 44
_215_SETL_IC_SFX = 45
_215_ISA_RE      = 61
_215_ISA_SFX     = 62
_215_CLR_IC_RE   = 78
_215_CLR_IC_SFX  = 79
_215_CLR_ISA_RE  = 95
_215_CLR_ISA_SFX = 96
_215_OPT_RE      = 107

# Reimbursement fee rates
_REIMB_RATE_PURCHASE   = 0.00547
_REIMB_RATE_QUASI_CASH = 0.00004
_REIMB_RATE_ATM        = 0.00350
_REIMB_RATE_MERCH_CR   = 0.00640

# ISA / FX rates
_ISA_RATE = 0.01   # 1% of interchange
_FX_RATE  = 0.02   # 2% of interchange

# Quasi-cash MCCs per VISA rules
_QUASI_CASH_MCCS = {"4829", "6050", "6051", "6540", "7995", "7801"}


# ── Buffer helpers ─────────────────────────────────────────────────────────────

def _buf(w: int = _W) -> list:
    return [" "] * w


def _p(b: list, col: int, text: str) -> None:
    for i, c in enumerate(str(text)):
        p = col - 1 + i
        if 0 <= p < len(b):
            b[p] = c


def _rp(b: list, right_col: int, text: str) -> None:
    _p(b, right_col - len(str(text)) + 1, str(text))


def _j(b: list) -> str:
    return "".join(b)


# ── Amount / count formatters ──────────────────────────────────────────────────

def _fmt_amt(paise: int) -> str:
    return f"{paise / 100:,.2f}"


def _fmt_cnt(n: int) -> str:
    return f"{n:,}"


def _net(credit_p: int, debit_p: int) -> Tuple[str, str]:
    net = debit_p - credit_p
    if net > 0:
        return _fmt_amt(net), "DB"
    if net < 0:
        return _fmt_amt(-net), "CR"
    return "0.00", ""


# ── Stats collection ───────────────────────────────────────────────────────────

def _collect_for_bin(
    groups: List[ScenarioGroup],
    dom: bool,
    bin_prefix: str,
) -> dict:
    iss_count = iss_credit = iss_debit = 0
    dom_flag = "D" if dom else "I"
    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_category != dom_flag:
                continue
            if tx.tran_type == "OW":
                continue
            if not tx.card_pan.startswith(bin_prefix):
                continue
            iss_count += 1
            # TC 06 merchandise credit (tran_type="MC") is a credit even with msg_type="0210"
            if tx.msg_type == "0420" or tx.tran_type == "MC":
                iss_credit += tx.amount
            else:
                iss_debit += tx.amount
    return {"iss_count": iss_count, "iss_credit": iss_credit, "iss_debit": iss_debit}


def _collect_for_bucket(
    groups: List[ScenarioGroup],
    dom: bool,
    bin_prefixes: List[str],
) -> dict:
    iss_count = iss_credit = iss_debit = 0
    dom_flag = "D" if dom else "I"
    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_category != dom_flag:
                continue
            if tx.tran_type == "OW":
                continue
            if not any(tx.card_pan.startswith(bp) for bp in bin_prefixes):
                continue
            iss_count += 1
            if tx.msg_type == "0420" or tx.tran_type == "MC":
                iss_credit += tx.amount
            else:
                iss_debit += tx.amount
    return {"iss_count": iss_count, "iss_credit": iss_credit, "iss_debit": iss_debit}


# ── Single VSS-110 report generator ───────────────────────────────────────────

def _vss110_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    s: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, count="", credit="", debit="", total="", suffix=""):
        b = _buf()
        _p(b, 1, label)
        if count:
            _rp(b, _COL_COUNT, count)
        if credit:
            _rp(b, _COL_CREDIT, credit)
        if debit:
            _rp(b, _COL_DEBIT, debit)
        if total:
            _rp(b, _COL_TOTAL, total)
        if suffix:
            _p(b, _COL_SUFFIX, suffix[:2])
        return _j(b)

    def shdr(name):
        b = _buf()
        _p(b, 1, f" {name}")
        return _j(b)

    def ic_row(label, count, credit_p, debit_p, with_count=True):
        net, sfx = _net(credit_p, debit_p)
        return row(label,
                   count=_fmt_cnt(count) if with_count else "",
                   credit=_fmt_amt(credit_p),
                   debit=_fmt_amt(debit_p),
                   total=net, suffix=sfx)

    def z_row(label, with_count=True):
        return row(label,
                   count="0" if with_count else "",
                   credit="0.00", debit="0.00", total="0.00")

    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-110")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  reporting_id)
    _p(b, 33,  reporting_name)
    _p(b, 64,  service_name)
    _p(b, 112, "PROC DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 1,   " ROLLUP TO:")
    _p(b, 22,  rollup_id)
    _p(b, 33,  rollup_name)
    _p(b, 62,  "SETTLEMENT SUMMARY REPORT")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    b = _buf()
    _p(b, 1,  " FUNDS XFER ENTITY:")
    _p(b, 22, funds_id)
    _p(b, 33, funds_name)
    ln4 = _j(b)

    b = _buf()
    _p(b, 1, f" SETTLEMENT CURRENCY:  {setl_ccy}")
    ln_ccy = _j(b)

    b = _buf()
    _p(b, 72,  "CREDIT")
    _p(b, 99,  "DEBIT")
    _p(b, 125, "TOTAL")
    ch1 = _j(b)

    b = _buf()
    _p(b, 47,  "COUNT")
    _p(b, 72,  "AMOUNT")
    _p(b, 98,  "AMOUNT")
    _p(b, 124, "AMOUNT")
    ch2 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-110 REPORT ***")
    end_ln = _j(b)

    iss_c  = s["iss_count"]
    iss_cr = s["iss_credit"]
    iss_db = s["iss_debit"]

    return [
        ln1, ln2, ln3, ln4,
        BLK,
        ln_ccy,
        BLK,
        BLK,
        ch1,
        ch2,
        BLK,
        shdr("INTERCHANGE VALUE"),
        BLK,
        z_row("    TOTAL ACQUIRER"),
        ic_row("    TOTAL ISSUER",  iss_c,  iss_cr, iss_db),
        z_row("    TOTAL OTHER"),
        BLK,
        ic_row("    TOTAL INTERCHANGE VALUE", iss_c, iss_cr, iss_db),
        BLK,
        shdr("REIMBURSEMENT FEES"),
        BLK,
        z_row("    TOTAL ACQUIRER",           with_count=False),
        z_row("    TOTAL ISSUER",             with_count=False),
        z_row("    TOTAL OTHER",              with_count=False),
        BLK,
        z_row("    TOTAL REIMBURSEMENT FEES", with_count=False),
        BLK,
        shdr("VISA CHARGES"),
        BLK,
        z_row("    TOTAL ACQUIRER",  with_count=False),
        z_row("    TOTAL ISSUER",    with_count=False),
        z_row("    TOTAL OTHER",     with_count=False),
        BLK,
        z_row("    TOTAL VISA CHARGES", with_count=False),
        BLK,
        shdr("TOTAL"),
        BLK,
        z_row("    TOTAL ACQUIRER",  with_count=False),
        ic_row("    TOTAL ISSUER",   iss_c, iss_cr, iss_db, with_count=False),
        z_row("    TOTAL OTHER",     with_count=False),
        BLK,
        ic_row("    NET SETTLEMENT AMOUNT", iss_c, iss_cr, iss_db, with_count=False),
        BLK,
        BLK,
        end_ln,
    ]


# ── Multi-report VSS-110 builder ───────────────────────────────────────────────

def _vss110(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bin_prefix = entry["bin"]
        bucket     = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bin_prefix)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bin_prefix)

        s = _collect_for_bin(groups, dom, bin_prefix)
        _append(_vss110_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, s=s,
        ))

    if biz_bins:
        s = _collect_for_bucket(groups, dom, biz_bins)
        _append(_vss110_report(
            tran_date, page,
            reporting_id=biz_id, reporting_name=biz_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, s=s,
        ))

    if debit_bins:
        s = _collect_for_bucket(groups, dom, debit_bins)
        _append(_vss110_report(
            tran_date, page,
            reporting_id=debit_id, reporting_name=debit_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, s=s,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        s = _collect_for_bucket(groups, dom, all_bins)
        _append(_vss110_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, s=s,
        ))

    return all_lines


# ── Single VSS-115 report generator ───────────────────────────────────────────

def _vss115_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,      # from _collect_txn_types
    total_cr: int,  # paise — matches VSS-110 iss_credit
    total_db: int,  # paise — matches VSS-110 iss_debit
    has_data: bool,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    # ── Row helpers ───────────────────────────────────────────────────────────

    def _row(label, cr_cnt="", cr_amt="", db_cnt="", db_amt="",
             tot_cnt="", tot_amt="", sfx=""):
        b = _buf()
        _p(b, 1, label)
        if cr_cnt:
            _rp(b, _115_CR_COUNT, cr_cnt)
        if cr_amt:
            _rp(b, _115_CR_AMT, cr_amt)
        if db_cnt:
            _rp(b, _115_DB_COUNT, db_cnt)
        if db_amt:
            _rp(b, _115_DB_AMT, db_amt)
        if tot_cnt:
            _rp(b, _115_TOT_COUNT, tot_cnt)
        if tot_amt:
            _rp(b, _115_TOT_AMT, tot_amt)
        if sfx:
            _p(b, _115_SUFFIX, sfx[:2])
        return _j(b)

    def _zero_row(label, with_counts=False):
        return _row(label,
                    cr_amt="0.00", db_amt="0.00", tot_amt="0.00")

    def _net_only_row(label, cr_p, db_p):
        """FINAL SETTLEMENT NET AMOUNT — only total column."""
        tot_val, tot_sfx = _net(cr_p, db_p)
        return _row(label, tot_amt=tot_val, sfx=tot_sfx)

    # ── Header ────────────────────────────────────────────────────────────────
    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-115")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  reporting_id)
    _p(b, 33,  reporting_name)
    _p(b, 64,  service_name)
    _p(b, 112, "PROC DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 1,   " ROLLUP TO:")
    _p(b, 22,  rollup_id)
    _p(b, 33,  rollup_name)
    _p(b, 62,  "SRE SETTLEMENT RECAP REPORT")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    b = _buf()
    _p(b, 1,  " FUNDS XFER ENTITY:")
    _p(b, 22, funds_id)
    _p(b, 33, funds_name)
    ln4 = _j(b)

    b = _buf()
    _p(b, 1, f" SETTLEMENT CURRENCY:  {setl_ccy}")
    ln_ccy = _j(b)

    # Column header line 1 — dashes with section labels
    hdr1 = (
        " " * 23
        + "-" * 14 + "CREDITS" + "-" * 13
        + " "
        + "-" * 18 + "DEBITS" + "-" * 10
        + " "
        + "-" * 18 + "TOTAL" + "-" * 15
        + "  "
    )

    # Column header line 2 — COUNT / AMOUNT labels
    hdr2 = (
        " " * 33 + "COUNT"
        + " " * 13 + "AMOUNT"
        + " " * 11 + "COUNT"
        + " " * 13 + "AMOUNT"
        + " " * 11 + "COUNT"
        + " " * 15 + "AMOUNT"
        + "    "
    )

    b = _buf()
    _p(b, 52, "*** END OF VSS-115 REPORT ***")
    end_ln = _j(b)

    b = _buf()
    _p(b, 52, "*** NO DATA FOR THIS REPORT ***")
    no_data_ln = _j(b)

    # ── No-data case ─────────────────────────────────────────────────────────
    if not has_data:
        return [
            ln1, ln2, ln3, ln4,
            BLK,
            ln_ccy,
            BLK,
            hdr1,
            hdr2,
            BLK,
            BLK,
            no_data_ln,
            BLK,
            BLK,
            end_ln,
        ]

    # ── Data rows ─────────────────────────────────────────────────────────────
    pur_db  = txn["purchase"]["db"]
    qc_db   = txn["quasi_cash"]["db"]
    mc_cr   = txn["merch_cr"]["cr"]
    atm_db  = txn["atm_cash"]["db"]
    rev_cr  = txn["reversals"]["cr"]

    # Count transactions per type (each transaction = 1 entry)
    # We track amounts but not counts; estimate counts from amounts & groups
    # Use actual group counts where possible
    # Build issuer transaction rows (only show non-zero types)
    txn_rows = []
    if pur_db:
        txn_rows.append(_row(
            " PURCHASE",
            cr_amt="0.00",
            db_cnt=_fmt_cnt(txn["purchase"]["db_cnt"]),
            db_amt=_fmt_amt(pur_db),
            tot_cnt=_fmt_cnt(txn["purchase"]["db_cnt"]),
            tot_amt=_fmt_amt(pur_db),
            sfx="DB",
        ))
    if qc_db:
        txn_rows.append(_row(
            " QUASI-CASH",
            cr_amt="0.00",
            db_cnt=_fmt_cnt(txn["quasi_cash"]["db_cnt"]),
            db_amt=_fmt_amt(qc_db),
            tot_cnt=_fmt_cnt(txn["quasi_cash"]["db_cnt"]),
            tot_amt=_fmt_amt(qc_db),
            sfx="DB",
        ))
    if mc_cr:
        txn_rows.append(_row(
            " MERCHANDISE CREDIT",
            cr_cnt=_fmt_cnt(txn["merch_cr"]["cr_cnt"]),
            cr_amt=_fmt_amt(mc_cr),
            db_amt="0.00",
            tot_cnt=_fmt_cnt(txn["merch_cr"]["cr_cnt"]),
            tot_amt=_fmt_amt(mc_cr),
            sfx="CR",
        ))
    if atm_db:
        txn_rows.append(_row(
            " ATM CASH",
            cr_amt="0.00",
            db_cnt=_fmt_cnt(txn["atm_cash"]["db_cnt"]),
            db_amt=_fmt_amt(atm_db),
            tot_cnt=_fmt_cnt(txn["atm_cash"]["db_cnt"]),
            tot_amt=_fmt_amt(atm_db),
            sfx="DB",
        ))
    if rev_cr:
        txn_rows.append(_row(
            " REVERSALS",
            cr_cnt=_fmt_cnt(txn["reversals"]["cr_cnt"]),
            cr_amt=_fmt_amt(rev_cr),
            db_amt="0.00",
            tot_cnt=_fmt_cnt(txn["reversals"]["cr_cnt"]),
            tot_amt=_fmt_amt(rev_cr),
            sfx="CR",
        ))

    ic_cr_cnt = (txn["merch_cr"]["cr_cnt"] + txn["reversals"]["cr_cnt"])
    ic_db_cnt = (txn["purchase"]["db_cnt"] + txn["quasi_cash"]["db_cnt"]
                 + txn["atm_cash"]["db_cnt"])
    ic_tot_cnt = ic_cr_cnt + ic_db_cnt

    ic_cr_p = mc_cr + rev_cr
    ic_db_p = pur_db + qc_db + atm_db
    ic_tot_val, ic_tot_sfx = _net(ic_cr_p, ic_db_p)

    total_val, total_sfx = _net(total_cr, total_db)

    issuer_total_row = _row(
        " TOTAL ISSUER",
        cr_cnt=_fmt_cnt(ic_cr_cnt) if ic_cr_cnt else "",
        cr_amt=_fmt_amt(total_cr),
        db_cnt=_fmt_cnt(ic_db_cnt) if ic_db_cnt else "",
        db_amt=_fmt_amt(total_db),
        tot_cnt=_fmt_cnt(ic_tot_cnt) if ic_tot_cnt else "",
        tot_amt=total_val,
        sfx=total_sfx,
    )

    total_row = _row(
        " TOTAL",
        cr_cnt=_fmt_cnt(ic_cr_cnt) if ic_cr_cnt else "",
        cr_amt=_fmt_amt(total_cr),
        db_cnt=_fmt_cnt(ic_db_cnt) if ic_db_cnt else "",
        db_amt=_fmt_amt(total_db),
        tot_cnt=_fmt_cnt(ic_tot_cnt) if ic_tot_cnt else "",
        tot_amt=total_val,
        sfx=total_sfx,
    )

    lines = [
        ln1, ln2, ln3, ln4,
        BLK,
        ln_ccy,
        BLK,
        hdr1,
        hdr2,
        BLK,
        # ACQUIRER (zeros)
        " ACQUIRER TRANSACTIONS" + " " * (_W - len(" ACQUIRER TRANSACTIONS")),
        BLK,
        _zero_row(" TOTAL INTERCHANGE"),
        _zero_row(" REIMBURSEMENT FEES"),
        _zero_row(" VISA CHARGES"),
        BLK,
        _zero_row(" TOTAL ACQUIRER"),
        BLK,
        # ISSUER
        " ISSUER TRANSACTIONS" + " " * (_W - len(" ISSUER TRANSACTIONS")),
        BLK,
    ]
    lines.extend(txn_rows)
    lines.extend([
        BLK,
        _row(" TOTAL INTERCHANGE",
             cr_cnt=_fmt_cnt(ic_cr_cnt) if ic_cr_cnt else "",
             cr_amt=_fmt_amt(ic_cr_p),
             db_cnt=_fmt_cnt(ic_db_cnt) if ic_db_cnt else "",
             db_amt=_fmt_amt(ic_db_p),
             tot_cnt=_fmt_cnt(ic_tot_cnt) if ic_tot_cnt else "",
             tot_amt=ic_tot_val,
             sfx=ic_tot_sfx),
        _zero_row(" REIMBURSEMENT FEES"),
        _zero_row(" VISA CHARGES"),
        BLK,
        issuer_total_row,
        BLK,
        # OTHER (zeros)
        " OTHER TRANSACTIONS" + " " * (_W - len(" OTHER TRANSACTIONS")),
        BLK,
        _zero_row(" TOTAL INTERCHANGE"),
        _zero_row(" REIMBURSEMENT FEES"),
        _zero_row(" VISA CHARGES"),
        BLK,
        _zero_row(" TOTAL OTHER"),
        BLK,
        total_row,
        BLK,
        _net_only_row(" FINAL SETTLEMENT NET AMOUNT", total_cr, total_db),
        BLK,
        BLK,
        end_ln,
    ])
    return lines


# ── Transaction-type stats with counts ────────────────────────────────────────

def _collect_txn_types_with_counts(
    groups: List[ScenarioGroup],
    dom: bool,
    bin_prefixes: List[str],
) -> dict:
    """Collect per-type amounts AND counts for VSS-115 and VSS-120."""
    s = {k: {"cr": 0, "db": 0, "cr_cnt": 0, "db_cnt": 0} for k in
         ("purchase", "quasi_cash", "merch_cr", "atm_cash",
          "reversals",                            # VSS-115 aggregate (non-MC revs)
          "pur_rev", "qc_rev", "mc_rev", "atm_rev")}  # VSS-120 per-instrument
    dom_flag = "D" if dom else "I"

    for sg in groups:
        for tx in sg.nfs_rows:
            if tx.tran_category != dom_flag:
                continue
            if tx.tran_type == "OW":
                continue
            if bin_prefixes and not any(tx.card_pan.startswith(bp) for bp in bin_prefixes):
                continue

            is_atm = tx.mcc == "6011"
            is_qc  = tx.mcc in _QUASI_CASH_MCCS
            is_rev = tx.msg_type == "0420"
            is_mc  = tx.tran_type == "MC"

            if is_rev:
                if is_mc:
                    # TC 26: MC reversal — debit to issuer (reverses credit)
                    s["mc_rev"]["db"]     += tx.amount
                    s["mc_rev"]["db_cnt"] += 1
                elif is_atm:
                    # TC 27: ATM reversal — credit to issuer
                    s["atm_rev"]["cr"]     += tx.amount
                    s["atm_rev"]["cr_cnt"] += 1
                    s["reversals"]["cr"]     += tx.amount
                    s["reversals"]["cr_cnt"] += 1
                elif is_qc and not is_atm:
                    # TC 25 QC: quasi-cash reversal — credit to issuer
                    s["qc_rev"]["cr"]     += tx.amount
                    s["qc_rev"]["cr_cnt"] += 1
                    s["reversals"]["cr"]     += tx.amount
                    s["reversals"]["cr_cnt"] += 1
                else:
                    # TC 25: purchase reversal — credit to issuer
                    s["pur_rev"]["cr"]     += tx.amount
                    s["pur_rev"]["cr_cnt"] += 1
                    s["reversals"]["cr"]     += tx.amount
                    s["reversals"]["cr_cnt"] += 1
            elif is_mc:
                s["merch_cr"]["cr"]      += tx.amount
                s["merch_cr"]["cr_cnt"]  += 1
            elif is_qc and not is_atm:
                s["quasi_cash"]["db"]     += tx.amount
                s["quasi_cash"]["db_cnt"] += 1
            elif is_atm:
                s["atm_cash"]["db"]     += tx.amount
                s["atm_cash"]["db_cnt"] += 1
            else:
                s["purchase"]["db"]     += tx.amount
                s["purchase"]["db_cnt"] += 1

    return s


# ── Multi-report VSS-115 builder ───────────────────────────────────────────────

def _vss115(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn  = _collect_txn_types_with_counts(groups, dom, [bp])
        base = _collect_for_bin(groups, dom, bp)
        _append(_vss115_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy,
            txn=txn,
            total_cr=base["iss_credit"],
            total_db=base["iss_debit"],
            has_data=base["iss_count"] > 0,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn  = _collect_txn_types_with_counts(groups, dom, bins)
        base = _collect_for_bucket(groups, dom, bins)
        _append(_vss115_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy,
            txn=txn,
            total_cr=base["iss_credit"],
            total_db=base["iss_debit"],
            has_data=base["iss_count"] > 0,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn  = _collect_txn_types_with_counts(groups, dom, all_bins)
        base = _collect_for_bucket(groups, dom, all_bins)
        _append(_vss115_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy,
            txn=txn,
            total_cr=base["iss_credit"],
            total_db=base["iss_debit"],
            has_data=base["iss_count"] > 0,
        ))

    return all_lines


# ── VSS-100-W (hierarchy list, DOM only) ──────────────────────────────────────

def _100w_clr_rows(
    level: int,
    entity_id: str,
    entity_name: str,
    bin_prefix: str,
    funds_transfer: str = "",
) -> List[str]:
    """Build data rows for one leaf SRE entity (no children).

    Returns the entity row + one continuation row per extra clearing entity.
    Domestic BINs always have two clearing entries:
      • processor 3200320000  network ALL
      • processor 4011380000  network ALL
    """
    indent = " " * (level * 2 + 1)  # level 0→1sp, level 1→3sp, level 2→5sp

    def _data_row(sid, sname, clr, proc, net, ftrans=""):
        b = _buf()
        _p(b, 1, indent)
        if sid:
            _p(b, 1 + len(indent), sid)
        if sname:
            _p(b, _100W_NAME_COL, sname)
        if ftrans:
            _p(b, _100W_FUNDS_COL, ftrans)
        if clr:
            _p(b, _100W_CLR_COL, clr)
        if proc:
            _p(b, _100W_PROC_COL, proc)
        if net:
            _p(b, _100W_NET_COL, net)
        return _j(b)

    rows = []
    rows.append(_data_row(entity_id, entity_name, bin_prefix, "3200320000", "ALL",
                          ftrans=funds_transfer))
    rows.append(_data_row("", "", bin_prefix, "4011380000", "ALL"))
    return rows


def _100w_group_row(level: int, entity_id: str, entity_name: str,
                    funds_transfer: str = "") -> str:
    """One row for a group/parent SRE with no clearing entity IDs."""
    indent = " " * (level * 2 + 1)
    b = _buf()
    _p(b, 1, indent)
    _p(b, 1 + len(indent), entity_id)
    _p(b, _100W_NAME_COL, entity_name)
    if funds_transfer:
        _p(b, _100W_FUNDS_COL, funds_transfer)
    return _j(b)


def _vss100w_single(
    tran_date: datetime,
    page: int,
    entity_id: str,
    entity_name: str,
    bin_prefix: str,
) -> List[str]:
    """Individual VSS-100-W for a single leaf BIN entity."""
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-100-W")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  entity_id)
    _p(b, 33,  entity_name)
    _p(b, 68,  "INDIA AREA NET")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 54,  "WEEKLY SETTLEMENT REPORTING HIERARCHY LIST")
    _p(b, 112, "LAST CHANGE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    b = _buf()
    _p(b, 1, " SETTLEMENT CURRENCY:  INR")
    ln_ccy = _j(b)

    hdr_a = _buf()
    _p(hdr_a, 1,   " SETTLEMENT REPORTING ENTITY (SRE)")
    _p(hdr_a, 42,  "SRE NAME")
    _p(hdr_a, 58,  "FUNDS")
    _p(hdr_a, 106, "PROCESSOR")
    _p(hdr_a, 118, "NETWORK")
    _p(hdr_a, 128, "TXN")

    hdr_b = _buf()
    _p(hdr_b, 14,  "HIERARCHY")
    _p(hdr_b, 53,  "TRANSFER")
    _p(hdr_b, 81,  "CLEARING ENTITY ID")
    _p(hdr_b, 105, "ID")
    _p(hdr_b, 116, "ID")
    _p(hdr_b, 124, "CURR")

    b = _buf()
    _p(b, 52, "*** END OF VSS-100-W REPORT ***")
    end_ln = _j(b)

    clr_rows = _100w_clr_rows(0, entity_id, entity_name, bin_prefix)

    return [
        ln1, ln2, ln3,
        ln_ccy,
        BLK,
        BLK,
        _j(hdr_a),
        _j(hdr_b),
        BLK,
    ] + clr_rows + [
        BLK,
        BLK,
        end_ln,
    ]


def _vss100w_group(
    tran_date: datetime,
    page: int,
    group_id: str,
    group_name: str,
    children: list,   # list of (id, name, bin_prefix)
    parent_id: str = "",
    parent_name: str = "",
    funds_transfer: str = "",
) -> List[str]:
    """VSS-100-W for a group entity with leaf children."""
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-100-W")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  group_id)
    _p(b, 33,  group_name)
    _p(b, 68,  "INDIA AREA NET")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 54,  "WEEKLY SETTLEMENT REPORTING HIERARCHY LIST")
    _p(b, 112, "LAST CHANGE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    b = _buf()
    _p(b, 1, " SETTLEMENT CURRENCY:  INR")
    ln_ccy = _j(b)

    hdr_a = _buf()
    _p(hdr_a, 1,   " SETTLEMENT REPORTING ENTITY (SRE)")
    _p(hdr_a, 42,  "SRE NAME")
    _p(hdr_a, 58,  "FUNDS")
    _p(hdr_a, 106, "PROCESSOR")
    _p(hdr_a, 118, "NETWORK")
    _p(hdr_a, 128, "TXN")

    hdr_b = _buf()
    _p(hdr_b, 14,  "HIERARCHY")
    _p(hdr_b, 53,  "TRANSFER")
    _p(hdr_b, 81,  "CLEARING ENTITY ID")
    _p(hdr_b, 105, "ID")
    _p(hdr_b, 116, "ID")
    _p(hdr_b, 124, "CURR")

    b = _buf()
    _p(b, 52, "*** END OF VSS-100-W REPORT ***")
    end_ln = _j(b)

    body = [_100w_group_row(0, group_id, group_name, funds_transfer=funds_transfer)]
    for c_id, c_name, c_bin in children:
        body.append(BLK)
        body.extend(_100w_clr_rows(1, c_id, c_name, c_bin))

    return [
        ln1, ln2, ln3,
        ln_ccy,
        BLK,
        BLK,
        _j(hdr_a),
        _j(hdr_b),
        BLK,
    ] + body + [
        BLK,
        BLK,
        end_ln,
    ]


def _vss100w_root(
    tran_date: datetime,
    page: int,
    root_id: str,
    root_name: str,
    biz_id: str,
    biz_name: str,
    biz_children: list,
    debit_id: str,
    debit_name: str,
    debit_children: list,
) -> List[str]:
    """VSS-100-W for the root (funds_xfer_entity) with full two-level hierarchy."""
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-100-W")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  root_id)
    _p(b, 33,  root_name)
    _p(b, 68,  "INDIA AREA NET")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 54,  "WEEKLY SETTLEMENT REPORTING HIERARCHY LIST")
    _p(b, 112, "LAST CHANGE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    b = _buf()
    _p(b, 1, " SETTLEMENT CURRENCY:  INR")
    ln_ccy = _j(b)

    hdr_a = _buf()
    _p(hdr_a, 1,   " SETTLEMENT REPORTING ENTITY (SRE)")
    _p(hdr_a, 42,  "SRE NAME")
    _p(hdr_a, 58,  "FUNDS")
    _p(hdr_a, 106, "PROCESSOR")
    _p(hdr_a, 118, "NETWORK")
    _p(hdr_a, 128, "TXN")

    hdr_b = _buf()
    _p(hdr_b, 14,  "HIERARCHY")
    _p(hdr_b, 53,  "TRANSFER")
    _p(hdr_b, 81,  "CLEARING ENTITY ID")
    _p(hdr_b, 105, "ID")
    _p(hdr_b, 116, "ID")
    _p(hdr_b, 124, "CURR")

    b = _buf()
    _p(b, 52, "*** END OF VSS-100-W REPORT ***")
    end_ln = _j(b)

    body: List[str] = []
    body.append(_100w_group_row(0, root_id, root_name, funds_transfer="YES"))
    body.append(BLK)

    # BIZ group
    body.append(_100w_group_row(1, biz_id, biz_name))
    for c_id, c_name, c_bin in biz_children:
        body.append(BLK)
        body.extend(_100w_clr_rows(2, c_id, c_name, c_bin))
    body.append(BLK)

    # DEBIT group
    body.append(_100w_group_row(1, debit_id, debit_name))
    for c_id, c_name, c_bin in debit_children:
        body.append(BLK)
        body.extend(_100w_clr_rows(2, c_id, c_name, c_bin))

    return [
        ln1, ln2, ln3,
        ln_ccy,
        BLK,
        BLK,
        _j(hdr_a),
        _j(hdr_b),
        BLK,
    ] + body + [
        BLK,
        BLK,
        end_ln,
    ]


def _vss100w(
    tran_date: datetime,
    config: dict,
) -> List[str]:
    """Generate all VSS-100-W reports for the DOM file."""
    variant    = config["visa"]["domestic"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]

    biz_children:   list = []
    debit_children: list = []
    all_lines:      List[str] = []
    page = 1

    def _append(lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(lines)
        page += 1

    # Individual reports for each BIN entry
    for entry in variant["reporting_for"]:
        e_id   = entry["id"]
        e_name = entry["name"]
        e_bin  = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            biz_children.append((e_id, e_name, e_bin))
        else:
            debit_children.append((e_id, e_name, e_bin))
        _append(_vss100w_single(tran_date, page, e_id, e_name, e_bin))

    # Root hierarchy report (funds_xfer_entity)
    _append(_vss100w_root(
        tran_date, page,
        root_id=funds_id, root_name=funds_name,
        biz_id=biz_id, biz_name=biz_name, biz_children=biz_children,
        debit_id=debit_id, debit_name=debit_name, debit_children=debit_children,
    ))

    # Group reports
    _append(_vss100w_group(
        tran_date, page,
        group_id=biz_id, group_name=biz_name,
        children=biz_children,
    ))
    _append(_vss100w_group(
        tran_date, page,
        group_id=debit_id, group_name=debit_name,
        children=debit_children,
    ))

    return all_lines


# ── Reimbursement fee helper ──────────────────────────────────────────────────

def _reimb_fees(txn: dict) -> tuple:
    """Return (fee_credit_paise, fee_debit_paise) for the txn dict."""
    cr = int(txn["purchase"]["db"]   * _REIMB_RATE_PURCHASE
             + txn["quasi_cash"]["db"] * _REIMB_RATE_QUASI_CASH
             + txn["atm_cash"]["db"]   * _REIMB_RATE_ATM)
    db = int(txn["merch_cr"]["cr"]   * _REIMB_RATE_MERCH_CR)
    return cr, db


# ── Common header builder ─────────────────────────────────────────────────────

def _std_header(
    report_id: str,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    date_str: str,
    line3_text: str,
    line3_col: int,
    setl_ccy: str,
    clr_ccy: str = "",
    no_rollup: bool = False,
    no_funds: bool = False,
) -> List[str]:
    """Build the 4-line standard header (ln1..ln4) + optional ccy lines."""
    b = _buf(_W1)
    _p(b, 1,   f"REPORT ID:  {report_id}")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  reporting_id)
    _p(b, 33,  reporting_name)
    _p(b, 64,  service_name)
    _p(b, 112, "PROC DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    if no_rollup:
        b = _buf()
        _p(b, 64,  line3_text if line3_col == 64 else "")
        if line3_col != 64:
            _p(b, line3_col, line3_text)
        _p(b, 112, "REPORT DATE:")
        _p(b, 125, date_str)
        ln3 = _j(b)
    else:
        b = _buf()
        _p(b, 1,   " ROLLUP TO:")
        _p(b, 22,  rollup_id)
        _p(b, 33,  rollup_name)
        _p(b, line3_col, line3_text)
        _p(b, 112, "REPORT DATE:")
        _p(b, 125, date_str)
        ln3 = _j(b)

    if no_funds:
        ln4 = " " * _W
    else:
        b = _buf()
        _p(b, 1,  " FUNDS XFER ENTITY:")
        _p(b, 22, funds_id)
        _p(b, 33, funds_name)
        ln4 = _j(b)

    lines = [ln1, ln2, ln3, ln4]

    b = _buf()
    _p(b, 1, f" SETTLEMENT CURRENCY:  {setl_ccy}")
    lines.append(_j(b))

    if clr_ccy:
        b = _buf()
        _p(b, 1, f" CLEARING CURRENCY:    {clr_ccy}")
        lines.append(_j(b))

    return lines


# ── Single VSS-120 report generator ───────────────────────────────────────────

def _vss120_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
    dom: bool = True,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, count="", clr_amt="", clr_sfx="", ic_cr="", ic_db="", rate_id=""):
        b = _buf()
        _p(b, 1, label)
        if rate_id:
            _p(b, 43, rate_id)
        if count:
            _rp(b, _120_COUNT_RE, count)
        if clr_amt:
            _rp(b, _120_CLR_RE, clr_amt)
        if clr_sfx:
            _p(b, _120_CLR_SFX, clr_sfx[:2])
        if ic_cr:
            _rp(b, _120_IC_CR_RE, ic_cr)
        if ic_db:
            _rp(b, _120_IC_DB_RE, ic_db)
        return _j(b)

    hdr = _std_header(
        "VSS-120", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="INTERCHANGE VALUE REPORT",
        line3_col=62,
        setl_ccy=setl_ccy,
        clr_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 43, "RATE")
    _p(b, 62, "COUNT")
    _p(b, 79, "CLEARING")
    _p(b, 99, "INTERCHANGE")
    _p(b, 120, "INTERCHANGE")
    ch1 = _j(b)

    b = _buf()
    _p(b, 43, "TABLE")
    _p(b, 79, "AMOUNT")
    _p(b, 100, "VALUE")
    _p(b, 121, "VALUE")
    ch2 = _j(b)

    b = _buf()
    _p(b, 45, "ID")
    _p(b, 99, "CREDITS")
    _p(b, 120, "DEBITS")
    ch3 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-120 REPORT ***")
    end_ln = _j(b)

    pur_db     = txn["purchase"]["db"]
    pur_cnt    = txn["purchase"]["db_cnt"]
    qc_db      = txn["quasi_cash"]["db"]
    qc_cnt     = txn["quasi_cash"]["db_cnt"]
    mc_cr      = txn["merch_cr"]["cr"]
    mc_cnt     = txn["merch_cr"]["cr_cnt"]
    atm_db     = txn["atm_cash"]["db"]
    atm_cnt    = txn["atm_cash"]["db_cnt"]
    # per-instrument reversal buckets (VSS-120 uses these, not the aggregate)
    pur_rev_cr  = txn["pur_rev"]["cr"];  pur_rev_cnt  = txn["pur_rev"]["cr_cnt"]
    qc_rev_cr   = txn["qc_rev"]["cr"];   qc_rev_cnt   = txn["qc_rev"]["cr_cnt"]
    mc_rev_db   = txn["mc_rev"]["db"];   mc_rev_cnt   = txn["mc_rev"]["db_cnt"]
    atm_rev_cr  = txn["atm_rev"]["cr"];  atm_rev_cnt  = txn["atm_rev"]["cr_cnt"]

    # For INT, IC values are net after 2% FX fee deduction on ATM and POS.
    # MC and QC have no FX adjustment (no MINUS clause in INT SQL queries).
    if dom:
        ic_pur     = pur_db
        ic_pur_rev = pur_rev_cr
        ic_atm     = atm_db
        ic_atm_rev = atm_rev_cr
    else:
        ic_pur     = pur_db     - int(pur_db     * _FX_RATE)
        ic_pur_rev = pur_rev_cr - int(pur_rev_cr * _FX_RATE)
        ic_atm     = atm_db     - int(atm_db     * _FX_RATE)
        ic_atm_rev = atm_rev_cr - int(atm_rev_cr * _FX_RATE)

    # IC totals: debits = purchase fwds + QC fwds + ATM fwds + MC reversals
    #            credits = MC fwds + purchase revs + QC revs + ATM revs
    total_ic_db = ic_pur + qc_db + ic_atm + mc_rev_db
    total_ic_cr = mc_cr + ic_pur_rev + qc_rev_cr + ic_atm_rev
    total_cnt   = (pur_cnt + qc_cnt + mc_cnt + atm_cnt
                   + pur_rev_cnt + qc_rev_cnt + mc_rev_cnt + atm_rev_cnt)

    body: List[str] = []
    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)

    # PURCHASE block — forward + optional reversal sub-line
    pur_total_cnt = pur_cnt + pur_rev_cnt
    pur_net_clr, pur_net_sfx = _net(pur_rev_cr, pur_db)
    body.append(row("   PURCHASE"))
    body.append(row("    ORIGINAL SALE",
                    count=_fmt_cnt(pur_cnt),
                    clr_amt=_fmt_amt(pur_db), clr_sfx="DB",
                    ic_db=_fmt_amt(ic_pur)))
    if pur_rev_cr:
        body.append(BLK)
        body.append(row("    ORIGINAL SALE        RVRSL",
                        rate_id="A1277",
                        count=_fmt_cnt(pur_rev_cnt),
                        clr_amt=_fmt_amt(pur_rev_cr), clr_sfx="CR",
                        ic_cr=_fmt_amt(ic_pur_rev)))
        body.append(row("    TOTAL ORIGINAL SALE RVRSL",
                        count=_fmt_cnt(pur_rev_cnt),
                        clr_amt=_fmt_amt(pur_rev_cr), clr_sfx="CR",
                        ic_cr=_fmt_amt(ic_pur_rev)))
    body.append(BLK)
    body.append(row("   TOTAL PURCHASE",
                    count=_fmt_cnt(pur_total_cnt),
                    clr_amt=pur_net_clr, clr_sfx=pur_net_sfx,
                    ic_cr=_fmt_amt(ic_pur_rev) if ic_pur_rev else "",
                    ic_db=_fmt_amt(ic_pur) if ic_pur else ""))
    body.append(row("   NET   PURCHASE",
                    ic_db=_fmt_amt(ic_pur - ic_pur_rev) if ic_pur >= ic_pur_rev
                          else "",
                    ic_cr=_fmt_amt(ic_pur_rev - ic_pur) if ic_pur_rev > ic_pur
                          else ""))
    body.append(BLK)

    # QUASI-CASH block (only if forwards or reversals > 0)
    if qc_db or qc_rev_cr:
        qc_total_cnt = qc_cnt + qc_rev_cnt
        qc_net_clr, qc_net_sfx = _net(qc_rev_cr, qc_db)
        body.append(row("   QUASI-CASH"))
        if qc_db:
            body.append(row("    ORIGINAL SALE",
                            count=_fmt_cnt(qc_cnt),
                            clr_amt=_fmt_amt(qc_db), clr_sfx="DB",
                            ic_db=_fmt_amt(qc_db)))
        if qc_rev_cr:
            body.append(BLK)
            body.append(row("    ORIGINAL SALE        RVRSL",
                            rate_id="A1278",
                            count=_fmt_cnt(qc_rev_cnt),
                            clr_amt=_fmt_amt(qc_rev_cr), clr_sfx="CR",
                            ic_cr=_fmt_amt(qc_rev_cr)))
            body.append(row("    TOTAL ORIGINAL SALE RVRSL",
                            count=_fmt_cnt(qc_rev_cnt),
                            clr_amt=_fmt_amt(qc_rev_cr), clr_sfx="CR",
                            ic_cr=_fmt_amt(qc_rev_cr)))
        body.append(BLK)
        body.append(row("   TOTAL QUASI-CASH",
                        count=_fmt_cnt(qc_total_cnt),
                        clr_amt=qc_net_clr, clr_sfx=qc_net_sfx,
                        ic_cr=_fmt_amt(qc_rev_cr) if qc_rev_cr else "",
                        ic_db=_fmt_amt(qc_db) if qc_db else ""))
        body.append(row("   NET   QUASI-CASH",
                        ic_db=_fmt_amt(qc_db - qc_rev_cr) if qc_db >= qc_rev_cr
                              else "",
                        ic_cr=_fmt_amt(qc_rev_cr - qc_db) if qc_rev_cr > qc_db
                              else ""))
        body.append(BLK)

    # MERCHANDISE CREDIT block (only if forwards or reversals > 0)
    if mc_cr or mc_rev_db:
        mc_total_cnt = mc_cnt + mc_rev_cnt
        mc_net_clr, mc_net_sfx = _net(mc_cr, mc_rev_db)
        body.append(row("   MERCHANDISE CREDIT"))
        if mc_cr:
            body.append(row("    ORIGINAL",
                            count=_fmt_cnt(mc_cnt),
                            clr_amt=_fmt_amt(mc_cr), clr_sfx="CR",
                            ic_cr=_fmt_amt(mc_cr)))
        if mc_rev_db:
            body.append(BLK)
            body.append(row("    ORIGINAL             RVRSL",
                            count=_fmt_cnt(mc_rev_cnt),
                            clr_amt=_fmt_amt(mc_rev_db), clr_sfx="DB",
                            ic_db=_fmt_amt(mc_rev_db)))
        body.append(BLK)
        body.append(row("   TOTAL MERCHANDISE CREDIT",
                        count=_fmt_cnt(mc_total_cnt),
                        clr_amt=mc_net_clr, clr_sfx=mc_net_sfx,
                        ic_cr=_fmt_amt(mc_cr) if mc_cr else "",
                        ic_db=_fmt_amt(mc_rev_db) if mc_rev_db else ""))
        body.append(row("   NET   MERCHANDISE CREDIT",
                        ic_cr=_fmt_amt(mc_cr - mc_rev_db) if mc_cr >= mc_rev_db
                              else "",
                        ic_db=_fmt_amt(mc_rev_db - mc_cr) if mc_rev_db > mc_cr
                              else ""))
        body.append(BLK)

    # ATM CASH block (only if forwards or reversals > 0)
    if atm_db or atm_rev_cr:
        atm_total_cnt = atm_cnt + atm_rev_cnt
        atm_net_clr, atm_net_sfx = _net(atm_rev_cr, atm_db)
        body.append(row("   ATM CASH"))
        if atm_db:
            body.append(row("    ORIGINAL WITHDRAWAL",
                            count=_fmt_cnt(atm_cnt),
                            clr_amt=_fmt_amt(atm_db), clr_sfx="DB",
                            ic_db=_fmt_amt(ic_atm)))
        if atm_rev_cr:
            body.append(BLK)
            body.append(row("    ORIGINAL WITHDRAWAL  RVRSL",
                            rate_id="A1279",
                            count=_fmt_cnt(atm_rev_cnt),
                            clr_amt=_fmt_amt(atm_rev_cr), clr_sfx="CR",
                            ic_cr=_fmt_amt(ic_atm_rev)))
        body.append(BLK)
        body.append(row("   TOTAL ATM CASH",
                        count=_fmt_cnt(atm_total_cnt),
                        clr_amt=atm_net_clr, clr_sfx=atm_net_sfx,
                        ic_cr=_fmt_amt(ic_atm_rev) if ic_atm_rev else "",
                        ic_db=_fmt_amt(ic_atm) if ic_atm else ""))
        body.append(row("   NET   ATM CASH",
                        ic_db=_fmt_amt(ic_atm - ic_atm_rev) if ic_atm >= ic_atm_rev
                              else "",
                        ic_cr=_fmt_amt(ic_atm_rev - ic_atm) if ic_atm_rev > ic_atm
                              else ""))
        body.append(BLK)

    # TOTAL ISSUER INTERCHANGE
    tot_clr_val, tot_clr_sfx = _net(total_ic_cr, total_ic_db)
    body.append(row(" TOTAL ISSUER INTERCHANGE",
                    count=_fmt_cnt(total_cnt),
                    clr_amt=tot_clr_val, clr_sfx=tot_clr_sfx,
                    ic_cr=_fmt_amt(total_ic_cr) if total_ic_cr else "",
                    ic_db=_fmt_amt(total_ic_db) if total_ic_db else ""))

    # NET ISSUER INTERCHANGE — net = ic_db - ic_cr
    net_ic = total_ic_db - total_ic_cr
    if net_ic >= 0:
        body.append(row(" NET   ISSUER INTERCHANGE",
                        ic_db=_fmt_amt(net_ic)))
    else:
        body.append(row(" NET   ISSUER INTERCHANGE",
                        ic_cr=_fmt_amt(-net_ic)))

    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK, BLK, ch1, ch2, ch3, BLK] + body


# ── Multi-report VSS-120 builder ───────────────────────────────────────────────

def _vss120(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, dom, [bp])
        _append(_vss120_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn, dom=dom,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, dom, bins)
        _append(_vss120_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn, dom=dom,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, dom, all_bins)
        _append(_vss120_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn, dom=dom,
        ))

    return all_lines


# ── Single VSS-130 report generator ───────────────────────────────────────────

def _vss130_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, count="", ic_amt="", ic_sfx="", fee_cr="", fee_db=""):
        b = _buf()
        _p(b, 1, label)
        if count:
            _rp(b, _130_COUNT_RE, count)
        if ic_amt:
            _rp(b, _130_IC_RE, ic_amt)
        if ic_sfx:
            _p(b, _130_IC_SFX, ic_sfx[:2])
        if fee_cr:
            _rp(b, _130_FEE_CR_RE, fee_cr)
        if fee_db:
            _rp(b, _130_FEE_DB_RE, fee_db)
        return _j(b)

    hdr = _std_header(
        "VSS-130", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="REIMBURSEMENT FEES REPORT",
        line3_col=62,
        setl_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 53, "COUNT")
    _p(b, 69, "INTERCHANGE")
    _p(b, 95, "FEE")
    _p(b, 117, "FEE")
    ch1 = _j(b)

    b = _buf()
    _p(b, 70, "AMOUNT")
    _p(b, 95, "CREDITS")
    _p(b, 118, "DEBITS")
    ch2 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-130 REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    pur_cnt = txn["purchase"]["db_cnt"]
    qc_db   = txn["quasi_cash"]["db"]
    qc_cnt  = txn["quasi_cash"]["db_cnt"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]
    rev_cr  = txn["reversals"]["cr"]
    rev_cnt = txn["reversals"]["cr_cnt"]

    fee_cr, fee_db = _reimb_fees(txn)

    pur_fee  = int(pur_db  * _REIMB_RATE_PURCHASE)
    qc_fee   = int(qc_db   * _REIMB_RATE_QUASI_CASH)
    atm_fee  = int(atm_db  * _REIMB_RATE_ATM)
    mc_fee   = int(mc_cr   * _REIMB_RATE_MERCH_CR)

    body: List[str] = []
    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)

    # PURCHASE
    body.append(row("    PURCHASE"))
    body.append(row("       ORIGINAL SALE"))
    body.append(row("          VISA A.P."))
    body.append(row("             INDIA - INDIA"))
    body.append(row("                INTERCHANGE",
                    count=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB",
                    fee_cr=_fmt_amt(pur_fee)))
    body.append(row("             TOTAL INDIA - INDIA",
                    fee_cr=_fmt_amt(pur_fee)))
    body.append(row("       TOTAL PURCHASE",
                    count=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB",
                    fee_cr=_fmt_amt(pur_fee)))
    body.append(row("       NET   PURCHASE",
                    fee_cr=_fmt_amt(pur_fee)))
    body.append(BLK)

    # QUASI-CASH
    if qc_db:
        body.append(row("    QUASI-CASH"))
        body.append(row("       ORIGINAL SALE"))
        body.append(row("          VISA A.P."))
        body.append(row("             INDIA - INDIA"))
        body.append(row("                INTERCHANGE",
                        count=_fmt_cnt(qc_cnt),
                        ic_amt=_fmt_amt(qc_db), ic_sfx="DB",
                        fee_cr=_fmt_amt(qc_fee)))
        body.append(row("             TOTAL INDIA - INDIA",
                        fee_cr=_fmt_amt(qc_fee)))
        body.append(row("       TOTAL QUASI-CASH",
                        count=_fmt_cnt(qc_cnt),
                        ic_amt=_fmt_amt(qc_db), ic_sfx="DB",
                        fee_cr=_fmt_amt(qc_fee)))
        body.append(row("       NET   QUASI-CASH",
                        fee_cr=_fmt_amt(qc_fee)))
        body.append(BLK)

    # ATM CASH
    if atm_db:
        body.append(row("    ATM CASH"))
        body.append(row("       ORIGINAL WITHDRAWAL"))
        body.append(row("          VISA A.P."))
        body.append(row("             INDIA - INDIA"))
        body.append(row("                INTERCHANGE",
                        count=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB",
                        fee_cr=_fmt_amt(atm_fee)))
        body.append(row("             TOTAL INDIA - INDIA",
                        fee_cr=_fmt_amt(atm_fee)))
        body.append(row("       TOTAL ATM CASH",
                        count=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB",
                        fee_cr=_fmt_amt(atm_fee)))
        body.append(row("       NET   ATM CASH",
                        fee_cr=_fmt_amt(atm_fee)))
        body.append(BLK)

    # MERCHANDISE CREDIT
    if mc_cr:
        body.append(row("    MERCHANDISE CREDIT"))
        body.append(row("       ORIGINAL"))
        body.append(row("          VISA A.P."))
        body.append(row("             INDIA - INDIA"))
        body.append(row("                INTERCHANGE",
                        count=_fmt_cnt(mc_cnt),
                        ic_amt=_fmt_amt(mc_cr), ic_sfx="CR",
                        fee_db=_fmt_amt(mc_fee)))
        body.append(row("             TOTAL INDIA - INDIA",
                        fee_db=_fmt_amt(mc_fee)))
        body.append(row("       TOTAL MERCHANDISE CREDIT",
                        count=_fmt_cnt(mc_cnt),
                        ic_amt=_fmt_amt(mc_cr), ic_sfx="CR",
                        fee_db=_fmt_amt(mc_fee)))
        body.append(row("       NET   MERCHANDISE CREDIT",
                        fee_db=_fmt_amt(mc_fee)))
        body.append(BLK)

    # REVERSALS (reverse of purchase fee at same rate, as credit)
    if rev_cr:
        rev_fee = int(rev_cr * _REIMB_RATE_PURCHASE)
        body.append(row("    REVERSALS"))
        body.append(row("       TOTAL REVERSALS",
                        count=_fmt_cnt(rev_cnt),
                        ic_amt=_fmt_amt(rev_cr), ic_sfx="CR",
                        fee_cr=_fmt_amt(rev_fee)))
        body.append(row("       NET   REVERSALS",
                        fee_cr=_fmt_amt(rev_fee)))
        body.append(BLK)
        fee_cr += rev_fee

    # Totals
    body.append(row(" TOTAL ISSUER REIMB FEES",
                    fee_cr=_fmt_amt(fee_cr) if fee_cr else "",
                    fee_db=_fmt_amt(fee_db) if fee_db else ""))

    net_fee = fee_cr - fee_db
    if net_fee > 0:
        body.append(row(" NET   ISSUER REIMB FEES",
                        fee_cr=_fmt_amt(net_fee)))
    elif net_fee < 0:
        body.append(row(" NET   ISSUER REIMB FEES",
                        fee_db=_fmt_amt(-net_fee)))
    else:
        body.append(row(" NET   ISSUER REIMB FEES",
                        fee_cr="0.00"))

    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK, BLK, ch1, ch2, BLK] + body


# ── Multi-report VSS-130 builder ───────────────────────────────────────────────

def _vss130(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, dom, [bp])
        _append(_vss130_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, dom, bins)
        _append(_vss130_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, dom, all_bins)
        _append(_vss130_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── Single VSS-300 report generator ───────────────────────────────────────────

def _vss300(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])
    date_str   = tran_date.strftime("%d%b%y").upper()
    BLK        = " " * _W

    def row(label, cnt="", ic_val="", ic_sfx="", reimb="", reimb_sfx="",
            visa="", net_val="", net_sfx=""):
        b = _buf()
        _p(b, 1, label)
        if cnt:
            _rp(b, _300_CNT_RE, cnt)
        if ic_val:
            _rp(b, _300_IC_RE, ic_val)
        if ic_sfx:
            _p(b, _300_IC_SFX, ic_sfx[:2])
        if reimb:
            _rp(b, _300_REIMB_RE, reimb)
        if reimb_sfx:
            _p(b, _300_REIMB_SFX, reimb_sfx[:2])
        if visa:
            _rp(b, _300_VISA_RE, visa)
        if net_val:
            _rp(b, _300_NET_RE, net_val)
        if net_sfx:
            _p(b, _300_NET_SFX, net_sfx[:2])
        return _j(b)

    # Header (VSS-300 has no ROLLUP TO or FUNDS XFER ENTITY lines)
    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-300")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, "1")
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  funds_id)
    _p(b, 33,  funds_name)
    _p(b, 64,  service)
    _p(b, 112, "PROC DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 63,  "SRE FINANCIAL RECAP REPORT")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    ln4 = BLK

    b = _buf()
    _p(b, 1, f" SETTLEMENT CURRENCY: {setl_ccy}")
    ln_ccy = _j(b)

    # Column headers
    b = _buf()
    _p(b, 35, "TOTAL")
    _p(b, 54, "TOTAL")
    _p(b, 73, "TOTAL")
    _p(b, 90, "TOTAL")
    _p(b, 111, "NET")
    ch1 = _j(b)

    b = _buf()
    _p(b, 31, "INTERCHANGE")
    _p(b, 51, "INTERCHANGE")
    _p(b, 68, "REIMBURSEMENT")
    _p(b, 93, "VISA")
    _p(b, 106, "SETTLEMENT")
    ch2 = _j(b)

    b = _buf()
    _p(b, 35, "COUNT")
    _p(b, 54, "VALUE")
    _p(b, 74, "FEES")
    _p(b, 91, "CHARGES")
    _p(b, 107, "AMOUNT")
    ch3 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-300 REPORT ***")
    end_ln = _j(b)

    # Collect data per entity
    all_entries = list(variant["reporting_for"])
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]

    biz_bins:   List[str] = []
    debit_bins: List[str] = []

    for entry in all_entries:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            biz_bins.append(bp)
        else:
            debit_bins.append(bp)

    body: List[str] = []

    def _entity_rows(e_id, e_name, bins):
        if not bins:
            txn = {k: {"cr": 0, "db": 0, "cr_cnt": 0, "db_cnt": 0}
                   for k in ("purchase", "quasi_cash", "merch_cr", "atm_cash", "reversals")}
        else:
            txn = _collect_txn_types_with_counts(groups, dom, bins)
        f_cr, f_db = _reimb_fees(txn)
        ic_db = (txn["purchase"]["db"] + txn["quasi_cash"]["db"]
                 + txn["atm_cash"]["db"])
        ic_cr = (txn["merch_cr"]["cr"] + txn["reversals"]["cr"])
        total_cnt = (txn["purchase"]["db_cnt"] + txn["quasi_cash"]["db_cnt"]
                     + txn["merch_cr"]["cr_cnt"] + txn["atm_cash"]["db_cnt"]
                     + txn["reversals"]["cr_cnt"])
        ic_val_s, ic_sfx = _net(ic_cr, ic_db)
        reimb_s = _fmt_amt(f_cr) if f_cr else "0.00"
        reimb_sfx = "CR" if f_cr else ""
        net_p = ic_db - ic_cr - f_cr + f_db
        if net_p > 0:
            net_s, n_sfx = _fmt_amt(net_p), "DB"
        elif net_p < 0:
            net_s, n_sfx = _fmt_amt(-net_p), "CR"
        else:
            net_s, n_sfx = "0.00", ""
        rows: List[str] = []
        rows.append(row(f" RECAP FOR: {e_id} {e_name}"))
        rows.append(row(" ACQUIRER",
                        cnt="0", ic_val="0.00", reimb="0.00",
                        visa="0.00", net_val="0.00"))
        rows.append(row(" ISSUER",
                        cnt=_fmt_cnt(total_cnt),
                        ic_val=ic_val_s, ic_sfx=ic_sfx,
                        reimb=reimb_s, reimb_sfx=reimb_sfx,
                        visa="0.00",
                        net_val=net_s, net_sfx=n_sfx))
        rows.append(row(" OTHER",
                        cnt="0", ic_val="0.00", reimb="0.00",
                        visa="0.00", net_val="0.00"))
        rows.append(row(" NET SETTLEMENT AMOUNT",
                        cnt=_fmt_cnt(total_cnt),
                        ic_val=ic_val_s, ic_sfx=ic_sfx,
                        reimb=reimb_s, reimb_sfx=reimb_sfx,
                        visa="0.00",
                        net_val=net_s, net_sfx=n_sfx))
        rows.append(BLK)
        return rows

    # Per leaf entity
    for entry in all_entries:
        body.extend(_entity_rows(entry["id"], entry["name"], [entry["bin"]]))

    # Biz rollup
    if biz_bins:
        body.extend(_entity_rows(biz_id, biz_name, biz_bins))

    # Debit rollup
    if debit_bins:
        body.extend(_entity_rows(debit_id, debit_name, debit_bins))

    # TOTAL FOR funds entity
    all_bins = biz_bins + debit_bins
    all_txn = _collect_txn_types_with_counts(groups, dom, all_bins) if all_bins else \
        {k: {"cr": 0, "db": 0, "cr_cnt": 0, "db_cnt": 0}
         for k in ("purchase", "quasi_cash", "merch_cr", "atm_cash", "reversals")}
    tot_f_cr, tot_f_db = _reimb_fees(all_txn)
    tot_ic_db = (all_txn["purchase"]["db"] + all_txn["quasi_cash"]["db"]
                 + all_txn["atm_cash"]["db"])
    tot_ic_cr = (all_txn["merch_cr"]["cr"] + all_txn["reversals"]["cr"])
    tot_cnt   = (all_txn["purchase"]["db_cnt"] + all_txn["quasi_cash"]["db_cnt"]
                 + all_txn["merch_cr"]["cr_cnt"] + all_txn["atm_cash"]["db_cnt"]
                 + all_txn["reversals"]["cr_cnt"])
    tot_ic_s, tot_ic_sfx = _net(tot_ic_cr, tot_ic_db)
    tot_reimb_s = _fmt_amt(tot_f_cr) if tot_f_cr else "0.00"
    tot_reimb_sfx = "CR" if tot_f_cr else ""
    tot_net_p = tot_ic_db - tot_ic_cr - tot_f_cr + tot_f_db
    if tot_net_p > 0:
        tot_net_s, tot_net_sfx = _fmt_amt(tot_net_p), "DB"
    elif tot_net_p < 0:
        tot_net_s, tot_net_sfx = _fmt_amt(-tot_net_p), "CR"
    else:
        tot_net_s, tot_net_sfx = "0.00", ""

    body.append(row(f" TOTAL FOR: {funds_id} {funds_name}"))
    body.append(row(" ACQUIRER",
                    cnt="0", ic_val="0.00", reimb="0.00",
                    visa="0.00", net_val="0.00"))
    body.append(row(" ISSUER",
                    cnt=_fmt_cnt(tot_cnt),
                    ic_val=tot_ic_s, ic_sfx=tot_ic_sfx,
                    reimb=tot_reimb_s, reimb_sfx=tot_reimb_sfx,
                    visa="0.00",
                    net_val=tot_net_s, net_sfx=tot_net_sfx))
    body.append(row(" OTHER",
                    cnt="0", ic_val="0.00", reimb="0.00",
                    visa="0.00", net_val="0.00"))
    body.append(row(" NET SETTLEMENT AMOUNT",
                    cnt=_fmt_cnt(tot_cnt),
                    ic_val=tot_ic_s, ic_sfx=tot_ic_sfx,
                    reimb=tot_reimb_s, reimb_sfx=tot_reimb_sfx,
                    visa="0.00",
                    net_val=tot_net_s, net_sfx=tot_net_sfx))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return [ln1, ln2, ln3, ln4, BLK, ln_ccy, BLK, ch1, ch2, ch3, BLK] + body


# ── Single VSS-900 report generator ───────────────────────────────────────────

def _vss900_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, cnt="", clr_amt="", clr_sfx="", tcnt="", tclr_amt="", tclr_sfx=""):
        b = _buf()
        _p(b, 1, label)
        if cnt:
            _rp(b, _900_CNT_RE, cnt)
        if clr_amt:
            _rp(b, _900_CLR_RE, clr_amt)
        if clr_sfx:
            _p(b, _900_CLR_SFX, clr_sfx[:2])
        if tcnt:
            _rp(b, _900_TCNT_RE, tcnt)
        if tclr_amt:
            _rp(b, _900_TCLR_RE, tclr_amt)
        if tclr_sfx:
            _p(b, _900_TCLR_SFX, tclr_sfx[:2])
        return _j(b)

    hdr = _std_header(
        "VSS-900", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="RECONCILIATION REPORT",
        line3_col=62,
        setl_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 44, "CRS")
    _p(b, 57, "COUNT")
    _p(b, 73, "CLEARING")
    _p(b, 93, "TOTAL")
    _p(b, 113, "TOTAL")
    ch1 = _j(b)

    b = _buf()
    _p(b, 44, "DATE")
    _p(b, 74, "AMOUNT")
    _p(b, 94, "COUNT")
    _p(b, 113, "CLEARING")
    ch2 = _j(b)

    b = _buf()
    _p(b, 115, "AMOUNT")
    ch3 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-900 REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    pur_cnt = txn["purchase"]["db_cnt"]
    qc_db   = txn["quasi_cash"]["db"]
    qc_cnt  = txn["quasi_cash"]["db_cnt"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]
    rev_cr  = txn["reversals"]["cr"]
    rev_cnt = txn["reversals"]["cr_cnt"]

    fin_cnt = pur_cnt + qc_cnt + mc_cnt + atm_cnt + rev_cnt
    fin_db  = pur_db + qc_db + atm_db
    fin_cr  = mc_cr + rev_cr
    fin_val, fin_sfx = _net(fin_cr, fin_db)

    pos_auth_cnt = fin_cnt
    pos_rev_cnt  = rev_cnt
    non_fin_total = pos_auth_cnt + pos_rev_cnt

    body: List[str] = []

    # ── Financial section ─────────────────────────────────────────────────────
    b = _buf()
    _p(b, 1, f" CLEARING CURRENCY:  {setl_ccy}")
    body.append(_j(b))

    b = _buf()
    _p(b, 1, " BUSINESS MODE:      ISSUER TRANSACTIONS")
    body.append(_j(b))
    body.append(BLK)
    body.extend([ch1, ch2, ch3, BLK])

    # PURCHASE
    body.append(row(" PURCHASE"))
    body.append(row("     ORIGINAL SALE"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(pur_cnt),
                    clr_amt=_fmt_amt(pur_db), clr_sfx="DB"))
    body.append(row("       TOTAL SENT TO SETTLEMENT",
                    tcnt=_fmt_cnt(pur_cnt),
                    tclr_amt=_fmt_amt(pur_db), tclr_sfx="DB"))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(pur_cnt),
                    tclr_amt=_fmt_amt(pur_db), tclr_sfx="DB"))
    body.append(BLK)

    if qc_cnt:
        body.append(row(" QUASI-CASH"))
        body.append(row("     ORIGINAL SALE"))
        body.append(row("         RECEIVED FROM VISA",
                        cnt=_fmt_cnt(qc_cnt),
                        clr_amt=_fmt_amt(qc_db), clr_sfx="DB"))
        body.append(row("       TOTAL SENT TO SETTLEMENT",
                        tcnt=_fmt_cnt(qc_cnt),
                        tclr_amt=_fmt_amt(qc_db), tclr_sfx="DB"))
        body.append(BLK)

    if mc_cnt:
        body.append(row(" MERCHANDISE CREDIT"))
        body.append(row("     ORIGINAL"))
        body.append(row("         RECEIVED FROM VISA",
                        cnt=_fmt_cnt(mc_cnt),
                        clr_amt=_fmt_amt(mc_cr), clr_sfx="CR"))
        body.append(row("       TOTAL SENT TO SETTLEMENT",
                        tcnt=_fmt_cnt(mc_cnt),
                        tclr_amt=_fmt_amt(mc_cr), tclr_sfx="CR"))
        body.append(BLK)

    if atm_cnt:
        body.append(row(" ATM CASH"))
        body.append(row("     ORIGINAL WITHDRAWAL"))
        body.append(row("         RECEIVED FROM VISA",
                        cnt=_fmt_cnt(atm_cnt),
                        clr_amt=_fmt_amt(atm_db), clr_sfx="DB"))
        body.append(row("       TOTAL SENT TO SETTLEMENT",
                        tcnt=_fmt_cnt(atm_cnt),
                        tclr_amt=_fmt_amt(atm_db), tclr_sfx="DB"))
        body.append(BLK)

    if rev_cnt:
        body.append(row(" REVERSALS"))
        body.append(row("     REVERSAL"))
        body.append(row("         RECEIVED FROM VISA",
                        cnt=_fmt_cnt(rev_cnt),
                        clr_amt=_fmt_amt(rev_cr), clr_sfx="CR"))
        body.append(row("       TOTAL SENT TO SETTLEMENT",
                        tcnt=_fmt_cnt(rev_cnt),
                        tclr_amt=_fmt_amt(rev_cr), tclr_sfx="CR"))
        body.append(BLK)

    # TOTAL ISSUER TRANSACTIONS (financial)
    body.append(row(" TOTAL ISSUER TRANSACTIONS"))
    body.append(row("       FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(fin_cnt),
                    clr_amt=fin_val, clr_sfx=fin_sfx))
    body.append(row("       TOTAL SENT TO SETTLEMENT",
                    tcnt=_fmt_cnt(fin_cnt),
                    tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(fin_cnt),
                    tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(BLK)
    body.append(BLK)

    # ── Non-financial section ─────────────────────────────────────────────────
    body.append(row(" CLEARING CURRENCY:  NONE"))
    b = _buf()
    _p(b, 1, " BUSINESS MODE:      ISSUER TRANSACTIONS")
    body.append(_j(b))
    body.append(BLK)
    body.extend([ch1, ch2, ch3, BLK])

    body.append(row(" POS AUTHORIZATION"))
    body.append(row("       NON-FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(pos_auth_cnt)))
    body.append(row("       TOTAL NON-FINANCIAL",
                    tcnt=_fmt_cnt(pos_auth_cnt)))
    body.append(BLK)

    if pos_rev_cnt:
        body.append(row(" POS AUTH REVERSAL"))
        body.append(row("       NON-FINANCIAL TRANSACTIONS"))
        body.append(row("         RECEIVED FROM VISA",
                        cnt=_fmt_cnt(pos_rev_cnt)))
        body.append(row("       TOTAL NON-FINANCIAL",
                        tcnt=_fmt_cnt(pos_rev_cnt)))
        body.append(BLK)

    body.append(row(" TOTAL ISSUER TRANSACTIONS"))
    body.append(row("       FINANCIAL TRANSACTIONS"))
    body.append(row("       NON-FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(non_fin_total)))
    body.append(row("       TOTAL NON-FINANCIAL",
                    tcnt=_fmt_cnt(non_fin_total)))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(non_fin_total)))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK] + body


# ── Multi-report VSS-900 builder ───────────────────────────────────────────────

def _vss900(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, dom, [bp])
        _append(_vss900_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, dom, bins)
        _append(_vss900_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, dom, all_bins)
        _append(_vss900_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── Single VSS-900-S report generator ─────────────────────────────────────────

def _vss900s_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, cnt="", clr_amt="", clr_sfx="", tcnt="", tclr_amt="", tclr_sfx=""):
        b = _buf()
        _p(b, 1, label)
        if cnt:
            _rp(b, _900_CNT_RE, cnt)
        if clr_amt:
            _rp(b, _900_CLR_RE, clr_amt)
        if clr_sfx:
            _p(b, _900_CLR_SFX, clr_sfx[:2])
        if tcnt:
            _rp(b, _900_TCNT_RE, tcnt)
        if tclr_amt:
            _rp(b, _900_TCLR_RE, tclr_amt)
        if tclr_sfx:
            _p(b, _900_TCLR_SFX, tclr_sfx[:2])
        return _j(b)

    # Header — no ROLLUP TO, no FUNDS XFER ENTITY
    b = _buf(_W1)
    _p(b, 1,   "REPORT ID:  VSS-900-S")
    _p(b, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b, 111, "PAGE:")
    _rp(b, 130, str(page))
    ln1 = _j(b)

    b = _buf()
    _p(b, 1,   " REPORTING FOR:")
    _p(b, 22,  reporting_id)
    _p(b, 33,  reporting_name)
    _p(b, 64,  service_name)
    _p(b, 112, "PROC DATE:")
    _p(b, 125, date_str)
    ln2 = _j(b)

    b = _buf()
    _p(b, 58,  "SUMMARY RECONCILIATION REPORT")
    _p(b, 112, "REPORT DATE:")
    _p(b, 125, date_str)
    ln3 = _j(b)

    ln4 = BLK

    b = _buf()
    _p(b, 57, "COUNT")
    _p(b, 73, "CLEARING")
    _p(b, 93, "TOTAL")
    _p(b, 113, "TOTAL")
    ch1 = _j(b)

    b = _buf()
    _p(b, 74, "AMOUNT")
    _p(b, 94, "COUNT")
    _p(b, 113, "CLEARING")
    ch2 = _j(b)

    b = _buf()
    _p(b, 115, "AMOUNT")
    ch3 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-900-S REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    pur_cnt = txn["purchase"]["db_cnt"]
    qc_db   = txn["quasi_cash"]["db"]
    qc_cnt  = txn["quasi_cash"]["db_cnt"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]
    rev_cr  = txn["reversals"]["cr"]
    rev_cnt = txn["reversals"]["cr_cnt"]

    fin_cnt  = pur_cnt + qc_cnt + mc_cnt + atm_cnt + rev_cnt
    fin_db   = pur_db + qc_db + atm_db
    fin_cr   = mc_cr + rev_cr
    fin_val, fin_sfx = _net(fin_cr, fin_db)

    nonfin_cnt  = fin_cnt + rev_cnt  # pos auth + pos auth reversal
    total_cnt   = fin_cnt + nonfin_cnt

    body: List[str] = []

    # Section 1: CLEARING CURRENCY INR — TOTAL ISSUER TRANSACTIONS
    b = _buf(); _p(b, 1, f" CLEARING CURRENCY:  {setl_ccy}"); body.append(_j(b))
    body.append(BLK)
    body.extend([ch1, ch2, ch3, BLK])
    body.append(row(" TOTAL ISSUER TRANSACTIONS"))
    body.append(row("       FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(fin_cnt), clr_amt=fin_val, clr_sfx=fin_sfx))
    body.append(row("       TOTAL SENT TO SETTLEMENT",
                    tcnt=_fmt_cnt(fin_cnt), tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(fin_cnt), tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(BLK)

    # Section 2: TOTAL CLEARING CURRENCY INR
    b = _buf(); _p(b, 1, f" CLEARING CURRENCY:  {setl_ccy}"); body.append(_j(b))
    body.append(BLK)
    body.append(row(f" TOTAL CLEARING CURRENCY: {setl_ccy}"))
    body.append(row("       FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(fin_cnt), clr_amt=fin_val, clr_sfx=fin_sfx))
    body.append(row("       TOTAL SENT TO SETTLEMENT",
                    tcnt=_fmt_cnt(fin_cnt), tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(fin_cnt), tclr_amt=fin_val, tclr_sfx=fin_sfx))
    body.append(BLK)

    # Section 3: CLEARING CURRENCY NONE — TOTAL ISSUER TRANSACTIONS
    body.append(row(" CLEARING CURRENCY:  NONE"))
    body.append(BLK)
    body.extend([ch1, ch2, ch3, BLK])
    body.append(row(" TOTAL ISSUER TRANSACTIONS"))
    body.append(row("       NON-FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL NON-FINANCIAL",
                    tcnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(nonfin_cnt)))
    body.append(BLK)

    # Section 4: TOTAL CLEARING CURRENCY NONE
    body.append(row(" CLEARING CURRENCY:  NONE"))
    body.append(BLK)
    body.append(row(" TOTAL CLEARING CURRENCY: NONE"))
    body.append(row("       NON-FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL NON-FINANCIAL",
                    tcnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(nonfin_cnt)))
    body.append(BLK)

    # Section 5: CLEARING CURRENCY ALL — TOTAL entity_name
    body.append(row(" CLEARING CURRENCY:  ALL"))
    body.append(BLK)
    body.append(row(f" TOTAL {reporting_name}"))
    body.append(row("       FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(fin_cnt)))
    body.append(row("       TOTAL SENT TO SETTLEMENT",
                    tcnt=_fmt_cnt(fin_cnt)))
    body.append(row("       NON-FINANCIAL TRANSACTIONS"))
    body.append(row("         RECEIVED FROM VISA",
                    cnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL NON-FINANCIAL",
                    tcnt=_fmt_cnt(nonfin_cnt)))
    body.append(row("       TOTAL TRANSACTIONS",
                    tcnt=_fmt_cnt(total_cnt)))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return [ln1, ln2, ln3, ln4, BLK] + body


# ── Multi-report VSS-900-S builder ─────────────────────────────────────────────

def _vss900s(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
    dom: bool,
) -> List[str]:
    variant    = config["visa"]["domestic"] if dom else config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            biz_bins.append(bp)
        else:
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, dom, [bp])
        _append(_vss900s_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, dom, bins)
        _append(_vss900s_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, dom, all_bins)
        _append(_vss900s_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── Single VSS-140 report generator (INT only) ────────────────────────────────

def _vss140_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, cnt="", ic_amt="", ic_sfx="", vc_cr="", vc_db=""):
        b = _buf()
        _p(b, 1, label)
        if cnt:
            _rp(b, _140_CNT_RE, cnt)
        if ic_amt:
            _rp(b, _140_IC_RE, ic_amt)
        if ic_sfx:
            _p(b, _140_IC_SFX, ic_sfx[:2])
        if vc_cr:
            _rp(b, _140_VC_CR_RE, vc_cr)
        if vc_db:
            _rp(b, _140_VC_DB_RE, vc_db)
        return _j(b)

    hdr = _std_header(
        "VSS-140", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="VISA CHARGES REPORT",
        line3_col=63,
        setl_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 52, "*** END OF VSS-140 REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    pur_cnt = txn["purchase"]["db_cnt"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]

    isa_pur = int(pur_db * _ISA_RATE)
    isa_mc  = int(mc_cr  * _ISA_RATE)
    isa_atm = int(atm_db * _ISA_RATE)
    total_isa = isa_pur + isa_mc + isa_atm

    # Total IC for CURRENCY CONVERSION FEES section (debit net)
    ic_ccf_db = pur_db + atm_db
    ic_ccf_cnt = pur_cnt + atm_cnt

    # Total IC for ISA section
    ic_isa_db  = pur_db + atm_db
    ic_isa_cr  = mc_cr
    ic_isa_cnt = pur_cnt + mc_cnt + atm_cnt

    body: List[str] = []

    # ─── Page 1: CURRENCY CONVERSION FEES ───────────────────────────────────
    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)
    body.append(row("   CURRENCY CONVERSION FEES"))
    body.append(BLK)

    # PURCHASE
    body.append(row("     PURCHASE"))
    body.append(row("       ORIGINAL SALE"))
    body.append(row("         VISA INTERNATIONAL"))
    body.append(row("            INDIA - WORLD",
                    cnt=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB"))
    body.append(row("             TOTAL VISA INTERNATIONAL"))
    body.append(row("       TOTAL PURCHASE",
                    cnt=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB"))
    body.append(row("       NET   PURCHASE",
                    vc_cr="0.00"))
    body.append(BLK)

    if atm_cnt:
        body.append(row("     ATM CASH"))
        body.append(row("       ORIGINAL SALE"))
        body.append(row("         VISA INTERNATIONAL"))
        body.append(row("            INDIA - WORLD",
                        cnt=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB"))
        body.append(row("             TOTAL VISA INTERNATIONAL"))
        body.append(row("       TOTAL ATM CASH",
                        cnt=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB"))
        body.append(row("       NET   ATM CASH",
                        vc_cr="0.00"))
        body.append(BLK)

    ic_ccf_val, ic_ccf_sfx = _net(0, ic_ccf_db)
    body.append(row("    TOTAL CURRENCY CONVERSION FEES",
                    cnt=_fmt_cnt(ic_ccf_cnt),
                    ic_amt=ic_ccf_val, ic_sfx=ic_ccf_sfx))
    body.append(BLK)
    body.append(BLK)

    # ─── Page 2+: INTERNATIONAL SERVICE ASSESSMENT ──────────────────────────
    # new page via separate header line (REPORT ID triggers page break in _to_rtf)
    b2 = _buf(_W1)
    _p(b2, 1,   "REPORT ID:  VSS-140")
    _p(b2, 61,  "VISANET SETTLEMENT SERVICE")
    _p(b2, 111, "PAGE:")
    _rp(b2, 130, str(page + 1))
    p2_ln1 = _j(b2)

    body.append(p2_ln1)
    # reuse hdr lines 2-4 (same entity)
    body.extend(hdr[1:])
    body.append(BLK)

    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)
    body.append(row("     PURCHASE"))
    body.append(row("       ORIGINAL SALE"))
    body.append(row("         VISA INTERNATIONAL"))
    body.append(row("            INDIA - WORLD",
                    cnt=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB",
                    vc_db=_fmt_amt(isa_pur)))
    body.append(row("             TOTAL VISA INTERNATIONAL",
                    vc_db=_fmt_amt(isa_pur)))
    body.append(row("       TOTAL PURCHASE",
                    cnt=_fmt_cnt(pur_cnt),
                    ic_amt=_fmt_amt(pur_db), ic_sfx="DB",
                    vc_db=_fmt_amt(isa_pur)))
    body.append(row("       NET   PURCHASE",
                    vc_db=_fmt_amt(isa_pur)))
    body.append(BLK)

    if mc_cnt:
        body.append(row("     MERCHANDISE CREDIT"))
        body.append(row("       ORIGINAL"))
        body.append(row("         VISA INTERNATIONAL"))
        body.append(row("            INDIA - WORLD",
                        cnt=_fmt_cnt(mc_cnt),
                        ic_amt=_fmt_amt(mc_cr), ic_sfx="CR",
                        vc_db=_fmt_amt(isa_mc)))
        body.append(row("             TOTAL VISA INTERNATIONAL",
                        vc_db=_fmt_amt(isa_mc)))
        body.append(row("       TOTAL MERCHANDISE CREDIT",
                        cnt=_fmt_cnt(mc_cnt),
                        ic_amt=_fmt_amt(mc_cr), ic_sfx="CR",
                        vc_db=_fmt_amt(isa_mc)))
        body.append(row("       NET   MERCHANDISE CREDIT",
                        vc_db=_fmt_amt(isa_mc)))
        body.append(BLK)

    if atm_cnt:
        body.append(row("     ATM CASH"))
        body.append(row("       ORIGINAL WITHDRAWAL"))
        body.append(row("         VISA INTERNATIONAL"))
        body.append(row("            INDIA - WORLD",
                        cnt=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB",
                        vc_db=_fmt_amt(isa_atm)))
        body.append(row("             TOTAL VISA INTERNATIONAL",
                        vc_db=_fmt_amt(isa_atm)))
        body.append(row("       TOTAL ATM CASH",
                        cnt=_fmt_cnt(atm_cnt),
                        ic_amt=_fmt_amt(atm_db), ic_sfx="DB",
                        vc_db=_fmt_amt(isa_atm)))
        body.append(row("       NET   ATM CASH",
                        vc_db=_fmt_amt(isa_atm)))
        body.append(BLK)

    ic_isa_val, ic_isa_sfx = _net(ic_isa_cr, ic_isa_db)
    body.append(row("    TOTAL INTERNATIONAL SERVICE ASSESSMENT",
                    cnt=_fmt_cnt(ic_isa_cnt),
                    ic_amt=ic_isa_val, ic_sfx=ic_isa_sfx,
                    vc_db=_fmt_amt(total_isa)))
    body.append(row("    NET   INTERNATIONAL SERVICE ASSESSMENT",
                    vc_db=_fmt_amt(total_isa)))
    body.append(BLK)
    body.append(row(" TOTAL ISSUER CHARGES",
                    vc_db=_fmt_amt(total_isa)))
    body.append(row(" NET   ISSUER CHARGES",
                    vc_db=_fmt_amt(total_isa)))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK] + body


# ── Multi-report VSS-140 builder ───────────────────────────────────────────────

def _vss140(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
) -> List[str]:
    variant    = config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 2  # VSS-140 always takes 2 pages

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, False, [bp])
        _append(_vss140_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, False, bins)
        _append(_vss140_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, False, all_bins)
        _append(_vss140_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── Single VSS-210 report generator (INT only) ────────────────────────────────

def _vss210_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, s_ic="", s_ic_sfx="", s_fee="", c_ic="", c_ic_sfx="",
            c_fee="", fx="", fx_sfx=""):
        b = _buf()
        _p(b, 1, label)
        if s_ic:
            _rp(b, _210_SETL_IC_RE, s_ic)
        if s_ic_sfx:
            _p(b, _210_SETL_IC_SFX, s_ic_sfx[:2])
        if s_fee:
            _rp(b, _210_SETL_FEE_RE, s_fee)
        if c_ic:
            _rp(b, _210_CLR_IC_RE, c_ic)
        if c_ic_sfx:
            _p(b, _210_CLR_IC_SFX, c_ic_sfx[:2])
        if c_fee:
            _rp(b, _210_CLR_FEE_RE, c_fee)
        if fx:
            _rp(b, _210_FX_RE, fx)
        if fx_sfx:
            _p(b, _210_FX_SFX, fx_sfx[:2])
        return _j(b)

    hdr = _std_header(
        "VSS-210", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="CURRENCY CONVERSION FEES REPORT",
        line3_col=55,
        setl_ccy=setl_ccy,
        clr_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 12, "***********SETTLEMENT CURRENCY***********")
    _p(b, 55, "*********************CLEARING CURRENCY**********************")
    ch0 = _j(b)

    b = _buf()
    _p(b, 24, "INTERCHANGE")
    _p(b, 40, "CONVERSION")
    _p(b, 62, "INTERCHANGE")
    _p(b, 78, "CONVERSION")
    _p(b, 94, "ISS FX CALC")
    ch1 = _j(b)

    b = _buf()
    _p(b, 26, "AMOUNT")
    _p(b, 41, "FEE")
    _p(b, 64, "AMOUNT")
    _p(b, 80, "FEE")
    _p(b, 97, "AMT")
    ch2 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-210 REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]

    fx_pur  = int(pur_db * _FX_RATE)
    fx_mc   = int(mc_cr  * _FX_RATE)
    fx_atm  = int(atm_db * _FX_RATE)

    net_ic_db = pur_db + atm_db
    net_ic_cr = mc_cr
    net_ic_val, net_ic_sfx = _net(net_ic_cr, net_ic_db)
    total_fx = fx_pur + fx_mc + fx_atm

    body: List[str] = []
    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)

    # PURCHASE
    body.append(row(" PURCHASE"))
    body.append(row("  ORIGINAL SALE"))
    body.append(row("   VISA INTERNATIONAL",
                    s_ic=_fmt_amt(pur_db), s_ic_sfx="DB", s_fee="0.00",
                    c_ic=_fmt_amt(pur_db), c_ic_sfx="DB", c_fee="0.00",
                    fx=_fmt_amt(fx_pur), fx_sfx="DB"))
    body.append(BLK)
    body.append(row(" TOTAL PURCHASE"))
    body.append(row("",
                    s_ic=_fmt_amt(pur_db), s_ic_sfx="DB", s_fee="0.00",
                    c_ic=_fmt_amt(pur_db), c_ic_sfx="DB", c_fee="0.00",
                    fx=_fmt_amt(fx_pur), fx_sfx="DB"))
    body.append(BLK)

    if mc_cnt:
        body.append(row(" MERCHANDISE CREDIT"))
        body.append(row("  ORIGINAL"))
        body.append(row("   VISA INTERNATIONAL",
                        s_ic=_fmt_amt(mc_cr), s_ic_sfx="CR", s_fee="0.00",
                        c_ic=_fmt_amt(mc_cr), c_ic_sfx="CR", c_fee="0.00",
                        fx=_fmt_amt(fx_mc), fx_sfx="DB"))
        body.append(BLK)
        body.append(row(" TOTAL MERCHANDISE CREDIT"))
        body.append(row("",
                        s_ic=_fmt_amt(mc_cr), s_ic_sfx="CR", s_fee="0.00",
                        c_ic=_fmt_amt(mc_cr), c_ic_sfx="CR", c_fee="0.00",
                        fx=_fmt_amt(fx_mc), fx_sfx="DB"))
        body.append(BLK)

    if atm_cnt:
        body.append(row(" ATM CASH"))
        body.append(row("  ORIGINAL WITHDRAWAL"))
        body.append(row("   VISA INTERNATIONAL",
                        s_ic=_fmt_amt(atm_db), s_ic_sfx="DB", s_fee="0.00",
                        c_ic=_fmt_amt(atm_db), c_ic_sfx="DB", c_fee="0.00",
                        fx=_fmt_amt(fx_atm), fx_sfx="DB"))
        body.append(BLK)
        body.append(row(" TOTAL ATM CASH"))
        body.append(row("",
                        s_ic=_fmt_amt(atm_db), s_ic_sfx="DB", s_fee="0.00",
                        c_ic=_fmt_amt(atm_db), c_ic_sfx="DB", c_fee="0.00",
                        fx=_fmt_amt(fx_atm), fx_sfx="DB"))
        body.append(BLK)

    body.append(row(" TOTAL CURRENCY CONVERSION FEES"))
    body.append(row("",
                    s_ic=net_ic_val, s_ic_sfx=net_ic_sfx, s_fee="0.00",
                    c_ic=net_ic_val, c_ic_sfx=net_ic_sfx, c_fee="0.00",
                    fx=_fmt_amt(total_fx), fx_sfx="DB"))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK, ch0, ch1, ch2, BLK] + body


# ── Multi-report VSS-210 builder ───────────────────────────────────────────────

def _vss210(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
) -> List[str]:
    variant    = config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, False, [bp])
        _append(_vss210_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, False, bins)
        _append(_vss210_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, False, all_bins)
        _append(_vss210_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── Single VSS-215 report generator (INT only) ────────────────────────────────

def _vss215_report(
    tran_date: datetime,
    page: int,
    reporting_id: str,
    reporting_name: str,
    rollup_id: str,
    rollup_name: str,
    funds_id: str,
    funds_name: str,
    service_name: str,
    setl_ccy: str,
    txn: dict,
) -> List[str]:
    date_str = tran_date.strftime("%d%b%y").upper()
    BLK = " " * _W

    def row(label, s_ic="", s_ic_sfx="", s_isa="", s_isa_sfx="",
            c_ic="", c_ic_sfx="", c_isa="", c_isa_sfx="", opt=""):
        b = _buf()
        _p(b, 1, label)
        if s_ic:
            _rp(b, _215_SETL_IC_RE, s_ic)
        if s_ic_sfx:
            _p(b, _215_SETL_IC_SFX, s_ic_sfx[:2])
        if s_isa:
            _rp(b, _215_ISA_RE, s_isa)
        if s_isa_sfx:
            _p(b, _215_ISA_SFX, s_isa_sfx[:2])
        if c_ic:
            _rp(b, _215_CLR_IC_RE, c_ic)
        if c_ic_sfx:
            _p(b, _215_CLR_IC_SFX, c_ic_sfx[:2])
        if c_isa:
            _rp(b, _215_CLR_ISA_RE, c_isa)
        if c_isa_sfx:
            _p(b, _215_CLR_ISA_SFX, c_isa_sfx[:2])
        if opt:
            _rp(b, _215_OPT_RE, opt)
        return _j(b)

    hdr = _std_header(
        "VSS-215", page,
        reporting_id, reporting_name,
        rollup_id, rollup_name,
        funds_id, funds_name,
        service_name, date_str,
        line3_text="INTERNATIONAL SERVICE ASSESSMENT REPORT",
        line3_col=48,
        setl_ccy=setl_ccy,
        clr_ccy=setl_ccy,
    )

    b = _buf()
    _p(b, 12, "***********SETTLEMENT CURRENCY***********")
    _p(b, 55, "*********************CLEARING CURRENCY**********************")
    ch0 = _j(b)

    b = _buf()
    _p(b, 24, "INTERCHANGE")
    _p(b, 38, "INTL SERVICE")
    _p(b, 62, "INTERCHANGE")
    _p(b, 76, "INTL SERVICE")
    _p(b, 93, "OPT ISA")
    ch1 = _j(b)

    b = _buf()
    _p(b, 26, "AMOUNT")
    _p(b, 39, "ASSESSMENT")
    _p(b, 64, "AMOUNT")
    _p(b, 77, "ASSESSMENT")
    _p(b, 96, "FEE")
    ch2 = _j(b)

    b = _buf()
    _p(b, 52, "*** END OF VSS-215 REPORT ***")
    end_ln = _j(b)

    pur_db  = txn["purchase"]["db"]
    mc_cr   = txn["merch_cr"]["cr"]
    mc_cnt  = txn["merch_cr"]["cr_cnt"]
    atm_db  = txn["atm_cash"]["db"]
    atm_cnt = txn["atm_cash"]["db_cnt"]

    isa_pur = int(pur_db * _ISA_RATE)
    isa_mc  = int(mc_cr  * _ISA_RATE)
    isa_atm = int(atm_db * _ISA_RATE)
    total_isa = isa_pur + isa_mc + isa_atm

    net_ic_db = pur_db + atm_db
    net_ic_cr = mc_cr
    net_ic_val, net_ic_sfx = _net(net_ic_cr, net_ic_db)

    body: List[str] = []
    body.append(row(" ISSUER TRANSACTIONS"))
    body.append(BLK)

    # PURCHASE
    body.append(row(" PURCHASE"))
    body.append(row("  ORIGINAL SALE"))
    body.append(row("   VISA INTERNATIONAL",
                    s_ic=_fmt_amt(pur_db), s_ic_sfx="DB",
                    s_isa=_fmt_amt(isa_pur), s_isa_sfx="DB",
                    c_ic=_fmt_amt(pur_db), c_ic_sfx="DB",
                    c_isa=_fmt_amt(isa_pur), c_isa_sfx="DB",
                    opt="0.00"))
    body.append(BLK)
    body.append(row(" TOTAL PURCHASE"))
    body.append(row("",
                    s_ic=_fmt_amt(pur_db), s_ic_sfx="DB",
                    s_isa=_fmt_amt(isa_pur), s_isa_sfx="DB",
                    c_ic=_fmt_amt(pur_db), c_ic_sfx="DB",
                    c_isa=_fmt_amt(isa_pur), c_isa_sfx="DB",
                    opt="0.00"))
    body.append(BLK)

    if mc_cnt:
        body.append(row(" MERCHANDISE CREDIT"))
        body.append(row("  ORIGINAL"))
        body.append(row("   VISA INTERNATIONAL",
                        s_ic=_fmt_amt(mc_cr), s_ic_sfx="CR",
                        s_isa=_fmt_amt(isa_mc), s_isa_sfx="DB",
                        c_ic=_fmt_amt(mc_cr), c_ic_sfx="CR",
                        c_isa=_fmt_amt(isa_mc), c_isa_sfx="DB",
                        opt="0.00"))
        body.append(BLK)
        body.append(row(" TOTAL MERCHANDISE CREDIT"))
        body.append(row("",
                        s_ic=_fmt_amt(mc_cr), s_ic_sfx="CR",
                        s_isa=_fmt_amt(isa_mc), s_isa_sfx="DB",
                        c_ic=_fmt_amt(mc_cr), c_ic_sfx="CR",
                        c_isa=_fmt_amt(isa_mc), c_isa_sfx="DB",
                        opt="0.00"))
        body.append(BLK)

    if atm_cnt:
        body.append(row(" ATM CASH"))
        body.append(row("  ORIGINAL WITHDRAWAL"))
        body.append(row("   VISA INTERNATIONAL",
                        s_ic=_fmt_amt(atm_db), s_ic_sfx="DB",
                        s_isa=_fmt_amt(isa_atm), s_isa_sfx="DB",
                        c_ic=_fmt_amt(atm_db), c_ic_sfx="DB",
                        c_isa=_fmt_amt(isa_atm), c_isa_sfx="DB",
                        opt="0.00"))
        body.append(BLK)
        body.append(row(" TOTAL ATM CASH"))
        body.append(row("",
                        s_ic=_fmt_amt(atm_db), s_ic_sfx="DB",
                        s_isa=_fmt_amt(isa_atm), s_isa_sfx="DB",
                        c_ic=_fmt_amt(atm_db), c_ic_sfx="DB",
                        c_isa=_fmt_amt(isa_atm), c_isa_sfx="DB",
                        opt="0.00"))
        body.append(BLK)

    body.append(row(" TOTAL INTL SERVICE ASSESSMENT"))
    body.append(row("",
                    s_ic=net_ic_val, s_ic_sfx=net_ic_sfx,
                    s_isa=_fmt_amt(total_isa), s_isa_sfx="DB",
                    c_ic=net_ic_val, c_ic_sfx=net_ic_sfx,
                    c_isa=_fmt_amt(total_isa), c_isa_sfx="DB",
                    opt="0.00"))
    body.append(BLK)
    body.append(BLK)
    body.append(end_ln)

    return hdr + [BLK, ch0, ch1, ch2, BLK] + body


# ── Multi-report VSS-215 builder ───────────────────────────────────────────────

def _vss215(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
) -> List[str]:
    variant    = config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])

    biz_bins:   List[str] = []
    debit_bins: List[str] = []
    all_lines:  List[str] = []
    page = 1

    def _append(report_lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(report_lines)
        page += 1

    for entry in variant["reporting_for"]:
        bp     = entry["bin"]
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(bp)
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(bp)

        txn = _collect_txn_types_with_counts(groups, False, [bp])
        _append(_vss215_report(
            tran_date, page,
            reporting_id=entry["id"], reporting_name=entry["name"],
            rollup_id=r_id, rollup_name=r_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        txn = _collect_txn_types_with_counts(groups, False, bins)
        _append(_vss215_report(
            tran_date, page,
            reporting_id=grp_id, reporting_name=grp_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    all_bins = biz_bins + debit_bins
    if all_bins:
        txn = _collect_txn_types_with_counts(groups, False, all_bins)
        _append(_vss215_report(
            tran_date, page,
            reporting_id=funds_id, reporting_name=funds_name,
            rollup_id=funds_id, rollup_name=funds_name,
            funds_id=funds_id, funds_name=funds_name,
            service_name=service, setl_ccy=setl_ccy, txn=txn,
        ))

    return all_lines


# ── VSS-230: Visa Charges Reconciliation (INT only, always NO DATA) ────────────

def _vss230(
    tran_date: datetime,
    groups: List[ScenarioGroup],
    config: dict,
) -> List[str]:
    variant    = config["visa"]["international"]
    rollup     = variant["rollup_to"]
    biz_id     = rollup["biz_id"]
    biz_name   = rollup["biz_name"]
    debit_id   = rollup["debit_id"]
    debit_name = rollup["debit_name"]
    funds_id   = variant["funds_xfer_entity"]
    funds_name = variant["funds_xfer_name"]
    service    = variant["service_name"]
    setl_ccy   = str(variant["settlement_currency"])
    date_str   = tran_date.strftime("%d%b%y").upper()
    BLK        = " " * _W

    def _one_report(page, rep_id, rep_name, roll_id, roll_name):
        b = _buf(_W1)
        _p(b, 1,   "REPORT ID:  VSS-230")
        _p(b, 61,  "VISANET SETTLEMENT SERVICE")
        _p(b, 111, "PAGE:")
        _rp(b, 130, str(page))
        ln1 = _j(b)

        b = _buf()
        _p(b, 1,   " REPORTING FOR:")
        _p(b, 22,  rep_id)
        _p(b, 33,  rep_name)
        _p(b, 64,  service)
        _p(b, 112, "PROC DATE:")
        _p(b, 125, date_str)
        ln2 = _j(b)

        b = _buf()
        _p(b, 1,   " ROLLUP TO:")
        _p(b, 22,  roll_id)
        _p(b, 33,  roll_name)
        _p(b, 58,  "VISA CHARGES RECONCILIATION REPORT")
        _p(b, 112, "REPORT DATE:")
        _p(b, 125, date_str)
        ln3 = _j(b)

        b = _buf()
        _p(b, 1,  " FUNDS XFER ENTITY:")
        _p(b, 22, funds_id)
        _p(b, 33, funds_name)
        ln4 = _j(b)

        b = _buf()
        _p(b, 1, f" SETTLEMENT CURRENCY:  {setl_ccy}")
        ln_ccy = _j(b)

        b = _buf()
        _p(b, 44, "*** NO DATA FOR THIS REPORT ***")
        no_data = _j(b)

        b = _buf()
        _p(b, 52, "*** END OF VSS-230 REPORT ***")
        end_ln = _j(b)

        return [ln1, ln2, ln3, ln4, BLK, ln_ccy, BLK, BLK, no_data, BLK, BLK, end_ln]

    all_lines: List[str] = []
    page = 1

    def _append(lines):
        nonlocal page
        if all_lines:
            all_lines.append("")
        all_lines.extend(lines)
        page += 1

    biz_bins:   List[str] = []
    debit_bins: List[str] = []

    for entry in variant["reporting_for"]:
        bucket = entry.get("bucket", "debit")
        if bucket == "biz":
            r_id, r_name = biz_id, biz_name
            biz_bins.append(entry["bin"])
        else:
            r_id, r_name = debit_id, debit_name
            debit_bins.append(entry["bin"])
        _append(_one_report(page, entry["id"], entry["name"], r_id, r_name))

    for grp_id, grp_name, bins in [
        (biz_id,   biz_name,   biz_bins),
        (debit_id, debit_name, debit_bins),
    ]:
        if not bins:
            continue
        _append(_one_report(page, grp_id, grp_name, funds_id, funds_name))

    if biz_bins or debit_bins:
        _append(_one_report(page, funds_id, funds_name, funds_id, funds_name))

    return all_lines


# ── RTF wrapper ────────────────────────────────────────────────────────────────

def _to_txt(all_lines: List[str]) -> str:
    parts: List[str] = []
    first_page = True

    for line in all_lines:
        if not line:
            parts.append("")
            continue
        stripped = line.lstrip()
        if stripped.startswith("REPORT ID:"):
            if first_page:
                first_page = False
            else:
                # Form feed before each new report page
                parts.append("\x0c")
        parts.append(line)

    return "\n".join(parts)


# ── Public entry point ─────────────────────────────────────────────────────────

def build_ep747_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    dom: bool,
) -> Tuple[str, str]:
    date_ddmmyy = tran_date.strftime("%d%m%y")
    filename = (f"EP747_DOM_{date_ddmmyy}.txt" if dom
                else f"EP747_INT_{date_ddmmyy}.txt")

    all_lines: List[str] = []

    def _ext(lines):
        if all_lines:
            all_lines.append("")
        all_lines.extend(lines)

    if dom:
        _ext(_vss100w(tran_date, config))

    _ext(_vss110(tran_date, groups, config, dom))
    _ext(_vss115(tran_date, groups, config, dom))
    _ext(_vss120(tran_date, groups, config, dom))
    _ext(_vss130(tran_date, groups, config, dom))

    if not dom:
        _ext(_vss140(tran_date, groups, config))
        _ext(_vss210(tran_date, groups, config))
        _ext(_vss215(tran_date, groups, config))
        _ext(_vss230(tran_date, groups, config))

    _ext(_vss300(tran_date, groups, config, dom))
    _ext(_vss900(tran_date, groups, config, dom))
    _ext(_vss900s(tran_date, groups, config, dom))

    content = _to_txt(all_lines)
    return content, filename
