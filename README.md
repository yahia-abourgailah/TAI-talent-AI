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
cp .env.example .env    # fill in paths and credentials — never commit this
```

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
| Business Requirements Document | Objectives, scope, numbered requirements, phases, risks |
