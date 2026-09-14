# Data handling

**This repository contains code only.** No candidate record, CV, export, report or credential is ever committed.

## Why

Candidate records are personal data under Egypt's Personal Data Protection Law, enforceable from October 2026. Git history is effectively permanent: deleting a file in a later commit does not remove it from the repository, and pushing to a hosted remote discloses it to that host regardless of whether the repository is private.

The existing handover holds roughly **5,140 candidate records** — names, phone numbers, profile URLs — together with individual CVs. None of it belongs here.

## Where data lives instead

| Data | Location | Referenced by |
|---|---|---|
| Master candidate record | Internal storage, outside the repository | `TALENT_MASTER_PATH` |
| Raw CVs and source payloads | Object storage | `TALENT_BLOB_*` |
| Evaluation holdout set | Internal storage | `TALENT_EVAL_PATH` |
| Credentials | Environment variables or a secret store | Never a file in the tree |

Paths are configuration. Code reads them from the environment and fails fast when they are unset — it never falls back to a committed sample.

## Enforced by

1. `.gitignore` blocks the data and credential extensions outright.
2. A CI job scans every pull request for candidate-data patterns and secrets, and fails the build on a hit.
3. `.gitignore` is owned by compliance in `CODEOWNERS`, so loosening it requires their review.

## If data is committed anyway

Treat it as an incident, not a cleanup.

1. Do not merge. Do not push the branch to a shared remote if it has not left the machine.
2. Notify the criteria owner and compliance.
3. If it reached a remote, rotate anything credential-shaped and assume disclosure.
4. Purge history with `git filter-repo`, force-push under coordination, and have every clone re-cloned. A revert commit is not sufficient.
