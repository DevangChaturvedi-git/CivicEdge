#!/bin/bash
# Build, start the node, run the full evaluation, leave the node running.
set -u; cd "$(dirname "$0")/.."
./scripts/setup.sh
mkdir -p results; rm -f results/results.json results/ledger_bench.json results/stats.csv
echo "== build"; docker compose build 2>&1 | tail -3
echo "== up";    docker compose up -d 2>&1 | tail -15
{ lscpu | grep -E 'Model name|^CPU\(s\)|Thread|max MHz'; free -m | head -2; uname -r; docker --version; docker compose version; } > results/host.txt 2>&1
sleep 20
. ./.env
if ! docker compose exec -T core python -c "
import httpx,sys
s=httpx.get('http://localhost:8700/stats',headers={'Authorization':'Bearer $INTERNAL_TOKEN'}).json()
print('smoke:',s['readings'],'readings ingested'); sys.exit(0 if s['readings']>100 else 1)"; then
  echo "SMOKE TEST FAILED"; docker compose ps -a > results/ps.txt 2>&1; docker compose logs --tail 40 > results/logs.txt 2>&1; tail -60 results/logs.txt; exit 1
fi
( while true; do t=$(date +%s); docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep '^civicedge' | sed "s/^/$t,/"; sleep 4; done ) > results/stats.csv 2>/dev/null &
SP=$!
echo "== experiments (about 17 minutes)"
docker compose --profile experiment run --rm -T runner
echo "== ledger benchmark"
docker compose --profile experiment run --rm -T runner python -m experiments.bench_ledger /results/ledger_bench.json
kill $SP 2>/dev/null
docker compose ps -a > results/ps.txt 2>&1; docker compose logs --tail 25 > results/logs.txt 2>&1
docker image inspect civicedge:0.1.0 --format '{{.Size}}' > results/image_size.txt 2>&1
echo "== finished; node still running on port ${PORTAL_PORT:-8700}"
