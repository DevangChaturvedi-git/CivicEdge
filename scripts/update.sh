#!/bin/bash
# Rebuild and restart the node after a code update (data and ledger are kept).
cd "$(dirname "$0")/.."; ./scripts/setup.sh >/dev/null
docker compose up -d --build 2>&1 | grep -E "Built|Started|Recreated|rror" | sort -u
sleep 8; . ./.env
docker compose exec -T core python -c "
import httpx
print('dashboard HTTP', httpx.get('http://localhost:8700/ui').status_code, '| mode', httpx.get('http://localhost:8700/health').json()['mode'])"
