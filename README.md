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

Sign-in uses the company identity provider (`TALENT_OIDC_ISSUER`, `TALENT_OIDC_AUDIENCE`). On a dev machine only, `TALENT_AUTH_DEV_BYPASS=true` skips it; the API refuses to start with the bypass on in staging or prod.

### Tests

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/unit

# integration: a real database, reached on loopback
docker compose -f compose.yaml -f compose.test.yaml up -d --wait postgres
docker compose -f compose.yaml -f compose.test.yaml run --rm migrate
.venv/bin/pytest tests/integration
```

### Database areas

| Schema | Holds | The API may |
|---|---|---|
| `raw` | Original submissions, exactly as received | Insert and read. Never change or delete — enforced by grants and a trigger (BR-107) |
| `core` | Candidates, field provenance, evaluations, criteria versions | Insert, read, update. No delete (BR-205) |
| `pipeline` | Requisitions, applications, stage events | Insert, read, update. No delete |
| `audit` | Attributed actions and job runs | Insert and read |

## Layout

| Path | Contents |
|---|---|
| `src/api/` | HTTP app, routes, sign-in dependency |
| `src/auth/` | Company sign-in token verification |
| `src/config/` | Settings from `TALENT_*` variables, JSON logging |
| `src/db/` | Database engine for the app role |
| `src/infra/` | Readiness checks, dev storage bootstrap |
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
