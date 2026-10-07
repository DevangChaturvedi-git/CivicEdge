"""Disclosure gateway: the only component with a path off the node.

civicedge mode: releases only what config/policy.yaml permits (windowed
aggregates above a k-device threshold, and whitelisted incident fields),
records every release/suppress decision in the signed ledger, and
stores-and-forwards across WAN outages.
cloud mode (baseline for evaluation): forwards every raw reading upstream and
relays the alerts the cloud computes."""
import hashlib, json, os, threading, time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import httpx, uvicorn, yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI, Header, HTTPException
from .common import env, now, canon
from .ledger import Ledger, verify_chain, FIELDS

DATA = env("DATA_DIR", "/data"); os.makedirs(DATA, exist_ok=True)
POLICY_RAW = open(env("POLICY", "config/policy.yaml"), "rb").read()
POLICY = yaml.safe_load(POLICY_RAW); POLICY_HASH = hashlib.sha256(POLICY_RAW).hexdigest()
KEY = ed25519.Ed25519PrivateKey.from_private_bytes(
    open(os.path.join(env("KEY_DIR", "/keys"), "node_ed25519.key"), "rb").read())
PUB = KEY.public_key()
PUB_HEX = PUB.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
ledger = Ledger(os.path.join(DATA, "ledger.db"), KEY)
CORE, WAN = env("CORE_URL", "http://core:8700"), env("WAN_URL", "http://wan:8720")
H_CORE = {"Authorization": "Bearer " + env("INTERNAL_TOKEN", "int")}
H_CITY = {"Authorization": "Bearer " + env("CITY_TOKEN", "city"), "Content-Type": "application/json"}
TOK = {env("OPERATOR_TOKEN", "op"): "operator", env("RESIDENT_TOKEN", "res"): "resident",
       env("INTERNAL_TOKEN", "int"): "internal"}
WINDOW = float(env("WINDOW_S", POLICY["window_s"]))
S = {"mode": env("MODE", "civicedge"), "out_msgs": 0, "out_bytes": 0, "out_device_records": 0,
     "send_failures": 0, "released": 0, "suppressed": 0, "last_ok": 0}
cur = {"raw": 0, "raw_ok": 0, "alert": 0, "win": 0.0}
PIPELINE = int(env("PIPELINE", "4"))
core = httpx.Client(base_url=CORE, headers=H_CORE, timeout=5)
wan = httpx.Client(base_url=WAN, headers=H_CITY, timeout=4,
                   limits=httpx.Limits(max_connections=16))

def record(decision, stream, purpose, w0, w1, n, payload):
    S["released" if decision == "release" else "suppressed"] += 1
    return ledger.append(ts=now(), decision=decision, stream=stream, purpose=purpose,
                         recipient=POLICY["recipient"], policy_hash=POLICY_HASH,
                         window_start=w0, window_end=w1, n_devices=n, payload=payload)

def window_loop():
    while True:
        time.sleep(0.25)
        if S["mode"] != "civicedge":
            continue
        if cur["win"] == 0:
            cur["win"] = (now() // WINDOW) * WINDOW + WINDOW
        w0 = cur["win"]; w1 = w0 + WINDOW
        if now() < w1 + 1.0:      # grace for late readings
            continue
        try:
            for stream, pol in POLICY["streams"].items():
                groups = core.get("/internal/aggregate", params={"kind": stream, "start": w0, "end": w1}).json()
                ok = [g for g in groups if g["n_devices"] >= pol["min_devices"]]
                for g in groups:
                    if g["n_devices"] < pol["min_devices"]:   # k-threshold: withhold, but log that we did
                        record("suppress", f"{stream}/{g['zone']}", pol["purpose"], w0, w1, g["n_devices"], None)
                if ok:
                    payload = {"type": "aggregate", "stream": stream, "purpose": pol["purpose"],
                               "window": [w0, w1],
                               "groups": [{"zone": g["zone"], **{k: g[k] for k in pol["stats"]}} for g in ok]}
                    record("release", stream, pol["purpose"], w0, w1, sum(g["n_devices"] for g in ok), payload)
            cur["win"] = w1
        except (httpx.HTTPError, ValueError):
            time.sleep(1)

def event_loop():
    while True:
        time.sleep(0.2)
        if S["mode"] != "civicedge":
            continue
        try:
            for a in core.get("/alerts", params={"after_id": cur["alert"]}).json():
                cur["alert"] = a["id"]
                pol = POLICY["events"].get(a["rule"])
                if pol is None:      # default deny, e.g. household leak alerts
                    record("suppress", a["rule"], "none", a["trigger_ts"], a["trigger_ts"], 1, None)
                    continue
                payload = {"type": "incident", "rule": a["rule"], "purpose": pol["purpose"],
                           "ts": a["trigger_ts"], **{k: a[k] for k in pol["fields"]}}
                record("release", a["rule"], pol["purpose"], a["trigger_ts"], a["trigger_ts"], 1, payload)
        except (httpx.HTTPError, ValueError):
            time.sleep(1)

def outbox_loop():      # store-and-forward, strictly in ledger order
    while True:
        batch = ledger.pending(20) if S["mode"] == "civicedge" else []
        if not batch:
            time.sleep(0.1); continue
        for e in batch:
            payload = json.loads(e["payload"])
            body = canon({"entry": {k: e[k] for k in FIELDS + ["entry_hash", "sig"]}, "payload": payload})
            try:
                r = wan.post("/ingest/disclosure", content=body); r.raise_for_status()
            except httpx.HTTPError:
                S["send_failures"] += 1; time.sleep(1); break
            ledger.mark_sent(e["seq"], now())
            S["out_msgs"] += 1; S["out_bytes"] += len(body); S["last_ok"] = now()
            S["out_device_records"] += 1 if "device" in payload else 0

def _send_raw(body):
    try:
        r = wan.post("/ingest/raw", content=body); r.raise_for_status()
        alerts = r.json().get("alerts", [])
    except (httpx.HTTPError, ValueError):
        return False
    S["out_msgs"] += 1; S["out_bytes"] += len(body); S["last_ok"] = now()
    if alerts:
        try:
            core.post("/internal/alerts", json=alerts)
        except httpx.HTTPError:
            pass
    return True

def cloud_loop():       # baseline: ship every raw reading upstream
    """Up to PIPELINE batches are in flight at once so the baseline is not
    penalised by a serialised uplink; after a failure it rewinds and sends
    serially until the link is healthy again."""
    pool = ThreadPoolExecutor(PIPELINE); inflight = deque(); serial = False
    while True:
        if S["mode"] != "cloud":
            inflight.clear(); time.sleep(0.1); continue
        while inflight and inflight[0][3].done():
            start, head, n, fut = inflight.popleft()
            if fut.result():
                cur["raw_ok"] = head; S["out_device_records"] += n; serial = False
            else:
                for x in inflight:
                    x[3].result()
                inflight.clear(); cur["raw"] = start; serial = True
                S["send_failures"] += 1; time.sleep(0.5)
        if len(inflight) >= (1 if serial else PIPELINE):
            time.sleep(0.003); continue
        try:
            d = core.get("/internal/raw", params={"after_id": cur["raw"], "limit": 500}).json()
        except (httpx.HTTPError, ValueError):
            time.sleep(0.5); continue
        if not d["items"]:
            time.sleep(0.01); continue
        inflight.append((cur["raw"], d["head"], len(d["items"]),
                         pool.submit(_send_raw, canon({"items": d["items"]}))))
        cur["raw"] = d["head"]

app = FastAPI(title="CivicEdge disclosure gateway", version="0.1.0")

def role(auth, *allowed):
    r = TOK.get((auth or "").removeprefix("Bearer ").strip())
    if r not in allowed:
        raise HTTPException(403 if r else 401, "not permitted")

@app.get("/health")
def health():
    return {"ok": True, "mode": S["mode"]}

@app.get("/ledger")          # residents may audit everything that left the node
def get_ledger(after: int = 0, limit: int = 500, last: int = 0, authorization: str = Header(None)):
    role(authorization, "operator", "resident", "internal")
    if last:
        return ledger.entries(max(0, ledger.head()[0] - min(last, 500)), 500)
    return ledger.entries(after, limit)

@app.get("/ledger/pubkey")
def pubkey():
    return {"ed25519": PUB_HEX, "policy_sha256": POLICY_HASH}

@app.get("/ledger/policy")
def policy(authorization: str = Header(None)):
    role(authorization, "operator", "resident", "internal")
    return POLICY

@app.get("/ledger/verify")
def verify(authorization: str = Header(None)):
    role(authorization, "operator", "resident", "internal")
    t = time.perf_counter(); after = 0; prev = "0" * 64; n = 0
    while True:
        page = ledger.entries(after, 2000)
        if not page:
            break
        ok, bad, why = verify_chain(page, PUB, prev, after)
        if not ok:
            return {"ok": False, "first_bad_seq": bad, "reason": why}
        after, prev, n = page[-1]["seq"], page[-1]["entry_hash"], n + len(page)
    return {"ok": True, "entries": n, "head": prev, "seconds": round(time.perf_counter() - t, 4)}

@app.get("/gateway/stats")
def stats(authorization: str = Header(None)):
    role(authorization, "operator", "internal")
    return dict(S, pending=ledger.count("status='pending'"), ledger_entries=ledger.count(),
                cursor_raw=cur["raw_ok"], window_s=WINDOW, now=now())

def set_mode(m):
    if m in ("civicedge", "cloud") and m != S["mode"]:
        st = core.post("/_ctl", json={"mode": m}).json()
        cur["raw"] = cur["raw_ok"] = st["raw_head"]; cur["win"] = 0.0
        al = core.get("/alerts", params={"last": 1}).json()
        if al:
            cur["alert"] = al[-1]["id"]
        S["mode"] = m
    return {"mode": S["mode"]}

@app.post("/gateway/_ctl")
def ctl(body: dict, authorization: str = Header(None)):
    role(authorization, "internal")
    return set_mode(body.get("mode"))

def wan_link(body=None):       # the link's control channel stays reachable while the data path is cut
    try:
        return httpx.post(WAN + "/_wan", json=body or {}, headers=H_CORE, timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return None

@app.get("/gateway/link")
def link(authorization: str = Header(None)):
    role(authorization, "operator", "internal")
    return wan_link()

@app.get("/gateway/city")      # what the upstream recipient currently holds, fetched over the WAN
def city_view(authorization: str = Header(None)):
    role(authorization, "operator", "internal")
    try:
        r = wan.get("/stats"); r.raise_for_status()
        return dict(r.json(), reachable=True)
    except (httpx.HTTPError, ValueError):
        return {"reachable": False}

@app.post("/gateway/demo")     # operator controls for demonstrations: mode, link state, link delay
def demo(body: dict, authorization: str = Header(None)):
    role(authorization, "operator", "internal")
    if "down" in body or "rtt" in body:
        b = {}
        if "down" in body:
            b["down"] = bool(body["down"])
        if "rtt" in body:
            rtt = max(0.0, min(2000.0, float(body["rtt"]))); b.update(delay_ms=rtt / 2, jitter_ms=rtt * 0.05)
        wan_link(b)
    if body.get("mode") in ("civicedge", "cloud") and body["mode"] != S["mode"]:
        set_mode(body["mode"]); time.sleep(1.5)
        try:
            wan.post("/_ctl", json={"reset": True})     # start the recipient's counters fresh for the new mode
        except httpx.HTTPError:
            pass
    return {"mode": S["mode"], "link": wan_link()}

def main():
    for f in (window_loop, event_loop, outbox_loop, cloud_loop):
        threading.Thread(target=f, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(env("PORT", "8710")), log_level="warning")

if __name__ == "__main__":
    main()
