# The same person, found twice (BR-203)

Counts and candidate ids only; no names, numbers or addresses. Nothing here is joined: every match waits for a person (BR-206).

| What | Count |
|---|---:|
| Candidates with a phone, email, profile or name | 5,140 |
| Pairs found (same phone, email or profile) | 1,352 |
| Pairs sharing only a name, left alone | 329 |
| Groups | 1,241 |
| Records inside a group | 2,525 |

## What pairs were matched on

| Evidence | Pairs |
|---|---:|
| email | 5 |
| name_arabic | 60 |
| name_latin | 59 |
| phone | 21 |
| profile_url | 1,348 |

## Group sizes

| Records in the group | Groups |
|---:|---:|
| 2 | 1,217 |
| 3 | 9 |
| 4 | 13 |
| 6 | 2 |

## Before anything is joined

A person checks a sample by hand (`python -m candidates.duplicates sample`), and the wrong-join rate must be under 0.5% before any joining starts.
