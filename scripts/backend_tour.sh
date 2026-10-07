#!/bin/bash
# A guided look behind the dashboard. Each step runs a real command against the
# running node and waits for Enter. Nothing here changes the node permanently.
cd "$(dirname "$0")/.."; . ./.env
dc() { docker compose "$@"; }
step() { echo; echo "================================================================"; echo "STEP $1"; echo "----------------------------------------------------------------"; }
pause() { echo; read -r -p "[Enter for next step] " _; }
py() { dc exec -T "$1" python -c "$2"; }

step "1. The services that are running (separate containers)"
echo '$ docker compose ps'
dc ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
pause

step "2. Live sensor messages on the MQTT broker (encrypted, authenticated)"
echo "Subscribing as the core service and printing the next 6 messages as they arrive:"
dc exec -T broker mosquitto_sub -h localhost -p 8883 --cafile /certs/ca.crt -u core -P "$CORE_MQTT_PASSWORD" -t 'ce/#' -C 6 -v
pause

step "3. The broker rejects a client with a wrong password"
dc exec -T broker mosquitto_sub -h localhost -p 8883 --cafile /certs/ca.crt -u air-z1-01 -P wrong-password -t 'ce/#' -C 1 -W 3 2>&1 | head -3
pause

step "4. Readings are being written to the node's local database right now"
Q="import sqlite3,time
d=sqlite3.connect('file:/data/core.db?mode=ro',uri=True)
a=d.execute('select count(*) from readings').fetchone()[0]; time.sleep(3)
b=d.execute('select count(*) from readings').fetchone()[0]
print('rows in readings table:',a,'-> 3 seconds later:',b,'(+%d)'%(b-a))
print('latest rows (timestamp, device, kind, zone, value):')
for r in d.execute('select ts,device,kind,zone,val from readings order by id desc limit 5'): print('  ',r)"
py core "$Q"
pause

step "5. The edge core has no network route off the node; the networks are internal"
py core "import socket
try:
    socket.create_connection(('1.1.1.1',443),3); print('core reached the internet (unexpected)')
except OSError as e: print('core -> internet:', e)"
for n in field edge uplink citynet; do echo "network civicedge_$n internal = $(docker network inspect civicedge_$n --format '{{.Internal}}')"; done
pause

step "6. The disclosure policy file the gateway enforces"
cat config/policy.yaml
pause

step "7. The last entries of the signed ledger, read straight from its database"
py gateway "import sqlite3
d=sqlite3.connect('file:/data/ledger.db?mode=ro',uri=True)
for r in d.execute('select seq,decision,stream,purpose,n_devices,bytes,status,substr(prev_hash,1,10),substr(entry_hash,1,10) from entries order by seq desc limit 6'): print(r)
print('(columns: seq, decision, stream, purpose, devices, bytes, status, prev_hash, entry_hash)')"
pause

step "8. Tamper test: change one stored ledger entry, verify, then put it back"
V="import httpx;print(httpx.get('http://localhost:8710/ledger/verify',headers={'Authorization':'Bearer $INTERNAL_TOKEN'}).json())"
T="import sqlite3,sys
d=sqlite3.connect('/data/ledger.db'); d.execute('update entries set n_devices=n_devices+(?) where seq=5',(int(sys.argv[1]),)); d.commit()"
echo "before:   $(py gateway "$V")"
dc exec -T gateway python -c "$T" 1
echo "tampered: $(py gateway "$V")"
dc exec -T gateway python -c "$T" -1
echo "restored: $(py gateway "$V")"
pause

step "9. What the city side has actually stored"
py city "import sqlite3
d=sqlite3.connect('file:/data/city.db?mode=ro',uri=True)
print('raw readings stored (all time, from baseline runs):',d.execute('select count(*) from raw').fetchone()[0])
print('signed disclosures stored:',d.execute('select count(*) from receipts').fetchone()[0])
print('latest disclosure payloads:')
for r in d.execute('select seq,stream,payload from receipts order by seq desc limit 3'): print('  ',r)"
echo; echo "End of tour. Source code is in /opt/civicedge/civicedge/"
