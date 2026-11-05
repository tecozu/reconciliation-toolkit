"""Bank statements -> one common transaction schema.

Two input formats:
* **OFX**, the standard export of virtually every bank (SGML-like, parsed without deps);
* **CSV** exports in the typical Brazilian layout (`;`, decimal comma, dd/mm/yyyy,
  a running balance column).

Every transaction gets a deterministic `txn_id`, so the same transaction arriving in two
overlapping statement files is loaded once. `check_balance_continuity` proves nothing was
lost: opening balance + sum of amounts must equal the closing balance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

COLUMNS = [
    "txn_id",
    "account",
    "posted_on",
    "amount",
    "description",
    "reference",
    "balance",
    "source_file",
]


@dataclass
class Statement:
    account: str
    transactions: pd.DataFrame
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


def _txn_id(account: str, posted_on: date, amount: Decimal, ref: str, seq: int) -> str:
    raw = f"{account}|{posted_on}|{amount}|{ref}|{seq}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _with_ids(df: pd.DataFrame) -> pd.DataFrame:
    # seq disambiguates identical transactions on the same day (two equal PIX payments)
    df = df.copy()
    df["_seq"] = df.groupby(["account", "posted_on", "amount", "reference"]).cumcount()
    df["txn_id"] = [
        _txn_id(a, d, m, r, s)
        for a, d, m, r, s in zip(
            df["account"], df["posted_on"], df["amount"], df["reference"], df["_seq"], strict=True
        )
    ]
    return df.drop(columns="_seq")[COLUMNS]


# --------------------------------------------------------------------------- OFX
_TAG = re.compile(r"<(\w+)>([^<\r\n]*)")


def _ofx_date(v: str) -> date:
    return datetime.strptime(v[:8], "%Y%m%d").date()


def parse_ofx(path: str | Path) -> Statement:
    text = Path(path).read_text(encoding="latin-1")
    acct = re.search(r"<ACCTID>([^<\r\n]+)", text)
    account = acct.group(1).strip() if acct else "unknown"
    rows = []
    for block in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, flags=re.S):
        tags = {k: v.strip() for k, v in _TAG.findall(block)}
        rows.append(
            {
                "account": account,
                "posted_on": _ofx_date(tags["DTPOSTED"]),
                "amount": Decimal(tags["TRNAMT"].replace(",", ".")),
                "description": tags.get("MEMO") or tags.get("NAME", ""),
                "reference": tags.get("CHECKNUM") or tags.get("REFNUM") or tags.get("FITID", ""),
                "balance": None,
                "source_file": Path(path).name,
            }
        )
    closing = re.search(r"<LEDGERBAL>\s*<BALAMT>([-\d.,]+)", text)
    closing_balance = Decimal(closing.group(1).replace(",", ".")) if closing else None
    df = _with_ids(pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "txn_id"]))
    # OFX carries no opening balance: deriving one from the closing balance would make the
    # continuity check pass by construction, so it is left unknown on purpose.
    return Statement(account, df, None, closing_balance)


# --------------------------------------------------------------------------- CSV (BR)
def parse_csv_br(path: str | Path, account: str) -> Statement:
    """Columns: Data;Descricao;Documento;Valor;Saldo (decimal comma, dd/mm/yyyy)."""
    raw = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")

    def money(v: str) -> Decimal | None:
        v = v.strip()
        return Decimal(v.replace(".", "").replace(",", ".")) if v else None

    df = pd.DataFrame(
        {
            "account": account,
            "posted_on": pd.to_datetime(raw["Data"], format="%d/%m/%Y").dt.date,
            "amount": raw["Valor"].map(money),
            "description": raw["Descricao"].str.strip(),
            "reference": raw["Documento"].str.strip(),
            "balance": raw["Saldo"].map(money),
            "source_file": Path(path).name,
        }
    )
    opening_row = df["description"].str.upper().str.startswith("SALDO ANTERIOR")
    opening = df.loc[opening_row, "balance"].iloc[0] if opening_row.any() else None
    df = df[~opening_row & df["amount"].notna()]
    closing = df["balance"].dropna().iloc[-1] if df["balance"].notna().any() else None
    return Statement(account, _with_ids(df), opening, closing)


# --------------------------------------------------------------------------- controls
def check_balance_continuity(st: Statement) -> Decimal | None:
    """Return the gap (0 = statement is complete), or None when balances are unavailable."""
    if st.opening_balance is None or st.closing_balance is None:
        return None
    return st.opening_balance + sum(st.transactions["amount"], Decimal(0)) - st.closing_balance


def combine(statements: list[Statement]) -> pd.DataFrame:
    """Union of statements, deduplicated by txn_id (overlapping exports are common)."""
    frames = [s.transactions for s in statements if not s.transactions.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames).drop_duplicates("txn_id").sort_values(["posted_on", "txn_id"])
