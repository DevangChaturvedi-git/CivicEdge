# CivicEdge

A local-first community edge node for smart-city IoT. Sensor data is processed
and kept inside the neighbourhood; only what a written policy permits leaves
the node, and every release is recorded in a signed, tamper-evident ledger
that residents can audit.

*Local by default; shared by purpose.*

## What it does

| Part | Role |
|---|---|
| `broker` | MQTT over TLS. One identity per device; a device can publish only to its own topic. |
| `core` | Validates readings, stores them locally, evaluates rules, raises alerts. Has no network route off the node. |
| `gateway` | The only egress path. Enforces `config/policy.yaml`, signs every release/suppress decision into the ledger, stores-and-forwards across WAN outages. |
| `portal` | Single published port (8700) for residents and operators. |
| `city` | Stand-in for the city platform. Verifies each disclosure and keeps receipts. |
| `wan` | The node-to-city link, with injectable delay, jitter and outage. |
| `emulator` | 111 emulated devices (air quality, household water meters, waste bins). Swap for real hardware; nothing else changes. |
| `notifier` | Optional: community alerts into a Matrix room. |

## Dashboard

Open `http://HOST:8700/ui` and enter a token. Operators also get live controls: switch between CivicEdge and the cloud-centric baseline, change the WAN delay, and cut the WAN link.

## Run

```
./scripts/setup.sh          # writes .env with random secrets
docker compose up -d
```

Operator and resident tokens are in `.env`.

```
curl -H "Authorization: Bearer $RESIDENT_TOKEN" http://HOST:8700/alerts
curl -H "Authorization: Bearer $RESIDENT_TOKEN" http://HOST:8700/zones/summary
curl -H "Authorization: Bearer $RESIDENT_TOKEN" http://HOST:8700/ledger
curl -H "Authorization: Bearer $RESIDENT_TOKEN" http://HOST:8700/ledger/verify
```

Independent audit from any machine:

```
python -m civicedge.cli verify --node http://HOST:8700 --token $RESIDENT_TOKEN
```

## Policy

`config/policy.yaml` is default-deny. A stream leaves the node only as a
windowed aggregate, only for its stated purpose, and only if at least
`min_devices` devices contributed (smaller groups are withheld, and the
withholding is itself logged). Incidents leave with a whitelisted set of
fields. Household leak alerts have no policy entry, so they never leave.

## Evaluate

```
./scripts/run_all.sh
```

Runs the cloud-centric baseline and CivicEdge on the same live fleet and
writes `results/`: alert latency against WAN round-trip time, data leaving the
node, behaviour through a WAN outage, resource use, and ledger cost.

## Remove

```
./scripts/teardown.sh
```

## Limits

Sensors and the WAN link are emulated. At-rest encryption is left to the host
volume. The city platform is a stand-in, not a real municipal system.
