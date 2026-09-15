# TAI_Master import reconciliation

Workbook SHA-256 `c9c7607e8f77c1dfc344850c2f731b922e5b7b737fb9aec4fbe213ae39efd8ae`. Counts only; no candidate values.

**Counts match: yes.** Every check below compares the database, and the raw captures read back from storage, with the workbook's cells.

The 200-row sample (seed 20260914) is written outside the repository for a person to check by hand. This report does not record that check; whoever does it records it in docs/migration/WEEK2_DECISIONS.md.

## Rows, raw captures and evaluations

| Check | Sheet | Database | Match |
|---|---:|---:|---|
| Non-blank rows vs candidates | 5,140 | 5,140 | yes |
| Workbook file kept as a raw capture | 1 | 1 | yes |
| Rows whose raw capture, read back, holds the row's cells | 5,140 | 5,140 | yes |
| Whole-number scores from 0 to 100 vs evaluations under 2026-08-04 | 3,940 | 3,940 | yes |
| Evaluations equal to the sheet (score, tier, recommendation, call priority, Signals and Flags text) | 3,940 | 3,940 | yes |

Score cells in the sheet: 3,940. A score that is not a whole number from 0 to 100 is listed as unresolved by the import, not imported.

## Replay

Criteria 2026-08-04 replayed as of 2026-09-14: 3,938 of 3,940 stored scores match exactly, 2 differences are ruled, 0 unexplained. Parity: yes. The replay reads the same stored scores the database holds: yes.

## Fields

| Column | Field | Filled in sheet | "No value" markers | Stored values | Not recorded | Rows equal to sheet | Match |
|---|---|---:|---:|---:|---:|---:|---|
| Name | `full_name` | 3,940 | 0 | 3,940 | 1,200 | 5,140 | yes |
| Age | `age` | 3,933 | 1,090 | 2,843 | 2,297 | 5,140 | yes |
| Title | `current_title` | 3,901 | 0 | 3,901 | 1,239 | 5,140 | yes |
| Employer | `current_employer` | 3,900 | 0 | 3,900 | 1,240 | 5,140 | yes |
| Location | `location` | 3,887 | 0 | 3,887 | 1,253 | 5,140 | yes |
| Phone Number | `phone` | 238 | 0 | 238 | 4,902 | 5,140 | yes |
| Email | `email` | 49 | 0 | 49 | 5,091 | 5,140 | yes |
| Profile URL | `profile_url` | 5,082 | 0 | 5,082 | 58 | 5,140 | yes |
| Education | `education` | 3,424 | 0 | 3,424 | 1,716 | 5,140 | yes |
| Years Exp | `years_experience` | 3,913 | 0 | 3,913 | 1,227 | 5,140 | yes |
| Last Active | `source_last_active` | 3,933 | 406 | 3,527 | 1,613 | 5,140 | yes |
| Platform | `source_platform` | 3,939 | 0 | 3,939 | 1,201 | 5,140 | yes |
| Date Added | `date_added` | 3,939 | 0 | 3,939 | 1,201 | 5,140 | yes |

## Kept in the raw capture only

These columns have no structured home yet. Every cell of them is in each row's raw capture, which is checked above.

| Column | Filled in sheet | Waiting on |
|---|---:|---|
| WhatsApp Invite Sent | 0 | Q-07 |
| Stage | 15 | Q-01 and the week 3 stage list |
| Phone Screen Result | 4 | Q-01 |
| Interview Scheduled | 2 | Q-21 |
| HR Interview Date & Time | 2 | Q-21 |
| HR Interview WA Sent | 2 | OPN-01 |
| HR Interview Result | 0 | the week 3 pipeline tables |
| HR Supervisor Result | 0 | Q-16 |
| IQ Sent | 3 | Q-06 |
| IQ Completed | 3 | Q-06 |
| IQ Score | 3 | Q-06 |
| IQ Band | 3 | Q-06 |
| Technical Interview | 0 | the week 3 pipeline tables |
| Attempt # | 0 | Q-03 |
| Sales Team | 0 | Q-04 |
| Job Offer Sent | 0 | the week 3 pipeline tables |
| On Floor Date | 0 | Q-02 |
| Hired ✓ | 0 | Q-02 |
| Hired Date | 0 | Q-02 |
| Rejection Stage | 0 | the week 3 pipeline tables |
| Rejection Reason | 0 | the TA rejection reason list |
| HR Feedback | 56 | Q-15 |
| WA First Contact Sent | 508 | Q-07 |
| WA Reply | 82 | Q-08 |
| WA Reply Date | 82 | Q-08 |
| Contact Decision | 82 | Q-09 |
| Assigned Recruiter | 596 | Q-18 |
| Notion Page ID | 15 | Q-05 |
