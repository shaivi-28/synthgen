"""
FSS Electronic Journal (EJ) Generator for IDFC First Bank ATMs (prefix IN).

Dispense-only, single INR500 cassette — same base flow as EuroNet but:
    - omits the "Card BIN group is 0" / "IDFCONUS CARDS" lines
    - OPCODE line has no leading space ("OPCODE = GB", not " OPCODE = GA")
    - branch is shown as a plain city name (no "BRANCH" suffix)
    - WITHDRAWAL / AVAIL BAL / FROM A/C fields are right-justified to fixed
      widths (amounts and account lengths vary across transactions)
    - RRN is typically a random 12-digit reference (not the TXN NO)
    - a DOUBLE TIMEOUT line followed by a line of 59 asterisks appears before
      TRANSACTION END

Reference structure:
    == MONTH DD, YYYY ATMID ==
    HHMMSS Card inserted / TRANSACTION START / ATR RECEIVED
    HHMMSS Card BIN entry ... / Customer PAN: masked
    HHMMSS PIN code entered / REQUEST SENT (OPCODE = GB) / RESPONSE RECEIVED / GENAC 2
    HHMMSS NOTES STACKED / Cash presented / Cash taken / CASH TOTAL table (500 only)
    receipt block (branch, ATM ID, masked card, TXN NO., WITHDRAWAL, FROM A/C,
    AVAIL BAL, RESPONSE CODE, RRN)
    HHMMSS Card ejected / Card taken / DOUBLE TIMEOUT ... / ***...*** / TRANSACTION END
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS / REFERENCE DATA
# ─────────────────────────────────────────────────────────────────────────────

LOCATIONS = ["KANPUR", "LUCKNOW", "AGRA", "VARANASI", "PATNA"]

CARD_PREFIXES = ["508536", "508546", "652281", "652163", "459156"]

AMOUNTS = [500, 1000, 1500, 2000, 3000, 3500, 4000, 5000, 7000,
           7500, 10000, 15000, 20000]

MONTH_MAP = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

SEPARATOR_LINE = "*" * 59

# Generic host decline response codes (varied across declined sessions) —
# the dedicated scenario codes (075/055/051/057/061/054) are handled by
# _gen_decline_with_receipt and never mixed in here.
DECLINE_CODES = ["073", "030", "100"]

# Short control-block preamble emitted once before the file's first '=='
# header (tab, SOH, backspace control bytes) — the following header's own
# tab-prefix supplies the tab that would otherwise open the next line.
_PREAMBLE = "\t  \x01  \x08   \n"

# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_header_date(dt: datetime) -> str:
    return f"{MONTH_MAP[dt.month]} {dt.day:02d}, {dt.year}"


def _fmt_slash_ts(dt: datetime) -> tuple:
    return dt.strftime("%m/%d/%Y"), dt.strftime("%H:%M:%S")


def _gen_atm_id() -> str:
    return f"21{random.randint(1000, 9999)}"


def _random_pan(prefix: str) -> tuple:
    last4 = str(random.randint(1000, 9999))
    return f"{prefix}***{last4}", f"{prefix}******{last4}"


def _random_account() -> str:
    """Variable-length account (16-19 digits), zero-padded like real bank accounts."""
    total_len = random.choice([16, 17, 18, 19])
    real_len = min(random.randint(6, 12), total_len)
    number = str(random.randint(10 ** (real_len - 1), 10 ** real_len - 1))
    return number.rjust(total_len, "0")


def _random_balance(min_bal: int = 100, max_bal: int = 200000) -> float:
    return round(random.uniform(min_bal, max_bal), 2)


def _fmt_money_field(amount: float, width: int) -> str:
    return f"{'RS.' + f'{amount:.2f}':>{width}}"


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LINE EMITTER
# ─────────────────────────────────────────────────────────────────────────────

class EJWriter:
    """IN (FSS) journal lines are tab-prefixed almost everywhere, except the
    '**TRANSACTION END**' line and the indented receipt-body block. `tab_mode`
    controls the default for event()/raw().

    The '== MMM DD, YYYY ATMID ==' marker line is NOT tied to transaction
    boundaries in the reference log — it's emitted by a periodic paper-feed
    unrelated to transaction content, landing every ~14-22 lines wherever the
    counter happens to be, except that it never interrupts an in-progress
    customer receipt (CARD NUMBER...footer block); if due while suppressed,
    it fires immediately once the receipt block ends, which is what produces
    the reference's occasional 2x/3x-sized gaps. begin_receipt()/end_receipt()
    bracket that non-interruptible span.
    """

    def __init__(self, start_dt: datetime, tran_date: datetime, atm_id_full: str):
        self._dt = start_dt
        self._lines = []
        self.tab_mode = True
        self._tran_date = tran_date
        self._atm_id_full = atm_id_full
        # Seeded so the very first marker still lands early (reference file's
        # first marker is its second line).
        self._lines_since_marker = random.randint(10, 16)
        self._marker_threshold = random.randint(14, 22)
        self._suppress_marker = 0

    def _next_ts(self, delta_secs: int = 1) -> datetime:
        self._dt += timedelta(seconds=delta_secs)
        return self._dt

    def _emit_marker(self) -> None:
        line = f"== {_fmt_header_date(self._tran_date)} {self._atm_id_full} =="
        self._lines.append(f"\t{line}" if self.tab_mode else line)
        self._lines_since_marker = 0
        self._marker_threshold = random.randint(14, 22)

    def _tick(self) -> None:
        self._lines_since_marker += 1
        if not self._suppress_marker and self._lines_since_marker >= self._marker_threshold:
            self._emit_marker()

    def event(self, text: str, delta_secs: int = 1, double_space: bool = False, tab: bool = None) -> None:
        ts = self._next_ts(delta_secs)
        sep = "  " if double_space else " "
        line = f"{ts.strftime('%H%M%S')}{sep}{text}"
        use_tab = self.tab_mode if tab is None else tab
        self._lines.append(f"\t{line}" if use_tab else line)
        self._tick()

    def raw(self, text: str = "", tab: bool = None) -> None:
        use_tab = self.tab_mode if tab is None else tab
        self._lines.append(f"\t{text}" if use_tab else text)
        self._tick()

    def begin_receipt(self) -> None:
        self._suppress_marker += 1

    def end_receipt(self) -> None:
        self._suppress_marker = max(0, self._suppress_marker - 1)
        if not self._suppress_marker and self._lines_since_marker >= self._marker_threshold:
            self._emit_marker()

    def advance(self, secs: int) -> None:
        self._dt += timedelta(seconds=secs)

    @property
    def dt(self) -> datetime:
        return self._dt

    def get_text(self) -> str:
        return "\n".join(self._lines)


# ─────────────────────────────────────────────────────────────────────────────
# CASSETTE STATE (500-denomination only)
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    def __init__(self):
        self.dispensed = random.randint(2000, 3000)
        self.rejected = random.randint(20, 80)
        self.remaining = random.randint(1500, 2500)
        # TYPE4 column (unknown denomination) — stays 0 unless the
        # unknown_denom_notes scenario bumps it. The real sample's CASH
        # TOTAL table always has exactly 5 rows (no dedicated UNKNOWN row);
        # TYPE4 is an existing-but-normally-zero column in that same table.
        self.unknown_dispensed = 0
        self.unknown_lost = 0

    def dispense(self, count: int) -> None:
        self.dispensed += count
        self.remaining = max(0, self.remaining - count)

    def table_lines(self) -> list:
        return [
            "CASH TOTAL   TYPE1 TYPE2 TYPE3 TYPE4",
            "DENOMINATION               500",
            f"DISPENSED    00000 00000 {self.dispensed:05d} {self.unknown_dispensed:05d}",
            f"REJECTED     00000 00000 {self.rejected:05d} 00000",
            f"REMAINING    00000 00000 {self.remaining:05d} 00000",
            f"LOST         00000 00000 00000 {self.unknown_lost:05d}",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _gen_card_session_opening(w: EJWriter) -> tuple:
    """Card inserted through Customer PAN, shared by every IN session type.
    Real reference dumps fire the *date*time*/TRANSACTION START/ATR RECEIVED
    trio TWICE before continuing to the Card BIN group/PAN steps — this
    reproduces that quirk. Returns (log_pan, receipt_pan) — the SAME masked
    card number for both, so the log and any printed receipt agree.
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

    date_part, time_part = _fmt_slash_ts(w.dt)
    w.event(f"*{date_part}*{time_part}*", delta_secs=0)
    w.raw("*TRANSACTION START*")
    w.raw("")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("Card BIN entry is DEFAULT", delta_secs=random.randint(1, 2))
    w.event("Card BIN group is 1", delta_secs=0)
    w.event("Card BIN entry is IDFCOFFUS CARDS", delta_secs=random.randint(2, 4))
    w.event(f"Customer PAN: {log_pan}", delta_secs=random.randint(2, 6))
    w.raw("")

    return log_pan, receipt_pan


def _gen_simple_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int,
                            tran_date: datetime, location: str,
                            atm_id_full: str, force_reject: bool = False,
                            force_unknown: bool = False) -> int:
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = str(random.randint(500000000000, 599999999999))

    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
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
    w.raw(f"{note_count}X500")
    w.event("Cash taken", delta_secs=random.randint(0, 1))

    cs.dispense(note_count)
    if force_reject:
        cs.rejected += random.randint(1, 4)
    if force_unknown:
        n = random.randint(1, 3)
        cs.unknown_dispensed += n
        cs.unknown_lost += max(1, n - random.randint(0, n))
    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))

    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw("")
    w.raw(f"TXN NO.     {txn_no}")
    w.raw("")
    w.raw(f"WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"FROM A/C:{acc_no:>23}")
    w.raw(f"AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("RESPONSE CODE              000")
    w.raw("YOUR TXN IS SUCCESSFUL")
    w.raw(f"RRN.               {rrn}")
    w.raw("GO CASH FREE!USE DEBIT CARDS")
    w.raw("NEVER SHARE YOUR CARD DETAILS")
    w.raw("AND PIN WITH ANYONE")
    w.raw("")
    w.end_receipt()

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_abandoned_session(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """Card inserted, BIN checks run, but the customer walks away before PIN
    entry. Never reaches the host, so it consumes no TXN NO."""
    w.tab_mode = True
    _gen_card_session_opening(w)

    w.event("Card ejected", delta_secs=random.randint(10, 30))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)


def _gen_pin_timeout_session(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """Card inserted, BIN checks run, PIN entry times out after 1-3 retries.
    Never reaches the host, so it consumes no TXN NO."""
    w.tab_mode = True
    _gen_card_session_opening(w)

    for _ in range(random.randint(1, 3)):
        w.event("TIMEOUT 'withdrawal-flow/pin-entry-action' after 15000ms.", delta_secs=random.randint(15, 20))

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))


def _gen_card_level_decline(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """PIN entered, request sent, GENAC AAC — but no receipt is ever printed.
    Reaches the host (the caller still advances txn_no), but nothing in the
    log shows the TXN NO. Rarer than the receipted host decline."""
    w.tab_mode = True
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Transaction Cancelled", delta_secs=0)
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)


def _gen_host_decline(w: EJWriter, txn_no: int, tran_date: datetime,
                       location: str, atm_id_full: str) -> int:
    """PIN entered, request sent, host declines (GENAC AAC) — no cash
    dispensed. Still reaches the host, so it consumes a TXN NO."""
    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)
    decline_code = random.choice(DECLINE_CODES)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))

    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw(f"TXN NO:               {txn_no}")
    w.raw("")
    w.raw("#SAVINGS")
    w.raw("")
    w.raw("")
    w.raw("SORRY UNABLE TO PROCESS")
    w.raw("")
    w.raw(f"RESPONSE CODE              {decline_code}")
    w.raw("")
    w.raw("RRN.")
    w.raw("IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("------------------------------------")
    w.raw("IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("PLEASE DIAL +975-2-332540 AND")
    w.raw("HELP US TO SERVE YOU BETTER")
    w.raw("")
    w.end_receipt()

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_decline_with_receipt(w: EJWriter, txn_no: int, tran_date: datetime,
                               location: str, atm_id_full: str,
                               response_code: str,
                               before_pin: bool = False) -> int:
    """Generic parameterized decline-with-receipt, used for the PIN-stage and
    post-request decline variants that share the same shape as _gen_host_decline
    but differ only in response code. If before_pin, the decline happens at
    the BIN/card-read stage (no PIN/REQUEST SENT/GENAC at all — a local ATM
    decision) instead of after the host round-trip. Always consumes a TXN NO
    (the ATM still prints a numbered receipt). The response code alone
    signals the scenario — no descriptive text line follows it, matching the
    reference: RESPONSE CODE goes straight to a blank line, then RRN."""
    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    if not before_pin:
        w.event("PIN code entered", delta_secs=random.randint(15, 30))
        w.event("", delta_secs=random.randint(5, 10))
        w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
        w.raw("OPCODE = GB")
        w.raw("")

        w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
        w.raw("")

        w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
        w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))

    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw(f"TXN NO:               {txn_no}")
    w.raw("")
    w.raw("#SAVINGS")
    w.raw("")
    w.raw("")
    w.raw("SORRY UNABLE TO PROCESS")
    w.raw("")
    w.raw(f"RESPONSE CODE              {response_code}")
    w.raw("")
    w.raw("RRN.")
    w.raw("IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("------------------------------------")
    w.raw("IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("PLEASE DIAL +975-2-332540 AND")
    w.raw("HELP US TO SERVE YOU BETTER")
    w.raw("")
    w.end_receipt()

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_balance_inquiry(w: EJWriter, txn_no: int, tran_date: datetime,
                          location: str, atm_id_full: str) -> int:
    """Balance inquiry — succeeds most of the time (GENAC TC, AVAILABLE BAL
    populated, nothing dispensed) but can also be declined like a withdrawal
    (GENAC AAC). Always reaches the host, consumes a TXN NO either way."""
    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = CB   C")
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    if random.random() < 0.85:
        w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
        w.raw("")

        w.begin_receipt()
        receipt_time = w.dt.strftime("%H:%M")
        receipt_date = w.dt.strftime("%d/%m/%y")
        w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))

        w.raw("")
        w.raw("")
        w.raw("DATE       TIME       ATM ID")
        w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
        w.raw("")
        w.raw(f"CARD NUMBER:  {receipt_pan}")
        w.raw(f"TXN NO.     {txn_no}")
        w.raw("BAL.INQUIRY     #SAVINGS")
        w.raw(f"FROM A/C:{'*' * 19:>23}")
        w.raw(f"AVAILABLE BAL{_fmt_money_field(_random_balance(), 18)}")
        w.raw("RESPONSE CODE              000")
        w.raw("")
        w.raw("YOUR TXN IS SUCCESSFUL")
        rrn = str(random.randint(500000000000, 599999999999))
        w.raw(f"RRN.               {rrn}")
        w.raw("IDFC... YOUR PARTNER IN GROWTH!")
        w.raw("------------------------------------")
        w.raw("IF YOU DON'T FIND THE ATM SITE CLEAN")
        w.raw("PLEASE DIAL +975-2-332540 AND")
        w.raw("HELP US TO SERVE YOU BETTER")
        w.raw("")
        w.end_receipt()

        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.raw(SEPARATOR_LINE)
        w.event("**TRANSACTION END**", delta_secs=0, tab=False)
        return txn_no + 1

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))

    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw(f"TXN NO:               {txn_no}")
    w.raw("")
    w.raw("#SAVINGS")
    w.raw("")
    w.raw("")
    w.raw("SORRY UNABLE TO PROCESS")
    w.raw("")
    w.raw(f"RESPONSE CODE              {random.choice(DECLINE_CODES)}")
    w.raw("")
    w.raw("RRN.")
    w.raw("IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("------------------------------------")
    w.raw("IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("PLEASE DIAL +975-2-332540 AND")
    w.raw("HELP US TO SERVE YOU BETTER")
    w.raw("")
    w.end_receipt()

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_host_timeout(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """PIN entered, request sent, the link drops and recovers, then the host
    never responds at all — no RESPONSE RECEIVED, no receipt block (real
    evidence from a EuroNet-family sample). Never reaches the host
    successfully, so no TXN NO is consumed."""
    w.tab_mode = True
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
    w.raw("")

    w.event("LINK1 Fatal [Closed]", delta_secs=random.randint(3, 8))
    w.event("LINK1 Healthy [Open]", delta_secs=random.randint(2, 5))
    w.event("HOST TX TIMEOUT", delta_secs=random.randint(5, 15), double_space=True)
    w.raw("")
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)


def _gen_power_cut(w: EJWriter) -> None:
    """Mid-day power cut / device reinit — not a customer transaction,
    inserted between transactions, no TXN NO consumed. The ATM drops out of
    service, the cabinet door cycles as devices are checked, several devices
    come back Fatal/uninitialized first, and then recover to Healthy one by
    one before the ATM returns to service (real-evidence pattern, e.g. the
    'Out of Service'/'-From Host' .. 'In Service' bracket)."""
    w.tab_mode = True
    w.event("Out of Service", delta_secs=random.randint(1, 3))
    w.raw("-From Host")
    w.raw("")
    w.event("CABINET DOOR: OPEN", delta_secs=random.randint(2, 6))
    w.event("MAG Fatal [90/-1/-1/-1]", delta_secs=random.randint(1, 3))
    w.raw("Device Not Initialized")
    w.event("CASH Fatal [90/-1/-1/-1]", delta_secs=random.randint(1, 3))
    w.raw("Device Not Initialized")
    w.event("CABINET DOOR: CLOSED", delta_secs=random.randint(10, 30))
    w.event("OPERATOR SWITCH: RUN", delta_secs=0)
    w.event("MAG Healthy [0/0/-100/0]", delta_secs=random.randint(2, 5))
    w.event("CASH Healthy [0/0/50/0]", delta_secs=random.randint(2, 5))
    w.event("SENS Healthy [0/0/-1/-1]", delta_secs=random.randint(1, 3))
    w.event("RCPT Healthy [0/0/50/-100]", delta_secs=random.randint(1, 3))
    w.event("In Service", delta_secs=random.randint(2, 5))


def _gen_admin_cassette(w: EJWriter) -> None:
    """Cassette replenishment / cart-swap maintenance session (real-evidence
    formatting) — not a customer transaction, no TXN NO, ATM effectively out
    of service for the duration. Inserted between transactions."""
    w.tab_mode = True
    w.event("CABINET DOOR: OPEN", delta_secs=random.randint(2, 6))
    w.event("OPERATOR SWITCH: SUPERVISOR", delta_secs=0)
    w.event("Operator Login", delta_secs=random.randint(2, 5))
    w.raw("")
    w.event("FINANCIAL_REPLENISHMENT", delta_secs=random.randint(60, 240))
    w.event("CASHREPLENMENU", delta_secs=random.randint(3, 8))
    w.event("MACHINESUBTOTALS", delta_secs=random.randint(3, 8))
    w.raw("NUM    CURR    TOT  DEP   LEFT   DISP")
    w.raw("3  INR500   4706    0   1891   2749")
    w.raw("")
    w.raw("NUM    CURR   RJCT   LOST")
    w.raw("3  INR500     66      0")
    w.raw("")
    w.event("CASHREPLENMENU", delta_secs=random.randint(10, 30))
    w.event("REPLACECASH", delta_secs=random.randint(3, 8))
    w.raw("CASH DISPENSER TOTALS")
    w.raw("NUM    CURR    TOT  DEP   LEFT   DISP")
    w.raw("3  INR500   4706    0   1891   2749")
    w.raw("")
    w.raw("NUM    CURR   RJCT   LOST")
    w.raw("3  INR500     66      0")
    w.raw("")
    w.event("CART 1 REMOVED", delta_secs=random.randint(60, 200))
    w.event("CART 2 REMOVED", delta_secs=random.randint(2, 5))
    w.event("CART 1 INSERTED", delta_secs=random.randint(10, 30))
    w.event("CART 2 INSERTED", delta_secs=random.randint(3, 8))
    w.event("CABINET DOOR: CLOSED", delta_secs=random.randint(3, 8))
    w.event("OPERATOR SWITCH: RUN", delta_secs=0)
    w.raw("CASH DISPENSER TOTALS")
    w.raw("NUM    CURR    TOT  DEP   LEFT   DISP")
    w.raw("3  INR500      0    0      0      0")
    w.raw("")
    w.raw("NUM    CURR   RJCT   LOST")
    w.raw("3  INR500      0      0")
    w.raw("")
    w.event("Operator Exiting", delta_secs=random.randint(2, 6))


def _gen_cash_not_taken(w: EJWriter, cs: CassetteState, txn_no: int,
                        tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Full successful withdrawal (host round-trip completes, receipt
    prints), but the customer takes the card and forgets the cash — a
    retract event fires instead of a clean ending. Consumes a TXN NO."""
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = str(random.randint(500000000000, 599999999999))

    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
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
    w.raw(f"{note_count}X500")

    cs.dispense(note_count)
    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw("")
    w.raw(f"TXN NO.     {txn_no}")
    w.raw("")
    w.raw(f"WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"FROM A/C:{acc_no:>23}")
    w.raw(f"AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("RESPONSE CODE              000")
    w.raw("YOUR TXN IS SUCCESSFUL")
    w.raw(f"RRN.               {rrn}")
    w.raw("GO CASH FREE!USE DEBIT CARDS")
    w.raw("NEVER SHARE YOUR CARD DETAILS")
    w.raw("AND PIN WITH ANYONE")
    w.raw("")
    w.end_receipt()

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("NOTES RETRACTED", delta_secs=random.randint(20, 40))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_partial_split_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int,
                                   tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Host dispenses the withdrawal in two physical batches within one
    authorization (extrapolated). Consumes 1 TXN NO."""
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    split1 = max(1, note_count // 2)
    split2 = note_count - split1
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = str(random.randint(500000000000, 599999999999))

    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
    w.raw("")
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")
    w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    total_notes = 0
    for batch in (split1, split2):
        if batch <= 0:
            continue
        w.event("NOTES STACKED", delta_secs=random.randint(5, 12), double_space=True)
        w.raw("")
        w.event("Cash presented", delta_secs=random.randint(5, 10))
        w.event(f"NOTES PRESENTED 0,0,{batch},0", delta_secs=random.randint(0, 1), double_space=True)
        w.raw("")
        w.event(f"{batch * 500} INR", delta_secs=0)
        w.raw(f"{batch}X500")
        cs.dispense(batch)
        total_notes += batch

    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw("")
    w.raw(f"TXN NO.     {txn_no}")
    w.raw("")
    w.raw(f"WITHDRAWAL{_fmt_money_field(total_notes * 500, 20)}")
    w.raw(f"FROM A/C:{acc_no:>23}")
    w.raw(f"AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("RESPONSE CODE              000")
    w.raw("YOUR TXN IS SUCCESSFUL")
    w.raw(f"RRN.               {rrn}")
    w.raw("GO CASH FREE!USE DEBIT CARDS")
    w.raw("NEVER SHARE YOUR CARD DETAILS")
    w.raw("AND PIN WITH ANYONE")
    w.raw("")
    w.end_receipt()

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


def _gen_failure_to_collect_card(w: EJWriter, cs: CassetteState, txn_no: int,
                                  tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Full successful withdrawal, but the card is captured/retained instead
    of being taken by the customer (extrapolated). Consumes a TXN NO."""
    amount = random.choice(AMOUNTS)
    note_count = amount // 500
    acc_no = _random_account()
    avail_bal_after = _random_balance()
    rrn = str(random.randint(500000000000, 599999999999))

    w.tab_mode = True
    _, receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw("OPCODE = GB")
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
    w.raw(f"{note_count}X500")
    w.event("Cash taken", delta_secs=random.randint(0, 1))

    cs.dispense(note_count)
    tbl = cs.table_lines()
    w.event(tbl[0], delta_secs=0)
    for line in tbl[1:]:
        w.raw(line)
    w.raw("")

    w.begin_receipt()
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    {location}", delta_secs=random.randint(1, 3))
    w.raw("")
    w.raw("")
    w.raw("DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}", tab=False)
    w.raw("")
    w.raw(f"CARD NUMBER:  {receipt_pan}")
    w.raw("")
    w.raw(f"TXN NO.     {txn_no}")
    w.raw("")
    w.raw(f"WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"FROM A/C:{acc_no:>23}")
    w.raw(f"AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("RESPONSE CODE              000")
    w.raw("YOUR TXN IS SUCCESSFUL")
    w.raw(f"RRN.               {rrn}")
    w.raw("GO CASH FREE!USE DEBIT CARDS")
    w.raw("NEVER SHARE YOUR CARD DETAILS")
    w.raw("AND PIN WITH ANYONE")
    w.raw("")
    w.end_receipt()

    w.event("Card retained", delta_secs=random.randint(1, 3))
    w.raw(SEPARATOR_LINE)
    w.event("**TRANSACTION END**", delta_secs=0, tab=False)

    return txn_no + 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_fss_ej(
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
    continuation: dict = None,
) -> dict:
    """
    Generate an FSS EJ file for IDFC First Bank ATMs.

    Args:
        tran_date: transaction date
        num_transactions: number of customer transactions
        selected_cases: list of case IDs to include (currently: 'simple_withdrawal')
        atm_id: numeric ATM ID string, e.g. '212730' (auto-generated if None)
        location: branch/city name (random if None)
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
    atm_id_full = f"IN{atm_id}"
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # File naming: IN{ATM_ID}_{DDMMYYYY}.txt
    date_str_file = tran_date.strftime("%d%m%Y")
    file_name = f"{atm_id_full}_{date_str_file}.txt"

    # This ATM is withdrawal-only and every generated file must include all
    # 16 required scenario types at least once — build a schedule that
    # guarantees each appears, then pad it out to num_transactions with
    # weighted-random extras (including a few non-required flavor cases for
    # realism) and shuffle the order.
    REQUIRED_SCENARIOS = [
        "simple_withdrawal", "balance_inquiry", "host_timeout",
        "cash_not_taken", "notes_in_reject", "admin_cassette", "power_cut",
        "pin_tries_exceeded", "invalid_pin",
        "declined_insufficient_funds", "declined_unauthorized_card",
        "daily_limit_exceeded", "card_expired",
        "partial_split_transaction", "unknown_denom_notes",
        "failure_to_collect_card",
    ]
    PADDING_SCENARIOS = [
        "simple_withdrawal", "abandoned", "pin_timeout",
        "host_decline", "card_decline", "balance_inquiry",
    ]
    PADDING_WEIGHTS = [42, 20, 19, 12, 3, 8]

    if num_transactions < len(REQUIRED_SCENARIOS):
        num_transactions = len(REQUIRED_SCENARIOS)

    schedule = list(REQUIRED_SCENARIOS)
    extra_needed = num_transactions - len(schedule)
    if extra_needed > 0:
        schedule += random.choices(PADDING_SCENARIOS, weights=PADDING_WEIGHTS, k=extra_needed)
    random.shuffle(schedule)

    start_dt = tran_date.replace(hour=5, minute=30, second=0, microsecond=0)
    w = EJWriter(start_dt, tran_date, atm_id_full)
    cs = CassetteState()

    if continuation and continuation.get("next_txn_no") is not None:
        txn_no = continuation["next_txn_no"]
    else:
        txn_no = random.randint(4000, 9000)
    counts = {
        "total": 0, "simple_withdrawal": 0, "abandoned": 0, "pin_timeout": 0,
        "host_decline": 0, "card_decline": 0,
        "balance_inquiry": 0, "host_timeout": 0,
        "pin_tries_exceeded": 0, "invalid_pin": 0,
        "declined_insufficient_funds": 0, "declined_unauthorized_card": 0,
        "daily_limit_exceeded": 0, "card_expired": 0,
        "cash_not_taken": 0, "notes_in_reject": 0,
        "partial_split_transaction": 0, "unknown_denom_notes": 0,
        "failure_to_collect_card": 0, "power_cut": 0, "admin_cassette": 0,
    }

    # Abandoned/timeout/card_decline/host_timeout/before-PIN declines never
    # show a TXN NO in the log. power_cut/admin_cassette are inter-transaction
    # device events, not customer transactions — no TXN NO, not counted in
    # counts["total"]. Everything else advances the shared TXN NO counter
    # since it either reaches the host or prints a numbered local-decision
    # receipt.
    for outcome in schedule:
        w.advance(random.randint(60, 900))

        if outcome == "simple_withdrawal":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "abandoned":
            _gen_abandoned_session(w, tran_date, atm_id_full)
        elif outcome == "pin_timeout":
            _gen_pin_timeout_session(w, tran_date, atm_id_full)
        elif outcome == "host_decline":
            txn_no = _gen_host_decline(w, txn_no, tran_date, location, atm_id_full)
        elif outcome == "card_decline":
            _gen_card_level_decline(w, tran_date, atm_id_full)
            txn_no += 1
        elif outcome == "balance_inquiry":
            txn_no = _gen_balance_inquiry(w, txn_no, tran_date, location, atm_id_full)
        elif outcome == "host_timeout":
            _gen_host_timeout(w, tran_date, atm_id_full)
        elif outcome == "pin_tries_exceeded":
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full, response_code="075")
        elif outcome == "invalid_pin":
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full, response_code="055")
        elif outcome == "declined_insufficient_funds":
            # RESPONSE CODE 051 is INVENTED/UNVERIFIED — no real-evidence
            # sample shows a decline at the amount-entry stage.
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full, response_code="051")
        elif outcome == "declined_unauthorized_card":
            # RESPONSE CODE 057 is INVENTED/UNVERIFIED — no real-evidence
            # sample shows a BIN-stage decline before PIN entry.
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full,
                response_code="057", before_pin=True)
        elif outcome == "daily_limit_exceeded":
            # RESPONSE CODE 061 is INVENTED/UNVERIFIED.
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full, response_code="061")
        elif outcome == "card_expired":
            # RESPONSE CODE 054 is INVENTED/UNVERIFIED.
            txn_no = _gen_decline_with_receipt(
                w, txn_no, tran_date, location, atm_id_full,
                response_code="054", before_pin=True)
        elif outcome == "cash_not_taken":
            txn_no = _gen_cash_not_taken(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "notes_in_reject":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full,
                                             force_reject=True)
        elif outcome == "partial_split_transaction":
            txn_no = _gen_partial_split_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "unknown_denom_notes":
            txn_no = _gen_simple_withdrawal(w, cs, txn_no, tran_date, location, atm_id_full,
                                             force_unknown=True)
        elif outcome == "failure_to_collect_card":
            txn_no = _gen_failure_to_collect_card(w, cs, txn_no, tran_date, location, atm_id_full)
        elif outcome == "power_cut":
            _gen_power_cut(w)
        elif outcome == "admin_cassette":
            _gen_admin_cassette(w)

        counts[outcome] += 1
        if outcome not in ("power_cut", "admin_cassette"):
            counts["total"] += 1

    out_path = output_dir / file_name
    content = _PREAMBLE + w.get_text() + "\n\t#EOL#\n\n"
    with open(out_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(content)

    cases_included = [c for c in counts if c != "total" and counts[c] > 0]

    manifest = {
        "run_id": run_id,
        "bank_id": "idfc",
        "vendor": "fss",
        "atm_type": "IN",
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
