#!/usr/bin/env bash
# PostToolUse hook: auto-format the file Claude just edited, by language.
#   .py            -> ruff format + ruff check --fix   (uv workspace / app .venv)
#   .ts/.tsx/.js*  -> prettier --write + eslint --fix  (pnpm workspace)
#   .json/.css/.md -> prettier --write
# Non-blocking: always exits 0; problems surface on stderr only.
set -uo pipefail

payload="$(cat)"
file_path="$(printf '%s' "$payload" | /usr/bin/python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("tool_input") or {}).get("file_path",""))' \
  2>/dev/null)"
[ -n "$file_path" ] && [ -f "$file_path" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
case "$file_path" in
  */node_modules/*|*/dist/*|*/.venv/*|*/generated/*) exit 0 ;;
esac

case "$file_path" in
  *.py)
    UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    if [ -x "$ROOT/.venv/bin/ruff" ]; then RUFF="$ROOT/.venv/bin/ruff"; else RUFF="$UV run ruff"; fi
    # shellcheck disable=SC2086
    $RUFF format "$file_path" >&2 2>&1 || echo "format_fix: ruff format failed on $file_path" >&2
    # shellcheck disable=SC2086
    $RUFF check --fix "$file_path" >&2 2>&1 || echo "format_fix: ruff check --fix reported issues on $file_path" >&2
    ;;
  *.ts|*.tsx|*.js|*.jsx)
    command -v pnpm >/dev/null 2>&1 || exit 0
    (cd "$ROOT" && pnpm exec prettier --write "$file_path") >&2 2>&1 || echo "format_fix: prettier failed on $file_path" >&2
    (cd "$ROOT" && pnpm exec eslint --fix "$file_path") >&2 2>&1 || true
    ;;
  *.json|*.css|*.md)
    command -v pnpm >/dev/null 2>&1 || exit 0
    (cd "$ROOT" && pnpm exec prettier --write "$file_path") >&2 2>&1 || true
    ;;
esac
exit 0
