#!/bin/bash
# Add a CivicEdge tile to an existing gethomepage dashboard (services.yaml).
# Additive and reversible: the original is saved next to it as services.yaml.bak-civicedge.
cd "$(dirname "$0")/.."; . ./.env
HOST=${1:-100.90.206.118}
f=$(find /opt/homestack /mnt/data/config -maxdepth 5 -name services.yaml -not -path "*/node_modules/*" 2>/dev/null | head -1)
[ -z "$f" ] && { echo "homepage services.yaml not found; add this link by hand: http://$HOST:${PORTAL_PORT:-8700}/ui"; exit 0; }
if grep -q "CivicEdge" "$f"; then echo "homepage already has a CivicEdge entry ($f)"; exit 0; fi
cp -p "$f" "$f.bak-civicedge"
[ -n "$(tail -c1 "$f")" ] && echo >> "$f"
cat >> "$f" <<EOT
- Smart City:
    - CivicEdge:
        href: http://$HOST:${PORTAL_PORT:-8700}/ui?token=$OPERATOR_TOKEN
        description: Community edge node - live dashboard
        icon: mdi-home-city
EOT
echo "added CivicEdge to $f (backup: $f.bak-civicedge)"
