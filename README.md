# Reconciliation Toolkit

[![CI](https://github.com/tecozu/reconciliation-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/tecozu/reconciliation-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**The daily reconciliation work of a credit or receivables operation, packaged as a tested
library and CLI.** It comes from years of doing this by hand with SQL and notebooks.

| Command | Question it answers |
|---|---|
| `recon match` | Which receivables were paid, partly paid or not paid, and which money arrived that we can't explain? (cash application) |
| `recon statements` | Did we receive every transaction? Are the bank statements complete? |
| `recon compare` | Does the partner's file agree with our database, row by row and in total? |
| `recon health` | Is this export damaged (encoding, HTML entities, broken rows) before we load it? |

Each command exits with code `1` when something needs a human, so it can gate a pipeline.

## Cash application

```text
$ recon match --receivables receivables.csv --statements july_part1.ofx july_part2.ofx --as-of 2026-07-31
matched 23 of 30 receivables: REFERENCE=20, EXACT=2, PARTIAL=1

partial payments (1)
receivable_id  rule     expected   paid    difference  due_date    posted_on
R0006          PARTIAL  696.47     417.88  -278.59     2026-07-10  2026-07-10

unmatched credits (money we can't explain) (4)
posted_on   amount   description                         reference
2026-07-14  500.00   PIX RECEBIDO DEBTOR 22              PIX000019
2026-07-14  2673.93  PIX RECEBIDO DEBTOR 10 REF NN10010  NN10010     <- overpaid by 50.00
2026-07-14  500.00   PIX RECEBIDO DEBTOR 21              PIX000018
2026-07-22  1234.56  TED RECEBIDA ORIGEM DESCONHECIDA    TED998877

overdue receivables (money we expected) (5)
R0004 · R0008 (unpaid) · R0010 (overpaid, needs review) · R0021 · R0022 (indistinguishable twins)
```

Matching rules run in order of confidence, and each payment and receivable is used at most once:

1. **REFERENCE**: the bank-slip number or PIX id appears in the transaction and the amount matches.
2. **EXACT**: same amount within ±5 days of the due date, **and unique in both directions**.
   Two receivables of R$ 500 due the same day, paid by two R$ 500 transfers without a
   reference, are left for a human. Guessing would attach the payment to the wrong debtor.
3. **PARTIAL**: reference found, amount lower than expected.

## Bank statements

- **OFX** (the standard export of every bank, parsed without dependencies) and
  **Brazilian-style CSV** (`;`, decimal comma, running balance) map to one schema.
- Every transaction gets a **deterministic id**, so overlapping exports are deduplicated:
  the example's two OFX files share 3 days, and 5 duplicates are removed. Two genuinely
  identical payments on the same day are kept, because a per-day sequence is part of the id.
- **Balance continuity**: opening balance + Σ amounts must equal the closing balance,
  which proves no transaction is missing. OFX files carry no opening balance, so the check
  reports `n/a` for them instead of deriving one, which would make it pass by construction.

## Dataset comparison

```text
$ recon compare portfolio_file.csv portfolio_db.csv --key id --value present_value:0.01 --decimal-a ,
summary: {'matched': 38, 'only_in_portfolio_file': 1, 'only_in_portfolio_db': 2, 'value_breaks': 1, 'duplicate_keys': 0}
```

Keys are normalized before matching (`'000017'`, `17`, `'17.0'` and `' 17 '` are the same
key), which is the most common reason two systems "disagree". The comparison also reports
value breaks above a tolerance, duplicated keys on either side, and totals per column.

## File health

```text
$ recon health damaged_export.csv
ISSUES damaged_export.csv: 7 lines, 4 columns
  - mojibake: 1 line(s): 3                 'JoÃ£o' = UTF-8 read as Latin-1
  - replacement_char: 1 line(s): 4         bytes lost in a bad conversion
  - field_count_mismatch: 2 line(s): 5, 6
  - html_entity: 1 line(s): 5              'O&#39;Connor'
```

Line 5 shows why this check exists. The `;` that ends `&#39;` is also the CSV delimiter,
so a web-exported apostrophe silently shifts every following column.

## Install & develop

```bash
pip install git+https://github.com/tecozu/reconciliation-toolkit
recon --help

# development
pip install -e ".[dev]"
python examples/make_examples.py   # regenerate the synthetic examples (every scenario planted on purpose)
pytest && ruff check .
```

All example data is synthetic.

## License

MIT
