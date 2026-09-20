#!/usr/bin/env bash
# Promote dev to staging, and staging to main, the way docs/BRANCHING.md says: forward only,
# merges never rewrites, nothing force-pushed.
#
#   scripts/promote.sh staging     # dev      -> staging
#   scripts/promote.sh main        # staging  -> main
#
# main is the production branch and the one the front-end team integrates against, so it carries
# the API and not the development console: `web/` is left out of it, and the image stops copying
# it. That removal is made here, in the merge, so nobody has to remember it.
#
# Nothing is pushed. The script stops with the branch ready and the commands to push it.
set -euo pipefail

target="${1:-}"
case "$target" in
  staging) source_branch="dev" ;;
  main)    source_branch="staging" ;;
  *) echo "usage: scripts/promote.sh staging|main" >&2; exit 2 ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ -n "$(git status --porcelain)" ]; then
  echo "The working tree is not clean. Commit or stash first." >&2
  exit 1
fi
here="$(git rev-parse --abbrev-ref HEAD)"
trap 'git checkout --quiet "$here"' EXIT

git rev-parse --verify --quiet "$source_branch" >/dev/null || {
  echo "No $source_branch branch here." >&2; exit 1; }

if ! git rev-parse --verify --quiet "$target" >/dev/null; then
  echo "Creating $target from $source_branch."
  git branch "$target" "$source_branch"
else
  git checkout --quiet "$target"
  # main keeps `web/` deleted. A change to the console on dev is then a modify/delete conflict,
  # and the answer is always the same: it stays deleted here.
  if ! git merge --no-edit "$source_branch"; then
    conflicted="$(git diff --name-only --diff-filter=U)"
    outside_web="$(echo "$conflicted" | grep -v '^web/' || true)"
    if [ "$target" != "main" ] || [ -n "$outside_web" ]; then
      echo "Merge conflict that is not the console. Resolve it by hand:" >&2
      echo "$conflicted" >&2
      exit 1
    fi
    git rm -r --quiet --force web
    git commit --no-edit --quiet
  fi
fi
git checkout --quiet "$target"

if [ "$target" = "main" ]; then
  # The console and the example careers page are a developer's tool. They stay on dev.
  if [ -d web ]; then
    git rm -r --quiet web
  fi
  if grep -q '^COPY web ./web$' Dockerfile; then
    python3 - <<'PY'
from pathlib import Path

docker = Path("Dockerfile")
text = docker.read_text(encoding="utf-8")
text = text.replace(
    "# The development console and the example careers page. Mounted only when TALENT_ENV=dev.\n"
    "COPY web ./web\n",
    "",
)
docker.write_text(text, encoding="utf-8")

ignore = Path(".dockerignore")
text = ignore.read_text(encoding="utf-8")
text = text.replace(
    "# The development console and the example careers page: static files, no data, and the API "
    "only\n# mounts them when TALENT_ENV=dev.\n!web/\n",
    "",
)
ignore.write_text(text, encoding="utf-8")
PY
    git add Dockerfile .dockerignore
  fi
  if [ -n "$(git status --porcelain)" ]; then
    git commit --quiet -m "Release: the API without the development console

main is what runs in production and what the front-end team integrates against. The
console and the example careers page are a developer's tool for trying the flows by
hand; they stay on dev, and the image no longer copies them.

Nothing else differs from staging: same code, same contract, same migrations."
  fi
fi

echo
echo "$target is ready at $(git rev-parse --short "$target")."
git --no-pager log --oneline "$source_branch..$target" | head -5 || true
echo
echo "Check it, then push:"
echo "    git push origin $target && git push yahia $target"
