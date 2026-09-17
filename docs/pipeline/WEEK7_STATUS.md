# Week 7, Person A: status (2026-09-17)

| Job | Rule | State | Waiting on |
|---|---|---|---|
| 1. The borderline number | BR-310 | Built, **switched off** | Karim picks and signs ([BORDERLINE_DECISION.md](../criteria/BORDERLINE_DECISION.md)) |
| 2. The review list, complete | BR-407 | Done | — |
| 3. A rule change proves itself | BR-304 | Built, **CI job asleep** | Infrastructure: the `talent` runner ([PARITY_RUNNER.md](../criteria/PARITY_RUNNER.md)) |
| 4. The last 200, scored twice | — | Tool built | The old laptop script, to run it on |

## 1. Borderline (BR-310)

- `core.criteria_borderline` (migration 0014) holds one signed rule per criteria version, never
  changed. With no row, nothing opens, and the scoring run counts `borderline_rule_not_signed`.
- The scoring worker opens a `borderline_score` item for a non-disqualified score within the
  signed distance. The item names the evaluation, the tier above and the tier below. The
  evaluation's flags carry the `BORDERLINE: …` sentence. The tier is never changed.
- `python -m scoring.sign_borderline` records the signature.
- `python -m replay.borderline_options` measured the options on the workbook. Two findings
  differ from the brief: "only below the line" at 1 point is 23 people, not about half of 294;
  and the input-flip option is dominated by location (59%).

## 2. The review list (BR-407)

- `GET /v1/review-queue`: every open item in your scope, of every kind (`negative_verdict`,
  `flagged_document`, `unverified_candidate`, `possible_duplicate`, `borderline_score`), oldest
  first. Each line has `reason`, a sentence a recruiter reads, and `resolve_at`.
- `reason` was also added to `/v1/review-items` and `/v1/candidate-review-items`. A proposed
  rejection names the label from the reason list in force; a code with no sentence falls back to
  the kind's sentence, never the code.
- `GET /v1/reports/review-queue`, and `review_queue` inside `GET /v1/reports/funnel`: open items
  per kind, the oldest one's wait in hours, and which kind it is. The same is available from
  `python -m reports review-queue`.
- Scope is unchanged. A recruiter sees the items of the applications they own and of the
  candidates they can see; a TA lead, an admin and the criteria owner see all; a locked
  candidate's items are left out.
- The `/v1` contract changed additively only (`docs/api/openapi-v1.json`).

## 3. Rule changes prove themselves (BR-304)

- `python -m replay.baseline --html-report <file>` writes the who-moves-tier page: counts from →
  to and twenty sheet rows. CI now writes it, uploads it with the shadow diff, and puts the
  report in the run summary. The job also runs when `src/replay` changes, since a ruling there
  changes what parity means.
- `python -m replay.compare --from <in force> --to <proposed>` writes the same page between two
  registered versions, for a real version 2.
- Checked locally on the workbook: unchanged, parity holds (3,938 exact + 2 ruled, 0
  unexplained). With the near-New-Cairo weight changed from 30 to 25, the replay exits 1: 1,129
  differences and 260 tier moves.
- **Note:** this branch touches `src/scoring` (the worker and the borderline rule) and
  `src/replay`, so its own PR fails the `parity` job until the runner exists. That failure is
  designed; the gate is working.

## 4. The last 200

- `python -m replay.last_200 --legacy-script <file>` takes the 200 newest rows by Date Added
  (1,201 rows have no readable date and count as oldest), scores them with the old script as it
  is and with the platform's path (text fields → `candidate_from_fields` → the registered
  version), and lists every difference with the part of the score that moved.
- Run against the ported module as a stand-in: 200 compared, 0 differences. The real run needs
  the laptop's scorer file. It is not in the repository or on this machine.

## Carried from week 6

- The 94-pair duplicate check: waiting for the TA recruiter's file, then
  `python -m candidates.duplicates check --labels <file>`.
- Karim's signature on the BR-704 re-score report: still open (0 tier changes pending).

## Asks for Monday

| Who | What |
|---|---|
| Karim | Pick the borderline rule from the options page, and sign it. Sign BR-704. |
| Infrastructure | A runner labelled `talent` with the workbook, then `PARITY_RUNNER_READY=true`. |
| TA | Confirm the 19 rejection reasons and the stage order. Return the duplicate sample. |
| Legal | Retention periods (OPN-07). |
| Whoever holds the laptop | The scorer file the old runs used, for the last-200 run. |

## Tests

- Unit: 328 pass. The 4 that fail on Windows (`test_jobs_failures` path cases,
  `test_replay` file mode and process cases) failed before this week and are POSIX-only.
- Integration: 254 pass, 10 of them new, on a freshly migrated database (0001 → 0014).
