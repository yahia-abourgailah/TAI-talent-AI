#!/usr/bin/env bash
# One night's backup: dump, restore it into a throwaway database as a check, prune the old ones,
# then copy any new CV files out of the object store.
#
# Install it with the unit and timer in docs/ops/, or from cron:
#   15 2 * * *  /opt/talent/scripts/nightly-backup.sh >> /var/log/talent-backup.log 2>&1
#
# It reads TALENT_BACKUP_DIR, TALENT_BACKUP_DSN and TALENT_BACKUP_TOOLS from the environment file.
set -euo pipefail

ENV_FILE="${TALENT_ENV_FILE:-/etc/talent/backup.env}"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

: "${TALENT_BACKUP_DIR:?Set TALENT_BACKUP_DIR: where backups are kept, outside any git repository}"
: "${TALENT_BACKUP_DSN:?Set TALENT_BACKUP_DSN: the owner role, so the dump is complete}"

cd "${TALENT_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
export PYTHONPATH="${PYTHONPATH:-src}"
PYTHON="${TALENT_PYTHON:-.venv/bin/python}"

echo "=== $(date -Is) backup starting"
"$PYTHON" -m ops.backup --out "$TALENT_BACKUP_DIR" take --keep "${TALENT_BACKUP_KEEP:-14}"
"$PYTHON" -m ops.backup --out "$TALENT_BACKUP_DIR" files || {
  echo "warning: the CV files were not copied. The database backup is still good." >&2
}
# Once a week, prove the newest backup can be rebuilt on a machine with nothing on it. The
# systemd timer does this on its own; this is here for a machine running the script from cron.
if [ "${TALENT_BACKUP_DRILL_DAY:-Sun}" = "$(date +%a)" ] && [ "${TALENT_BACKUP_DRILL:-1}" = "1" ]; then
  echo "--- weekly restore drill: an empty machine"
  "$PYTHON" -m ops.backup --out "$TALENT_BACKUP_DIR" verify --fresh || {
    echo "error: the newest backup does not stand on its own. This is urgent." >&2
    exit 1
  }
fi

echo "=== $(date -Is) backup finished"
