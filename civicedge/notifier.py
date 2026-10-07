"""Optional: push community alerts into a Matrix room (e.g. the node owner's
existing Synapse/Element). Household-private alerts are never posted."""
import time, uuid
import httpx
from .common import env

def main():
    core = httpx.Client(base_url=env("CORE_URL", "http://core:8700"), timeout=5,
                        headers={"Authorization": "Bearer " + env("RESIDENT_TOKEN", "res")})
    hs, room, tok = env("MATRIX_URL", "http://matrix-synapse:8008"), env("MATRIX_ROOM"), env("MATRIX_TOKEN")
    if not (room and tok):
        print("notifier: MATRIX_ROOM / MATRIX_TOKEN not set, idle", flush=True)
        while True:
            time.sleep(3600)
    last = 0
    try:
        a = core.get("/alerts", params={"after_id": 0, "limit": 100000}).json()
        last = a[-1]["id"] if a else 0     # do not replay history
    except httpx.HTTPError:
        pass
    while True:
        time.sleep(1)
        try:
            for a in core.get("/alerts", params={"after_id": last}).json():
                last = a["id"]
                text = f"[CivicEdge] {a['rule'].replace('_', ' ')} in zone {a['zone']} (severity {a['severity']}, value {a['value']})"
                httpx.put(f"{hs}/_matrix/client/v3/rooms/{room}/send/m.room.message/{uuid.uuid4().hex}",
                          headers={"Authorization": "Bearer " + tok},
                          json={"msgtype": "m.text", "body": text}, timeout=5)
        except httpx.HTTPError:
            time.sleep(3)

if __name__ == "__main__":
    main()
