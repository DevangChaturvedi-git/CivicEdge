#!/bin/bash
# Remove CivicEdge completely: containers, networks, volumes, image.
cd "$(dirname "$0")/.."
docker compose --profile experiment --profile matrix down -v --remove-orphans
docker image rm civicedge:0.1.0 2>/dev/null
echo "CivicEdge removed. Delete this folder to finish: rm -rf $(pwd)"
