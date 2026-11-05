"""Generate the synthetic example files (deterministic). All names and numbers are fake.

    python examples/make_examples.py

Scenarios planted on purpose, so the README output and the tests are predictable:
  receivables vs. bank : paid with reference, paid without reference, partial payment,
                         overpayment, unknown credit, unpaid & overdue, two indistinguishable
                         twins, not yet due, and a transaction repeated in two overlapping
                         OFX exports
  file vs. database    : leading zeros, missing rows on each side, a value break
  file health          : mojibake, replacement char, HTML entity, broken row
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "data"
AS_OF = date(2026, 7, 31)


def money_br(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ofx(path: Path, account: str, txns: list[dict], closing: float) -> None:
    body = "\n".join(
        f"<STMTTRN>\n<TRNTYPE>{'CREDIT' if t['amount'] > 0 else 'DEBIT'}\n"
        f"<DTPOSTED>{t['date']:%Y%m%d}120000\n<TRNAMT>{t['amount']:.2f}\n"
        f"<FITID>{t['fitid']}\n<CHECKNUM>{t['ref']}\n<MEMO>{t['memo']}\n</STMTTRN>"
        for t in txns
    )
    path.write_text(
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\n\n<OFX>\n<BANKMSGSRSV1>"
        f"<STMTTRNRS><STMTRS><CURDEF>BRL\n<BANKACCTFROM><BANKID>999<ACCTID>{account}"
        f"</BANKACCTFROM>\n<BANKTRANLIST>\n{body}\n</BANKTRANLIST>\n"
        f"<LEDGERBAL><BALAMT>{closing:.2f}<DTASOF>20260731</LEDGERBAL>\n"
        "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n",
        encoding="latin-1",
    )


def main() -> None:
    rng = random.Random(3)
    OUT.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ receivables
    recs = []
    for i in range(1, 31):
        due = date(2026, 7, 1) + timedelta(days=rng.randint(0, 35))
        recs.append(
            {
                "receivable_id": f"R{i:04d}",
                "reference": f"NN{10000 + i}",
                "debtor": f"Debtor {i}",
                "due_date": due,
                "amount": round(rng.uniform(150, 3000), 2),
            }
        )
    for i in (3, 5, 7, 9, 11, 13):  # scenario rows: due well before the as-of date
        recs[i]["due_date"] = date(2026, 7, 5 + i)
    recs[20]["due_date"] = recs[21]["due_date"] = date(2026, 7, 14)  # twins: same due, same
    recs[20]["amount"] = recs[21]["amount"] = 500.00  # amount, paid without reference
    with open(OUT / "receivables.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0]))
        w.writeheader()
        w.writerows(recs)

    # ------------------------------------------------------------------ bank activity
    txns, seq = [], 0

    def pay(rec, amount, ref=True, delay=0):
        nonlocal seq
        seq += 1
        d = rec["due_date"] + timedelta(days=delay)
        txns.append(
            {
                "date": d,
                "amount": amount,
                "fitid": f"F{seq:05d}",
                "ref": rec["reference"] if ref else f"PIX{seq:06d}",
                "memo": f"PIX RECEBIDO {rec['debtor'].upper()}"
                + (f" REF {rec['reference']}" if ref else ""),
            }
        )

    for i, r in enumerate(recs):
        if r["due_date"] > AS_OF:
            continue  # not yet due
        if i in (3, 7):
            continue  # unpaid -> overdue
        if i == 5:
            pay(r, round(r["amount"] * 0.6, 2))  # partial
        elif i == 9:
            pay(r, round(r["amount"] + 50, 2))  # overpaid -> needs a human
        elif i in (11, 13, 20, 21):
            pay(r, r["amount"], ref=False, delay=rng.randint(0, 2))  # no reference
        else:
            pay(r, r["amount"], delay=rng.randint(-1, 3))
    seq += 1
    txns.append(
        {
            "date": date(2026, 7, 22),
            "amount": 1234.56,
            "fitid": f"F{seq:05d}",
            "ref": "TED998877",
            "memo": "TED RECEBIDA ORIGEM DESCONHECIDA",
        }
    )
    seq += 1
    txns.append(
        {
            "date": date(2026, 7, 10),
            "amount": -89.90,
            "fitid": f"F{seq:05d}",
            "ref": "TARIFA",
            "memo": "TARIFA PACOTE SERVICOS",
        }
    )
    txns.sort(key=lambda t: t["date"])

    opening = 10_000.00
    cut = date(2026, 7, 16)
    first = [t for t in txns if t["date"] <= cut]
    second = [t for t in txns if t["date"] >= cut - timedelta(days=2)]  # 3 days overlap
    bal_first = opening + sum(t["amount"] for t in first)
    ofx(OUT / "statement_2026-07_part1.ofx", "12345-6", first, bal_first)
    ofx(
        OUT / "statement_2026-07_part2.ofx",
        "12345-6",
        second,
        opening + sum(t["amount"] for t in txns),
    )

    # A second account exported as the typical Brazilian CSV (with a running balance)
    rows, bal = [["Data", "Descricao", "Documento", "Valor", "Saldo"]], 2500.0
    rows.append(["01/07/2026", "SALDO ANTERIOR", "", "", money_br(bal)])
    for d, desc, doc, val in [
        (3, "PIX RECEBIDO CLIENTE A", "E1", 320.0),
        (8, "PAGTO FORNECEDOR", "B7", -1200.0),
        (15, "PIX RECEBIDO CLIENTE B", "E2", 880.45),
    ]:
        bal += val
        rows.append([f"{d:02d}/07/2026", desc, doc, money_br(val), money_br(bal)])
    with open(OUT / "statement_account_b.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerows(rows)

    # ------------------------------------------------------------------ file vs database
    portfolio = [
        {"id": f"{i:06d}", "present_value": round(rng.uniform(100, 5000), 2)} for i in range(1, 41)
    ]
    file_rows = [dict(p) for p in portfolio if p["id"] != "000017"]  # missing in the file
    db_rows = [
        {"id": str(int(p["id"])), "present_value": p["present_value"]}  # zeros dropped
        for p in portfolio
        if p["id"] != "000033"
    ]
    db_rows.append({"id": "41", "present_value": 777.0})  # only in db
    file_rows[4]["present_value"] = round(file_rows[4]["present_value"] + 12.34, 2)  # break
    for name, data, dec in (
        ("portfolio_file.csv", file_rows, ","),
        ("portfolio_db.csv", db_rows, "."),
    ):
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "present_value"])
            w.writerows([r["id"], f"{r['present_value']:.2f}".replace(".", dec)] for r in data)

    # ------------------------------------------------------------------ damaged file
    damaged = [
        "id;name;city;amount",
        "1;Ana Souza;Sao Paulo;100,00",
        "2;JoÃ£o Pereira;BrasÃ­lia;250,00",  # UTF-8 read as Latin-1
        "3;Maria � Lima;Recife;80,00",  # lost bytes
        "4;O&#39;Connor Ltda;Curitiba;99,90",  # HTML entity
        "5;Carlos;Belo Horizonte",  # missing field
        "6;Beatriz;Salvador;45,00",
    ]
    (OUT / "damaged_export.csv").write_text("\n".join(damaged) + "\n", encoding="utf-8")
    print(f"examples written to {OUT}")


if __name__ == "__main__":
    main()
