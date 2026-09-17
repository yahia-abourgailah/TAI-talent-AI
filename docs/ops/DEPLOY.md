# Deploying the Talent Platform

For whoever is handed the machine. It assumes you did not build the platform. When you are
finished, the [runbook](RUNBOOK.md) is for keeping it running.

**The one rule, again.** Nothing in this system is deleted. If a step below seems to need a
deletion of candidate data, stop and call the platform team.

---

## 0. What you need

| Item | Detail |
|---|---|
| A Linux machine | 4 cores, 16 GB memory, 200 GB disk for the database and backups. Inside the company network. |
| A second machine | Only to restore a backup onto, once (§8). It can be switched off afterwards. |
| Docker Engine with the compose plugin | `docker compose version` must print v2.24 or newer |
| git, curl, Python 3.12 | For the scripts and the host-side tools |
| A reverse proxy with TLS | In front of the API. The API itself listens on loopback only. |
| From IT | The identity provider's **issuer** and **audience** for this application |
| From Infrastructure | Object storage: an endpoint, a bucket with **object lock**, an access key and a secret |
| From the OCR team | The OCR address and an API key, reachable from this machine. Or the decision to launch with CV upload closed (§9). |
| From the website team | The careers page's exact origin (`https://…`), and how many proxies stand in front of the API |

## 1. Install

```bash
sudo useradd --system --create-home --home-dir /opt/talent talent
sudo usermod -aG docker talent
sudo -u talent git clone <repository URL> /opt/talent
cd /opt/talent
sudo -u talent git checkout main
sudo -u talent python3.12 -m venv .venv
sudo -u talent .venv/bin/pip install -e .
sudo mkdir -p /etc/talent /var/lib/talent/deploy /var/backups/talent
sudo chown talent: /var/lib/talent/deploy /var/backups/talent
sudo chmod 700 /var/backups/talent
```

The checkout supplies the scripts, the compose file and the database role script. The platform
itself runs from images the deploy script builds from a tagged commit, never from the working
folder.

## 2. The settings: `/etc/talent/talent.env`

Owned by root, readable by `talent` only: `sudo chown root:talent /etc/talent/talent.env && sudo
chmod 640 /etc/talent/talent.env`. One `KEY=value` per line, with no quotes and no `export`.

```ini
TALENT_ENV=prod
COMPOSE_PROJECT_NAME=talent
# add ",crm" once the CRM webhook is set; "bundled-storage" only if there is no company storage
COMPOSE_PROFILES=

# Where the API is published. 127.0.0.1 behind the reverse proxy.
TALENT_API_BIND=127.0.0.1
TALENT_API_PORT=8090
# The number of proxies in front of the API: 1 for the reverse proxy alone, 2 if a load
# balancer is in front of that. It must match, or rate limits are wrong for everyone.
TALENT_TRUSTED_PROXY_HOPS=1
# The database, on loopback, for the host's tools
TALENT_DB_HOST_PORT=5432

# Generate each with: openssl rand -base64 36
TALENT_DB_OWNER_PASSWORD=
TALENT_DB_APP_PASSWORD=
TALENT_JWT_SECRET=

TALENT_BLOB_ENDPOINT=https://<company object storage>
TALENT_BLOB_BUCKET=talent-raw
TALENT_BLOB_ACCESS_KEY=
TALENT_BLOB_SECRET_KEY=

TALENT_AUTH_MODE=oidc
TALENT_OIDC_ISSUER=https://<issuer from IT>
TALENT_OIDC_AUDIENCE=<audience from IT>

TALENT_OCR_MODE=api
TALENT_OCR_BASE_URL=https://<OCR host>
TALENT_OCR_API_KEY=

TALENT_CORS_ORIGINS=https://<careers page origin>,https://<CRM dashboard origin>

TALENT_BACKUP_DIR=/var/backups/talent
TALENT_ALERT_WEBHOOK_URL=https://<internal chat relay>

# Once the CRM team gives them
TALENT_CRM_WEBHOOK_URL=
TALENT_CRM_WEBHOOK_SECRET=
```

Leave out `TALENT_LLM_BASE_URL`, `TALENT_EMBED_BASE_URL`, `TALENT_EVAL_PATH` and
`TALENT_VECTOR_URL`. Nothing reads them, and the platform calls no language model
([EGRESS.md](../security/EGRESS.md)).

`TALENT_REDIS_URL` and `TALENT_DB_DSN` are not set here. The compose file builds them for the
containers.

### Check it before anything runs

```bash
cd /opt/talent
PYTHONPATH=src .venv/bin/python -m ops.preflight --env-file /etc/talent/talent.env --strict
```

Every line names a setting and what is wrong with it. `ERROR` stops the deploy. Fix every
`WARNING` before go-live, or write down why it stays. It never prints a value.

## 3. The settings for the timers: `/etc/talent/backup.env`

The nightly backup and the health check run on the host, as `talent`, because only the host can
see the backups.

```ini
TALENT_ENV=prod
TALENT_BACKUP_DIR=/var/backups/talent
TALENT_BACKUP_TOOLS=docker:postgres
TALENT_BACKUP_DSN=postgresql://talent_owner:<owner password>@127.0.0.1:5432/talent
TALENT_DB_DSN=postgresql+psycopg://talent_app:<app password>@127.0.0.1:5432/talent
TALENT_ALERT_WEBHOOK_URL=https://<internal chat relay>
TALENT_HOME=/opt/talent
TALENT_PYTHON=/opt/talent/.venv/bin/python
COMPOSE_PROJECT_NAME=talent
COMPOSE_FILE=/opt/talent/deploy/compose.prod.yaml
COMPOSE_ENV_FILES=/etc/talent/talent.env
```

Same ownership and mode as `talent.env`.

## 4. The first deploy

Deploy a **tag** from `main`, never a branch.

```bash
cd /opt/talent && sudo -u talent git fetch --tags
sudo -u talent scripts/deploy.sh v1.0.0-p10
```

The script:

1. builds the image from that commit (`talent-platform:<commit>`)
2. runs the start-up check in it; an error stops here with nothing changed
3. takes a backup, restored and counted, before any migration (skipped on the very first deploy:
   there is nothing to back up)
4. starts the database and Redis, migrates, then starts the API and the worker
5. waits for `/ready`, runs the watch, tags the image `talent-platform:current`, and records the
   deploy in `/var/lib/talent/deploy/history.tsv`

Then check it by hand:

```bash
curl -s http://127.0.0.1:8090/ready        # {"status":"ready", ...}
docker compose -f deploy/compose.prod.yaml --env-file /etc/talent/talent.env ps
PYTHONPATH=src .venv/bin/python -m ops.watch   # with /etc/talent/backup.env loaded
```

Point the reverse proxy at `127.0.0.1:8090`, with TLS, and sign in with a company account.

**One API process.** The public rate limits are counted in the API process's memory. Two
processes would double every limit. Keep one API container until the limits move to Redis.

## 5. The timers

```bash
sudo cp docs/ops/talent-backup.* docs/ops/talent-watch.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now talent-backup.timer talent-watch.timer
systemctl list-timers 'talent-*'
sudo systemctl start talent-backup.service && journalctl -u talent-backup.service -n 20
```

The backup's first run must end with "restored and counted back". Then `ops.watch` reports
`backup ok`.

## 6. Later deploys

```bash
cd /opt/talent && sudo -u talent git fetch --tags && sudo -u talent git checkout <tag>
sudo -u talent scripts/deploy.sh <tag>
```

Deploy after 06:00 Africa/Cairo ([BRANCHING.md](../BRANCHING.md)). The history file keeps who
deployed what, when, and at which schema.

## 7. Rolling back

```bash
sudo -u talent scripts/rollback.sh                       # the version before the last deploy
sudo -u talent scripts/rollback.sh talent-platform:<commit>   # a specific one still on the machine
```

**Migrations only go forward.** A rollback starts the previous image on the database as it is
now. It never undoes a migration, because undoing one would mean dropping what people recorded.
This works because every migration adds tables, columns and rules and never removes one the
older code uses. Keep it that way: a migration that removes or renames something the previous
version reads needs two releases (stop using it, then remove it).

The script refuses to start an image that needs a *newer* schema than the database has. That is
a deploy, not a rollback.

**Do not go back by deploying an older commit.** `deploy.sh <old commit>` runs *that version's*
start-up check, and an older check can refuse settings the newer one accepts — it stops before it
touches anything, so nothing breaks, but you have spent the outage arguing with a check instead of
serving. `rollback.sh` starts the old image directly and is the way back. (Seen on 17 September:
deploying the commit before the week-8 merge was refused by its own start-up check, which asked
the database questions before the database was started.)

If the new version wrote data the old one does not know about (a new kind of review item, say),
that data stays in the database and waits. Nothing is lost, and rolling forward again shows it.

### Rehearsed, 17 September 2026, on a laptop

A separate compose project with staging settings, from the scripts in this repository:

| Step | Version | Schema | Time |
|---|---|---|---|
| Deploy A, first time, image built | week 6 (`5ea2893`) | none → 0013 | 87 s |
| Add three made-up candidates | | | |
| Deploy B, image built, backup restored, migrated | week 7 (`b209809`) | 0013 → 0015 | 176 s |
| Deploy B again, image already built | | 0015 → 0015 | 79 s |
| **Roll back to A** | week 6 on schema 0015 | 0015 kept | **32 s** |
| On A: create an application, let the old worker score it | | | succeeded |
| Deploy B again | | 0015 | 73 s |

After the rollback, the three candidates were there, `/ready` answered, the API logged no error,
and the week 6 worker scored a new application on the week 7 schema. A redeploy of a built image
takes about 80 seconds, and most of it is the pre-migration backup.

**Do it once more on the real machine (§8) before go-live.**

## 8. Prove it on the machine

1. Deploy, then roll back, then deploy again (§6, §7). Write the times in the table above.
2. **Restore a backup onto the second machine.** Copy the newest `talent-*.dump` and its `.json`
   there (they hold every candidate: use `scp` between the two machines, nothing in between),
   check out the repository, and run:
   ```bash
   PYTHONPATH=src .venv/bin/python -m ops.backup --out <folder with the dump> drill
   ```
   It starts an empty Postgres container, restores into it, counts every table against the
   backup's manifest, and checks the application's permissions. It needs `"ok": true`. Then
   delete the copied dump from the second machine.
3. Switch the laptop off for a full day (go-live condition 6), and let the watch run.

## 9. If the OCR is not reachable on launch day

The fake OCR refuses to run outside development, and it should. Know what happens today:

- **With no OCR address, the deploy stops.** The start-up check reports `TALENT_OCR_BASE_URL`
  as an error. That is correct: without an address, every CV-reading job fails outright instead
  of sending the CV to the review list.
- **With an address the machine cannot reach,** the platform starts. Each uploaded CV is tried
  with growing gaps, then waits in the review list as "we could not read this CV". Nothing is
  lost, but every applicant who uploads a CV lands in a person's queue.

**There is no switch that closes CV upload.** If the decision is to launch closed, that switch
has to be built first: a setting that makes `POST /v1/public/cv-uploads` refuse with a clear code,
while the website team hides the step. It is about half a day of work, and it is not built,
because nothing new is built this week unless it blocks the launch. Take the decision early in
week 8, so there is time to build the switch if it is needed.

## 10. What is not here yet

- Legal's retention periods (OPN-07). The eraser refuses to run without them, so the platform
  goes live holding data with no deletion date. That is a known and recorded state.
- The network deny rule and a week of its log ([EGRESS.md](../security/EGRESS.md)).
