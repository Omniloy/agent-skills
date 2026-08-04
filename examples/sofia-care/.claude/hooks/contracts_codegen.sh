#!/usr/bin/env bash
# PostToolUse hook: keep generated types in sync with packages/contracts.
#
# When Claude edits a JSON Schema under packages/contracts/, regenerate the
# TypeScript + Pydantic types so no consumer ever builds against a stale
# contract (the #1 debt of the previous product: hand-mirrored wire types).
#
# Non-blocking: if codegen is unavailable or fails, it WARNS loudly on stderr
# so the transcript shows the types are stale — the Stop gate's contract tests
# will catch it hard.
set -uo pipefail

payload="$(cat)"
file_path="$(printf '%s' "$payload" | /usr/bin/python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("tool_input") or {}).get("file_path",""))' \
  2>/dev/null)"

case "$file_path" in
  */packages/contracts/*.json|*/packages/contracts/**/*.json) ;;
  *) exit 0 ;;
esac
case "$file_path" in
  */generated/*|*/dist/*) exit 0 ;;   # outputs, not sources
esac

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

# Try the canonical entrypoints in order (E0.1 scaffold conventions).
if command -v just >/dev/null 2>&1 && just --summary 2>/dev/null | grep -qw contracts-codegen; then
  just contracts-codegen >&2 2>&1 && { echo "contracts_codegen: types regenerated (just contracts-codegen)" >&2; exit 0; }
elif command -v pnpm >/dev/null 2>&1; then
  pnpm --filter @sofia-care/contracts run codegen >&2 2>&1 && { echo "contracts_codegen: types regenerated (pnpm)" >&2; exit 0; }
fi

echo "contracts_codegen: WARNING — $file_path changed but codegen did not run." >&2
echo "Generated TS/Pydantic types are now STALE. Run 'just contracts-codegen'" >&2
echo "(or 'pnpm --filter @sofia-care/contracts run codegen') before continuing." >&2
exit 0
