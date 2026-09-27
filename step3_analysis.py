"""Round 5 Step 3: statistical analysis of results_llm_full.csv. Read-only, no new API calls."""
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REASON_TAGS = ["PARTS", "MAINT", "HARMONIZE", "COST", "NOFAIL"]


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def fisher_exact_two_sided(a, b, c, d):
    """Exact Fisher test on a 2x2 table [[a,b],[c,d]] via direct enumeration (no scipy)."""
    n = a + b + c + d
    row1, row2, col1, col2 = a + b, c + d, a + c, b + d

    def hyper_p(x):
        return (math.comb(col1, x) * math.comb(col2, row1 - x)) / math.comb(n, row1)

    lo = max(0, row1 - col2)
    hi = min(row1, col1)
    p_obs = hyper_p(a)
    total = 0.0
    for x in range(lo, hi + 1):
        px = hyper_p(x)
        if px <= p_obs * (1 + 1e-9):
            total += px
    return min(1.0, total)


def document_verdict(rows):
    """Same rule as extractor_llm.py's Round 5 aggregation fix: exclude rows where BOTH
    final_value and requested_value are null before deciding the document's verdict."""
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

    print(f"Total rows: {len(rows)}  |  documents with >=1 detected block: {len(by_doc)}")

    # --- 1. Concession ratio distribution for PARTIAL outcomes ---
    partial_ratios = []
    for r in rows:
        if r["outcome"] == "PARTIAL" and r["concession_ratio"] not in (None, ""):
            try:
                partial_ratios.append(float(r["concession_ratio"]))
            except ValueError:
                pass
    print(f"\n--- Concession ratio (PARTIAL rows) ---")
    print(f"n = {len(partial_ratios)} (of {sum(1 for r in rows if r['outcome']=='PARTIAL')} PARTIAL rows total)")
    if partial_ratios:
        partial_ratios.sort()
        median = statistics.median(partial_ratios)
        q1 = statistics.median(partial_ratios[: len(partial_ratios) // 2])
        q3 = statistics.median(partial_ratios[(len(partial_ratios) + 1) // 2:])
        print(f"median = {median:.3f}, IQR = [{q1:.3f}, {q3:.3f}]")

    # --- document-level verdicts, decade, reasons, oem ---
    doc_info = {}
    for dn, doc_rows in by_doc.items():
        verdict = document_verdict(doc_rows)
        decade = doc_rows[0]["decade"]
        reasons = set()
        oem = False
        for r in doc_rows:
            if r["reasons"]:
                reasons.update(r["reasons"].split(";"))
            if r["faa_cites_oem_parts"] == "True":
                oem = True
        doc_info[dn] = {"verdict": verdict, "decade": decade, "reasons": reasons, "oem": oem}

    from collections import Counter
    print(f"\n--- Document-level verdict counts (n={len(doc_info)}) ---")
    print(Counter(v["verdict"] for v in doc_info.values()))

    # --- 2. Outcome distribution by decade with Wilson CIs ---
    print(f"\n--- Outcome by decade (GRANTED vs DENIED, resolved documents only) ---")
    by_decade = defaultdict(list)
    for v in doc_info.values():
        by_decade[v["decade"]].append(v["verdict"])
    for decade in sorted(by_decade):
        verdicts = by_decade[decade]
        granted = verdicts.count("GRANTED")
        denied = verdicts.count("DENIED")
        resolved = granted + denied
        unresolved = len(verdicts) - resolved
        p, lo, hi = wilson_ci(granted, resolved) if resolved else (None, None, None)
        if p is None:
            print(f"{decade}: n={len(verdicts)}, resolved=0 (unresolved={unresolved}) -- no rate computable")
        else:
            print(f"{decade}: n={len(verdicts)}, resolved={resolved} (unresolved={unresolved}), "
                  f"granted={granted}, grant rate={p:.1%}  95% CI [{lo:.1%}, {hi:.1%}]")

    # --- 3. Grant rate by reason tag, Fisher exact + odds ratio ---
    print(f"\n--- Grant rate by reason tag (resolved documents only; descriptive, not causal) ---")
    all_docs = list(doc_info.values())
    resolved_docs = [v for v in all_docs if v["verdict"] in ("GRANTED", "DENIED")]
    print(f"resolved documents overall: {len(resolved_docs)} of {len(all_docs)}")
    for tag in REASON_TAGS:
        with_tag = [v for v in resolved_docs if tag in v["reasons"]]
        without_tag = [v for v in resolved_docs if tag not in v["reasons"]]
        a = sum(1 for v in with_tag if v["verdict"] == "GRANTED")
        b = sum(1 for v in with_tag if v["verdict"] == "DENIED")
        c = sum(1 for v in without_tag if v["verdict"] == "GRANTED")
        d = sum(1 for v in without_tag if v["verdict"] == "DENIED")
        n_tag = a + b
        p_val = fisher_exact_two_sided(a, b, c, d) if n_tag > 0 else None
        rate = a / n_tag if n_tag else None
        or_val = (a * d) / (b * c) if (b > 0 and c > 0) else None
        rate_str = f"{rate:.1%}" if rate is not None else "n/a"
        or_str = f"{or_val:.2f}" if or_val is not None else "undefined (zero cell)"
        p_str = f"{p_val:.4f}" if p_val is not None else "n/a"
        print(f"{tag}: n={n_tag} (granted={a}, denied={b})  grant rate={rate_str}  "
              f"odds ratio vs rest={or_str}  Fisher p={p_str}")

    # --- 4. OEM_PARTS comparison ---
    print(f"\n--- FAA_CITES_OEM_PARTS comparison (resolved documents only) ---")
    oem_true = [v for v in resolved_docs if v["oem"]]
    oem_false = [v for v in resolved_docs if not v["oem"]]
    g_t = sum(1 for v in oem_true if v["verdict"] == "GRANTED")
    d_t = sum(1 for v in oem_true if v["verdict"] == "DENIED")
    g_f = sum(1 for v in oem_false if v["verdict"] == "GRANTED")
    d_f = sum(1 for v in oem_false if v["verdict"] == "DENIED")
    print(f"OEM_PARTS=True:  n={g_t+d_t} (granted={g_t}, denied={d_t})")
    print(f"OEM_PARTS=False: n={g_f+d_f} (granted={g_f}, denied={d_f})")
    if (g_t + d_t) < 15 or (g_t + d_t) < 20:
        print("UNDERPOWERED: fewer than 15-20 resolved OEM_PARTS=True documents -- no headline number reported.")
    else:
        p_val = fisher_exact_two_sided(g_t, d_t, g_f, d_f)
        print(f"grant rate True={g_t/(g_t+d_t):.1%}  grant rate False={g_f/(g_f+d_f):.1%}  Fisher p={p_val:.4f}")

    # --- verbatim failures ---
    print(f"\n--- Verbatim quote check ---")
    print(f"verbatim_ok=False rows: {sum(1 for r in rows if r['verbatim_ok']=='False')} of {len(rows)}")


if __name__ == "__main__":
    main()
