#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${BASE_URL:-https://aivartba-production.up.railway.app}"

usage() {
  echo "Usage: $0 {read|update|bulk-delete}"
  echo
  echo "  read          read-only demo action        - expect AUTONOMOUS"
  echo "  update        single update demo action     - expect CONFIRM"
  echo "  bulk-delete   bulk irreversible delete demo  - expect FULL_REVIEW"
  exit 1
}

[ $# -eq 1 ] || usage

case "$1" in
  read)
    echo "Scenario: read-only   |  expected tier: AUTONOMOUS"
    echo
    python3 cli.py --base-url "$BASE_URL" \
      --agent-id agent-07 --action-type read --resource customers/42 \
      --reversibility read --affected-records 1 --regulatory none --params '{}'
    ;;
  update)
    echo "Scenario: single update   |  expected tier: CONFIRM"
    echo
    python3 cli.py --base-url "$BASE_URL" \
      --agent-id demo-agent --action-type update --resource orders/9981 \
      --reversibility update_without_snapshot --affected-records 1 --regulatory none --params '{}'
    ;;
  bulk-delete)
    echo "Scenario: bulk delete   |  expected tier: FULL_REVIEW"
    echo
    python3 cli.py --base-url "$BASE_URL" \
      --agent-id agent-07 --action-type delete --resource customers \
      --reversibility irreversible --affected-records 5000 --regulatory none --params '{"resource_id": 999}'
    ;;
  *)
    usage
    ;;
esac
