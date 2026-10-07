#!/bin/bash
# Create .env with fresh random secrets (kept if it already exists).
cd "$(dirname "$0")/.."
[ -f .env ] && { echo ".env exists, keeping it"; exit 0; }
r() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
cat > .env <<EOT
FLEET_SECRET=$(r)
CORE_MQTT_PASSWORD=$(r)
OPERATOR_TOKEN=$(r)
RESIDENT_TOKEN=$(r)
INTERNAL_TOKEN=$(r)
CITY_TOKEN=$(r)
RATE_HZ=1
EVENT_EVERY_S=1.0
PORTAL_PORT=8700
# optional Matrix alerts (docker compose --profile matrix up -d notifier)
MATRIX_URL=http://matrix-synapse:8008
MATRIX_ROOM=
MATRIX_TOKEN=
EOT
chmod 600 .env; echo "wrote .env"
