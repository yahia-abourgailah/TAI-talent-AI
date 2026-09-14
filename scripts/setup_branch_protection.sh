#!/usr/bin/env bash
# Applies the protection rules in docs/BRANCHING.md.
# Requires: gh CLI, authenticated, with admin on the repository.
#   usage: REPO=org/talent-platform bash scripts/setup_branch_protection.sh
set -euo pipefail

: "${REPO:?Set REPO=owner/name}"
command -v gh >/dev/null || { echo "gh CLI not found"; exit 1; }

protect () {
  local branch="$1" approvals="$2" linear="$3" admins="$4" checks="$5"
  echo "Protecting $branch..."
  gh api -X PUT "repos/$REPO/branches/$branch/protection" \
    --input - <<JSON
{
  "required_status_checks": { "strict": true, "contexts": $checks },
  "enforce_admins": $admins,
  "required_pull_request_reviews": {
    "required_approving_review_count": $approvals,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": $linear,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
}

ALL='["No candidate data or secrets","Lint and types","Unit tests","Golden replay parity","Integration tests"]'
CORE='["No candidate data or secrets","Lint and types","Unit tests","Golden replay parity"]'

protect main    2 true  true  "$ALL"
protect staging 1 false true  "$ALL"
protect dev     1 false false "$CORE"

echo "Setting default branch to dev"
gh api -X PATCH "repos/$REPO" -f default_branch=dev
echo "Done."
