# The same person, found twice (BR-203)

Counts and candidate ids only; no names, numbers or addresses. Nothing here is joined: every match waits for a person (BR-206).

| What | Count |
|---|---:|
| Candidates with a phone, email, profile or name | 5,140 |
| Pairs found | 1,681 |
| &nbsp;&nbsp;strong (same phone, email or profile) | 1,338 |
| &nbsp;&nbsp;possible (same name only) | 343 |
| Groups | 1,178 |
| Records inside a group | 2,646 |

## What pairs were matched on

| Evidence | Pairs |
|---|---:|
| email | 5 |
| name_arabic | 216 |
| name_latin | 386 |
| phone | 7 |
| profile_url | 1,332 |

## Group sizes

| Records in the group | Groups |
|---:|---:|
| 2 | 1,057 |
| 3 | 48 |
| 4 | 34 |
| 5 | 17 |
| 6 | 10 |
| 7 | 4 |
| 8 | 2 |
| 9 | 3 |
| 10 | 1 |
| 12 | 1 |
| 14 | 1 |

## Before anything is joined

A person checks a sample by hand (`python -m candidates.duplicates sample`), and the wrong-join rate must be under 0.5% before any joining starts.
