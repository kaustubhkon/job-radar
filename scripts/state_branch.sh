#!/usr/bin/env bash
# Keep Job Radar's state (seen.json, runs/) on an orphan `state` branch, checked out at ./state,
# so main's history stays clean.
#
#   scripts/state_branch.sh checkout   # before the run
#   scripts/state_branch.sh push       # after the run
set -euo pipefail

BRANCH="${STATE_BRANCH:-state}"
DIR="${STATE_DIR:-state}"

case "${1:-}" in
  checkout)
    rm -rf "$DIR"
    git worktree prune
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch --depth=1 origin "$BRANCH"
      git worktree add --detach "$DIR" FETCH_HEAD
    else
      echo "No '$BRANCH' branch yet; creating it (first run)."
      git worktree add --detach "$DIR"
      git -C "$DIR" checkout --orphan "$BRANCH"
      git -C "$DIR" rm -rf --quiet . >/dev/null 2>&1 || true
      git -C "$DIR" clean -fdx --quiet
      printf '# Job Radar state\n\nWritten by the workflow. `seen.json` tracks postings already sent; `runs/` has a JSON log and CSV per run.\n' > "$DIR/README.md"
    fi
    ;;
  push)
    cd "$DIR"
    git add -A
    if git diff --cached --quiet; then
      echo "State unchanged."
      exit 0
    fi
    git -c user.name="job-radar[bot]" -c user.email="job-radar@users.noreply.github.com" \
      commit --quiet -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
    git push origin "HEAD:refs/heads/$BRANCH"
    ;;
  *)
    echo "usage: $0 checkout|push" >&2
    exit 2
    ;;
esac
