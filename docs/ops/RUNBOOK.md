# Runbook

For whoever is looking after the Talent Platform today. It assumes you did not build it.

You need: a shell on the platform machine, the environment file at `/etc/talent/`, and the ability
to run `docker compose`. Nothing here needs Python knowledge.

**The one rule.** Everything in this system is built so that nothing is ever lost: no table allows
a delete, and the database itself refuses one. If a fix you are about to make involves deleting
candidate data, stop and call someone. There is always another way.

---

## 1. Is anything wrong?

```
cd /opt/talent
PYTHONPATH=src .venv/bin/python -m ops.watch
```

It prints one line per check and exits 0 (fine), 1 (look today) or 2 (wrong now). The same answer
is at `GET /v1/ops/health` for an admin. The nightly timer runs it every 15 minutes and posts to
the chat channel when it is not fine.

| Check | What it means | Turn to |
|---|---|---|
| `queue_waiting` | Work is piling up: CVs unread, applications unscored | §2 |
| `jobs_failing` | Jobs gave up in the last 24 hours | §3 |
| `jobs_stuck` | A job says "running" but no worker is on it — a worker was killed | §2 |
| `events_undelivered` | The CRM is not taking our events | §4 |
| `backup` | Last night's backup is missing, old, or was never restored | §5 |

In development the checks ignore job kinds beginning `test-`: those come from the test suite. No
real kind is named that way.

---

## 2. The queue is growing, or a job is stuck

A worker has died, or none is running. Nothing is lost — the queue is in the database.

```
docker compose ps                          # is the worker container up?
docker compose logs --tail=100 worker      # what did it say before it stopped?
docker compose up -d worker                # start it again
```

A job left marked "running" by a killed worker is picked up again by itself: the worker records
the stop (`audit.job_run` with `worker_stopped`) and queues the job for another attempt, up to the
attempt limit. You do not have to do anything by hand.

Watch it drain:

```
PYTHONPATH=src .venv/bin/python -m ops.watch
```

If the queue does not drain with the worker up, it is failing rather than stopped — §3.

---

## 3. Jobs are failing

Every run is recorded with its counts and its error. Nothing is guessed and nothing is hidden.

```
docker compose exec -T postgres psql -U talent_owner -d talent -c "
  SELECT kind, error, count(*), max(finished_at)
  FROM audit.job_run WHERE outcome = 'failed' AND finished_at > now() - interval '24 hours'
  GROUP BY kind, error ORDER BY 3 DESC LIMIT 20"
```

| What the error says | What is happening | What to do |
|---|---|---|
| `ocr_unavailable`, `ocr_timed_out` | The CV reading service is down or slow | Check with the OCR team. Nothing is lost: each CV waits in the review list and is read when the service is back |
| `ocr_rejected`, `ocr_answer_unreadable` | The service answered, but not with something we can use | Leave it. The CV is already waiting for a person |
| `no scorer for criteria version …` | A job refers to a rules version this build does not have | Do not change the rules. Call the platform team |
| Anything mentioning `permission denied` | Someone is running as the wrong database role | Call the platform team. Do not grant anything |

A failing job never loses a candidate. The candidate sits in the review list until a person sees
them — that is the design, not a fault.

---

## 4. The CRM is not receiving events

Delivery is switched off entirely while `TALENT_CRM_WEBHOOK_URL` is empty; then this check simply
counts the events sitting in the catch-up feed, and the CRM team can read them at any time from
`GET /v1/events`. Nothing is lost either way — events are kept.

If the URL is set and events are "parked", we tried repeatedly and gave up:

1. Ask the CRM team whether their endpoint is up and whether their shared secret changed.
2. When it is back, events resume from where they stopped. They may see one twice; they are told
   to de-duplicate on the event id.

Never edit the event table to "clear" a backlog.

---

## 5. Backups

They live in `TALENT_BACKUP_DIR` (see `/etc/talent/backup.env`), one dump and one manifest per
night, readable only by the `talent` user. Each one is restored into a throwaway database as soon
as it is taken — a backup nobody restored is not a backup.

```
PYTHONPATH=src .venv/bin/python -m ops.backup --out "$TALENT_BACKUP_DIR" list
PYTHONPATH=src .venv/bin/python -m ops.backup --out "$TALENT_BACKUP_DIR" verify   # newest one
systemctl list-timers talent-backup.timer
journalctl -u talent-backup.service -n 50
```

### Restoring for real

You are restoring because something is badly wrong. Read all four steps before starting.

1. **Take a dump of what is there now, whatever state it is in.** It costs two minutes and it is
   the only copy of anything that happened since last night.
   ```
   PYTHONPATH=src .venv/bin/python -m ops.backup --out /var/tmp/before-restore take --no-verify
   ```
2. **Stop the API and the workers** so nothing writes while you work:
   ```
   docker compose stop api worker
   ```
3. **Restore into a new database, never over the live one**, then point the platform at it. The
   check command does the restore for you into a scratch database; for a real one:
   ```
   docker compose exec -T postgres createdb -U talent_owner talent_restored
   docker compose exec -T postgres pg_restore -U talent_owner -d talent_restored \
       --no-owner --no-privileges --exit-on-error < "$TALENT_BACKUP_DIR/<file>.dump"
   ```
   Then change the database name in `/etc/talent/*.env` to `talent_restored` and start again:
   ```
   docker compose up -d api worker
   PYTHONPATH=src .venv/bin/python -m ops.watch
   ```
4. **Tell people what was lost.** Anything recorded between the backup and the incident is in the
   dump from step 1, not in the running system. The platform team can replay it.

The CV files are not in the database. They are copied by the same nightly run into
`$TALENT_BACKUP_DIR/files`, and because each file is named by its own content hash, copying them
back is safe to repeat.

---

## 6. Erasing candidates whose time is up

Nothing is erased today: Legal owes us the retention periods (OPN-07), and until a policy is
activated the command refuses to run. When they answer, the periods are loaded as a version and
put in force — they are never edited afterwards, a change is a new version.

```
PYTHONPATH=src .venv/bin/python -m ops.retention policy     --file docs/retention/<version>.json --by "<your name>" --activate
PYTHONPATH=src .venv/bin/python -m ops.retention due         # who falls due, ids and counts
PYTHONPATH=src .venv/bin/python -m ops.retention erase --by "<your name>"           # says what it would do
PYTHONPATH=src .venv/bin/python -m ops.retention erase --by "<your name>" --confirm # does it
```

It runs as the owner role (`TALENT_ERASURE_DSN`), because the application role cannot erase
anything and must not be able to. Without `--confirm` it only reports.

A candidate who asks to be forgotten now, rather than waiting for their period, is done one at a
time and recorded as a request, not as retention:

```
PYTHONPATH=src .venv/bin/python -m ops.retention erase --candidate 4821 --reason request     --by "<your name>" --confirm
```

**What erasure removes:** every field value, the original CV and the reader's answer to it, and the
text inside an evaluation that quotes the person. **What it keeps:** the record marked erased, its
score and tier, the steps it moved through, the consent it gave, and the erasure itself. That is
deliberate — it honours the erasure without making last quarter's numbers a lie.

It cannot be undone. Take a backup first (§5), and never erase because a record "looks wrong" —
that is what archiving is for.

---

## 7. Deploying a version, and going back

```
scripts/deploy.sh 2026.09.20-a1b2c3d        # put this version on
scripts/deploy.sh 2026.09.20-a1b2c3d --dry  # say what that would do, change nothing
scripts/rollback.sh                          # back to the version before
```

The deploy takes a backup before it migrates, waits for `/ready`, and writes the version into
`/var/lib/talent/current`, with every deploy and rollback in `history` beside it. If the API does
not answer, it stops and tells you to roll back rather than leaving you to guess.

**A rollback does not undo the database migration.** Ours are forward-only, because undoing one
would lose decisions people made; every migration adds rather than replaces, so the previous
version's code runs against the newer schema. That is safe for one version back. For more than
one, restore a backup (§5) instead.

Rehearsed on 17 Sep on a stack built from nothing: deploy 16s, second deploy 26s, **rollback 17s**.

---

## 8. The API is down

```
curl -s localhost:8090/health        # the process is up
curl -s localhost:8090/ready         # it can reach the database, cache and file store
docker compose logs --tail=100 api
docker compose up -d api
```

`/ready` failing with the database means start at §9. Failing with the file store means CV
uploads and downloads are refused — everything else keeps working.

---

## 9. The database will not start, or is out of space

```
docker compose logs --tail=100 postgres
df -h                                # the disk the database volume is on
```

Out of space: **do not delete candidate data to make room.** Move old backups off the machine
first (they are the biggest thing there), then call the platform team. Postgres refuses writes
before it corrupts anything, so a full disk is an outage, not a loss.

---

## 10. Things you must never do

- Delete candidates, fields, moves, evaluations or events. The database refuses; do not look for a
  way around it. A record that must go is archived with a reason, or erased under the retention
  rules, which is a different and recorded thing.
- Run with `TALENT_AUTH_MODE=dev` anywhere but a developer's machine. It refuses to start, and
  that refusal is protecting you.
- Restore a backup over the live database without step 1 above.
- Paste candidate names, numbers or CVs into a chat channel, a ticket, or a log. Ids and counts
  only — every tool here is built that way and so should you be.

---

## 11. Who to call

| Situation | Who |
|---|---|
| Scoring looks wrong, or a rules question | Karim (criteria owner) |
| A candidate's record is wrong, or a duplicate needs joining | TA lead |
| The platform itself: errors, restores, deploys | Platform team (Person A / Person B) |
| The CV reading service | OCR team |
| The careers page | Website team |
| A candidate asks us to stop keeping their data | TA lead records it in the platform; it locks at once |
