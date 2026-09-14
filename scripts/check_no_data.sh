#!/usr/bin/env bash
# Fails the build if candidate data or credentials are tracked.
# See docs/DATA_HANDLING.md.
set -euo pipefail

FORBIDDEN_EXT='\.(xlsx|xls|xlsm|csv|pdf|docx|jsonl|pem|key|p12)$'
FORBIDDEN_NAME='(service_account|credentials|oauth|token|secret)'
fail=0

echo "Scanning tracked files..."

if hits=$(git ls-files | grep -iE "$FORBIDDEN_EXT" || true); [ -n "$hits" ]; then
  echo "::error::Data files are tracked. This repository is code only."
  echo "$hits" | sed 's/^/  /'
  fail=1
fi

if hits=$(git ls-files | grep -iE "$FORBIDDEN_NAME" | grep -iE '\.json$|\.ya?ml$' || true); [ -n "$hits" ]; then
  echo "::error::Credential-shaped files are tracked."
  echo "$hits" | sed 's/^/  /'
  fail=1
fi

# Egyptian mobile numbers and long digit runs in source files
if hits=$(git ls-files -- '*.py' '*.js' '*.ts' '*.md' '*.json' \
          | xargs -r grep -nE '(^|[^0-9])01[0-9]{9}([^0-9]|$)' 2>/dev/null || true); [ -n "$hits" ]; then
  echo "::error::Possible Egyptian phone numbers found in source."
  echo "$hits" | head -20 | sed 's/^/  /'
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "Clean: no data or credential files tracked."
fi
exit "$fail"
