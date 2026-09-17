#!/usr/bin/env bash
# Shared by deploy.sh and rollback.sh. Not run on its own.
#
# Settings, all with production defaults (docs/ops/DEPLOY.md):
#   TALENT_HOME          the checkout the scripts and compose file come from   /opt/talent
#   TALENT_ENV_FILE      the platform's settings                               /etc/talent/talent.env
#   TALENT_STATE_DIR     what is running, what ran before, and the history     /var/lib/talent/deploy
#   TALENT_BACKUP_DIR    where the pre-migration backup goes (from the env file)
#   TALENT_PYTHON        the Python that runs the backup and watch tools       $TALENT_HOME/.venv/bin/python
#   COMPOSE_PROJECT_NAME the compose project                                   talent

TALENT_HOME="${TALENT_HOME:-/opt/talent}"
TALENT_ENV_FILE="${TALENT_ENV_FILE:-/etc/talent/talent.env}"
TALENT_STATE_DIR="${TALENT_STATE_DIR:-/var/lib/talent/deploy}"
export TALENT_HOME TALENT_ENV_FILE

say() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -f "$TALENT_ENV_FILE" ] || die "no settings at $TALENT_ENV_FILE (docs/ops/DEPLOY.md, step 3)"
[ -f "$TALENT_HOME/deploy/compose.prod.yaml" ] || die "no deploy/compose.prod.yaml under $TALENT_HOME"
command -v docker >/dev/null || die "docker is not installed"

# The settings file's values, for this script: the compose project, the port, the passwords.
# Read as docker reads it: KEY=VALUE, the value taken literally (spaces and $ included), never run
# through a shell.
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  export "$key=${line#*=}"
done < "$TALENT_ENV_FILE"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-talent}"
# ops.backup runs `docker compose exec`; these make it reach this stack, not a dev one.
export COMPOSE_FILE="$TALENT_HOME/deploy/compose.prod.yaml"
export COMPOSE_ENV_FILES="$TALENT_ENV_FILE"

if [ -z "${TALENT_PYTHON:-}" ]; then
  if [ -x "$TALENT_HOME/.venv/bin/python" ]; then TALENT_PYTHON="$TALENT_HOME/.venv/bin/python"
  else TALENT_PYTHON="$TALENT_HOME/.venv/Scripts/python.exe"; fi
fi

compose() { docker compose --env-file "$TALENT_ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
tools() { (cd "$TALENT_HOME" && PYTHONPATH=src "$TALENT_PYTHON" "$@"); }

mkdir -p "$TALENT_STATE_DIR"
state() { cat "$TALENT_STATE_DIR/$1" 2>/dev/null || true; }
record() {
  # history: when (UTC), who, what, image, git commit, schema
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SUDO_USER:-${USER:-${USERNAME:-?}}}" \
    "$1" "$2" "${3:--}" "${4:--}" >> "$TALENT_STATE_DIR/history.tsv"
}

db_running() { [ -n "$(compose ps --status running --quiet postgres 2>/dev/null)" ]; }

db_revision() {
  db_running || { echo none; return; }
  compose exec -T postgres psql -U talent_owner -d talent -Atc \
    "SELECT version_num FROM alembic_version" 2>/dev/null || echo none
}

# The newest migration an image carries.
image_head() {
  docker run --rm --entrypoint alembic "$1" heads 2>/dev/null | awk '{print $1; exit}'
}

wait_ready() {
  local url="http://${TALENT_API_BIND:-127.0.0.1}:${TALENT_API_PORT:-8090}/ready"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then say "ready: $url"; return 0; fi
    sleep 2
  done
  return 1
}

watch_now() {
  # Advice, not a gate. Run from the host, like talent-watch.timer, because only the host can see
  # the backups.
  if ! TALENT_DB_DSN="postgresql+psycopg://talent_app:${TALENT_DB_APP_PASSWORD}@127.0.0.1:${TALENT_DB_HOST_PORT:-5432}/talent" \
      tools -m ops.watch --backup-dir "${TALENT_BACKUP_DIR:-}"; then
    say "the watch reports something to look at (above)"
  fi
}

mark_current() {
  docker tag "$1" talent-platform:current
  printf '%s\n' "$1" > "$TALENT_STATE_DIR/current"
}
