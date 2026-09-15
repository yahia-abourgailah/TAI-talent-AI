# Week 2 decisions and requests

**Owner:** Person A. Fill each answer in as it is given, with who gave it and when. No candidate values here.

## A5: decisions before the real import

| ID | Question | Asked | Answer | Given by, date |
|---|---|---|---|---|
| OPN-10 | 1,200 rows have no score, and 1,199 of them share a profile URL with a scored row (COLUMN_MAP F-03). Score them, keep them as archive, or link them to the scored record as a duplicate capture? | Karim | | |
| OPN-11 | 4,898 rows have neither a phone nor an email. Are they still in the process (a pipeline application), or a candidate record only? | TA | | |

## A6: nothing waiting on another team in week 3

| Request | From | Sent | Received |
|---|---|---|---|
| Read access to employee data (own-staff check) | HRIS / data owner | | |
| Stage list | TA | | |
| Allowed moves between stages | TA | | |
| Rejection reasons (BR-404) | TA | | |
| OPN-03: confirm or reverse the 2 rows with employer exactly "The Address" being excluded as own staff | Karim | | |

## A1: parity check in CI

| Step | Who | Done |
|---|---|---|
| Register a self-hosted GitHub runner labelled `talent` on a machine that can read the workbook | Infrastructure | |
| Add the repository secret `TALENT_MASTER_PATH` (path to the workbook on that machine) | Repository admin | |
| Set the repository variable `PARITY_RUNNER_READY=true` | Repository admin | |
| Open a pull request that touches `src/scoring/` and see **Golden replay parity** green | Person A | |

## Import runs on the real workbook, 15 September 2026

After the week 2 review fixes (migration 0004), on a fresh database. Workbook SHA-256 `c9c7607e8f77…`. Counts only; the full report is [RECONCILIATION_REPORT.md](RECONCILIATION_REPORT.md).

| Check | Result |
|---|---|
| Import stopped part-way (killed after 25 seconds) | Nothing kept. The next import recorded the stopped attempt as a failed run and finished the same job on attempt 2 |
| First complete import | 5,140 rows read: 5,140 candidates, 5,140 row captures plus the workbook file, 66,820 fields, 3,940 evaluations under 2026-08-04; 1,200 rows with no stored score |
| Second import of the same file | Nothing written: every table's fingerprint identical, timestamps included |
| Import of a re-saved copy (same cells, different file) | No new row captures, one new capture for the new file; candidates, fields and evaluations unchanged |
| Unresolved in every run | 1: sheet row 1496, Platform "Test", waiting on Q-13 (not archived) |
| Reconciliation | Every check matches: rows, the workbook capture, each raw capture read back cell by cell, every field, every evaluation |
| Independent check, outside the tools | Every current stored field and every evaluation equals its workbook cell |
| Replay of 2026-08-04 | 3,938 of 3,940 exact, 2 ruled, 0 unexplained: parity reached |
| Run records | No candidate values; the only error text is the stopped-worker message |

The tool's comparison of the 200-row sample found 0 disagreements. That is not the sign-off: a person still checks the sample by hand (B5 below).

## B5: 200-row sample sign-off

The tool's comparison is not the sign-off. A person opens the sample CSV (kept outside the repository) beside the workbook and checks it by hand.

| Workbook SHA-256 (first 12) | Sample seed | Rows checked | Disagreements | Checked by, date |
|---|---|---|---|---|
| c9c7607e8f77 | 20260914 | | | |
