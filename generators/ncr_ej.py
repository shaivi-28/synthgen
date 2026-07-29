"""
NCR (NX) Electronic Journal (EJ) Generator for IDFC First Bank ATMs.

NCR uses a dense, largely single-line raw-dump format distinct from the other
IDFC vendors: lines are prefixed with the literal ESC control sequence
"\\x1b[020t" (toggled on/off per line, matching the reference exactly), each
transaction is bracketed by "\\x1b[020t*SEQ*MM/DD/YYYY*HH:MM:SS*" markers
("*TRANSACTION START*" / "*PRIMARY CARD READER ACTIVATED*"), and session
lifecycle is tracked via embedded "{{{ ... }}}" JSON blocks (SESSION CREATED /
SESSION CLOSED, with UTC timestamps 5:30 behind local IST time). Cardless
cash-deposit transactions (with banknote serial number blocks) also occur in
the reference dump but are out of scope for this first pass — only the simple
withdrawal case is implemented here.

Reference structure (withdrawal transaction):
    \\x1b[020t*SEQ*MM/DD/YYYY*HH:MM:SS*
         *TRANSACTION START*
    \\x1b[020t CARD INSERTED
     HH:MM:SS ATR RECEIVED T=0
    {{{ "SESSION": N, "EVENT": "SESSION CREATED", "DATE": "...Z" }}}
    \\x1b[020tCARD: masked
    DATE DD-MM-YY    TIME HH:MM:SS
    \\x1b[020t HH:MM:SS PIN ENTERED
    \\x1b[020t HH:MM:SS OPCODE = AB     A
    HH:MM:SS REQUEST SENT [AMOUNT=NNNNNNNN]
     HH:MM:SS GENAC 1 : ARQC
    HH:MM:SS RESPONSE RECEIVED [FUNCTION ID=A,  TXN SN NO=NNNN]
     HH:MM:SS GENAC 2 : TC
    \\x1b[020t HH:MM:SS NOTES STACKED
    \\x1b[020t
    HH:MM:SS CARD TAKEN
    receipt block (branch, ATM ID, masked card, TXN NO., WITHDRAWAL, FROM A/C,
    AVAIL BAL, RESPONSE CODE, RRN)
    \\x1b[020t HH:MM:SS
    NOTES PRESENTED / POSITION 1,2,3,0 / COUNT n100,n200,n500,0
    \\x1b[020t
    CASH TOTAL / CASS POSITION / DENOMINATION / DISPENSED / REJECTED / REMAINING
    \\x1b[020t HH:MM:SS NOTES TAKEN
    {{{ "SESSION": N, "EVENT": "SESSION CLOSED", "DATE": "...Z" }}}
    \\x1b[020t HH:MM:SS TRANSACTION END
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ESC = "\x1b[020t"
ESC_CIM_ON = "\x1b(I"
ESC_CIM_OFF = "\x1b(1"

# Cross-file continuity: TXN NO / seq marker / SESSION must continue — not
# restart — from wherever the last generated file for a given ATM ID left
# off. Persist the last-used values per ATM ID here; _load_ncr_state /
# _save_ncr_state are the only readers/writers.
_STATE_DIR = Path(__file__).parent / "_ncr_state"


def _load_ncr_state(atm_id_full: str) -> dict:
    path = _STATE_DIR / f"{atm_id_full}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_ncr_state(atm_id_full: str, state: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _STATE_DIR / f"{atm_id_full}.json"
    path.write_text(json.dumps(state), encoding="utf-8")

CUSTOMER_NAMES = [
    "RAHUL SHARMA", "PRIYA VERMA", "AMIT SINGH", "SUNITA RAO", "VIKRAM MEHTA",
    "ANITA DESAI", "SANJAY GUPTA", "NEHA JOSHI", "ROHIT NAIR", "KAVITA IYER",
]
CUSTOMER_TITLES = ["MR.", "MRS.", "MS."]

# Deposit note counts (Rs.500 notes — cardless CIM deposit denomination)
DEPOSIT_NOTE_COUNTS = list(range(20, 120))

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS / REFERENCE DATA
# ─────────────────────────────────────────────────────────────────────────────

LOCATIONS = [
    "ADANI SHANTIGRAM DBU ",
    "SAKET DBU ",
    "KAROL BAGH DBU ",
    "NEHRU PLACE DBU ",
    "DARYAGANJ DBU ",
]

CARD_PREFIXES = ["817406", "428094", "652292", "508162", "421366",
                  "401138", "401347", "436393", "508979", "607947", "652211", "817335"]

AMOUNTS = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
           6000, 7000, 7500, 8000, 9000, 10000, 15000, 20000]

# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _gen_atm_id() -> str:
    return f"42{random.randint(1000, 9999)}"


def _random_pan(prefix: str) -> tuple:
    last4 = str(random.randint(1000, 9999))
    return f"{prefix}******{last4}"


def _random_account() -> str:
    total_len = random.choice([16, 17, 18, 19])
    real_len = min(random.randint(6, 12), total_len)
    number = str(random.randint(10 ** (real_len - 1), 10 ** real_len - 1))
    return number.rjust(total_len, "0")


def _random_balance(min_bal: int = 500, max_bal: int = 990000) -> float:
    return round(random.uniform(min_bal, max_bal), 2)


def _random_rrn(txn_no: int) -> str:
    """Real NX RRNs are either a '616'-prefixed 12-digit reference or
    literally equal to the TXN NO — never an unrelated random value."""
    if random.random() < 0.5:
        return str(txn_no)
    return f"616{random.randint(100, 299)}{random.randint(600000, 699999)}"


def _random_customer_name() -> str:
    return f"{random.randint(1, 999):03d}{random.choice(CUSTOMER_TITLES)} {random.choice(CUSTOMER_NAMES)}"


def _random_note_serial() -> str:
    """Banknote serial number: 1 digit + 2 letters + 6 digits, e.g. '3BD884457'."""
    lead_digit = random.choice("0123456789")
    letters = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=2))
    digits = "".join(random.choices("0123456789", k=6))
    return f"{lead_digit}{letters}{digits}"


def _random_from_ac_field(acc_no: str, width: int = 23) -> str:
    """Real FROM A/C fields vary: zero-padded (majority), fully asterisk-masked,
    raw unpadded digits, or entirely blank."""
    style = random.choices(["padded", "masked", "raw", "blank"], weights=[55, 15, 15, 15], k=1)[0]
    if style == "masked":
        return "*" * width
    if style == "raw":
        return f"{acc_no.lstrip('0') or '0':>{width}}"
    if style == "blank":
        return " " * width
    return f"{acc_no:>{width}}"


def _fmt_money_field(amount: float, width: int) -> str:
    return f"{'RS.' + f'{amount:.2f}':>{width}}"


# Response codes: 075/055 are REAL EVIDENCE (PIN tries exceeded / invalid
# PIN). 051/057/061/054 are INVENTED PLACEHOLDERS — every real decline found
# across all 6 pulled samples happens before amount entry, so there is no
# evidence for a post-amount-entry or card-stage decline code. Flag these to
# the host/switch team before trusting them for recon testing.
PIN_TRIES_EXCEEDED_CODE = "075"
INVALID_PIN_CODE = "055"
INSUFFICIENT_FUNDS_CODE = "051"   # INVENTED/UNVERIFIED
UNAUTHORIZED_CARD_CODE = "057"    # INVENTED/UNVERIFIED
DAILY_LIMIT_CODE = "061"          # INVENTED/UNVERIFIED
CARD_EXPIRED_CODE = "054"         # INVENTED/UNVERIFIED


def _denom_split(amount: int) -> tuple:
    n500 = amount // 500
    rem = amount % 500
    if n500 > 0 and random.random() < 0.4:
        n500 -= 1
        rem += 500
    n200 = rem // 200
    rem -= n200 * 200
    n100 = rem // 100
    return n100, n200, n500


def _utc_iso(local_dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds, IST-5:30 offset removed."""
    utc_dt = local_dt - timedelta(hours=5, minutes=30)
    millis = random.randint(0, 999)
    return f"{utc_dt.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def _json_block(session_id: int, event: str, local_dt: datetime) -> list:
    return [
        "{{{",
        f' "SESSION": {session_id},',
        f' "EVENT": "{event}",',
        f' "DATE": "{_utc_iso(local_dt)}"',
        "}}}",
    ]


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

    def event(self, text: str, delta_secs: int = 1, esc: bool = False, leading_space: bool = False) -> None:
        ts = self._next_ts(delta_secs)
        prefix = ESC + " " if esc else (" " if leading_space else "")
        suffix = f" {text}" if text else ""
        self._lines.append(f"{prefix}{ts.strftime('%H:%M:%S')}{suffix}")

    def raw(self, text: str = "") -> None:
        self._lines.append(text)

    def advance(self, secs: int) -> None:
        self._dt += timedelta(seconds=secs)

    @property
    def dt(self) -> datetime:
        return self._dt

    def get_text(self) -> str:
        return "\r".join(self._lines)


# ─────────────────────────────────────────────────────────────────────────────
# CASSETTE STATE (100 / 200 / 500 denominations)
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    def __init__(self):
        self.dispensed = {100: random.randint(20, 60), 200: random.randint(5, 20), 500: random.randint(80, 150)}
        self.rejected = {100: random.randint(1, 3), 200: random.randint(1, 3), 500: random.randint(1, 3)}
        self.remaining = {100: random.randint(900, 1000), 200: random.randint(950, 1000), 500: random.randint(2200, 2400)}
        self.unknown = 0

    def dispense(self, n100: int, n200: int, n500: int) -> None:
        self.dispensed[100] += n100
        self.dispensed[200] += n200
        self.dispensed[500] += n500
        self.remaining[100] = max(0, self.remaining[100] - n100)
        self.remaining[200] = max(0, self.remaining[200] - n200)
        self.remaining[500] = max(0, self.remaining[500] - n500)

    def table_lines(self) -> list:
        lines = [
            "CASH TOTAL       TYPE1 TYPE2 TYPE3 TYPE4",
            "CASS POSITION        1     2     3     0",
            "DENOMINATION       100   200   500     0",
            f"DISPENSED        {self.dispensed[100]:05d} {self.dispensed[200]:05d} {self.dispensed[500]:05d} 00000",
            f"REJECTED         {self.rejected[100]:05d} {self.rejected[200]:05d} {self.rejected[500]:05d} 00000",
            f"REMAINING        {self.remaining[100]:05d} {self.remaining[200]:05d} {self.remaining[500]:05d} 00000",
        ]
        if self.unknown:
            lines.append(f"UNKNOWN          00000 00000 00000 {self.unknown:05d}")
        return lines


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _gen_simple_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int, seq: int, session_id: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            force_reject: bool = False, force_unknown: bool = False,
                            retract_notes: bool = False, retain_card: bool = False,
                            split: bool = False) -> tuple:
    """Successful withdrawal, plus optional modifiers layered on the same
    happy-path flow (no real evidence for any of these on NX specifically —
    extrapolated from the shared PN/PR/IN/ER withdrawal shape):
      force_reject   -> a few notes counted into REJECTED (notes_in_reject)
      force_unknown  -> a few notes counted into an UNKNOWN denom row
      retract_notes  -> customer never takes the cash (cash_not_taken)
      retain_card    -> customer never takes the card (failure_to_collect_card)
      split          -> cash dispensed in two stacking batches (partial_split_transaction)
    """
    amount = random.choice(AMOUNTS)
    n100, n200, n500 = _denom_split(amount)
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = _random_rrn(txn_no)

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("PIN ENTERED", delta_secs=random.randint(5, 12), esc=True)
    w.event("OPCODE = AB     A", delta_secs=random.randint(5, 12), esc=True)
    w.event(f"REQUEST SENT [AMOUNT={amount:08d}]", delta_secs=0)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=A,  TXN SN NO={txn_no}]", delta_secs=0)
    w.event("GENAC 2 : TC", delta_secs=random.randint(0, 1), leading_space=True)

    if split:
        n100_a, n200_a, n500_a = n100 // 2, n200 // 2, max(1, n500 // 2)
        batches = [
            (n100_a, n200_a, n500_a),
            (n100 - n100_a, n200 - n200_a, n500 - n500_a),
        ]
    else:
        batches = [(n100, n200, n500)]

    if force_unknown:
        w.event("WRONG NOTES DETECTED- MOVED TO CONFIGURED CASSETTES",
                 delta_secs=random.randint(5, 10), esc=True)

    for _ in batches:
        w.event("NOTES STACKED", delta_secs=random.randint(6, 15), esc=True)
        w.raw(ESC)

    if retain_card:
        w.event("CARD RETAINED", delta_secs=random.randint(15, 30))
    else:
        w.event("CARD TAKEN", delta_secs=random.randint(3, 8))

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {masked_pan}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{_random_from_ac_field(acc_no)}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    for n100_i, n200_i, n500_i in batches:
        w.event("", delta_secs=random.randint(1, 3), esc=True)
        w.raw("NOTES PRESENTED")
        w.raw("POSITION 1,2,3,0")
        w.raw(f"COUNT    {n100_i},{n200_i},{n500_i},0")
        w.raw(ESC)

    cs.dispense(n100, n200, n500)
    if force_reject:
        n_rej = random.randint(1, 3)
        cs.rejected[500] += n_rej
    if force_unknown:
        cs.unknown += random.randint(1, 3)
    for ln in cs.table_lines():
        w.raw(ln)
    w.raw("")

    if retract_notes:
        w.event("NOTES RETRACTED", delta_secs=random.randint(20, 40), esc=True)
    else:
        w.event("NOTES TAKEN", delta_secs=random.randint(1, 3), esc=True)
    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_card_reader_marker(w: EJWriter, seq: int) -> None:
    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *PRIMARY CARD READER ACTIVATED*")


def _init_preamble(tran_date: datetime) -> str:
    """One-time file-open init sequence NCR emits before the first
    transaction (approximated — the real dump also contains raw binary
    control bytes that aren't worth reproducing exactly). The rollover
    record is dated the day BEFORE the file's first transaction (a
    23:58 EOD init from the prior business day), not the same day."""
    init_date = tran_date - timedelta(days=1)
    date_str = init_date.strftime("%m/%d/%Y")
    return "\r".join([
        "INIT BY EJ SCHEDULED INIT",
        "",
        f"*{date_str}*23:58:13*",
        "EJ LOG COPIED OK",
        "",
        "AUTO INIT COPY FAILED",
        "",
        "CONSUMER RESOURCE RELINQUISHED",
        "CONNECTIONS STATE COMPLETE-REASON:COMPLETE-000",
        "CONNECTIONS STATE STARTED",
    ])


def _gen_declined_balance_inquiry(w: EJWriter, txn_no: int, seq: int, session_id: int,
                                   location: str, atm_id_full: str) -> int:
    """Declined balance-inquiry attempt — host declines (GENAC AAC), nothing
    dispensed. Still consumes a TXN SN NO in the same sequence as withdrawals."""
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=1", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("PIN ENTERED", delta_secs=random.randint(5, 12), esc=True)
    w.event("OPCODE = CB     A", delta_secs=random.randint(5, 12), esc=True)
    w.event("REQUEST SENT", delta_secs=0)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=5,  TXN SN NO={txn_no}]", delta_secs=0)
    w.raw("EXTERNAL AUTHENTICATE: NO ARPC")
    w.event("GENAC 2 : AAC", delta_secs=random.randint(0, 1), leading_space=True)

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.raw(f"    LOCATION: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {masked_pan}")
    w.raw(f"    TXN NO:               {txn_no}")
    w.raw("    BALANCE INQUIRY")
    w.raw("    #SAVINGS")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw("    RESPONSE CODE              100")
    w.raw("    UNABLE TO PROCESS       ")
    w.raw("    RRN.                           ")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     ------------------------------------")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")

    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_balance_inquiry_success(w: EJWriter, txn_no: int, seq: int, session_id: int,
                                  location: str, atm_id_full: str) -> int:
    """Successful balance inquiry — the success sibling of
    _gen_declined_balance_inquiry: GENAC 2:TC instead of AAC, AVAILABLE BAL
    populated, RESPONSE CODE 000 and the full IDFC footer instead of the
    decline disclaimer block. Consumes a TXN SN NO like withdrawal."""
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)
    acc_no = _random_account()
    avail_bal = _random_balance()

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=1", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("PIN ENTERED", delta_secs=random.randint(5, 12), esc=True)
    w.event("OPCODE = CB     A", delta_secs=random.randint(5, 12), esc=True)
    w.event("REQUEST SENT", delta_secs=0)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=5,  TXN SN NO={txn_no}]", delta_secs=0)
    w.event("GENAC 2 : TC", delta_secs=random.randint(0, 1), leading_space=True)

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.raw(f"    LOCATION: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {masked_pan}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw("    BAL.INQUIRY     #SAVINGS")
    w.raw(f"    FROM A/C:{_random_from_ac_field(acc_no)}")
    w.raw(f"    AVAILABLE BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {_random_rrn(txn_no)}")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     ------------------------------------")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")

    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_decline_receipt_block(w: EJWriter, location: str, atm_id_full: str, masked_pan: str,
                                txn_no: int, response_code: str) -> None:
    """Shared decline-receipt tail, structurally identical to
    _gen_declined_balance_inquiry's receipt (same disclaimer block, verbatim),
    parameterized only by response code — reused by the withdrawal-flow
    decline variants below. The response code alone signals the scenario;
    RESPONSE CODE goes straight to 'UNABLE TO PROCESS' with nothing in
    between, matching the reference's only real decline example."""
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.raw(f"    LOCATION: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {masked_pan}")
    w.raw(f"    TXN NO:               {txn_no}")
    w.raw("    #SAVINGS")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(f"    RESPONSE CODE              {response_code}")
    w.raw("    UNABLE TO PROCESS       ")
    w.raw("    RRN.                           ")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     ------------------------------------")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEAN")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")


def _gen_pin_stage_decline(w: EJWriter, txn_no: int, seq: int, session_id: int,
                            location: str, atm_id_full: str,
                            response_code: str) -> int:
    """Decline after PIN entry but BEFORE amount selection — used for
    pin_tries_exceeded (075) and invalid_pin (055), both REAL EVIDENCE codes
    (from the EN sample; NX shape extrapolated from its own withdrawal/
    balance-inquiry skeleton). No AMOUNT= on REQUEST SENT since no amount was
    ever entered."""
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("PIN ENTERED", delta_secs=random.randint(5, 12), esc=True)
    w.event("OPCODE = AB     A", delta_secs=random.randint(5, 12), esc=True)
    w.event("REQUEST SENT", delta_secs=0)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=A,  TXN SN NO={txn_no}]", delta_secs=0)
    w.event("GENAC 2 : AAC", delta_secs=random.randint(0, 1), leading_space=True)

    _gen_decline_receipt_block(w, location, atm_id_full, masked_pan, txn_no, response_code)

    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_amount_stage_decline(w: EJWriter, txn_no: int, seq: int, session_id: int,
                               location: str, atm_id_full: str,
                               response_code: str) -> int:
    """Decline AFTER amount entry — used for declined_insufficient_funds (051)
    and daily_limit_exceeded (061). Both codes are INVENTED/UNVERIFIED: every
    real decline found across all 6 pulled samples happens before amount
    entry, so this shape (amount requested, then declined) has no direct
    evidence and should be confirmed against the real host response table."""
    amount = random.choice(AMOUNTS)
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("PIN ENTERED", delta_secs=random.randint(5, 12), esc=True)
    w.event("OPCODE = AB     A", delta_secs=random.randint(5, 12), esc=True)
    w.event(f"REQUEST SENT [AMOUNT={amount:08d}]", delta_secs=0)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=A,  TXN SN NO={txn_no}]", delta_secs=0)
    w.event("GENAC 2 : AAC", delta_secs=random.randint(0, 1), leading_space=True)

    _gen_decline_receipt_block(w, location, atm_id_full, masked_pan, txn_no, response_code)

    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_bin_stage_decline(w: EJWriter, txn_no: int, seq: int, session_id: int,
                            location: str, atm_id_full: str,
                            response_code: str) -> int:
    """Decline BEFORE PIN entry (card read/validated, then rejected) — used
    for declined_unauthorized_card (057) and card_expired (054), both
    INVENTED/UNVERIFIED codes with no direct evidence. A quick card-validation
    round-trip still occurs so a TXN SN NO is still consumed, but PIN is
    never requested."""
    card_prefix = random.choice(CARD_PREFIXES)
    masked_pan = _random_pan(card_prefix)

    w.raw(f"{ESC}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *TRANSACTION START*")
    w.raw(f"{ESC} CARD INSERTED")
    w.event("ATR RECEIVED T=0", delta_secs=random.randint(1, 2), leading_space=True)

    session_open_dt = w.dt
    for ln in _json_block(session_id, "SESSION CREATED", session_open_dt):
        w.raw(ln)

    w.advance(random.randint(1, 3))
    w.raw(f"{ESC}CARD: {masked_pan}")
    w.raw(f"DATE {w.dt.strftime('%d-%m-%y')}    TIME {w.dt.strftime('%H:%M:%S')}")

    w.event("REQUEST SENT", delta_secs=random.randint(2, 5), esc=True)
    w.event("GENAC 1 : ARQC", delta_secs=random.randint(1, 2), leading_space=True)
    w.event(f"RESPONSE RECEIVED [FUNCTION ID=A,  TXN SN NO={txn_no}]", delta_secs=0)
    w.event("GENAC 2 : AAC", delta_secs=random.randint(0, 1), leading_space=True)

    _gen_decline_receipt_block(w, location, atm_id_full, masked_pan, txn_no, response_code)

    for ln in _json_block(session_id, "SESSION CLOSED", w.dt):
        w.raw(ln)
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no + 1


def _gen_cim_deposit(w: EJWriter, txn_no: int, seq: int, location: str, atm_id_full: str) -> int:
    """NX cardless cash-in (CIM) deposit — customer inserts notes without a
    card. Uses a distinct opening marker ('CARDLESS TRANSACTION START' under
    the (I/(1 escape pair, not the usual [020t*SEQ*.../TRANSACTION START'),
    a CIM event stream (activate/shutter/insert/present/take), banknote
    serial capture, and a cassette-breakdown table before the usual-shaped
    CASH DEPOSIT PARTICULARS receipt. Consumes two TXN NOs like ER/PR
    deposits (authorize + confirm)."""
    count = random.choice(DEPOSIT_NOTE_COUNTS)
    amount = count * 500
    acc_no = _random_account()
    name_field = _random_customer_name()
    txn_no_1 = txn_no
    txn_no_2 = txn_no + 1

    w.raw(f"{ESC_CIM_ON}*{seq}*{w.dt.strftime('%m/%d/%Y')}*{w.dt.strftime('%H:%M:%S')}*")
    w.raw("     *CARDLESS TRANSACTION START*")
    w.raw(ESC_CIM_OFF)
    w.event("CIM-DEPOSIT ACTIVATED", delta_secs=random.randint(2, 5))
    w.event("CIM-SHUTTER OPENED", delta_secs=random.randint(3, 8))
    w.event("CIM-ITEMS INSERTED", delta_secs=random.randint(8, 20))

    serials = [_random_note_serial() for _ in range(count)]
    w.raw("SERIAL NUMBERS:")
    w.raw(",".join(serials))

    w.event("CASHIN DEPOSIT SELECTED", delta_secs=random.randint(2, 5))
    w.event("REQUEST SENT", delta_secs=0)
    w.raw("(Operation Code : BC     A)")
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 3))
    w.raw(f"(Host Sequence No. : {txn_no_1},  TXN SN NO={txn_no_1})")

    w.event("CIM-ITEMS PRESENTED", delta_secs=random.randint(3, 8))
    w.event("CIM-ITEMS TAKEN", delta_secs=random.randint(2, 5))
    w.event("CIM-DEPOSIT COMPLETED", delta_secs=random.randint(2, 5))
    w.event("REQUEST SENT", delta_secs=0)
    w.raw("(Operation Code : BCC     )")
    w.event("RESPONSE RECEIVED", delta_secs=random.randint(1, 3))
    w.raw(f"(Host Sequence No. : {txn_no_2},  TXN SN NO={txn_no_2})")

    w.raw("DENOM   ABOX    CASS2   CASS3   CASS4   ")
    w.raw(f"  500   {count:05d}   00000   00000   00000")
    w.raw("DENOM   CASS5   RETRACT REJECT  CNTRFEIT")
    w.raw("  500   00000   00000   00000   00000")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%d/%m/%y")
    w.raw(f"    ATM ADD: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name_field}")
    w.raw("    CARD NUMBER:  888888******8888")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no_2}")
    w.raw("    RRN.                           ")
    w.raw("    CASH DEPOSIT PARTICULARS")
    w.raw(f"    DEPOSIT AMOUNT        RS.{amount}.00")
    w.raw("    DENOMS   COUNTS   SUB TOTALS.")
    w.raw(f"    0500     {count:03d}       {amount}")
    w.raw(f"    TOTAL:       {amount}.00")
    w.raw("    RESPONSE CODE              000")

    w.event("TRANSACTION END", delta_secs=random.randint(5, 15), esc=True)

    return txn_no_2 + 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_ncr_ej(
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
    continuation: dict = None,
) -> dict:
    """
    Generate an NCR EJ file for IDFC First Bank ATMs.

    Args:
        tran_date: transaction date
        num_transactions: number of customer transactions
        selected_cases: list of case IDs to include (currently: 'simple_withdrawal')
        atm_id: numeric ATM ID string, e.g. '423561' (auto-generated if None)
        location: branch/DBU name (random if None)
        output_dir: output directory (uses /tmp if None)
        continuation: optional {"next_txn_no": int, "next_seq_marker": int,
            "next_session_id": int} from a prior run's result to keep TXN SN
            NO / seq marker / session ID continuous across multiple files for
            the same ATM+day batch ("sync with other files"), instead of
            restarting at fresh random values.

    Returns:
        dict with run_id, file_name, atm_id, location, counts, continuation
    """
    random.seed()  # non-deterministic

    run_id = uuid.uuid4().hex[:12]
    if atm_id is None:
        atm_id = _gen_atm_id()
    atm_id_full = f"NX{atm_id}"
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # File naming: NX{ATM_ID}_{YYYYMMDD}.txt
    date_str_file = tran_date.strftime("%Y%m%d")
    file_name = f"{atm_id_full}_{date_str_file}.txt"

    start_dt = tran_date.replace(hour=9, minute=0, second=0, microsecond=0)
    w = EJWriter(start_dt)
    cs = CassetteState()

    case_set = set(selected_cases) if selected_cases else set()
    do_deposit = "cash_deposit" in case_set

    # This ATM's current scope is withdrawal-only (no cash-deposit / admin /
    # host-timeout / power-cut logic here) and every generated file must
    # include all 13 required scenarios at least once — build a schedule that
    # guarantees each appears, then pad it out to num_transactions with
    # weighted-random extras and shuffle the order. cash_deposit (CIM) stays
    # a separate opt-in add-on, gated by selected_cases as before, layered on
    # top rather than competing with the guaranteed schedule.
    REQUIRED_SCENARIOS = [
        "simple_withdrawal", "balance_inquiry_success",
        "cash_not_taken", "failure_to_collect_card", "notes_in_reject",
        "pin_tries_exceeded", "invalid_pin",
        "declined_insufficient_funds", "declined_unauthorized_card",
        "daily_limit_exceeded", "card_expired",
        "partial_split_transaction", "unknown_denom_notes",
    ]
    PADDING_SCENARIOS = ["simple_withdrawal", "declined_balance_inquiry"]
    PADDING_WEIGHTS = [85, 15]

    if num_transactions < len(REQUIRED_SCENARIOS):
        num_transactions = len(REQUIRED_SCENARIOS)

    schedule = list(REQUIRED_SCENARIOS)
    extra_needed = num_transactions - len(schedule)
    if extra_needed > 0:
        schedule += random.choices(PADDING_SCENARIOS, weights=PADDING_WEIGHTS, k=extra_needed)
    random.shuffle(schedule)

    # Cross-file continuity: continue TXN NO / seq marker / SESSION from
    # wherever the last generated file for this ATM ID left off, unless the
    # caller passed an explicit continuation (manual override) or no prior
    # file exists yet for this ATM ID (fresh seed).
    persisted = _load_ncr_state(atm_id_full)
    if continuation and continuation.get("next_txn_no") is not None:
        txn_no = continuation["next_txn_no"]
    elif persisted.get("next_txn_no") is not None:
        txn_no = persisted["next_txn_no"]
    else:
        txn_no = random.randint(9000, 9900)
    if continuation and continuation.get("next_seq_marker") is not None:
        seq_marker = continuation["next_seq_marker"]
    elif persisted.get("next_seq_marker") is not None:
        seq_marker = persisted["next_seq_marker"]
    else:
        seq_marker = random.randint(130, 400)
    if continuation and continuation.get("next_session_id") is not None:
        session_id = continuation["next_session_id"]
    elif persisted.get("next_session_id") is not None:
        session_id = persisted["next_session_id"]
    else:
        session_id = random.randint(20000, 29999)

    counts = {"total": 0, "cash_deposit": 0, "declined_balance_inquiry": 0}
    for name in REQUIRED_SCENARIOS:
        counts[name] = 0

    w.raw(_init_preamble(tran_date))
    _gen_card_reader_marker(w, seq_marker)

    for outcome in schedule:
        w.advance(random.randint(60, 900))
        seq_marker += 1
        # A CIM cardless deposit consumes 2 TXN SN NOs (authorize + confirm)
        # but is 1 customer session, so it only counts once against
        # num_transactions — same convention as ER/PR deposits. It's an
        # opt-in add-on layered on top of the guaranteed schedule, so it
        # never displaces a required scenario's slot.
        if do_deposit and random.random() < 0.15:
            txn_no = _gen_cim_deposit(w, txn_no, seq_marker, location, atm_id_full)
            counts["cash_deposit"] += 1
            counts["total"] += 1
        else:
            if outcome == "simple_withdrawal":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full)
            elif outcome == "cash_not_taken":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full,
                                                 retract_notes=True)
            elif outcome == "notes_in_reject":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full,
                                                 force_reject=True)
            elif outcome == "partial_split_transaction":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full,
                                                 split=True)
            elif outcome == "unknown_denom_notes":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full,
                                                 force_unknown=True)
            elif outcome == "failure_to_collect_card":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, seq_marker, session_id, tran_date, location, atm_id_full,
                                                 retain_card=True)
            elif outcome == "declined_balance_inquiry":
                txn_no = _gen_declined_balance_inquiry(w, txn_no, seq_marker, session_id, location, atm_id_full)
            elif outcome == "balance_inquiry_success":
                txn_no = _gen_balance_inquiry_success(w, txn_no, seq_marker, session_id, location, atm_id_full)
            elif outcome == "pin_tries_exceeded":
                txn_no = _gen_pin_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                 PIN_TRIES_EXCEEDED_CODE)
            elif outcome == "invalid_pin":
                txn_no = _gen_pin_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                 INVALID_PIN_CODE)
            elif outcome == "declined_insufficient_funds":
                txn_no = _gen_amount_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                    INSUFFICIENT_FUNDS_CODE)
            elif outcome == "daily_limit_exceeded":
                txn_no = _gen_amount_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                    DAILY_LIMIT_CODE)
            elif outcome == "declined_unauthorized_card":
                txn_no = _gen_bin_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                 UNAUTHORIZED_CARD_CODE)
            elif outcome == "card_expired":
                txn_no = _gen_bin_stage_decline(w, txn_no, seq_marker, session_id, location, atm_id_full,
                                                 CARD_EXPIRED_CODE)

            counts[outcome] += 1
            session_id += 1
            counts["total"] += 1
        w.advance(random.randint(2, 8))
        seq_marker += 1
        _gen_card_reader_marker(w, seq_marker)

    out_path = output_dir / file_name
    with open(out_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(w.get_text() + "\r")

    # seq_marker holds the LAST value written to the file (it's used as-is
    # for a marker, then incremented before the next one) — persist/return
    # seq_marker + 1 so a later run's initial marker doesn't repeat it.
    next_seq_marker = seq_marker + 1
    _save_ncr_state(atm_id_full, {
        "next_txn_no": txn_no,
        "next_seq_marker": next_seq_marker,
        "next_session_id": session_id,
    })

    cases_included = [c for c in counts if c != "total" and counts[c] > 0]

    manifest = {
        "run_id": run_id,
        "bank_id": "idfc",
        "vendor": "ncr",
        "atm_type": "NX",
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
        "continuation": {
            "next_txn_no": txn_no,
            "next_seq_marker": next_seq_marker,
            "next_session_id": session_id,
        },
    }
