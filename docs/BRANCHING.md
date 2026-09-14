# Branching strategy

Three long-lived branches, short-lived work branches, forward-only promotion.

```
feature/*  fix/*  criteria/*  chore/*
      |
      v
    dev  ---------->  staging  ---------->  main
 integration          UAT / shadow         production
 dev database         anonymised copy      live data
      ^                                        |
      +------------ back-merge <--- hotfix/* ---+
```

## Long-lived branches

| Branch | Purpose | Environment | Accepts merges from |
|---|---|---|---|
| `main` | Released, running in production | Production | `staging`, `hotfix/*` |
| `staging` | Release candidate under UAT and shadow evaluation | Pre-production, anonymised data | `dev`, `hotfix/*` |
| `dev` | Integration of completed work | Development | `feature/*`, `fix/*`, `criteria/*`, `chore/*` |

None of the three is ever force-pushed, rebased or deleted.

## Work branches

Branch from `dev`, merge back into `dev`, delete on merge.

| Prefix | Use |
|---|---|
| `feature/` | New capability |
| `fix/` | Defect in unreleased work |
| `criteria/` | A new hiring-criteria version |
| `chore/` | Tooling, dependencies, documentation |
| `hotfix/` | Production defect — branches from `main` |

Include the delivery phase where one applies, so the board reads against the BRD:

```
feature/p3-stage-machine
feature/p4-cv-extraction
fix/p1-shorouk-word-boundary
criteria/v2026-10-01-track-b-crossover
chore/p0-ci-parity-gate
hotfix/consent-guard-bypass
```

## Promotion

1. Work branch opens a PR into `dev`. **Squash-merge** — one commit per unit of work.
2. `dev` merges into `staging` when a phase or a coherent set of work is ready. **Merge commit**, never squash, so the release boundary stays visible.
3. `staging` merges into `main` on a named release. **Merge commit**, then tag.

Nothing skips a step. A change reaches production only via `staging`, with the single exception of a hotfix.

## Hotfixes

```bash
git switch main && git pull
git switch -c hotfix/<short-description>
# fix, test, PR into main with an incident reference
```

After a hotfix merges to `main`, immediately back-merge into `staging` and `dev` so the fix is not lost on the next promotion. A hotfix PR that does not link an incident will be rejected.

## Releases

Tag `main` on every promotion: `v<major>.<minor>.<patch>-p<phase>` — for example `v0.3.0-p3`.

Production deploys go out **after 06:00 Africa/Cairo**. Screening runs in an overnight GPU window (NFR-01) and a deploy into a running batch will interrupt it.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/). The scope is the module; the body cites the requirement.

```
feat(scoring): add Track B crossover employer band

Implements BR-305 for the adjacent-industry cohort.
Shadow diff: 36 candidates gain points, 8 move T2->T1.
Ruling: Karim AlAkkad, 2026-09-14.
```

Types: `feat`, `fix`, `criteria`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`.

## Two rules specific to this project

### Scoring changes are gated on the golden replay

Any PR touching `src/scoring/` runs the full-population parity suite against the master record. It cannot merge with a single unexplained difference. Each difference is triaged individually and ruled on by the criteria owner; the ruling is linked in the PR.

This exists because a scale correction on 2026-08-04 moved **1,518 candidates** between tiers. That was a deliberate, ratified change. The gate ensures the next one is also deliberate, and never silent.

### Criteria versions are immutable

A `criteria/*` branch may only **add** a version. Editing a published version is rejected in review. Every evaluation pins the version that produced it, and an edited version makes historic verdicts unreconstructable — which breaks BR-307 and CR-04.

## Branch protection

Applied via `scripts/setup_branch_protection.sh`, or by hand in repository settings.

| Setting | `main` | `staging` | `dev` |
|---|---|---|---|
| Require pull request | yes | yes | yes |
| Required approvals | 2 | 1 | 1 |
| Require CODEOWNERS review | yes | yes | yes |
| Dismiss stale approvals | yes | yes | no |
| Require status checks | all | all | unit + parity |
| Require branches up to date | yes | yes | no |
| Require linear history | yes | no | no |
| Require conversation resolution | yes | yes | yes |
| Block force push | yes | yes | yes |
| Block deletion | yes | yes | yes |
| Include administrators | yes | yes | no |
