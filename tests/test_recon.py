from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from recon.cli import main
from recon.compare import normalize_key, reconcile
from recon.health import check_file
from recon.matching import match_payments
from recon.statements import check_balance_continuity, combine, parse_csv_br, parse_ofx

DATA = Path(__file__).parents[1] / "examples" / "data"
AS_OF = date(2026, 7, 31)


# --------------------------------------------------------------------------- compare
def test_normalize_key():
    s = pd.Series(["000123", 123, "123.0", " 123 ", "ab-1"])
    assert normalize_key(s).tolist() == ["123", "123", "123", "123", "AB-1"]


def test_reconcile_finds_every_planted_difference():
    left = pd.read_csv(DATA / "portfolio_file.csv", sep=";", dtype=str)
    right = pd.read_csv(DATA / "portfolio_db.csv", sep=";", dtype=str)
    left["present_value"] = left["present_value"].str.replace(",", ".").astype(float)
    res = reconcile(left, right, key="id", compare={"present_value": 0.01}, names=("file", "db"))

    assert len(res.matched) == 38  # leading zeros didn't prevent matching
    assert res.only_left["id"].tolist() == ["000033"]
    assert set(res.only_right["id"]) == {"17", "41"}
    assert len(res.breaks) == 1 and res.breaks["difference"].iloc[0] == pytest.approx(12.34)
    assert not res.ok


def test_reconcile_reports_duplicate_keys():
    left = pd.DataFrame({"k": ["1", "1", "2"], "v": [1, 1, 2]})
    right = pd.DataFrame({"k": ["1", "2"], "v": [1, 2]})
    res = reconcile(left, right, key="k", compare={"v": 0})
    assert res.duplicates["_key"].tolist() == ["1"]


# --------------------------------------------------------------------------- health
def test_health_detects_damage():
    rep = check_file(DATA / "damaged_export.csv")
    assert rep.issues == {
        "mojibake": [3],
        "replacement_char": [4],
        "html_entity": [5],
        # line 5 too: the ';' that ends '&#39;' is also the CSV delimiter, so the entity
        # silently shifts every following column
        "field_count_mismatch": [5, 6],
    }


def test_clean_file_is_ok(tmp_path):
    f = tmp_path / "ok.csv"
    f.write_text("a;b\n1;São Paulo\n2;Ação\n", encoding="utf-8")
    assert check_file(f).ok


# --------------------------------------------------------------------------- statements
def test_ofx_statements_tie_out_and_overlap_is_deduplicated():
    p1 = parse_ofx(DATA / "statement_2026-07_part1.ofx")
    p2 = parse_ofx(DATA / "statement_2026-07_part2.ofx")
    assert p1.account == "12345-6"
    assert check_balance_continuity(p1) is None  # OFX has no opening balance: not faked
    assert p2.closing_balance is not None
    combined = combine([p1, p2])
    assert len(combined) < len(p1.transactions) + len(p2.transactions)  # overlap removed
    assert combined["txn_id"].is_unique


def test_csv_statement_balance_continuity():
    st = parse_csv_br(DATA / "statement_account_b.csv", account="B")
    assert st.opening_balance == Decimal("2500.00")
    assert len(st.transactions) == 3
    assert check_balance_continuity(st) == 0


def test_balance_gap_is_detected():
    st = parse_csv_br(DATA / "statement_account_b.csv", account="B")
    st.transactions = st.transactions.iloc[1:]  # a transaction went missing
    assert check_balance_continuity(st) == Decimal("-320.00")


def test_identical_same_day_transactions_are_kept():
    st = parse_csv_br(DATA / "statement_account_b.csv", account="B")
    twin = pd.concat([st.transactions, st.transactions.iloc[[0]]])
    from recon.statements import _with_ids

    rebuilt = _with_ids(twin.drop(columns="txn_id"))
    assert rebuilt["txn_id"].is_unique  # two equal PIX on the same day are two payments


# --------------------------------------------------------------------------- matching
@pytest.fixture(scope="module")
def match_result():
    recs = pd.read_csv(DATA / "receivables.csv", dtype=str)
    recs["amount"] = recs["amount"].map(Decimal)
    recs["due_date"] = pd.to_datetime(recs["due_date"]).dt.date
    txns = combine(
        [
            parse_ofx(DATA / "statement_2026-07_part1.ofx"),
            parse_ofx(DATA / "statement_2026-07_part2.ofx"),
        ]
    )
    return recs, match_payments(txns, recs, as_of=AS_OF)


def test_matching_rules(match_result):
    _, out = match_result
    rule = dict(zip(out["matches"]["receivable_id"], out["matches"]["rule"], strict=True))
    assert rule["R0006"] == "PARTIAL"
    assert rule["R0012"] == "EXACT" and rule["R0014"] == "EXACT"  # paid without reference
    assert "R0021" not in rule and "R0022" not in rule  # twins: never guess
    assert all(r == "REFERENCE" for k, r in rule.items() if k not in {"R0006", "R0012", "R0014"})


def test_exceptions_are_reported(match_result):
    _, out = match_result
    unexplained = out["unmatched_credits"]["amount"].tolist()
    assert Decimal("1234.56") in unexplained  # unknown TED
    overdue = set(out["overdue"]["receivable_id"])
    assert {"R0004", "R0008", "R0010", "R0021", "R0022"} <= overdue  # unpaid, overpaid, twins
    assert all(d > AS_OF for d in out["not_yet_due"]["due_date"])


def test_each_payment_used_once(match_result):
    _, out = match_result
    assert out["matches"]["txn_id"].is_unique
    assert out["matches"]["receivable_id"].is_unique


# --------------------------------------------------------------------------- cli
def test_cli_exit_codes(tmp_path, capsys):
    assert main(["health", str(DATA / "damaged_export.csv")]) == 1
    assert (
        main(
            [
                "statements",
                str(DATA / "statement_2026-07_part1.ofx"),
                str(DATA / "statement_2026-07_part2.ofx"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "compare",
                str(DATA / "portfolio_file.csv"),
                str(DATA / "portfolio_db.csv"),
                "--key",
                "id",
                "--value",
                "present_value:0.01",
                "--decimal-a",
                ",",
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "match",
                "--receivables",
                str(DATA / "receivables.csv"),
                "--statements",
                str(DATA / "statement_2026-07_part1.ofx"),
                str(DATA / "statement_2026-07_part2.ofx"),
                "--as-of",
                "2026-07-31",
                "--out",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert (tmp_path / "matches.csv").exists()
