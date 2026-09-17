#!/usr/bin/env bash
# Puts one version of the platform on this machine.
#
#   scripts/deploy.sh 2026.09.20-a1b2c3d          # deploy that image tag
#   scripts/deploy.sh 2026.09.20-a1b2c3d --dry    # say what it would do
#
# What it does, in order: check the image exists, take a backup, run the migrations, start the new
# containers, wait until the API answers /readiness, and write the version down. If any step fails,
# it stops there and tells you how to go back (scripts/rollback.sh).
#
# It does NOT undo a migration. Ours are forward-only by design — an undo would lose decisions
# people made — so a rollback runs the previous code against the newer schema. That is safe for
# one version, because every migration adds; it is not a licence to skip a version.
set -euo pipefail

ENV_FILE="${TALENT_ENV_FILE:-/etc/talent/talent.env}"
HOME_DIR="${TALENT_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE="${TALENT_STATE_DIR:-/var/lib/talent}"
IMAGE="${TALENT_IMAGE:-ghcr.io/theaddresstech/talent-platform}"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
READY_URL="http://127.0.0.1:${TALENT_API_PORT:-8090}/ready"
READY_TRIES="${TALENT_READY_TRIES:-30}"

tag="${1:-}"
dry="${2:-}"
[ -n "$tag" ] || { echo "usage: $0 <image tag> [--dry]" >&2; exit 2; }

say () { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
# "none" when nothing has ever been migrated here, which is how a first deploy is told apart from
# a broken one.
schema_version () {
  "${COMPOSE[@]}" exec -T postgres psql -U talent_owner -d talent -tAc \
    "SELECT coalesce(max(version_num), 'none') FROM alembic_version" 2>/dev/null || echo none
}
run () { if [ "$dry" = "--dry" ]; then echo "    would run: $*"; else "$@"; fi; }

cd "$HOME_DIR"
export TALENT_IMAGE="$IMAGE" TALENT_IMAGE_TAG="$tag" TALENT_ENV_FILE="$ENV_FILE"

say "deploying $IMAGE:$tag"
[ -f "$ENV_FILE" ] || { echo "error: no environment file at $ENV_FILE" >&2; exit 1; }

say "1/7  is this machine configured?"
if [ "$dry" != "--dry" ]; then
  env PYTHONPATH=src "${TALENT_PYTHON:-.venv/bin/python}" -m ops.preflight --no-db || {
    status=$?
    [ "$status" -ge 2 ] && { echo "error: fix the configuration before deploying." >&2; exit 1; }
    say "     warnings above; continuing"
  }
fi

say "1b/7 is the image there?"
if ! docker image inspect "$IMAGE:$tag" >/dev/null 2>&1; then
  run docker pull "$IMAGE:$tag"
fi

say "2/7  database, cache and file store"
run "${COMPOSE[@]}" up -d --wait postgres redis object-storage

say "3/7  backup first, so there is a way back from the migration"
if [ -z "${TALENT_BACKUP_DIR:-}" ]; then
  say "     TALENT_BACKUP_DIR is not set: skipping. Set it before the next deploy."
elif [ "$dry" = "--dry" ]; then
  echo "    would run: ops.backup take --out $TALENT_BACKUP_DIR"
elif env PYTHONPATH=src "${TALENT_PYTHON:-.venv/bin/python}" -m ops.backup \
       --out "$TALENT_BACKUP_DIR" take --keep "${TALENT_BACKUP_KEEP:-14}"; then
  :
elif [ "$(schema_version)" = "none" ]; then
  # Nothing has been migrated here yet, so there is nothing to lose: this is a first deploy.
  say "     nothing to back up yet — first deploy on this machine"
else
  echo "error: the backup failed and this database holds data. Fix the backup before migrating:" >&2
  echo "       a migration with no way back is how an afternoon becomes a week." >&2
  exit 1
fi

say "4/7  migrations"
run "${COMPOSE[@]}" run --rm migrate

say "5/7  starting the new version"
run "${COMPOSE[@]}" up -d --remove-orphans

say "6/7  waiting for the API to answer"
if [ "$dry" != "--dry" ]; then
  for attempt in $(seq 1 "$READY_TRIES"); do
    if curl -fsS --max-time 3 "$READY_URL" >/dev/null 2>&1; then
      say "     ready after ${attempt} tries"
      break
    fi
    if [ "$attempt" -eq "$READY_TRIES" ]; then
      echo "error: the API did not become ready. Previous version: $(cat "$STATE/previous" 2>/dev/null || echo unknown)" >&2
      echo "       go back with: scripts/rollback.sh" >&2
      exit 1
    fi
    sleep 2
  done
fi

say "7/7  writing down what is running"
if [ "$dry" != "--dry" ]; then
  mkdir -p "$STATE"
  [ -f "$STATE/current" ] && cp "$STATE/current" "$STATE/previous"
  echo "$tag" > "$STATE/current"
  printf '%s  deployed %s by %s\n' "$(date -Is)" "$tag" "${USER:-unknown}" >> "$STATE/history"
fi

say "deployed $tag. Check it: PYTHONPATH=src ${TALENT_PYTHON:-.venv/bin/python} -m ops.watch"
