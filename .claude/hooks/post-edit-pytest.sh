#!/bin/bash
# PostToolUse hook: runs pytest after Edit/Write under app/.
# PostToolUse cannot block (tool already ran) — this is observability only,
# per Claude Code hooks docs (https://code.claude.com/docs/en/hooks.md).
set -e

FILE=$(jq -r '.tool_input.file_path // empty')

case "$FILE" in
  "${CLAUDE_PROJECT_DIR}"/app/*) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR}" && pytest -q
exit $?
