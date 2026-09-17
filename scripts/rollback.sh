#!/usr/bin/env bash
# Puts the previous version back: scripts/rollback.sh [image]
#
# With no argument it starts the image that ran before the last deploy. It never touches the
# schema. Our migrations only go forward and every one of them adds, so the previous code runs on
# the newer schema; undoing a migration would mean dropping what people recorded. The script says
# which schema it leaves in place.
#
# It refuses to start an image that needs a newer schema than the database has: that is a
# deploy, not a rollback.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy-lib.sh
. "$here/deploy-lib.sh"

started=$(date +%s)
current=$(state current)
target="${1:-$(state previous)}"
[ -n "$target" ] || die "nothing to roll back to: no previous deploy is recorded in $TALENT_STATE_DIR"
[ "$target" != "$current" ] || die "$target is already running"
docker image inspect "$target" >/dev/null 2>&1 \
  || die "$target is not on this machine; it was removed, so roll forward with scripts/deploy.sh"
say "rolling back from ${current:-nothing} to $target"

schema=$(db_revision)
needs=$(image_head "$target")
say "schema: database at ${schema}, $target was built for ${needs}"
if [ "$schema" != "none" ] && [ -n "$needs" ] && [ "$((10#$needs))" -gt "$((10#$schema))" ]; then
  die "$target needs schema $needs, newer than the database ($schema). Use scripts/deploy.sh"
fi
if [ "$schema" != "$needs" ]; then
  say "keeping schema $schema: migrations only go forward, and $target runs on it"
fi

export TALENT_IMAGE="$target"
compose up -d --wait api worker || die "$target did not start. Look at: docker compose logs api"
if [[ ",${COMPOSE_PROFILES:-}," == *",crm,"* ]]; then compose up -d event-delivery; fi
wait_ready || die "$target is not ready. Look at: docker compose logs api"
watch_now

mark_current "$target"
[ -n "$current" ] && printf '%s\n' "$current" > "$TALENT_STATE_DIR/previous"
record rollback "$target" - "$schema"
say "rolled back to $target in $(( $(date +%s) - started ))s. Schema left at $schema"
