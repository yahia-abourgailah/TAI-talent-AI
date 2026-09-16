# Talent Platform

Candidate sourcing, screening and outreach for The Address Investments — Talent Acquisition.

Replaces the Leila AI spreadsheet-and-scripts system with a governed, auditable platform running on owned infrastructure. Requirements are in the BRD; this repository is the implementation.

> **This repository contains code only.** No candidate record, CV, export or credential is ever committed. Read [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) before adding files.

## Principles

**Deterministic rules decide. Language models order and explain.** A candidate's tier always traces to a rule and a number, never to a model output — that is what a rejected applicant is entitled to ask for.

**Criteria versions are immutable.** Every evaluation pins the criteria, model and prompt versions that produced it, so any verdict is reconstructable months later without re-running anything.

**Blank and flagged beats filled-in and wrong.** No candidate field is ever inferred. Unverified is the default, and it is visible.

## Getting started

```bash
git clone <remote> talent-platform
cd talent-platform
git switch dev
cp .env.example .env    # fill in passwords and keys — never commit this
docker compose up --build
```

The API answers on `http://127.0.0.1:8090` — `/health`, `/ready`, `/docs`. It is the only published port; the database, cache and object storage stay inside the compose network.

Sign-in uses the company identity provider (`TALENT_OIDC_ISSUER`, `TALENT_OIDC_AUDIENCE`). On a dev machine, set `TALENT_AUTH_MODE=dev` to sign in as a fake account instead. The tokens are checked exactly like real ones, and the API refuses to start in dev mode outside dev.

```bash
curl -s http://127.0.0.1:8090/dev/accounts            # recruiter-a, recruiter-b, ta-lead, criteria-owner, admin
TOKEN=$(curl -s -X POST http://127.0.0.1:8090/dev/token -H 'Content-Type: application/json' \
  -d '{"account": "recruiter-a"}' | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')
curl -s http://127.0.0.1:8090/v1/me -H "Authorization: Bearer $TOKEN"
```

In `/docs`, use **Authorize** and paste the token.

### Tests

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/unit

# integration: a real database, reached on loopback
docker compose -f compose.yaml -f compose.test.yaml up -d --wait postgres
docker compose -f compose.yaml -f compose.test.yaml run --rm migrate
.venv/bin/pytest tests/integration
```

### Import TAI_Master

Set `TALENT_MASTER_PATH` (the workbook) and `TALENT_RECONCILE_DIR` (an output folder) in `.env`, both outside the repository, and set `TALENT_UID` and `TALENT_GID` to the output of `id -u` and `id -g`, so the tools can use private (`chmod 700`) data folders.

```bash
docker compose up -d --build
docker compose -f compose.yaml -f compose.tools.yaml run --rm jobs python -m importer.tai_master --by "<your name>"
docker compose -f compose.yaml -f compose.tools.yaml run --rm jobs python -m jobs show <job id>
docker compose -f compose.yaml -f compose.tools.yaml run --rm jobs python -m importer.reconcile --out /data/out
```

Running the import again writes nothing new, even from a re-saved or re-exported copy of the workbook. A row whose cells changed since it was imported is never overwritten: nothing is written for it and it is listed as unresolved. Each run is recorded in `audit.job_run` with its counts, skips and unresolved rows, and never with candidate values. If an import is stopped part-way, nothing from it is kept; the next import, or `python -m jobs recover`, records the stopped run and queues it again.

The reconciliation compares the database and the raw captures with the workbook's cells, and writes a counts-only report and a 200-row sample. The sample holds candidate data and never goes in the repository. Whoever checks it by hand records that in `docs/migration/WEEK2_DECISIONS.md`.

A local database loaded before migration 0004 holds rows in the old capture format: reset it with `docker compose down -v` and import again. Archive a record with `python -m candidates.archive --candidate-id N --reason "..." --by "<your name>"`.

### Baseline replay

Replays criteria version 2026-08-04 over every master row. The per-row output holds candidate data, so it must go outside the repository; the aggregate report holds counts only.

```bash
set -a; . ./.env; set +a
.venv/bin/python -m replay.baseline --out ~/TAI-data/baseline --run-date 2026-09-14 \
  --report-copy docs/migration/BASELINE_REPORT.md
```

### Week 3 checks

BR-704: the rows the 3 August 2026 fixes could have changed, stored result against today's rules. The report holds counts and sheet rows only, and nothing is re-scored.

```bash
set -a; . ./.env; set +a
.venv/bin/python -m replay.rescore --run-date 2026-09-14 --report-copy docs/migration/RESCORE_REPORT.md
```

Decisions waiting on it are in [docs/migration/WEEK3_DECISIONS.md](docs/migration/WEEK3_DECISIONS.md).

### API and events

`docker compose up -d api` serves the API on http://127.0.0.1:8090, with interactive docs at `/docs`. The contract, conventions and examples for the CRM and website teams are in [docs/api/API_PLAN.md](docs/api/API_PLAN.md).

```bash
# The frozen /v1 contract (docs/api/openapi-v1.json). --check fails on a breaking change;
# --write records an additive one, and the file change goes through review.
.venv/bin/python -m api.contract --check

# Events to the CRM: set TALENT_CRM_WEBHOOK_URL and TALENT_CRM_WEBHOOK_SECRET in .env, then
docker compose --profile crm up -d event-delivery
docker compose run --rm api python -m integrations.webhooks status   # delivered, pending, parked
```

### Reading CVs (week 5)

`TALENT_OCR_MODE` picks the OCR: `fake` (saved samples, dev only, and the default in dev) or `api` (the OCR API on our own host: set `TALENT_OCR_BASE_URL` and `TALENT_OCR_API_KEY`). The `worker` service reads uploaded CVs as `read_cv` jobs. Anything that goes wrong sends the CV to a person as a `flagged_document` review item. Details are in [docs/intake/OCR_ANSWER.md](docs/intake/OCR_ANSWER.md).

```bash
curl -s -F "file=@/path/outside/repo/made-up-cv.pdf" http://127.0.0.1:8090/v1/public/cv-uploads
curl -s http://127.0.0.1:8090/v1/public/cv-uploads/upl_1 -H "X-Upload-Token: <token>"

# CV reading accuracy on the labelled test set (docs/intake/CV_TEST_SET.md)
.venv/bin/python -m intake.accuracy run --set "$TALENT_CV_TEST_SET" --reader api
```

### Database areas

| Schema | Holds | The API may |
|---|---|---|
| `raw` | Original submissions, exactly as received | Insert and read. Never change or delete — enforced by grants and a trigger (BR-107) |
| `core` | Candidates, field provenance, evaluations, criteria versions | Insert, read, update. No delete (BR-205) |
| `pipeline` | Requisitions, applications, stage events | Insert, read, update. No delete |
| `audit` | Attributed actions and job runs | Insert and read |
| `api` | Kept responses for `Idempotency-Key` retries | Insert and read. Append-only |
| `integration` | Events for the CRM, and every delivery attempt | Read events (the database writes them); insert and read attempts |
| `intake` | CV uploads (token hashes only) and how reading each CV ended | Insert and read. Append-only |

## Layout

| Path | Contents |
|---|---|
| `src/api/` | HTTP app, routes, sign-in dependency |
| `src/auth/` | Company sign-in token verification |
| `src/config/` | Settings from `TALENT_*` variables, JSON logging |
| `src/db/` | Database engine for the app role |
| `src/infra/` | Readiness checks, dev storage bootstrap |
| `src/intake/` | CVs in: upload, the OCR adapter (real and fake), the answer-to-fields mapping, the `read_cv` job, manual entry, and the accuracy runner |
| `src/integrations/` | Events for the CRM: the feed and webhook delivery |
| `src/replay/` | Golden replay: re-runs a criteria version over the master workbook |
| `src/scoring/` | Candidate scoring. `rulesets/` holds one immutable module per criteria version |
| `migrations/` | Database migrations, run as the schema owner |
| `docker/` | Database role setup |
| `tests/unit/` | Fast tests, no services needed |
| `tests/integration/` | Tests against a real database |
| `docs/` | BRD, build plan, criteria archive, migration column map |

Candidate data stays outside the repository and is referenced by `TALENT_MASTER_PATH`.

## Branches

`dev` → `staging` → `main`. Work branches come off `dev`. Full detail in [docs/BRANCHING.md](docs/BRANCHING.md).

```bash
git switch dev && git pull
git switch -c feature/p3-stage-machine
```

## Documentation

| Document | Contents |
|---|---|
| [docs/BRANCHING.md](docs/BRANCHING.md) | Branch model, promotion, releases, protection rules |
| [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) | What may not be committed, and why |
| [docs/BRD.html](docs/BRD.html) | Business Requirements Document v0.1 — objectives, scope, numbered requirements, phases, risks |
| [docs/BUILD_PLAN.html](docs/BUILD_PLAN.html) | 8-week, 2-person delivery plan v2 — every BR, NFR and CR mapped to a week |
| [docs/criteria/CRITERIA_2026-08-04.md](docs/criteria/CRITERIA_2026-08-04.md) | Legacy hiring criteria, archived at the version the scorer implements |
| [docs/migration/COLUMN_MAP.md](docs/migration/COLUMN_MAP.md) | Where each of the 47 workbook columns goes, with open questions |
