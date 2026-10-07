import operator
import yaml
from .common import FIELD

OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}

def load(path):
    with open(path) as f:
        return yaml.safe_load(f)

def validate(r, ranges):
    """Return None if the reading is acceptable, else a reason string."""
    try:
        kind = r["kind"]; f = FIELD[kind]; v = r["v"][f]
        if not isinstance(r["id"], str) or not isinstance(r["zone"], str):
            return "bad-identity"
        float(r["ts"]); int(r["seq"])
    except (KeyError, TypeError, ValueError):
        return "malformed"
    if not isinstance(v, (int, float)):
        return "non-numeric"
    lo, hi = ranges[kind][f]
    if not (lo <= v <= hi):
        return "out-of-range"
    return None

class RuleEngine:
    def __init__(self, rules):
        self.rules = rules
        self.run = {}    # (rule, device) -> consecutive hits
        self.last = {}   # (rule, device) -> ts of last alert

    def feed(self, r):
        out = []
        for rule in self.rules:
            if rule["kind"] != r["kind"]:
                continue
            key = (rule["id"], r["id"])
            v = r["v"].get(rule["field"])
            if v is not None and OPS[rule["op"]](v, rule["threshold"]):
                self.run[key] = self.run.get(key, 0) + 1
            else:
                self.run[key] = 0
                continue
            if self.run[key] >= rule["consecutive"] and \
               r["ts"] - self.last.get(key, 0) >= rule.get("cooldown_s", 0):
                self.last[key] = r["ts"]
                out.append({"rule": rule["id"], "kind": r["kind"], "device": r["id"],
                            "zone": r["zone"], "severity": rule["severity"],
                            "value": v, "trigger_ts": r["ts"]})
        return out
