"""Experiment driver. Runs the cloud-centric baseline and CivicEdge on the
same live fleet and writes results/results.json.
E1 alert latency vs WAN RTT, E2 data leaving the node, E3 WAN outage."""
import json, os, statistics as st, sys, time
import httpx
from civicedge.cli import audit

E = os.environ.get
INT = {"Authorization": "Bearer " + E("INTERNAL_TOKEN", "int")}
CITY = {"Authorization": "Bearer " + E("CITY_TOKEN", "city")}
core = httpx.Client(base_url=E("CORE_URL", "http://core:8700"), headers=INT, timeout=20)
gw = httpx.Client(base_url=E("GATEWAY_URL", "http://gateway:8710"), headers=INT, timeout=20)
wan = httpx.Client(base_url=E("WAN_URL", "http://wan:8720"), headers=INT, timeout=20)
city = httpx.Client(base_url=E("CITY_URL", "http://city:8730"), headers=CITY, timeout=20)
RTTS = [int(x) for x in E("RTTS", "20,100,300").split(",")]
DUR = float(E("PHASE_S", "90")); OUT = float(E("OUTAGE_S", "90")); PRE = float(E("PRE_S", "30"))
OUTDIR = E("OUT_DIR", "/results"); os.makedirs(OUTDIR, exist_ok=True)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def link(rtt=None, down=None):
    b = {}
    if rtt is not None:
        b.update(delay_ms=rtt / 2, jitter_ms=rtt * 0.05)
    if down is not None:
        b["down"] = down
    wan.post("/_wan", json=b).raise_for_status()

def mode(m):
    gw.post("/gateway/_ctl", json={"mode": m}).raise_for_status()
    time.sleep(3)      # let batches still in flight from the previous mode land before counting
    city.post("/_ctl", json={"reset": True}).raise_for_status()

def snap():
    return {"core": core.get("/stats").json(), "gw": gw.get("/gateway/stats").json(),
            "city": city.get("/stats").json()}

def last_alert():
    a = core.get("/alerts", params={"after_id": 0, "limit": 1000000}).json()
    return a[-1]["id"] if a else 0

def alerts_after(i):
    return core.get("/alerts", params={"after_id": i, "limit": 1000000}).json()

def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None

def lat_summary(al):
    ms = [(a["created_ts"] - a["trigger_ts"]) * 1000 for a in al]
    if not ms:
        return {"n": 0}
    return {"n": len(ms), "p50": round(pct(ms, 50), 2), "p95": round(pct(ms, 95), 2),
            "p99": round(pct(ms, 99), 2), "mean": round(st.mean(ms), 2), "max": round(max(ms), 2),
            "min": round(min(ms), 2)}

def delta(a, b):
    g = lambda k, f: b[k][f] - a[k][f]
    return {"readings": g("core", "readings"), "bytes_ingested": g("core", "bytes_in"),
            "bytes_out": g("gw", "out_bytes"), "msgs_out": g("gw", "out_msgs"),
            "device_records_out": g("gw", "out_device_records"),
            "released": g("gw", "released"), "suppressed": g("gw", "suppressed"),
            "city_personal_records": g("city", "personal_records"),
            "city_distinct_devices": b["city"]["distinct_devices"],
            "city_distinct_households": b["city"]["distinct_households"],
            "city_disclosures": g("city", "disclosures"), "city_raw_records": g("city", "raw_records")}

def e1(m, rtt):
    link(rtt=rtt, down=False); mode(m); time.sleep(6)
    a0, s0, t0 = last_alert(), snap(), time.time()
    time.sleep(DUR); t1 = time.time(); time.sleep(4)
    s1 = snap()
    al = [a for a in alerts_after(a0) if t0 <= a["trigger_ts"] < t1]
    r = {"mode": m, "rtt_ms": rtt, "t0": t0, "t1": t1, "duration_s": DUR,
         "latency_ms": lat_summary(al), "latencies_ms": [round((a["created_ts"] - a["trigger_ts"]) * 1000, 2) for a in al],
         "by_rule": {k: lat_summary([a for a in al if a["rule"] == k]) for k in sorted({a["rule"] for a in al})},
         "traffic": delta(s0, s1)}
    log("E1", m, rtt, r["latency_ms"], r["traffic"]["bytes_out"], "/", r["traffic"]["bytes_ingested"])
    return r

def backlog(m):
    g = gw.get("/gateway/stats").json()
    if m == "civicedge":
        return g["pending"]
    return max(0, core.get("/stats").json()["raw_head"] - g["cursor_raw"])

def e3(m):
    link(rtt=100, down=False); mode(m); time.sleep(6)
    a0, s0, t0 = last_alert(), snap(), time.time()
    time.sleep(PRE)
    link(down=True); t_down = time.time(); log("E3", m, "link down")
    series = []
    while time.time() - t_down < OUT:
        series.append([round(time.time() - t_down, 1), backlog(m)]); time.sleep(2)
    peak = backlog(m)
    link(down=False); t_up = time.time(); log("E3", m, "link up, backlog", peak)
    drain = None; quiet = 0
    while time.time() - t_up < 180:
        b = backlog(m); series.append([round(time.time() - t_down, 1), b])
        quiet = quiet + 1 if b <= (0 if m == "civicedge" else 150) else 0
        if quiet >= 3 and time.time() - t_up > 1.5:
            drain = time.time() - t_up - 0.6; break
        time.sleep(0.3)
    time.sleep(PRE); t1 = time.time(); time.sleep(4)
    s1 = snap(); al = alerts_after(a0)
    during = [a for a in al if t_down <= a["trigger_ts"] < t_up]
    fast = [a for a in during if a["created_ts"] - a["trigger_ts"] <= 1.0]
    r = {"mode": m, "t0": t0, "t_down": t_down, "t_up": t_up, "t1": t1, "outage_s": OUT,
         "alerts_triggered_during_outage": len(during), "delivered_within_1s": len(fast),
         "availability": round(len(fast) / len(during), 4) if during else None,
         "latency_during_outage_ms": lat_summary(during),
         "latency_outside_outage_ms": lat_summary([a for a in al if not (t_down <= a["trigger_ts"] < t_up) and t0 <= a["trigger_ts"] < t1]),
         "peak_backlog": peak, "drain_s": round(drain, 2) if drain is not None else None,
         "backlog_series": series, "traffic": delta(s0, s1),
         "points": [[round(a["trigger_ts"] - t_down, 2), round((a["created_ts"] - a["trigger_ts"]) * 1000, 2)]
                    for a in al if t0 <= a["trigger_ts"] < t1]}
    if m == "civicedge":
        r["released_entries"] = r["traffic"]["released"]; r["received_by_city"] = r["traffic"]["city_disclosures"]
        r["pending_after_drain"] = s1["gw"]["pending"]
    else:
        r["readings_in_phase"] = r["traffic"]["readings"]; r["received_by_city"] = r["traffic"]["city_raw_records"]
    log("E3", m, {k: v for k, v in r.items() if k not in ("backlog_series", "traffic", "points")})
    return r

def main():
    for c in (core, gw, city):
        for _ in range(120):
            try:
                c.get("/health" if c is not city else "/stats").raise_for_status(); break
            except httpx.HTTPError:
                time.sleep(1)
    log("warm-up"); time.sleep(float(E("WARMUP_S", "15")))
    res = {"started": time.time(), "config": {"rtts": RTTS, "phase_s": DUR, "outage_s": OUT,
           "fleet": core.get("/stats").json()}, "e1": [], "e3": []}
    for rtt in RTTS:
        for m in ("cloud", "civicedge"):
            res["e1"].append(e1(m, rtt))
    for m in ("cloud", "civicedge"):
        res["e3"].append(e3(m))
    link(rtt=20, down=False); mode("civicedge")
    res["ledger_audit"] = audit(E("GATEWAY_URL", "http://gateway:8710"), E("INTERNAL_TOKEN", "int"),
                                E("CITY_URL", "http://city:8730"), E("CITY_TOKEN", "city"))
    res["ledger_verify"] = gw.get("/ledger/verify").json()
    res["finished"] = time.time()
    json.dump(res, open(os.path.join(OUTDIR, "results.json"), "w"), indent=1)
    log("done", res["ledger_audit"])

if __name__ == "__main__":
    main()
