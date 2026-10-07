"""Turn results/ into the paper's figures and a summary table.
usage: python -m experiments.plots RESULTS_DIR OUT_DIR"""
import csv, json, os, statistics as st, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.linewidth": 0.6,
                     "savefig.dpi": 300, "savefig.bbox": "tight", "legend.frameon": False})
C = {"cloud": "#b5482f", "civicedge": "#1f5f8b"}
LAB = {"cloud": "Cloud-centric baseline", "civicedge": "CivicEdge"}
W = 3.45

def fig_latency(R, out):
    fig, ax = plt.subplots(figsize=(W, 2.2))
    for m, mk in (("cloud", "s"), ("civicedge", "o")):
        rows = sorted((r for r in R["e1"] if r["mode"] == m), key=lambda r: r["rtt_ms"])
        x = [r["rtt_ms"] for r in rows]; p50 = [r["latency_ms"]["p50"] for r in rows]
        p95 = [r["latency_ms"]["p95"] for r in rows]
        ax.errorbar(x, p50, yerr=[[0] * len(x), [b - a for a, b in zip(p50, p95)]], marker=mk, ms=4,
                    color=C[m], lw=1, capsize=2, label=LAB[m])
    ax.set_yscale("log"); ax.set_xlabel("WAN round-trip time (ms)"); ax.set_ylabel("Alert latency (ms)")
    ax.set_xticks(sorted({r["rtt_ms"] for r in R["e1"]})); ax.grid(True, which="both", lw=0.3, alpha=0.5)
    ax.legend(loc="center right"); fig.savefig(os.path.join(out, "fig_latency.png")); plt.close(fig)

def fig_outage(R, out):
    fig, ax = plt.subplots(figsize=(W, 2.2))
    o = R["e3"][0]["outage_s"]
    ax.axvspan(0, o, color="0.88", lw=0)
    for r, mk in zip(R["e3"], ("s", "o")):
        if r.get("points"):
            x, y = zip(*r["points"])
            ax.scatter(x, [max(v, 0.3) for v in y], s=7, marker=mk, color=C[r["mode"]], label=LAB[r["mode"]], lw=0)
    ax.set_yscale("log"); ax.set_xlabel("Time since link failure (s)"); ax.set_ylabel("Alert latency (ms)")
    ax.axhline(1000, color="0.4", lw=0.5, ls="--"); ax.text(ax.get_xlim()[0] + 2, 1150, "1 s", fontsize=6, color="0.3")
    ax.text(o / 2, ax.get_ylim()[0] * 1.6, "WAN down", ha="center", fontsize=7, color="0.3")
    ax.grid(True, which="major", lw=0.3, alpha=0.5)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=6.5, handletextpad=0.2)
    fig.savefig(os.path.join(out, "fig_outage.png")); plt.close(fig)

def fig_egress(R, out):
    fig, ax = plt.subplots(figsize=(W, 2.0))
    rtts = sorted({r["rtt_ms"] for r in R["e1"]}); w = 0.38
    for i, m in enumerate(("cloud", "civicedge")):
        v = [next(r for r in R["e1"] if r["mode"] == m and r["rtt_ms"] == t)["traffic"] for t in rtts]
        kb = [x["bytes_out"] / 1024 / ((R["config"]["phase_s"] + 4) / 60) for x in v]
        b = ax.bar([j + (i - 0.5) * w for j in range(len(rtts))], kb, w, color=C[m], label=LAB[m])
        for rect, k in zip(b, kb):
            ax.text(rect.get_x() + rect.get_width() / 2, k * 1.08, f"{k:.0f}", ha="center", fontsize=6)
    ax.set_yscale("log"); ax.set_xticks(range(len(rtts))); ax.set_xticklabels([f"{t} ms" for t in rtts])
    ax.set_xlabel("WAN round-trip time"); ax.set_ylabel("Data leaving node (KiB/min)")
    ax.set_ylim(top=ax.get_ylim()[1] * 2.5); ax.legend(loc="upper right", ncol=2, fontsize=6.5)
    fig.savefig(os.path.join(out, "fig_egress.png")); plt.close(fig)

def fig_arch(out):
    fig, ax = plt.subplots(figsize=(W, 3.25)); ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    def box(x, y, w, h, t, fc="white", fs=6, ls="-"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6", fc=fc, ec="black", lw=0.7, ls=ls))
        ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=fs, linespacing=1.15)
    def arrow(x1, y1, x2, y2):
        ax.annotate("", (x2, y2), (x1, y1), arrowprops=dict(arrowstyle="->", lw=0.7, shrinkA=0, shrinkB=0))
    for x, t in ((3, "Air-quality\nsensors"), (36, "Household\nwater meters"), (69, "Waste-bin\nsensors")):
        box(x, 86, 28, 11, t)
    ax.add_patch(FancyBboxPatch((1.5, 24), 97, 52, boxstyle="round,pad=0.6", fc="#eef3f7", ec="#1f5f8b", lw=0.9, ls="--"))
    ax.text(96.5, 72, "CivicEdge node (commodity home server)", ha="right", va="center", fontsize=6.2, color="#1f5f8b", weight="bold")
    box(4, 49, 26, 18, "MQTT broker\nTLS, per-device\nidentity,\ntopic ACL")
    box(37, 49, 26, 18, "Edge core\nvalidate, store,\nrules, alerts\n(no route out)", fc="#dbe8f1")
    box(70, 49, 26, 18, "Disclosure\ngateway\npolicy, ledger,\nstore-forward", fc="#dbe8f1")
    box(4, 28, 26, 12, "Portal\nrole tokens,\nledger audit"); box(37, 28, 26, 12, "Local\ndatabase")
    box(4, 3, 26, 12, "Residents,\noperators"); box(40, 3, 20, 12, "City\nplatform"); box(70, 3, 26, 12, "WAN link", ls=":")
    for x in (17, 50, 83):
        ax.plot([x, x], [85, 80.5], color="black", lw=0.7)
    ax.plot([17, 83], [80.5, 80.5], color="black", lw=0.7); arrow(17, 80.5, 17, 68.5)
    ax.text(66.5, 83, "MQTT over TLS", fontsize=5.3, ha="center", va="center", color="0.25")
    arrow(31, 58, 36, 58); arrow(64, 58, 69, 58); arrow(50, 48, 50, 41); arrow(30.8, 41, 36.5, 48.2)
    arrow(17, 16, 17, 27); arrow(83, 48, 83, 16); arrow(69, 9, 61, 9)
    ax.text(81, 19.5, "permitted\nreleases only", fontsize=5.3, ha="right", va="center", color="0.25")
    fig.savefig(os.path.join(out, "fig_arch.png")); plt.close(fig)

def overhead(res_dir, R):
    p = os.path.join(res_dir, "stats.csv"); rows = {}
    if not os.path.exists(p):
        return {}
    t0, t1 = R["started"], R["finished"]
    for line in csv.reader(open(p)):
        try:
            t, name, cpu, mem = float(line[0]), line[1], float(line[2].strip("%")), line[3].split("/")[0].strip()
        except (ValueError, IndexError):
            continue
        if not (t0 <= t <= t1):
            continue
        mb = float(mem[:-3]) * (1024 if mem.endswith("GiB") else 1 / 1024 if mem.endswith("KiB") else 1)
        rows.setdefault(name.replace("civicedge-", "").rsplit("-", 1)[0], []).append((cpu, mb))
    return {k: {"cpu_mean": round(st.mean(c for c, _ in v), 2), "cpu_max": round(max(c for c, _ in v), 2),
                "mem_mean_mb": round(st.mean(m for _, m in v), 1), "mem_max_mb": round(max(m for _, m in v), 1),
                "samples": len(v)} for k, v in sorted(rows.items())}

def main(res_dir, out):
    os.makedirs(out, exist_ok=True)
    R = json.load(open(os.path.join(res_dir, "results.json")))
    fig_arch(out); fig_latency(R, out); fig_outage(R, out); fig_egress(R, out)
    summary = {"overhead": overhead(res_dir, R)}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
