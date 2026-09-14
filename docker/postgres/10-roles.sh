#!/bin/sh
# First boot of the local database only: creates the app login role.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$TALENT_DB_APP_PASSWORD" -f /talent/roles.sql
