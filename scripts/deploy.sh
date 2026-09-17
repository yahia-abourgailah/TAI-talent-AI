#!/usr/bin/env bash
# Deploys one version of the platform: scripts/deploy.sh <git tag or commit>
#
#   1. builds the image for that commit, unless it is already here
#   2. runs the start-up check with the real settings; an error stops here, nothing touched
#   3. takes a backup, restored and counted, before any migration
#   4. migrates, then starts the API and the workers on the new image
#   5. waits for /ready, runs the watch, and records what is running
#
# Migrations only go forward. If this deploy has to be undone, scripts/rollback.sh starts the
# previous image on the new schema: every migration here adds and never removes, so the old code
# still runs. The new schema stays. See docs/ops/DEPLOY.md, "Rolling back".
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy-lib.sh
. "$here/deploy-lib.sh"

ref="${1:-}"
[ -n "$ref" ] || die "usage: scripts/deploy.sh <git tag or commit>"
started=$(date +%s)

commit=$(git -C "$TALENT_HOME" rev-parse --verify --quiet "${ref}^{commit}") \
  || die "$ref is not a commit in $TALENT_HOME (git fetch --tags first?)"
image="talent-platform:${commit:0:12}"
say "deploying $ref ($commit) as $image"

# 1. The image, built from the commit itself, never from whatever is in the working folder.
if docker image inspect "$image" >/dev/null 2>&1; then
  say "image already built"
else
  say "building the image"
  git -C "$TALENT_HOME" archive --format=tar "$commit" | docker build --quiet -t "$image" - >/dev/null
fi
export TALENT_IMAGE="$image"

# 2. The start-up check, in the new image, with the settings exactly as the API will see them.
say "start-up check"
compose run --rm --no-deps preflight || die "the start-up check found a problem; nothing was changed"

before=$(db_revision)
target=$(image_head "$image")
say "schema: database at ${before}, this version needs ${target}"

# 3. A backup before anything can change the schema. The first deploy has nothing to back up.
if [ "$before" != "none" ]; then
  if [ "${TALENT_DEPLOY_SKIP_BACKUP:-}" = "yes-i-am-sure" ]; then
    say "warning: backup skipped on request"
  else
    [ -n "${TALENT_BACKUP_DIR:-}" ] || die "TALENT_BACKUP_DIR is not set; no deploy without a backup"
    say "backup before migrating"
    TALENT_BACKUP_DSN="postgresql://talent_owner:${TALENT_DB_OWNER_PASSWORD}@postgres:5432/talent" \
      tools -m ops.backup --out "$TALENT_BACKUP_DIR" --tools docker:postgres take \
      --keep "${TALENT_BACKUP_KEEP:-14}" \
      || die "the backup failed; nothing was changed"
  fi
fi

# 4. Migrate, then swap the processes.
say "starting the database and cache"
compose up -d --wait postgres redis
if [[ ",${COMPOSE_PROFILES:-}," == *",bundled-storage,"* ]]; then
  compose up -d --wait object-storage
  compose run --rm bucket-init >/dev/null
fi
say "migrating"
compose run --rm migrate
after=$(db_revision)

previous=$(state current)
say "starting the API and the workers"
if ! compose up -d --wait api worker; then
  record deploy-failed "$image" "$commit" "$after"
  die "the new version did not start. Roll back with: scripts/rollback.sh"
fi
if [[ ",${COMPOSE_PROFILES:-}," == *",crm,"* ]]; then compose up -d event-delivery; fi

# 5. Is it serving, and is anything wrong?
if ! wait_ready; then
  record deploy-failed "$image" "$commit" "$after"
  die "the API is not ready. Roll back with: scripts/rollback.sh"
fi
watch_now

if [ -n "$previous" ] && [ "$previous" != "$image" ]; then
  printf '%s\n' "$previous" > "$TALENT_STATE_DIR/previous"
fi
mark_current "$image"
record deploy "$image" "$commit" "$after"
say "deployed $ref in $(( $(date +%s) - started ))s. Schema ${before} -> ${after}. Previous: ${previous:-none}"
