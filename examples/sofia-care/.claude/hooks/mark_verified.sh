#!/usr/bin/env bash
# Records that the current change state passed the full gate (static checks +
# /verify-change smoke + change-reviewer). Writes the state hash so the Stop
# hook (require_checks.sh) lets the turn finish. Run only AFTER smoke + review.
#
# Usage:
#   bash .claude/hooks/mark_verified.sh            # both steps completed
#   bash .claude/hooks/mark_verified.sh skip "why" # intentional skip (logged)
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" 2>/dev/null || exit 1
WATCH=(apps packages db tests evals)

hash_state() {
  { git diff HEAD -- "${WATCH[@]}"; git status --porcelain -- "${WATCH[@]}"; } 2>/dev/null \
    | shasum | awk '{print $1}'
}

mkdir -p .claude
hash_state > .claude/.verify_state

if [ "${1:-}" = "skip" ]; then
  echo "$(date -u +%FT%TZ)  SKIP  ${2:-no reason given}" >> .claude/.verify_log
  echo "Recorded intentional skip of smoke+review for the current change state."
else
  echo "$(date -u +%FT%TZ)  DONE  smoke + review" >> .claude/.verify_log
  echo "Recorded: smoke + review complete. Finishing is now unblocked."
fi
