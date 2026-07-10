"""
VISA VSS-110 Settlement Report Generator
Generates a fixed-width text report mimicking the VISA VSS-110 Settlement Summary.

Format derived from real VSS-110 reports sent by VisaNet to member banks.
Structure:
  - Header block (report ID, reporting entity, dates)
  - Interchange Value section (TOTAL ISSUER debit/credit)
  - Reimbursement Fees (interchange earned/paid by issuer)
  - VISA Processing Charges
  - Net Settlement Amount

Fee Rates (typical issuer interchange):
  Reimbursement earned (issuer):  1.80% of purchase volume
  Reimbursement paid (issuer):    ~0.10% of purchase volume (assessment fees)
  VISA Processing Charge:         0.10% of volume (VisaNet access fee)
"""

from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────
# FEE RATES
# ─────────────────────────────────────────────────────────────

FEE_RATES = {
    "reimb_earned_pct":   0.0180,   # 1.80% — issuer earns on cardholder purchases
    "reimb_paid_pct":     0.0010,   # 0.10% — issuer pays (assessment / network fees)
    "visa_charge_pct":    0.0010,   # 0.10% — VISA processing charge
}


# ─────────────────────────────────────────────────────────────
# STATS AGGREGATOR
# ─────────────────────────────────────────────────────────────

@dataclass
class VisaSettlementStats:
    """Aggregated stats built from the VISA matrix manifest."""
    bank_name: str = "TEST BANK LTD"
    settlement_date: Optional[datetime] = None
    run_id: str = ""

    # POS (TC=05) approved purchases
    pos_count:   int   = 0
    pos_amount:  float = 0.0   # INR

    # Cash (TC=07) approved disbursements
    cash_count:  int   = 0
    cash_amount: float = 0.0   # INR

    # POS reversals (TC=25)
    pos_rev_count:  int   = 0
    pos_rev_amount: float = 0.0

    # Cash reversals (TC=27)
    cash_rev_count:  int   = 0
    cash_rev_amount: float = 0.0

    # Declined (no CBS entry — informational only)
    declined_count: int = 0


def stats_from_manifest(manifest: dict, bank_name: str) -> VisaSettlementStats:
    """
    Build VisaSettlementStats from the VISA matrix manifest.

    Counts:
      tc "05" rows (from visa_tc file) → POS purchases
      tc "07" rows → Cash disbursements
      tc "25" rows → POS reversals
      tc "27" rows → Cash reversals
    """
    stats = VisaSettlementStats(bank_name=bank_name)
    stats.run_id = manifest.get("run_id", "")

    date_str = manifest.get("tran_date", "")
    if date_str:
        try:
            stats.settlement_date = datetime.strptime(date_str, "%d%m%Y")
        except ValueError:
            stats.settlement_date = datetime.today()

    for row in manifest.get("rows", []):
        file_id = row.get("file", "")
        if file_id not in ("visa_tc",):
            # Only count VISA TC file rows for settlement
            # Switch TLF and CBS are not settlement-source rows
            # For matrix runs the file key may be "nfs" (the slot name)
            if file_id not in ("nfs",):
                continue

        # tc is stored in row["tc"] for visa_tc rows; for matrix rows use
        # nfs_value to infer direction (1=forward, -1=reversal)
        tc = row.get("tc", "")
        # visa_value (VISA matrix runs) or nfs_value (legacy) for structural direction
        visa_val = row.get("visa_value", row.get("nfs_value"))

        # Normalise amount to INR
        if "amount_inr" in row:
            amount_inr = float(row["amount_inr"])
        else:
            amount_inr = float(row.get("amount", 0)) / 100

        if tc == "05" or (not tc and visa_val == 1):
            stats.pos_count += 1
            stats.pos_amount += amount_inr
        elif tc == "07":
            stats.cash_count += 1
            stats.cash_amount += amount_inr
        elif tc == "25" or (not tc and visa_val == -1):
            stats.pos_rev_count += 1
            stats.pos_rev_amount += amount_inr
        elif tc == "27":
            stats.cash_rev_count += 1
            stats.cash_rev_amount += amount_inr

    return stats


# ─────────────────────────────────────────────────────────────
# AMOUNT FORMATTERS
# ─────────────────────────────────────────────────────────────

def _fmt_inr(amount: float) -> str:
    """
    Format amount as Indian currency string with Indian grouping.
    e.g. 1234567.89 -> "12,34,567.89"
    Handles negative amounts.
    """
    negative = amount < 0
    amount = abs(amount)
    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    # Indian grouping: last 3 digits, then groups of 2
    s = str(rupees)
    if len(s) <= 3:
        grouped = s
    else:
        # Last 3 digits
        last3 = s[-3:]
        rest = s[:-3]
        # Groups of 2 from right
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        parts.insert(0, rest)
        grouped = ",".join(parts) + "," + last3

    result = f"{grouped}.{paise:02d}"
    if negative:
        result = "-" + result
    return result


def _col(s: str, width: int, align: str = "right") -> str:
    """Pad string to fixed column width."""
    s = str(s)[:width]
    if align == "right":
        return s.rjust(width)
    elif align == "left":
        return s.ljust(width)
    else:
        return s.center(width)


# ─────────────────────────────────────────────────────────────
# REPORT WRITER
# ─────────────────────────────────────────────────────────────

_LINE_WIDTH = 132  # VSS-110 standard line width


def _hr(char: str = "-") -> str:
    # Use ASCII-safe characters only
    safe = {"═": "=", "─": "-"}.get(char, char)
    return " " + safe * (_LINE_WIDTH - 2)


def _blank() -> str:
    return ""


def _hdr_line(left: str, center: str, right: str) -> str:
    """Build a header line with left/center/right segments."""
    l_width = 40
    c_width = 50
    r_width = _LINE_WIDTH - l_width - c_width - 3
    return " " + _col(left, l_width, "left") + " " + _col(center, c_width, "center") + " " + _col(right, r_width, "right")


def _data_line(
    label: str,
    count: int,
    debit: float,
    credit: float,
    label_width: int = 40,
    is_total: bool = False,
) -> str:
    """Build a data row with label | count | debit | credit columns."""
    prefix = "   " if not is_total else "   "
    cnt_col   = _col(str(count) if count else "", 10, "right")
    debit_col = _col(_fmt_inr(debit)  if debit  else "", 20, "right")
    cred_col  = _col(_fmt_inr(credit) if credit else "", 20, "right")
    lbl       = _col(label, label_width, "left")
    return prefix + lbl + "  " + cnt_col + "  " + debit_col + "  " + cred_col


def _section_header(title: str) -> str:
    return " " + title.upper()


def write_vss110_report(stats: VisaSettlementStats, out_path: Path) -> Path:
    """Write the VSS-110 VISA Settlement Summary text report."""

    date_str = stats.settlement_date.strftime("%d%b%y").upper() \
               if stats.settlement_date else datetime.today().strftime("%d%b%y").upper()
    report_date = date_str

    # ── Compute amounts ───────────────────────────────────────
    total_fwd_count  = stats.pos_count + stats.cash_count
    total_fwd_amount = stats.pos_amount + stats.cash_amount

    total_rev_count  = stats.pos_rev_count + stats.cash_rev_count
    total_rev_amount = stats.pos_rev_amount + stats.cash_rev_amount

    # Interchange debit = what issuer owes VISA (purchases processed)
    interchange_debit = round(total_fwd_amount, 2)

    # Interchange credit = what VISA owes issuer (reversals)
    interchange_credit = round(total_rev_amount, 2)

    # Reimbursement (earned by issuer on purchases)
    reimb_earned = round(interchange_debit * FEE_RATES["reimb_earned_pct"], 2)

    # Reimbursement (paid by issuer — assessments)
    reimb_paid = round(interchange_debit * FEE_RATES["reimb_paid_pct"], 2)

    # VISA processing charge
    visa_charge = round(interchange_debit * FEE_RATES["visa_charge_pct"], 2)

    # Net settlement:
    # Issuer receives: interchange_credit + reimb_earned
    # Issuer pays:     interchange_debit + reimb_paid + visa_charge
    net_to_issuer   = round(interchange_credit + reimb_earned, 2)
    net_from_issuer = round(interchange_debit  + reimb_paid + visa_charge, 2)
    net_settlement  = round(net_to_issuer - net_from_issuer, 2)

    lines = []

    # ── PAGE HEADER ──────────────────────────────────────────
    lines.append(_blank())
    lines.append(_hdr_line(
        " REPORT ID:  VSS-110",
        "VISANET SETTLEMENT SERVICE",
        f"PAGE:              1  "
    ))
    lines.append(_hdr_line(
        f" REPORTING FOR:      1000565643 {stats.bank_name[:20]}",
        "INTERNATIONAL SETTLEMENT SERVICE",
        f"PROC DATE:   {report_date}  "
    ))
    lines.append(_hdr_line(
        f" ROLLUP TO:          9000375024 {stats.bank_name[:20]}",
        "SETTLEMENT SUMMARY REPORT",
        f"REPORT DATE: {report_date}  "
    ))
    lines.append(_hdr_line(
        f" FUNDS XFER ENTITY:  9000375016 {stats.bank_name[:15]}",
        "",
        ""
    ))
    lines.append(_blank())
    lines.append(_hr("="))
    lines.append(_blank())

    # ── COLUMN HEADERS ────────────────────────────────────────
    lines.append(
        "   " + _col("DESCRIPTION", 40, "left") +
        "  " + _col("COUNT", 10, "right") +
        "  " + _col("DEBIT (INR)", 20, "right") +
        "  " + _col("CREDIT (INR)", 20, "right")
    )
    lines.append(_hr())
    lines.append(_blank())

    # ── INTERCHANGE VALUE SECTION ─────────────────────────────
    lines.append(_section_header(" INTERCHANGE VALUE"))
    lines.append(_blank())

    lines.append(_data_line(
        "TOTAL ACQUIRER",
        0, 0.0, 0.0
    ))
    lines.append(_blank())

    lines.append(_data_line(
        "ISSUER - POS PURCHASES (TC05)",
        stats.pos_count, stats.pos_amount, 0.0
    ))
    lines.append(_data_line(
        "ISSUER - CASH DISBURSEMENTS (TC07)",
        stats.cash_count, stats.cash_amount, 0.0
    ))
    lines.append(_data_line(
        "ISSUER - POS REVERSALS (TC25)",
        stats.pos_rev_count, 0.0, stats.pos_rev_amount
    ))
    lines.append(_data_line(
        "ISSUER - CASH REVERSALS (TC27)",
        stats.cash_rev_count, 0.0, stats.cash_rev_amount
    ))
    lines.append(_blank())

    lines.append(_data_line(
        "TOTAL ISSUER",
        total_fwd_count + total_rev_count,
        interchange_debit,
        interchange_credit,
        is_total=True
    ))
    lines.append(_blank())
    lines.append(_hr())
    lines.append(_blank())

    # ── REIMBURSEMENT FEES SECTION ────────────────────────────
    lines.append(_section_header(" REIMBURSEMENT FEES"))
    lines.append(_blank())

    lines.append(_data_line(
        "ISSUER REIMBURSEMENT EARNED (1.80%)",
        total_fwd_count, 0.0, reimb_earned
    ))
    lines.append(_data_line(
        "ISSUER ASSESSMENT FEES PAID (0.10%)",
        total_fwd_count, reimb_paid, 0.0
    ))
    lines.append(_blank())

    lines.append(_data_line(
        "NET REIMBURSEMENT",
        total_fwd_count,
        reimb_paid,
        reimb_earned,
        is_total=True
    ))
    lines.append(_blank())
    lines.append(_hr())
    lines.append(_blank())

    # ── VISA PROCESSING CHARGES ───────────────────────────────
    lines.append(_section_header(" VISA PROCESSING CHARGES"))
    lines.append(_blank())

    lines.append(_data_line(
        "VISANET ACCESS FEE (0.10%)",
        total_fwd_count, visa_charge, 0.0
    ))
    lines.append(_blank())

    lines.append(_data_line(
        "TOTAL VISA CHARGES",
        total_fwd_count, visa_charge, 0.0,
        is_total=True
    ))
    lines.append(_blank())
    lines.append(_hr("="))
    lines.append(_blank())

    # ── NET SETTLEMENT ────────────────────────────────────────
    lines.append(_section_header(" SETTLEMENT SUMMARY"))
    lines.append(_blank())

    lines.append(_data_line("TOTAL DEBITS  (ISSUER PAYS)",  0, net_from_issuer, 0.0))
    lines.append(_data_line("TOTAL CREDITS (ISSUER RECEIVES)", 0, 0.0, net_to_issuer))
    lines.append(_blank())

    if net_settlement < 0:
        net_lbl = "NET SETTLEMENT (ISSUER PAYS VISA)"
        net_d, net_c = abs(net_settlement), 0.0
    else:
        net_lbl = "NET SETTLEMENT (VISA PAYS ISSUER)"
        net_d, net_c = 0.0, net_settlement

    lines.append(_data_line(net_lbl, 0, net_d, net_c, is_total=True))
    lines.append(_blank())
    lines.append(_hr("="))
    lines.append(_blank())

    # ── FOOTER ────────────────────────────────────────────────
    lines.append(
        f"   SETTLEMENT DATE: {report_date}    "
        f"TOTAL POS TXNS: {total_fwd_count}    "
        f"TOTAL VOLUME: INR {_fmt_inr(total_fwd_amount)}    "
        f"RUN ID: {stats.run_id}"
    )
    lines.append(_blank())
    lines.append("   *** END OF REPORT VSS-110 ***")
    lines.append(_blank())

    out_path = Path(out_path)
    out_path.write_text("\n".join(lines), encoding="ascii", errors="replace")
    return out_path


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────

def generate_visa_settlement(
    manifest: dict,
    bank_name: str,
    output_dir: Path,
) -> dict:
    """
    Generate a VISA VSS-110 Settlement report from a manifest.

    Parameters
    ----------
    manifest   : manifest dict produced by generate_visa_matrix() or generate()
    bank_name  : name of the bank
    output_dir : directory to write the file

    Returns
    -------
    dict with path, counts and amounts (same structure as generate_settlement())
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    stats = stats_from_manifest(manifest, bank_name)

    date_str = stats.settlement_date.strftime("%d%m%Y") \
               if stats.settlement_date else datetime.today().strftime("%d%m%Y")

    out_path = output_dir / f"VSS110_VISA_{date_str}.txt"
    write_vss110_report(stats, out_path)

    # Compute net settlement for return dict
    total_fwd   = stats.pos_amount + stats.cash_amount
    reimb_earned = round(total_fwd * FEE_RATES["reimb_earned_pct"], 2)
    reimb_paid   = round(total_fwd * FEE_RATES["reimb_paid_pct"], 2)
    visa_charge  = round(total_fwd * FEE_RATES["visa_charge_pct"], 2)
    total_rev    = stats.pos_rev_amount + stats.cash_rev_amount
    net_from     = round(total_fwd + reimb_paid + visa_charge, 2)
    net_to       = round(total_rev + reimb_earned, 2)
    net_settlement = round(net_to - net_from, 2)

    return {
        "path":              str(out_path),
        "bank_name":         bank_name,
        "settlement_date":   date_str,
        "pos_approved":      stats.pos_count,
        "pos_amount":        stats.pos_amount,
        "cash_approved":     stats.cash_count,
        "cash_amount":       stats.cash_amount,
        "reversal_count":    stats.pos_rev_count + stats.cash_rev_count,
        "reversal_amount":   total_rev,
        "reimb_earned":      reimb_earned,
        "visa_charges":      visa_charge,
        "net_settlement":    net_settlement,
        "total_debit":       net_from,
        "total_credit":      net_to,
        "final_settlement":  net_settlement,
    }


if __name__ == "__main__":
    import json, sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from generators.visa_pos import generate as generate_visa

    result = generate_visa("visa_pos_issuer", volume=50)
    manifest_path = result["manifest_path"]
    manifest = json.loads(Path(manifest_path).read_text())

    out = generate_visa_settlement(
        manifest=manifest,
        bank_name="TEST BANK LTD",
        output_dir=Path(__file__).parent.parent / "output",
    )
    print(f"VSS-110 file: {out['path']}")
    print(f"  POS approved:    {out['pos_approved']} txns  INR {out['pos_amount']:,.2f}")
    print(f"  Cash approved:   {out['cash_approved']} txns  INR {out['cash_amount']:,.2f}")
    print(f"  Net settlement:  INR {out['net_settlement']:,.2f}")
