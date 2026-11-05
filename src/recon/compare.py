"""Dataset-vs-dataset reconciliation (e.g. a partner's file vs. your database).

Answers the questions an operations team asks every day:
which keys exist only on one side, which matched keys disagree on a value
(beyond a tolerance), and do the totals tie out?
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def normalize_key(s: pd.Series) -> pd.Series:
    """'000123', 123, '123.0', ' 123 ' all become '123' — the usual reason keys fail to match."""
    out = s.astype("string").str.strip()
    out = out.str.replace(r"\.0+$", "", regex=True)
    numeric = out.str.fullmatch(r"\d+").fillna(False)
    out = out.where(~numeric, out.str.lstrip("0").replace("", "0"))
    return out.str.upper()


def to_number(s: pd.Series, decimal: str = ".") -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    text = s.astype("string").str.strip()
    if decimal == ",":
        text = text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


@dataclass
class ReconResult:
    left_name: str
    right_name: str
    matched: pd.DataFrame
    only_left: pd.DataFrame
    only_right: pd.DataFrame
    breaks: pd.DataFrame  # one row per (key, column) that differs beyond tolerance
    duplicates: pd.DataFrame
    totals: pd.DataFrame

    @property
    def ok(self) -> bool:
        return self.only_left.empty and self.only_right.empty and self.breaks.empty

    def summary(self) -> dict:
        return {
            "matched": len(self.matched),
            f"only_in_{self.left_name}": len(self.only_left),
            f"only_in_{self.right_name}": len(self.only_right),
            "value_breaks": len(self.breaks),
            "duplicate_keys": len(self.duplicates),
        }


def reconcile(
    left: pd.DataFrame,
    right: pd.DataFrame,
    key: str | list[str],
    compare: dict[str, float] | None = None,
    right_key: str | list[str] | None = None,
    names: tuple[str, str] = ("left", "right"),
    decimal: str = ".",
) -> ReconResult:
    """Full outer reconciliation on `key`; `compare` maps column -> absolute tolerance."""
    lk = [key] if isinstance(key, str) else list(key)
    rk = lk if right_key is None else ([right_key] if isinstance(right_key, str) else right_key)
    compare = compare or {}
    ln, rn = names

    l, r = left.copy(), right.copy()  # noqa: E741
    l["_key"] = normalize_key(l[lk].astype(str).agg("|".join, axis=1))
    r["_key"] = normalize_key(r[rk].astype(str).agg("|".join, axis=1))
    for col in compare:
        l[col] = to_number(l[col], decimal)
        r[col] = to_number(r[col], decimal)

    dups = pd.concat(
        [
            l.loc[l["_key"].duplicated(keep=False), ["_key"]].assign(side=ln),
            r.loc[r["_key"].duplicated(keep=False), ["_key"]].assign(side=rn),
        ]
    ).drop_duplicates()

    merged = l.drop_duplicates("_key").merge(
        r.drop_duplicates("_key"),
        on="_key",
        how="outer",
        suffixes=(f"_{ln}", f"_{rn}"),
        indicator=True,
    )
    matched = merged[merged["_merge"] == "both"].drop(columns="_merge")
    only_left = l[l["_key"].isin(merged.loc[merged["_merge"] == "left_only", "_key"])]
    only_right = r[r["_key"].isin(merged.loc[merged["_merge"] == "right_only", "_key"])]

    break_rows = []
    for col, tol in compare.items():
        a, b = matched[f"{col}_{ln}"], matched[f"{col}_{rn}"]
        diff = a - b
        bad = (diff.abs() > tol) | (a.isna() != b.isna())
        for k, va, vb, d in zip(matched.loc[bad, "_key"], a[bad], b[bad], diff[bad], strict=True):
            break_rows.append({"key": k, "column": col, ln: va, rn: vb, "difference": d})
    breaks = pd.DataFrame(break_rows, columns=["key", "column", ln, rn, "difference"])

    totals = pd.DataFrame(
        [
            {"column": c, ln: l[c].sum(), rn: r[c].sum(), "difference": l[c].sum() - r[c].sum()}
            for c in compare
        ]
        + [{"column": "row_count", ln: len(l), rn: len(r), "difference": len(l) - len(r)}]
    )
    return ReconResult(ln, rn, matched, only_left, only_right, breaks, dups, totals)
