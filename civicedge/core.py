"""Edge core: ingest, validate, store, evaluate rules, raise alerts.
It sits only on internal networks and has no route off the node."""
import json, os, sqlite3, ssl, threading, time
from collections import deque
import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from .common import env, now, FIELD
from . import rules as R

DATA = env("DATA_DIR", "/data"); os.makedirs(DATA, exist_ok=True)
CFG = R.load(env("RULES", "config/rules.yaml"))
TOK = {env("OPERATOR_TOKEN", "op"): "operator", env("RESIDENT_TOKEN", "res"): "resident",
       env("INTERNAL_TOKEN", "int"): "internal"}
PRIVATE_RULES = {"water_leak"}          # household alerts: operator/household only

db = sqlite3.connect(os.path.join(DATA, "core.db"), check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=NORMAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY, ts REAL, device TEXT, kind TEXT, zone TEXT, val REAL);
CREATE INDEX IF NOT EXISTS r_kt ON readings(kind, ts);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, rule TEXT, kind TEXT, device TEXT, zone TEXT,
  severity TEXT, value REAL, trigger_ts REAL, created_ts REAL, origin TEXT);
""")
lock = threading.Lock()
S = {"mode": env("MODE", "civicedge"), "readings": 0, "bytes_in": 0, "rejected": 0,
     "alerts_local": 0, "alerts_remote": 0, "started": now()}
engine = R.RuleEngine(CFG["rules"])
raw = deque(maxlen=200000); raw_id = 0

def add_alert(a, origin):
    with lock:
        db.execute("INSERT INTO alerts(rule,kind,device,zone,severity,value,trigger_ts,created_ts,origin)"
                   " VALUES(?,?,?,?,?,?,?,?,?)", (a["rule"], a["kind"], a["device"], a["zone"],
                   a["severity"], a["value"], a["trigger_ts"], now(), origin))
        db.commit()
    S["alerts_local" if origin == "local" else "alerts_remote"] += 1

def on_message(c, u, m):
    global raw_id
    S["bytes_in"] += len(m.payload)
    try:
        r = json.loads(m.payload)
    except ValueError:
        S["rejected"] += 1; return
    # broker ACL binds topic to identity; reject payloads claiming another id
    if R.validate(r, CFG["ranges"]) or m.topic != f"ce/{r['id']}/telemetry":
        S["rejected"] += 1; return
    S["readings"] += 1
    with lock:
        db.execute("INSERT INTO readings(ts,device,kind,zone,val) VALUES(?,?,?,?,?)",
                   (r["ts"], r["id"], r["kind"], r["zone"], r["v"][FIELD[r["kind"]]]))
        raw_id += 1; raw.append((raw_id, r))
    if S["mode"] == "civicedge":
        for a in engine.feed(r):
            add_alert(a, "local")

def committer():
    while True:
        time.sleep(0.2)
        with lock:
            db.commit()

def mqtt_loop():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="core")
    c.username_pw_set("core", env("CORE_MQTT_PASSWORD", "change-me-core"))
    c.tls_set(ca_certs=os.path.join(env("CERT_DIR", "/certs"), "ca.crt"), cert_reqs=ssl.CERT_REQUIRED)
    c.on_connect = lambda cl, u, f, rc, p=None: cl.subscribe("ce/+/telemetry", qos=0)
    c.on_message = on_message
    c.reconnect_delay_set(1, 5)
    while True:
        try:
            c.connect(env("BROKER_HOST", "broker"), int(env("BROKER_PORT", "8883")), 30); break
        except OSError:
            time.sleep(1)
    c.loop_forever(retry_first_connection=True)

app = FastAPI(title="CivicEdge core", version="0.1.0")

def role(auth, *allowed):
    r = TOK.get((auth or "").removeprefix("Bearer ").strip())
    if r not in allowed:
        raise HTTPException(403 if r else 401, "not permitted")
    return r

@app.get("/health")
def health():
    return {"ok": True, "mode": S["mode"]}

UI = os.path.join(os.path.dirname(__file__), "ui.html")

@app.get("/")
def root():
    return RedirectResponse("/ui")

@app.get("/ui", response_class=HTMLResponse)
def ui():                      # static page; all data it shows is fetched with the viewer's token
    return open(UI, encoding="utf8").read()

@app.get("/whoami")
def whoami(authorization: str = Header(None)):
    return {"role": role(authorization, "operator", "resident")}

@app.get("/stats")
def stats(authorization: str = Header(None)):
    role(authorization, "operator", "internal")
    return dict(S, raw_head=raw_id, now=now())

@app.get("/alerts")
def alerts(after_id: int = 0, limit: int = 1000, last: int = 0, authorization: str = Header(None)):
    r = role(authorization, "operator", "resident", "internal")
    q = "SELECT id,rule,kind,device,zone,severity,value,trigger_ts,created_ts,origin FROM alerts "
    with lock:
        if last:
            rows = db.execute(q + "ORDER BY id DESC LIMIT ?", (min(last, 500),)).fetchall()[::-1]
        else:
            rows = db.execute(q + "WHERE id>? ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
    keys = ["id", "rule", "kind", "device", "zone", "severity", "value", "trigger_ts", "created_ts", "origin"]
    out = [dict(zip(keys, x)) for x in rows]
    if r == "resident":   # residents see community alerts, never another household's
        out = [{k: v for k, v in a.items() if k != "device"} for a in out if a["rule"] not in PRIVATE_RULES]
    return out

def aggregate(kind, start, end):
    with lock:
        db.commit()
        rows = db.execute("SELECT zone, COUNT(DISTINCT device), AVG(val), MAX(val), COUNT(*) FROM readings"
                          " WHERE kind=? AND ts>=? AND ts<? GROUP BY zone ORDER BY zone",
                          (kind, start, end)).fetchall()
    return [{"zone": z, "n_devices": n, "mean": round(m, 2), "max": round(mx, 2), "count": c}
            for z, n, m, mx, c in rows]

@app.get("/zones/summary")
def zone_summary(kind: str = "air", seconds: int = 60, authorization: str = Header(None)):
    role(authorization, "operator", "resident")
    t = now()
    return [g for g in aggregate(kind, t - seconds, t) if g["n_devices"] >= 3]

@app.get("/internal/aggregate")
def internal_aggregate(kind: str, start: float, end: float, authorization: str = Header(None)):
    role(authorization, "internal")
    return aggregate(kind, start, end)

@app.get("/internal/raw")
def internal_raw(after_id: int = 0, limit: int = 500, authorization: str = Header(None)):
    role(authorization, "internal")
    with lock:
        if not raw or after_id >= raw_id:
            return {"head": raw_id, "items": []}
        first = raw[0][0]
        i = max(0, after_id + 1 - first)
        items = [raw[j] for j in range(i, min(len(raw), i + limit))]
    return {"head": items[-1][0] if items else raw_id, "items": [x[1] for x in items]}

@app.post("/internal/alerts")
def internal_alerts(items: list[dict], authorization: str = Header(None)):
    role(authorization, "internal")
    for a in items:
        add_alert(a, "cloud")
    return {"stored": len(items)}

@app.post("/_ctl")
def ctl(body: dict, authorization: str = Header(None)):
    role(authorization, "internal")
    global engine
    if body.get("mode") in ("civicedge", "cloud"):
        S["mode"] = body["mode"]; engine = R.RuleEngine(CFG["rules"])
    return {"mode": S["mode"], "raw_head": raw_id}

def main():
    threading.Thread(target=committer, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(env("PORT", "8700")), log_level="warning")

if __name__ == "__main__":
    main()
