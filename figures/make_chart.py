"""Regenerates figures/decade_grant_rate.png from results_llm_full.csv. Read-only, no extraction."""
import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def document_verdict(rows):
    scoreable = [r for r in rows if r.get("outcome") and (r.get("final_value") or r.get("requested_value"))]
    outcomes = [r["outcome"] for r in scoreable]
    if any(o in ("FULL", "PARTIAL") for o in outcomes):
        return "GRANTED"
    if any(o == "DENY" for o in outcomes):
        return "DENIED"
    if any(o == "UNCLEAR" for o in outcomes):
        return "UNCLEAR_MODEL"
    return "NO_OUTCOME"


def main():
    rows = list(csv.DictReader(open(ROOT / "results_llm_full.csv", encoding="utf-8")))
    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["document_number"]].append(r)

    by_decade = defaultdict(list)
    for dn, doc_rows in by_doc.items():
        by_decade[doc_rows[0]["decade"]].append(document_verdict(doc_rows))

    decades = sorted(by_decade)
    rates, los, his, ns = [], [], [], []
    for decade in decades:
        verdicts = by_decade[decade]
        granted = verdicts.count("GRANTED")
        denied = verdicts.count("DENIED")
        resolved = granted + denied
        p, lo, hi = wilson_ci(granted, resolved)
        rates.append(p * 100)
        los.append((p - lo) * 100)
        his.append((hi - p) * 100)
        ns.append(resolved)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = range(len(decades))
    ax.errorbar(x, rates, yerr=[los, his], fmt="o-", capsize=5, color="#1f4e79", ecolor="#1f4e79",
                linewidth=1.8, markersize=7)
    for xi, r, n in zip(x, rates, ns):
        ax.annotate(f"n={n}", (xi, r), textcoords="offset points", xytext=(0, -18),
                    ha="center", fontsize=9, color="#555")
    ax.set_xticks(list(x))
    ax.set_xticklabels(decades)
    ax.set_ylabel("Grant rate (%) among resolved documents")
    ax.set_title("Compliance-time requests granted, by decade\n(Wilson 95% CI; extracted with openai/gpt-oss-120b)")
    ax.set_ylim(0, 60)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = ROOT / "figures" / "decade_grant_rate.png"
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
