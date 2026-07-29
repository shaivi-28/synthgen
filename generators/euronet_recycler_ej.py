"""
EuroNet Recycler (ER) Electronic Journal (EJ) Generator for IDFC First Bank ATMs.

Unlike the plain EuroNet (EN) dispense-only format, ER is a recycler with a very
different raw-dump shape: the file opens with a startup sequence (cassette/cash
count status, denomination counts, out-of-service/start-up/go-in-service events),
and each transaction is framed by "==> DATE TIME  Terminal ID : ATMID" / "Machine
Sequence No" markers rather than the "== MONTH DD, YYYY ATMID ==" header used by
EN/EPS/FSS. Transactions include per-step camera "Take Picture" events with
{pic-person}/{pic-exitslot} photo references, banknote separation/ejection
counters, and a running per-cassette suffix (A/B/C/D/E/F/R). Deposit transactions
(with photo refs, inserted-banknote serials, stored-note counts) also occur in
the reference dump but are out of scope for this first pass — only the simple
withdrawal case is implemented here.

Reference structure (withdrawal transaction):
    ==> DATE TIME  Terminal ID : ATMID
        Machine Sequence No : NNNNNN
        HH:MM:SS Read Result : ICC
    {pic-person}...CardInserted
        HH:MM:SS Take Picture(Face) : Succeeded / CardInserted
        HH:MM:SS AID : ... / AppLabel : ...
        HH:MM:SS Card Number : PPPP********SSSS
    {pic-person}...PINEntered
        HH:MM:SS Take Picture(Face) : Succeeded / PINEntered
        HH:MM:SS Entered Amount is : (Rs.NNNN       )
        HH:MM:SS 1stGENAC Result : ARQC
        HH:MM:SS Transaction Req. Send : Succeeded (Operation Code : AB   C A)
        HH:MM:SS Transaction Res. Received (Host Sequence No. : NNNN,FID : A,NextState : 121)
        HH:MM:SS 2ndGENAC Result : TC / TVR / TSI
        HH:MM:SS Banknote separation in cassette : Succeeded + cassette suffix
    {pic-person}...CardRemoved / Card ejection / Shutter Open-Close
    {pic-exitslot}/{pic-person}...Removed
        receipt block (branch, ATM ID, masked card, TXN NO., WITHDRAWAL, FROM A/C,
        AVAIL BAL, RESPONSE CODE, RRN)
        HH:MM:SS SolicitedStatus Send / Card ejection : No card / Transaction End
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
    "ASHOK NAGAR BRANCH AT",
    "KAROL BAGH BRANCH AT",
    "SAKET BRANCH AT",
    "NEHRU PLACE BRANCH AT",
    "DARYAGANJ BRANCH AT",
]

CARD_PREFIXES = ["401347", "401613", "817406", "652163", "428094"]

NETWORKS = [
    ("A0000005241010", "RuPay Debit"),
    ("A0000000031010", "VISA CREDIT"),
    ("A0000000031010", "VISA DEBIT"),
]

AMOUNTS = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000,
           6000, 7000, 7500, 8000, 9000, 10000, 15000, 20000]

CUSTOMER_NAMES = [
    "RAHUL SHARMA", "PRIYA VERMA", "AMIT SINGH", "SUNITA RAO", "VIKRAM MEHTA",
    "ANITA DESAI", "SANJAY GUPTA", "NEHA JOSHI", "ROHIT NAIR", "KAVITA IYER",
]

CUSTOMER_TITLES = ["MR.", "MRS.", "MS."]

# Deposit note counts (mostly Rs.500 notes — recycler deposit cassette denomination)
DEPOSIT_NOTE_COUNTS = list(range(4, 41))

# Real evidence (PIN-stage declines): RESPONSE CODE 075/055 with a literal
# extra message line right after RESPONSE CODE.
PIN_TRIES_EXCEEDED_CODE = "075"
INVALID_PIN_CODE = "055"

# INVENTED / UNVERIFIED — no real evidence anywhere in the mined samples
# (every real decline happens before amount entry, at the PIN/card stage).
# Confirm these against the actual host/switch response-code table before
# trusting them for recon testing.
INSUFFICIENT_FUNDS_CODE = "051"
UNAUTHORIZED_CARD_CODE = "057"
DAILY_LIMIT_CODE = "061"
CARD_EXPIRED_CODE = "054"

# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _gen_atm_id() -> str:
    return f"80{random.randint(1000, 9999)}"


def _random_pan(prefix: str) -> tuple:
    """Returns (event_masked '4dig********4dig', receipt_masked '6dig******4dig', photo_masked lowercase-x)."""
    last4 = str(random.randint(1000, 9999))
    prefix4 = prefix[:4]
    event_masked = f"{prefix4}********{last4}"
    receipt_masked = f"{prefix}******{last4}"
    photo_masked = f"{prefix4}xxxxxxxx{last4}"
    return event_masked, receipt_masked, photo_masked


def _random_customer_name() -> str:
    return f"{random.choice(CUSTOMER_TITLES)} {random.choice(CUSTOMER_NAMES)}"


def _random_account() -> str:
    total_len = random.choice([16, 17, 18, 19])
    real_len = min(random.randint(6, 12), total_len)
    number = str(random.randint(10 ** (real_len - 1), 10 ** real_len - 1))
    return number.rjust(total_len, "0")


def _account_photo_mask(acc_no: str) -> str:
    """Masked account identifier for a cardless (account-entry) deposit: the
    first 4 digits after stripping leading zeros + literal 'XXX' + last 4
    digits, e.g. '1010XXX7432'."""
    stripped = acc_no.lstrip("0") or acc_no
    return f"{stripped[:4]}XXX{acc_no[-4:]}"


def _random_balance(min_bal: int = 500, max_bal: int = 990000) -> float:
    return round(random.uniform(min_bal, max_bal), 2)


def _fmt_money_field(amount: float, width: int) -> str:
    return f"{'RS.' + f'{amount:.2f}':>{width}}"


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


def _random_reject_count() -> int:
    """Real reject counts are mostly 0, occasionally 1-3."""
    return random.choices([0, 1, 2, 3], weights=[58, 11, 5, 2], k=1)[0]


def _deposit_note_mix() -> tuple:
    """Returns (n100, n200, n500, amount). Real deposits are mostly pure
    Rs.500 notes, but sometimes include a 100/200 mix."""
    n500 = random.choice(DEPOSIT_NOTE_COUNTS)
    n100 = n200 = 0
    if random.random() < 0.3:
        n200 = random.randint(0, 3)
        n100 = random.randint(0, 3)
    amount = n100 * 100 + n200 * 200 + n500 * 500
    return n100, n200, n500, amount


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

    def event(self, text: str, delta_secs: int = 1) -> None:
        """Emit a 4-space-indented 'HH:MM:SS text' line (ER indents every event line)."""
        ts = self._next_ts(delta_secs)
        self._lines.append(f"    {ts.strftime('%H:%M:%S')} {text}")

    def raw(self, text: str = "") -> None:
        self._lines.append(text)

    def advance(self, secs: int) -> None:
        self._dt += timedelta(seconds=secs)

    @property
    def dt(self) -> datetime:
        return self._dt

    def get_text(self) -> str:
        return "\r\n".join(self._lines)


# ─────────────────────────────────────────────────────────────────────────────
# CASSETTE STATE (recycler: A/B=500, C=200, D=100, E=dep&rej&ret, F=retract, R=retained)
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    def __init__(self):
        self.a = 2535
        self.b = 2569
        self.c = 4
        self.d = 10
        self.e = 1702
        self.f = 0
        self.r = 1
        self.dispensed = {100: 0, 200: 0, 500: 0}
        self.deposited = {100: 0, 200: 0, 500: 0}
        self.rejected = {100: random.randint(1, 3), 200: random.randint(1, 3), 500: random.randint(1, 3)}
        self.remaining = {100: 970, 200: 990, 500: 2372}

    def dispense(self, n100: int, n200: int, n500: int) -> None:
        take500 = n500
        from_a = min(self.a, take500)
        self.a -= from_a
        take500 -= from_a
        from_b = min(self.b, take500)
        self.b -= from_b
        self.c = max(0, self.c - n200)
        self.d = max(0, self.d - n100)
        self.dispensed[100] += n100
        self.dispensed[200] += n200
        self.dispensed[500] += n500
        self.remaining[100] = max(0, self.remaining[100] - n100)
        self.remaining[200] = max(0, self.remaining[200] - n200)
        self.remaining[500] = max(0, self.remaining[500] - n500)

    def deposit(self, n100: int, n200: int, n500: int) -> None:
        self.e += n100 + n200 + n500
        self.deposited[100] += n100
        self.deposited[200] += n200
        self.deposited[500] += n500
        self.remaining[100] += n100
        self.remaining[200] += n200
        self.remaining[500] += n500

    def suffix(self) -> str:
        return (f"A{self.a:04d}:B{self.b:04d}:C{self.c:04d}:D{self.d:04d}:"
                f"E{self.e:04d}:F{self.f:04d}:R{self.r:04d}")


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP PREAMBLE (cassette info, denomination counts, out-of-service events)
# ─────────────────────────────────────────────────────────────────────────────

def _startup_preamble(tran_date: datetime, atm_id_full: str) -> str:
    prev_date = (tran_date - timedelta(days=4)).strftime("%m-%d-%Y")
    date_str = tran_date.strftime("%m-%d-%Y")
    return "\r\n".join([
        "    00:01:00 Restart process started by program",
        "---Cassette Information---",
        f"     Terminal ID: {atm_id_full}",
        f"     Accounting Cycle Start:      {prev_date} 10:16:28",
        "     Accounting Cycle End:        -",
        "             ****        Cash count             ****",
        "           Type         Denomi     Initial  Current  Status",
        "     CAS_A Recycle      Rs.500           0     2535  Full  ",
        "    CAS_B Recycle      Rs.500        1200     2569  Full  ",
        "    CAS_C Recycle      Rs.200           0        4  Empty ",
        "    CAS_D Recycle      Rs.100           0       10  Empty ",
        "    CAS_E Dep&Rej&Ret  -                0     1702  Normal",
        "    CAS_F Retract      -                0        0  Empty ",
        "    CAS_R Retained     -                0        1  Normal",
        "     Total replenishment                          600000.00",
        "     Current total count                         3392600.00",
        f"Terminal ID:   00000000{atm_id_full}",
        f"                                       {date_str} 00:01:00",
        "            ****    Denomination count    ****",
        "Note              Initial    Dispensed    Deposited    Remaining",
        "Rs.10                   0            0            0            0",
        "Rs.20                   0            0            0            0",
        "Rs.50                   0            0            0            0",
        "Rs.100                  0          642          672           30",
        "Rs.200                  0          244          262           18",
        "Rs.500               1200         3798         9370         6772",
        "Rs.1000                 0            0            0            0",
        "Rs.2000                 0            0            0            0",
        "Total amount       600000      2012000      4804600      3392600",
        "    00:01:00 OUT OF SERVICE",
        f"    {date_str} 00:11:10  Start Up",
        "    (Software version : 02.00.24.089.018.EN)",
        "    REC1:04.21 REC2:04.21 REC3:04.21 REC4:00.00",
        "    REC5:00.02 REC6:99.99 REC7:02.00 REC8:02.12",
        "    00:11:12 OFFLINE",
        "    00:11:50 UnSolicitedStatus  Send : Succeeded",
        "    00:11:50 Anti-Skimming Sensor : OFF",
        "    00:11:59 GO IN SERVICE",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _gen_simple_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            force_reject_count: int = None) -> int:
    amount = random.choice(AMOUNTS)
    n100, n200, n500 = _denom_split(amount)
    card_prefix = random.choice(CARD_PREFIXES)
    event_masked, receipt_masked, photo_masked = _random_pan(card_prefix)
    aid, applabel = random.choice(NETWORKS)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = f"{txn_no}        "

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    w.event("Read Result : ICC", delta_secs=0)
    mmddyy = w.dt.strftime("%m%d%y")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_NA_CardInserted")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             CardInserted")

    w.event(f"AID : {aid}", delta_secs=random.randint(3, 8))
    w.event(f"AppLabel : {applabel}", delta_secs=0)
    w.event(f"Card Number : {event_masked}", delta_secs=random.randint(1, 3))

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_PINEntered")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(8, 16))
    w.raw("             PINEntered")

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")
    w.event("2ndGENAC Result : TC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 7000", delta_secs=0)

    w.event("Banknote separation", delta_secs=random.randint(6, 12))
    w.raw("                        in cassette : Succeeded")
    w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}   ")
    reject_count = _random_reject_count() if force_reject_count is None else force_reject_count
    w.raw(f"    Reject   :{reject_count}    ")
    cs.dispense(n100, n200, n500)
    w.raw(f"    {cs.suffix()}")

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Shutter Open -> Take Cash", delta_secs=0)
    w.event("Shutter Close", delta_secs=random.randint(6, 12))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Banknote ejection to", delta_secs=0)
    w.raw("                    banknote bucket : Succeeded")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card ejection : No card", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_declined_withdrawal(w: EJWriter, txn_no: int, machine_seq: int,
                              tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Real ER withdrawals decline roughly a third of the time — PIN/card
    accepted, but the host declines (GENAC AAC) and no cash is dispensed.
    Still consumes a TXN NO/Machine Sequence No like a success. The
    disclaimer block's odd hard line-wraps ('CLEA'/'N' split,
    '-----...-'/'-' split) are a static template quirk in the real dump —
    reproduced literally."""
    card_prefix = random.choice(CARD_PREFIXES)
    event_masked, receipt_masked, photo_masked = _random_pan(card_prefix)
    aid, applabel = random.choice(NETWORKS)
    amount = random.choice(AMOUNTS)

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    w.event("Read Result : ICC", delta_secs=0)
    mmddyy = w.dt.strftime("%m%d%y")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_NA_CardInserted")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             CardInserted")

    w.event(f"AID : {aid}", delta_secs=random.randint(3, 8))
    w.event(f"AppLabel : {applabel}", delta_secs=0)
    w.event(f"Card Number : {event_masked}", delta_secs=random.randint(1, 3))

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_PINEntered")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(8, 16))
    w.raw("             PINEntered")

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AC      )")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : 5,NextState : 048)")
    w.event("2ndGENAC Result : AAC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 6000", delta_secs=0)

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    card_tag = random.choice(["#CREDIT CARD", "#SAVINGS"])
    w.raw(f"    LOCATION: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO:               {txn_no}")
    w.raw("    WITHDRAWAL")
    w.raw(f"    {card_tag}")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw("    RESPONSE CODE              100")
    w.raw("    UNABLE TO PROCESS       ")
    w.raw("    RRN.                           ")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     -----------------------------------")
    w.raw("-")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEA")
    w.raw("N")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Receipt printed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             Receipt printed")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(1, 3))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_deposit(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                  tran_date: datetime, location: str, atm_id_full: str, card_based: bool = False) -> int:
    """Recycler cash deposit — deposited notes are mostly Rs.500 with an
    occasional 100/200 mix (cassette denominations).

    A deposit is a single customer session but two Host round-trips (two
    consecutive TXN NOs): the first authorizes the deposit and opens the
    shutter for note insertion; the second confirms/stores the counted cash
    and prints the final receipt. Card-based deposits run the full EMV
    round-trip (1stGENAC/2ndGENAC/TVR/TSI, opcode 'B CC   A'/'B B') and end
    with the card being physically removed; cardless deposits use a lighter
    opcode ('BC     A'/'BCC') and end with 'Card ejection : No card' since
    there's no card to remove.
    """
    n100, n200, n500, amount = _deposit_note_mix()
    acc_no = _random_account()
    name = _random_customer_name()
    txn_no_1 = txn_no
    txn_no_2 = txn_no + 1

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    if card_based:
        card_prefix = random.choice(CARD_PREFIXES)
        event_masked, receipt_masked, photo_masked = _random_pan(card_prefix)
        aid, applabel = random.choice(NETWORKS)
        card_no_field = receipt_masked
        photo_tag = photo_masked

        w.event("Read Result : ICC", delta_secs=0)
        mmddyy = w.dt.strftime("%m%d%y")
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_NA_CardInserted")
        w.event("Take Picture(Face) : Succeeded", delta_secs=0)
        w.raw("             CardInserted")
        w.event(f"AID : {aid}", delta_secs=random.randint(3, 8))
        w.event(f"AppLabel : {applabel}", delta_secs=0)
        w.event(f"Card Number : {event_masked}", delta_secs=random.randint(1, 3))
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_PINEntered")
        w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(8, 16))
        w.raw("             PINEntered")
        w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
        opcode_init = "B CC   A"
        next_state_init = "395"
    else:
        card_no_field = "888888******8888"
        photo_tag = _account_photo_mask(acc_no)
        mmddyy = w.dt.strftime("%m%d%y")

        masked_acc = _account_photo_mask(acc_no)
        w.event("Non Card Transaction", delta_secs=0)
        w.event("Entered Data", delta_secs=random.randint(5, 12))
        w.raw(f"    ({masked_acc}" + " " * 21 + ")")
        w.event("Entered Data", delta_secs=random.randint(3, 8))
        w.raw(f"    ({masked_acc}" + " " * 21 + ")")
        opcode_init = "BC     A"
        next_state_init = "588"

    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw(f"    (Operation Code : {opcode_init})")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no_1},FID : 5,NextState : {next_state_init})")
    if card_based:
        w.event("2ndGENAC Result : TC", delta_secs=0)
        w.event("TVR : 8080040000", delta_secs=0)
        w.event("TSI : 7000", delta_secs=0)

    receipt_date = w.dt.strftime("%m/%d/%y")
    receipt_time = w.dt.strftime("%H:%M")
    w.raw(f"    ATM ADD: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name:<30}")
    w.raw(f"    CARD NUMBER:  {card_no_field}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no_1}")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Shutter Open -> Insert Cash", delta_secs=random.randint(3, 8))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Inserted")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=random.randint(6, 15))
    w.raw("             NotesInserted")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Inserted")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesInserted")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    w.event("Counted banknote :", delta_secs=random.randint(5, 12))
    w.raw(f"     Rs.100x{n100}       Rs.200x{n200}       Rs.500x{n500}      ")
    w.raw(f"     Rejectx{_random_reject_count()}       ")
    w.raw(f"    Amount Rs.{amount}")
    w.raw(f"    Total Amount Rs.{amount}")
    w.raw("    No Counterfeit notes")
    w.raw(cs.suffix())

    w.event("Shutter Open -> Take Cash", delta_secs=random.randint(3, 8))
    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Removed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_DepositConfirmed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             DepositConfirmed")

    opcode_complete = "B B     " if card_based else "BCC     "
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(2, 5))
    w.raw(f"    (Operation Code : {opcode_complete})")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no_2},FID : -,NextState : 408)")

    w.event("Stored banknote :", delta_secs=random.randint(3, 8))
    w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}   ")
    w.raw("    No Counterfeit notes")
    cs.deposit(n100, n200, n500)
    w.raw(cs.suffix())

    receipt_date2 = w.dt.strftime("%m/%d/%y")
    receipt_time2 = w.dt.strftime("%H:%M")
    w.raw(f"    ATM ADD: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date2}   {receipt_time2}      {atm_id_full}")
    w.raw(f"    NAME: {name:<30}")
    w.raw(f"    CARD NUMBER:  {card_no_field}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no_2}")
    w.raw("    RRN.                           ")
    w.raw("    CASH DEPOSIT PARTICULARS")
    w.raw(f"    DEPOSIT AMOUNT        RS.{amount}.00")
    w.raw("    DENOMS   COUNTS   SUB TOTALS.")
    if n100 > 0:
        w.raw(f"    0100     {n100:03d}       {n100 * 100}")
    if n200 > 0:
        w.raw(f"    0200     {n200:03d}       {n200 * 200}")
    if n500 > 0:
        w.raw(f"    0500     {n500:03d}       {n500 * 500}")
    w.raw(f"    TOTAL:       {amount}.00")
    w.raw("    RESPONSE CODE              000")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Receipt printed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             Receipt printed")
    if card_based:
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_CardRemoved")
        w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(1, 3))
        w.raw("             CardRemoved")
        w.event("Card ejection : Succeeded", delta_secs=0)
    else:
        w.event("Card ejection : No card", delta_secs=random.randint(1, 3))
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no_2 + 1


# ─────────────────────────────────────────────────────────────────────────────
# SHARED OPENING / DECLINE HELPERS (new scenarios below only — the three
# validated functions above keep their own inline copies untouched)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_card_opening(w: EJWriter, atm_id_full: str) -> tuple:
    """Read Result:ICC -> CardInserted pic -> AID/AppLabel/Card Number.
    Returns (event_masked, receipt_masked, photo_masked, mmddyy)."""
    card_prefix = random.choice(CARD_PREFIXES)
    event_masked, receipt_masked, photo_masked = _random_pan(card_prefix)
    aid, applabel = random.choice(NETWORKS)

    w.event("Read Result : ICC", delta_secs=0)
    mmddyy = w.dt.strftime("%m%d%y")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_NA_CardInserted")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             CardInserted")

    w.event(f"AID : {aid}", delta_secs=random.randint(3, 8))
    w.event(f"AppLabel : {applabel}", delta_secs=0)
    w.event(f"Card Number : {event_masked}", delta_secs=random.randint(1, 3))

    return event_masked, receipt_masked, photo_masked, mmddyy


def _gen_pin_entered_pic(w: EJWriter, atm_id_full: str, mmddyy: str, photo_masked: str) -> None:
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_PINEntered")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(8, 16))
    w.raw("             PINEntered")


def _gen_decline_receipt(w: EJWriter, location: str, receipt_masked: str, txn_no: int,
                          body_lines: list, response_code: str, extra_message: str,
                          atm_id_full: str) -> None:
    """The shared decline-receipt block (LOCATION/DATE/CARD NUMBER/TXN NO/
    body/SORRY UNABLE TO PROCESS/RESPONSE CODE/[extra message]/blank
    RRN/disclaimer) — same shape/wording as _gen_declined_withdrawal's
    receipt, reused across every new decline scenario below."""
    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    LOCATION: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO:               {txn_no}")
    for bl in body_lines:
        w.raw(f"    {bl}")
    w.raw("    SORRY UNABLE TO PROCESS")
    w.raw(f"    RESPONSE CODE              {response_code}")
    w.raw(f"    {extra_message}       " if extra_message else "    UNABLE TO PROCESS       ")
    w.raw("    RRN.                           ")
    w.raw("     IDFC... YOUR PARTNER IN GROWTH!")
    w.raw("     -----------------------------------")
    w.raw("-")
    w.raw("     IF YOU DON'T FIND THE ATM SITE CLEA")
    w.raw("N")
    w.raw("        PLEASE DIAL +975-2-332540 AND")
    w.raw("         HELP US TO SERVE YOU BETTER")


def _gen_decline_ending(w: EJWriter, atm_id_full: str, mmddyy: str, photo_masked: str) -> None:
    """SolicitedStatus Send -> Receipt printed pic -> CardRemoved pic ->
    Card ejection Succeeded -> Transaction End (shared tail, mirrors
    _gen_declined_withdrawal's ending exactly)."""
    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Receipt printed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             Receipt printed")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(1, 3))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))


# ─────────────────────────────────────────────────────────────────────────────
# NEW SCENARIOS
# ─────────────────────────────────────────────────────────────────────────────

def _gen_balance_inquiry(w: EJWriter, txn_no: int, machine_seq: int,
                          tran_date: datetime, location: str, atm_id_full: str,
                          decline: bool = False) -> int:
    """Balance inquiry — success shows BALANCE INQUIRY/AVAILABLE BAL/RESPONSE
    CODE 000 with nothing dispensed; decline mirrors the generic decline
    shape (GENAC AAC). The 'BQ   C A' opcode is an assumption (no real
    balance-inquiry sample was mined for ER) — flag if the real opcode
    differs."""
    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : BQ   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")

    w.event(f"2ndGENAC Result : {'AAC' if decline else 'TC'}", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event(f"TSI : {'6000' if decline else '7000'}", delta_secs=0)

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)

    if decline:
        card_tag = random.choice(["#CREDIT CARD", "#SAVINGS"])
        _gen_decline_receipt(w, location, receipt_masked, txn_no,
                             ["BALANCE INQUIRY", card_tag], "100", None, atm_id_full)
    else:
        avail_bal = _random_balance()
        receipt_time = w.dt.strftime("%H:%M")
        receipt_date = w.dt.strftime("%m/%d/%y")
        w.raw(f"    {location}")
        w.raw("    DATE       TIME       ATM ID")
        w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
        w.raw(f"    CARD NUMBER:  {receipt_masked}")
        w.raw(f"    TXN NO.     {txn_no}")
        w.raw("    BALANCE INQUIRY")
        w.raw(f"    AVAILABLE BAL{_fmt_money_field(avail_bal, 18)}")
        w.raw("    RESPONSE CODE              000")
        w.raw("    YOUR TXN IS SUCCESSFUL")
        w.raw(f"    RRN.               {txn_no}        ")
        w.raw("    GO CASH FREE!USE DEBIT CARDS")
        w.raw("    NEVER SHARE YOUR CARD DETAILS")
        w.raw("    AND PIN WITH ANYONE")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card ejection : No card", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_host_timeout(w: EJWriter, machine_seq: int, tran_date: datetime, atm_id_full: str) -> None:
    """Real evidence (mined EN sample): link drops/recovers and the host
    never answers the transaction request. No Transaction Res. Received,
    no receipt — does NOT consume a TXN NO/Machine Sequence No, so the
    caller must not touch txn_no for this outcome."""
    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    amount = random.choice(AMOUNTS)
    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")

    w.event("LINK1 Fatal [Closed]", delta_secs=random.randint(3, 8))
    w.event("LINK1 Healthy [Open]", delta_secs=random.randint(2, 5))
    w.event("HOST TX TIMEOUT", delta_secs=random.randint(10, 20))
    w.event("Transaction Cancelled", delta_secs=random.randint(1, 3))
    w.event("Card ejection : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card taken", delta_secs=random.randint(1, 4))
    w.event("Transaction End", delta_secs=random.randint(3, 8))


def _emit_device_status_block(w: EJWriter) -> None:
    """LITERAL TEMPLATE (user-supplied, real evidence): the fixed
    Device Status Area / Error Severity / M-Status / Supplies Status
    broadcast that accompanies the power-cut reinit and both ends of
    admin_cassette's Supervisor Mode login/logout. Every code below is a
    fixed literal, not a randomized placeholder — do not swap in words
    like 'Fatal'/'OK'/'LOW'."""
    w.event("Device Status Area : E Status Cash Handler : 000000000", delta_secs=random.randint(0, 2))
    w.event(" Error Severity : 12200", delta_secs=random.randint(0, 1))
    w.event(" M-Status : 0000000000", delta_secs=random.randint(0, 1))
    w.event(" Supplies Status  : 13310", delta_secs=random.randint(0, 1))


def _cash_count_rows(cs: CassetteState, cas_a_five_space: bool) -> list:
    """The CAS_A..CAS_R 'Cash count' rows shared by every '---Cassette
    Information---'/'---Standard Cash---' block. LITERAL TEMPLATE: CAS_A
    is indented with 5 leading spaces in Cassette Information blocks but
    4 in the Standard Cash block (cas_a_five_space toggles this); B-R
    always use 4 — matches the user-supplied template exactly."""
    def status(n: int) -> str:
        return "Full  " if n > 50 else ("Empty " if n == 0 else "Normal")
    a_indent = "     " if cas_a_five_space else "    "
    return [
        f"{a_indent}CAS_A Recycle      Rs.500           0     {cs.a:4d}  {status(cs.a)}",
        f"    CAS_B Recycle      Rs.500        1200     {cs.b:4d}  {status(cs.b)}",
        f"    CAS_C Recycle      Rs.200           0     {cs.c:4d}  {status(cs.c)}",
        f"    CAS_D Recycle      Rs.100           0     {cs.d:4d}  {status(cs.d)}",
        f"    CAS_E Dep&Rej&Ret  -                0     {cs.e:4d}  Normal",
        f"    CAS_F Retract      -                0     {cs.f:4d}  {status(cs.f)}",
        f"    CAS_R Retained     -                0     {cs.r:4d}  {status(cs.r)}",
    ]


def _cassette_information_block(cs: CassetteState, atm_id_full: str, tran_date: datetime) -> str:
    """LITERAL TEMPLATE '---Cassette Information---' block (user-supplied) —
    the opening (before-replenishment) occurrence: Terminal ID, Accounting
    Cycle Start/End, Cash count table, Total replenishment/Current total
    count."""
    prev_date = (tran_date - timedelta(days=4)).strftime("%m-%d-%Y")
    lines = [
        "---Cassette Information---",
        f"     Terminal ID: {atm_id_full}",
        f"     Accounting Cycle Start:      {prev_date} 10:16:28",
        "     Accounting Cycle End:        -",
        "             ****        Cash count             ****",
        "           Type         Denomi     Initial  Current  Status",
        *_cash_count_rows(cs, cas_a_five_space=True),
        "     Total replenishment                          600000.00",
        "     Current total count                         3392600.00",
    ]
    return "\r\n".join(lines)


def _cassette_information_retract_retain_block(atm_id_full: str, w_dt: datetime) -> str:
    """LITERAL TEMPLATE '---Cassette Information---' block (user-supplied,
    corrected) — the closing (after-replenishment) occurrence: Terminal ID,
    a bare date-time line, then Retract Info and Retain Info per-
    denomination note-count tables. All counts are 0 in the only real
    evidence given (no per-denomination retract/retain tracking exists in
    CassetteState) — rendered as the literal fixed template rather than an
    invented nonzero heuristic."""
    dt_str = f"{w_dt.strftime('%m-%d-%Y')} {w_dt.strftime('%H:%M:%S')}"
    return "\r\n".join([
        "---Cassette Information---",
        f"    Terminal ID: {atm_id_full}",
        f"                                       {dt_str}",
        "            ****        Retract Info           ****",
        "    Denomination      No of Notes",
        "    Rs.100                     0",
        "    Rs.200                     0",
        "    Rs.500                     0",
        "    Rs.2000                    0",
        "    Unknown                    0",
        "            ****        Retain Info           ****",
        "    Denomination      No of Notes",
        "    Rs.100                     0",
        "    Rs.200                     0",
        "    Rs.500                     0",
        "    Rs.2000                    0",
    ])


def _gen_power_cut(w: EJWriter, tran_date: datetime, atm_id_full: str) -> None:
    """LITERAL TEMPLATE (user-supplied, real evidence): mid-day power
    failure during a Start Up sequence — UnSolicitedStatus Send :
    Succeeded -> Power Failure : B : 0149 -> device status broadcast ->
    Device Handler Error -> OUT OF SERVICE -> GO IN SERVICE. Not a
    customer-transaction outcome: consumes no num_transactions slot and
    no TXN NO/Machine Sequence No."""
    w.event("UnSolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Power Failure : B : 0149", delta_secs=random.randint(1, 3))
    _emit_device_status_block(w)
    w.event("Device Handler Error", delta_secs=random.randint(1, 3))
    w.event("OUT OF SERVICE", delta_secs=random.randint(2, 5))
    w.event("GO IN SERVICE", delta_secs=random.randint(5, 15))


def _gen_admin_cassette(w: EJWriter, cs: CassetteState, atm_id_full: str, tran_date: datetime) -> None:
    """LITERAL TEMPLATE (user-supplied, real evidence): cassette-
    replenishment via Supervisor Mode — login -> Cassette Information
    (before) -> Safe Door opened/closed x2 + CAS_R removed/inserted ->
    'Replenishment' serial block -> '---Standard Cash---' block ->
    Cassette Information (after, with Denomination count table) -> logout
    -> device status broadcast -> the exact (GO IN SERVICE / OUT OF
    SERVICE / OUT OF SERVICE / GO IN SERVICE) closing order. Not a
    customer-transaction outcome: consumes no num_transactions slot and no
    TXN NO/Machine Sequence No. Replenishment tops up cassettes A/B/C/D
    and empties the R (retained-notes) cassette, carried forward
    cumulatively into the shared CassetteState.

    Every header/label here ('Replenishment', '---Standard Cash---', the
    Cas/Typ/Cnt columns, etc.) is a fixed literal per the user-supplied
    template — do not rename or restructure. Only the numeric fill values
    (serial, top-up amounts) are generated placeholders."""
    w.event("Logged into Supervisor Mode", delta_secs=random.randint(2, 5))
    w.raw(_cassette_information_block(cs, atm_id_full, tran_date))

    w.event("Safe Door : Opened", delta_secs=random.randint(3, 8))
    w.event("Safe Door : Closed", delta_secs=random.randint(2, 5))
    w.event("Safe Door : Opened", delta_secs=random.randint(3, 8))
    w.event("   CAS_R    removed", delta_secs=random.randint(2, 5))
    w.event("   CAS_R    inserted", delta_secs=random.randint(10, 30))
    w.event("Safe Door : Closed", delta_secs=random.randint(5, 15))

    serial = str(random.randint(1000, 9999))
    dt_str = f"{w.dt.strftime('%m-%d-%Y')} {w.dt.strftime('%H:%M:%S')}"
    top_up_a = random.randint(1000, 2000)
    top_up_b = random.randint(1000, 2000)
    top_up_c = random.randint(200, 600)
    top_up_d = random.randint(100, 400)
    top_up_e = random.randint(0, 50)
    w.raw("\r\n".join([
        "Replenishment",
        f"Serial No.{serial}   Date:{dt_str}",
        " Cas A        B        C        D        E",
        "     Rs.500   Rs.500   Rs.200   Rs.100   ALL",
        " Typ Recycle  Recycle  Recycle  Recycle  Dp&Rj&Rt",
        f" Cnt {top_up_a:<9}{top_up_b:<9}{top_up_c:<9}{top_up_d:<9}{top_up_e}",
    ]))

    cs.a += top_up_a
    cs.b += top_up_b
    cs.c += top_up_c
    cs.d += top_up_d
    cs.r = 0

    w.raw("\r\n".join([
        "---Standard Cash---",
        f"    Terminal ID: {atm_id_full}",
        f"    Standard Cash Time:          {dt_str}",
        "            ****       Cash count             ****",
        "          Type         Denomi     Initial  Current  Status",
        *_cash_count_rows(cs, cas_a_five_space=False),
        "     Total replenishment                          600000.00",
    ]))

    w.raw(_cassette_information_retract_retain_block(atm_id_full, w.dt))

    w.event("Logged out from Supervisor Mode", delta_secs=random.randint(2, 5))
    w.event("UnSolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    _emit_device_status_block(w)
    w.event("Device Handler Error", delta_secs=random.randint(1, 3))
    w.event("GO IN SERVICE", delta_secs=random.randint(2, 5))
    w.event("OUT OF SERVICE", delta_secs=random.randint(1, 3))
    w.event("OUT OF SERVICE", delta_secs=random.randint(1, 3))
    w.event("GO IN SERVICE", delta_secs=random.randint(2, 5))


def _gen_pin_stage_decline(w: EJWriter, txn_no: int, machine_seq: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            response_code: str, extra_message: str) -> int:
    """Decline at the PIN stage — no amount is ever entered. Used for PIN
    tries exceeded (075) and invalid PIN (055), both real evidence."""
    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AC      )")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : 5,NextState : 048)")
    w.event("2ndGENAC Result : AAC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 6000", delta_secs=0)

    card_tag = random.choice(["#CREDIT CARD", "#SAVINGS"])
    _gen_decline_receipt(w, location, receipt_masked, txn_no,
                         ["WITHDRAWAL", card_tag], response_code, extra_message, atm_id_full)
    _gen_decline_ending(w, atm_id_full, mmddyy, photo_masked)

    return txn_no + 1


def _gen_post_amount_decline(w: EJWriter, txn_no: int, machine_seq: int,
                              tran_date: datetime, location: str, atm_id_full: str,
                              response_code: str, extra_message: str) -> int:
    """Decline after amount entry/host request — same point as the generic
    decline (_gen_declined_withdrawal), different response code + extra
    message line. Used for insufficient funds (051, INVENTED) and daily
    limit exceeded (061, INVENTED)."""
    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    amount = random.choice(AMOUNTS)
    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AC      )")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : 5,NextState : 048)")
    w.event("2ndGENAC Result : AAC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 6000", delta_secs=0)

    card_tag = random.choice(["#CREDIT CARD", "#SAVINGS"])
    _gen_decline_receipt(w, location, receipt_masked, txn_no,
                         ["WITHDRAWAL", card_tag], response_code, extra_message, atm_id_full)
    _gen_decline_ending(w, atm_id_full, mmddyy, photo_masked)

    return txn_no + 1


def _gen_bin_stage_decline(w: EJWriter, txn_no: int, machine_seq: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            response_code: str, extra_message: str) -> int:
    """Decline at the card-read/BIN stage — before PIN is ever entered.
    Used for unauthorized card (057, INVENTED) and card expired
    (054, INVENTED)."""
    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)

    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AC      )")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : 5,NextState : 048)")
    w.event("2ndGENAC Result : AAC", delta_secs=0)

    card_tag = random.choice(["#CREDIT CARD", "#SAVINGS"])
    _gen_decline_receipt(w, location, receipt_masked, txn_no,
                         ["WITHDRAWAL", card_tag], response_code, extra_message, atm_id_full)
    _gen_decline_ending(w, atm_id_full, mmddyy, photo_masked)

    return txn_no + 1


def _gen_cash_not_taken(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                         tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Successful dispense, but the customer never collects the cash — a
    retract event (invented wording: 'Notes retracted') fires instead of
    the normal notes-removed ending, after a delay."""
    amount = random.choice(AMOUNTS)
    n100, n200, n500 = _denom_split(amount)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = f"{txn_no}        "

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")
    w.event("2ndGENAC Result : TC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 7000", delta_secs=0)

    w.event("Banknote separation", delta_secs=random.randint(6, 12))
    w.raw("                        in cassette : Succeeded")
    w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}   ")
    w.raw(f"    Reject   :{_random_reject_count()}    ")
    cs.dispense(n100, n200, n500)
    w.raw(f"    {cs.suffix()}")

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Shutter Open -> Take Cash", delta_secs=0)
    w.event("Shutter Close", delta_secs=random.randint(6, 12))

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    w.event("Notes retracted", delta_secs=random.randint(15, 30))
    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card ejection : No card", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_partial_split_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                                   tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Host dispenses the amount in two physical batches within one
    authorization — two Banknote-separation/dispense cycles (cassette
    suffix updated each time), one final receipt. Consumes 1 TXN NO."""
    amount = random.choice(AMOUNTS)
    amount1 = ((amount // 2) // 500) * 500 or 500
    amount1 = min(amount1, amount - 500) if amount > 500 else amount
    amount2 = amount - amount1
    n100_a, n200_a, n500_a = _denom_split(amount1)
    n100_b, n200_b, n500_b = _denom_split(amount2)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = f"{txn_no}        "

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")
    w.event("2ndGENAC Result : TC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 7000", delta_secs=0)

    for n100, n200, n500 in [(n100_a, n200_a, n500_a), (n100_b, n200_b, n500_b)]:
        w.event("Banknote separation", delta_secs=random.randint(6, 12))
        w.raw("                        in cassette : Succeeded")
        w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}   ")
        w.raw(f"    Reject   :{_random_reject_count()}    ")
        cs.dispense(n100, n200, n500)
        w.raw(f"    {cs.suffix()}")

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Shutter Open -> Take Cash", delta_secs=0)
    w.event("Shutter Close", delta_secs=random.randint(6, 12))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Banknote ejection to", delta_secs=0)
    w.raw("                    banknote bucket : Succeeded")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card ejection : No card", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_unknown_denom_withdrawal(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                                   tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Same as a successful withdrawal, but an unknown-denomination note
    shows up in the dispense breakdown. Extrapolated: ER's dispense line
    has no dedicated 'Unknown' slot in any mined sample, so one is appended
    in the same 'Rs.NNN   :n' style rather than inventing a new line
    format."""
    amount = random.choice(AMOUNTS)
    n100, n200, n500 = _denom_split(amount)
    unknown_count = random.randint(1, 2)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = f"{txn_no}        "

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")
    w.event("2ndGENAC Result : TC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 7000", delta_secs=0)

    w.event("Banknote separation", delta_secs=random.randint(6, 12))
    w.raw("                        in cassette : Succeeded")
    w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}    Rs.Unknown:{unknown_count}   ")
    w.raw(f"    Reject   :{_random_reject_count()}    ")
    cs.dispense(n100, n200, n500)
    w.raw(f"    {cs.suffix()}")

    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_CardRemoved")
    w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("             CardRemoved")
    w.event("Card ejection : Succeeded", delta_secs=0)
    w.event("Shutter Open -> Take Cash", delta_secs=0)
    w.event("Shutter Close", delta_secs=random.randint(6, 12))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Banknote ejection to", delta_secs=0)
    w.raw("                    banknote bucket : Succeeded")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Card ejection : No card", delta_secs=0)
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_failure_to_collect_card(w: EJWriter, cs: CassetteState, txn_no: int, machine_seq: int,
                                  tran_date: datetime, location: str, atm_id_full: str) -> int:
    """Successful withdrawal, but the customer never takes the card back —
    a retain event (invented wording: 'Card retained') fires instead of
    the normal CardRemoved step, and no eject/no-card status ever posts."""
    amount = random.choice(AMOUNTS)
    n100, n200, n500 = _denom_split(amount)
    acc_no = _random_account()
    avail_bal = _random_balance()
    rrn = f"{txn_no}        "

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    event_masked, receipt_masked, photo_masked, mmddyy = _gen_card_opening(w, atm_id_full)
    _gen_pin_entered_pic(w, atm_id_full, mmddyy, photo_masked)

    w.event("Entered Amount is :", delta_secs=random.randint(5, 12))
    w.raw(f"    ({'Rs.' + str(amount):<15})")
    w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw("    (Operation Code : AB   C A)")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : A,NextState : 121)")
    w.event("2ndGENAC Result : TC", delta_secs=0)
    w.event("TVR : 8000040000", delta_secs=0)
    w.event("TSI : 7000", delta_secs=0)

    w.event("Banknote separation", delta_secs=random.randint(6, 12))
    w.raw("                        in cassette : Succeeded")
    w.raw(f"    Rs.100   :{n100}    Rs.200   :{n200}    Rs.500   :{n500}   ")
    w.raw(f"    Reject   :{_random_reject_count()}    ")
    cs.dispense(n100, n200, n500)
    w.raw(f"    {cs.suffix()}")

    w.event("Card retained", delta_secs=random.randint(15, 30))
    w.event("Shutter Open -> Take Cash", delta_secs=0)
    w.event("Shutter Close", delta_secs=random.randint(6, 12))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Banknote ejection to", delta_secs=0)
    w.raw("                    banknote bucket : Succeeded")

    receipt_time = w.dt.strftime("%H:%M")
    receipt_date = w.dt.strftime("%m/%d/%y")
    w.raw(f"    {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    CARD NUMBER:  {receipt_masked}")
    w.raw(f"    TXN NO.     {txn_no}")
    w.raw(f"    WITHDRAWAL{_fmt_money_field(amount, 20)}")
    w.raw(f"    FROM A/C:{acc_no:>23}")
    w.raw(f"    AVAIL BAL{_fmt_money_field(avail_bal, 18)}")
    w.raw("    RESPONSE CODE              000")
    w.raw("    YOUR TXN IS SUCCESSFUL")
    w.raw(f"    RRN.               {rrn}")
    w.raw("    GO CASH FREE!USE DEBIT CARDS")
    w.raw("    NEVER SHARE YOUR CARD DETAILS")
    w.raw("    AND PIN WITH ANYONE")

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_deposit_opening(w: EJWriter, txn_no: int, machine_seq: int, location: str,
                          atm_id_full: str, card_based: bool) -> tuple:
    """Shared deposit-authorize opening (identical to _gen_deposit's phase-1
    up through the phase-1 receipt block) for the two new deposit-failure
    scenarios below. Returns (acc_no, name, card_no_field, photo_tag, mmddyy)."""
    acc_no = _random_account()
    name = _random_customer_name()

    txn_date = w.dt.strftime("%m-%d-%Y")
    w.raw(f"==> {txn_date} {w.dt.strftime('%H:%M:%S')}  Terminal ID : {atm_id_full}")
    w.raw(f"    Machine Sequence No : {machine_seq:06d}")

    if card_based:
        card_prefix = random.choice(CARD_PREFIXES)
        event_masked, receipt_masked, photo_masked = _random_pan(card_prefix)
        aid, applabel = random.choice(NETWORKS)
        card_no_field = receipt_masked
        photo_tag = photo_masked

        w.event("Read Result : ICC", delta_secs=0)
        mmddyy = w.dt.strftime("%m%d%y")
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_NA_CardInserted")
        w.event("Take Picture(Face) : Succeeded", delta_secs=0)
        w.raw("             CardInserted")
        w.event(f"AID : {aid}", delta_secs=random.randint(3, 8))
        w.event(f"AppLabel : {applabel}", delta_secs=0)
        w.event(f"Card Number : {event_masked}", delta_secs=random.randint(1, 3))
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_masked}_PINEntered")
        w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(8, 16))
        w.raw("             PINEntered")
        w.event("1stGENAC Result : ARQC", delta_secs=random.randint(1, 3))
        opcode_init = "B CC   A"
        next_state_init = "395"
    else:
        card_no_field = "888888******8888"
        photo_tag = _account_photo_mask(acc_no)
        mmddyy = w.dt.strftime("%m%d%y")
        masked_acc = _account_photo_mask(acc_no)
        w.event("Non Card Transaction", delta_secs=0)
        w.event("Entered Data", delta_secs=random.randint(5, 12))
        w.raw(f"    ({masked_acc}" + " " * 21 + ")")
        w.event("Entered Data", delta_secs=random.randint(3, 8))
        w.raw(f"    ({masked_acc}" + " " * 21 + ")")
        opcode_init = "BC     A"
        next_state_init = "588"

    w.event("Transaction Req. Send : Succeeded", delta_secs=random.randint(3, 8))
    w.raw(f"    (Operation Code : {opcode_init})")
    w.event("Transaction Res. Received", delta_secs=random.randint(1, 3))
    w.raw(f"    (Host Sequence No. : {txn_no},FID : 5,NextState : {next_state_init})")
    if card_based:
        w.event("2ndGENAC Result : TC", delta_secs=0)
        w.event("TVR : 8080040000", delta_secs=0)
        w.event("TSI : 7000", delta_secs=0)

    receipt_date = w.dt.strftime("%m/%d/%y")
    receipt_time = w.dt.strftime("%H:%M")
    w.raw(f"    ATM ADD: {location}")
    w.raw("    DATE       TIME       ATM ID")
    w.raw(f"    {receipt_date}   {receipt_time}      {atm_id_full}")
    w.raw(f"    NAME: {name:<30}")
    w.raw(f"    CARD NUMBER:  {card_no_field}")
    w.raw(f"    FROM A/C      {acc_no}")
    w.raw(f"    TXN NO.               {txn_no}")

    return acc_no, name, card_no_field, photo_tag, mmddyy


def _gen_deposit_retracted(w: EJWriter, txn_no: int, machine_seq: int,
                            tran_date: datetime, location: str, atm_id_full: str,
                            card_based: bool = False) -> int:
    """Notes inserted, but the deposit is retracted/refused before a CASH
    DEPOSIT PARTICULARS receipt ever prints — no funds credited. Only the
    phase-1 (authorize) TXN NO is consumed, not the phase-2 confirm."""
    acc_no, name, card_no_field, photo_tag, mmddyy = _gen_deposit_opening(
        w, txn_no, machine_seq, location, atm_id_full, card_based)

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Shutter Open -> Insert Cash", delta_secs=random.randint(3, 8))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Inserted")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=random.randint(6, 15))
    w.raw("             NotesInserted")
    w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Inserted")
    w.event("Take Picture(Face) : Succeeded", delta_secs=0)
    w.raw("             NotesInserted")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    w.event("Deposit Retracted", delta_secs=random.randint(5, 15))
    w.raw("                        notes returned to retract cassette")
    w.event("Shutter Open -> Take Cash", delta_secs=random.randint(3, 8))
    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    if card_based:
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_CardRemoved")
        w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(1, 3))
        w.raw("             CardRemoved")
        w.event("Card ejection : Succeeded", delta_secs=0)
    else:
        w.event("Card ejection : No card", delta_secs=random.randint(1, 3))
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


def _gen_deposit_cash_jam(w: EJWriter, txn_no: int, machine_seq: int,
                          tran_date: datetime, location: str, atm_id_full: str,
                          card_based: bool = False) -> int:
    """Hardware jam during note insertion/counting — distinct failure mode
    from a retract: ends in device error, no CASH DEPOSIT PARTICULARS
    block. Only the phase-1 TXN NO is consumed."""
    acc_no, name, card_no_field, photo_tag, mmddyy = _gen_deposit_opening(
        w, txn_no, machine_seq, location, atm_id_full, card_based)

    w.event("SolicitedStatus  Send : Succeeded", delta_secs=random.randint(1, 3))
    w.event("Shutter Open -> Insert Cash", delta_secs=random.randint(3, 8))

    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Inserted")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=random.randint(6, 15))
    w.raw("             NotesInserted")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    w.event("Note Jam Detected", delta_secs=random.randint(5, 15))
    w.raw("                        CIM_OTHERNOTEERROR - hardware jam, deposit aborted")
    w.event("Device Error : CIM Jam", delta_secs=random.randint(2, 5))
    w.event("Shutter Open -> Take Cash", delta_secs=random.randint(3, 8))
    w.raw(f"{{pic-exitslot}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_Removed")
    w.event("Take Picture(Hand) : Succeeded", delta_secs=0)
    w.raw("             NotesRemoved")
    w.event("Shutter Close", delta_secs=random.randint(3, 8))

    if card_based:
        w.raw(f"{{pic-person}}{mmddyy}_{w.dt.strftime('%H%M%S')}_{atm_id_full}_{photo_tag}_CardRemoved")
        w.event("Take Picture(Face) : Succeeded", delta_secs=random.randint(1, 3))
        w.raw("             CardRemoved")
        w.event("Card ejection : Succeeded", delta_secs=0)
    else:
        w.event("Card ejection : No card", delta_secs=random.randint(1, 3))
    w.event("Transaction End", delta_secs=random.randint(3, 8))

    return txn_no + 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_euronet_recycler_ej(
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
    continuation: dict = None,
) -> dict:
    """
    Generate a EuroNet Recycler (ER) EJ file for IDFC First Bank ATMs.

    Args:
        tran_date: transaction date
        num_transactions: number of customer transactions
        selected_cases: list of case IDs to include (currently: 'simple_withdrawal')
        atm_id: numeric ATM ID string, e.g. '801190' (auto-generated if None)
        location: branch name (random if None)
        output_dir: output directory (uses /tmp if None)
        continuation: optional {"next_txn_no": int, "next_machine_seq": int} from
            a prior run's result to keep TXN NO / Machine Sequence No continuous
            across multiple files for the same ATM+day batch ("sync with other
            files"), instead of restarting at fresh random values.

    Returns:
        dict with run_id, file_name, atm_id, location, counts, continuation
    """
    random.seed()  # non-deterministic

    run_id = uuid.uuid4().hex[:12]
    if atm_id is None:
        atm_id = _gen_atm_id()
    atm_id_full = f"ER{atm_id}"
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # File naming: ER{ATM_ID}-{MMDDYYYY}.txt
    date_str_file = tran_date.strftime("%m%d%Y")
    file_name = f"{atm_id_full}-{date_str_file}.txt"

    lines = [_startup_preamble(tran_date, atm_id_full)]

    start_dt = tran_date.replace(hour=1, minute=15, second=0, microsecond=0)
    w = EJWriter(start_dt)
    cs = CassetteState()

    case_set = set(selected_cases) if selected_cases else set()

    ALL_WITHDRAWAL_OUTCOMES = {
        "simple_withdrawal": 45, "declined_withdrawal": 20, "balance_inquiry": 8,
        "host_timeout": 3, "pin_tries_exceeded": 3, "invalid_pin": 3,
        "declined_insufficient_funds": 3, "declined_unauthorized_card": 2,
        "daily_limit_exceeded": 2, "card_expired": 2, "cash_not_taken": 3,
        "notes_in_reject": 3, "partial_split_transaction": 2,
        "unknown_denom_notes": 2, "failure_to_collect_card": 2,
    }
    ALL_DEPOSIT_OUTCOMES = {"cash_deposit": 80, "deposit_retracted": 10, "deposit_cash_jam": 10}

    if not case_set:
        withdrawal_pool = dict(ALL_WITHDRAWAL_OUTCOMES)
        deposit_pool = dict(ALL_DEPOSIT_OUTCOMES)
    else:
        withdrawal_pool = {k: v for k, v in ALL_WITHDRAWAL_OUTCOMES.items() if k in case_set}
        deposit_pool = {k: v for k, v in ALL_DEPOSIT_OUTCOMES.items() if k in case_set}

    do_withdrawal = bool(withdrawal_pool)
    do_deposit = bool(deposit_pool)
    withdrawal_outcomes = list(withdrawal_pool.keys())
    withdrawal_weights = list(withdrawal_pool.values())
    deposit_outcomes = list(deposit_pool.keys())
    deposit_weights = list(deposit_pool.values())

    if continuation and continuation.get("next_txn_no") is not None:
        txn_no = continuation["next_txn_no"]
    else:
        txn_no = random.randint(3000, 9000)
    if continuation and continuation.get("next_machine_seq") is not None:
        machine_seq = continuation["next_machine_seq"]
    else:
        machine_seq = random.randint(30600, 30900)
    counts = {
        "total": 0, "simple_withdrawal": 0, "cash_deposit": 0, "declined_withdrawal": 0,
        "balance_inquiry": 0, "host_timeout": 0, "pin_tries_exceeded": 0, "invalid_pin": 0,
        "declined_insufficient_funds": 0, "declined_unauthorized_card": 0,
        "daily_limit_exceeded": 0, "card_expired": 0, "cash_not_taken": 0,
        "notes_in_reject": 0, "partial_split_transaction": 0, "unknown_denom_notes": 0,
        "failure_to_collect_card": 0, "deposit_retracted": 0, "deposit_cash_jam": 0,
        "power_cut": 0, "admin_cassette": 0,
    }

    # Coverage guarantee: every explicitly-selected outcome (withdrawal or
    # deposit) must appear at least once in the file rather than being left
    # to chance — low-weight scenarios could easily roll zero times over a
    # normal-sized run, which looked like regressions across successive
    # generations even though nothing was actually broken. Reserve one slot
    # per pending outcome among the trailing iterations; if it hasn't fired
    # naturally by the time its reserved slot is reached, force it there —
    # overriding the deposit-vs-withdrawal branch pick for that iteration.
    to_guarantee = [("d", o) for o in deposit_outcomes] + [("w", o) for o in withdrawal_outcomes]
    random.shuffle(to_guarantee)
    guarantee_start = max(0, num_transactions - len(to_guarantee))

    for _txn_idx in range(num_transactions):
        w.advance(random.randint(60, 900))

        is_last = _txn_idx == num_transactions - 1
        # power_cut/admin_cassette are separate between-transaction "noise"
        # events (not in the outcome pools above) — guaranteed independently,
        # forced on the final iteration if they haven't fired yet.
        force_power_cut = "power_cut" in case_set and counts["power_cut"] == 0 and is_last
        if "power_cut" in case_set and (force_power_cut or random.random() < 0.03):
            _gen_power_cut(w, tran_date, atm_id_full)
            counts["power_cut"] += 1
        force_admin = "admin_cassette" in case_set and counts["admin_cassette"] == 0 and is_last
        if "admin_cassette" in case_set and (force_admin or random.random() < 0.03):
            _gen_admin_cassette(w, cs, atm_id_full, tran_date)
            counts["admin_cassette"] += 1

        reserved_idx = _txn_idx - guarantee_start
        pending = to_guarantee[reserved_idx] if 0 <= reserved_idx < len(to_guarantee) else None
        if pending is not None and counts[pending[1]] > 0:
            pending = None  # already fired naturally — no need to force it again
        force_deposit = pending is not None and pending[0] == "d"
        force_withdrawal = pending is not None and pending[0] == "w"

        # ER is a recycler (dispense + deposit) — interleave deposits with withdrawals.
        # A deposit consumes 2 TXN NOs (authorize + confirm) but is 1 customer session,
        # so it only counts once against num_transactions.
        if do_deposit and (force_deposit or (not force_withdrawal and (not do_withdrawal or random.random() < 0.25))):
            outcome = pending[1] if force_deposit else random.choices(deposit_outcomes, weights=deposit_weights, k=1)[0]
            card_based = random.random() < 0.5
            if outcome == "cash_deposit":
                txn_no = _gen_deposit(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full,
                                       card_based=card_based)
            elif outcome == "deposit_retracted":
                txn_no = _gen_deposit_retracted(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 card_based=card_based)
            else:  # deposit_cash_jam
                txn_no = _gen_deposit_cash_jam(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                card_based=card_based)
            machine_seq += 1
            counts[outcome] += 1
            counts["total"] += 1
        elif do_withdrawal:
            outcome = pending[1] if force_withdrawal else random.choices(withdrawal_outcomes, weights=withdrawal_weights, k=1)[0]
            if outcome == "simple_withdrawal":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full)
            elif outcome == "declined_withdrawal":
                txn_no = _gen_declined_withdrawal(w, txn_no, machine_seq, tran_date, location, atm_id_full)
            elif outcome == "balance_inquiry":
                decline = random.random() < 0.3
                txn_no = _gen_balance_inquiry(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                               decline=decline)
            elif outcome == "host_timeout":
                _gen_host_timeout(w, machine_seq, tran_date, atm_id_full)
                # no TXN NO consumed for this outcome
            elif outcome == "pin_tries_exceeded":
                txn_no = _gen_pin_stage_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 PIN_TRIES_EXCEEDED_CODE, "PIN TRIES EXCEEDED")
            elif outcome == "invalid_pin":
                txn_no = _gen_pin_stage_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 INVALID_PIN_CODE, "INVALID PIN")
            elif outcome == "declined_insufficient_funds":
                txn_no = _gen_post_amount_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                   INSUFFICIENT_FUNDS_CODE, "INSUFFICIENT FUNDS")
            elif outcome == "declined_unauthorized_card":
                txn_no = _gen_bin_stage_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 UNAUTHORIZED_CARD_CODE, "TRANSACTION NOT PERMITTED")
            elif outcome == "daily_limit_exceeded":
                txn_no = _gen_post_amount_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                   DAILY_LIMIT_CODE, "EXCEEDS WITHDRAWAL LIMIT")
            elif outcome == "card_expired":
                txn_no = _gen_bin_stage_decline(w, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 CARD_EXPIRED_CODE, "CARD EXPIRED")
            elif outcome == "cash_not_taken":
                txn_no = _gen_cash_not_taken(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full)
            elif outcome == "notes_in_reject":
                txn_no = _gen_simple_withdrawal(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full,
                                                 force_reject_count=random.randint(1, 4))
            elif outcome == "partial_split_transaction":
                txn_no = _gen_partial_split_withdrawal(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full)
            elif outcome == "unknown_denom_notes":
                txn_no = _gen_unknown_denom_withdrawal(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full)
            else:  # failure_to_collect_card
                txn_no = _gen_failure_to_collect_card(w, cs, txn_no, machine_seq, tran_date, location, atm_id_full)
            machine_seq += 1
            counts[outcome] += 1
            counts["total"] += 1

    lines.append(w.get_text())

    out_path = output_dir / file_name
    with open(out_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")

    cases_included = [c for c in selected_cases if counts.get(c, 0) > 0] or ["simple_withdrawal"]

    manifest = {
        "run_id": run_id,
        "bank_id": "idfc",
        "vendor": "euronet_recycler",
        "atm_type": "ER",
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
        "continuation": {"next_txn_no": txn_no, "next_machine_seq": machine_seq},
    }
