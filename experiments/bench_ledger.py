"""Ledger micro-benchmark: append/verify cost and tamper detection."""
import json, os, random, sys, tempfile, time
from cryptography.hazmat.primitives.asymmetric import ed25519
from civicedge.ledger import Ledger, verify_chain

def build(n, key):
    d = tempfile.mkdtemp(); L = Ledger(os.path.join(d, "l.db"), key)
    t = time.perf_counter()
    for i in range(n):
        L.append(ts=float(i), decision="release", stream="air", purpose="public-health-reporting",
                 recipient="city-platform", policy_hash="p" * 64, window_start=float(i), window_end=float(i + 30),
                 n_devices=25, payload={"type": "aggregate", "groups": [{"zone": "Z1", "mean": 80.1, "max": 99.0, "count": 150}]})
    return L, time.perf_counter() - t

def main(out):
    key = ed25519.Ed25519PrivateKey.generate(); pub = key.public_key(); res = {"scaling": []}
    for n in (1000, 10000, 50000):
        L, ta = build(n, key); es = L.entries(0, n + 1)
        t = time.perf_counter(); ok, _, _ = verify_chain(es, pub); tv = time.perf_counter() - t
        res["scaling"].append({"entries": n, "append_ms_per_entry": round(ta / n * 1000, 4),
                               "verify_s": round(tv, 4), "verify_us_per_entry": round(tv / n * 1e6, 2), "ok": ok})
        print(res["scaling"][-1], flush=True)
    L, _ = build(2000, key); base = L.entries(0, 5000); random.seed(1)
    kinds = {"modify-field": 0, "delete-entry": 0, "reorder": 0, "forge-resign-wrong-key": 0}; trials = 250
    other = ed25519.Ed25519PrivateKey.generate()
    for kind in kinds:
        for _ in range(trials):
            es = [dict(e) for e in base]; i = random.randrange(1, len(es) - 1)
            if kind == "modify-field":
                f = random.choice(["n_devices", "purpose", "payload_sha256", "bytes", "decision", "window_end"])
                es[i][f] = es[i][f] + 1 if isinstance(es[i][f], (int, float)) else es[i][f] + "x"
            elif kind == "delete-entry":
                del es[i]
            elif kind == "reorder":
                es[i], es[i + 1] = es[i + 1], es[i]
            else:   # attacker rewrites an entry and recomputes hash, signs with own key
                from civicedge.ledger import entry_hash
                es[i]["n_devices"] += 1; es[i]["entry_hash"] = entry_hash(es[i])
                es[i]["sig"] = other.sign(bytes.fromhex(es[i]["entry_hash"])).hex()
            ok, _, _ = verify_chain(es, pub)
            kinds[kind] += 0 if ok else 1
    res["tamper"] = {"trials_per_kind": trials, "detected": kinds}
    print(res["tamper"], flush=True)
    json.dump(res, open(out, "w"), indent=1)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/results/ledger_bench.json")
