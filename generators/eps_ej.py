"""
EPS Electronic Journal (EJ) Generator for IDFC First Bank ATMs.

Covers two EPS ATM types, both nearly identical to the EuroNet format but using
OPCODE = GB and a DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' line
before TRANSACTION END:
    - PN: dispense-only, single denomination cassette not required — dispenses
      a mix of INR100/INR200/INR500 notes.
    - PR: recycler, same flow/format as PN.

Reference structure:
    == MONTH DD, YYYY ATMID ==
    HHMMSS Card inserted / TRANSACTION START / ATR RECEIVED
    HHMMSS Card BIN entry ... / Customer PAN: masked
    HHMMSS PIN code entered / REQUEST SENT (OPCODE = GB) / RESPONSE RECEIVED / GENAC 2
    HHMMSS NOTES STACKED / Cash presented / Cash taken / CASH TOTAL table (100/200/500)
    receipt block (branch, ATM ID, masked card, TXN NO., WITHDRAWAL, FROM A/C,
    AVAIL BAL, RESPONSE CODE, RRN)
    HHMMSS Card ejected / Card taken / DOUBLE TIMEOUT ... / TRANSACTION END
"""

import json
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS / REFERENCE DATA
# ─────────────────────────────────────────────────────────────────────────────

LOCATIONS = [
    "KAROL BAGH BRANCH",
    "GAYATRI NAGAR BRANCH",
    "DARYAGANJ BRANCH",
    "SAKET BRANCH",
    "NEHRU PLACE BRANCH",
]

CARD_PREFIXES = ["498759", "401138", "607098", "401613", "652163"]

# Withdrawal amounts — multiples of 500; denomination split may hold back a
# 500 note in favor of a 100/200 mix, matching the reference variability.
AMOUNTS = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
           6000, 7000, 7500, 8000, 9000, 10000, 15000, 20000]

CUSTOMER_NAMES = [
    "RAHUL SHARMA", "PRIYA VERMA", "AMIT SINGH", "SUNITA RAO", "VIKRAM MEHTA",
    "ANITA DESAI", "SANJAY GUPTA", "NEHA JOSHI", "ROHIT NAIR", "KAVITA IYER",
]

# Deposit note counts (Rs.500 notes only — recycler deposit cassette denomination)
DEPOSIT_NOTE_COUNTS = list(range(4, 41))

MONTH_MAP = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Per ATM-type quirks observed in reference dumps
ATM_TYPE_CONFIG = {
    "PN": {
        "atm_id_prefix": "20",
        "opcode_line": " OPCODE = GB",
        "denom_order": [500, 100, 200],
        "rrn_style": "long",   # majority pattern: random 12-digit RRN
    },
    "PR": {
        "atm_id_prefix": "80",
        "opcode_line": " OPCODE = GB     A",
        "denom_order": [100, 200, 500],
        "rrn_style": "txn_no",  # majority pattern: RRN == TXN NO, padded
    },
}

# Real PN decline sessions use varied opcode suffixes unrelated to outcome —
# GENAC (TC vs AAC) is what actually signals success vs. decline — and varied
# host response codes.
PN_DECLINE_OPCODES = [" OPCODE = GB", " OPCODE = GB   C", " OPCODE = GB     A", " OPCODE = GB   C A"]
PN_DECLINE_CODES = ["073", "055", "075", "100"]

# Response-code conventions differ by real-evidence reference sample:
# PN (PN201090_02052025.txt): 000/055/073/075/100 — only 055 ever prints a
# message line ("INVALID PIN"); every other code's message line is blank.
# PR (PR803920_03052025.txt): 000/051/030/100 — code 100 DOES carry a message
# ("UNABLE TO PROCESS"), 051/030 are blank. 075="PIN TRIES EXCEEDED"/
# 055="INVALID PIN" are shared real evidence from elsewhere in this vendor
# family, reused for both terminal types. insufficient_funds/unauthorized_card/
# daily_limit_exceeded have no dedicated code in PN's native vocabulary (reuse
# 100, no message) but DO for PR, per explicit user-confirmed mapping across
# multiple real-file-consistency passes (051/057/061 respectively).
DECLINE_CODE_CONFIG = {
    "PN": {
        "generic_pool": ["073", "055", "075", "100"],
        "insufficient_funds": "100",
        "unauthorized_card": "100",
        "daily_limit_exceeded": "100",
        "card_expired": "054",
        "message_codes": {"055": "INVALID PIN"},
    },
    "PR": {
        "generic_pool": ["051", "030", "100"],
        "insufficient_funds": "051",
        "unauthorized_card": "057",
        "daily_limit_exceeded": "061",
        "card_expired": "054",
        "message_codes": {"055": "INVALID PIN", "100": "UNABLE TO PROCESS       "},
    },
}

# Fixed 2048-byte printer control-block record emitted once before the file's
# first '==' header — vendor-specific lead control bytes, space-padded, CRLF-terminated.
_PREAMBLE_PREFIX = {
    "PN": "(w\x04  \x08   \x03",
    "PR": "\x03N\x02  \x08   \x03",
}


def _control_preamble(atm_type: str) -> str:
    prefix = _PREAMBLE_PREFIX[atm_type]
    return prefix + " " * (2048 - len(prefix) - 2) + "\r\n"

# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_header_date(dt: datetime) -> str:
    return f"{MONTH_MAP[dt.month]} {dt.day:02d}, {dt.year}"


def _fmt_slash_ts(dt: datetime) -> tuple:
    return dt.strftime("%m/%d/%Y"), dt.strftime("%H:%M:%S")


def _gen_atm_id(atm_type: str) -> str:
    prefix = ATM_TYPE_CONFIG[atm_type]["atm_id_prefix"]
    return f"{prefix}{random.randint(1000, 9999)}"


def _random_pan(prefix: str) -> tuple:
    last4 = str(random.randint(1000, 9999))
    return f"{prefix}***{last4}", f"{prefix}******{last4}"


def _random_customer_name() -> str:
    return random.choice(CUSTOMER_NAMES)


def _random_account() -> str:
    """Variable-length account (16-19 digits), zero-padded like real bank accounts."""
    total_len = random.choice([16, 17, 18, 19])
    real_len = min(random.randint(6, 12), total_len)
    number = str(random.randint(10 ** (real_len - 1), 10 ** real_len - 1))
    return number.rjust(total_len, "0")


def _random_note_serial() -> str:
    """Banknote serial number: 1 digit + 2 letters (occasionally an
    OCR-unreadable '?', matching real reference data) + 6 digits, e.g. '3BD884457'."""
    lead_digit = random.choice(string.digits)
    mid = "".join("?" if random.random() < 0.05 else random.choice(string.ascii_uppercase) for _ in range(2))
    tail_digits = "".join(random.choices(string.digits, k=6))
    return f"{lead_digit}{mid}{tail_digits}"


def _progressive_counts(total_count: int, steps: int) -> list:
    """Split total_count into `steps` positive increments, returning cumulative
    running totals climbing to total_count (mimics progressive note re-validation)."""
    if steps <= 1 or total_count <= 1:
        return [total_count]
    parts = []
    remaining = total_count
    for i in range(steps - 1):
        max_take = remaining - (steps - 1 - i)
        take = random.randint(1, max(1, max_take))
        parts.append(take)
        remaining -= take
    parts.append(remaining)
    cumulative = []
    running = 0
    for p in parts:
        running += p
        cumulative.append(running)
    return cumulative


def _random_balance(min_bal: int = 500, max_bal: int = 990000) -> float:
    return round(random.uniform(min_bal, max_bal), 2)


def _fmt_money_field(amount: float, width: int) -> str:
    """Right-justify 'RS.amount' within a fixed total width."""
    return f"{'RS.' + f'{amount:.2f}':>{width}}"


def _denom_split(amount: int) -> tuple:
    """Split amount into (n100, n200, n500), occasionally holding back a 500
    note in favor of smaller denominations (matches reference variability)."""
    n500 = amount // 500
    rem = amount % 500
    if n500 > 0 and random.random() < 0.4:
        n500 -= 1
        rem += 500
    n200 = rem // 200
    rem -= n200 * 200
    n100 = rem // 100
    return n100, n200, n500


def _split_amount_into_cycles(amount: int, cycles: int) -> list:
    """Split `amount` into up to `cycles` positive Rs.500-multiple parts that
    sum back to `amount`, for a partial/split-transaction dispense loop."""
    units = amount // 500
    cycles = max(1, min(cycles, units)) if units else 1
    base = units // cycles
    rem = units % cycles
    parts = [(base + (1 if i < rem else 0)) * 500 for i in range(cycles)]
    return [p for p in parts if p > 0] or [amount]


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LINE EMITTER
# ─────────────────────────────────────────────────────────────────────────────

class EJWriter:
    def __init__(self, start_dt: datetime):
        self._dt = start_dt
        self._lines = []

    def _next_ts(self, delta_secs: int = 1) -> datetime:
        self._dt += timedelta(seconds=delta_secs)
        return self._dt

    def event(self, text: str, delta_secs: int = 1, double_space: bool = False) -> None:
        ts = self._next_ts(delta_secs)
        sep = "  " if double_space else " "
        self._lines.append(f"{ts.strftime('%H%M%S')}{sep}{text}")

    def raw(self, text: str = "") -> None:
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
# CASSETTE STATE (100 / 200 / 500 denominations)
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    def __init__(self):
        self.dispensed = {100: random.randint(150, 500), 200: random.randint(50, 200), 500: random.randint(3000, 4000)}
        self.rejected = {100: random.randint(1, 50), 200: random.randint(1, 10), 500: random.randint(10, 60)}
        self.remaining = {100: random.randint(1000, 2000), 200: random.randint(500, 1200), 500: random.randint(150, 900)}
        self.lost = {100: 0, 200: 0, 500: 0}
        # TYPE4 column (unknown denomination) — stays 0 unless
        # unknown_denom_notes bumps it. Per vendor convention, an
        # unidentified note ends up in the LOST column, not counted as
        # successfully DISPENSED.
        self.unknown_lost = 0

    def dispense(self, n100: int, n200: int, n500: int) -> None:
        self.dispensed[100] += n100
        self.dispensed[200] += n200
        self.dispensed[500] += n500
        self.remaining[100] = max(0, self.remaining[100] - n100)
        self.remaining[200] = max(0, self.remaining[200] - n200)
        self.remaining[500] = max(0, self.remaining[500] - n500)

    def deposit(self, n100: int, n200: int, n500: int) -> None:
        self.remaining[100] += n100
        self.remaining[200] += n200
        self.remaining[500] += n500

    def bump_lost(self, n100: int, n200: int, n500: int) -> None:
        """Notes that were dispensed but never collected (cash_not_taken)
        end up in the LOST column for their real denomination."""
        self.lost[100] += n100
        self.lost[200] += n200
        self.lost[500] += n500

    def bump_unknown(self, count: int) -> None:
        """Force a nonzero TYPE4 (unknown-denomination) LOST reading — an
        unidentified note the dispenser couldn't classify."""
        self.unknown_lost += count

    def table_lines(self) -> list:
        return [
            "CASH TOTAL   TYPE1 TYPE2 TYPE3 TYPE4",
            "DENOMINATION   100   200   500      ",
            f"DISPENSED    {self.dispensed[100]:05d} {self.dispensed[200]:05d} {self.dispensed[500]:05d} 00000",
            f"REJECTED     {self.rejected[100]:05d} {self.rejected[200]:05d} {self.rejected[500]:05d} 00000",
            f"REMAINING    {self.remaining[100]:05d} {self.remaining[200]:05d} {self.remaining[500]:05d} 00000",
            f"LOST         {self.lost[100]:05d} {self.lost[200]:05d} {self.lost[500]:05d} {self.unknown_lost:05d}",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _gen_card_session_opening(w: EJWriter, mid_header: tuple = None) -> str:
    """Card inserted through Customer PAN, shared by every PN/PR card-present
    flow. Real reference dumps fire the *date*time*/TRANSACTION START/ATR
    RECEIVED trio TWICE before continuing to the BIN group/PAN steps — this
    reproduces that quirk. Returns the receipt-format masked PAN so the
    caller prints the SAME card number that was scanned at login (instead of
    re-rolling a fresh mask that wouldn't match the logged one).

    If `mid_header` is (tran_date, atm_id_full), an extra '==' banner is
    emitted right after 'Card BIN group is 1' (matches the deposit flow's
    established mid-transaction header placement).
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
    if mid_header:
        w.header(*mid_header)
    w.event("Card BIN entry is IDFCOFFUS CARDS", delta_secs=random.randint(2, 4))
    w.event("Card BIN group is 0", delta_secs=random.randint(2, 4))
    w.event("Card BIN entry is IDFCONUS CARDS", delta_secs=0)
    w.event(f"Customer PAN: {log_pan}", delta_secs=0)
    w.raw("")

    return receipt_pan


_PICK_FAILURE_CART = {100: "CART001", 200: "CART002", 500: "CART003"}


def _gen_suspected_pick_failure(w: EJWriter) -> dict:
    """REAL EVIDENCE (PR803920_03052025.txt, 26 instances): a 'Suspected Pick
    Failure' event immediately followed by one or more
    '- N Bills from CARTxxx (INR,denom)' lines — the ONLY mechanism by which
    REJECTED ever increases in the real reference file. Returns the
    denom -> bill-count map so the caller can bump CassetteState.rejected
    by the exact same amounts (REJECTED must never move without this event
    backing it)."""
    denoms = random.sample([100, 200, 500], k=random.randint(1, 2))
    counts = {}
    w.event("Suspected Pick Failure", delta_secs=random.randint(3, 8))
    for d in denoms:
        n = random.randint(1, 4)
        counts[d] = n
        w.raw(f" - {n} Bills from {_PICK_FAILURE_CART[d]} (INR,{d})")
    return counts


def _gen_simple_withdrawal(w: EJWriter, cs: CassetteState, atm_type: str, txn_no: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            split_cycles: int = 1, force_reject: bool = False,
                            force_unknown: bool = False, ending: str = "normal") -> int:
    """ending: 'normal' (default) | 'cash_not_taken' (dispensed notes are
    retracted instead of collected — vendor TIMEOUT + 'Notes retracted',
    LOST column bumped) | 'card_not_collected' (card is retracted instead
    of taken — vendor TIMEOUT + 'Card retracted', no 'Card taken' line at
    all). split_cycles>1 repeats the dispense cycle that many times before
    a SINGLE final 'Cash taken'/ending event (partial/split transaction).
    force_reject/force_unknown bump the REJECTED / TYPE4 (unknown-denom
    LOST) cassette columns so this transaction's CASH TOTAL table shows a
    nonzero reading there. All defaults reproduce the original behavior."""
    cfg = ATM_TYPE_CONFIG[atm_type]
    amount = random.choice(AMOUNTS)
    acc_no = _random_account()
    avail_bal_after = _random_balance()

    w.header(tran_date, atm_id_full)
    receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(cfg["opcode_line"])
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : TC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    cycle_amounts = _split_amount_into_cycles(amount, split_cycles) if split_cycles > 1 else [amount]
    total_n100 = total_n200 = total_n500 = 0
    reject_counts = None
    for i, cyc_amount in enumerate(cycle_amounts):
        cn100, cn200, cn500 = _denom_split(cyc_amount)
        total_n100 += cn100
        total_n200 += cn200
        total_n500 += cn500

        if i == 0 and force_reject and atm_type == "PR":
            # REAL EVIDENCE (PR803920): REJECTED only ever increases via this
            # event — placed right before the dispense it applies to.
            reject_counts = _gen_suspected_pick_failure(w)

        w.event("NOTES STACKED", delta_secs=random.randint(5, 12), double_space=True)
        w.raw("")

        w.event("Cash presented", delta_secs=random.randint(5, 10))
        w.event(f"NOTES PRESENTED {cn100},{cn200},{cn500},0", delta_secs=random.randint(0, 1), double_space=True)
        w.raw("")

        w.event(f"{cyc_amount} INR", delta_secs=0)
        breakdown = "".join(f"{n}X{d} " for d, n in
                             [(d, {500: cn500, 100: cn100, 200: cn200}[d]) for d in cfg["denom_order"]]
                             if {500: cn500, 100: cn100, 200: cn200}[d] > 0)
        w.raw(breakdown)

    # A single Cash-taken/ending event after ALL cycles, not per-cycle —
    # matches the vendor's "two consecutive dispense cycles... before a
    # single 'Cash taken'" template for partial/split transactions.
    if ending == "cash_not_taken":
        w.event("TIMEOUT 'dispense-flow/notes-taken-action' after 30000ms.", delta_secs=random.randint(28, 32))
        w.event("Notes retracted", delta_secs=random.randint(1, 3))
    else:
        w.event("Cash taken", delta_secs=random.randint(0, 1))

    n100, n200, n500 = total_n100, total_n200, total_n500
    cs.dispense(n100, n200, n500)
    if ending == "cash_not_taken":
        cs.bump_lost(n100, n200, n500)
    if force_reject:
        if reject_counts is not None:
            for d, n in reject_counts.items():
                cs.rejected[d] += n
        else:
            cs.rejected[100] += random.randint(1, 3)
            cs.rejected[200] += random.randint(0, 2)
            cs.rejected[500] += random.randint(1, 4)
    if force_unknown:
        cs.bump_unknown(random.randint(1, 3))
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
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal_after, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    if cfg["rrn_style"] == "long":
        rrn = str(random.randint(500000000000, 599999999999))
        w.raw(f"    RRN.               {rrn}")
    else:
        w.raw(f"    RRN.               {txn_no}        ")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")
    w.raw("")

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    if ending == "card_not_collected":
        w.event("TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(10, 12))
        w.event("Card retracted", delta_secs=random.randint(1, 3))
    else:
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_pn_abandoned_after_pin(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """PN: customer enters PIN but walks away before selecting an amount —
    no host request is ever sent, so no TXN NO is consumed."""
    w.header(tran_date, atm_id_full)
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.header(tran_date, atm_id_full)

    if random.random() < 0.4:
        w.event("TIMEOUT 'withdrawal-flow/amount-selection-action'", delta_secs=random.randint(15, 25))
        w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    else:
        w.event("Transaction Cancelled", delta_secs=random.randint(15, 25))

    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)


def _gen_pn_card_level_decline(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """PN: PIN entered, host request sent, GENAC AAC — no receipt is ever
    printed. Reaches the host (the caller still advances txn_no), but
    nothing in the log shows the number."""
    w.header(tran_date, atm_id_full)
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(random.choice(PN_DECLINE_OPCODES))
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    w.header(tran_date, atm_id_full)
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)


def _gen_pn_host_decline_with_receipt(w: EJWriter, txn_no: int, tran_date: datetime,
                                       location: str, atm_id_full: str,
                                       atm_type: str = "PN",
                                       response_code: str = None, extra_message: str = None) -> int:
    """PN/PR: PIN entered, host request sent, host declines with a printed
    decline receipt. Reaches the host, consumes a TXN NO.

    Vendor convention differs by terminal type (see DECLINE_CODE_CONFIG):
    PN — only 055 ever prints a message line ('INVALID PIN'); RRN always
    populated. PR — 055 AND 100 print messages ('INVALID PIN'/
    'UNABLE TO PROCESS'); RRN is populated for 051/030 but BLANK for 100
    (matches real PR803920 examples). response_code/extra_message let
    callers pin a specific decline code/override message; otherwise the
    per-atm_type generic_pool/message_codes config applies automatically."""
    cfg = DECLINE_CODE_CONFIG[atm_type]
    w.header(tran_date, atm_id_full)
    receipt_pan = _gen_card_session_opening(w)
    rrn = str(random.randint(500000000000, 599999999999))

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(random.choice(PN_DECLINE_OPCODES))
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    TXN NO:               {txn_no}")
    w.raw(" ")
    w.raw("    #SAVINGS")
    w.raw(" ")
    w.raw(" ")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(" ")
    code = response_code or random.choice(cfg["generic_pool"])
    message = extra_message
    if message is None:
        message = cfg["message_codes"].get(code)
    if atm_type == "PR" and code == "100":
        rrn = " " * 12  # real PR803920 evidence: RRN blank for code 100
    w.raw(f"    RESPONSE CODE              {code}")
    if message:
        w.raw(f"    {message}")
    w.raw(" ")
    w.raw(f"    RRN.               {rrn}")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     ------------------------------------")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")
    w.raw("")

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.header(tran_date, atm_id_full)
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_pin_tries_exceeded(w: EJWriter, txn_no: int, tran_date: datetime,
                             location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """RESPONSE CODE 075 — no message line (shared real evidence, both
    terminal types)."""
    return _gen_pn_host_decline_with_receipt(w, txn_no, tran_date, location, atm_id_full,
                                              atm_type=atm_type, response_code="075")


def _gen_invalid_pin(w: EJWriter, txn_no: int, tran_date: datetime,
                      location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """REAL EVIDENCE: RESPONSE CODE 055, 'INVALID PIN' (shared, both terminal
    types)."""
    return _gen_pn_host_decline_with_receipt(w, txn_no, tran_date, location, atm_id_full,
                                              atm_type=atm_type, response_code="055", extra_message="INVALID PIN")


def _gen_declined_insufficient_funds(w: EJWriter, txn_no: int, tran_date: datetime,
                                      location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """PN: no dedicated code exists, reuses 100 (blank message). PR: real-
    evidence-confirmed dedicated code 051 (blank message). See
    DECLINE_CODE_CONFIG."""
    code = DECLINE_CODE_CONFIG[atm_type]["insufficient_funds"]
    return _gen_pn_host_decline_with_receipt(w, txn_no, tran_date, location, atm_id_full,
                                              atm_type=atm_type, response_code=code)


def _gen_daily_limit_exceeded(w: EJWriter, txn_no: int, tran_date: datetime,
                               location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """PN: no dedicated code exists, reuses 100 (blank message). PR: real-
    evidence-confirmed dedicated code 061 (blank message). See
    DECLINE_CODE_CONFIG."""
    code = DECLINE_CODE_CONFIG[atm_type]["daily_limit_exceeded"]
    return _gen_pn_host_decline_with_receipt(w, txn_no, tran_date, location, atm_id_full,
                                              atm_type=atm_type, response_code=code)


def _gen_pn_bin_stage_decline(w: EJWriter, txn_no: int, tran_date: datetime,
                               location: str, atm_id_full: str,
                               response_code: str, extra_message: str = None) -> int:
    """PN/PR: card read/BIN check fails before PIN entry ever happens — no
    host round-trip, so RRN stays blank, but the ATM still prints a
    local-decision decline receipt consuming the shared TXN NO counter.

    Vendor convention: only response code 055 ever prints a message line —
    extra_message should be None for every other code (only kept as a
    parameter for the 055 case, if ever routed through here)."""
    w.header(tran_date, atm_id_full)
    receipt_pan = _gen_card_session_opening(w)

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))
    w.raw(" ")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(" ")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    TXN NO:               {txn_no}")
    w.raw(" ")
    w.raw("    #SAVINGS")
    w.raw(" ")
    w.raw(" ")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(" ")
    w.raw(f"    RESPONSE CODE              {response_code}")
    if extra_message:
        w.raw(f"    {extra_message}")
    w.raw(" ")
    w.raw("    RRN.")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     ------------------------------------")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")
    w.raw("")

    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.header(tran_date, atm_id_full)
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_declined_unauthorized_card(w: EJWriter, txn_no: int, tran_date: datetime,
                                     location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """PN: no dedicated code exists, reuses 100 (blank message, no invented
    'TRANSACTION NOT PERMITTED' text). PR: real-evidence-confirmed dedicated
    code 057 (blank message). See DECLINE_CODE_CONFIG."""
    code = DECLINE_CODE_CONFIG[atm_type]["unauthorized_card"]
    return _gen_pn_bin_stage_decline(w, txn_no, tran_date, location, atm_id_full, code)


def _gen_card_expired(w: EJWriter, txn_no: int, tran_date: datetime,
                       location: str, atm_id_full: str, atm_type: str = "PN") -> int:
    """RESPONSE CODE 054 — not in the native vocabulary, but a consistent
    extension using the vendor's blank-message convention (no message
    line, same as every other non-055/100 code). Same code for both
    terminal types."""
    code = DECLINE_CODE_CONFIG[atm_type]["card_expired"]
    return _gen_pn_bin_stage_decline(w, txn_no, tran_date, location, atm_id_full, code)


def _gen_host_timeout(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """PN/PR: PIN entered, request sent, but the host never answers — no
    RESPONSE RECEIVED, no receipt at all. Does NOT consume a TXN NO. Uses
    this Diebold-style vendor's own TIMEOUT syntax
    (TIMEOUT '<flow-name>/<action-name>' after <ms>ms.), not the Wincor
    (ER-series) LINK1/HOST TX TIMEOUT vocabulary."""
    w.header(tran_date, atm_id_full)
    _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(random.choice(PN_DECLINE_OPCODES))
    w.raw("")

    w.event("TIMEOUT 'hostResponse-flow/transaction-request-action' after 30000ms.", delta_secs=random.randint(28, 32))
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejected", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("**TRANSACTION END**", delta_secs=0)


def _gen_balance_inquiry(w: EJWriter, atm_type: str, txn_no: int, tran_date: datetime,
                          location: str, atm_id_full: str, force_success: bool = False) -> int:
    """PN/PR: balance inquiry. Success prints BALANCE INQUIRY/AVAILABLE BAL
    (full word, not AVAIL BAL) with a fully asterisk-masked FROM A/C and no
    dispense section at all; decline mirrors the generic host decline shape.
    Always consumes a TXN NO (reaches the host either way). force_success
    pins the successful branch (used by the coverage guarantee to ensure at
    least one SUCCESSFUL instance appears, not just declines)."""
    cfg = ATM_TYPE_CONFIG[atm_type]
    avail_bal = _random_balance()
    success = True if force_success else random.random() < 0.7

    w.header(tran_date, atm_id_full)
    receipt_pan = _gen_card_session_opening(w)

    w.event("PIN code entered", delta_secs=random.randint(15, 30))
    w.event("", delta_secs=random.randint(5, 10))
    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(cfg["opcode_line"] if success else random.choice(PN_DECLINE_OPCODES))
    w.raw("")

    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)
    w.raw("")

    w.event(f"GENAC 2 : {'TC' if success else 'AAC'}", delta_secs=random.randint(1, 3), double_space=True)
    w.raw("")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")

    if success:
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
        w.raw(f"    FROM A/C:{'*' * 23}")
        w.raw(f"    AVAILABLE BAL{_fmt_money_field(avail_bal, 14)}")
        w.raw("    RESPONSE CODE              000")
        w.raw("    YOUR TXN IS SUCCESSFUL")
        if cfg["rrn_style"] == "long":
            rrn = str(random.randint(500000000000, 599999999999))
            w.raw(f"    RRN.               {rrn}")
        else:
            w.raw(f"    RRN.               {txn_no}        ")
        w.raw("    GO CASH FREE!USE DEBIT CARDS")
        w.raw("    NEVER SHARE YOUR CARD DETAILS")
        w.raw("    AND PIN WITH ANYONE")
        w.raw("")
        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
        w.event("**TRANSACTION END**", delta_secs=0)
    else:
        rrn = str(random.randint(500000000000, 599999999999))
        w.event(f"    LOCATION: {location}", delta_secs=random.randint(1, 3))
        w.raw(" ")
        w.raw("    DATE       TIME       ATM ID")
        w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
        w.raw(" ")
        w.raw(f"    CARD NUMBER:  {receipt_pan}")
        w.raw(f"    TXN NO:               {txn_no}")
        w.raw(" ")
        w.raw("    #SAVINGS")
        w.raw(" ")
        w.raw(" ")
        w.raw("    SORRY UNABLE TO PROCESS")
        w.raw(" ")
        decline_code = random.choice(PN_DECLINE_CODES)
        w.raw(f"    RESPONSE CODE              {decline_code}")
        if decline_code == "055":
            w.raw("    INVALID PIN")
        w.raw(" ")
        w.raw(f"    RRN.               {rrn}")
        w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
        w.raw("     ------------------------------------")
        w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
        w.raw("        PLEASE DIAL +975-2-332540 AND")
        w.raw("         HELP US TO SERVE YOU BETTER")
        w.raw("")
        w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.header(tran_date, atm_id_full)
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_power_cut(w: EJWriter) -> None:
    """LITERAL TEMPLATE (user-supplied, real evidence): this vendor's own
    hardware-fault power-cut/reinit sequence — CASH Fatal -> RESET command
    failure threshold reached -> XFS STATUS block -> Out of Service -> In
    Service. Not the Wincor (ER-series) reboot-log vocabulary. Occurs
    BETWEEN customer transactions, is not itself a customer transaction,
    and consumes no TXN NO."""
    w.event("CASH Fatal [90/0/0/0]", delta_secs=random.randint(2, 5))
    w.raw("RESET command failure threshold reached")
    w.raw("XFS STATUS:")
    w.raw("-HW_ERROR: 8")
    w.raw("-HW_ERROR_DESC: AFD0ST07    99=AFD 0 ST 07 7000=3 10=5 1=0 2=0 7001=0 7002=4294967295 7003=0 7004=0 7005=1 7006=0 7010=0 7015=0 7017=1 7020=0 7021=0 7022=0 7023=0 65663=0 69391=5771279 69392=14128 69393=21587 69395=12336 69396=12336 69397=13872 69398=13872 69399=13872 69400=12336 69401=12336 69406=3813632 196735=0 262271=0 262275=0 262276=0 262284=2 262285=0 9437308=514 9437311=0 9502847=0 9568383=0 9633919=0 9699455=0 9764988=514 9764991=258 9830526=262 9830527=0 9896060=514 9896063=0 9961599=0 10027135=0 10092670=2 10158206=0 10227468=0 12583039=0 12648575=0 12714111=4 12779647=4 12845183=4 12910719=1 14090367=262 14221439=262 14286975=261 276824188=0 285212799=0 285278335=0    ")
    w.event("Out of Service", delta_secs=random.randint(1, 3))
    w.raw("-From Host")
    w.raw("")
    w.event("In Service", delta_secs=random.randint(5, 15))


def _gen_admin_cassette(w: EJWriter, cs: CassetteState, atm_id_full: str) -> None:
    """LITERAL TEMPLATE (user-supplied, real evidence): this vendor's own
    menu-navigation trail for cassette replenishment — CABINET DOOR: OPEN
    -> Operator Login -> FAULTS/DIAGNOSTICS menu wandering ->
    FINANCIAL_REPLENISHMENT -> CASHREPLENMENU -> MACHINESUBTOTALS ->
    dispenser-totals + reject/lost tables -> SAFE DOOR/CABINET DOOR closed
    -> Operator Exiting. Occurs BETWEEN customer transactions, is not
    itself a customer transaction, and consumes no TXN NO."""
    w.event("CABINET DOOR: OPEN", delta_secs=random.randint(2, 5))
    w.event("Operator Login", delta_secs=random.randint(3, 8))
    w.raw("")
    w.event("FAULTS", delta_secs=random.randint(2, 5))
    w.event("BACK", delta_secs=random.randint(2, 5))
    w.event("FAULTS", delta_secs=random.randint(2, 5))
    w.event("DIAGNOSTICS", delta_secs=random.randint(2, 5))
    w.event("BACK", delta_secs=random.randint(2, 5))
    w.event("FINANCIAL_REPLENISHMENT", delta_secs=random.randint(2, 5))
    w.event("CASHREPLENMENU", delta_secs=random.randint(2, 5))
    w.event("MACHINESUBTOTALS", delta_secs=random.randint(2, 5))
    w.event("", delta_secs=random.randint(1, 3))

    date_str = w.dt.strftime("%m/%d/%Y")
    time_str = w.dt.strftime("%H:%M")
    w.raw(f"DATE{date_str}CASH DISPENSER TOTALS          TIME{time_str}          Terminal ID: {atm_id_full}")
    w.raw(" NUM    CURR    TOT  DEP   LEFT   DISP")
    for num, denom in ((1, 100), (2, 200), (3, 500)):
        tot = cs.remaining[denom] + cs.dispensed[denom]
        w.raw(f"   {num}  INR{denom}   {tot}    0    {cs.remaining[denom]}   {cs.dispensed[denom]}")
    w.raw("")
    w.raw(" NUM    CURR   RJCT   LOST")
    for num, denom in ((1, 100), (2, 200), (3, 500)):
        w.raw(f"   {num}  INR{denom}     {cs.rejected[denom]}      {cs.lost[denom]}")
    w.raw("")

    w.event("SAFE DOOR: CLOSED", delta_secs=random.randint(3, 8))
    w.event("CABINET DOOR: CLOSED", delta_secs=random.randint(2, 5))
    w.event("Operator Exiting", delta_secs=random.randint(2, 5))


def _gen_deposit(w: EJWriter, cs: CassetteState, tran_date: datetime, txn_no: int,
                  location: str, atm_id_full: str, card_based: bool = False,
                  force_unknown: bool = False) -> int:
    """PR recycler cash deposit — deposited notes are Rs.500 only (cassette denom).

    A deposit is a single customer session but two Host round-trips (two
    consecutive TXN NOs): the first authorizes the deposit and opens the
    hopper for note insertion, counted note-by-note ('Cat4 Serial Number(s)')
    and re-validated progressively via 'Cash-in amount' / 'Cat4 notes' steps;
    the second confirms/stores the counted cash, prints the final receipt,
    and closes with a 'ONEDEPOSIT_CASH ID' ledger entry and cash-deposited
    summary. Supports both a cardless (manual A/C entry) and a card-based path.

    force_unknown (BEST-INTERPRETATION — no real evidence in either
    reference file for this exact wording): 1-2 of the scanned notes come
    back unidentified and are returned to the customer via a
    'Cat4b note(s) returned:' line; only the remaining recognized notes are
    actually deposited (excluded from amount/DENOMS/summary entirely).
    """
    count = random.choice(DEPOSIT_NOTE_COUNTS)
    unknown_count = 0
    deposited_count = count
    amount = deposited_count * 500
    acc_no = _random_account()
    name = _random_customer_name()
    txn_no_1 = txn_no
    txn_no_2 = txn_no + 1

    if card_based:
        w.header(tran_date, atm_id_full)
        receipt_pan = _gen_card_session_opening(w, mid_header=(tran_date, atm_id_full))
        w.event("PIN code entered", delta_secs=random.randint(15, 30))
        opcode_init = " OPCODE = B CC   A"
        opcode_complete = " OPCODE = B B"
    else:
        receipt_pan = "888888******8888"
        w.event("===== CARDLESS DEPOSIT SELECTED =====", delta_secs=random.randint(1, 3))
        w.header(tran_date, atm_id_full)
        opcode_init = " OPCODE = BC     A"
        opcode_complete = " OPCODE = BCC"

    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(opcode_init)
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)

    receipt_date = w.dt.strftime("%d/%m/%y")
    receipt_time = w.dt.strftime("%H:%M")
    w.event(f"    ATM ADD: {location}", delta_secs=random.randint(1, 3))
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name}")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no_1}")

    if card_based:
        w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)

    w.event("Cat4 Serial Number(s):", delta_secs=random.randint(3, 8))
    for _ in range(count):
        w.raw(f"  (INR 500) {_random_note_serial()}")

    if force_unknown and count > 1:
        unknown_count = random.randint(1, min(2, count - 1))
        deposited_count = count - unknown_count
        amount = deposited_count * 500
        w.event("Cat4b note(s) returned:", delta_secs=random.randint(2, 5))
        w.raw(f" {unknown_count} note(s) of INR Unidentified")

    w.event("CASH Warning [25/0/5/0]", delta_secs=random.randint(2, 5))
    w.raw("Cassette(s) nearly empty")

    steps = random.randint(2, 4)
    for running_count in _progressive_counts(deposited_count, steps):
        running_amount = running_count * 500
        w.event(f"Cash-in amount: {running_amount:,}.00 INR", delta_secs=random.randint(3, 8))
        w.raw("Cat4 notes:")
        w.raw(f"  {running_count} of INR 500: {running_amount:,}.00 INR")
        w.raw("")

    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(opcode_complete)
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)

    receipt_date2 = w.dt.strftime("%d/%m/%y")
    receipt_time2 = w.dt.strftime("%H:%M")
    w.event(f"    ATM ADD: {location}", delta_secs=random.randint(1, 3))
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date2}   {receipt_time2}      {atm_id_full}")
    w.raw(f"    NAME: {name}")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no_2}")
    w.raw("    RRN.                           ")
    w.raw(" ")
    w.raw("    CASH DEPOSIT PARTICULARS")
    w.raw(f"    DEPOSIT AMOUNT        RS.{amount}.00")
    w.raw(" ")
    w.raw("    DENOMS   COUNTS   SUB TOTALS.")
    w.raw(f"    0500     {deposited_count:03d}       {amount}")
    w.raw(" ")
    w.raw(" ")
    w.raw(" ")
    w.raw(" ")
    w.raw(f"    TOTAL:       {amount}.00")
    w.raw(" ")
    w.raw("    RESPONSE CODE              000")

    cs.deposit(0, 0, deposited_count)

    if card_based:
        w.header(tran_date, atm_id_full)
        w.event("CASH Warning [25/0/5/0]", delta_secs=random.randint(3, 8))
        w.raw("Cassette(s) nearly empty")
        w.event("Cat4b note(s) deposited:", delta_secs=random.randint(2, 5))
        w.raw(f" {deposited_count} note(s) of INR 500")
        w.raw("")
        deposit_id = random.randint(10000, 99999)
        w.event(f"ONEDEPOSIT_CASH ID: {deposit_id}", delta_secs=random.randint(1, 3))
        ledger_date = w.dt.strftime("%d/%m/%Y")
        ledger_time = w.dt.strftime("%H:%M")
        w.raw(f"{ledger_date} {ledger_time} {atm_id_full}")
        w.raw("RA:000RI:000")
        w.raw("")
        w.event("Cash Deposited:", delta_secs=random.randint(2, 5))
        w.raw(f"  {deposited_count} of INR 500:  {amount:,}.00 INR")
        w.raw(f"Total Cash Deposit: {amount:,}.00 INR")
        w.raw(f"Total Deposit Amount: {amount:,}.00 INR")
        w.raw("")
        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
        w.event("**TRANSACTION END**", delta_secs=0)
    else:
        w.event("Cat4a note(s) recycled:", delta_secs=random.randint(2, 5))
        w.raw(f" {deposited_count} note(s) of INR 500")
        w.event("CASH Warning [25/0/5/0]", delta_secs=random.randint(2, 5))
        w.raw("Cassette(s) nearly empty")
        deposit_id = random.randint(10000, 99999)
        w.event(f"PSONEDEPOSIT_CASH_FROM_SWITCH ID: {deposit_id}", delta_secs=random.randint(1, 3))
        ledger_date = w.dt.strftime("%d/%m/%Y")
        ledger_time = w.dt.strftime("%H:%M")
        w.raw(f"{ledger_date} {ledger_time} {atm_id_full}")
        w.raw("RA:000RI:000")
        w.raw("")
        w.event("**TRANSACTION END**", delta_secs=random.randint(2, 5))
        w.event("Cash Deposited:", delta_secs=random.randint(2, 5))
        w.raw(f"  {deposited_count} of INR 500:  {amount:,}.00 INR")
        w.raw(f"Total Cash Deposit: {amount:,}.00 INR")
        w.raw(f"Total Deposit Amount: {amount:,}.00 INR")

    return txn_no_2 + 1


def _gen_deposit_abandoned(w: EJWriter) -> None:
    """PR: cardless deposit selected then abandoned immediately — no host
    round-trip at all, so no TXN NO is consumed."""
    w.event("===== CARDLESS DEPOSIT SELECTED =====", delta_secs=random.randint(1, 3))
    w.event("Transaction Cancelled", delta_secs=random.randint(5, 15))
    w.event("**TRANSACTION END**", delta_secs=0)


def _gen_deposit_retracted(w: EJWriter, tran_date: datetime, txn_no: int,
                            location: str, atm_id_full: str, card_based: bool = False) -> int:
    """EXTRAPOLATED — PR: notes inserted and counted (reuses the same
    opening/Cat4 Serial Number(s)/Cash-in amount steps as a real deposit),
    but the deposit is refused/retracted before the confirm round-trip —
    ends WITHOUT a CASH DEPOSIT PARTICULARS block, no funds credited.
    Consumes only the first (authorize) TXN NO; the confirm round-trip
    never happens since the deposit never completes."""
    count = random.choice(DEPOSIT_NOTE_COUNTS)
    acc_no = _random_account()
    name = _random_customer_name()

    if card_based:
        w.header(tran_date, atm_id_full)
        receipt_pan = _gen_card_session_opening(w, mid_header=(tran_date, atm_id_full))
        w.event("PIN code entered", delta_secs=random.randint(15, 30))
        opcode_init = " OPCODE = B CC   A"
    else:
        receipt_pan = "888888******8888"
        w.event("===== CARDLESS DEPOSIT SELECTED =====", delta_secs=random.randint(1, 3))
        w.header(tran_date, atm_id_full)
        opcode_init = " OPCODE = BC     A"

    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(opcode_init)
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)

    receipt_date = w.dt.strftime("%d/%m/%y")
    receipt_time = w.dt.strftime("%H:%M")
    w.event(f"    ATM ADD: {location}", delta_secs=random.randint(1, 3))
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name}")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no}")

    if card_based:
        w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)

    w.event("Cat4 Serial Number(s):", delta_secs=random.randint(3, 8))
    for _ in range(count):
        w.raw(f"  (INR 500) {_random_note_serial()}")

    w.event("CASH Warning [25/0/5/0]", delta_secs=random.randint(2, 5))
    w.raw("Cassette(s) nearly empty")

    running_amount = count * 500
    w.event(f"Cash-in amount: {running_amount:,}.00 INR", delta_secs=random.randint(3, 8))
    w.raw("Cat4 notes:")
    w.raw(f"  {count} of INR 500: {running_amount:,}.00 INR")
    w.raw("")

    w.event("DEPOSIT REFUSED", delta_secs=random.randint(3, 8))
    w.event("NOTES RETRACTED", delta_secs=random.randint(5, 12))
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))

    if card_based:
        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


def _gen_deposit_cash_jam(w: EJWriter, tran_date: datetime, txn_no: int,
                           location: str, atm_id_full: str, card_based: bool = False) -> int:
    """EXTRAPOLATED — PR: a hardware jam during note insertion/counting,
    distinct from the existing retry-then-succeed note-error pattern (that
    one recovers and completes); this one ends in failure, no CASH DEPOSIT
    PARTICULARS block, no funds credited. Consumes only the first (authorize)
    TXN NO."""
    acc_no = _random_account()
    name = _random_customer_name()

    if card_based:
        w.header(tran_date, atm_id_full)
        receipt_pan = _gen_card_session_opening(w, mid_header=(tran_date, atm_id_full))
        w.event("PIN code entered", delta_secs=random.randint(15, 30))
        opcode_init = " OPCODE = B CC   A"
    else:
        receipt_pan = "888888******8888"
        w.event("===== CARDLESS DEPOSIT SELECTED =====", delta_secs=random.randint(1, 3))
        w.header(tran_date, atm_id_full)
        opcode_init = " OPCODE = BC     A"

    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), double_space=True)
    w.raw(opcode_init)
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 2), double_space=True)

    receipt_date = w.dt.strftime("%d/%m/%y")
    receipt_time = w.dt.strftime("%H:%M")
    w.event(f"    ATM ADD: {location}", delta_secs=random.randint(1, 3))
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name}")
    w.raw(f"    CARD NUMBER:  {receipt_pan}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no}")

    if card_based:
        w.event("GENAC 2 : AAC", delta_secs=random.randint(1, 3), double_space=True)

    w.event("NOTE ERROR OCCURRED:REASON:CIM_OTHERNOTEERROR--OTHER NOTE ERROR HAS BEEN DETECTED",
            delta_secs=random.randint(2, 5))
    w.event("CASH JAM DETECTED", delta_secs=random.randint(3, 8))
    w.event("Transaction Cancelled", delta_secs=random.randint(2, 5))

    if card_based:
        w.event("Card ejected", delta_secs=random.randint(1, 3))
        w.event("Card taken", delta_secs=random.randint(1, 4))
        w.event("DOUBLE TIMEOUT 'endOfSession-flow/card-eject-action' after 11000ms.", delta_secs=random.randint(5, 10))
    w.event("**TRANSACTION END**", delta_secs=0)

    return txn_no + 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_eps_ej(
    atm_type: str,
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
    continuation: dict = None,
) -> dict:
    """
    Generate an EPS EJ file for IDFC First Bank ATMs.

    Args:
        atm_type: 'PN' (dispense-only) or 'PR' (recycler)
        tran_date: transaction date
        num_transactions: number of customer transactions
        selected_cases: list of case IDs to include (currently: 'simple_withdrawal')
        atm_id: numeric ATM ID string (auto-generated if None)
        location: branch name (random if None)
        output_dir: output directory (uses /tmp if None)
        continuation: optional {"next_txn_no": int} from a prior run's result to
            keep TXN NO continuous across multiple files for the same ATM+day
            batch ("sync with other files"), instead of restarting at a fresh
            random TXN NO.

    Returns:
        dict with run_id, file_name, atm_id, location, counts, continuation
    """
    if atm_type not in ATM_TYPE_CONFIG:
        raise ValueError(f"atm_type must be 'PN' or 'PR', got {atm_type!r}")

    random.seed()  # non-deterministic

    run_id = uuid.uuid4().hex[:12]
    if atm_id is None:
        atm_id = _gen_atm_id(atm_type)
    atm_id_full = f"{atm_type}{atm_id}"
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # File naming: PN{ATM_ID}_{DDMMYYYY}.txt / PR{ATM_ID}_{DDMMYYYY}.txt
    date_str_file = tran_date.strftime("%d%m%Y")
    file_name = f"{atm_id_full}_{date_str_file}.txt"

    start_dt = tran_date.replace(hour=8, minute=30, second=0, microsecond=0)
    w = EJWriter(start_dt)
    cs = CassetteState()

    case_set = set(selected_cases) if selected_cases else {"simple_withdrawal"}
    do_withdrawal = "simple_withdrawal" in case_set or not case_set
    # PR is a recycler (dispense + deposit) — interleave deposits with withdrawals.
    # PN is dispense-only and must stay withdrawal-only regardless of case selection.
    do_deposit = atm_type == "PR" and "cash_deposit" in case_set
    # PN sessions succeed only ~45-50% of the time in the reference; the rest
    # are abandoned or declined. PR is unaffected — it keeps its 100% success
    # rate for withdrawals (only deposits vs. withdrawals are mixed there).
    do_pn_failures = atm_type == "PN"

    if continuation and continuation.get("next_txn_no") is not None:
        txn_no = continuation["next_txn_no"]
    else:
        txn_no = random.randint(2000, 9000)
    counts = {
        "total": 0, "simple_withdrawal": 0, "cash_deposit": 0, "deposit_abandoned": 0,
        "abandoned": 0, "card_decline": 0, "host_decline": 0,
        "balance_inquiry": 0, "host_timeout": 0, "power_cut": 0, "admin_cassette": 0,
        "pin_tries_exceeded": 0, "invalid_pin": 0,
        "declined_insufficient_funds": 0, "declined_unauthorized_card": 0,
        "daily_limit_exceeded": 0, "card_expired": 0,
        "cash_not_taken": 0, "notes_in_reject": 0, "partial_split_transaction": 0,
        "unknown_denom_notes": 0, "failure_to_collect_card": 0,
        "deposit_retracted": 0, "deposit_cash_jam": 0,
    }

    # New withdrawal-family extras — opt-in: only mixed into the outcome pool
    # when their case ID is explicitly selected. (weight, handler) pairs;
    # handler returns the updated txn_no (all of these consume one).
    _EXTRA_WITHDRAWAL_OUTCOMES = [
        ("balance_inquiry", 6, lambda: _gen_balance_inquiry(w, atm_type, txn_no, tran_date, location, atm_id_full)),
        ("pin_tries_exceeded", 2, lambda: _gen_pin_tries_exceeded(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("invalid_pin", 2, lambda: _gen_invalid_pin(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("declined_insufficient_funds", 2, lambda: _gen_declined_insufficient_funds(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("declined_unauthorized_card", 2, lambda: _gen_declined_unauthorized_card(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("daily_limit_exceeded", 1, lambda: _gen_daily_limit_exceeded(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("card_expired", 1, lambda: _gen_card_expired(w, txn_no, tran_date, location, atm_id_full, atm_type=atm_type)),
        ("cash_not_taken", 2, lambda: _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full, ending="cash_not_taken")),
        ("notes_in_reject", 2, lambda: _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full, force_reject=True)),
        ("partial_split_transaction", 2, lambda: _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full, split_cycles=random.choice([2, 3]))),
        ("unknown_denom_notes", 1, lambda: _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full, force_unknown=True)),
        ("failure_to_collect_card", 1, lambda: _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full, ending="card_not_collected")),
    ]
    # host_timeout doesn't consume a TXN NO, so it's handled separately below.

    # Coverage guarantee: every explicitly-selected outcome must appear at
    # least once in the file rather than being left to chance — low-weight
    # scenarios could easily roll zero times over a normal-sized run, which
    # looked like regressions across successive generations even though
    # nothing was actually broken. Reserve one slot per pending outcome
    # among the trailing withdrawal-consuming iterations; if it hasn't
    # fired naturally by the time its reserved slot is reached, force it
    # there. balance_inquiry's guaranteed occurrence is forced to the
    # SUCCESS branch specifically, since the internal 70/30 split could
    # otherwise guarantee only a decline.
    to_guarantee_ids = [cid for cid, _wt, _h in _EXTRA_WITHDRAWAL_OUTCOMES if cid in case_set]
    if "host_timeout" in case_set:
        to_guarantee_ids.append("host_timeout")
    random.shuffle(to_guarantee_ids)
    guarantee_start = max(0, num_transactions - len(to_guarantee_ids))

    for _txn_idx in range(num_transactions):
        w.advance(random.randint(60, 900))

        is_last = _txn_idx == num_transactions - 1
        # power_cut / admin_cassette are maintenance events, not customer
        # transactions — inserted independently of the outcome pick below,
        # don't consume num_transactions or a TXN NO.
        force_power_cut = "power_cut" in case_set and counts["power_cut"] == 0 and is_last
        if "power_cut" in case_set and (force_power_cut or random.random() < 0.03):
            _gen_power_cut(w)
            counts["power_cut"] += 1
        force_admin = "admin_cassette" in case_set and counts["admin_cassette"] == 0 and is_last
        if "admin_cassette" in case_set and (force_admin or random.random() < 0.03):
            _gen_admin_cassette(w, cs, atm_id_full)
            counts["admin_cassette"] += 1

        reserved_idx = _txn_idx - guarantee_start
        pending = to_guarantee_ids[reserved_idx] if 0 <= reserved_idx < len(to_guarantee_ids) else None
        if pending is not None and counts[pending] > 0:
            pending = None  # already fired naturally — no need to force it again

        # A deposit consumes 2 TXN NOs (authorize + confirm) but is 1 customer
        # session, so it only counts once against num_transactions. A deposit
        # abandoned right after selection never reaches the host at all.
        if do_deposit and (not do_withdrawal or random.random() < 0.25):
            deposit_pool = ["cash_deposit", "deposit_abandoned"]
            deposit_weights = [79, 11]
            if "deposit_retracted" in case_set:
                deposit_pool.append("deposit_retracted")
                deposit_weights.append(5)
            if "deposit_cash_jam" in case_set:
                deposit_pool.append("deposit_cash_jam")
                deposit_weights.append(5)
            deposit_outcome = random.choices(deposit_pool, weights=deposit_weights, k=1)[0]
            card_based = random.random() < 0.5

            if deposit_outcome == "cash_deposit":
                txn_no = _gen_deposit(w, cs, tran_date, txn_no, location, atm_id_full, card_based=card_based)
            elif deposit_outcome == "deposit_abandoned":
                _gen_deposit_abandoned(w)
            elif deposit_outcome == "deposit_retracted":
                txn_no = _gen_deposit_retracted(w, tran_date, txn_no, location, atm_id_full, card_based=card_based)
            else:
                txn_no = _gen_deposit_cash_jam(w, tran_date, txn_no, location, atm_id_full, card_based=card_based)
            counts[deposit_outcome] += 1
            counts["total"] += 1
        elif do_withdrawal and do_pn_failures:
            # Card-level decline reaches the host but prints no receipt at all,
            # so its TXN NO is invisible in the log — but the counter must
            # still advance to keep subsequent TXN NOs correctly offset.
            outcome_pool = ["simple_withdrawal", "abandoned", "host_decline", "card_decline"]
            weights_pool = [40, 20, 16, 6]
            if "host_timeout" in case_set:
                outcome_pool.append("host_timeout")
                weights_pool.append(3)
            for cid, wt, _handler in _EXTRA_WITHDRAWAL_OUTCOMES:
                if cid in case_set:
                    outcome_pool.append(cid)
                    weights_pool.append(wt)
            outcome = pending if pending is not None else random.choices(outcome_pool, weights=weights_pool, k=1)[0]

            if outcome == "simple_withdrawal":
                txn_no = _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full)
            elif outcome == "abandoned":
                _gen_pn_abandoned_after_pin(w, tran_date, atm_id_full)
            elif outcome == "host_decline":
                txn_no = _gen_pn_host_decline_with_receipt(w, txn_no, tran_date, location, atm_id_full)
            elif outcome == "card_decline":
                _gen_pn_card_level_decline(w, tran_date, atm_id_full)
                txn_no += 1
            elif outcome == "host_timeout":
                _gen_host_timeout(w, tran_date, atm_id_full)
            elif outcome == "balance_inquiry":
                txn_no = _gen_balance_inquiry(w, atm_type, txn_no, tran_date, location, atm_id_full,
                                               force_success=(pending == "balance_inquiry"))
            else:
                handler = next(h for cid, _wt, h in _EXTRA_WITHDRAWAL_OUTCOMES if cid == outcome)
                txn_no = handler()
            counts[outcome] += 1
            counts["total"] += 1
        elif do_withdrawal:
            # PR: withdrawals stay ~100% successful except for these opt-in
            # extras — none fire unless explicitly selected in case_set.
            outcome_pool = ["simple_withdrawal"]
            weights_pool = [70]
            if "host_timeout" in case_set:
                outcome_pool.append("host_timeout")
                weights_pool.append(3)
            for cid, wt, _handler in _EXTRA_WITHDRAWAL_OUTCOMES:
                if cid in case_set:
                    outcome_pool.append(cid)
                    weights_pool.append(wt)
            outcome = pending if pending is not None else random.choices(outcome_pool, weights=weights_pool, k=1)[0]

            if outcome == "simple_withdrawal":
                txn_no = _gen_simple_withdrawal(w, cs, atm_type, txn_no, tran_date, location, atm_id_full)
            elif outcome == "host_timeout":
                _gen_host_timeout(w, tran_date, atm_id_full)
            elif outcome == "balance_inquiry":
                txn_no = _gen_balance_inquiry(w, atm_type, txn_no, tran_date, location, atm_id_full,
                                               force_success=(pending == "balance_inquiry"))
            else:
                handler = next(h for cid, _wt, h in _EXTRA_WITHDRAWAL_OUTCOMES if cid == outcome)
                txn_no = handler()
            counts[outcome] += 1
            counts["total"] += 1

    out_path = output_dir / file_name
    content = _control_preamble(atm_type) + w.get_text() + "\r\n#EOL#\r\n"
    with open(out_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(content)

    cases_included = [c for c in selected_cases if counts.get(c, 0) > 0] or ["simple_withdrawal"]

    manifest = {
        "run_id": run_id,
        "bank_id": "idfc",
        "vendor": "eps",
        "atm_type": atm_type,
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
