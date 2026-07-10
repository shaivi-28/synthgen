from datetime import datetime
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction


def _tran_type_code(tx: Transaction) -> str:
    is_atm = tx.mcc == "6011"
    is_acq = tx.tran_type == "OW"
    is_rev = tx.msg_type == "0420"
    if is_atm:
        if is_acq:
            return "OWCR" if is_rev else "OWDR"
        return "CWRR" if is_rev else "CWDR"
    return "PRCR" if is_rev else "PRDR"


def _is_rev(tx: Transaction) -> bool:
    return tx.msg_type == "0420"


def _tran_ref(tx: Transaction) -> str:
    if tx.mcc == "6011":
        # ATM: journal_no-based so CWDR+CWRR (same auth_id) get unique MERGE keys
        return tx.journal_no[:6] + "000"
    # POS: auth_id-based; "001" suffix for reversals keeps MERGE ON key unique while
    # SUBSTR(trxn_id,1,6) stays auth_id[:6] → switch_approval_code = PTLF approvalcode.
    suffix = "001" if tx.msg_type == "0420" else "000"
    return tx.auth_id[:6] + suffix


def _auth_code(tx: Transaction) -> str:
    # Must match CBSMCW AUTH_CODE
    return tx.auth_id.zfill(8)


def _card_pool_row(tx: Transaction, gl_account: str, branch: str, card_pool: str, date_ddmmyyyy: str, network: str = "VISA") -> str:
    """One card posting row — used for both ATM (CWDR/CWRR) and POS (PRDR/PRCR)."""
    amount_17 = f"{tx.amount / 100:017.2f}"
    trxn_id = _tran_ref(tx)
    auth = _auth_code(tx)
    rrn_12 = tx.rrn
    auth6 = tx.auth_id[:6]
    tx_time = tx.tran_date.strftime("%H%M%S")
    tt = _tran_type_code(tx)
    is_reversal = _is_rev(tx)
    # Both ATM and POS: forward=CR, reversal=DR
    dr_cr = "DR" if is_reversal else "CR"
    narration = "GENERATED CORR O/S CASH POSTING" if is_reversal else "GENERATED CASH POSTING"
    return (
        f"D|{gl_account}|{branch}|{card_pool}|{date_ddmmyyyy}|{date_ddmmyyyy}|{dr_cr}"
        f"|{amount_17}|{trxn_id}|{narration}||||{network}|{tt}|{auth}|{rrn_12}|{auth6}"
        f"|{tx_time}|||||"
    )


def _build_section(
    txs: List[Transaction],
    gl_account: str,
    card_pool: str,
    branch: str,
    date_ddmmyyyy: str,
    section_name: str,
    network: str = "VISA",
    no_duplicates: bool = True,
) -> List[str]:
    """H| header + one card pool row per transaction (ATM or POS)."""
    if no_duplicates:
        seen: set = set()
        unique_txs = []
        for tx in txs:
            dr_cr = "DR" if _is_rev(tx) else "CR"
            # Dedup key mirrors CBSMCW exactly so both files always contain
            # the same set of transactions — nothing gets deduplicated in one
            # file but not the other.
            key = (
                tx.card_pan[:16],
                tx.rrn.zfill(12),
                dr_cr,
                f"{tx.amount / 100:.2f}",
                tx.tran_date.strftime("%d-%m-%Y"),
                tx.tran_date.strftime("%H:%M:%S"),
                _tran_type_code(tx),
                _auth_code(tx),
            )
            if key not in seen:
                seen.add(key)
                unique_txs.append(tx)
        txs = unique_txs

    fwd = sum(tx.amount for tx in txs if not _is_rev(tx))
    rev = sum(tx.amount for tx in txs if _is_rev(tx))
    net = (fwd - rev) / 100
    lines = [f"H|{date_ddmmyyyy}|{gl_account}|{section_name}|{net:017.2f}|CR"]
    for tx in txs:
        lines.append(_card_pool_row(tx, gl_account, branch, card_pool, date_ddmmyyyy, network=network))
    return lines


def build_fssgl_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    run_ts: datetime,
) -> Tuple[str, str]:
    cbs = config["cbs"]
    atm_gl = cbs["atm_gl_account"]
    pos_gl = cbs["pos_gl_account"]
    branch = cbs["branch_code"]
    card_pool = cbs["card_pool_account"]
    network = config.get("cbsmcw", {}).get("network", "VISA")
    atm_section = config.get("fssgl", {}).get("atm_section_name", f"{network} ATM PAYABLE A/C")
    pos_section = config.get("fssgl", {}).get("pos_section_name", f"{network} POS PAYABLE A/C")

    date_ddmmyyyy = tran_date.strftime("%d%m%Y")
    ts_str = run_ts.strftime("%H%M%S")
    filename = f"FSS-GL-OUTFILE_{date_ddmmyyyy}_{ts_str}.txt"

    all_txs: List[Transaction] = []
    for sg in groups:
        all_txs.extend(sg.cbs_rows)

    # Mirror CBSMCW's allowed_tran_types={"CWDR","CWRR","PRDR","PRCR"}:
    # exclude ACQ_ATM (OW → OWDR/OWCR) and merchandise credit (MC → MCCR/MCDR)
    # so FSSGL and CBSMCW always contain exactly the same transaction set.
    atm_txs = [tx for tx in all_txs if tx.mcc == "6011" and tx.tran_type not in ("OW", "MC")]
    pos_txs = [tx for tx in all_txs if tx.mcc != "6011" and tx.tran_type != "MC"]

    lines: List[str] = []

    if atm_txs:
        lines.extend(_build_section(
            atm_txs, atm_gl, card_pool, branch, date_ddmmyyyy, atm_section, network=network
        ))

    if pos_txs:
        lines.extend(_build_section(
            pos_txs, pos_gl, card_pool, branch, date_ddmmyyyy, pos_section, network=network
        ))

    content = "\n".join(lines)
    return content, filename
