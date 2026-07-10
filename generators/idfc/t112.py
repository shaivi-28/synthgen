"""
Mastercard T112 network file generator for IDFC First Bank (issuer, POS).

T112 is a CSV with 124 columns. One row per POS transaction.
Key recon fields (0-indexed):
  col1  = MTI (1240 for presentment, 1442 for reversal advice)
  col7  = Processing Code (0=purchase, 9=unique, 18=unique, 12=unique)
  col28 = Acceptor Terminal Id  (auth code zero-padded to 8)
  col44 = Message Reversal Indicator ("" = forward, "R" = reversal)
  col49 = Approval Code (auth code numeric)
  col52 = Function Code (200 = First Presentment)
  col55 = Local Transaction Datetime (DD-MM-YYYY)
  col66 = Transaction Amount (paise)
Amount status rules:
  +1  : MTI=1240, FC=200, PC in (00,09,18,12), MRI=""
  -1  : MTI=1240, FC=200, PC in (00,09,18,12), MRI="R"
   0  : MTI != 1240  (use 1442 for generator "present but not counted")
  NULL: absent
"""

import csv
import io
import random
from datetime import datetime
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction

# T112 has 124 columns (0-indexed 0-123)
_HEADERS = [
    "Acceptor Name And Location", "Message Type Identifier", "Recon Iteration Id",
    "Message Reason Code", "Dun And Bradstreet Number", "Acceptor Business Code Or Mcc",
    "Acceptor Name Ipm", "Processing Code", "Settlement File",
    "Interchange Fee Settlement Indicator", "File Date", "Pos Data Code",
    "Merchant Name", "Cross Border Indicator", "Action Date",
    "Reconciliation Currency Code", "Transaction Currency Code", "Acceptor City Ipm",
    "Currency Exponents", "Unique Identifier", "Cardholder Billing Amount New",
    "Unmatch Source Id", "Ticket Id", "Reconciliation Amount New", "Legal Corporate Name",
    "Cardholder Billing Amount", "Acceptor Business Code", "Fee Amount Recon Currency Credit",
    "Acceptor Terminal Id", "Pan Masked First 9", "Multi Use Transaction Identification Data",
    "Voucher Charges Amt", "Unique Identifier Dispute", "Transaction Originator Institution Id Code",
    "Terminal Type", "Forwarding Institution Id", "Missing Source Tag",
    "Cardholder Billing Currency Code", "Transaction Life Cycle Id",
    "Acceptor Street Address Ipm", "Action Status", "Acceptor Id Code",
    "Voucher Id", "Mastercard Assigned Id", "Message Reversal Indicator",
    "Settlement Date", "Settlement Service", "Acceptor Postalcode Ipm",
    "Voucher Uid", "Approval Code", "File Id Sequence Number",
    "Acquiring Institution Id", "Function Code", "Settlement Cycle", "Id",
    "Local Transaction Datetime", "Additional Acceptor Data",
    "Primary Account Number Last 4 Masked", "Card Sequence Number",
    "Installment Payment Data", "Primary Account Number", "Reconciliation Amount",
    "Document Indicator", "Recon Date", "Sole Proprietor Name",
    "Voucher Interchange Amt", "Transaction Amount", "Acquirer Reference Id",
    "Acceptor Url Address", "Unmatch Tag", "Unmatch Source Data Id",
    "Remote Payments Program Data", "Remarks", "Electronic Commerce Security Level Idencator",
    "Voucher Recon Amt", "File Id Delivery Cycle", "Data Record",
    "Trasaction Category Indicator", "Voucher Status", "Acceptor Inquiry Information",
    "Acquiring Institution Code", "Transaction Amount Recon Currency Debit",
    "Token Requestor Id", "Independent Sales Organization Id", "Issuer Reference Data",
    "Status", "Fee Collection Control Number", "Recon Cycle", "Business Activity",
    "Acquirer Reference Data", "Date Flag", "Reconciled File Id Sequence Number",
    "Retrieval Reference Code", "Processing Code 6 Digits",
    "Interchange Fee Processing Code", "Interchange Rate Designator",
    "Additional Amounts", "Interchange Fee Recon Amount", "Additional Merchant Data",
    "Currency Codes Original Amounts", "Amount Partial Transaction", "Advisment Date",
    "Transaction Destination Institution Id Code", "Merchant City",
    "Settlement Indicator", "Country Code Transaction", "Interchange Fee Currency Code",
    "Reconciled File Id Delivery Cycle", "Original Amount", "Transit Program",
    "Ticket Status", "Reconciliation Conversion Factor",
    "Reconciled File Id Clearing Cycle", "Interchange Fee Type", "Interchange Fee Amount",
    "Pan Masked", "Interchange Fee Recon Currency Code", "File Id Clearing Cycle",
    "Service Code", "File Type", "File Name", "Final Charges",
    "Osfin Transaction Id", "Voucher Billing Amt",
]

assert len(_HEADERS) == 124, f"Expected 124 headers, got {len(_HEADERS)}"


def _unique_id(tx: Transaction) -> str:
    """14-digit masked card identifier: F6 + 4-middle + L4 of PAN."""
    pan = tx.card_pan.strip()
    if len(pan) >= 16:
        return pan[:6] + pan[6:10] + pan[-4:]
    return (pan + "0" * 14)[:14]


def _approval_code(tx: Transaction) -> str:
    """Numeric auth code (strip leading zeros from 6-char auth_id)."""
    try:
        return str(int(tx.auth_id[:6]))
    except (ValueError, TypeError):
        return tx.auth_id[:6]


def _amt_rupees(paise: int) -> str:
    """Convert paise to rupee string, stripping trailing zeros after decimal."""
    val = paise / 100
    if paise % 100 == 0:
        return str(int(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def _build_t112_row(
    tx: Transaction,
    config: dict,
    file_name: str,
    osfin_id: int,
    status_0: bool = False,
) -> List[str]:
    """Build one T112 CSV row (124-element list of strings)."""
    mc = config.get("mastercard", {})
    acq_inst_id = mc.get("acquiring_institution_id", "7064")
    acq_ref_id  = mc.get("acquirer_reference_id", "503726")
    is_rev = tx.msg_type == "0420"

    # status=0 rows: use MTI=1442 (not 1240) so recon engine marks them 0
    mti = "1442" if status_0 else "1240"
    fc  = "200" if not status_0 else "200"
    mri = "R" if (is_rev and not status_0) else ""
    processing_code = "0"   # 00 = Purchase Original

    tran_amt_paise = tx.amount
    tran_amt_rupees = _amt_rupees(tran_amt_paise)

    # Currency: 356=INR always for settlement; transaction currency matches
    recon_currency = "356"
    tran_currency  = "356"  # domestic; INT would have foreign currency

    tran_date_str = tx.tran_date.strftime("%d-%m-%Y")

    # Interchange fee: ~0.9% of transaction amount
    fee_paise = max(1, round(tran_amt_paise * 0.009))
    fee_str = _amt_rupees(fee_paise)

    # Masked PAN: F6+4+L4
    uid = _unique_id(tx)
    pan_last4 = tx.card_pan[-4:] if len(tx.card_pan) >= 4 else tx.card_pan
    pan_masked = tx.card_pan[:6] + ("X" * (len(tx.card_pan) - 10)) + tx.card_pan[-4:]

    row = [""] * 124

    row[0]  = "\\\\"                              # Acceptor Name And Location
    row[1]  = mti                                  # Message Type Identifier
    row[3]  = "1401"                               # Message Reason Code
    row[5]  = tx.mcc                               # Acceptor Business Code Or Mcc
    row[7]  = processing_code                      # Processing Code
    row[12] = (tx.terminal_location or "")[:35]    # Merchant Name
    row[15] = recon_currency                       # Reconciliation Currency Code
    row[16] = tran_currency                        # Transaction Currency Code
    row[19] = uid                                  # Unique Identifier
    row[20] = tran_amt_rupees                      # Cardholder Billing Amount New (rupees)
    row[23] = tran_amt_rupees                      # Reconciliation Amount New
    row[25] = str(tran_amt_paise)                  # Cardholder Billing Amount (paise)
    row[28] = tx.auth_id.zfill(8)                  # Acceptor Terminal Id (auth code = terminal key)
    row[37] = recon_currency                       # Cardholder Billing Currency Code
    row[44] = mri                                  # Message Reversal Indicator
    row[49] = _approval_code(tx)                   # Approval Code
    row[51] = acq_inst_id                          # Acquiring Institution Id
    row[52] = fc                                   # Function Code
    row[54] = str(osfin_id)                        # Id (sequential)
    row[55] = tran_date_str                        # Local Transaction Datetime
    row[57] = pan_last4                            # Primary Account Number Last 4 Masked
    row[58] = "1"                                  # Card Sequence Number
    row[61] = str(tran_amt_paise)                  # Reconciliation Amount (paise)
    row[66] = str(tran_amt_paise)                  # Transaction Amount (paise)
    row[67] = acq_ref_id                           # Acquirer Reference Id
    row[77] = "I" if tx.tran_category == "I" else ""   # Transaction Category Indicator
    row[78] = "UNPROCESSED"                        # Voucher Status
    row[81] = str(tran_amt_paise)                  # Transaction Amount Recon Currency Debit
    row[85] = "UNRECONCILED"                       # Status
    row[90] = "1"                                  # Date Flag
    row[95] = "PE"                                 # Interchange Rate Designator
    row[100] = "0"                                 # Amount Partial Transaction
    row[103] = (tx.terminal_location or "INDIA")[:30]   # Merchant City
    row[105] = "IN"                                # Country Code Transaction
    row[106] = recon_currency                      # Interchange Fee Currency Code
    row[113] = "0"                                 # Interchange Fee Type
    row[114] = fee_str                             # Interchange Fee Amount
    row[115] = pan_masked                          # Pan Masked
    row[120] = file_name                           # File Name
    row[122] = str(osfin_id)                       # Osfin Transaction Id

    return row


def build_t112_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    run_ts: datetime,
) -> Tuple[str, str]:
    """
    Generate the T112 CSV file for Mastercard POS issuer recon.
    Returns (content_str, filename).
    """
    ddmmyy  = tran_date.strftime("%d%m%y").upper()
    hhmmss  = tran_date.strftime("%H%M%S")
    run_ts_str = run_ts.strftime("%Y%m%d%H%M%S") + str(random.randint(100, 999))
    seq = random.randint(1, 9)
    file_name = f"MCI.AR.T112.D{ddmmyy}.T{hhmmss}_OS_{run_ts_str}.A{seq:03d}"

    # Filename for the output file uses the run timestamp
    ts_str = run_ts.strftime("%H%M%S")
    ddmmyyyy = tran_date.strftime("%d%m%Y")
    output_filename = f"T112_{ddmmyyyy}_{ts_str}.txt"

    rows: List[List[str]] = []
    osfin_counter = random.randint(5000, 9000)

    for sg in groups:
        for tx in sg.t112_rows:
            if tx.mcc == "6011":
                continue   # T112 is POS only; ATM uses T464
            osfin_counter += 1
            # status_0 rows are flagged by a sentinel msg_type we never generate otherwise
            status_0 = (tx.msg_type == "0000")
            row = _build_t112_row(tx, config, file_name, osfin_counter, status_0=status_0)
            rows.append(row)

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_HEADERS)
    writer.writerows(rows)
    content = output.getvalue()

    return content, output_filename
