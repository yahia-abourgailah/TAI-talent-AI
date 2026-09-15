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

The criteria exclude a candidate whose current employer or title names The Address. Checked on the real workbook against the company CRM employee list (`GET /api/learning-integration/get_users`, the endpoint the L&D reports use), 15 September 2026. Full counts: [OWN_STAFF_REPORT.md](OWN_STAFF_REPORT.md).

| Check | Result |
|---|---|
| Rows excluded as own staff | 38, every one with stored score 0 |
| Employer exactly "The Address" (OPN-03) | Sheet rows 53 and 1478, both stored 0 / P4 |
| Active employees in the CRM | 1,473, each with an employee code and a full name |
| Mobile or email in the CRM answer | **None.** The endpoint returns name, employee code, company, department, sector, job level and status only |
| Confirmed matches (same mobile or email) | 0: impossible without contact details |
| Exact full-name matches | 0. CRM names are 3–5 word legal names; 1,073 of the 1,252 named candidates have a 2-word name |
| First and last word of the name match (counted, not used) | 78 candidates, 2 of them among the 38. Common names make this too loose to act on without a person checking each one |

So the own-staff exclusions cannot yet be confirmed or corrected from employee data. The check changes nothing; run it again once the CRM returns contact details:

```bash
.venv/bin/python -m staff.check --crm --out ~/TAI-data/own-staff \
  --report-copy docs/migration/OWN_STAFF_REPORT.md
```

| Decision | Asked | Answer | Given by, date |
|---|---|---|---|
| OPN-03: keep or reverse the exclusion of sheet rows 53 and 1478 (employer exactly "The Address") | Karim | | |
| Until the CRM returns contact details: keep the 38 text exclusions as they are, or have TA check first-and-last-name matches by hand? | Karim | | |
| An active employee the text did not exclude: exclude as own staff, or keep? (only if a later check finds one) | Karim | | |

## Requests waiting on another team

| Request | From | Sent | Received |
|---|---|---|---|
| Add work mobile and work email to `get_users` (read with the existing service key), so a candidate can be confirmed as an employee | CRM team | | |
| Only if the CRM cannot: an employee roster file with Employee ID, Full Name, Mobile, Email, Status, placed outside the repository (`--roster`) | HRIS / data owner | | |
| Rejection reasons (BR-404): the list TA uses, one reason per line, with which are shown to the candidate | TA | | |
| Stage list and allowed moves between stages | TA | | |
