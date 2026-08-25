#!/usr/bin/env bash
# Stop hook — monorepo change gate. Fires when Claude tries to finish a turn.
#
# If code changed this session (apps/, packages/, db/, tests/, evals/):
#   1. Static gate on the AFFECTED packages only:
#        TS  -> turbo lint+typecheck+test filtered to changed workspaces
#        PY  -> ruff + pytest per changed app (transcriber / assistant)
#        DB  -> reminder that migrations need the /new-migration flow (RLS test)
#      Any failure BLOCKS finishing with the errors.
#   2. Gate passed -> blocks ONCE with the REQUIRED steps: /verify-change smoke
#      + change-reviewer agent, then mark_verified.sh.
#
# Loop-safety: the .claude/.verify_state hash sentinel lets /ship and
# /epic-loop iterations finish once they've run their own gate and recorded
# mark_verified.sh — the hook never blocks the same verified state twice.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" 2>/dev/null || exit 0
ROOT="$(pwd)"
WATCH=(apps packages db tests evals)

changed="$(git status --porcelain -- "${WATCH[@]}" 2>/dev/null \
  | grep -E '\.(py|ts|tsx|js|jsx|sql|json)$' | awk '{print $NF}')"
[ -z "$changed" ] && exit 0

hash_state() {
  { git diff HEAD -- "${WATCH[@]}"; git status --porcelain -- "${WATCH[@]}"; } 2>/dev/null \
    | shasum | awk '{print $1}'
}
cur="$(hash_state)"
marked="$(cat .claude/.verify_state 2>/dev/null || true)"
[ "$cur" = "$marked" ] && exit 0

block() { jq -n --arg r "$1" '{decision:"block", reason:$r}'; exit 0; }

fail_out=""; fail=0

# --- TypeScript side (packages/*, apps/api) ----------------------------------
if printf '%s\n' "$changed" | grep -qE '^(packages/|apps/api/).*\.(ts|tsx|js|jsx|json)$'; then
  if command -v pnpm >/dev/null 2>&1; then
    ts_out="$(pnpm exec turbo run lint typecheck test --filter='...[HEAD]' --output-logs=errors-only 2>&1)"; ts_rc=$?
    if [ $ts_rc -ne 0 ]; then
      fail=1; fail_out="$fail_out"$'\n\n'"turbo lint+typecheck+test (affected):"$'\n'"$(printf '%s' "$ts_out" | tail -20)"
    fi
  fi
fi

# --- Python side (apps/transcriber, apps/assistant) --------------------------
for app in transcriber assistant; do
  if printf '%s\n' "$changed" | grep -qE "^apps/$app/.*\.py$"; then
    UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    py_out="$(cd "apps/$app" && $UV run ruff check . --fix 2>&1 && $UV run pytest -q -m "not slow" 2>&1)"; py_rc=$?
    if [ $py_rc -ne 0 ]; then
      fail=1; fail_out="$fail_out"$'\n\n'"apps/$app (ruff + pytest):"$'\n'"$(printf '%s' "$py_out" | tail -20)"
    fi
  fi
done

[ $fail -ne 0 ] && block "❌ Static gate FAILED — fix before finishing.$fail_out"

# --- DB migrations touched? ---------------------------------------------------
extra=""
if printf '%s\n' "$changed" | grep -qE '^db/.*\.sql$'; then
  extra=$'\n'"⚠ db/ migrations changed: the /new-migration flow is REQUIRED (apply from scratch locally + cross-tenant RLS isolation test)."
fi
if printf '%s\n' "$changed" | grep -qE '^packages/contracts/'; then
  extra="$extra"$'\n'"⚠ packages/contracts changed: confirm codegen ran (contracts_codegen hook) and consumer contract tests pass."
fi

printf -v reason '%s\n' \
  "✅ Static gate passed on the affected packages.$extra" \
  "" \
  "Two REQUIRED steps remain before finishing this change:" \
  "  1) Smoke — /verify-change: run the touched flow for real (service or compose+mocks)." \
  "  2) Review — change-reviewer agent on the diff (it delegates the clinical/PHI lens" \
  "     to medical-safety-reviewer when the diff touches clinical flows)." \
  "" \
  "When BOTH are done:  bash .claude/hooks/mark_verified.sh" \
  "Docs-only / intentional skip:  bash .claude/hooks/mark_verified.sh skip \"reason\"" \
  "(/ship and /epic-loop: run mark_verified.sh as the last step of your own gate.)"
block "$reason"
