"""City platform stand-in: the upstream recipient. It verifies every
disclosure against the node's public key and keeps receipts, which act as
external anchors for the node's ledger. In baseline (cloud) mode it also
receives raw readings and evaluates the rules centrally."""
import json, os, sqlite3, threading
import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI, Header, HTTPException, Request
from .common import env, now, canon, sha256, FIELD
from .ledger import entry_hash
from . import rules as R

DATA = env("DATA_DIR", "/data"); os.makedirs(DATA, exist_ok=True)
CFG = R.load(env("RULES", "config/rules.yaml"))
TOKEN = env("CITY_TOKEN", "city")
db = sqlite3.connect(os.path.join(DATA, "city.db"), check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS raw(ts REAL, device TEXT, kind TEXT, zone TEXT, val REAL);
CREATE TABLE IF NOT EXISTS receipts(seq INTEGER PRIMARY KEY, entry_hash TEXT, received_ts REAL, stream TEXT, payload TEXT);
""")
lock = threading.Lock()
engine = R.RuleEngine(CFG["rules"])
S = {}
devices, households = set(), set()

def reset():
    global engine
    S.update(recv_bytes=0, raw_records=0, personal_records=0, disclosures=0, rejected=0, device_level_incidents=0)
    devices.clear(); households.clear(); engine = R.RuleEngine(CFG["rules"])
reset()

def pubkey():
    hx = open(os.path.join(env("CERT_DIR", "/certs"), "node_ed25519.pub")).read().strip()
    return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(hx))

app = FastAPI(title="City platform (stand-in)", version="0.1.0")

def auth(a):
    if (a or "").removeprefix("Bearer ").strip() != TOKEN:
        raise HTTPException(401, "bad token")

@app.post("/ingest/disclosure")
async def disclosure(req: Request, authorization: str = Header(None)):
    auth(authorization)
    body = await req.body(); S["recv_bytes"] += len(body)
    d = json.loads(body); e, p = d["entry"], d["payload"]
    try:
        if entry_hash(e) != e["entry_hash"] or sha256(canon(p)) != e["payload_sha256"]:
            raise InvalidSignature
        pubkey().verify(bytes.fromhex(e["sig"]), bytes.fromhex(e["entry_hash"]))
    except (InvalidSignature, ValueError, KeyError):
        S["rejected"] += 1
        raise HTTPException(400, "disclosure failed verification")
    with lock:
        db.execute("INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?)",
                   (e["seq"], e["entry_hash"], now(), e["stream"], json.dumps(p)))
        db.commit()
    S["disclosures"] += 1
    if "device" in p:
        S["device_level_incidents"] += 1; devices.add(p["device"])
    return {"receipt": e["entry_hash"], "seq": e["seq"]}

@app.post("/ingest/raw")
async def raw(req: Request, authorization: str = Header(None)):
    auth(authorization)
    body = await req.body(); S["recv_bytes"] += len(body)
    items = json.loads(body)["items"]; alerts = []
    with lock:
        for r in items:
            db.execute("INSERT INTO raw VALUES(?,?,?,?,?)",
                       (r["ts"], r["id"], r["kind"], r["zone"], r["v"][FIELD[r["kind"]]]))
            devices.add(r["id"])
            if r["kind"] == "water":
                S["personal_records"] += 1; households.add(r["id"])
            alerts += engine.feed(r)
        db.commit()
    S["raw_records"] += len(items)
    return {"stored": len(items), "alerts": alerts}

@app.get("/receipts")
def receipts(after: int = 0, limit: int = 5000, authorization: str = Header(None)):
    auth(authorization)
    with lock:
        rows = db.execute("SELECT seq, entry_hash FROM receipts WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
    return [{"seq": s, "entry_hash": h} for s, h in rows]

@app.get("/stats")
def stats(authorization: str = Header(None)):
    auth(authorization)
    return dict(S, distinct_devices=len(devices), distinct_households=len(households), now=now())

@app.post("/_ctl")
def ctl(body: dict, authorization: str = Header(None)):
    auth(authorization)
    if body.get("reset"):
        reset()
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(env("PORT", "8730")), log_level="warning")
