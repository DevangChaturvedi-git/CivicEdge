"""Tamper-evident disclosure ledger.

Every decision the disclosure gateway takes (release or suppress) becomes one
entry. Entries form a SHA-256 hash chain and each entry hash is signed with
the node's Ed25519 key, so any later edit, deletion or reordering is
detectable by anyone holding the node's public key."""
import json, sqlite3, threading
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature
from .common import canon, sha256

GENESIS = "0" * 64
FIELDS = ["seq", "ts", "decision", "stream", "purpose", "recipient", "policy_hash",
          "window_start", "window_end", "n_devices", "payload_sha256", "bytes", "prev_hash"]

def entry_hash(e) -> str:
    return sha256(canon({k: e[k] for k in FIELDS}))

class Ledger:
    def __init__(self, path, key: ed25519.Ed25519PrivateKey):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS entries(
            seq INTEGER PRIMARY KEY, ts REAL, decision TEXT, stream TEXT, purpose TEXT,
            recipient TEXT, policy_hash TEXT, window_start REAL, window_end REAL,
            n_devices INTEGER, payload_sha256 TEXT, bytes INTEGER, prev_hash TEXT,
            entry_hash TEXT, sig TEXT, payload TEXT, status TEXT, sent_ts REAL)""")
        self.db.commit()
        self.key = key
        self.lock = threading.Lock()

    def head(self):
        r = self.db.execute("SELECT seq, entry_hash FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        return (r["seq"], r["entry_hash"]) if r else (0, GENESIS)

    def append(self, *, ts, decision, stream, purpose, recipient, policy_hash,
               window_start, window_end, n_devices, payload):
        body = canon(payload) if payload is not None else b""
        with self.lock:
            seq, prev = self.head()
            e = dict(seq=seq + 1, ts=ts, decision=decision, stream=stream, purpose=purpose,
                     recipient=recipient, policy_hash=policy_hash, window_start=window_start,
                     window_end=window_end, n_devices=n_devices, payload_sha256=sha256(body),
                     bytes=len(body), prev_hash=prev)
            e["entry_hash"] = entry_hash(e)
            e["sig"] = self.key.sign(bytes.fromhex(e["entry_hash"])).hex()
            status = "pending" if decision == "release" else "withheld"
            self.db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [e[k] for k in FIELDS] + [e["entry_hash"], e["sig"],
                             body.decode() if decision == "release" else None, status, None])
            self.db.commit()
        return e

    def pending(self, limit=50):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM entries WHERE status='pending' ORDER BY seq LIMIT ?", (limit,))]

    def mark_sent(self, seq, ts):
        with self.lock:
            self.db.execute("UPDATE entries SET status='sent', sent_ts=? WHERE seq=?", (ts, seq))
            self.db.commit()

    def entries(self, after=0, limit=500):
        cols = ",".join(FIELDS + ["entry_hash", "sig", "status", "sent_ts"])
        return [dict(r) for r in self.db.execute(
            f"SELECT {cols} FROM entries WHERE seq>? ORDER BY seq LIMIT ?", (after, limit))]

    def count(self, where="1=1"):
        return self.db.execute(f"SELECT COUNT(*) FROM entries WHERE {where}").fetchone()[0]

def verify_chain(entries, pubkey: ed25519.Ed25519PublicKey, start_prev=GENESIS, start_seq=0):
    """Returns (ok, first_bad_seq, reason). `entries` must be in seq order."""
    prev, seq = start_prev, start_seq
    for e in entries:
        if e["seq"] != seq + 1:
            return False, e["seq"], "gap-or-reorder"
        if e["prev_hash"] != prev:
            return False, e["seq"], "broken-link"
        if entry_hash(e) != e["entry_hash"]:
            return False, e["seq"], "content-modified"
        try:
            pubkey.verify(bytes.fromhex(e["sig"]), bytes.fromhex(e["entry_hash"]))
        except (InvalidSignature, ValueError):
            return False, e["seq"], "bad-signature"
        prev, seq = e["entry_hash"], e["seq"]
    return True, None, None
