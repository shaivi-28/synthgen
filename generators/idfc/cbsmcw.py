import random
from datetime import datetime
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup, Transaction


def _tran_type(tx: Transaction, tran_type_map: dict) -> str:
    is_atm = tx.mcc == "6011"
    is_acq = tx.tran_type == "OW"
    is_mc  = tx.tran_type == "MC"   # TC 06 merchandise credit
    is_rev = tx.msg_type == "0420"
    if is_mc:
        key = "MC_rev" if is_rev else "MC_fwd"
    elif is_atm:
        if is_acq:
            key = "ACQ_ATM_rev" if is_rev else "ACQ_ATM_fwd"
        else:
            key = "ATM_rev" if is_rev else "ATM_fwd"
    else:
        key = "POS_rev" if is_rev else "POS_fwd"
    return tran_type_map.get(key, "CWDR")


def _mask_pan(pan: str) -> str:
    pan = pan.strip()
    if len(pan) < 9:
        return pan
    return f"{pan[:6]}*******{pan[-3:]}"


def _network(config: dict) -> str:
    return config.get("cbsmcw", {}).get("network", "VISA")


def _tran_ref(tx: Transaction) -> str:
    if tx.mcc == "6011":
        # ATM: journal_no-based so CWDR+CWRR (same auth_id) get unique MERGE keys
        return tx.journal_no[:6] + "000"
    # POS: auth_id-based so SUBSTR(trxn_id,1,6) = auth_id = switch_approval_code = PTLF approvalcode.
    # Forward uses "000" suffix, reversal uses "001" — keeps MERGE ON key unique while
    # SUBSTR(tran_ref,1,6) stays auth_id[:6] for both, so switch_approval_code matches PTLF.
    suffix = "001" if tx.msg_type == "0420" else "000"
    return tx.auth_id[:6] + suffix


def _auth_code(tx: Transaction) -> str:
    return tx.auth_id.zfill(8)


def build_cbsmcw_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
    no_duplicates: bool = True,
    allowed_tran_types: set = None,
) -> Tuple[str, str]:
    tran_type_map = config["cbsmcw"]["tran_type_map"]
    valid_types = (
        allowed_tran_types
        if allowed_tran_types is not None
        else {"CWDR", "CWRR", "PRDR", "PRCR", "OWDR", "OWCR", "MCCR", "MCDR"}
    )

    date_str = tran_date.strftime("%d%m%Y")
    header_date = tran_date.strftime("%Y%m%d")

    rows: List[str] = []
    seen_keys: set = set()

    for sg in groups:
        for tx in sg.cbs_rows:
            tt = _tran_type(tx, tran_type_map)
            if tt not in valid_types:
                continue

            dr_cr = "C" if (tx.msg_type == "0420" or tx.tran_type == "MC") else "D"
            if no_duplicates:
                dedup_key = (
                    tx.card_pan[:16],
                    tx.rrn.zfill(12),
                    dr_cr,
                    f"{tx.amount / 100:.2f}",
                    tx.tran_date.strftime("%d-%m-%Y"),
                    tx.tran_date.strftime("%H:%M:%S"),
                    tt,
                    _auth_code(tx),
                )
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

            tran_code = str(random.randint(10000, 99999))
            amount_str = f"{tx.amount / 100:.2f}"
            card_num = _mask_pan(tx.card_pan[:16])
            rrn_12 = tx.rrn.zfill(12)
            acct = tx.account_no[:14].ljust(14)
            tran_date_fmt = tran_date.strftime("%d-%m-%Y")
            tran_time_fmt = tx.tran_date.strftime("%H:%M:%S")
            network = _network(config)
            auth_code = _auth_code(tx)
            tran_ref = _tran_ref(tx)
            posting_date = tran_date.strftime("%d-%m-%Y")

            row = "|".join([
                tran_code,
                amount_str,
                dr_cr,
                card_num,
                rrn_12,
                acct,
                tran_date_fmt,
                tran_time_fmt,
                tt,
                network,
                auth_code,
                tran_ref,
                posting_date,
                "0",
                "0",
                "0",
                tx.tran_category,
            ])
            rows.append(row)

    count = len(rows)
    header = f"FH{header_date}{count:06d}"
    content = header + "\n" + "\n".join(rows)
    filename = f"CBSMCWISS{date_str}{random.randint(10, 99)}.txt"
    return content, filename
