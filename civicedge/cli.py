"""Auditor CLI.  python -m civicedge.cli verify --node URL --token T [--city URL --city-token T]
Re-derives the whole hash chain from the node's ledger, checks every
signature, and (optionally) checks that each receipt the city holds matches
the ledger, which anchors the chain outside the node."""
import argparse, json, sys
import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519
from .ledger import verify_chain

def audit(node, token, city=None, city_token=None):
    h = {"Authorization": "Bearer " + token}
    pk = httpx.get(node + "/ledger/pubkey", timeout=10).json()
    pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk["ed25519"]))
    after, prev, hashes, counts = 0, "0" * 64, {}, {"release": 0, "suppress": 0}
    while True:
        page = httpx.get(node + "/ledger", params={"after": after, "limit": 2000}, headers=h, timeout=30).json()
        if not page:
            break
        ok, bad, why = verify_chain(page, pub, prev, after)
        if not ok:
            return {"ok": False, "first_bad_seq": bad, "reason": why}
        for e in page:
            hashes[e["seq"]] = e["entry_hash"]; counts[e["decision"]] += 1
        after, prev = page[-1]["seq"], page[-1]["entry_hash"]
    out = {"ok": True, "entries": len(hashes), "head": prev, **counts}
    if city:
        rc = httpx.get(city + "/receipts", headers={"Authorization": "Bearer " + city_token}, timeout=30).json()
        bad = [r["seq"] for r in rc if hashes.get(r["seq"]) != r["entry_hash"]]
        out.update(city_receipts=len(rc), receipts_mismatched=len(bad), ok=not bad)
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["verify"])
    ap.add_argument("--node", default="http://localhost:8700"); ap.add_argument("--token", required=True)
    ap.add_argument("--city"); ap.add_argument("--city-token")
    a = ap.parse_args()
    r = audit(a.node, a.token, a.city, a.city_token)
    print(json.dumps(r, indent=2)); sys.exit(0 if r["ok"] else 1)

if __name__ == "__main__":
    main()
