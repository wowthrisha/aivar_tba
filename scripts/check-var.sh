#!/usr/bin/env bash
# D-36: prints PRESENT/ABSENT for one variable on one platform, at most
# the first 6 characters - never the rest. The full value is captured
# into a shell variable and truncated before it is ever echoed; it is
# never passed through an intermediate command whose own output would
# print it in full (e.g. bare `railway variables`, bare
# `aws lambda get-function`).
#
# Usage: scripts/check-var.sh <railway|lambda|local> <VAR_NAME>

set -euo pipefail

PLATFORM="${1:-}"
VAR_NAME="${2:-}"

if [ -z "$PLATFORM" ] || [ -z "$VAR_NAME" ]; then
  echo "Usage: $0 <railway|lambda|local> <VAR_NAME>" >&2
  exit 2
fi

report() {
  local value="$1"
  # AWS CLI --output text renders a missing JMESPath key as the literal
  # string "None", not empty - handled here alongside empty/null so
  # ABSENT is reported correctly on all three platforms.
  if [ -z "$value" ] || [ "$value" = "null" ] || [ "$value" = "None" ]; then
    echo "${VAR_NAME}: ABSENT"
  else
    echo "${VAR_NAME}: PRESENT (${value:0:6}…)"
  fi
}

case "$PLATFORM" in
  railway)
    value="$(railway variables --json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('${VAR_NAME}', ''))
")"
    report "$value"
    ;;
  lambda)
    value="$(aws lambda get-function --function-name "${FUNCTION_NAME:-ps91-t15}" --region "${AWS_REGION:-us-east-1}" \
      --query "Configuration.Environment.Variables.${VAR_NAME}" --output text 2>/dev/null || true)"
    report "$value"
    ;;
  local)
    if [ ! -f .env ]; then
      echo "${VAR_NAME}: ABSENT (.env does not exist)"
      exit 0
    fi
    value="$(command grep -E "^${VAR_NAME}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
    report "$value"
    ;;
  *)
    echo "Unknown platform: $PLATFORM (expected railway|lambda|local)" >&2
    exit 2
    ;;
esac
