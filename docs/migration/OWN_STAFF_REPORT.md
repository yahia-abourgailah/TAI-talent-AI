# Own-staff check against the employee roster (BR-301)

Workbook SHA-256 `c9c7607e8f77c1dfc344850c2f731b922e5b7b737fb9aec4fbe213ae39efd8ae`. Counts and sheet rows only; no candidate or employee values. Nothing was excluded or changed by this check.

Roster from the CRM: 1,473 employees, 1,473 active, 0 without an employee id (not used). Active employees with a usable mobile: 0, email: 0, full name: 1,473.

## Candidates the criteria excluded as own staff

| Rows | Stored score 0 | Confirmed employee | Name matches only | No match |
|---:|---:|---:|---:|---:|
| 38 | 38 | 0 | 0 | 38 |

## Candidates the criteria did not exclude

| Rows | Confirmed employee | Name matches only | No match |
|---:|---:|---:|---:|
| 5,102 | 0 | 0 | 5,102 |

**Active employees the criteria did not exclude:** none. Each needs a ruling; none is excluded automatically.

**Name-only matches to check by hand (BR-206):** none.

**Employer exactly "The Address" (OPN-03):** sheet row 53 (none), sheet row 1478 (none).
