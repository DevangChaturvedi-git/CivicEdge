import hashlib, hmac, json, os, time

def env(k, d=None):
    return os.environ.get(k, d)

def now():
    return time.time()

def canon(obj) -> bytes:
    """Canonical JSON used for hashing and signing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

# ---- fleet definition (shared by the init job and the emulator) ----------
ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]
PER_ZONE = {"air": 5, "water": 10, "bin": 5}
SMALL_ZONE = {"air": 2, "water": 4, "bin": 5}   # Z6 is deliberately sparse

def fleet():
    out = []
    for z in ZONES:
        counts = SMALL_ZONE if z == "Z6" else PER_ZONE
        for kind, n in counts.items():
            for i in range(1, n + 1):
                out.append({"id": f"{kind}-{z.lower()}-{i:02d}", "kind": kind, "zone": z})
    return out

def device_password(secret: str, device_id: str) -> str:
    return hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).hexdigest()[:32]

FIELD = {"air": "pm25", "water": "flow_lpm", "bin": "fill_pct"}
