"""recon: everyday reconciliation tasks for financial operations.

  recon compare FILE_A FILE_B --key id --value present_value:0.01 [--decimal-a , --decimal-b .]
  recon health FILE [--delimiter ;]
  recon statements FILE.ofx|FILE.csv [...] [--account ACC]   balance continuity + dedup
  recon match --receivables R.csv --statements S.ofx [...] --as-of 2026-07-31 [--out DIR]

Exit code 1 when something needs attention (breaks, damaged file, balance gap).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from recon.compare import reconcile, to_number
from recon.health import check_file, render
from recon.matching import match_payments
from recon.statements import check_balance_continuity, combine, parse_csv_br, parse_ofx


def _load_statement(path: Path, account: str | None):
    return (
        parse_ofx(path)
        if path.suffix.lower() == ".ofx"
        else parse_csv_br(path, account or path.stem)
    )


def _show(title: str, df: pd.DataFrame, limit: int = 15) -> None:
    print(f"\n{title} ({len(df)})")
    if not df.empty:
        print(df.head(limit).to_string(index=False))


def cmd_compare(a) -> int:
    read = lambda p: pd.read_csv(p, sep=a.delimiter, dtype=str, keep_default_na=False)  # noqa: E731
    left, right = read(a.file_a), read(a.file_b)
    tolerances = {col: float(tol) for col, tol in (v.split(":") for v in a.value)}
    for col in tolerances:  # each file may use its own decimal separator
        left[col] = to_number(left[col], a.decimal_a)
        right[col] = to_number(right[col], a.decimal_b)
    res = reconcile(
        left, right, key=a.key, compare=tolerances, names=(Path(a.file_a).stem, Path(a.file_b).stem)
    )
    print("summary:", res.summary())
    _show("totals", res.totals)
    _show(f"only in {res.left_name}", res.only_left.drop(columns="_key"))
    _show(f"only in {res.right_name}", res.only_right.drop(columns="_key"))
    _show("value breaks", res.breaks)
    return 0 if res.ok else 1


def cmd_health(a) -> int:
    bad = 0
    for f in a.files:
        rep = check_file(f, delimiter=a.delimiter)
        print(render(rep))
        bad += not rep.ok
    return 1 if bad else 0


def cmd_statements(a) -> int:
    sts = [_load_statement(Path(f), a.account) for f in a.files]
    gaps = 0
    for f, st in zip(a.files, sts, strict=True):
        gap = check_balance_continuity(st)
        status = "n/a" if gap is None else ("OK" if gap == 0 else f"GAP {gap}")
        gaps += gap not in (None, 0)
        print(
            f"{Path(f).name}: account {st.account}, {len(st.transactions)} txns, "
            f"opening {st.opening_balance}, closing {st.closing_balance}, continuity {status}"
        )
    all_txns = combine(sts)
    total = sum(len(s.transactions) for s in sts)
    print(
        f"combined: {len(all_txns)} unique transactions ({total - len(all_txns)} duplicates "
        f"from overlapping files removed)"
    )
    return 1 if gaps else 0


def cmd_match(a) -> int:
    recs = pd.read_csv(a.receivables, dtype=str)
    recs["amount"] = recs["amount"].map(Decimal)
    recs["due_date"] = pd.to_datetime(recs["due_date"]).dt.date
    txns = combine([_load_statement(Path(f), None) for f in a.statements])
    out = match_payments(txns, recs, as_of=a.as_of)
    m = out["matches"]
    print(
        f"matched {len(m)} of {len(recs)} receivables: "
        + ", ".join(f"{k}={v}" for k, v in m["rule"].value_counts().items())
    )
    _show("partial payments", m[m["rule"] == "PARTIAL"])
    _show(
        "unmatched credits (money we can't explain)",
        out["unmatched_credits"][["posted_on", "amount", "description", "reference"]],
    )
    _show("overdue receivables (money we expected)", out["overdue"])
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        for name, df in out.items():
            df.to_csv(Path(a.out) / f"{name}.csv", index=False)
        print(f"\nreports written to {a.out}/")
    needs_attention = (
        (m["rule"] == "PARTIAL").any()
        or not out["unmatched_credits"].empty
        or not out["overdue"].empty
    )
    return 1 if needs_attention else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="recon", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare")
    c.add_argument("file_a")
    c.add_argument("file_b")
    c.add_argument("--key", required=True, action="append")
    c.add_argument("--value", action="append", default=[], help="column:tolerance")
    c.add_argument("--delimiter", default=";")
    c.add_argument("--decimal-a", default=".")
    c.add_argument("--decimal-b", default=".")

    h = sub.add_parser("health")
    h.add_argument("files", nargs="+")
    h.add_argument("--delimiter", default=";")

    s = sub.add_parser("statements")
    s.add_argument("files", nargs="+")
    s.add_argument("--account")

    m = sub.add_parser("match")
    m.add_argument("--receivables", required=True)
    m.add_argument("--statements", nargs="+", required=True)
    m.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    m.add_argument("--out")

    a = p.parse_args(argv)
    return {
        "compare": cmd_compare,
        "health": cmd_health,
        "statements": cmd_statements,
        "match": cmd_match,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
