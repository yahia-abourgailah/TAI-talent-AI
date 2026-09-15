# Week 3 decisions and requests

**Owner:** Person A. Fill each answer in as it is given, with who gave it and when. No candidate values here.

## BR-704: re-scoring after the 3 August 2026 fixes

Checked on the real workbook (SHA-256 `c9c7607e8f77…`), criteria 2026-08-04 replayed as of 14 September 2026. Full counts: [RESCORE_REPORT.md](RESCORE_REPORT.md).

| Check | Result |
|---|---|
| Rows the old country matching could have disqualified (a country inside a location word) | 9. All 9 stored results already match today's rules |
| Rows the old manager matching could have disqualified (a managerial word inside a title word) | 20. 18 already match; the other 2 are sheet rows 5355 and 5384, kept disqualified by the recorded rulings |
| Rows added before 3 August | 3,928. 3,926 match today's rules, 2 are the ruled rows above |
| Pending tier or score changes | **0** |

Nothing needs re-scoring: the stored scores were produced after the fixes. BR-704 still asks the criteria owner to accept the report before it is closed.

| Decision | Asked | Answer | Given by, date |
|---|---|---|---|
| Accept RESCORE_REPORT.md: no candidate is re-scored, and rows 5355 and 5384 stay disqualified under their rulings | Karim | | |

## BR-301: own-staff check

The criteria exclude a candidate whose current employer or title names The Address. Checked on the real workbook, with no roster yet:

| Check | Result |
|---|---|
| Rows excluded as own staff | 38, every one with stored score 0 |
| Employer exactly "The Address" (OPN-03) | Sheet rows 53 and 1478, both stored 0 / P4 |
| Compared with active employees | Not yet: waiting for the HRIS roster |

When the roster arrives, run the check below. It reports which of the 38 are confirmed active employees (same phone or email), name-only matches for a person to decide, and any active employee the text did not exclude. It changes nothing.

```bash
.venv/bin/python -m staff.check --roster "$TALENT_EMPLOYEES_PATH" --out ~/TAI-data/own-staff \
  --report-copy docs/migration/OWN_STAFF_REPORT.md
```

| Decision | Asked | Answer | Given by, date |
|---|---|---|---|
| OPN-03: keep or reverse the exclusion of sheet rows 53 and 1478 (employer exactly "The Address") | Karim | | |
| An active employee the text did not exclude: exclude as own staff, or keep? (only if the roster check finds one) | Karim | | |

## Requests waiting on another team

| Request | From | Sent | Received |
|---|---|---|---|
| Employee roster as .xlsx or .csv, placed outside the repository. Columns: Employee ID, Full Name, Mobile, Email, Status (active or not). Other header names are fine; tell us and `src/staff/roster.py` is updated | HRIS / data owner | | |
| Rejection reasons (BR-404): the list TA uses, one reason per line, with which are shown to the candidate | TA | | |
| Stage list and allowed moves between stages | TA | | |
