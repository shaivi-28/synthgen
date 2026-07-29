"""
EuroNet (EN) Electronic Journal (EJ) Generator for IDFC First Bank ATMs.

EuroNet ATMs are dispense-only (withdrawal + balance inquiry). Structurally
identical to the PN/IN dispense-only family: same duplicate TRANSACTION
START/ATR RECEIVED sequence, same == DATE ID == / control-byte preamble /
#EOL# bookends, CRLF line endings, same TXN NO./RRN padding rules. Only the
filename uses ER-style hyphenated dates (EN{ATM_ID}-{DDMMYYYY}).

Reference structure (successful withdrawal):
    == MONTH DD, YYYY ATMID ==
    HHMMSS Card inserted / *date*time* / TRANSACTION START / ATR RECEIVED
    HHMMSS Card BIN entry DEFAULT / *date*time* / TRANSACTION START / ATR RECEIVED (repeated)
    HHMMSS Card BIN group 1 / Card BIN entry IDFCOFFUS/IDFCONUS / Customer PAN
    HHMMSS PIN code entered / REQUEST SENT / RESPONSE RECEIVED / GENAC 2
    HHMMSS NOTES STACKED / Cash presented / Cash taken / CASH TOTAL table
    receipt block (branch, ATM ID, masked card, TXN NO., WITHDRAWAL, FROM A/C,
    AVAIL BAL, RESPONSE CODE, RRN)
    HHMMSS Card ejected / Card taken / TRANSACTION END
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS / REFERENCE DATA
# ─────────────────────────────────────────────────────────────────────────────

LOCATIONS = [
    "DARYAGANJ BRANCH",
    "CONNAUGHT PLACE BRANCH",
    "KAROL BAGH BRANCH",
    "SAKET BRANCH",
    "NEHRU PLACE BRANCH",
]

CARD_PREFIXES = ["508162", "421366", "544670", "460033", "402314"]

# Withdrawal amounts — EuroNet sample dispenses INR500 notes only
AMOUNTS = [1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
           6000, 7000, 8000, 9000, 10000]

MONTH_MAP = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Generic decline response code — no dedicated real-EN sample for the
# generic case, reused from the PN/PR/IN family's established convention.
GENERIC_DECLINE_CODE = "100"

# Real-evidence PIN-stage decline pair (RESPONSE CODE + literal message line).
PIN_TRIES_EXCEEDED_CODE = "075"
INVALID_PIN_CODE = "055"

# INVENTED/UNVERIFIED — no real EN sample shows a decline after amount entry
# or at the card-read stage (every real decline in the mined sample happens
# at the PIN stage). Confirm these against the actual host/switch code table
# before trusting them for reconciliation testing.
INSUFFICIENT_FUNDS_CODE = "051"       # INVENTED
DAILY_LIMIT_EXCEEDED_CODE = "061"     # INVENTED
UNAUTHORIZED_CARD_CODE = "057"        # INVENTED
CARD_EXPIRED_CODE = "054"             # INVENTED

# Fixed 2048-byte printer control-block preamble emitted once before the
# file's first '==' header. EN's own byte-exact preamble hasn't been
# captured — this borrows PN's exact lead bytes/structure (see eps_ej.py's
# _control_preamble) as a documented placeholder pending real EN evidence.
_PREAMBLE_PREFIX = "(w\x04  \x08   \x03"


def _control_preamble() -> str:
    return _PREAMBLE_PREFIX + " " * (2048 - len(_PREAMBLE_PREFIX) - 2) + "\r\n"


# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_header_date(dt: datetime) -> str:
    """Format as 'MAY 02, 2025'"""
    return f"{MONTH_MAP[dt.month]} {dt.day:02d}, {dt.year}"


def _fmt_slash_ts(dt: datetime) -> tuple:
    """Format as '05/02/2025*10:30:46' pieces used inside *DD/MM/YYYY*HH:MM:SS*"""
    return dt.strftime("%m/%d/%Y"), dt.strftime("%H:%M:%S")


def _gen_atm_id() -> str:
    """Generate a 6-digit numeric ATM ID (e.g. 201230)."""
    return f"20{random.randint(1000, 9999)}"


def _random_pan(prefix: str) -> tuple:
    """Returns (log_masked, receipt_masked) e.g. ('508162***3344', '508162******3344')."""
    last4 = str(random.randint(1000, 9999))
    log_masked = f"{prefix}***{last4}"
    receipt_masked = f"{prefix}******{last4}"
    return log_masked, receipt_masked


def _random_account() -> str:
    """17-digit account number, zero-padded like '00000010036578102'."""
    return "000000" + str(random.randint(10**10, 10**11 - 1))


def _random_balance(min_bal: int = 15000, max_bal: int = 60000) -> float:
    return round(random.uniform(min_bal, max_bal), 2)


def _fmt_money_field(amount: float, width: int) -> str:
    """Right-justify 'RS.amount' within a fixed total width (matches reference alignment)."""
    return f"{'RS.' + f'{amount:.2f}':>{width}}"


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LINE EMITTER
# ─────────────────────────────────────────────────────────────────────────────

class EJWriter:
    """Tracks the current timestamp and emits EuroNet-style EJ lines."""

    def __init__(self, start_dt: datetime):
        self._dt = start_dt
        self._lines = []

    def _next_ts(self, delta_secs: int = 1) -> datetime:
        self._dt += timedelta(seconds=delta_secs)
        return self._dt

    def event(self, text: str, delta_secs: int = 1, double_space: bool = False) -> None:
        """Emit a timestamp-prefixed event line."""
        ts = self._next_ts(delta_secs)
        sep = "  " if double_space else " "
        self._lines.append(f"{ts.strftime('%H%M%S')}{sep}{text}")

    def raw(self, text: str = "") -> None:
        """Emit a raw line with no timestamp prefix (journal / receipt body)."""
        self._lines.append(text)

    def header(self, tran_date: datetime, atm_id_full: str) -> None:
        self._lines.append(f"== {_fmt_header_date(tran_date)} {atm_id_full} ==")

    def advance(self, secs: int) -> None:
        self._dt += timedelta(seconds=secs)

    @property
    def dt(self) -> datetime:
        return self._dt

    def get_text(self) -> str:
        return "\r\n".join(self._lines)


# ─────────────────────────────────────────────────────────────────────────────
# CASSETTE STATE (500-denomination only, matches EuroNet sample)
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    def __init__(self):
        self.dispensed = random.randint(4000, 4600)
        self.rejected = random.randint(3, 15)
        self.remaining = random.randint(3000, 4000)

    def dispense(self, count: int) -> None:
        self.dispensed += count
        self.remaining = max(0, self.remaining - count)

    def table_lines(self, force_reject: int = None) -> list:
        # TYPE4 (unknown denomination) column stays permanently 00000 — the
        # real EN sample's CASH TOTAL table always has exactly 5 rows and no
        # populated TYPE4 reading; 'unknown_denom_notes' is represented as a
        # log line instead (see _gen_simple_withdrawal), not a table change.
        rejected = force_reject if force_reject is not None else self.rejected
        return [
            "CASH TOTAL   TYPE1 TYPE2 TYPE3 TYPE4",
            "DENOMINATION               500      ",
            f"DISPENSED    00000 00000 {self.dispensed:05d} 00000",
            f"REJECTED     00000 00000 {rejected:05d} 00000",
            f"REMAINING    00000 00000 {self.remaining:05d} 00000",
            "LOST         00000 00000 00000 00000",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# SHARED SESSION OPENING
# ─────────────────────────────────────────────────────────────────────────────

def _gen_card_session_opening(w: EJWriter) -> tuple:
    """Card inserted through Customer PAN, shared by every EN session type.
    Real reference dumps fire the *date*time*/TRANSACTION START/ATR RECEIVED
    trio TWICE before continuing to the Card BIN group/PAN steps. Returns
    (log_pan, receipt_pan) from a single _random_pan() call so the log and
    any printed receipt always agree.
    """
    card_prefix = random.choice(CARD_PREFIXES)
    log_pan, receipt_pan = _random_pan(card_prefix)

    w.event("Card inserted", delta_secs=random.randint(1, 3))
    date_part, time_part = _fmt_slash_ts(w.dt)
    w.event(f"*{date_part}*{time_part}*", delta_secs=0)
    w.raw("*TRANSACTION START*")
    w.raw("")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("Card BIN entry is DEFAULT", delta_secs=random.randint(1, 2))

    date_part, time_part = _fmt_slash_ts(w.dt)
    w.event(f"*{date_part}*{time_part}*", delta_secs=0)
    w.raw("*TRANSACTION START*")
    w.raw("")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("Card BIN group is 1", delta_secs=0)
    w.event("Card BIN entry is IDFCOFFUS CARDS", delta_secs=random.randint(2, 4))
    w.event("Card BIN group is 0", delta_secs=random.randint(2, 4))
    w.event("Card BIN entry is IDFCONUS CARDS", delta_secs=0)
    w.event(f"Customer PAN: {log_pan}", delta_secs=0)
    w.raw("")

    return log_pan, receipt_pan


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def _gen_simple_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int,
                            tran_date: datetime, location: str,
                            atm_id_full: str, force_reject: int = None,
                            force_unknown: bool = False, split: bool = False) -> int:
    """Generate a single successful simple withdrawal transaction.

    force_reject lets 'notes_in_reject' reuse this exact function rather
    than inventing new structure — it just forces a nonzero value into the
    existing REJECTED table column (no extra row/table change).

    force_unknown represents 'unknown_denom_notes'. EN is dispense-only, so
    the customer-facing "unknown denomination note" concept used by ER/PR/NX
    deposit flows doesn't physically apply here — there's nothing for the
    ATM to misidentify on the way IN. The dispense-side equivalent is the
    cash dispenser's own note-recognition sensor flagging a note as
    unreadable while counting/stacking from the cassette. INVENTED/UNVERIFIED
    (no real EN sample shows a dispense-side unknown-denom event): rendered
    as a single fault line right before 'Cash presented', not as any change
    to the CASH TOTAL table (a prior new-row approach there was reverted as
    a format bug — don't repeat it).

    split=True repeats the dispense cycle twice (partial/split transaction)
    before the single final receipt.
    """
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = txn_no

    w.header(tran_date, atm_id_full)
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    if split:
        first = note_count // 2
        rounds = [first, note_count - first]
    else:
        rounds = [note_count]

    for round_idx, rc in enumerate(rounds):
        w.event("NOTES STACKED", delta_secs=random.randint(5, 12), double_space=True)
        w.raw("")

        if force_unknown and round_idx == 0:
            # INVENTED/UNVERIFIED — best-interpretation placeholder for a
            # dispense-side note-recognition fault (see docstring above).
            w.event("NOTE RECOGNITION FAULT - UNIDENTIFIED DENOMINATION",
                    delta_secs=random.randint(1, 3))

        w.event("Cash presented", delta_secs=random.randint(5, 10))
        w.event(f"NOTES PRESENTED 0,0,{rc},0", delta_secs=random.randint(0, 1), double_space=True)
        w.raw("")

        w.event(f"{rc * 500} INR", delta_secs=0)
        w.raw(f"{rc}X500 ")
        w.event("Cash taken", delta_secs=random.randint(0, 1))

        cs.dispense(rc)

    tbl = cs.table_lines(force_reject=force_reject)
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(" ")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(" ")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:      {acc_no}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}        ")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")
    w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_balance_inquiry(w: EJWriter, txn_no: int, tran_date: datetime,
                          location: str, atm_id_full: str, success: bool = True) -> int:
    """Balance inquiry — success shows AVAILABLE BAL (full word, unlike
    withdrawal's AVAIL BAL) and a BALANCE INQUIRY line instead of
    WITHDRAWAL; nothing dispensed, FROM A/C fully asterisk-masked. Decline
    reuses the generic decline shape (GENAC AAC). Consumes a TXN NO either way."""
    acc_no = _random_account()

    w.header(tran_date, atm_id_full)
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")

    if success:
        w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
        w.raw("")
        avail_bal = _random_balance()
        w.event(f"    {location}", delta_secs=random.randint(1, 3))
        w.raw(" ")
        w.raw("    DATE       TIME       ATM ID")
        w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
        w.raw(" ")
        w.raw(f"    CARD NUMBER:  {receipt_pan}")
        w.raw(" ")
        w.raw(f"    TXN NO.     {txn_no}")
        w.raw(" ")
        w.raw("    BALANCE INQUIRY")
        w.raw(f"    FROM A/C:      {'*' * len(acc_no)}")
        w.raw(f"    AVAILABLE BAL{_fmt_money_field(avail_bal, 14)}")
        w.raw("    RESPONSE CODE              000")
        w.raw("    YOUR TXN IS SUCCESSFUL")
        w.raw(f"    RRN.               {txn_no}        ")
        w.raw("    GO CASH FREE!USE DEBIT CARDS")
        w.raw("    NEVER SHARE YOUR CARD DETAILS")
        w.raw("    AND PIN WITH ANYONE")
        w.raw("")
    else:
        w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
        w.raw("")
        w.event(f"    {location}", delta_secs=random.randint(1, 3))
        w.raw(" ")
        w.raw("    DATE       TIME       ATM ID")
        w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
        w.raw(" ")
        w.raw(f"    CARD NUMBER:  {receipt_pan}")
        w.raw(" ")
        w.raw(f"    TXN NO.     {txn_no}")
        w.raw(" ")
        w.raw("    BALANCE INQUIRY")
        w.raw(f"    RESPONSE CODE              {GENERIC_DECLINE_CODE}")
        w.raw("    SORRY UNABLE TO PROCESS")
        w.raw(f"    RRN.               {txn_no}        ")
        w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_host_timeout(w: EJWriter, tran_date: datetime, location: str, atm_id_full: str) -> None:
    """REAL EVIDENCE (EN sample): the link drops and recovers, then the
    host never responds at all. No RESPONSE RECEIVED, no receipt block.
    Does not consume a TXN NO since the host never assigned one."""
    w.header(tran_date, atm_id_full)
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")

    w.event("LINK1 Fatal [Closed]", delta_secs=random.randint(2, 5))
    w.event("LINK1 Healthy [Open]", delta_secs=random.randint(3, 8))
    w.event("HOST TX TIMEOUT", delta_secs=random.randint(20, 40))
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)


def _gen_power_cut(w: EJWriter) -> None:
    """REAL EVIDENCE (EN sample): mid-log device restart/reinit sequence —
    not a customer transaction, doesn't consume num_transactions or a TXN NO."""
    dt = w.dt
    w.raw(f"========== LOGS INITIALIZED: {dt.month}/{dt.day}/{dt.year} ==========")
    w.raw("Preparing to write audit lines from before initialization")
    w.raw(r"ERROR: Could not open 'X:\SMARTatm\activity\C2NOTESRETAIN.LOG' in read/write mode - new file created")
    w.raw(r"ERROR: Could not open 'X:\SMARTatm\activity\CASHRETRACT.LOG' in read/write mode - new file created")
    w.raw(r"ERROR: Could not open 'X:\SMARTatm\activity\CHEQUEAUDIT.LOG' in read/write mode - new file created")
    w.event("MAG Healthy [0/0/-100/0]", delta_secs=random.randint(1, 3))
    w.event("KEYS Healthy [0/0/-1/-1]", delta_secs=random.randint(1, 3))
    w.event("ENCR Healthy [0/0/-1/-1]", delta_secs=random.randint(1, 3))
    w.event("RCPT Healthy [0/0/50/-100]", delta_secs=random.randint(1, 3))
    w.event("CASH Fatal [90/-1/-1/-1]", delta_secs=random.randint(1, 3))
    w.raw("Device Not Initialized")
    w.event("SENS Healthy [0/0/-1/-1]", delta_secs=random.randint(1, 3))
    w.event("OSKEYS Fatal [90/-1/-1/-1]", delta_secs=random.randint(1, 3))
    w.event("SOFTWARE VERSIONS", delta_secs=random.randint(1, 3))
    w.raw("VCP: 6.0.13")
    w.raw(f"NDC: {random.randint(1, 9)}.{random.randint(0, 9)}.{random.randint(0, 20)}")
    w.raw(f"KERNEL: {random.randint(1, 9)}.{random.randint(0, 9)}.{random.randint(0, 20)}")
    w.raw(f"OS: {random.randint(1, 9)}.{random.randint(0, 9)}.{random.randint(0, 20)}")
    w.advance(random.randint(5, 20))


def _gen_admin_cassette(w: EJWriter) -> None:
    """REAL EVIDENCE (EN sample): cassette-replenishment maintenance
    sequence — not a customer transaction, no card/PIN flow, no TXN NO."""
    w.event("CASH Fatal [90/0/0/0]", delta_secs=random.randint(2, 5))
    w.raw("Media Empty")
    w.event("REJECT BIN REMOVED", delta_secs=random.randint(5, 15))
    w.event("CART 1 REMOVED", delta_secs=random.randint(2, 5))
    w.event("CART 2 REMOVED", delta_secs=random.randint(2, 5))
    w.event("CASH Fatal [90/0/0/0]", delta_secs=random.randint(2, 5))
    w.raw("REJECT cart not found")
    w.event("REJECT BIN INSERTED", delta_secs=random.randint(5, 15))
    w.event("CART 3 REMOVED", delta_secs=random.randint(2, 5))
    w.event("CART 1 INSERTED", delta_secs=random.randint(20, 60))
    w.event("CART 2 INSERTED", delta_secs=random.randint(5, 15))
    w.event("CART 3 INSERTED", delta_secs=random.randint(5, 15))
    w.event("CART 4 INSERTED", delta_secs=random.randint(5, 15))
    w.event("REJECT BIN INSERTED", delta_secs=random.randint(5, 15))
    w.raw("NUM    CURR    TOT  DEP   LEFT   DISP")
    w.raw("3  INR500      0    0      0      0")
    w.raw("NUM    CURR   RJCT   LOST")
    w.raw("3  INR500      0      0")
    w.advance(random.randint(5, 20))


def _gen_pin_stage_decline(w: EJWriter, txn_no: int, tran_date: datetime,
                            location: str, atm_id_full: str,
                            response_code: str, message: str = None) -> int:
    """Shared shape for every decline that reaches the host after PIN entry
    (generic decline / PIN tries exceeded / invalid PIN / insufficient
    funds / daily limit exceeded) — only RESPONSE CODE + an optional extra
    message line differ. PIN tries exceeded (075) and invalid PIN (055) are
    REAL EVIDENCE; insufficient funds/daily limit codes are INVENTED
    placeholders (no real sample shows a post-amount-entry decline).
    Consumes a TXN NO."""
    w.header(tran_date, atm_id_full)
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(" ")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(" ")
    w.raw(f"    RESPONSE CODE              {response_code}")
    if message:
        w.raw(f"    {message}")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(f"    RRN.               {txn_no}        ")
    w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_bin_stage_decline(w: EJWriter, txn_no: int, tran_date: datetime,
                            location: str, atm_id_full: str,
                            response_code: str, message: str) -> int:
    """Shared shape for declines that never reach PIN entry — card is
    rejected at the BIN/card-read stage (unauthorized card, expired card).
    INVENTED/UNVERIFIED: no real EN sample shows this exact receipt shape,
    only that these declines must occur before 'PIN code entered' ever
    appears. Consumes a TXN NO (assumes the ATM still logs a local
    reference number for the rejected attempt)."""
    card_prefix = random.choice(CARD_PREFIXES)
    log_pan, receipt_pan = _random_pan(card_prefix)

    w.header(tran_date, atm_id_full)
    w.event("Card inserted", delta_secs=random.randint(1, 3))
    date_part, time_part = _fmt_slash_ts(w.dt)
    w.event(f"*{date_part}*{time_part}*", delta_secs=0)
    w.raw("*TRANSACTION START*")
    w.raw("")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("Card BIN entry is DEFAULT", delta_secs=random.randint(1, 2))

    date_part, time_part = _fmt_slash_ts(w.dt)
    w.event(f"*{date_part}*{time_part}*", delta_secs=0)
    w.raw("*TRANSACTION START*")
    w.raw("")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("Card BIN group is 1", delta_secs=0)
    w.event(f"Customer PAN: {log_pan}", delta_secs=random.randint(2, 4))
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(" ")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(" ")
    w.raw(f"    RESPONSE CODE              {response_code}")
    w.raw(f"    {message}")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(f"    RRN.               {txn_no}        ")
    w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_cash_not_taken(w: EJWriter, cs: CassetteState, txn_no: int, tran_date: datetime,
                         location: str, atm_id_full: str) -> int:
    """Extrapolated: cash is dispensed but never collected — a retract
    event fires instead of the customer taking it."""
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = txn_no

    w.header(tran_date, atm_id_full)
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")
    w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")
    w.event("NOTES STACKED", delta_secs=random.randint(5, 12), double_space=True)
    w.raw("")
    w.event("Cash presented", delta_secs=random.randint(5, 10))
    w.event(f"NOTES PRESENTED 0,0,{note_count},0", delta_secs=random.randint(0, 1), double_space=True)
    w.raw("")
    w.event(f"{amount} INR", delta_secs=0)
    w.raw(f"{note_count}X500 ")

    w.event("NOTES RETRACTED", delta_secs=random.randint(25, 35))

    cs.dispense(note_count)
    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(" ")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(" ")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:      {acc_no}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}        ")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")
    w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_failure_to_collect_card(w: EJWriter, cs: CassetteState, txn_no: int, tran_date: datetime,
                                  location: str, atm_id_full: str) -> int:
    """Extrapolated: normal successful withdrawal, but the card is never
    collected — a capture/retain event fires instead of 'Card taken'."""
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = txn_no

    w.header(tran_date, atm_id_full)
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(" OPCODE = GA     A")
    w.raw("")
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")
    w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")
    w.event("NOTES STACKED", delta_secs=random.randint(5, 12), double_space=True)
    w.raw("")
    w.event("Cash presented", delta_secs=random.randint(5, 10))
    w.event(f"NOTES PRESENTED 0,0,{note_count},0", delta_secs=random.randint(0, 1), double_space=True)
    w.raw("")
    w.event(f"{amount} INR", delta_secs=0)
    w.raw(f"{note_count}X500 ")
    w.event("Cash taken", delta_secs=random.randint(0, 1))

    cs.dispense(note_count)
    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(" ")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(" ")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:      {acc_no}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}        ")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")
    w.raw("")

    w.event("Card retained", delta_secs=random.randint(3, 8))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

# case_id -> baseline mix weight (only outcomes whose case_id is selected
# are included in the pool for a given run).
_OUTCOME_WEIGHTS = {
    "simple_withdrawal": 45,
    "declined_withdrawal": 10,
    "balance_inquiry": 6,
    "host_timeout": 3,
    "pin_tries_exceeded": 3,
    "invalid_pin": 3,
    "declined_insufficient_funds": 3,
    "declined_unauthorized_card": 2,
    "daily_limit_exceeded": 2,
    "card_expired": 2,
    "cash_not_taken": 3,
    "notes_in_reject": 5,
    "partial_split_transaction": 4,
    "unknown_denom_notes": 3,
    "failure_to_collect_card": 3,
}
# Non-customer-transaction "noise" events — inserted between transactions,
# never consume num_transactions or a TXN NO.
_NOISE_CASE_IDS = ["power_cut", "admin_cassette"]


def generate_euronet_ej(
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
    continuation: dict = None,
) -> dict:
    """
    Generate a EuroNet EJ file for IDFC First Bank ATMs.

    Args:
        tran_date: transaction date
        num_transactions: number of customer transactions
        selected_cases: list of case IDs to include
        atm_id: numeric ATM ID string, e.g. '201230' (auto-generated if None)
        location: branch name (random if None)
        output_dir: output directory (uses /tmp if None)
        continuation: optional {"next_txn_no": int} from a prior run's result to
            keep TXN NO continuous across multiple files for the same ATM+day
            batch ("sync with other files"), instead of restarting at a fresh
            random TXN NO.

    Returns:
        dict with run_id, file_name, atm_id, location, counts, continuation
    """
    random.seed()  # non-deterministic

    run_id = uuid.uuid4().hex[:12]
    if atm_id is None:
        atm_id = _gen_atm_id()
    atm_id_full = f"EN{atm_id}"
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # File naming: EN{ATM_ID}-{DDMMYYYY}.txt
    date_str_file = tran_date.strftime("%d%m%Y")
    file_name = f"{atm_id_full}-{date_str_file}.txt"

    start_dt = tran_date.replace(hour=1, minute=45, second=0, microsecond=0)
    w = EJWriter(start_dt)
    cs = CassetteState()

    case_set = set(selected_cases) if selected_cases else {"simple_withdrawal"}

    outcome_pool, weights = [], []
    if "simple_withdrawal" in case_set or not case_set:
        outcome_pool.append("simple_withdrawal")
        weights.append(_OUTCOME_WEIGHTS["simple_withdrawal"])
    for case_id in _OUTCOME_WEIGHTS:
        if case_id == "simple_withdrawal":
            continue
        if case_id in case_set:
            outcome_pool.append(case_id)
            weights.append(_OUTCOME_WEIGHTS[case_id])

    if continuation and continuation.get("next_txn_no") is not None:
        txn_no = continuation["next_txn_no"]
    else:
        txn_no = random.randint(5000, 9000)
    counts = {"total": 0, **{cid: 0 for cid in _OUTCOME_WEIGHTS}, **{cid: 0 for cid in _NOISE_CASE_IDS}}

    # Coverage guarantee: every explicitly-selected outcome must appear at
    # least once in the file rather than being left to chance — low-weight
    # scenarios (rare declines, edge cases) could easily roll zero times over
    # a normal-sized run, which looked like regressions across successive
    # generations even though nothing was actually broken. Reserve one slot
    # per pending outcome among the trailing iterations of the loop; if an
    # outcome still hasn't fired naturally by the time its reserved slot is
    # reached, force it there. admin_cassette/power_cut are separate
    # between-transaction "noise" events (not customer-transaction outcomes,
    # so they aren't in outcome_pool) and get the same guarantee below,
    # forced on the final iteration if they haven't fired yet.
    to_guarantee = list(outcome_pool)
    random.shuffle(to_guarantee)
    guarantee_start = max(0, num_transactions - len(to_guarantee))

    for _txn_idx in range(num_transactions):
        w.advance(random.randint(60, 900))

        is_last = _txn_idx == num_transactions - 1
        force_power_cut = "power_cut" in case_set and counts["power_cut"] == 0 and is_last
        if "power_cut" in case_set and (force_power_cut or random.random() < 0.03):
            _gen_power_cut(w)
            counts["power_cut"] += 1
        # admin_cassette is a real, explicitly-selected maintenance scenario —
        # guarantee at least one instance per file (not just a per-iteration
        # roll) by forcing it on the last iteration if it hasn't fired yet.
        force_admin = "admin_cassette" in case_set and counts["admin_cassette"] == 0 and is_last
        if "admin_cassette" in case_set and (force_admin or random.random() < 0.05):
            _gen_admin_cassette(w)
            counts["admin_cassette"] += 1

        if not outcome_pool:
            continue
        reserved_idx = _txn_idx - guarantee_start
        pending = to_guarantee[reserved_idx] if 0 <= reserved_idx < len(to_guarantee) else None
        if pending is not None and counts[pending] == 0:
            outcome = pending
        else:
            outcome = random.choices(outcome_pool, weights=weights, k=1)[0]

        if outcome == "simple_withdrawal":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "declined_withdrawal":
            txn_no = _gen_pin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             GENERIC_DECLINE_CODE)
        elif outcome == "balance_inquiry":
            txn_no = _gen_balance_inquiry(w, txn_no, tran_date, location, atm_id_full,
                                           success=random.random() < 0.7)
        elif outcome == "host_timeout":
            _gen_host_timeout(w, tran_date, location, atm_id_full)
        elif outcome == "pin_tries_exceeded":
            txn_no = _gen_pin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             PIN_TRIES_EXCEEDED_CODE, "PIN TRIES EXCEEDED")
        elif outcome == "invalid_pin":
            txn_no = _gen_pin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             INVALID_PIN_CODE, "INVALID PIN")
        elif outcome == "declined_insufficient_funds":
            txn_no = _gen_pin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             INSUFFICIENT_FUNDS_CODE, "INSUFFICIENT FUNDS")
        elif outcome == "declined_unauthorized_card":
            txn_no = _gen_bin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             UNAUTHORIZED_CARD_CODE, "TRANSACTION NOT PERMITTED")
        elif outcome == "daily_limit_exceeded":
            txn_no = _gen_pin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             DAILY_LIMIT_EXCEEDED_CODE, "EXCEEDS WITHDRAWAL LIMIT")
        elif outcome == "card_expired":
            txn_no = _gen_bin_stage_decline(w, txn_no, tran_date, location, atm_id_full,
                                             CARD_EXPIRED_CODE, "CARD EXPIRED")
        elif outcome == "cash_not_taken":
            txn_no = _gen_cash_not_taken(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "notes_in_reject":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full,
                                             force_reject=random.randint(1, 4))
        elif outcome == "partial_split_transaction":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full,
                                             split=True)
        elif outcome == "unknown_denom_notes":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full,
                                             force_unknown=True)
        elif outcome == "failure_to_collect_card":
            txn_no = _gen_failure_to_collect_card(w, cs, txn_no, tran_date, location, atm_id_full)

        counts[outcome] += 1
        counts["total"] += 1

    out_path = output_dir / file_name
    content = _control_preamble() + w.get_text() + "\r\n#EOL#\r\n"
    with open(out_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(content)

    cases_included = [c for c in selected_cases if counts.get(c, 0) > 0] or ["simple_withdrawal"]

    # ── Write manifest (consumed by /api/download-ej/<run_id>) ─────────────
    manifest = {
        "run_id": run_id,
        "bank_id": "idfc_euronet",
        "atm_id": atm_id_full,
        "location": location,
        "tran_date": tran_date.strftime("%Y-%m-%d"),
        "file_name": file_name,
        "file_path": str(out_path),
        "files": {"ej": file_name},
        "counts": counts,
        "cases_included": cases_included,
    }
    manifest_path = output_dir / f"manifest_ej_{run_id}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return {
        "run_id": run_id,
        "file_name": file_name,
        "atm_id": atm_id_full,
        "location": location,
        "counts": counts,
        "cases_included": cases_included,
        "continuation": {"next_txn_no": txn_no},
    }
