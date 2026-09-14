# TAI_Master column map

**Status:** Draft for Karim's review, 14 September 2026
**Requirement:** BR-701 (week 1, Person A)
**Rulings from:** Karim AlAkkad, HRIS Lead, criteria owner

## Purpose

This document says where each of the 47 columns in `TAI_Master.xlsx` goes in the new database. It is the input for weeks 2 and 3, when the tables are built and the 5,140 rows are moved. Where a column has no clear home, or its meaning is unclear, it is listed as a question for Karim. Nothing marked as a question is built until it is ruled on. All counts come from the workbook on 14 September 2026. Only non-blank rows are counted, which gives 5,140 rows. The ~617 rows that hold only formatting are ignored. This file holds no candidate values. The only values shown are the category labels in section 4.

## How to read the main table

- **Filled**: non-blank cells, and the percentage of 5,140.
- **Kind**: identity, contact PII, profile, evaluation output, pipeline event, outreach, or external reference.
- **Target**: `schema.entity.field`. Every entity and field name is **proposed**. Week 2 builds the real tables.
- **Migration rule**: how the value is moved. The short codes below apply.
- **Req**: the BRD requirement the rule serves.

### Rules that apply to every column

| Code | Meaning |
|---|---|
| RAW | Every sheet row is first stored whole, once, as one `raw.capture` (source `tai_master`, with the workbook hash on the migration job run). Raw is never updated. All 47 cells stay there, byte for byte, even when a column has no structured home. |
| M-PROV | Provenance on `core.candidate_field`: `source = migrated from TAI_Master`, `source_ref = raw capture id + exact column header`, `verification_state = unverified`, `verified_at` empty, `verified_by` empty (BR-201, BRD 6.2). |
| HIST-EVAL | Stored on `core.evaluation` as the historic evaluation under criteria v1, with `origin = migrated_historic`. The value is copied as it is. It is **never recomputed on import**. It is the golden-replay baseline (BR-702). |
| NR | An empty pipeline cell becomes an explicit `not_recorded` state. It is never read as "new", "not contacted" or "not hired", and it is never back-filled (BR-703). |
| UNK | An actor, timestamp or previous stage the sheet does not hold is stored as `unknown`. It is never invented or guessed from nearby columns. |
| INF? | The value may have been derived or inferred, not stated by the candidate. It is stored with `inference = unknown`, so it cannot pass as verified. |

The golden replay reads inputs from the raw capture, not from `core`. So cleaning a value in `core` (for example, treating "?" as not recorded) cannot change replay parity.

## Proposed entities

| Entity (proposed) | Holds |
|---|---|
| `raw.capture` | Already exists (migration 0001). One append-only capture per sheet row. |
| `core.candidate` | One record per imported row. Lifecycle state, archive reason and actor (BR-205). |
| `core.candidate_field` | One row per attribute: `field`, `value`, `source`, `source_ref`, `verified_at`, `verified_by`, `verification_state`, `inference`. |
| `core.candidate_identifier` | External identifiers: profile URL, Notion page. Used for matching in P5. |
| `core.candidate_source` | Channel, sourcing recruiter and team, first-captured date (BR-108). |
| `core.criteria_version` | v1 = ratified 4 August 2026 rules. |
| `core.evaluation` | `score`, `tier`, `recommendation`, `signals`, `flags`, `call_priority`, `criteria_version_id`, `origin`, `evaluated_at`. |
| `pipeline.requisition` | Job openings. The sheet has none (see Q-17). |
| `pipeline.application` | Candidate on a requisition: `current_stage`, `stage_recording_state`, `owner_recruiter_id`. |
| `pipeline.stage_event` | `from_stage`, `to_stage`, `actor`, `occurred_at`, `reason_code`, `origin`. |
| `pipeline.interview` | `kind`, `scheduled_at`, `outcome`, `notes`. |
| `pipeline.assessment` | `kind`, `sent`, `completed`, `score`, `band`. |
| `pipeline.offer` | `sent_at`, `start_date`. |
| `pipeline.contact_attempt` | `channel`, `direction`, `purpose`, `occurred_at`, `actor`, `content`, `recruiter_decision`. |
| `audit.job_run` | The migration run: workbook hash, rows read, rows written, rows skipped, with reasons (BR-604). |

## 1. Main table: all 47 columns, in workbook order

| # | Column | Filled | Kind | Target (proposed) | Migration rule | Req |
|---|---|---|---|---|---|---|
| 1 | Name | 3,940 (76.7%) | identity | `core.candidate_field` `full_name` | M-PROV. Exact spelling kept; never translated or normalised. Not used to merge at import. Blank on all 1,200 unscored rows: left empty. | BR-701, BR-201, BR-203, BR-206, BR-309 |
| 2 | Age | 3,933 (76.5%) | profile | `core.candidate_field` `age` | M-PROV + INF?. 1,090 cells hold "?", which is not a value, so they become not recorded. That leaves 2,843 real values (55.3%). The sheet cannot show which ages were stated and which were inferred from graduation year. | BR-201, CR-07, OPN-02 |
| 3 | Title | 3,901 (75.9%) | profile | `core.candidate_field` `current_title` | M-PROV. Original text. Scoring input (management gate, routing, own-staff check). | BR-201, BR-301 |
| 4 | Employer | 3,900 (75.9%) | profile | `core.candidate_field` `current_employer` | M-PROV. Original text. Scoring input (own-staff check). | BR-201, BR-301, OPN-03 |
| 5 | Location | 3,887 (75.6%) | profile | `core.candidate_field` `location` | M-PROV. Original text, no geocoding at import. Scoring input (Greater Cairo gate). | BR-201, BR-301, BR-704 |
| 6 | Phone Number | 238 (4.6%) | contact PII | `core.candidate_field` `phone` | M-PROV: "migrated from TAI_Master, unverified". Original text kept. A normalised copy is used for matching only. Unverified, so it cannot be used for contact. No consent is recorded. | BR-201, BR-202, BR-109, CR-02, OPN-01, OPN-11 |
| 7 | Email | 49 (1.0%) | contact PII | `core.candidate_field` `email` | M-PROV: "migrated from TAI_Master, unverified". Same rules as Phone Number. | BR-201, BR-202, BR-109, CR-02, OPN-01, OPN-11 |
| 8 | Profile URL | 5,082 (98.9%) | external reference | `core.candidate_identifier` `kind = profile_url` | M-PROV. Stored as given, plus a normalised form for matching. Shared URLs are flagged for P5 review, never merged at import (see Findings F-03). | BR-106, BR-203, BR-204, BR-206, OPN-10 |
| 9 | WhatsApp Invite Sent | 0 (0%) | outreach | `pipeline.contact_attempt` `purpose = invite` (home unconfirmed) | Empty in every row. Nothing created. Outreach history is NR. | BR-505, BR-703, Q-07 |
| 10 | Education | 3,424 (66.6%) | profile | `core.candidate_field` `education` | M-PROV. Original text. Scoring input, and the likely basis of any age inference. | BR-201, BR-301, OPN-02 |
| 11 | Years Exp | 3,913 (76.1%) | profile | `core.candidate_field` `years_experience` | M-PROV + INF?. 2,448 are numbers and 1,465 are digits stored as text. Cast only a plain number. May have been computed from job dates. Scoring input (eleven-or-more-years gate). | BR-201, BR-301, Q-19 |
| 12 | Last Active | 3,933 (76.5%) | profile | `core.candidate_field` `source_last_active` | M-PROV. Original text only. 406 cells are "?", so not recorded. About 15 formats, some relative with no anchor date. Never converted to a date. | BR-201, Q-19 |
| 13 | Platform | 3,939 (76.6%) | profile | `core.candidate_source` `channel`, `sourcing_recruiter` | M-PROV. Original label kept. Split into channel and the recruiter in brackets only after Q-13. The single "Test" row is archived with a reason once ruled. | BR-108, BR-205, BR-602, Q-13 |
| 14 | Date Added | 3,939 (76.6%) | profile | `core.candidate_source` `first_captured_at` | M-PROV. Date only; time and timezone UNK. This is not `raw.capture.received_at`, which is the migration time. | BR-701, BR-704, Q-14 |
| 15 | Score | 3,940 (76.7%) | evaluation output | `core.evaluation` `score` | HIST-EVAL. Integer as stored. On the 1,200 blank rows: no evaluation row, and the candidate state is "never scored", not zero. | BR-701, BR-702, BR-301, BR-307, CR-04, NFR-08, OPN-10 |
| 16 | Tier | 3,940 (76.7%) | evaluation output | `core.evaluation` `tier` | HIST-EVAL. Only P1 to P4 are present; there are no T tiers. | BR-701, BR-702, BR-301, Q-11 |
| 17 | Recommendation | 3,940 (76.7%) | evaluation output | `core.evaluation` `recommendation` | HIST-EVAL. 7 labels. P4 holds 4 different labels that encode a gate or routing outcome. Stays in the replay baseline unless Q-11 says otherwise. | BR-701, BR-702, BR-302, Q-11 |
| 18 | Signals (Reasons to call) | 3,701 (72.0%) | evaluation output | `core.evaluation` `signals` | HIST-EVAL. Original text plus a split list. On a scored row a blank means an empty list, not NR (confirm in Q-12). | BR-701, BR-702, BR-307, Q-12 |
| 19 | Flags (reasons of disqualification) | 536 (10.4%) | evaluation output | `core.evaluation` `flags` | HIST-EVAL. Same blank rule as Signals. | BR-701, BR-702, BR-302, Q-12 |
| 20 | Call Priority | 3,748 (72.9%) | evaluation output (origin unconfirmed) | `core.evaluation` `call_priority` | HIST-EVAL. Original label kept, plus the label without its leading symbol. 192 scored rows are blank. Does not follow Tier (Q-10). | BR-701, BR-702, Q-10 |
| 21 | Stage | 15 (0.3%) | pipeline event | `pipeline.stage_event` `to_stage`; `pipeline.application` `current_stage` | One stage event per filled cell: `from_stage`, `actor` and `occurred_at` are UNK; `origin = migrated`. "New" maps to new (14) and "HR Interview" to HR interview (1). No steps in between are invented. On the 5,125 blank rows, `stage_recording_state` is NR. | BR-402, BR-403, BR-703, Q-01, Q-17 |
| 22 | Phone Screen Result | 4 (0.1%) | pipeline event | `pipeline.interview` `kind = phone_screen`, `outcome` | Outcome "accepted" (case folded, original kept). Date and interviewer UNK. No stage event unless Q-01 allows it. Blank is NR. | BR-402, BR-703, Q-01 |
| 23 | Interview Scheduled | 2 (<0.1%) | pipeline event | `pipeline.interview` `scheduled`, `scheduled_at` | Mixed: one yes-flag and one date-time stored as text. Original kept. `scheduled_at` only after Q-21. Blank is NR. | BR-507, BR-703, Q-21 |
| 24 | HR Interview Date & Time | 2 (<0.1%) | pipeline event | `pipeline.interview` `kind = hr`, `scheduled_at` | Two different text formats. Original kept; parsed only after Q-21. Blank is NR. | BR-402, BR-703, Q-21 |
| 25 | HR Interview WA Sent | 2 (<0.1%) | outreach | `pipeline.contact_attempt` `channel = whatsapp`, `purpose = interview_invite` | Check mark means sent. `occurred_at` and `actor` UNK. No consent record. Blank is NR. | BR-505, CR-02, BR-703, OPN-01 |
| 26 | HR Interview Result | 0 (0%) | pipeline event | `pipeline.interview` `kind = hr`, `outcome` | Empty. NR. | BR-402, BR-703 |
| 27 | HR Supervisor Result | 0 (0%) | pipeline event | No BRD stage. Proposed `pipeline.interview` `kind = hr_supervisor`, `outcome` | Empty. NR. | BR-402, BR-703, Q-16 |
| 28 | IQ Sent | 3 (0.1%) | pipeline event | `pipeline.assessment` `kind = aptitude`, `sent` | "Yes" means sent; `sent_at` UNK. Blank is NR. | BR-402, BR-703, Q-06 |
| 29 | IQ Completed | 3 (0.1%) | pipeline event | `pipeline.assessment` `completed` | "Yes" means completed; `completed_at` UNK. Blank is NR. | BR-402, BR-703, Q-06 |
| 30 | IQ Score | 3 (0.1%) | pipeline event (assessment result) | `pipeline.assessment` `score` | Digits stored as text; cast only a plain number. Scale UNK. Not a criteria score. | BR-703, Q-06 |
| 31 | IQ Band | 3 (0.1%) | pipeline event (assessment result) | `pipeline.assessment` `band` | Label copied. Its "BORDERLINE" value is not the BR-310 verdict unless Q-06 says so. | BR-310, BR-703, Q-06 |
| 32 | Technical Interview | 0 (0%) | pipeline event | `pipeline.interview` `kind = technical` | Empty. NR. | BR-402, BR-703 |
| 33 | Attempt # | 0 (0%) | unclear | **No home** | Empty. Nothing built until ruled. | Q-03 |
| 34 | Sales Team | 0 (0%) | unclear | **No home**. Candidate: `pipeline.application` `team` or `pipeline.requisition` | Empty. Nothing built until ruled. | BR-108, BR-401, Q-04 |
| 35 | Job Offer Sent | 0 (0%) | pipeline event | `pipeline.offer` `sent_at`; stage event to offer | Empty. NR. | BR-402, BR-703 |
| 36 | On Floor Date | 0 (0%) | pipeline event (after hire) | **No BRD stage**. Proposed `pipeline.offer` `start_date` | Empty. NR. | BR-703, Q-02 |
| 37 | Hired ✓ | 0 (0%) | pipeline event (outcome) | `pipeline.stage_event` `to_stage = hired` | Empty. NR. No hires exist to migrate. | BR-402, BR-703, Q-02 |
| 38 | Hired Date | 0 (0%) | pipeline event (outcome) | `pipeline.stage_event` `occurred_at` of the hired event | Empty. NR. | BR-403, BR-703, Q-02 |
| 39 | Rejection Stage | 0 (0%) | pipeline event (outcome) | `pipeline.stage_event` `to_stage = rejected`, `from_stage` | Empty. NR. | BR-404, BR-703 |
| 40 | Rejection Reason | 0 (0%) | pipeline event (outcome) | `pipeline.stage_event` `reason_code` (from the TA list) | Empty. NR. There is no free-text reason to map. | BR-404, BR-703 |
| 41 | HR Feedback | 56 (1.1%) | pipeline event (free-text note) | **No confirmed home**. Proposed `pipeline.interview` `notes` or an application note | Text kept as written. Author and date UNK. Treated as PII-bearing free text. 47 of the 56 rows have no Stage. | BR-406, BR-703, Q-15 |
| 42 | WA First Contact Sent | 508 (9.9%) | outreach | `pipeline.contact_attempt` `channel = whatsapp`, `direction = outbound`, `purpose = first_contact` | Only the 153 timestamp cells become contact attempts (`occurred_at` = value, `actor` UNK). The 355 link cells are not contact events; they stay in raw only until Q-07. Blank is NR, never "not contacted". | BR-505, CR-02, BR-703, OPN-01, Q-07 |
| 43 | WA Reply | 82 (1.6%) | outreach | `pipeline.contact_attempt` `direction = inbound`, `content` | Free text. Whether the content or only the fact of a reply is kept depends on Q-08. No "replied" stage event unless Q-08 allows it. | BR-505, BR-703, Q-08 |
| 44 | WA Reply Date | 82 (1.6%) | outreach | `pipeline.contact_attempt` (inbound) `occurred_at` | 50 date-time and 32 date-only values. Date-only keeps date precision; no time is invented. | BR-403, BR-703 |
| 45 | Contact Decision | 82 (1.6%) | pipeline event (meaning unconfirmed) | Proposed `pipeline.contact_attempt` `recruiter_decision` | Copied as a label. Not a rejection, not a stage event, and does not archive the candidate. `actor` UNK. | BR-404, BR-405, Q-09 |
| 46 | Assigned Recruiter | 596 (11.6%) | pipeline event (ownership) | `pipeline.application` `owner_recruiter_id` | Mapped to a staff login identity (Q-18). `assigned_at` and `assigned_by` UNK. Blank means owner not recorded (NR), not "unassigned". This is not the sourcing recruiter, which comes from Platform. | BR-701, BR-108, BR-408, NFR-09, Q-18 |
| 47 | Notion Page ID | 15 (0.3%) | external reference | `core.candidate_identifier` `kind = notion_page` | Kept as a reference only. Notion is not called during migration. Filled on exactly the same 15 rows as Stage. | BR-705, CR-01, Q-05 |

**Columns mapped: 47 of 47.** Columns with no confirmed home: 33 Attempt #, 34 Sales Team, 36 On Floor Date, 41 HR Feedback, 45 Contact Decision, and the 355 link cells in 42.

## 2. Columns with no clear home: questions for Karim

Each question needs a short ruling. "Our proposal" is what we would do. It is **not applied** until ruled (BRD 13: nothing is resolved by assumption). If a question is not Karim's call, please say who owns it.

### Existing open decisions this map depends on

| ID | Question, as it touches the columns | Our proposal | Blocks |
|---|---|---|---|
| OPN-02 | Age (col 2) cannot show which values were stated and which were inferred from graduation year, and 1,090 cells are "?". Is sheet Age allowed as a gate input, and may inferred ages be stored at all? **Answered 14 Sep 2026:** age limits and guessing age from graduation year are allowed. Still needed here: record for each age whether it was stated or guessed. | Store every age as unverified, with inference unknown. "?" is not recorded. The replay uses the raw capture as the scorer saw it. | Criteria v1 activation; week 2 replay sign-off  |
| OPN-03 | Employer (col 4): two rows list exactly "The Address" and were excluded as own staff. Confirm or reverse. | No proposal. This is Karim's ruling. | P2 migration sign-off |
| OPN-10 | 1,200 rows have no score. 1,199 of them share a profile URL with a scored row (F-03). Score them, migrate them as archive, or link them to the scored record as a duplicate capture? | Import as separate candidates, flagged as possible duplicates of the scored record, for P5 review. Do not score on import. | P2 migration sign-off; row-count reconciliation |
| OPN-11 | 4,898 rows have neither a phone nor an email. Do they become pipeline applications or archive? | No application for rows with no contact channel and no pipeline data; candidate record only. | Whether `pipeline.application` is created for about 4,900 rows |
| OPN-12 | The backup workbooks with pre-re-scale scores are missing. Without them, Score (col 15) cannot be checked against its pre-4-August value. | Replay against the sheet as it stands today. | Golden-replay triage (week 2) |
| OPN-01 | Columns 25, 42 and 43 record WhatsApp messages, but no consent record exists for anyone. | Import them as history with consent "none recorded". They never enable contact. | P9 only; not the migration |

### New questions

| ID | Question | Our proposal | Blocks |
|---|---|---|---|
| Q-01 | **Stage vs the per-stage result columns.** 14 of the 15 Stage values say New, but all 15 rows also have a first-contact value. 3 rows have phone-screen or IQ results with no Stage at all. Which is the source of truth for stage events? | Only Stage (col 21) creates stage events. Columns 22 to 31 create interview and assessment records, not events. | Week 2 pipeline import; funnel baseline |
| Q-02 | **Hired ✓, Hired Date, On Floor Date** (all empty). Is Hired ✓ the hired event and Hired Date its time? Is On Floor Date a start date after hire (outside the BRD stage list)? | Yes, yes. On Floor Date goes on the offer as `start_date`, with no new stage. | Week 3 offer and hired schema |
| Q-03 | **Attempt #** (empty). Was it the contact attempt number, a re-application count, or an interview or test retry? | Drop it. Contact attempts are counted from `pipeline.contact_attempt` rows. | Week 3 contact log design |
| Q-04 | **Sales Team** (empty). Is it the sourcing team (BR-108), the team the candidate is hired into, or the requisition's team? | Belongs on the requisition, not the candidate. | Week 3 requisition schema |
| Q-05 | **Notion Page ID** (15 rows, the same rows as Stage). What does Notion hold for these candidates? Does it hold names, phones or CVs? If so, it is a third-party store under CR-01 and must be on the BR-705 switch-off list. | Keep the ID as an external reference only. Add Notion to the decommission list. | CR-01 evidence; BR-705 order |
| Q-06 | **IQ columns** (28 to 31). Is the "IQ" test the BRD "aptitude test" stage? What scale is IQ Score on? Is IQ Band "BORDERLINE" a separate idea from the BR-310 borderline verdict? | Aptitude test = IQ test. The band is a test result, not a criteria verdict. Stored on `pipeline.assessment`. | Week 3 stage list; week 7 review queue |
| Q-07 | **WhatsApp Invite Sent vs WA First Contact Sent.** Are they the same event? 355 of the 508 first-contact cells hold a profile link (343 Wuzzuf, 12 LinkedIn), not a time. Does a link mean "contacted", and if so, when? | Only timestamp cells become contact attempts. Link cells stay in raw only. The invite column is merged into first contact. | Contact volume baseline (F-01); week 3 contact log |
| Q-08 | **WA Reply content.** Should reply text be kept in the contact log, or only the fact and date of a reply (less PII)? Does a reply create a "replied" stage event? | Keep only the fact and date. The text stays in raw only. No stage event. | Week 3 contact log; PDPL minimisation |
| Q-09 | **Contact Decision** (Call 40, Archive 42). What does "Archive" mean: a rejection, a hold, or "do not pursue now"? 19 P1 candidates are marked Archive. | A recruiter decision on the contact record. It is not a rejection, because BR-404 needs a stage and a listed reason and BR-405 needs human confirmation. | Week 3 rejection handling |
| Q-10 | **Call Priority vs Tier.** Call Priority does not follow Tier: 13 P4 rows say "Call Today", 14 P1 rows say "Low Priority" or "Skip", and 9 of the 38 current-employee rows carry a call-type priority. Does the scorer produce it, or was it edited by hand? | If the scorer produces it, include it in the replay baseline. If it was edited by hand, migrate it as a historic label outside the replay. | Week 2 replay scope |
| Q-11 | **Recommendation and Track B.** 111 rows are "PROFILE ONLY - Track B target" at P4, and no T1 to T4 tier exists anywhere. Is Track B the tenured track? Should these rows carry a tenured tier? Is Recommendation part of parity? | Track B = tenured track. Historic P4 is kept as it is. Recommendation is included in the replay. | Week 2 replay; BR-301 "both tracks" |
| Q-12 | **Flags and blank outputs.** The header says "reasons of disqualification", but 243 flagged rows are P1 to P3. Are flags warnings or disqualifiers? On a scored row, does a blank Signals or Flags cell mean "none"? | Flags are warnings unless the Recommendation says disqualified. A blank on a scored row means an empty list. | Week 2 replay comparison rules |
| Q-13 | **Platform codes.** What do "W" (2,725), "LR" (894) and "T" (1) mean? Is "Test" (1 row, scored P1) test data to archive? Should "LinkedIn Recruiter (name)" be split into channel plus sourcing recruiter? | W = Wuzzuf, LR = LinkedIn Recruiter, T = TikTok. Split channel and recruiter. Archive "Test" with a reason. | BR-108 sourcing label; BR-602 source report |
| Q-14 | **Date Added.** Is it the date the row was scored? It is the only date that can pick the BR-704 population (scored before 3 August). | Use it as the capture date only. BR-704 population is ruled separately. | Week 3 BR-704 re-score; `evaluated_at` of historic evaluations |
| Q-15 | **HR Feedback** (56 free-text cells, 47 with no Stage). Is it interview notes, a reason for overriding a verdict (BR-406), or a general note? Who wrote it? | An unattributed application note. Not a labelled override. | Week 3 notes; BR-406 labels |
| Q-16 | **HR Supervisor Result** (empty). Is it a second HR interview? The BRD stage list has none. Add a stage, or record it as an outcome inside HR interview? | An interview kind inside the HR interview stage. No new stage. | Week 3 stage list (with TA) |
| Q-17 | **Requisition for migrated applications.** The sheet has no job column, but BR-401 needs a requisition. Should there be one "historic, migrated" requisition per track, or no applications at all for historic rows? | One historic requisition per track, created only for rows that have pipeline data. | Week 2 pipeline import |
| Q-18 | **Assigned Recruiter.** The column holds one distinct value across 596 rows. Which staff login is it? Is it the owner or the sourcing recruiter? 367 first-contact rows have no recruiter. | Owner. The staff mapping is confirmed by Karim. Contact actor stays unknown. | BR-408 scoping on migrated rows |
| Q-19 | **Years Exp and Last Active.** Did the scraper compute Years Exp from job dates (so it is derived)? Last Active holds relative values with no anchor date. | Both stored as reported, with inference unknown. Last Active is never turned into a date. | Week 2 field provenance |
| Q-20 | **Repeated profile URLs among scored rows.** 122 scored rows fall into 57 groups that share a URL, and 14 of those groups hold more than one tier. Each row migrates as its own candidate, flagged for P5. Confirm that none is merged or dropped before P5. | Confirm. | Row-count reconciliation at the week 2 gate |
| Q-21 | **Interview times.** One Interview Scheduled value is a date-time, and HR Interview Date & Time uses two formats. Is the timezone Cairo local time? Does Interview Scheduled refer to the HR interview? | Cairo local time, HR interview. Otherwise keep the text only. | Week 3 interview import (2 rows) |

**Open questions: 27 in total.** That is 6 existing OPN items (OPN-01, OPN-02, OPN-03, OPN-10, OPN-11, OPN-12) and 21 new questions (Q-01 to Q-21).

## 3. Categorical value inventory

Only category columns are listed. Recruiter names in Platform are replaced with letters, except Karim AlAkkad.

### Evaluation outputs

**Tier** (3,940 filled)

| Value | Rows |
|---|---|
| P1 | 326 |
| P2 | 1,136 |
| P3 | 2,097 |
| P4 | 381 |

No T1 to T4 values exist. Ruling needed: Q-11.

**Recommendation** (3,940 filled). The cross-check against Tier is exact.

| Value | Rows | Tier |
|---|---|---|
| Strong Match - Call Today | 326 | all P1 |
| Good Match - Call This Week | 1,136 | all P2 |
| Possible Match - Follow Up Later | 2,097 | all P3 |
| Poor Match - Archive | 142 | all P4 |
| PROFILE ONLY - Track B target | 111 | all P4 |
| DO NOT CALL - disqualified | 90 | all P4 |
| CURRENT EMPLOYEE - do not source | 38 | all P4 |

Maps cleanly to evaluation output. P4 needs Q-11.

**Call Priority** (3,748 filled). Each stored value starts with a coloured symbol, left out here.

| Value | Rows |
|---|---|
| Call Today | 319 |
| Call This Week | 827 |
| Follow Up | 525 |
| Low Priority | 2,032 |
| Skip | 45 |

Does not follow Tier. Ruling needed: Q-10.

### Source

**Platform** (3,939 filled, 15 distinct values)

| Value | Rows |
|---|---|
| W | 2,725 |
| LR | 894 |
| Wuzzuf | 111 |
| LinkedIn Recruiter (Recruiter B, full name) | 54 |
| LinkedIn Recruiter (Recruiter C) | 53 |
| LinkedIn Recruiter (Recruiter D) | 33 |
| LinkedIn Recruiter (Unknown) | 26 |
| LinkedIn Recruiter (Karim AlAkkad) | 21 |
| LinkedIn Recruiter (Karim) | 9 |
| LinkedIn Recruiter (Recruiter B, first name only) | 5 |
| Facebook (G1-JobsEgypt) | 3 |
| LinkedIn Recruiter (TAI) | 2 |
| Facebook (G3-JobsCairo) | 1 |
| T | 1 |
| Test | 1 |

Mixes channel and sourcing recruiter. Two people appear to be written two ways each (Karim; Recruiter B). Confirm in Q-13. Ruling needed: Q-13.

### Pipeline columns

| Column | Distinct values (rows) | BRD stage | Maps cleanly? |
|---|---|---|---|
| Stage | New (14), HR Interview (1) | new; HR interview | Yes as labels. But 14 "New" rows also have first contact (Q-01) |
| Phone Screen Result | Accepted (3), accepted (1) | phone screen | Result yes; stage event needs Q-01. Case differs |
| Interview Scheduled | one yes-flag (1), one date-time text (1); date not shown | HR interview (assumed) | No. Mixed types. Q-21 |
| HR Interview WA Sent | ✓ check mark (2) | none (outreach) | Contact log, not a stage |
| IQ Sent | Yes (3) | aptitude test | Needs Q-06 |
| IQ Completed | Yes (3) | aptitude test | Needs Q-06 |
| IQ Band | High (2), BORDERLINE (1) | aptitude test result | Needs Q-06 |
| Contact Decision | Archive (42), Call (40) | none | No. Q-09 |
| WhatsApp Invite Sent | empty | contacted? | Q-07 |
| HR Interview Result | empty | HR interview | Yes, nothing to map |
| HR Supervisor Result | empty | none | No. Q-16 |
| Technical Interview | empty | technical interview | Yes, nothing to map |
| Attempt # | empty | none | No. Q-03 |
| Sales Team | empty | none | No. Q-04 |
| Job Offer Sent | empty | offer | Yes, nothing to map |
| Hired ✓ | empty | hired | Yes, nothing to map (Q-02) |
| Rejection Stage | empty | rejected (from stage) | Yes, nothing to map |
| Rejection Reason | empty | rejected (reason) | Needs the TA reason list (BR-404) |

### BRD stage list against the sheet

| BRD stage | Sheet evidence | Rows with a value | Ruling |
|---|---|---|---|
| new | Stage = New | 14 | Q-01 |
| contacted | WA First Contact Sent (timestamps only); WhatsApp Invite Sent | 153; 0 | Q-07 |
| replied | WA Reply, WA Reply Date | 82 | Q-08 |
| phone screen | Phone Screen Result | 4 | Q-01 |
| HR interview | Stage = HR Interview; HR Interview Date & Time; Interview Scheduled; HR Interview Result; HR Supervisor Result | 1; 2; 2; 0; 0 | Q-16, Q-21 |
| aptitude test | IQ Sent, IQ Completed, IQ Score, IQ Band | 3 | Q-06 |
| technical interview | Technical Interview | 0 | none |
| offer | Job Offer Sent | 0 | none |
| hired | Hired ✓, Hired Date (On Floor Date after) | 0 | Q-02 |
| rejected | Rejection Stage, Rejection Reason | 0 | Contact Decision "Archive" is not mapped here (Q-09) |

## 4. Findings against the BRD numbers

Raw non-blank counts match every figure in BRD section 2.2. Several of those figures do not mean what the table says.

| BRD field | BRD says | Found (non-blank) | Match? |
|---|---|---|---|
| Score and tier | 3,940 (76.7%) | 3,940 (76.7%) | Yes |
| Profile URL | 5,082 (98.9%) | 5,082 (98.9%) | Yes |
| Phone number | 238 (4.6%) | 238 (4.6%) | Yes |
| Email | 49 (1.0%) | 49 (1.0%) | Yes |
| Assigned recruiter | 596 (11.6%) | 596 (11.6%) | Yes |
| First contact sent | 508 (9.9%) | 508 (9.9%) | Count yes, meaning no (F-01) |
| Reply received | 82 (1.6%) | 82 (1.6%) | Yes |
| HR feedback | 56 (1.1%) | 56 (1.1%) | Yes |
| Stage | 15 (0.3%) | 15 (0.3%) | Count yes, meaning no (F-02) |
| Hire and rejection outcome | 0 | 0 | Yes |
| Never scored | 1,200 | 1,200 | Count yes, meaning no (F-03) |

- **F-01. First contact is 153, not 508.** Only 153 of the 508 cells (3.0% of 5,140) hold a timestamp. The other 355 hold a profile link (343 Wuzzuf, 12 LinkedIn). 335 of those links are on rows with no phone number. The BRD's "outreach volume" figure is overstated by 355.
- **F-02. Only 1 row has a stage past "New".** Of the 15 Stage values, 14 are New and 1 is HR Interview. The OBJ-01 baseline of 0.3% counts candidates marked New. Also, 3 rows have phone-screen or IQ results but no Stage.
- **F-03. 5,140 rows is not 5,140 people.** Profile URL has 3,818 distinct values after normalising (lower case, no trailing slash, no query string). 2,485 rows sit in 1,221 groups that share a URL, with up to 6 rows per URL. 1,199 of the 1,200 "never scored" rows share a URL with a scored row. So OPN-10's premise that 1,200 candidates were never scored is very likely wrong: most of them are repeat captures. Among scored rows, 122 rows share a URL (57 groups), and 58 scored rows have no URL. Phones: 234 distinct in 238 cells. Emails: 44 distinct in 49 cells.
- **F-04. The replay can compare only 3,940 rows.** OBJ-02 and BR-702 ask for parity "on all 5,140 rows". Only 3,940 rows have a score, tier, flags or signals to compare against.
- **F-05. Age is filled in 2,843 rows, not 3,933.** 1,090 Age cells hold "?". Last Active has 406 "?" cells. Neither is in the BRD table, but both matter for OPN-02 and BR-201.
- **F-06. P1 count differs from BRD 2.3.** The BRD says the 4 August re-scale cut P1 "from 775 to 331". The sheet holds 326 P1 rows today. This map does not explain the difference of 5. It goes to the week 2 replay triage.
- **F-07. The own-staff count matches, but those rows still carry call priorities.** 38 rows say "CURRENT EMPLOYEE - do not source", matching the BRD's 38. Of these, 9 carry Call Today, Call This Week or Follow Up (Q-10).
- **F-08. There is no tenured-track data.** BR-301 and the glossary describe T1 to T4 tiers. The sheet has none. Tenured targets appear as P4 "PROFILE ONLY - Track B target" (111 rows) (Q-11).
- **F-09. Flags are not only disqualifiers.** 243 of the 536 flagged rows are P1 to P3 (Q-12).
- **F-10. Contact columns disagree with each other.** All 82 Contact Decision values sit on reply rows. 17 of the 82 replies have no first contact recorded. 2 Contact Decisions are on unscored rows. 367 first-contact rows have no assigned recruiter, and 455 assigned rows have no first contact.
- **F-11. Test data and gaps in scored rows.** One scored row has Platform "Test" (tier P1). One scored row has no Platform and no Date Added. 7 scored rows have no Age. 192 scored rows have no Call Priority.
- **F-12. Notion is linked to exactly the staged rows.** Notion Page ID is filled on the same 15 rows as Stage, so the 15 staged candidates probably also exist in Notion (Q-05, CR-01).
