"""
Hyosung Electronic Journal (EJ) Generator for SBI ATMs.

Supports U1 (withdrawal-only) and S5 (recycler: deposit + withdrawal) ATM types.
Generates realistic EJ files matching Hyosung format observed in sample files.
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
    "SBI NAWADA",
    "GHODA NIKAS ROAD, RAMGANJ",
    "SBI MAIN BRANCH",
    "SBI CAMPUS ROAD",
    "SBI SECTOR 17",
    "SBI MARKET ROAD",
]

CARD_PREFIXES = ["652163", "652294", "459156", "508204", "517024"]

NETWORKS = [
    ("A0000001523010", "RuPay Debit"),
    ("A0000005241010", "DOMESTIC"),
    ("A0000000031010", "VISA DEBIT"),
]

AMOUNTS = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500,
           5000, 6000, 7000, 8000, 9000, 10000, 12000, 15000, 17000, 20000]

MONTH_MAP = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Denomination slots: index -> (INR denom, TYPE code for U1 table)
# TypeA=50, TypeB=100, TypeC=500, TypeD=2000, TypeE=200
DENOM_MAP = {
    0: (50,   "0001"),
    1: (100,  "0002"),
    2: (500,  "0003"),
    3: (2000, "0004"),
    4: (200,  "0005"),
}

# S5 recycler denominations (subset that participates in RCY table)
S5_RCY_DENOMS = [100, 200, 500]

# ─────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_ts(dt: datetime) -> str:
    """Format datetime as DD/MM/YYYY HH:MM:SS"""
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def _fmt_date_journal(dt: datetime) -> str:
    """Format date as DD-MON-YYYY (e.g. 26-MAR-2025)"""
    return f"{dt.day:02d}-{MONTH_MAP[dt.month]}-{dt.year}"


def _random_card(prefix: str) -> str:
    """Generate masked card number: PREFIX + XXXXXX + 4 digits"""
    suffix = str(random.randint(1000, 9999))
    return f"{prefix}XXXXXX{suffix}"


def _random_account() -> str:
    """Generate masked account: XXXXXXXXXXX + 4 digits"""
    suffix = str(random.randint(1000, 9999))
    return "XXXXXXXXXXX" + suffix


def _random_reference() -> str:
    return str(random.randint(500000000000, 599999999999))


def _random_balance() -> str:
    """Random balance like 12345.67"""
    return f"{random.randint(0, 99999)}.{random.randint(0, 99):02d}"


def _note_line(tname: str, dlabel: str, count: int) -> str:
    """Format a Notes Dispensed line: TypeX(denom) padded to 16 chars then ' = count'."""
    label = f"{tname}({dlabel})"
    return f"{label:<16} = {count}"


def _random_mobile() -> str:
    return str(random.randint(7000000000, 9999999999))


def _random_serial_no() -> str:
    """Generate a note serial number like 3TA286290"""
    prefix = str(random.randint(0, 9))
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = str(random.randint(100000, 999999))
    return prefix + letters + digits


def _random_ip() -> str:
    return f"10.{random.randint(100,200)}.{random.randint(0,255)}.{random.randint(1,254)}"


def _write_serial_block(w, serials: list) -> None:
    """Write serial numbers as [s1,s2,...,sN] across lines (each ~80 chars)."""
    if not serials:
        w.raw("[]")
        return
    lines = []
    current = "["
    for i, s in enumerate(serials):
        sep = "]" if i == len(serials) - 1 else ","
        if len(current) + len(s) + len(sep) > 80 and current != "[":
            lines.append(current)  # current already ends with "," from previous entry
            current = s + sep
        else:
            current += s + sep
    lines.append(current)
    for line in lines:
        w.raw(line)


def _random_hex5() -> str:
    return "".join(random.choices("0123456789abcdef", k=5))


def _gen_atm_id(atm_type: str) -> str:
    """Generate ATM ID: TYPE_PREFIX + 2 alpha + 9 digits = 13 chars"""
    alpha = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = "".join(random.choices(string.digits, k=9))
    return f"{atm_type}{alpha}{digits}"


def _make_denomination(amount: int, cassette: dict) -> tuple:
    """
    Determine note counts to dispense `amount` from cassette.
    Returns (counts_list_7, dispensed_dict) where counts_list_7 is [50,100,500,2000,200,0,0].
    Uses greedy: prefer 500s then 100s then 200s then 50s.
    """
    counts = [0] * 7  # TypeA-G
    remaining = amount

    # Order: 500 (index 2), 100 (index 1), 200 (index 4), 50 (index 0)
    for idx, denom in [(2, 500), (1, 100), (4, 200), (0, 50)]:
        avail = cassette.get(denom, 0)
        needed = min(remaining // denom, avail)
        counts[idx] = needed
        remaining -= needed * denom
        if remaining == 0:
            break

    if remaining > 0:
        # Can't make exact amount — adjust to something dispensable
        # Just use 100s only as fallback
        n100 = min(amount // 100, cassette.get(100, 0))
        counts = [0] * 7
        counts[1] = n100

    return counts


# ─────────────────────────────────────────────────────────────────────────────
# EVENT LINE EMITTER
# ─────────────────────────────────────────────────────────────────────────────

class EJWriter:
    """Tracks event counter and current timestamp, emits formatted EJ lines."""

    def __init__(self, start_counter: int, start_dt: datetime):
        self._counter = start_counter
        self._dt = start_dt
        self._lines: list[str] = []

    def _next_ts(self, delta_secs: int = 1) -> datetime:
        self._dt += timedelta(seconds=delta_secs)
        return self._dt

    def event(self, text: str, delta_secs: int = 1) -> None:
        """Emit a bracketed event line."""
        ts = self._next_ts(delta_secs)
        line = f"[{self._counter:06d}][{_fmt_ts(ts)}]{text}"
        self._lines.append(line)
        self._counter += 1

    def raw(self, text: str) -> None:
        """Emit a raw non-counter line (journal data body)."""
        self._lines.append(text)

    def blank(self) -> None:
        self._lines.append("")

    @property
    def counter(self) -> int:
        return self._counter

    @property
    def dt(self) -> datetime:
        return self._dt

    def advance(self, secs: int) -> None:
        self._dt += timedelta(seconds=secs)

    def get_text(self) -> str:
        return "\n".join(self._lines)


# ─────────────────────────────────────────────────────────────────────────────
# CASSETTE STATE
# ─────────────────────────────────────────────────────────────────────────────

class CassetteState:
    """
    Tracks cassette state for both U1 and S5 machines.

    U1: two cassettes — INR100 (TYPE 0002) and INR500 (TYPE 0003)
    S5: three RCY cassettes — INR100, INR200, INR500
        plus UNIVERSAL cassette (for rejected notes)
    """

    def __init__(self, atm_type: str):
        self.atm_type = atm_type
        if atm_type == "U1":
            # Large initial loads so cassettes never empty during a session
            self.cst = {100: 4800, 500: 8500}
            self.rej = {100: 10, 500: 31}
            self.disp = {100: 170, 500: 420}
            # total = disp + cst + rej (initial stock)
            self.total = {100: 4800 + 10 + 170, 500: 8500 + 31 + 420}
            self.initial_disp = dict(self.disp)
            self.rej_accumulated = dict(self.rej)
            # track last dispense counts for admin LTDISPNOTES
            self.last_disp_counts = [0] * 7
        else:
            # S5 recycler — start with healthy loads
            self.cst = {100: 1200, 200: 1000, 500: 2500}
            self.rej = {100: 0, 200: 0, 500: 0}
            self.stor = {100: 0, 200: 0, 500: 0}
            self.init = {100: 0, 200: 0, 500: 0}
            self.disp_total = {100: 0, 200: 0, 500: 0}
            # Universal cassette counts by denom
            self.univ = {}
            self.p6_counterfeit = 0
            # Running totals for TOTAL AMOUNT block
            self.disp_amount = 0
            self.stor_amount = 0
            # track last dispense counts for admin LTDISPNOTES
            self.last_disp_counts = [0] * 7

    def dispense(self, denom: int, count: int) -> None:
        """Update state after dispensing count notes of denom."""
        if count <= 0:
            return
        if self.atm_type == "U1":
            self.cst[denom] = max(0, self.cst.get(denom, 0) - count)
            self.disp[denom] = self.disp.get(denom, 0) + count
        else:
            self.cst[denom] = max(0, self.cst.get(denom, 0) - count)
            self.disp_total[denom] = self.disp_total.get(denom, 0) + count
            self.disp_amount += denom * count

    def add_reject(self, denom: int, count: int) -> None:
        if count <= 0:
            return
        if self.atm_type == "U1":
            self.rej[denom] = self.rej.get(denom, 0) + count
            self.cst[denom] = max(0, self.cst.get(denom, 0) - count)
        else:
            self.rej[denom] = self.rej.get(denom, 0) + count
            self.cst[denom] = max(0, self.cst.get(denom, 0) - count)
            univ_denom = denom  # rejected notes go to universal cassette
            self.univ[univ_denom] = self.univ.get(univ_denom, 0) + count

    def deposit(self, denom: int, count: int) -> None:
        """S5 only: deposit notes stored into recycler."""
        if self.atm_type != "S5" or count <= 0:
            return
        self.stor[denom] = self.stor.get(denom, 0) + count
        self.cst[denom] = self.cst.get(denom, 0) + count
        self.stor_amount += denom * count

    def u1_cassette_table(self) -> list[str]:
        """Return lines for U1 # CUR TYPE CST + REJ = REM + DISP = TOTAL block."""
        lines = []
        lines.append("# CUR TYPE CST + REJ = REM + DISP= TOTAL")
        row_num = 1
        for denom, type_code in [(100, "0002"), (500, "0003"), (50, "0001"), (2000, "0004"), (200, "0005")]:
            cst = self.cst.get(denom, 0)
            rej = self.rej.get(denom, 0)
            if cst == 0 and rej == 0:
                continue
            rem = cst + rej
            disp = self.disp.get(denom, 0)
            total = rem + disp
            lines.append(
                f"{row_num} INR {type_code} {cst:05d} {rej:05d} {rem:05d} {disp:05d} {total:05d}"
            )
            row_num += 1
        return lines

    def s5_rcy_table(self) -> list[str]:
        """Return lines for S5 CUR DENO INIT-DISP+STOR= CST+ REJ block."""
        lines = []
        lines.append("    CUR DENO INIT-DISP+STOR= CST+ REJ")
        for denom in [100, 200, 500]:
            init = self.init.get(denom, 0)
            disp = self.disp_total.get(denom, 0)
            stor = self.stor.get(denom, 0)
            cst = self.cst.get(denom, 0)
            rej = self.rej.get(denom, 0)
            lines.append(
                f"RCY Rs. {denom:>4d} {init:04d} {disp:04d} {stor:04d} {cst:04d} {rej:04d}"
            )
        return lines

    def s5_universal_block(self) -> list[str]:
        """Return lines for S5 UNIVERSAL CASSETTE block."""
        lines = []
        total_univ = sum(self.univ.values())
        lines.append(f"UNIVERSAL CASSETTE COUNT: {total_univ:05d}")
        lines.append(f"P6 COUNTERFEIT COUNT    : {self.p6_counterfeit:05d}")
        lines.append("")
        lines.append("[UNIVERSAL CASSETTE]")
        lines.append("NO DENOM       STOR + REJ + RET =TOTAL")
        row_num = 1
        for denom in [100, 500]:
            c = self.univ.get(denom, 0)
            if c > 0:
                lines.append(f" {row_num} Rs. {denom:>4d}   00000 {c:05d} 00000  {c:05d}")
                row_num += 1
        total_univ_line = sum(self.univ.values())
        lines.append(f"   TOTAL      00000 {total_univ_line:05d} 00000  {total_univ_line:05d}")
        return lines

    def s5_denomination_block(self) -> list[str]:
        """Return [DENOMINATION] block for S5."""
        lines = []
        lines.append("")
        lines.append("[DENOMINATION]")
        lines.append("NO DENOM      COUNT")
        denom_total = 0
        row_num = 1
        for denom in [100, 500, 200]:
            cst = self.cst.get(denom, 0)
            rej = self.rej.get(denom, 0)
            count = cst + rej
            if count > 0:
                lines.append(f" {row_num} Rs. {denom:>4d}   {count:05d}")
                denom_total += count
                row_num += 1
        lines.append(f"   TOTAL      {denom_total:05d}")
        return lines

    def s5_total_amount_block(self) -> list[str]:
        """Return [TOTAL AMOUNT] block for S5."""
        lines = []
        lines.append("")
        lines.append("[TOTAL AMOUNT]")
        init_amt = sum(self.init.get(d, 0) * d for d in [100, 200, 500])
        rem_amount = self.stor_amount - self.disp_amount
        lines.append(f"INIT AMOUNT:                  {init_amt:,d} Rs.")
        lines.append(f"DISP AMOUNT:            {self.disp_amount:,d} Rs.")
        lines.append(f"STOR AMOUNT:            {self.stor_amount:,d} Rs.")
        lines.append(f"REM  AMOUNT:             {rem_amount:,d} Rs.")
        lines.append("* UNKNOWN/RETRACT NOTE IS NOT INCLUDED")
        return lines

    def cassette_suffix(self) -> str:
        """S5: A{cst100}:B{cst200}:C{cst500}:D{cst2000}:U{univ_total}:P{p6}"""
        a = self.cst.get(100, 0)
        b = self.cst.get(200, 0)
        c = self.cst.get(500, 0)
        d = self.cst.get(2000, 0)
        u = sum(self.univ.values())
        p = self.p6_counterfeit
        return f"A{a:04d}:B{b:04d}:C{c:04d}:D{d:04d}:U{u:04d}:P{p:04d}"


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def _ltserno_block(w: EJWriter, atm_type: str, serno: int, disp_counts: list) -> None:
    """Emit LTSERNO, LTSTATUS, LTDISPNOTESxCOUNT, LTCOINDISPAMT (and S5 extras)."""
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i, cnt in enumerate(disp_counts, 1):
        w.event(f"LTDISPNOTES{i}COUNT [{cnt:05d}]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    if atm_type == "S5":
        w.event("LTCASHDEPDIRECTION [0]")
        w.event("LTCASHDEPCOUNT [00000000000000000000]")
        w.event("LTCASHNOTESREF [00000]")
        w.event("LTCASHNOTESREJ [00000]")
        w.event("LTCASHNOTESENC [00000]")
        w.event("LTCASHNOTESESC [00000]")
        w.event("LTTXNCASHRECYCLED [00]")


def _emv_start(w: EJWriter, card_no: str, amount: int, opcode: str, serno: int,
               disp_counts: list, atm_type: str, network_idx: int = None) -> int:
    """Emit transaction start through TRANSACTION REQUESTING. Returns next NEXT value."""
    net_idx = network_idx if network_idx is not None else random.randint(0, 2)
    net_id, net_name = NETWORKS[net_idx]
    candidates = random.randint(1, 2)

    w.event("TRANSACTION START", delta_secs=random.randint(2, 5))
    w.event(f"EMV CANDIDATES: {candidates}", delta_secs=random.randint(1, 4))
    w.event(f"EMV FINAL APP SELECTION SUCCESS: {net_id}({net_name})", delta_secs=random.randint(1, 3))
    w.event(f"CARD NUMBER {card_no}", delta_secs=1)
    w.event(f"ENTERED AMOUNT : [{amount}]", delta_secs=random.randint(7, 15))
    w.event("PIN ENTERED", delta_secs=random.randint(2, 5))
    w.event("EMV 1ST GENAC: ARQC", delta_secs=random.randint(1, 3))
    w.event(f"TRANSACTION REQUESTING: OPcode[{opcode}]")
    _ltserno_block(w, atm_type, serno, disp_counts)
    return random.randint(100, 999)


def _gen_withdrawal_success(w: EJWriter, atm_type: str, cs: CassetteState,
                             txn_no: int, serno: int, dt_base: datetime,
                             location: str, atm_id: str) -> int:
    """Generate a successful withdrawal transaction. Returns next serno."""
    amount = random.choice(AMOUNTS)
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    acc_no = _random_account()

    # Determine dispense counts
    counts_7 = [0] * 7
    temp_cs = {d: cs.cst.get(d, 0) for d in [50, 100, 200, 500, 2000]}
    rem = amount
    for idx, denom in [(2, 500), (1, 100), (4, 200), (0, 50)]:
        avail = temp_cs.get(denom, 0)
        n = min(rem // denom, avail)
        counts_7[idx] = n
        rem -= n * denom
        if rem == 0:
            break
    if rem > 0:
        n100 = min(amount // 100, cs.cst.get(100, 0))
        counts_7 = [0] * 7
        counts_7[1] = n100
        amount = n100 * 100

    opcode = "AB     A"
    next_val = _emv_start(w, card_no, amount, opcode, serno, counts_7, atm_type)

    fid = "B" if atm_type == "U1" else "2"
    w.event(f"TRANSACTION REPLIED: FID[{fid}] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8000048000, TSI: 6800", delta_secs=1)

    if atm_type == "S5":
        w.event(f"DISPENSE COMPLETE", delta_secs=random.randint(8, 15))
    # For U1, JOURNAL DATA comes before DISPENSE COMPLETE

    # JOURNAL DATA block
    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"      TXN NO         {txn_no}")
        w.raw("")
        w.raw(f"      REFERENCE NO  {_random_reference()}")
        w.raw("    RESPONSE CODE   000")
        w.raw("")
        w.raw(f"    WITHDRAWAL    RS.        {amount:,.2f}")
        w.raw("")
        w.raw(f"     FROM  A/C     {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(" PLEASE REGISTER YOUR MOBILE NO AT")
        w.raw(" BRANCH FOR SMS ALERTS.ENSURE")
        w.raw(" YOUR ACCOUNT IS KYC COMPLIANT.")
        w.raw("")

        w.event("CARD EJECTED", delta_secs=1)
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
        w.event("DISPENSE COMPLETE", delta_secs=random.randint(8, 18))
    else:
        # S5: DISPENSE COMPLETE emitted above, then JOURNAL DATA after CASH TAKEN
        pass

    # Notes Dispensed block
    w.event("Notes Dispensed:", delta_secs=random.randint(1, 4))
    if atm_type == "U1":
        denom_labels = [("TypeA", "INR50"), ("TypeB", "INR100"), ("TypeC", "INR500"),
                        ("TypeD", "INR2000"), ("TypeE", "INR200"), ("TypeF", "NOT SET"),
                        ("TypeG", "NOT SET")]
    else:
        denom_labels = [("TypeA", "Rs.50"), ("TypeB", "Rs.100"), ("TypeC", "Rs.500"),
                        ("TypeD", "Rs.2000"), ("TypeE", "Rs.200"), ("TypeF", "NOT SET"),
                        ("TypeG", "NOT SET")]
    for i, (tname, dlabel) in enumerate(denom_labels):
        w.raw(_note_line(tname, dlabel, counts_7[i]))
    if atm_type == "S5":
        w.raw(cs.cassette_suffix())

    w.event("CASH PRESENTED", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)
    w.event("CASH TAKEN", delta_secs=random.randint(3, 8))

    if atm_type == "S5":
        w.event("Close Shutter Complete", delta_secs=random.randint(3, 6))
        # JOURNAL DATA for S5
        w.event("JOURNAL DATA", delta_secs=1)
        w.raw(f"    {location}               ")
        w.raw("")
        w.raw(" DATE           TIME      ATM ID")
        w.raw(f" {date_str}  {time_str} {atm_id}   ")
        w.raw(f"CARD NUMBER   {card_prefix}XXXXXXXXX{card_no[-1]}")
        w.raw("")
        w.raw(f"      TXN NO        {txn_no}")
        w.raw("")
        w.raw("    RESPONSE CODE   000")
        w.raw("")
        w.raw(f"    WITHDRAWAL    RS.        {amount:,.2f}")
        w.raw("")
        w.raw(f"     FROM  A/C     {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL     RS.            {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw("")

    # Update cassette state
    for idx, (denom_val, _) in DENOM_MAP.items():
        if idx < len(counts_7):
            cs.dispense(denom_val, counts_7[idx])
    cs.last_disp_counts = list(counts_7)

    # TRANSACTION DATA (COMPLETED)
    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(1, 3))
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw(f"Function ID      [{fid}]")
    w.raw(f"Amount           [{amount:08d}]")
    w.raw(f"Denomination     [50,100,500,2000,200,NA,NA]")
    w.raw(f"Request Count    [{counts_7[0]:02d},{counts_7[1]:02d},{counts_7[2]:02d},{counts_7[3]:02d},{counts_7[4]:02d},{counts_7[5]:02d},{counts_7[6]:02d}]")
    w.raw(f"Pickup Count     [{counts_7[0]},{counts_7[1]},{counts_7[2]},{counts_7[3]},{counts_7[4]},{counts_7[5]},{counts_7[6]}]")
    w.raw(f"Dispense Count   [{counts_7[0]},{counts_7[1]},{counts_7[2]},{counts_7[3]},{counts_7[4]},{counts_7[5]},{counts_7[6]}]")
    w.raw(f"Remain Count     [{cs.cst.get(50,0)},{cs.cst.get(100,0)},{cs.cst.get(500,0)},{cs.cst.get(2000,0)},{cs.cst.get(200,0)},0,0]")
    w.raw(f"Reject Count     [0,0,0,0,0,0,0]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    if atm_type == "U1":
        for line in cs.u1_cassette_table():
            w.raw(line)
    else:
        for line in cs.s5_rcy_table():
            w.raw(line)
        w.raw("")
        for line in cs.s5_universal_block():
            w.raw(line)
        for line in cs.s5_denomination_block():
            w.raw(line)
        for line in cs.s5_total_amount_block():
            w.raw(line)

    w.raw("")
    w.raw("")

    if atm_type == "S5":
        w.event("CARD EJECTED", delta_secs=random.randint(5, 10))
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))

    w.event("TRANSACTION END", delta_secs=random.randint(5, 15))
    return txn_no + 1


def _gen_balance_inquiry(w: EJWriter, atm_type: str, txn_no: int, serno: int,
                          dt_base: datetime, location: str, atm_id: str) -> int:
    """Generate a balance inquiry transaction."""
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    acc_no = _random_account()
    opcode = "CA     A"
    disp_counts = [0] * 7

    _emv_start(w, card_no, 0, opcode, serno, disp_counts, atm_type)

    next_val = random.randint(50, 999)
    w.event(f"TRANSACTION REPLIED: FID[5] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8080048000, TSI: 6800", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")
    acct_type = random.choice(["SAVINGS", "CHECKING", "CREDIT CARD"])

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw(f"    REFERENCE NO  {_random_reference()}")
        w.raw("    RESPONSE CODE 000 ")
        w.raw("")
        w.raw(f"    BAL.INQUIRY   {acct_type}   ")
        w.raw("")
        w.raw(f"    FROM A/C      {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL         RS. {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL         RS. {_random_balance()}")
        w.raw("")
        w.raw("")
    else:
        w.event("JOURNAL DATA")
        w.raw(f"    {location}               ")
        w.raw("")
        w.raw(" DATE           TIME      ATM ID")
        w.raw(f" {date_str}  {time_str} {atm_id}   ")
        w.raw(f"CARD NUMBER   {card_prefix}XXXXXXXXX{card_no[-1]}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw("    RESPONSE CODE 000 ")
        w.raw("")
        w.raw(f"    BAL.INQUIRY   {acct_type}   ")
        w.raw("")
        w.raw(f"    FROM A/C      {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL         RS. {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL         RS. {_random_balance()}")
        w.raw("")
        w.raw("")

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw("Function ID      [5]")
    w.raw(f"Amount           [00000000]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    w.event("CARD EJECTED", delta_secs=random.randint(2, 6))
    w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(3, 10))
    return txn_no + 1


def _gen_declined(w: EJWriter, atm_type: str, cs: CassetteState,
                  txn_no: int, serno: int, dt_base: datetime,
                  location: str, atm_id: str, reason: str = "insufficient") -> int:
    """Generate a declined transaction."""
    amount = random.choice(AMOUNTS)
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    opcode = "AB     A"
    disp_counts = [0] * 7

    _emv_start(w, card_no, amount, opcode, serno, disp_counts, atm_type)

    next_val = random.randint(50, 999)
    w.event(f"TRANSACTION REPLIED: FID[5] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: ERROR", delta_secs=1)
    w.event("EMV TVR: 8080048000, TSI: 6800", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")

    if reason == "insufficient":
        resp_code = "051"
        msg1 = "    SAVINGS    "
        msg2 = "    TRANSACTION DECLINED DUE TO INSUFFICIENT BALANCE"
    elif reason == "unauthorized":
        resp_code = "050"
        msg1 = "    SAVINGS    "
        msg2 = "    UNAUTHORIZED CARD USAGE "
    else:
        resp_code = "091"
        msg1 = "    SAVINGS    "
        msg2 = "    HOST DID NOT RESPOND    "

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw(f"    RESPONSE CODE {resp_code}")
        w.raw("")
        w.raw(msg1)
        w.raw("")
        w.raw(msg2)
        w.raw("")
        w.raw(" INCONVENIENCE IS REGRETTED. KINDLY ")
        w.raw(" CALL 24X7 HELPLINE AT 1800-1234 OR")
        w.raw(" 1800-2100 OR 1800112211 OR")
        w.raw(" 080-26599990")
        w.raw("")
    else:
        w.event("JOURNAL DATA")
        w.raw(f"    {location}               ")
        w.raw("")
        w.raw(" DATE           TIME      ATM ID")
        w.raw(f" {date_str}  {time_str} {atm_id}   ")
        w.raw(f"CARD NUMBER   {card_prefix}XXXXXXXXX{card_no[-1]}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw(f"    RESPONSE CODE {resp_code}")
        w.raw("")
        w.raw(msg1)
        w.raw("")
        w.raw("    UNABLE TO PROCESS       ")
        w.raw("")
        w.raw(" INCONVENIENCE IS REGRETTED. KINDLY ")
        w.raw(" CALL 24X7 HELPLINE AT 1800-1234 OR")
        w.raw(" 1800112211 OR 080-26599990")
        w.raw("")

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw("Function ID      [5]")
    w.raw(f"Amount           [{amount:08d}]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    w.event("CARD EJECTED", delta_secs=random.randint(2, 8))
    w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(3, 10))
    return txn_no + 1


def _gen_cash_not_taken(w: EJWriter, atm_type: str, cs: CassetteState,
                         txn_no: int, serno: int, dt_base: datetime,
                         location: str, atm_id: str) -> int:
    """Generate withdrawal where customer doesn't take cash — ATM retracts."""
    amount = random.choice(AMOUNTS[:10])  # smaller amounts
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    acc_no = _random_account()
    opcode = "AB     A"

    counts_7 = [0] * 7
    temp_cs = {d: cs.cst.get(d, 0) for d in [50, 100, 200, 500, 2000]}
    rem = amount
    for idx, denom in [(2, 500), (1, 100), (4, 200), (0, 50)]:
        avail = temp_cs.get(denom, 0)
        n = min(rem // denom, avail)
        counts_7[idx] = n
        rem -= n * denom
        if rem == 0:
            break
    if rem > 0:
        n100 = min(amount // 100, cs.cst.get(100, 0))
        counts_7 = [0] * 7
        counts_7[1] = n100
        amount = n100 * 100

    _emv_start(w, card_no, amount, opcode, serno, counts_7, atm_type)

    fid = "B" if atm_type == "U1" else "2"
    next_val = random.randint(100, 999)
    w.event(f"TRANSACTION REPLIED: FID[{fid}] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8000048000, TSI: 6800", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"      TXN NO         {txn_no}")
        w.raw("")
        w.raw(f"      REFERENCE NO  {_random_reference()}")
        w.raw("    RESPONSE CODE   000")
        w.raw("")
        w.raw(f"    WITHDRAWAL    RS.        {amount:,.2f}")
        w.raw("")
        w.raw(f"     FROM  A/C     {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(" PLEASE REGISTER YOUR MOBILE NO AT")
        w.raw(" BRANCH FOR SMS ALERTS.ENSURE")
        w.raw(" YOUR ACCOUNT IS KYC COMPLIANT.")
        w.raw("")
        w.event("CARD EJECTED", delta_secs=1)
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
        w.event("DISPENSE COMPLETE", delta_secs=random.randint(8, 18))
    else:
        w.event("DISPENSE COMPLETE", delta_secs=random.randint(8, 15))

    w.event("Notes Dispensed:", delta_secs=random.randint(1, 4))
    if atm_type == "U1":
        denom_labels = [("TypeA", "INR50"), ("TypeB", "INR100"), ("TypeC", "INR500"),
                        ("TypeD", "INR2000"), ("TypeE", "INR200"), ("TypeF", "NOT SET"), ("TypeG", "NOT SET")]
    else:
        denom_labels = [("TypeA", "Rs.50"), ("TypeB", "Rs.100"), ("TypeC", "Rs.500"),
                        ("TypeD", "Rs.2000"), ("TypeE", "Rs.200"), ("TypeF", "NOT SET"), ("TypeG", "NOT SET")]
    for i, (tname, dlabel) in enumerate(denom_labels):
        w.raw(_note_line(tname, dlabel, counts_7[i]))
    if atm_type == "S5":
        w.raw(cs.cassette_suffix())

    w.event("CASH PRESENTED", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    # Cash NOT taken — retract
    w.event("CASH NOT TAKEN", delta_secs=random.randint(25, 40))
    w.event("RETRACT START", delta_secs=2)
    w.event("Notes Retracted:", delta_secs=random.randint(5, 12))
    for i, (tname, dlabel) in enumerate(denom_labels):
        w.raw(_note_line(tname, dlabel, counts_7[i]))

    # Notes retracted — no net change to cassette; last_disp_counts reflects zeros
    cs.last_disp_counts = [0] * 7

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(2, 5))
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw(f"Function ID      [{fid}]")
    w.raw(f"Amount           [{amount:08d}]")
    w.raw(f"Denomination     [50,100,500,2000,200,NA,NA]")
    w.raw(f"Request Count    [{counts_7[0]:02d},{counts_7[1]:02d},{counts_7[2]:02d},{counts_7[3]:02d},{counts_7[4]:02d},{counts_7[5]:02d},{counts_7[6]:02d}]")
    # Pickup count is 0 (cash retracted)
    w.raw(f"Pickup Count     [0,0,0,0,0,0,0]")
    w.raw(f"Dispense Count   [{counts_7[0]},{counts_7[1]},{counts_7[2]},{counts_7[3]},{counts_7[4]},{counts_7[5]},{counts_7[6]}]")
    w.raw(f"Remain Count     [{cs.cst.get(50,0)},{cs.cst.get(100,0)},{cs.cst.get(500,0)},{cs.cst.get(2000,0)},{cs.cst.get(200,0)},0,0]")
    w.raw(f"Reject Count     [0,0,0,0,0,0,0]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    if atm_type == "U1":
        for line in cs.u1_cassette_table():
            w.raw(line)
    else:
        for line in cs.s5_rcy_table():
            w.raw(line)
        w.raw("")
        for line in cs.s5_universal_block():
            w.raw(line)
        for line in cs.s5_denomination_block():
            w.raw(line)
        for line in cs.s5_total_amount_block():
            w.raw(line)

    w.raw("")
    w.raw("")
    if atm_type == "S5":
        w.event("CARD EJECTED", delta_secs=random.randint(3, 8))
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15))
    return txn_no + 1


def _gen_notes_in_reject(w: EJWriter, atm_type: str, cs: CassetteState,
                          txn_no: int, serno: int, dt_base: datetime,
                          location: str, atm_id: str) -> int:
    """Withdrawal where some notes go to reject cassette."""
    amount = random.choice(AMOUNTS[2:10])
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    acc_no = _random_account()
    opcode = "AB     A"

    counts_7 = [0] * 7
    rem = amount
    for idx, denom in [(2, 500), (1, 100), (4, 200)]:
        avail = cs.cst.get({0: 50, 1: 100, 2: 500, 3: 2000, 4: 200}[idx], 0)
        n = min(rem // denom, avail)
        counts_7[idx] = n
        rem -= n * denom
        if rem == 0:
            break
    if rem > 0:
        n100 = min(amount // 100, cs.cst.get(100, 0))
        counts_7 = [0] * 7
        counts_7[1] = n100
        amount = n100 * 100

    _emv_start(w, card_no, amount, opcode, serno, counts_7, atm_type)

    fid = "B" if atm_type == "U1" else "2"
    next_val = random.randint(100, 999)
    w.event(f"TRANSACTION REPLIED: FID[{fid}] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8000048000, TSI: 6800", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"      TXN NO         {txn_no}")
        w.raw("")
        w.raw(f"      REFERENCE NO  {_random_reference()}")
        w.raw("    RESPONSE CODE   000")
        w.raw("")
        w.raw(f"    WITHDRAWAL    RS.        {amount:,.2f}")
        w.raw("")
        w.raw(f"     FROM  A/C     {acc_no}")
        w.raw("")
        w.raw(f"     MOD  BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(f"    AVAIL BAL     RS.         {_random_balance()}")
        w.raw("")
        w.raw(" PLEASE REGISTER YOUR MOBILE NO AT")
        w.raw(" BRANCH FOR SMS ALERTS.ENSURE")
        w.raw(" YOUR ACCOUNT IS KYC COMPLIANT.")
        w.raw("")
        w.event("CARD EJECTED", delta_secs=1)
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
        w.event("DISPENSE COMPLETE", delta_secs=random.randint(8, 18))
    else:
        w.event("DISPENSE COMPLETE", delta_secs=random.randint(8, 15))

    w.event("Notes Dispensed:", delta_secs=random.randint(1, 4))
    if atm_type == "U1":
        denom_labels = [("TypeA", "INR50"), ("TypeB", "INR100"), ("TypeC", "INR500"),
                        ("TypeD", "INR2000"), ("TypeE", "INR200"), ("TypeF", "NOT SET"), ("TypeG", "NOT SET")]
    else:
        denom_labels = [("TypeA", "Rs.50"), ("TypeB", "Rs.100"), ("TypeC", "Rs.500"),
                        ("TypeD", "Rs.2000"), ("TypeE", "Rs.200"), ("TypeF", "NOT SET"), ("TypeG", "NOT SET")]
    for i, (tname, dlabel) in enumerate(denom_labels):
        w.raw(_note_line(tname, dlabel, counts_7[i]))
    if atm_type == "S5":
        w.raw(cs.cassette_suffix())

    w.event("CASH PRESENTED", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)
    w.event("CASH TAKEN", delta_secs=random.randint(3, 8))
    if atm_type == "S5":
        w.event("Close Shutter Complete", delta_secs=random.randint(3, 6))

    # Determine reject counts (1-2 notes for one denomination)
    rej_idx = next((i for i in [2, 1, 4] if counts_7[i] > 0), None)
    rej_counts = [0] * 7
    if rej_idx is not None:
        rej_counts[rej_idx] = random.randint(1, min(2, counts_7[rej_idx]))

    # Update cassette
    for idx, (denom_val, _) in DENOM_MAP.items():
        if idx < len(counts_7):
            cs.dispense(denom_val, counts_7[idx])
    # Add rejected notes back
    for idx, (denom_val, _) in DENOM_MAP.items():
        if idx < len(rej_counts) and rej_counts[idx] > 0:
            cs.add_reject(denom_val, rej_counts[idx])
    cs.last_disp_counts = list(counts_7)

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(1, 3))
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw(f"Function ID      [{fid}]")
    w.raw(f"Amount           [{amount:08d}]")
    w.raw(f"Denomination     [50,100,500,2000,200,NA,NA]")
    w.raw(f"Request Count    [{counts_7[0]:02d},{counts_7[1]:02d},{counts_7[2]:02d},{counts_7[3]:02d},{counts_7[4]:02d},{counts_7[5]:02d},{counts_7[6]:02d}]")
    actual_pick = [counts_7[i] for i in range(7)]
    for i in range(7):
        actual_pick[i] += rej_counts[i]  # pickup includes rejected
    w.raw(f"Pickup Count     [{actual_pick[0]},{actual_pick[1]},{actual_pick[2]},{actual_pick[3]},{actual_pick[4]},{actual_pick[5]},{actual_pick[6]}]")
    w.raw(f"Dispense Count   [{counts_7[0]},{counts_7[1]},{counts_7[2]},{counts_7[3]},{counts_7[4]},{counts_7[5]},{counts_7[6]}]")
    w.raw(f"Remain Count     [{cs.cst.get(50,0)},{cs.cst.get(100,0)},{cs.cst.get(500,0)},{cs.cst.get(2000,0)},{cs.cst.get(200,0)},0,0]")
    w.raw(f"Reject Count     [{rej_counts[0]},{rej_counts[1]},{rej_counts[2]},{rej_counts[3]},{rej_counts[4]},{rej_counts[5]},{rej_counts[6]}]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    if atm_type == "U1":
        for line in cs.u1_cassette_table():
            w.raw(line)
    else:
        for line in cs.s5_rcy_table():
            w.raw(line)
        w.raw("")
        for line in cs.s5_universal_block():
            w.raw(line)
        for line in cs.s5_denomination_block():
            w.raw(line)
        for line in cs.s5_total_amount_block():
            w.raw(line)

    w.raw("")
    w.raw("")
    if atm_type == "S5":
        w.event("CARD EJECTED", delta_secs=random.randint(5, 10))
        w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15))
    return txn_no + 1


def _gen_partial_ej(w: EJWriter, atm_type: str, cs: CassetteState,
                    txn_no: int, serno: int, dt_base: datetime) -> int:
    """Generate a partial/split transaction — starts but no COMPLETED block."""
    amount = random.choice(AMOUNTS)
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    opcode = "AB     A"

    counts_7 = [0] * 7
    rem = amount
    for idx, denom in [(2, 500), (1, 100)]:
        avail = cs.cst.get({1: 100, 2: 500}[idx], 0)
        n = min(rem // denom, avail)
        counts_7[idx] = n
        rem -= n * denom
        if rem == 0:
            break

    _emv_start(w, card_no, amount, opcode, serno, counts_7, atm_type)

    fid = "B" if atm_type == "U1" else "2"
    next_val = random.randint(100, 999)
    w.event(f"TRANSACTION REPLIED: FID[{fid}] NEXT[{next_val}]", delta_secs=random.randint(3, 6))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    # File ends here — no TRANSACTION DATA (COMPLETED) and no TRANSACTION END
    return txn_no + 1


def _gen_admin(w: EJWriter, atm_type: str, cs: CassetteState,
               txn_no: int, serno: int, dt_base: datetime,
               location: str, atm_id: str) -> int:
    """Generate an admin machine subtotal + cassette replenishment sequence."""
    opcode = "ADACA   "
    # Use previous transaction's dispense counts in the LT block
    prev_disp = getattr(cs, 'last_disp_counts', [0] * 7)

    w.event("TRANSACTION START", delta_secs=random.randint(2, 5))
    w.event(f"CARD NUMBER 002002XXXXXXXXX{random.randint(1000, 9999)}", delta_secs=1)
    w.event("PIN ENTERED", delta_secs=random.randint(3, 6))
    w.event(f"TRANSACTION REQUESTING: OPcode[{opcode}]", delta_secs=random.randint(2, 5))
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i, cnt in enumerate(prev_disp):
        w.event(f"LTDISPNOTES{i+1}COUNT [{cnt:05d}]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    if atm_type == "S5":
        w.event("LTCASHDEPDIRECTION [0]")
        w.event("LTCASHDEPCOUNT [00000000000000000000]")
        w.event("LTCASHNOTESREF [00000]")
        w.event("LTCASHNOTESREJ [00000]")
        w.event("LTCASHNOTESENC [00000]")
        w.event("LTCASHNOTESESC [00000]")
        w.event("LTTXNCASHRECYCLED [00]")

    w.event("TRANSACTION REPLIED: FID[5] NEXT[175]", delta_secs=random.randint(2, 5))
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M:%S")

    # Compute admin balance
    if atm_type == "U1":
        admin_bal = sum(cs.cst.get(d, 0) * d for d in [50, 100, 200, 500, 2000])
    else:
        admin_bal = sum(cs.cst.get(d, 0) * d for d in [100, 200, 500])

    bgl_bal = max(0, admin_bal - random.randint(5000, 20000))
    diff = admin_bal - bgl_bal

    w.event("JOURNAL DATA", delta_secs=1)
    if atm_type == "U1":
        w.raw(f"    {location}")
        w.raw("")
        w.raw(f"    DATE          TIME    ATM ID")
        w.raw(f" {date_str}      {time_str}   {atm_id}   ")
        w.raw("CARD NUMBER   002002XXXXXXXXXXXX2")
        w.raw("")
    else:
        w.raw(f"    {location}               ")
        w.raw("")
        w.raw(" DATE           TIME      ATM ID")
        w.raw(f" {date_str}  {time_str} {atm_id}   ")
        w.raw("CARD NUMBER   002002XXXXXXXXXXXX2")
        w.raw("")

    # Cassette IC/DC/OC/EC lines (cumulative lifetime amounts)
    c2_ic = random.randint(5000000, 9000000)
    c2_dc = random.randint(3000000, c2_ic)
    c2_oc = c2_ic - c2_dc - random.randint(10000, 50000)
    c2_ec = random.randint(0, 50000)
    c3_ic = random.randint(100000000, 250000000)
    c3_dc = random.randint(60000000, c3_ic)
    c3_oc = c3_ic - c3_dc - random.randint(50000, 200000)
    c3_ec = random.randint(0, 200000)
    c5_ic = random.randint(10000000, 20000000)
    c5_dc = random.randint(5000000, c5_ic)
    c5_oc = c5_ic - c5_dc - random.randint(10000, 60000)
    c5_ec = random.randint(0, 60000)

    w.raw("  C2 STR      RS.0")
    w.raw(f"  C2 IC   {c2_ic} C2 DC RS{c2_dc}")
    w.raw(f"  C2 OC   {c2_oc} C2 EC   RS{c2_ec}")
    w.raw("  C3 STR      RS.0")
    w.raw(f"  C3 IC {c3_ic} C3 DCRS{c3_dc}")
    w.raw(f"  C3 OC {c3_oc} C3 EC  RS{c3_ec}")
    w.raw("  C4 STR      RS.0")
    w.raw("  C4 IC         0 C4 DC       RS0")
    w.raw("  C4 OC         0 C4 EC       RS0")
    w.raw("  C5 STR      RS.0")
    w.raw(f"  C5 IC  {c5_ic} C5 DC RS{c5_dc}")
    w.raw(f"  C5 OC   {c5_oc} C5 EC   RS{c5_ec}")
    w.raw("    DEP0   0  DEP1   0  CHK    0")
    w.raw("    MSG    0  PAY    0")
    w.raw("    TOT    0  CRD    3  SUB")
    w.raw("     RESPONSE CODE     000")
    w.raw("")
    w.raw(f"    SEQ NO.{txn_no}")
    w.raw("    TXN TYPE: MACHINE SUBTOTAL")
    w.raw("    ADMIN BALANCE")
    w.raw(f"    (SUM OF ENDCASH)   RS. {admin_bal:.2f}")
    w.raw(f"    BGL BALANCE        RS. {bgl_bal:.2f}")
    sign = "-" if diff >= 0 else ""
    w.raw(f"    DIFFERENCE         {sign}RS. {abs(diff):.2f}")
    w.raw("PLEASE TALLY ADMIN BALANCE (HOPPER ")
    w.raw("WISE ENDCASH)WITH PHYSICAL CASH")
    w.raw("(DENOMINATION-WISE) AND AS A PART OF")
    w.raw("CONFIRMATION,PLEASE REPORT HOPPER-WISE")
    w.raw("SHORT CASH OR EXCESS CASH OR NO")
    w.raw("DIFFERENCE,THROUGH -CASH VERIFICATION-")
    w.raw("MENU AT ATM WITHOUT FAIL")
    w.raw("")

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(2, 5))
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw("Function ID      [5]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    w.event("TRANSACTION END", delta_secs=random.randint(3, 10))

    # ── Supervisor / cassette replenishment sequence ───────────────────────────
    w.event("MODE: InService -> Supervisor", delta_secs=random.randint(30, 90))
    w.event("ENTERED SUPERVISOR PROGRAM", delta_secs=random.randint(3, 8))
    w.event("CASH DISPENSER - NOT ONLINE", delta_secs=random.randint(60, 120))
    w.event("CASH_JAM_OCCURED", delta_secs=1)
    w.event("SAFE DOOR OPENED", delta_secs=random.randint(3, 6))
    w.event("TRANSPORT STATUS CHANGED", delta_secs=random.randint(3, 6))
    w.event("TRANSPORT STATUS CHANGED", delta_secs=1)
    w.event("CASH ACCEPTOR - ACCEPTOR CHANGED", delta_secs=random.randint(2, 5))
    w.event("CASH ACCEPTOR - NOT AVAILABLE", delta_secs=1)
    w.event("CASH ACCEPTOR - ACCEPTOR CHANGED", delta_secs=random.randint(60, 110))
    w.event("TRANSPORT STATUS CHANGED", delta_secs=1)
    w.event("TRANSPORT STATUS CHANGED", delta_secs=1)
    w.event("OPERATOR ENTER", delta_secs=random.randint(10, 20))

    # COUNT_BEFORE_CLEAR — shows state before cassette reset
    w.event("[COUNT_BEFORE_CLEAR]", delta_secs=random.randint(5, 15))
    w.event("PRINT CASH: ", delta_secs=1)
    if atm_type == "S5":
        for line in cs.s5_rcy_table():
            w.raw(line)
        w.raw("")
        for line in cs.s5_universal_block():
            w.raw(line)
        for line in cs.s5_denomination_block():
            w.raw(line)
        for line in cs.s5_total_amount_block():
            w.raw(line)
    else:
        for line in cs.u1_cassette_table():
            w.raw(line)
    w.raw("")
    # Timestamp of last clear
    prev_clear = dt_base - timedelta(hours=random.randint(18, 36))
    w.raw(f"BILL COUNT LAST CLEARED")
    w.raw(prev_clear.strftime("%d/%m/%Y %H:%M:%S"))
    w.raw("")
    if atm_type == "S5":
        w.raw("BRM IS NOT WORKING")
        w.raw("")

    # Cassette changed events (7 slots)
    w.event("CASH DISPENSER - NOT AVAILABLE", delta_secs=random.randint(30, 60))
    for _ in range(7):
        w.event("BRM_CASSETTE_CHANGED", delta_secs=1)
    w.event("CLEAR COUNT(CASHIN) SUCCESS", delta_secs=random.randint(3, 8))
    w.event("CLEAR COUNT(CDM) SUCCESS", delta_secs=random.randint(5, 10))

    # COUNT_AFTER_CLEAR — cassette loaded fresh; reset running counters
    if atm_type == "U1":
        # Replenish cassettes to fresh load
        fresh_100 = random.randint(4000, 6000)
        fresh_500 = random.randint(7000, 10000)
        cs.cst[100] = fresh_100
        cs.cst[500] = fresh_500
        cs.rej = {100: 0, 500: 0}
        cs.disp = {100: 0, 500: 0}
        cs.total = {100: fresh_100, 500: fresh_500}
    else:
        # Replenish recycler cassettes
        fresh_100 = random.randint(1000, 2000)
        fresh_200 = random.randint(800, 1500)
        fresh_500 = random.randint(2000, 4000)
        cs.cst = {100: fresh_100, 200: fresh_200, 500: fresh_500}
        cs.rej = {100: 0, 200: 0, 500: 0}
        cs.stor = {100: 0, 200: 0, 500: 0}
        cs.disp_total = {100: 0, 200: 0, 500: 0}
        cs.univ = {}
        cs.p6_counterfeit = 0
        cs.disp_amount = 0
        cs.stor_amount = 0

    w.event("[COUNT_AFTER_CLEAR]", delta_secs=random.randint(3, 8))
    w.event("PRINT CASH: ", delta_secs=1)
    if atm_type == "S5":
        for line in cs.s5_rcy_table():
            w.raw(line)
        w.raw("")
        for line in cs.s5_universal_block():
            w.raw(line)
        for line in cs.s5_denomination_block():
            w.raw(line)
        for line in cs.s5_total_amount_block():
            w.raw(line)
    else:
        for line in cs.u1_cassette_table():
            w.raw(line)
    w.raw("")
    w.raw(f"BILL COUNT LAST CLEARED")
    w.raw(w.dt.strftime("%d/%m/%Y %H:%M:%S"))
    w.raw("")
    if atm_type == "S5":
        w.raw("BRM IS NOT WORKING")
        w.raw("")

    w.event("HOST DISCONNECTED", delta_secs=random.randint(30, 90))
    w.event("SAFE DOOR CLOSED", delta_secs=random.randint(10, 30))
    w.event("RETRACT START", delta_secs=random.randint(5, 15))
    w.event("Reset Complete", delta_secs=random.randint(3, 6))
    if atm_type == "S5":
        w.event("A0000:B0000:C0000:D0000:U0000:P0000", delta_secs=1)
    w.event("EXITED SUPERVISOR", delta_secs=random.randint(3, 8))
    w.event("MODE: Supervisor -> OffLine", delta_secs=random.randint(2, 5))
    w.event("HOST CONNECTED", delta_secs=random.randint(60, 150))
    w.event("MODE: OffLine -> InService", delta_secs=random.randint(3, 8))
    w.event("EMV ENABLED", delta_secs=1)
    w.event("GO IN-SERVICE", delta_secs=1)

    cs.last_disp_counts = [0] * 7
    return txn_no + 1


def _gen_unknown_notes(w: EJWriter, atm_type: str, cs: CassetteState,
                       txn_no: int, serno: int, dt_base: datetime,
                       location: str, atm_id: str) -> int:
    """Withdrawal with UNKNOWN denomination in cassette table."""
    # Generate a normal withdrawal first
    next_txn = _gen_withdrawal_success(w, atm_type, cs, txn_no, serno, dt_base, location, atm_id)
    # The cassette table is already written in the withdrawal block.
    # For unknown notes scenario, we append an UNKNOWN entry in the next admin-like block.
    # In practice this manifests as extra lines in the denomination table.
    # We'll just note it in a following supervisor entry.
    w.event("OPERATOR ENTER", delta_secs=random.randint(60, 120))
    w.event("[COUNT_BEFORE_CLEAR]", delta_secs=1)
    w.event("PRINT CASH: ", delta_secs=1)
    for line in cs.s5_rcy_table() if atm_type == "S5" else cs.u1_cassette_table():
        w.raw(line)
    w.raw("")
    # Unknown denomination entry
    unknown_count = random.randint(1, 3)
    w.raw(f"UNKNOWN    {unknown_count:05d}")
    w.raw("")
    return next_txn


def _gen_host_timeout(w: EJWriter, atm_type: str, txn_no: int, serno: int,
                      dt_base: datetime, location: str, atm_id: str) -> int:
    """Generate a host timeout transaction."""
    amount = random.choice(AMOUNTS)
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    opcode = "AB     A"
    disp_counts = [0] * 7

    _emv_start(w, card_no, amount, opcode, serno, disp_counts, atm_type)

    w.event("TRANSACTION REPLIED: FID[5] NEXT[000]", delta_secs=random.randint(25, 40))
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")

    if atm_type == "U1":
        w.event("JOURNAL DATA")
        w.raw(f"    {location}")
        w.raw("")
        w.raw("    DATE          TIME    ATM ID")
        w.raw(f"    {date_str}      {time_str}   {atm_id}   ")
        w.raw(f"    CARD NUMBER   {card_no}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw("    RESPONSE CODE 091")
        w.raw("")
        w.raw("    SAVINGS    ")
        w.raw("")
        w.raw("    HOST DID NOT RESPOND    ")
        w.raw("")
        w.raw(" INCONVENIENCE IS REGRETTED. KINDLY ")
        w.raw(" CALL 24X7 HELPLINE AT 1800-1234 OR")
        w.raw(" 1800112211 OR 080-26599990")
        w.raw("")
    else:
        w.event("JOURNAL DATA")
        w.raw(f"    {location}               ")
        w.raw("")
        w.raw(" DATE           TIME      ATM ID")
        w.raw(f" {date_str}  {time_str} {atm_id}   ")
        w.raw(f"CARD NUMBER   {card_prefix}XXXXXXXXX{card_no[-1]}")
        w.raw("")
        w.raw(f"    TXN NO        {txn_no}")
        w.raw("")
        w.raw("    RESPONSE CODE 091")
        w.raw("")
        w.raw("    SAVINGS    ")
        w.raw("")
        w.raw("    UNABLE TO PROCESS — HOST TIMEOUT")
        w.raw("")
        w.raw(" INCONVENIENCE IS REGRETTED. KINDLY ")
        w.raw(" CALL 24X7 HELPLINE AT 1800-1234 OR")
        w.raw(" 1800112211 OR 080-26599990")
        w.raw("")

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw(f"OPCode           [{opcode}]")
    w.raw("Function ID      [5]")
    w.raw(f"Amount           [{amount:08d}]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    w.event("CARD EJECTED", delta_secs=random.randint(2, 6))
    w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(3, 10))
    return txn_no + 1


# ─── S5 DEPOSIT TRANSACTIONS ─────────────────────────────────────────────────

def _gen_s5_deposit_cardless(w: EJWriter, cs: CassetteState,
                              txn_no: int, serno: int,
                              location: str, atm_id: str,
                              with_reject: bool = False,
                              retracted: bool = False,
                              with_jam: bool = False) -> int:
    """S5 cardless deposit: BAAGC → BBDG → BBBG sequence."""
    mobile_no = _random_mobile()
    benf_ac = "XXXXXXX" + str(random.randint(1000, 9999))
    deposit_denom = random.choice([500, 100])
    deposit_count = random.randint(10, 49)
    deposit_amount = deposit_denom * deposit_count

    # ── BAAGC (initiation) ─────────────────────────────────────────────────
    w.event("CARDLESS TRANSACTION START", delta_secs=random.randint(5, 15))
    w.event("TRANSACTION REQUESTING: OPcode[BAAGC   ]", delta_secs=random.randint(5, 15))
    prev_disp = cs.last_disp_counts
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i, cnt in enumerate(prev_disp, 1):
        w.event(f"LTDISPNOTES{i}COUNT [{cnt:05d}]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event("TRANSACTION REPLIED: FID[5] NEXT[899]", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)
    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [BAAGC   ]")
    w.raw("Function ID      [5]")
    w.raw(f"Trans SEQ Number [{serno:04d}]")
    w.raw("")

    # ── BBDG (notes insertion) ─────────────────────────────────────────────
    w.event("TRANSACTION REQUESTING: OPcode[BBDG    ]", delta_secs=random.randint(15, 25))
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i in range(7):
        w.event(f"LTDISPNOTES{i+1}COUNT [00000]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event("TRANSACTION REPLIED: FID[5] NEXT[538]", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(w.dt)
    time_str = w.dt.strftime("%H:%M")

    w.event("JOURNAL DATA", delta_secs=1)
    w.raw(f"    {location}               ")
    w.raw("")
    w.raw(" DATE           TIME      ATM ID")
    w.raw(f" {date_str}  {time_str} {atm_id}   ")
    w.raw("CARD NUMBER   999999XXXXXXXXX9")
    w.raw("")
    w.raw(f"    TXN NO.    {txn_no}")
    w.raw(f"    MOBILE NO. {mobile_no}")
    w.raw(f"    BENF A/C : {benf_ac}")
    w.raw("    CARDLESS DEPOSIT TRANSACTION")
    w.raw("")
    w.raw("")

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [BBDG    ]")
    w.raw("Function ID      [5]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    if with_jam:
        w.event("SHUTTER OPENED", delta_secs=random.randint(5, 15))
        w.event("NOTES INSERTED IN HOPPER", delta_secs=random.randint(10, 20))
        w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))
        w.event("CASH_JAM_OCCURED", delta_secs=random.randint(5, 10))
        w.event("SAFE DOOR OPENED", delta_secs=random.randint(3, 6))
        w.event("TRANSPORT STATUS CHANGED", delta_secs=1)
        w.event("CASH ACCEPTOR - NOT AVAILABLE", delta_secs=1)
        w.event("TRANSACTION END", delta_secs=random.randint(10, 20))
        cs.last_disp_counts = [0] * 7
        return txn_no + 1

    # ── First insertion ────────────────────────────────────────────────────
    first_count = deposit_count - (1 if with_reject else 0)
    first_amount = deposit_denom * first_count

    w.event("SHUTTER OPENED", delta_secs=random.randint(5, 15))
    w.event("NOTES INSERTED IN HOPPER", delta_secs=random.randint(10, 20))
    w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))
    w.event("CASH RETURNED", delta_secs=random.randint(10, 25))
    w.event("", delta_secs=1)       # [NNNNNN][timestamp] blank event line before Escrow
    w.raw("Escrow Count")
    w.raw(f"{deposit_denom} x {first_count}")
    w.raw(f"Amount   {first_amount}")
    w.raw(cs.cassette_suffix())
    serials_first = [_random_serial_no() for _ in range(max(0, first_count))]
    w.event("", delta_secs=1)       # [NNNNNN][timestamp] blank event line before serials
    w.raw("Accepted Serial No.")
    _write_serial_block(w, serials_first)

    stored_serials = list(serials_first)
    rej_count = 0

    if with_reject:
        rej_count = 1
        w.event("SHUTTER OPENED", delta_secs=random.randint(3, 8))
        w.event("REJECTED COUNT: 1", delta_secs=random.randint(2, 5))
        w.event("CASH REMOVAL DETECTED", delta_secs=1)
        w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))
        # Re-insertion of the rejected note
        w.event("SHUTTER OPENED", delta_secs=random.randint(3, 8))
        w.event("NOTES INSERTED IN HOPPER", delta_secs=random.randint(5, 15))
        w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))
        w.event("", delta_secs=random.randint(3, 8))
        w.raw("Escrow Count")
        w.raw(f"{deposit_denom} x {deposit_count}")
        w.raw(f"Amount   {deposit_amount}")
        w.raw(cs.cassette_suffix())
        extra_serial = _random_serial_no()
        stored_serials = [extra_serial] + stored_serials  # re-inserted note first
        w.event("", delta_secs=1)
        w.raw("Accepted Serial No.")
        w.raw(extra_serial)

    if retracted:
        w.event("RETRACT START", delta_secs=random.randint(5, 15))
        w.event("TRANSACTION END", delta_secs=random.randint(5, 10))
        cs.last_disp_counts = [0] * 7
        return txn_no + 1

    # ── BBBG (final confirmation) ──────────────────────────────────────────
    w.event("TRANSACTION REQUESTING: OPcode[BBBG    ]", delta_secs=random.randint(3, 8))
    w.event(f"LTSERNO [{txn_no:04d}]")
    w.event("LTSTATUS [1]")
    for i in range(7):
        w.event(f"LTDISPNOTES{i+1}COUNT [00000]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event("TRANSACTION REPLIED: FID[-] NEXT[634]", delta_secs=1)
    w.event("Host Store: Stored", delta_secs=random.randint(3, 8))
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str2 = _fmt_date_journal(w.dt)
    time_str2 = w.dt.strftime("%H:%M")

    w.event("JOURNAL DATA", delta_secs=1)
    w.raw(f"    {location}               ")
    w.raw("")
    w.raw(" DATE           TIME      ATM ID")
    w.raw(f" {date_str2}  {time_str2} {atm_id}   ")
    w.raw("CARD NUMBER   999999XXXXXXXXX9")
    w.raw("")
    w.raw(f"    TXN NO.   {txn_no + 1}")
    w.raw(f"    BENF A/C :{benf_ac}")
    w.raw("    CASH DEPOSIT PARTICULARS")
    w.raw("    DENOMS  COUNTS  SUB TOTALS")
    w.raw(f"    {deposit_denom}       {deposit_count:03d}        {deposit_amount}")
    for _ in range(5):
        w.raw("    ")
    w.raw(f"    DEPOSIT AMOUNT:    RS.{deposit_amount:.2f}")
    w.raw("    CARDLESS DEPOSIT TRANSACTION IS")
    w.raw("    SUCCESSFUL")
    w.raw("     THANK YOU")
    w.raw("")
    w.raw("")

    cs.deposit(deposit_denom, deposit_count)
    cs.last_disp_counts = [0] * 7
    new_cs_suffix = cs.cassette_suffix()

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(2, 5))
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [BBBG    ]")
    w.raw("Function ID      [-]")
    w.raw("Amount           [000000]")
    w.raw(f"Trans SEQ Number [{txn_no + 1:04d}]")
    w.raw(f"Rejected Count \t [{rej_count}]")
    w.raw("Stored Count ")
    w.raw(f"[{deposit_denom} x {len(stored_serials)}]")
    w.raw("Stored Serial No. ")
    _write_serial_block(w, stored_serials)
    w.raw("Remain Count ")
    w.raw(f"[{new_cs_suffix}]")
    w.raw("")

    for ln in cs.s5_rcy_table():
        w.raw(ln)
    w.raw("")
    for ln in cs.s5_universal_block():
        w.raw(ln)
    for ln in cs.s5_denomination_block():
        w.raw(ln)
    for ln in cs.s5_total_amount_block():
        w.raw(ln)
    w.raw("")

    w.event("TRANSACTION END", delta_secs=random.randint(5, 15))
    return txn_no + 2  # BAAGC uses serno, BBDG uses txn_no, BBBG uses txn_no+1


def _gen_s5_deposit_card(w: EJWriter, cs: CassetteState,
                          txn_no: int, serno: int, dt_base: datetime,
                          location: str, atm_id: str) -> int:
    """S5 card deposit: IAAA → IAAA I → GAAA sequence."""
    card_prefix = random.choice(CARD_PREFIXES)
    card_no = _random_card(card_prefix)
    deposit_denom = random.choice([500, 100])
    # round to nearest 10 for clean LT encoding
    deposit_count = (random.randint(1, 9)) * 10
    deposit_amount = deposit_denom * deposit_count

    net_idx = random.randint(0, 2)
    net_id, net_name = NETWORKS[net_idx]

    reject_count = random.randint(1, min(deposit_count - 1, 15))
    final_count = deposit_count  # total notes ultimately stored

    # ── IAAA: deposit initiation (no PIN yet) ─────────────────────────────
    w.event("TRANSACTION START", delta_secs=random.randint(5, 15))
    w.event(f"EMV CANDIDATES: {random.randint(1, 2)}", delta_secs=random.randint(1, 3))
    w.event(f"EMV FINAL APP SELECTION SUCCESS: {net_id}({net_name})", delta_secs=1)
    w.event(f"CARD NUMBER {card_no}", delta_secs=random.randint(10, 30))
    w.event("TRANSACTION REQUESTING: OPcode[IAAA    ]", delta_secs=random.randint(5, 15))
    prev_disp = cs.last_disp_counts
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i, cnt in enumerate(prev_disp, 1):
        w.event(f"LTDISPNOTES{i}COUNT [{cnt:05d}]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event(f"TRANSACTION REPLIED: FID[5] NEXT[{random.randint(100, 999)}]",
            delta_secs=random.randint(2, 5))
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)
    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [IAAA    ]")
    w.raw("Function ID      [5]")
    w.raw(f"Trans SEQ Number [{serno:04d}]")
    w.raw("")

    # ── IAAA I: PIN + amount confirmation ─────────────────────────────────
    w.event("PIN ENTERED", delta_secs=random.randint(8, 20))
    w.event("EMV 1ST GENAC: ARQC", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION REQUESTING: OPcode[IAAA I  ]", delta_secs=1)
    w.event(f"LTSERNO [{serno:04d}]")
    w.event("LTSTATUS [1]")
    for i in range(7):
        w.event(f"LTDISPNOTES{i+1}COUNT [00000]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event(f"TRANSACTION REPLIED: FID[5] NEXT[{random.randint(10, 999):03d}]",
            delta_secs=random.randint(2, 5))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8080048000, TSI: 6800", delta_secs=1)
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)
    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=1)
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [IAAA I  ]")
    w.raw("Function ID      [5]")
    w.raw(f"Trans SEQ Number [{txn_no:04d}]")
    w.raw("")

    # ── Physical notes insertion ───────────────────────────────────────────
    w.event(f"EMV FINAL APP SELECTION SUCCESS: {net_id}({net_name})",
            delta_secs=random.randint(3, 8))
    w.event("SHUTTER OPENED", delta_secs=random.randint(5, 15))
    w.event("NOTES INSERTED IN HOPPER", delta_secs=random.randint(15, 35))
    w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))

    first_accepted = deposit_count - reject_count
    w.event("CASH RETURNED", delta_secs=random.randint(20, 45))
    w.event("", delta_secs=1)
    w.raw("Escrow Count")
    w.raw(f"{deposit_denom} x {deposit_count}")
    w.raw(f"Amount   {deposit_amount}")
    w.raw(cs.cassette_suffix())
    serials_first = [_random_serial_no() for _ in range(first_accepted)]
    w.event("", delta_secs=1)
    w.raw("Accepted Serial No.")
    _write_serial_block(w, serials_first)

    all_stored_serials = list(serials_first)
    cumulative_rej = reject_count

    # Rejection + re-insertion round
    w.event("SHUTTER OPENED", delta_secs=random.randint(3, 8))
    w.event(f"REJECTED COUNT: {cumulative_rej}", delta_secs=random.randint(2, 5))
    w.event("CASH REMOVAL DETECTED", delta_secs=1)
    w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))

    w.event("SHUTTER OPENED", delta_secs=random.randint(3, 8))
    w.event("NOTES INSERTED IN HOPPER", delta_secs=random.randint(8, 20))
    w.event("SHUTTER CLOSED", delta_secs=random.randint(3, 8))
    w.event("CASH RETURNED", delta_secs=random.randint(20, 45))
    w.event("", delta_secs=1)
    w.raw("Escrow Count")
    w.raw(f"{deposit_denom} x {final_count}")
    w.raw(f"Amount   {final_count * deposit_denom}")
    w.raw(cs.cassette_suffix())
    serials_second = [_random_serial_no() for _ in range(reject_count)]
    all_stored_serials = serials_second + all_stored_serials
    w.event("", delta_secs=1)
    w.raw("Accepted Serial No.")
    _write_serial_block(w, serials_second)

    # Final rejection check (all accepted)
    w.event("SHUTTER OPENED", delta_secs=random.randint(2, 6))
    w.event("REJECTED COUNT: 0", delta_secs=random.randint(1, 3))
    w.event("CASH REMOVAL DETECTED", delta_secs=1)
    w.event("SHUTTER CLOSED", delta_secs=random.randint(2, 5))

    # ── GAAA: store confirmation ───────────────────────────────────────────
    w.event("EMV 1ST GENAC: ARQC", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION REQUESTING: OPcode[GAAA    ]", delta_secs=1)
    w.event(f"LTSERNO [{txn_no:04d}]")
    w.event("LTSTATUS [1]")
    for i in range(7):
        w.event(f"LTDISPNOTES{i+1}COUNT [00000]")
    w.event("LTCOINDISPAMT [0000000000000000000000000]")
    w.event("LTCASHDEPDIRECTION [0]")
    w.event("LTCASHDEPCOUNT [00000000000000000000]")
    w.event("LTCASHNOTESREF [00000]")
    w.event("LTCASHNOTESREJ [00000]")
    w.event("LTCASHNOTESENC [00000]")
    w.event("LTCASHNOTESESC [00000]")
    w.event("LTTXNCASHRECYCLED [00]")
    w.event("TRANSACTION REPLIED: FID[-] NEXT[634]", delta_secs=random.randint(2, 5))
    w.event("EMV EXTERNAL AUTHENTICATE: SUCCESS", delta_secs=1)
    w.event("EMV 2ND GENAC: TC", delta_secs=1)
    w.event("EMV TVR: 8080048000, TSI: 6800", delta_secs=1)
    w.event("Host Store: Stored", delta_secs=random.randint(8, 20))
    w.event("SOLICITED STATUS SEND: SUCCEEDED", delta_secs=1)

    date_str = _fmt_date_journal(dt_base)
    time_str = w.dt.strftime("%H:%M")
    acc_no = _random_account()

    w.event("JOURNAL DATA", delta_secs=1)
    w.raw(f"    {location}               ")
    w.raw("")
    w.raw(" DATE           TIME      ATM ID")
    w.raw(f" {date_str}  {time_str} {atm_id}   ")
    w.raw(f"CARD NUMBER   {card_prefix}XXXXXXXXX{card_no[-1]}")
    w.raw("")
    w.raw(f"    TXN NO.   {txn_no + 1}")
    w.raw(f"    BENF A/C :{acc_no}")
    w.raw("    CASH DEPOSIT PARTICULARS")
    w.raw("    DENOMS  COUNTS  SUB TOTALS")
    w.raw(f"    {deposit_denom}       {final_count:03d}        {final_count * deposit_denom}")
    for _ in range(5):
        w.raw("    ")
    w.raw(f"    DEPOSIT AMOUNT:    RS.{final_count * deposit_denom:.2f}")
    w.raw(f"    AVAILABLE BALANCE:    RS.{_random_balance()}")
    w.raw("     DEPOSIT TRANSACTION IS SUCCESSFUL")
    w.raw("     THANK YOU")
    w.raw("")
    w.raw("")

    cs.deposit(deposit_denom, final_count)
    cs.last_disp_counts = [0] * 7
    new_cs_suffix = cs.cassette_suffix()

    w.event("TRANSACTION DATA (COMPLETED)", delta_secs=random.randint(2, 5))
    w.raw("[TRANSACTION RECORD]")
    w.raw("OPCode           [GAAA    ]")
    w.raw("Function ID      [-]")
    w.raw("Amount           [000000]")
    w.raw(f"Trans SEQ Number [{txn_no + 1:04d}]")
    w.raw(f"Rejected Count \t [{reject_count}]")
    w.raw("Stored Count ")
    w.raw(f"[{deposit_denom} x {final_count}]")
    w.raw("Stored Serial No. ")
    _write_serial_block(w, all_stored_serials)
    w.raw("Remain Count ")
    w.raw(f"[{new_cs_suffix}]")
    w.raw("")

    for ln in cs.s5_rcy_table():
        w.raw(ln)
    w.raw("")
    for ln in cs.s5_universal_block():
        w.raw(ln)
    for ln in cs.s5_denomination_block():
        w.raw(ln)
    for ln in cs.s5_total_amount_block():
        w.raw(ln)
    w.raw("")
    w.raw("")

    w.event("CARD EJECTED", delta_secs=random.randint(5, 10))
    w.event("CARD TAKEN", delta_secs=random.randint(1, 3))
    w.event("TRANSACTION END", delta_secs=random.randint(5, 15))
    # IAAA uses serno, IAAA I uses txn_no, GAAA uses txn_no+1 → next = txn_no+2
    return txn_no + 2


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_hyosung_ej(
    atm_type: str,
    tran_date: datetime,
    num_transactions: int,
    selected_cases: list,
    atm_id: str = None,
    location: str = None,
    output_dir: Path = None,
) -> dict:
    """
    Generate a Hyosung EJ file for SBI ATMs.

    Args:
        atm_type: 'U1' or 'S5'
        tran_date: transaction date
        num_transactions: number of customer transactions (excluding admin)
        selected_cases: list of case IDs to include
        atm_id: ATM ID string (auto-generated if None)
        location: branch name (random if None)
        output_dir: output directory (uses /tmp if None)

    Returns:
        dict with run_id, file_name, counts
    """
    if atm_type not in ("U1", "S5"):
        raise ValueError(f"atm_type must be 'U1' or 'S5', got {atm_type!r}")

    random.seed()  # non-deterministic

    # ── Setup ──────────────────────────────────────────────────────────────
    run_id = uuid.uuid4().hex[:12]
    if atm_id is None:
        atm_id = _gen_atm_id(atm_type)
    if location is None:
        location = random.choice(LOCATIONS)
    if output_dir is None:
        output_dir = Path("/tmp")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ip = _random_ip()

    # File naming
    date_str_file = tran_date.strftime("%Y%m%d")
    if atm_type == "U1":
        file_name = f"{atm_id}{date_str_file}_{ip}.txt"
    else:
        hex5 = _random_hex5()
        file_name = f"{atm_id}{date_str_file}_{ip}_{hex5}"

    # ── Start counter ──────────────────────────────────────────────────────
    if atm_type == "U1":
        start_counter = 1
    else:
        start_counter = random.randint(400000, 600000)

    # Start time: midnight of tran_date
    start_dt = tran_date.replace(hour=0, minute=5, second=0, microsecond=0)

    w = EJWriter(start_counter, start_dt)
    cs = CassetteState(atm_type)

    # ── Header ─────────────────────────────────────────────────────────────
    w.event("SBI Electronic Journal")

    # ── Transaction planning ────────────────────────────────────────────────
    case_set = set(selected_cases)

    # Filter S5-only cases if U1
    if atm_type == "U1":
        s5_only = {"deposit_card", "deposit_cardless", "deposit_rejected",
                   "deposit_retracted", "deposit_jam"}
        case_set -= s5_only

    # Build a plan: list of (case_id, instances) to generate
    # Admin: always include 1-2 if admin_cassette in cases, else 1 admin anyway
    num_admin = 2 if "admin_cassette" in case_set else 1
    plan = []

    # Special cases that generate 1-3 instances
    special_cases = [c for c in case_set
                     if c not in ("sync", "withdrawal_success", "admin_cassette")]
    for case in special_cases:
        instances = random.randint(1, 3)
        plan.extend([case] * instances)

    if "withdrawal_success" in case_set:
        plan.extend(["withdrawal_success"] * random.randint(2, 4))

    # Fill remaining slots with "sync" (normal withdrawals) and balance inquiries
    used = len(plan)
    remaining = max(0, num_transactions - used - num_admin)
    filler = ["sync"] * remaining
    plan.extend(filler)

    # Shuffle the plan, then insert admin transactions at 1/3 and 2/3 points
    random.shuffle(plan)
    admin_insert_1 = len(plan) // 3
    admin_insert_2 = (2 * len(plan)) // 3
    plan.insert(admin_insert_2, "admin_cassette")
    plan.insert(admin_insert_1, "admin_cassette")

    # ── Execute plan ───────────────────────────────────────────────────────
    txn_no = random.randint(900, 1000)
    serno = txn_no - 1

    counts = {
        "total": 0,
        "withdrawal_success": 0,
        "balance_inquiry": 0,
        "declined_insufficient": 0,
        "declined_unauthorized": 0,
        "host_timeout": 0,
        "cash_not_taken": 0,
        "notes_in_reject": 0,
        "partial_ej": 0,
        "admin_cassette": 0,
        "unknown_notes": 0,
        "deposit_card": 0,
        "deposit_cardless": 0,
        "deposit_rejected": 0,
        "deposit_retracted": 0,
        "deposit_jam": 0,
        "sync": 0,
    }

    for case in plan:
        # Advance time by 5-30 minutes between transactions
        w.advance(random.randint(300, 1800))

        if case in ("sync", "withdrawal_success"):
            txn_no = _gen_withdrawal_success(w, atm_type, cs, txn_no, serno,
                                              tran_date, location, atm_id)
            counts["withdrawal_success" if case == "withdrawal_success" else "sync"] += 1

        elif case == "balance_inquiry":
            txn_no = _gen_balance_inquiry(w, atm_type, txn_no, serno,
                                           tran_date, location, atm_id)
            counts["balance_inquiry"] += 1

        elif case == "declined_insufficient":
            txn_no = _gen_declined(w, atm_type, cs, txn_no, serno,
                                    tran_date, location, atm_id, "insufficient")
            counts["declined_insufficient"] += 1

        elif case == "declined_unauthorized":
            txn_no = _gen_declined(w, atm_type, cs, txn_no, serno,
                                    tran_date, location, atm_id, "unauthorized")
            counts["declined_unauthorized"] += 1

        elif case == "host_timeout":
            txn_no = _gen_host_timeout(w, atm_type, txn_no, serno,
                                        tran_date, location, atm_id)
            counts["host_timeout"] += 1

        elif case == "cash_not_taken":
            txn_no = _gen_cash_not_taken(w, atm_type, cs, txn_no, serno,
                                          tran_date, location, atm_id)
            counts["cash_not_taken"] += 1

        elif case == "notes_in_reject":
            txn_no = _gen_notes_in_reject(w, atm_type, cs, txn_no, serno,
                                           tran_date, location, atm_id)
            counts["notes_in_reject"] += 1

        elif case == "partial_ej":
            txn_no = _gen_partial_ej(w, atm_type, cs, txn_no, serno, tran_date)
            counts["partial_ej"] += 1

        elif case == "admin_cassette":
            txn_no = _gen_admin(w, atm_type, cs, txn_no, serno,
                                 tran_date, location, atm_id)
            counts["admin_cassette"] += 1

        elif case == "unknown_notes":
            txn_no = _gen_unknown_notes(w, atm_type, cs, txn_no, serno,
                                         tran_date, location, atm_id)
            counts["unknown_notes"] += 1

        # ── S5-only ──────────────────────────────────────────────────────
        elif case == "deposit_cardless" and atm_type == "S5":
            txn_no = _gen_s5_deposit_cardless(w, cs, txn_no, serno,
                                               location, atm_id)
            counts["deposit_cardless"] += 1

        elif case == "deposit_rejected" and atm_type == "S5":
            txn_no = _gen_s5_deposit_cardless(w, cs, txn_no, serno,
                                               location, atm_id, with_reject=True)
            counts["deposit_rejected"] += 1

        elif case == "deposit_retracted" and atm_type == "S5":
            txn_no = _gen_s5_deposit_cardless(w, cs, txn_no, serno,
                                               location, atm_id, retracted=True)
            counts["deposit_retracted"] += 1

        elif case == "deposit_jam" and atm_type == "S5":
            txn_no = _gen_s5_deposit_cardless(w, cs, txn_no, serno,
                                               location, atm_id, with_jam=True)
            counts["deposit_jam"] += 1

        elif case == "deposit_card" and atm_type == "S5":
            txn_no = _gen_s5_deposit_card(w, cs, txn_no, serno,
                                           tran_date, location, atm_id)
            counts["deposit_card"] += 1

        serno = txn_no - 1
        counts["total"] += 1

    # ── Write file ─────────────────────────────────────────────────────────
    out_path = output_dir / file_name
    with open(out_path, "w", encoding="ascii", errors="replace") as f:
        f.write(w.get_text())

    # ── Write manifest ─────────────────────────────────────────────────────
    manifest = {
        "run_id": run_id,
        "atm_type": atm_type,
        "atm_id": atm_id,
        "location": location,
        "tran_date": tran_date.strftime("%Y-%m-%d"),
        "file_name": file_name,
        "file_path": str(out_path),
        "files": {"ej": file_name},
        "counts": counts,
        "cases_included": [c for c in selected_cases if counts.get(c, 0) > 0],
    }
    manifest_path = output_dir / f"manifest_ej_{run_id}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return {
        "run_id": run_id,
        "file_name": file_name,
        "atm_id": atm_id,
        "location": location,
        "counts": counts,
        "cases_included": manifest["cases_included"],
    }
