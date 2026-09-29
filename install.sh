#!/usr/bin/env bash
# Install the skills in this repo into ~/.claude/skills/ as SYMLINKS, so a
# `git pull` is all it takes to pick up changes.
#
#   ./install.sh                 # every tier
#   ./install.sh core test       # only these tiers
#   ./install.sh --uninstall     # remove every link that points into this repo
#
# Safe to re-run. On each run it also:
#   - removes links to skills that no longer exist in the repo (renamed/deleted),
#   - moves an existing COPY of a skill (the old `cp -R` install) to
#     ~/.claude/skills.bak/<timestamp>/ before linking, never deletes it,
#   - installs git hooks (post-merge, post-rewrite) that re-run it after every
#     `git pull`, so NEW skills get linked without anyone remembering.
#
# Target directory: $CLAUDE_SKILLS_DIR, defaulting to ~/.claude/skills.
# Works with the bash 3.2 that ships with macOS.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
BACKUP_ROOT="${DEST%/}.bak"
HOOK_MARKER="# managed-by: agent-skills/install.sh"

QUIET=0
UNINSTALL=0
TIERS=()
for arg in "$@"; do
  case "$arg" in
    -q|--quiet) QUIET=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
    *) TIERS+=("$arg") ;;
  esac
done

say() { [ "$QUIET" = 1 ] || echo "$@"; }

# Resolve a link's target to an absolute, physical path (no readlink -f on
# older macOS).
resolve() {
  local target
  target="$(readlink "$1")"
  case "$target" in /*) ;; *) target="$(dirname "$1")/$target" ;; esac
  if [ -d "$target" ]; then (cd "$target" && pwd -P); else echo "$target"; fi
}

points_into_repo() {
  [ -L "$1" ] || return 1
  case "$(resolve "$1")" in "$REPO"/skills/*) return 0 ;; *) return 1 ;; esac
}

mkdir -p "$DEST"

# --- prune: links into this repo whose skill is gone (or everything, on uninstall)
for link in "$DEST"/*; do
  points_into_repo "$link" || continue
  if [ "$UNINSTALL" = 1 ] || [ ! -f "$(resolve "$link")/SKILL.md" ]; then
    rm "$link"
    say "removed  $(basename "$link")"
  fi
done

hooks_dir="$(git -C "$REPO" rev-parse --git-path hooks 2>/dev/null || true)"
case "$hooks_dir" in ""|/*) ;; *) hooks_dir="$REPO/$hooks_dir" ;; esac

if [ "$UNINSTALL" = 1 ]; then
  for hook in post-merge post-rewrite; do
    if [ -n "$hooks_dir" ] && grep -qs "$HOOK_MARKER" "$hooks_dir/$hook"; then
      rm "$hooks_dir/$hook"
      say "removed  git hook $hook"
    fi
  done
  exit 0
fi

# --- collect skills: skills/<tier>/<skill>/SKILL.md
EXPLICIT_TIERS="${#TIERS[@]}"
if [ "$EXPLICIT_TIERS" = 0 ]; then
  for d in "$REPO"/skills/*/; do TIERS+=("$(basename "$d")"); done
fi

names=""
linked=0
for tier in "${TIERS[@]}"; do
  if [ ! -d "$REPO/skills/$tier" ]; then
    echo "install.sh: no tier '$tier' in $REPO/skills" >&2
    exit 2
  fi
  for skill_md in "$REPO/skills/$tier"/*/SKILL.md; do
    [ -f "$skill_md" ] || continue
    src="$(dirname "$skill_md")"
    name="$(basename "$src")"

    # Claude Code needs the skills flat, so a name may exist in one tier only.
    case " $names " in
      *" $name "*) echo "install.sh: skill '$name' exists in more than one tier" >&2; exit 1 ;;
    esac
    names="$names $name"

    link="$DEST/$name"
    if [ -L "$link" ]; then
      if [ "$(resolve "$link")" = "$src" ]; then linked=$((linked + 1)); continue; fi
      if ! points_into_repo "$link"; then
        echo "skipped  $name: $link is a link to $(readlink "$link"), not this repo" >&2
        continue
      fi
      rm "$link"   # link into this repo, but to another tier/path: relink
    elif [ -e "$link" ]; then
      backup="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
      mkdir -p "$backup"
      mv "$link" "$backup/$name"
      say "backup   $name -> $backup/$name"
    fi
    ln -s "$src" "$link"
    say "linked   $name -> skills/$tier/$name"
    linked=$((linked + 1))
  done
done

# --- git hooks: re-run after `git pull` (merge/fast-forward and rebase).
# Pinned to THIS checkout, so a pull inside another worktree of the same repo
# never re-points ~/.claude/skills at it.
if [ -n "$hooks_dir" ]; then
  mkdir -p "$hooks_dir"
  tier_args=""   # only pin tiers the user chose; "all" must keep picking up new ones
  if [ "$EXPLICIT_TIERS" != 0 ]; then
    for t in "${TIERS[@]}"; do tier_args="$tier_args '$t'"; done
  fi
  for hook in post-merge post-rewrite; do
    file="$hooks_dir/$hook"
    if [ -e "$file" ] && ! grep -qs "$HOOK_MARKER" "$file"; then
      echo "skipped  git hook $hook: $file already exists and is not ours" >&2
      continue
    fi
    cat > "$file" <<EOF
#!/usr/bin/env bash
$HOOK_MARKER
# Re-links the skills after a pull so new/renamed ones show up in Claude Code.
[ "\$(git rev-parse --show-toplevel)" = "$REPO" ] || exit 0
[ "$hook" != post-rewrite ] || [ "\${1:-}" = rebase ] || exit 0
CLAUDE_SKILLS_DIR='$DEST' '$REPO/install.sh' --quiet$tier_args || true
EOF
    chmod +x "$file"
  done
fi

say "$linked skill(s) linked into $DEST"
