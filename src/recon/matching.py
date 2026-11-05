"""Cash application: match bank credits to the receivables they pay.

Rules run in order of confidence; each payment and each receivable is used at most once:

1. REFERENCE  - the receivable's reference (bank slip number, PIX txid) appears in the
                transaction reference or description, and the amount is within tolerance;
2. EXACT      - same amount (± tolerance) and posted within `window_days` of the due date,
                and the match is unique in both directions (no guessing between twins);
3. PARTIAL    - reference found but the amount is lower: a partial payment.

Everything else is reported for a human: unmatched credits (money we can't explain)
and open receivables past due (money we expected and didn't get).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd


def match_payments(
    credits: pd.DataFrame,
    receivables: pd.DataFrame,
    as_of: date,
    tolerance: Decimal = Decimal("0.01"),
    window_days: int = 5,
) -> dict[str, pd.DataFrame]:
    """credits: txn_id, posted_on, amount, description, reference.
    receivables: receivable_id, reference, due_date, amount."""
    credits = credits[credits["amount"] > 0].copy()
    used_txn: set[str] = set()
    used_rec: set[str] = set()
    matches: list[dict] = []

    def take(rec, txn, rule: str) -> None:
        used_txn.add(txn.txn_id)
        used_rec.add(rec.receivable_id)
        matches.append(
            {
                "receivable_id": rec.receivable_id,
                "txn_id": txn.txn_id,
                "rule": rule,
                "expected": rec.amount,
                "paid": txn.amount,
                "difference": txn.amount - rec.amount,
                "due_date": rec.due_date,
                "posted_on": txn.posted_on,
            }
        )

    # 1 + 3: reference-based
    for rec in receivables.itertuples():
        ref = str(rec.reference).strip()
        if not ref:
            continue
        for txn in credits.itertuples():
            if txn.txn_id in used_txn:
                continue
            haystack = f"{txn.reference} {txn.description}"
            if ref in haystack:
                if abs(txn.amount - rec.amount) <= tolerance:
                    take(rec, txn, "REFERENCE")
                elif txn.amount < rec.amount:
                    take(rec, txn, "PARTIAL")
                break

    # 2: unique amount + date window
    window = timedelta(days=window_days)
    open_recs = [r for r in receivables.itertuples() if r.receivable_id not in used_rec]
    free_txns = [t for t in credits.itertuples() if t.txn_id not in used_txn]

    def close(r, t) -> bool:
        return abs(t.amount - r.amount) <= tolerance and abs(t.posted_on - r.due_date) <= window

    for rec in open_recs:
        cands = [t for t in free_txns if t.txn_id not in used_txn and close(rec, t)]
        if len(cands) != 1:
            continue
        txn = cands[0]
        rivals = [r for r in open_recs if r.receivable_id not in used_rec and close(r, txn)]
        if len(rivals) == 1:  # unique both ways, otherwise leave it for a human
            take(rec, txn, "EXACT")

    m = pd.DataFrame(
        matches,
        columns=[
            "receivable_id",
            "txn_id",
            "rule",
            "expected",
            "paid",
            "difference",
            "due_date",
            "posted_on",
        ],
    )
    unmatched_credits = credits[~credits["txn_id"].isin(used_txn)]
    open_rec = receivables[~receivables["receivable_id"].isin(used_rec)].copy()
    overdue = open_rec[open_rec["due_date"] < as_of].assign(
        days_overdue=lambda d: [(as_of - x).days for x in d["due_date"]]
    )
    return {
        "matches": m,
        "unmatched_credits": unmatched_credits,
        "overdue": overdue,
        "not_yet_due": open_rec[open_rec["due_date"] >= as_of],
    }
