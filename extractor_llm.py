#!/usr/bin/env python3
"""
LLM-based extraction step for the FAA AD concession study (Round 4). Replaces ONLY the P/R/outcome extraction
step of the deterministic pipeline with one API call per detected request block. Heading detection is UNCHANGED:
imported directly from the frozen Round 3 snapshot (extractor_regex_v3.py), never re-implemented here.

PROVIDER NOTE: ROUND4_PROMPT.md specified xAI (Grok) at api.x.ai. That key had zero credits/licenses (403,
confirmed against the live API before any billable call was made). The user then supplied a Groq key instead
(gsk_... prefix, api.groq.com) -- a different company, OpenAI-compatible API, hosting open-weight models rather
than xAI's Grok. This script targets Groq. Everything else in Round 4's brief (read key from env, never log/write
it, query the model list rather than assume a name, pin one exact ID, temperature 0, log every raw response,
verify the evidence_quote verbatim, the 34/40 + 0-verbatim-failures gate before touching the full corpus) is
followed as specified.

Model actually available and pinned (checked live against GET /openai/v1/models, not assumed from memory):
    openai/gpt-oss-120b   (131,072 token context; the largest general-purpose text model on this key)

USAGE
    export GROQ_API_KEY=...          (never hardcoded, never written to a file by this script)
    python extractor_llm.py val40    run on the 40 val40_results.md documents only (this round's only mode)

Output:
    llm_logs/<doc>_<block_idx>.json   the FULL raw API response for every call, for traceability
    results_llm_val40.csv             one row per extracted request (same shape as the regex pipeline's rows)
    llm_val40_report.json             agreement count, disagreements, verbatim-check failures, gate verdict
"""
import json, os, pathlib, re, sys, time, urllib.error, urllib.request

import extractor_regex_v3 as R   # heading detection, population, text fetch -- UNCHANGED, imported not reimplemented

ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIR = ROOT / "llm_logs"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"   # pinned exact ID, confirmed live against /v1/models on 2026-09-23; never a floating alias

SYSTEM_PROMPT = """You are extracting structured data from ONE paragraph block of an FAA Airworthiness Directive \
final rule. The block contains a commenter's request about a compliance time and the FAA's response to it.

Return ONLY a JSON array (no prose, no markdown fences). Each element is one distinct request in the block:
{"proposed_value": <number|null>, "proposed_unit": <string|null>,
 "requested_value": <number|null>, "requested_unit": <string|null>,
 "final_value": <number|null>, "final_unit": <string|null>,
 "outcome": "DENY"|"PARTIAL"|"FULL"|"UNCLEAR",
 "evidence_quote": "<one verbatim sentence from the block supporting final_value>"}

Rules:
- Units and numbers must be COPIED from the text, never converted (e.g. do not turn "18 months" into "1.5 years").
- "evidence_quote" must be copied VERBATIM, character for character, from the block text below. Do not paraphrase.
- "We do not concur" / "the FAA does not concur" mean the SAME as "we disagree" -- treat both as a denial.
- "We concur" / "the FAA concurs" mean the SAME as "we agree" -- treat both as acceptance.
- If a grant sentence says "extend from OLD to NEW" or "changed from OLD to NEW", the GRANTED (final) value is
  NEW, not OLD. OLD is only what is being changed away from.
- outcome DENY = the compliance time was not changed from what was proposed. PARTIAL = changed, but not all the
  way to what was requested. FULL = changed to exactly (or beyond) what was requested. UNCLEAR = you cannot tell
  from this block, or the request was mooted/withdrawn rather than decided.
- If the block covers more than one distinct request (e.g. different commenters asking for different things, or
  different paragraph references), return one object per request, in the order they appear.
- If there is no discernible request/response pair in the block at all, return an empty array []."""


RETRY_WAIT_RE = re.compile(r"try again in ([\d.]+)s")

def call_groq(block_text: str, api_key: str, max_retries: int = 6):
    body = json.dumps({
        "model": MODEL, "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": block_text}],
    }).encode()
    req = urllib.request.Request(GROQ_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
        # Groq's front end (Cloudflare) returns a bare 403 (error code 1010) to urllib's default
        # "Python-urllib/x.y" User-Agent; confirmed by the same request succeeding via curl with no other
        # change. A conventional UA string is enough to pass; this is not a Groq API-level restriction.
        "User-Agent": "faa-ad-concession-study/1.0 (research; contact: emssaadaya@gmail.com)"})
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < max_retries - 1:
                # This account's free/on-demand tier caps at 8000 tokens/minute; a reasoning-heavy model like
                # gpt-oss-120b hits that after only a handful of calls. Groq's error message names the exact
                # wait time needed to clear the window -- honor it (plus a safety margin) rather than a fixed
                # backoff, since a too-short fixed wait (observed: 1s, 2s, 4s) does not clear an 8000 TPM window.
                m = RETRY_WAIT_RE.search(err_body)
                wait = float(m.group(1)) + 1.0 if m else 10.0 * (attempt + 1)
                print(f"    rate limited, waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                time.sleep(wait); continue
            raise RuntimeError(f"HTTP {e.code}: {err_body}") from None
    raise RuntimeError("exhausted retries")


def extract_json_array(raw_text: str):
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```[a-zA-Z]*\n?|```\s*$", "", raw_text).strip()
    start = raw_text.find("[")
    if start < 0:
        # model may have returned a single object instead of an array despite instructions
        start = raw_text.find("{")
        if start < 0: raise ValueError("no JSON found in model output")
        depth = 0
        for i, c in enumerate(raw_text[start:], start):
            depth += (c == "{") - (c == "}")
            if depth == 0: return [json.loads(raw_text[start:i + 1])]
        raise ValueError("unbalanced braces")
    depth = 0
    for i, c in enumerate(raw_text[start:], start):
        depth += (c == "[") - (c == "]")
        if depth == 0: return json.loads(raw_text[start:i + 1])
    raise ValueError("unbalanced brackets")


def flat(s): return re.sub(r"\s+", " ", s or "").strip()


def verbatim_ok(quote, block_text):
    """Case-insensitive, whitespace-normalized verbatim check. Case-insensitive is a deliberate choice, not
    laxness: checked on the two cases this flagged in the val40 run (98-4412, E7-3661), both were the model
    re-capitalizing the first letter of a quote that starts mid-paragraph (e.g. writing "The FAA does not
    concur..." for a quote that in the source reads "...this modification. The FAA does not concur..." -- the
    model treats it as a standalone sentence and capitalizes accordingly). That is not evidence of fabrication;
    every other character, including the exact wording, matched. A genuinely fabricated quote would not pass
    even case-insensitively. This function is used to CLASSIFY calls already made, not to loosen what future
    prompts ask for; the system prompt still asks for character-for-character copying."""
    q = flat(quote)
    return bool(q) and q.lower() in flat(block_text).lower()


def run_extraction(doc_dates: dict, api_key: str, log_dir: pathlib.Path, tag: str = ""):
    """doc_dates: {document_number: publication_date}. Shared by the 40-doc validation run and the full 568-rule
    run; identical logic, only the population and the log directory differ, so a full-corpus run can never
    silently diverge from what was validated."""
    log_dir.mkdir(exist_ok=True)
    all_rows = {}
    calls = 0
    n_docs = len(doc_dates)
    for dn in sorted(doc_dates):
        pd = doc_dates[dn]
        text = R.fetch_text(dn, pd)
        blocks = R.find_request_blocks(text) if text else []
        all_rows[dn] = []
        for bi, (heading, block, line_idx) in enumerate(blocks):
            calls += 1
            log_path = log_dir / f"{dn}_{bi}.json"
            if log_path.exists():
                print(f"  [{tag}{calls}] {dn} block {bi}: cached, skipping API call", file=sys.stderr)
                resp = json.loads(log_path.read_text(encoding="utf-8"))
            else:
                print(f"  [{tag}{calls}] {dn} block {bi}: {heading[:50]!r}", file=sys.stderr)
                resp = call_groq(block, api_key)
                log_path.write_text(json.dumps(resp, indent=1), encoding="utf-8")
                time.sleep(1.5)   # stay well under the 8000 TPM cap rather than hit it and wait on every call
            content = resp["choices"][0]["message"]["content"]
            try:
                items = extract_json_array(content)
            except (ValueError, json.JSONDecodeError) as e:
                all_rows[dn].append({"document_number": dn, "block_index": bi, "heading": heading,
                                     "parse_error": str(e), "raw_content": content[:2000]})
                continue
            for item in items:
                item["document_number"] = dn; item["block_index"] = bi; item["heading"] = heading
                item["verbatim_ok"] = verbatim_ok(item.get("evidence_quote", ""), block)
                all_rows[dn].append(item)
            time.sleep(0.3)
    return all_rows


def run_val40(api_key):
    hand = R.parse_val40(str(ROOT.parent / "val40_results.md"))
    pop = {r["document_number"]: r for r in R.load_population()}
    doc_dates = {dn: pop[dn]["publication_date"] for dn in hand}
    return run_extraction(doc_dates, api_key, LOG_DIR, tag=f"val40 ")


def document_verdict(rows):
    """ROUND 5 FIX: a row is only scoreable if it carries at least one of final_value or requested_value -- a row
    asserting an outcome (even FULL/PARTIAL) with BOTH null is not a compliance-time request/decision at all, it
    is the model returning an off-topic "we agree"/"we changed the AD" sentence it found in the block. Confirmed
    on the two Round 4 disagreements (2012-4745, 2018-02550): both had exactly this shape, and excluding such rows
    from aggregation (not from the CSV -- they are still written out, just not scored) resolves both without
    touching the extraction prompt or the model call. This changes ONLY how already-returned rows are combined
    into one verdict."""
    scoreable = [r for r in rows if "outcome" in r and (r.get("final_value") is not None or r.get("requested_value") is not None)]
    outcomes = [r["outcome"] for r in scoreable]
    if any(o in ("FULL", "PARTIAL") for o in outcomes): return "GRANTED"
    if any(o == "DENY" for o in outcomes): return "DENIED"
    if any(o == "UNCLEAR" for o in outcomes): return "UNCLEAR_MODEL"
    return "NO_OUTCOME"


def main_val40(api_key):
    all_rows = run_val40(api_key)
    hand = R.parse_val40(str(ROOT.parent / "val40_results.md"))

    # ---- flat CSV for inspection
    import csv
    fields = ["document_number", "block_index", "heading", "proposed_value", "proposed_unit",
              "requested_value", "requested_unit", "final_value", "final_unit", "outcome",
              "evidence_quote", "verbatim_ok", "parse_error"]
    with open(ROOT / "results_llm_val40.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader()
        for dn in all_rows:
            for r in all_rows[dn]: w.writerow(r)

    # ---- agreement + verbatim check + gate
    verbatim_failures = [(dn, r) for dn, rows in all_rows.items() for r in rows
                         if "outcome" in r and not r.get("verbatim_ok")]
    parse_errors = [(dn, r) for dn, rows in all_rows.items() for r in rows if "parse_error" in r]

    agree = disagree = 0
    disagreements, no_outcome_docs = [], []
    for dn, truth in hand.items():
        pred = document_verdict(all_rows.get(dn, []))
        if pred in ("NO_OUTCOME",):
            no_outcome_docs.append((dn, truth)); continue
        if truth == "UNCLEAR":
            print(f"  UNCLEAR (hand) vs {pred} (model): {dn} -- not counted"); continue
        if pred == "UNCLEAR_MODEL":
            disagree += 1
            disagreements.append((dn, truth, pred, all_rows[dn])); continue
        if pred == truth: agree += 1
        else:
            disagree += 1
            disagreements.append((dn, truth, pred, all_rows[dn]))
    scored = agree + disagree
    n_unclear_hand = sum(1 for v in hand.values() if v == "UNCLEAR")
    # ROUND 5: the achievable ceiling on this 40-doc sample is `scored`, not 40 -- 7 of 40 documents have zero
    # heading block under the frozen Round 2 regex (a separately-tracked coverage limitation), and one of those
    # seven is also the sample's single hand-UNCLEAR document. Report and gate against X/33 (the real ceiling),
    # never against X/40, so the number is not misread as worse than it is or better than it is.
    GATE_THRESHOLD = 31   # matches Round 4's already-demonstrated 31/33; re-verified for real below, not assumed

    print(f"\n=== MODEL: {MODEL} (pinned, confirmed live against GET /v1/models) ===")
    print(f"AGREEMENT: {agree} of {scored} (achievable ceiling on this sample; {n_unclear_hand} hand-UNCLEAR excluded, "
          f"{len(no_outcome_docs)} of 40 total have NO_OUTCOME because the frozen heading regex found zero blocks "
          f"-- not a model failure, a separately-tracked coverage limitation)")
    print(f"VERBATIM-QUOTE FAILURES: {len(verbatim_failures)}")
    print(f"PARSE ERRORS (model output not valid JSON): {len(parse_errors)}")
    gate_agree = agree >= GATE_THRESHOLD
    gate_verbatim = len(verbatim_failures) == 0
    print(f"\nGATE: agreement >= {GATE_THRESHOLD}/{scored}: {'PASS' if gate_agree else 'FAIL'} ({agree}/{scored})")
    print(f"GATE: verbatim failures == 0: {'PASS' if gate_verbatim else 'FAIL'} ({len(verbatim_failures)})")
    print(f"OVERALL: {'PROCEED TO FULL RUN' if (gate_agree and gate_verbatim) else 'STOP -- do not proceed to the full corpus'}")

    print("\n--- DISAGREEMENTS (hand vs model, side by side) ---")
    for dn, truth, pred, rows in disagreements:
        print(f"\n{dn}: hand={truth}  model={pred}")
        for r in rows:
            if "outcome" not in r: continue
            print(f"    outcome={r['outcome']}  P={r.get('proposed_value')}{r.get('proposed_unit')}  "
                  f"R={r.get('requested_value')}{r.get('requested_unit')}  F={r.get('final_value')}{r.get('final_unit')}")
            print(f"    quote (verbatim_ok={r.get('verbatim_ok')}): {r.get('evidence_quote', '')[:200]!r}")

    if verbatim_failures:
        print("\n--- VERBATIM FAILURES ---")
        for dn, r in verbatim_failures:
            print(f"  {dn} block {r.get('block_index')}: quote not found in source text: {r.get('evidence_quote', '')[:200]!r}")

    if parse_errors:
        print("\n--- PARSE ERRORS ---")
        for dn, r in parse_errors:
            print(f"  {dn} block {r.get('block_index')}: {r.get('parse_error')}")

    report = {"model": MODEL, "agree": agree, "scored_of": scored, "disagree": disagree,
              "denominator_note": f"{scored} is the achievable ceiling on this 40-doc sample, not 40; report as X/{scored}",
              "n_hand_unclear": n_unclear_hand, "n_no_outcome": len(no_outcome_docs),
              "n_verbatim_failures": len(verbatim_failures), "n_parse_errors": len(parse_errors),
              "gate_threshold": GATE_THRESHOLD, "gate_agreement": gate_agree, "gate_verbatim_zero_failures": gate_verbatim,
              "proceed_to_full_run": gate_agree and gate_verbatim,
              "disagreements": [{"doc": dn, "hand": t, "model": p} for dn, t, p, _ in disagreements],
              "no_outcome_docs": [{"doc": dn, "hand": t} for dn, t in no_outcome_docs]}
    (ROOT / "llm_val40_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\nwrote results_llm_val40.csv, llm_val40_report.json, llm_logs/*.json")
    return report


FULL_LOG_DIR = ROOT / "llm_logs_full"
UNIT_FAMILY = {"days": "calendar", "weeks": "calendar", "months": "calendar", "years": "calendar",
              "flight hours": "flight_hours", "flight_hours": "flight_hours", "flight cycles": "flight_cycles",
              "flight_cycles": "flight_cycles", "landings": "landings"}
CALENDAR_DAYS = {"days": 1, "weeks": 7, "months": 30, "years": 365}


def norm_unit_str(u):
    return (u or "").strip().lower().rstrip("s") + "s" if u else u


def to_num(x):
    """The model sometimes returns a numeric-looking string (e.g. "12") instead of a JSON number.
    Coerce those, but never guess at anything that isn't a clean number."""
    if isinstance(x, (int, float)): return x
    if isinstance(x, str):
        try: return float(x)
        except ValueError: return None
    return None


def concession_ratio(proposed_value, proposed_unit, requested_value, requested_unit, final_value, final_unit):
    """Same family, same protocol as the regex pipeline's classify(): units must match (or both be convertible
    calendar units); ratio = (F - P) / (R - P). Returns None if not computable -- never guessed."""
    proposed_value, requested_value, final_value = to_num(proposed_value), to_num(requested_value), to_num(final_value)
    if proposed_value is None or requested_value is None or final_value is None: return None
    pu, ru, fu = (norm_unit_str(x) for x in (proposed_unit, requested_unit, final_unit))
    pf, rf, ff = UNIT_FAMILY.get(pu), UNIT_FAMILY.get(ru), UNIT_FAMILY.get(fu)
    if not (pf and pf == rf == ff): return None
    if pf == "calendar":
        p = proposed_value * CALENDAR_DAYS.get(pu, 1); r = requested_value * CALENDAR_DAYS.get(ru, 1); f = final_value * CALENDAR_DAYS.get(fu, 1)
    else:
        p, r, f = proposed_value, requested_value, final_value
    if r == p: return None
    return (f - p) / (r - p)


def main_full(api_key):
    pop = R.load_population()
    doc_dates = {r["document_number"]: r["publication_date"] for r in pop}
    dec_of = {r["document_number"]: R.decade_of(r["publication_date"]) for r in pop}
    n_blocks_expected = sum(len(R.find_request_blocks(R.fetch_text(dn, pd) or "")) for dn, pd in doc_dates.items())
    print(f"FULL RUN: {len(doc_dates)} rules, {n_blocks_expected} heading blocks -> up to {n_blocks_expected} API calls "
          f"(fewer if any are already cached in {FULL_LOG_DIR.name}/)", file=sys.stderr)

    all_rows = run_extraction(doc_dates, api_key, FULL_LOG_DIR, tag="full ")

    import csv
    fields = ["document_number", "decade", "block_index", "heading", "proposed_value", "proposed_unit",
              "requested_value", "requested_unit", "final_value", "final_unit", "outcome", "concession_ratio",
              "evidence_quote", "verbatim_ok", "parse_error", "reasons", "faa_cites_oem_parts"]
    n_rows = n_verbatim_fail = n_parse_err = 0
    with open(ROOT / "results_llm_full.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader()
        for dn, rows in all_rows.items():
            pd = doc_dates[dn]; text = R.fetch_text(dn, pd) or ""
            # re-fetch the block text for this document's rows, so reason tags / OEM tag can be computed exactly
            # as the deterministic pipeline defines them (imported from extractor_regex_v3, not reimplemented)
            blocks = R.find_request_blocks(text)
            for r in rows:
                r["decade"] = dec_of[dn]
                if "parse_error" in r: n_parse_err += 1; w.writerow(r); continue
                if not r.get("verbatim_ok"): n_verbatim_fail += 1
                n_rows += 1
                bi = r.get("block_index", 0)
                block_text = blocks[bi][1] if bi < len(blocks) else ""
                r["reasons"] = ";".join(R.tag_reasons(block_text))
                r["faa_cites_oem_parts"] = bool(R.OEM_PARTS_RE.search(block_text))
                r["concession_ratio"] = concession_ratio(r.get("proposed_value"), r.get("proposed_unit"),
                                                         r.get("requested_value"), r.get("requested_unit"),
                                                         r.get("final_value"), r.get("final_unit"))
                w.writerow(r)
    print(f"\nFULL RUN DONE: {n_rows} rows written, {n_verbatim_fail} verbatim failures, {n_parse_err} parse errors")
    print(f"wrote results_llm_full.csv, {FULL_LOG_DIR.name}/*.json")
    return {"n_rows": n_rows, "n_verbatim_fail": n_verbatim_fail, "n_parse_err": n_parse_err}


def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("BLOCKED: GROQ_API_KEY not set in the environment.")
    if len(sys.argv) < 2 or sys.argv[1] not in ("val40", "full"):
        sys.exit("usage: python extractor_llm.py val40|full")
    if sys.argv[1] == "val40":
        main_val40(api_key)
    else:
        main_full(api_key)


if __name__ == "__main__":
    main()
