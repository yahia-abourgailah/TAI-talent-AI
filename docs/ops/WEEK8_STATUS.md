# Week 8: status (2026-09-17)

No machine yet. Six of the nine jobs are done dry. Three wait for the hardware.

| Job | Rule | State | Waiting on |
|---|---|---|---|
| 1. Deploy and rollback, rehearsed | — | **Done** on the laptop | The real machine, to run it once more |
| 2. Restore into an empty machine | NFR-02 | **Done**. Found and fixed a gap | — |
| 3. Start-up check | — | **Done** | — |
| 4. "Nothing leaves" in CI | CR-01 | **Done** | — |
| 5. Arrivals per week | BR-602 | **Done** | Real applications, to have numbers |
| 6. Deployment guide | — | **Done** ([DEPLOY.md](DEPLOY.md)) | — |
| 7. Deploy for real | — | Waiting | Infrastructure: the machine and a second one; IT: the identity provider |
| 8. Switch the old system off | BR-705 | Ready ([SWITCH_OFF.md](SWITCH_OFF.md)) | Go-live, then TA leadership for step 1 |
| 9. Egress proof on the network | CR-01, OBJ-03 | Waiting ([EGRESS.md](../security/EGRESS.md)) | The machine |

## 1. Deploy and rollback

- `deploy/compose.prod.yaml`, `scripts/deploy.sh <tag>`, `scripts/rollback.sh [image]`.
- The deploy builds the image from the commit, runs the start-up check, takes a restored backup
  before migrating, migrates, starts, waits for `/ready`, runs the watch, and records it.
- **Migrations are forward-only.** A rollback keeps the newer schema and starts the old code on
  it. The rollback script refuses an image that needs a newer schema than the database has.
- Rehearsed on this laptop in a separate compose project: week 6 → week 7 (migrations 0014 and
  0015) → rollback to week 6 → week 7. The rollback took **32 s**, the data survived, and the
  week 6 worker scored a new application on the week 7 schema. Times are in DEPLOY.md §7.
- Two things the rehearsal found, both fixed: the settings file broke when read as shell (a value
  with a space), and the watch could not see the backups from inside a container. The watch now
  runs on the host, as the timer always did, and the database is published on loopback for it.

## 2. Restore into an empty machine

`python -m ops.backup drill` restores a backup into a Postgres container that holds nothing but
the software, counts every table against the manifest, and checks what the application role may
do.

**The gap was real.** Backups were taken with `--no-privileges`. On an empty machine every row
came back (81,077 of 81,077), and **the application could read none of them**. The nightly check
never saw this, because it restores on the same server, where the grants already exist.
Backups now keep their grants. After the change, the drill passes at schema 0007 (the local
database) and at 0015 (the rehearsal). The only thing created by hand is the two roles, from
`docker/postgres/roles.sql`, and that step is now in the runbook. Backups taken before this
change have no grants.

## 3. Start-up check

`python -m ops.preflight [--env-file FILE] [--strict]`. It runs in the deploy script and as a
one-shot service the API and the worker wait for. It names, without printing any value:

- no identity provider
- the stand-in OCR outside development, or no OCR address
- empty or unsafe `TALENT_CORS_ORIGINS`
- a proxy count that does not match how the API is published
- secrets empty, short or at a known example value
- empty database, Redis or storage addresses
- settings nothing reads (`TALENT_EVAL_PATH`, `TALENT_VECTOR_URL`) and model addresses

It found a bug in `.env.example`: an empty `TALENT_OCR_MODE=` stops the API from starting. The
line is now commented out, and the two unused settings are removed.

## 4. Nothing leaves

`tests/unit/test_egress.py` fails CI when a module that is not listed can open a network
connection, when a listed one picks up another network library, or when a network-capable
dependency is added. [EGRESS.md](../security/EGRESS.md) lists the destinations.

**There are six destinations, not five.** The week 7 watch posts alerts to a chat webhook
(counts only). It is listed, and the network rule must include it, or point it at an internal
relay.

## 5. Arrivals

`python -m reports arrivals [--master <sheet>]` and `GET /v1/reports/arrivals` (TA lead, admin):
new candidates per week by channel (careers page, job-post link by channel, CV only, typed by a
recruiter, scraped import), with `own_page` summed. With the sheet, `scraped_by_sheet` gives the
scrapers' weekly count from its Date Added, which is the number to compare. The local database is
at an older schema than the report needs, so there are no real numbers yet. The report fills as
applications arrive on the real machine.

## Decisions needed

| Who | What | Why now |
|---|---|---|
| OCR team / TA | OCR reachable, or launch with CV upload closed | Closing it needs a switch that is not built (about half a day). DEPLOY.md §9 |
| Infrastructure | The production machine, and a second one to restore onto | Jobs 7 and 9 |
| IT | Identity provider issuer and audience | The start-up check refuses without them |
| Website team | The careers page origin, and the number of proxies | `TALENT_CORS_ORIGINS`, `TALENT_TRUSTED_PROXY_HOPS` |
| Platform team | Where alert messages go: an internal relay, or no webhook | The sixth destination |
| Legal | Retention periods (OPN-07), consent wording | We go live holding data with no deletion date |

## Also noticed

- The local development database is recorded at migration 0007 but already holds objects from
  0008, so `alembic upgrade` fails on it. It is untouched. Rebuild it from a backup, or re-import,
  before relying on it.

## Tests

- Unit: 383 pass and 3 are skipped (the bash parse checks, which run on Linux). The 4 that fail
  on Windows are POSIX path and file-mode tests that failed before this week.
- Integration: 274 of 275 pass on a freshly migrated database. The 1 failure is the
  backup file-mode assertion, which cannot hold on Windows. The new grants assertion was checked
  on its own and holds.
