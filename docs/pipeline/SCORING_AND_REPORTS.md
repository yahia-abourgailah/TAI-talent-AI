# Scoring on arrival, open jobs, funnel and time reports

**Week 4, Person A.** OBJ-06, BR-301, BR-401, BR-405, BR-410, BR-601, BR-109.

## A1: every new application is scored

Creating an application queues a `score_application` job in the same transaction (migration 0006), whatever code created it. The `worker` service (`python -m jobs work --loop`) picks it up within about a second.

The worker builds the scorer's input from `core.candidate_field_current` with the replay's own mapping (`replay.mapping.candidate_from_values`, no Last Active), scores with the opening's criteria version (Track A → entry, Track B → headhunt), and writes a new `core.evaluation` row with `origin = 'computed'` and `evaluated_at`. A re-score is a new row.

A disqualification never rejects anyone. It opens a `proposed_rejection` review item for a person:

| Scorer says | Review item reason |
|---|---|
| Current TAI employee | `current_employee` |
| Non-Egypt location, Outside Cairo | `outside_hiring_area` |
| Over 32, Under 21 (Track A); Over 38 (Track B) | `age_outside_range` |
| Managerial/director title, 11+ or 12+ years (Track A); too senior (Track B) | `experience_not_a_fit` |
| Team Leader / Supervisor → Track B; Under 25 → Track A | none: routing, not a rejection |
| Track B: no tenured title, employer not recognised | none: listed as unresolved in the job run, **needs a ruling** |

If the list in force does not have the reason (for example the BRD placeholder list), no item is opened and the run lists it as unresolved. Load the proposed list first:

```bash
docker compose run --rm api python -m pipeline.lists load docs/pipeline/lists/proposed-2026-09-15.json --by "<your name>"
```

## A2: loading TA's open jobs

The file stays outside the repository. Columns (header row, any order): `brand, department, track, headcount, owner_recruiter, team, criteria_version`. `owner_recruiter` is the recruiter's sign-in subject.

```bash
python -m pipeline.load_openings "D:\TAI-data\open-jobs.csv" --api http://127.0.0.1:8090 --dev-account ta-lead --check-only
python -m pipeline.load_openings "D:\TAI-data\open-jobs.csv" --api http://127.0.0.1:8090 --dev-account ta-lead
```

On the test server use `--token-env TALENT_API_TOKEN` with a TA lead's token. Any invalid line refuses the whole file, by line number. A second run skips openings already there. `POST /v1/openings` now accepts an optional `criteria_version_id` (the version in force is used when absent).

## A3: the funnel

`reports.funnel.funnel_report(conn, group_by=..., date_from=..., date_to=...)`, the numbers behind `GET /v1/reports/funnel`. Counted only from `pipeline.move`; steps from the list in force. Group by `opening`, `brand`, `recruiter`, `team`. **Source is not offered** until applications carry a channel and tracking code. Contactability (phone or email on record) is reported per group and for all candidates.

```bash
docker compose run --rm api python -m reports funnel --group-by brand --from 2026-09-21T00:00:00+03:00
```

## A4: applied → scored → assigned

```bash
docker compose run --rm api python -m reports timing
```

Median, 90th percentile and slowest seconds per week of arrival, and every application not scored within 12 hours (late, including never scored). Assigned equals applied until week 6; the report says so. Migrated candidates are left out.
