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

## OPN-03: own staff

The criteria exclude a candidate whose current employer or title names The Address: 38 rows, every one stored score 0. A past role at The Address does not exclude anyone; it is flagged as a rehire candidate. Checking the exclusions against employee data is postponed.

| Decision | Asked | Answer | Given by, date |
|---|---|---|---|
| OPN-03: keep or reverse the exclusion of sheet rows 53 and 1478 (employer exactly "The Address") | Karim | | |

## Requests waiting on another team

| Request | From | Sent | Received |
|---|---|---|---|
| Rejection reasons (BR-404): the list TA uses, one reason per line, with which are shown to the candidate | TA | | |
| Stage list and allowed moves between stages | TA | | |
