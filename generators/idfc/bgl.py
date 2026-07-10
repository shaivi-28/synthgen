import random
from datetime import datetime
from typing import List, Tuple

from generators.nfs_atm import ScenarioGroup

_MONTH = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _ddmonyy(dt: datetime) -> str:
    return f"{dt.day:02d}-{_MONTH[dt.month - 1]}-{str(dt.year)[2:]}"


def _fmt_balance(rupees: float) -> str:
    if rupees == 0.0:
        return f"{'0':>17}"
    return f"{rupees:17.2f}"


def _data_row(
    date_str: str,
    account_no: str,
    bgl_name: str,
    nature: str,
    branch: str,
    home_branch: str,
    balance: float,
) -> str:
    return (
        f"{date_str}|"
        f"{account_no:<40}|"
        f"{bgl_name:<40}|"
        f"INR|"
        f"{nature:5}|"
        f"{branch:5}|"
        f"{home_branch:<40}|"
        f"{_fmt_balance(balance)}|"
        f"ACTIVE"
    )


def build_bgl_file(
    groups: List[ScenarioGroup],
    tran_date: datetime,
    config: dict,
) -> Tuple[str, str]:
    cbs = config["cbs"]
    atm_gl = cbs["atm_gl_account"]
    pos_gl = cbs["pos_gl_account"]
    branch = cbs["branch_code"]

    date_str = _ddmonyy(tran_date)
    date_yyyymmdd = tran_date.strftime("%Y%m%d")
    filename = f"BGL_BALANCE_REPORT_{date_yyyymmdd}_{random.randint(100000, 999999)}.txt"

    # Net closing balance per GL (paise): forward = CR (+), reversal = DR (-)
    atm_paise = 0
    pos_paise = 0
    has_atm = False
    has_pos = False

    for sg in groups:
        for tx in sg.cbs_rows:
            is_rev = tx.msg_type == "0420"
            if tx.mcc == "6011":
                has_atm = True
                atm_paise += -tx.amount if is_rev else tx.amount
            else:
                has_pos = True
                pos_paise += -tx.amount if is_rev else tx.amount

    header = (
        f"(SELECTSY|"
        f"{'ACCOUNT_NO':<40}|"
        f"{'BGL_NAME':<40}|"
        f"CUR|NATUR|BRANC|"
        f"{'GL_HOME_BRANCH_NAME':<40}|"
        f"  CURRENT_BALANCE|STATUS"
    )
    separator = (
        f"{'-'*9}|{'-'*40}|{'-'*40}|{'-'*3}|{'-'*5}|{'-'*5}|"
        f"{'-'*40}|{'-'*17}|{'-'*6}"
    )

    lines = ["", header, separator]

    if has_atm:
        lines.append(_data_row(
            date_str, atm_gl, "VISA ATM PAYABLE A/C",
            "21001", branch, "Cards Operations",
            atm_paise / 100,
        ))

    if has_pos:
        lines.append(_data_row(
            date_str, pos_gl, "VISA POS PAYABLE A/C",
            "21001", branch, "Cards Operations",
            pos_paise / 100,
        ))

    return "\n".join(lines), filename
