#!/usr/bin/env bash
# D-36: filenames-only secret scan. Never prints line content, never
# prints a matched value - this is the entire point (see the "eighth
# exposure" this exists to prevent: `grep -rn` over a tree containing
# .env printed real secret values to a transcript, four times in one
# session).
#
# Uses `command grep` explicitly, not whatever `grep` resolves to in the
# caller's shell - some environments alias grep to a gitignore-aware
# wrapper (e.g. ugrep --ignore-files), which would silently skip .env
# during a recursive scan and produce a false "clean" result. This
# script exclude-lists .env by NAME, explicitly, so its behavior does
# not depend on that.

set -euo pipefail

cd "$(dirname "$0")/.."

# Unambiguous patterns: specific key/URL prefixes with no legitimate
# collision anywhere in this repo. Applied everywhere.
UNAMBIGUOUS='npg_[A-Za-z0-9]{10,}|sk-proj-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|postgres(ql)?://[^ ]*:[^ @]*@|-----BEGIN [A-Z ]*PRIVATE KEY-----'

# Generic 40-char hex token: also matches a plain git commit SHA, which
# this governance-heavy repo references by the hundred in docs/tests as
# a full-length hex string. Tested against this repo before finalizing:
# applying it repo-wide false-
# positived on governance/**, tests/**, and *.md - a scanner that cries
# wolf on legitimate content gets disabled, which is the exact root
# cause this script exists to prevent. Scoped to the paths where a real
# secret could actually cause damage if it leaked there; governance
# docs/tests are covered by the unambiguous patterns above instead.
HEX_TOKEN='\b[0-9a-f]{40}\b'
HEX_SCOPED_PATHS=("app" "infra" "scripts" ".github" "Dockerfile" "railway.json" "pyproject.toml")

# Allowlist: files expected to hold real secret-shaped values locally.
# .env is gitignored/untracked by design (see README) - this is the
# ONLY entry. Nothing else should ever be added here without asking why
# a tracked file needs to hold something matching these patterns.
ALLOWLIST=(".env")

is_allowlisted() {
  local f="$1"
  for a in "${ALLOWLIST[@]}"; do
    [ "$f" = "./$a" ] && return 0
    [ "$f" = "$a" ] && return 0
  done
  return 1
}

matches="$(command grep -rlE "$UNAMBIGUOUS" . --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ 2>/dev/null || true)"
hex_matches=""
for p in "${HEX_SCOPED_PATHS[@]}"; do
  [ -e "$p" ] || continue
  found="$(command grep -rlE "$HEX_TOKEN" "$p" --exclude-dir=.git --exclude-dir=__pycache__ 2>/dev/null || true)"
  [ -n "$found" ] && hex_matches="$hex_matches
$found"
done
matches="$matches
$hex_matches"

violations=()
if [ -n "$(echo "$matches" | tr -d '[:space:]')" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    is_allowlisted "$f" || violations+=("$f")
  done <<< "$(echo "$matches" | sort -u)"
fi

if [ "${#violations[@]}" -gt 0 ]; then
  echo "SECRET SCAN: FAILED - secret-shaped content found outside the allowlist:" >&2
  printf '  %s\n' "${violations[@]}" >&2
  echo "(filenames only - values are never printed by this scanner)" >&2
  exit 1
fi

echo "SECRET SCAN: clean (only allowlisted files matched, or nothing matched)."
exit 0
