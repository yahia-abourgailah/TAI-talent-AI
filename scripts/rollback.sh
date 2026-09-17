#!/usr/bin/env bash
# Puts the previous version back.
#
#   scripts/rollback.sh                    # the version before this one
#   scripts/rollback.sh 2026.09.19-9f8e7d  # a specific one
#
# The database schema is NOT rolled back: our migrations are forward-only, because undoing one
# would lose decisions people made. Every migration adds rather than replaces, so the previous code
# runs against the newer schema. Going back more than one version has not been tested — if you need
# that, restore a backup instead (docs/ops/RUNBOOK.md §5).
set -euo pipefail

HOME_DIR="${TALENT_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE="${TALENT_STATE_DIR:-/var/lib/talent}"
IMAGE="${TALENT_IMAGE:-ghcr.io/theaddresstech/talent-platform}"
READY_URL="http://127.0.0.1:${TALENT_API_PORT:-8090}/ready"
READY_TRIES="${TALENT_READY_TRIES:-30}"
cd "$HOME_DIR"

tag="${1:-$(cat "$STATE/previous" 2>/dev/null || true)}"
[ -n "$tag" ] || { echo "error: no previous version recorded. Give one: $0 <image tag>" >&2; exit 2; }
current="$(cat "$STATE/current" 2>/dev/null || echo unknown)"

echo "rolling back from $current to $tag (the schema stays where it is)"
export TALENT_IMAGE="$IMAGE" TALENT_IMAGE_TAG="$tag" \
       TALENT_ENV_FILE="${TALENT_ENV_FILE:-/etc/talent/talent.env}"

docker image inspect "$IMAGE:$tag" >/dev/null 2>&1 || docker pull "$IMAGE:$tag"
# No migration step: that is the whole difference from a deploy.
docker compose -f compose.yaml -f compose.prod.yaml up -d --remove-orphans

for attempt in $(seq 1 "$READY_TRIES"); do
  if curl -fsS --max-time 3 "$READY_URL" >/dev/null 2>&1; then
    echo "back on $tag after ${attempt} tries"
    break
  fi
  if [ "$attempt" -eq "$READY_TRIES" ]; then
    echo "error: the API did not come back on $tag. This is the point to restore a backup:" >&2
    echo "       docs/ops/RUNBOOK.md §5" >&2
    exit 1
  fi
  sleep 2
done

mkdir -p "$STATE" 2>/dev/null || true
printf '%s  rolled back to %s by %s\n' "$(date -Is)" "$tag" "${USER:-unknown}" >> "$STATE/history" 2>/dev/null || true
echo "$tag" > "$STATE/current" 2>/dev/null || true
echo "Check it: PYTHONPATH=src .venv/bin/python -m ops.watch"
