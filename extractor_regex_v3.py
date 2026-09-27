#!/usr/bin/env python3
"""
Deterministic (no-LLM) extractor for contested compliance-time requests in FAA part 39 Airworthiness Directive
(AD) final rules.

INPUT POPULATION: the ~568-rule "contested" list already identified in this project (Phase 0/0b: rules in bucket
A or B -- a "Request to Extend/Revise/Change ... Compliance Time" heading was found and a proposal was published).
That list is read directly from two existing JSON files (not rebuilt here):
    ../phase0/final_classes.json          document_number -> bucket ("A"/"B"/"C"/"")
    ../phase0b/check_denominator.json     document_number -> {contestable: bool, publication_date}
Full text: read from the local cache at ../phase0b/cache/govinfo/<doc>.txt (already fetched for all 13,151 rules
in this project's earlier phase; covers all 568 contested rules too). If a document is missing from the cache,
this script fetches it from govinfo directly over HTTPS (stdlib urllib only, no dependencies) and caches it next
to the others, so it also works with a fresh checkout that has no cache at all.

USAGE
    python extractor.py run        --out results.csv --review review_list.csv
    python extractor.py validate40 --zip <path-to-validation_rules_40.zip> --val val40_results.md
    python extractor.py missaudit  --n 60
    python extractor.py redundancy --out results.csv

Every regex and every design choice that was NOT fully specified by the task is called out in a comment where it
appears, so it can be inspected and argued with, not just trusted.
"""
from __future__ import annotations
import argparse, csv, io, json, os, pathlib, re, statistics, sys, urllib.error, urllib.request, zipfile
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
PROJECT = ROOT.parent                                  # .../projet plane
CACHE = PROJECT / "phase0b" / "cache" / "govinfo"
UA = "faa-ad-concession-study/1.0 (research; contact: fennaya@users.noreply.github.com)"

# --------------------------------------------------------------------------------------------------------- fetch

def govinfo_url(doc_number: str, pub_date: str) -> str:
    return f"https://www.govinfo.gov/content/pkg/FR-{pub_date}/html/{doc_number}.htm"

_TAG = re.compile(r"<[^>]+>")
_PAGE = re.compile(r"[ \t]*\[\[Page \d+\]\][ \t]*\n?")   # govinfo page-break markers land mid-sentence; strip them

def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|tr|h\d)>", "\n", raw)
    import html
    txt = html.unescape(_TAG.sub("", raw))
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = "\n".join(l.strip() for l in txt.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", txt).strip()

def fetch_text(doc_number: str, pub_date: str) -> str | None:
    """Local cache first (already 100% populated for this project's 13,151-rule corpus); live govinfo fetch as a
    fallback so this script is self-sufficient on a machine with no cache at all."""
    p = CACHE / f"{doc_number.replace('/', '_')}.txt"
    if p.exists():
        t = p.read_text(encoding="utf-8")
        return None if t == "__MISSING__" else _PAGE.sub(" ", t)
    CACHE.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(govinfo_url(doc_number, pub_date), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = _strip_html(r.read().decode("utf-8", "replace"))
        if len(txt) < 200: raise ValueError("suspiciously short body")
        p.write_text(txt, encoding="utf-8")
        return _PAGE.sub(" ", txt)
    except Exception as e:
        print(f"  ! fetch failed for {doc_number}: {e}", file=sys.stderr)
        p.write_text("__MISSING__", encoding="utf-8")
        return None

def load_population():
    """The 568-rule contested population: Phase 0 bucket A or B, intersected with Phase 0b's corrected
    'contestable' (proposal-published) flag. See PHASE0B_REPORT.md for how 569 -> 568 (one A-bucket rule,
    04-13915, revises an earlier AD with no NPRM and is not contestable)."""
    cls = json.load(open(PROJECT / "phase0" / "final_classes.json", encoding="utf-8"))
    den = {r["document_number"]: r for r in json.load(open(PROJECT / "phase0b" / "check_denominator.json", encoding="utf-8"))["rows"]
           if r["contestable"]}
    rows = [{"document_number": dn, "publication_date": den[dn]["publication_date"]}
            for dn, b in cls.items() if b in ("A", "B") and dn in den]
    rows.sort(key=lambda r: r["publication_date"])
    return rows

def decade_of(date_str: str) -> str:
    y = int(date_str[:4])
    return f"{y // 10 * 10}s"

# ------------------------------------------------------------------------------------------------ heading & block

# Task-specified heading pattern, applied case-insensitively (the task gave both "To" and "to" as alternatives,
# which only makes sense if the match is meant to be case-insensitive across Title Case and sentence case
# headings -- both forms occur in these documents). Matched against a SINGLE physical line (see looks_like_heading
# rationale below): headings in these documents are formatted as their own line, so working line-by-line avoids
# the line-wrap artifact these govinfo texts have in body paragraphs, at the cost of missing a heading that itself
# happens to wrap across two lines. That miss rate is exactly what Step 2c (heading-miss audit) measures.
HEADING_RE = re.compile(
    r"(?:Requests?\s+(?:to|for)\s+)?(?:Extend|Extension|Increase|Reduce|Shorten|Revise|Change)\b.{0,60}?Compliance\s+Time",
    re.IGNORECASE,
)
# BUG 2 fix (coverage, applied for real after a diagnostic-only test confirmed it): the task's literal regex
# requires the word "Request(s)" in the heading. A substantial share of real headings in this corpus omit it
# entirely -- "Extend Compliance Time", "Revise Compliance Time in Paragraph (a)(2)(ii)" -- a heading style
# already documented in this project's earlier phases. Diagnostic block-detection with the prefix made optional:
# 73.8% -> 79.9% of the 568-rule population (still short of the 80% gate on its own; missaudit re-run after this
# change shows what phrasing remains uncovered). Made optional above with `(?:...)?` rather than kept as a
# separate branch, so every downstream use of HEADING_RE / HEADING_LINE_RE benefits automatically.
# The task's pattern above also matches this phrase in the middle of an ordinary sentence (e.g. "We disagree with
# DAL's request to extend the compliance time." legitimately contains the substring "request to extend ... the
# compliance time"). Real headings in these documents are their own short line, starting with the word
# Request(s); body sentences that happen to contain the phrase do not start the line with it. So a hit only
# counts as a HEADING if the match starts at (or very near) the start of the stripped line -- allowing a small
# numeral/"Comment Issue No. N:" prefix, which some documents use. This anchoring is my addition, not in the
# task's regex text, and it is exactly what makes the difference between "found the heading" and "found the word
# request inside the FAA's rebuttal" -- I verified this on real documents before the full run (see extractor
# sanity check output).
HEADING_LINE_RE = re.compile(
    r"^(?:\d+\.\s*)?(?:Comment\s+Issue\s+No\.?\s*\d+\s*:\s*)?" + HEADING_RE.pattern, re.IGNORECASE
)

# "Does this line look like the START of a new section?" -- used only to decide where a matched request's
# paragraph block ENDS. Two tests, either is sufficient: (1) a short Title-Case-ish line (no terminal sentence
# punctuation, mostly capitalized words -- these AD documents format every subheading this way); (2) one of the
# section titles that reliably close out a comment-by-comment discussion in this document type. This heuristic is
# NOT part of the task's spec; it is my own choice for "until the next heading," and it is a source of block
# boundary error like any heuristic -- long response paragraphs that happen to contain a short all-caps phrase
# could truncate early. Not tuned against the 40 (that file does not exist in this session; see run() output).
KNOWN_CLOSERS = re.compile(
    r"^(costs? of compliance|regulatory findings|regulatory flexibility (?:act|determination)|"
    r"executive orders?|unfunded mandates|paperwork reduction act|environmental review|"
    r"authority for this rulemaking|related rulemaking|list of subjects|the amendment|"
    r"adoption of the amendment|differences between this ad and|comments?$|discussion(?: of (?:comments|final))?|"
    r"finding of no significant impact|regulatory evaluation|comments? received)\b",
    re.IGNORECASE,
)

def looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not (4 <= len(s) <= 90): return False
    if KNOWN_CLOSERS.match(s): return True
    if s.endswith((".", ",", ";", ":")): return False
    words = s.split()
    if len(words) < 2: return False
    small = {"of", "the", "to", "for", "and", "in", "on", "a", "an", "or", "by", "with", "this", "from", "at"}
    capish = sum(1 for w in words if w[:1].isupper() or w.lower() in small)
    return capish / len(words) >= 0.7

def find_request_blocks(text: str):
    """Yield (heading_text, block_text, start_line_idx) for every heading-line match, block = the lines from the
    heading up to (not including) the next line that looks_like_heading, or up to the next HEADING_RE match,
    whichever comes first."""
    lines = text.split("\n")
    # A real heading in these documents sits on its own line with a blank line before AND after it (verified
    # against real documents during the extractor's development -- see extractor's module docstring / dev notes).
    # Without this check, a body sentence that happens to WRAP such that a line break lands right before the
    # words "request to increase the compliance time" produces a false heading (observed on a real document,
    # 2020-23947, line 158: a mid-sentence wrap, no surrounding blank lines). This costs a small number of
    # genuine headings that are not blank-line-isolated in some older/oddly-formatted documents; that trade-off
    # is exactly what the miss-rate audit (missaudit) is for.
    def isolated(i):
        prev_blank = (i == 0) or (lines[i - 1].strip() == "")
        next_blank = (i == len(lines) - 1) or (lines[i + 1].strip() == "")
        return prev_blank and next_blank
    hits = [i for i, l in enumerate(lines) if HEADING_LINE_RE.match(l.strip()) and isolated(i)]
    hitset = set(hits)
    out = []
    for i in hits:
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if j in hitset: end = j; break
            # A candidate "next section" line must ALSO be isolated to count -- otherwise an ordinary sentence
            # that happens to open with several Title-Case words (an organization's full name: "The Air
            # Transport Association (ATA) of America, on behalf of...") is mistaken for a new heading and closes
            # the block after zero lines of real content. Observed on a real document (96-27645): that exact
            # sentence, wrapped across two lines, was flagged as a heading by the capitalization test alone
            # because it isn't followed by a blank line -- adding the isolation requirement here fixed it.
            if looks_like_heading(lines[j]) and isolated(j): end = j; break
        out.append((lines[i].strip(), "\n".join(lines[i:end]).strip(), i))
    return out

# ------------------------------------------------------------------------------------------------- time expressions

UNIT_WORDS = {
    "day": "days", "days": "days", "week": "weeks", "weeks": "weeks", "month": "months", "months": "months",
    "year": "years", "years": "years", "flight hour": "flight_hours", "flight hours": "flight_hours",
    "flight-hour": "flight_hours", "flight-hours": "flight_hours", "fh": "flight_hours",
    "flight cycle": "flight_cycles", "flight cycles": "flight_cycles", "flight-cycle": "flight_cycles",
    "flight-cycles": "flight_cycles", "fc": "flight_cycles", "landing": "landings", "landings": "landings",
    # Bare "hour(s)" and "cycle(s)" (no "flight" prefix) are common shorthand in these documents for flight
    # hours / flight cycles time-in-service (e.g. "500 hours time-in-service"); in this domain they essentially
    # never mean calendar hours. Mapping them here is a deliberate, documented choice, not an oversight.
    "hour": "flight_hours", "hours": "flight_hours", "cycle": "flight_cycles", "cycles": "flight_cycles",
}
NUM_RE = r"(\d[\d,]*(?:\.\d+)?)"
UNIT_ALT = "|".join(sorted((re.escape(k) for k in UNIT_WORDS), key=len, reverse=True))
TIME_RE = re.compile(rf"{NUM_RE}\s*(?:-|to)?\s*({UNIT_ALT})s?\b", re.IGNORECASE)
EVENT_RE = re.compile(r"\bbefore\s+further\s+flight\b|\bat\s+the\s+next\s+(?:scheduled\s+)?(?:heavy\s+)?maintenance\s+visit\b|"
                     r"\bnext\s+(?:scheduled\s+)?(?:c-check|heavy\s+maintenance|special\s+visit)\b", re.IGNORECASE)
# "from A UNIT to B UNIT" -- the commenter's own restatement of both the proposed and requested figures. Allows
# a short filler after "from" (e.g. "from the proposed 500 hours") and between the first unit and "to" (e.g.
# "500 hours time-in-service to 3,000 hours"), both observed on real documents (96-32048); {0,25} keeps this
# from reaching across an unrelated second sentence.
FROM_TO_RE = re.compile(
    rf"from\s+(?:[a-z]+\s+){{0,3}}{NUM_RE}\s*(?:-|to)?\s*({UNIT_ALT})s?\b.{{0,25}}?\s+to\s+{NUM_RE}\s*(?:-|to)?\s*({UNIT_ALT})s?\b",
    re.IGNORECASE,
)

def norm_unit(u: str) -> str:
    return UNIT_WORDS.get(u.lower().rstrip("s"), UNIT_WORDS.get(u.lower(), u.lower()))

def first_time(s: str):
    """First numeric time expression, or an EVENT pseudo-time, or None. Returns (value_or_None, unit_or_eventstr)."""
    m = TIME_RE.search(s)
    if m:
        try: return float(m.group(1).replace(",", "")), norm_unit(m.group(2))
        except ValueError: pass
    m = EVENT_RE.search(s)
    if m: return None, "event:" + re.sub(r"\s+", "_", m.group(0).lower())
    return None, None

# --- Anchored extraction (fix for a confirmed bug, hand-checked against real text: 00-18039). Grabbing "the first
# number near a request/proposal-ish sentence" is not safe: a sentence can state a RANGE across several distinct
# commenters ("suggestions ... range from 10 months to 2 years or 6,000 flight hours") that has nothing to do
# with the single P->R pair, while the true proposed figure sits in its own separate sentence ("The FAA proposed
# a compliance time of 3,000 flight hours"). first_time()/FROM_TO_RE alone silently picked "10 months" as P and
# "2 years" as R here -- plausible-looking, wrong. Fix: a number only counts as P if the word "propos-" appears
# within ANCHOR_WINDOW characters immediately before it; a number only counts as R if an extend/increase/reduce/
# shorten/request(ed) verb appears within ANCHOR_WINDOW characters immediately before it. No anchor, no value --
# left None (UNPARSEABLE downstream) rather than guessed. Confirmed by re-reading 00-18039 and 9 further
# review_list.csv rows against source text (see findings.md, Step 2 bug writeups) before trusting this change.
ANCHOR_WINDOW = 80
PROPOSE_ANCHOR = re.compile(r"propos", re.IGNORECASE)
REQUEST_ANCHOR = re.compile(r"\b(extend(?:ed|ing|s)?|extension|increase[ds]?|reduce[ds]?|shorten(?:ed|ing|s)?|"
                            r"request(?:ed|s|ing)?|revis(?:e[ds]?|ing)|chang(?:e[ds]?|ing))\b", re.IGNORECASE)

def anchored_time(s: str, anchor_re, window: int = ANCHOR_WINDOW):
    """First time expression in s whose ANCHOR word appears within `window` chars immediately before it.
    Returns (value, unit, span) where span=(start,end) is the match's position IN s, or (None, None, None).
    Never falls back to an unanchored guess. The span is returned so callers can detect when two independent
    anchored searches (e.g. P and R) land on the exact same number (see the overlap check in extract_rule,
    BUG 3 fix #3)."""
    for m in TIME_RE.finditer(s):
        pre = s[max(0, m.start() - window): m.start()]
        if anchor_re.search(pre):
            try: return float(m.group(1).replace(",", "")), norm_unit(m.group(2)), (m.start(), m.end())
            except ValueError: continue
    return None, None, None

# BUG 3 fix #4, confirmed on 98-24059: F_preamble used to take the FIRST number after the request sentence, which
# can be one of several ALTERNATIVES a commenter suggested or the FAA is merely discussing ("Compliance times of
# 36 months, 48 months, and 72 months are suggested as appropriate"), not the figure actually granted. Prefer a
# number in a sentence carrying an explicit agree/grant verb (AGREE_RE); a number in a sentence that is clearly
# describing a suggestion/alternative is the LAST resort, not the default.
SUGGESTION_RE = re.compile(r"\bsuggest(?:ed|s|ion)?\b|\bcommenters?\s+(?:state|states|stated)\b|\brecommend(?:ed|s)?\b|"
                           r"\bare\s+suggested\b|\bappropriate\s+for\s+the\s+extension\b", re.IGNORECASE)

def preferred_time(text: str):
    """First time expression preferring: (1) a sentence with an explicit agree/grant verb, then (2) a sentence
    that is neither a grant nor a suggestion/alternative, then (3) a suggestion/alternative sentence as a last
    resort (explicitly the least trusted, kept only so a value is not thrown away when nothing better exists)."""
    sentences = re.split(r"(?<=[.;])\s+", text)
    grant = other = suggestion = None
    for s in sentences:
        v, u = first_time(s)
        if v is None: continue
        if AGREE_RE.search(s):
            if grant is None: grant = (v, u)
        elif SUGGESTION_RE.search(s):
            if suggestion is None: suggestion = (v, u)
        else:
            if other is None: other = (v, u)
    return grant or other or suggestion or (None, None)

def split_subblocks(block: str):
    """BUG 3 fix #1, confirmed on 2012-18583: a block can discuss more than one distinct issue (each tied to its
    own paragraph reference), and collapsing the whole block to ONE verdict misreads which issue the verdict was
    about. If the block mentions more than one distinct paragraph reference, split it at each new reference's
    first occurrence into separate self-contained spans (the heading and any lead-in text before the first
    reference stays attached to the FIRST span, so no context is lost); each span is then processed as its own
    row. A block with zero or one distinct reference is returned unchanged as a single span, matching prior
    behavior exactly (most blocks: extraction is unaffected by this fix)."""
    seen = {}
    for m in PARA_REF_RE.finditer(block):
        key = (m.group(1), m.group(2))
        seen.setdefault(key, m.start())
    if len(seen) <= 1:
        return [(None, block)]
    ordered = sorted(seen.items(), key=lambda kv: kv[1])
    spans = []
    for i, (key, _) in enumerate(ordered):
        start = 0 if i == 0 else ordered[i][1]
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(block)
        spans.append((key, block[start:end]))
    return spans

# ------------------------------------------------------------------------------------------- direction & reasons

VERB_DIRECTION = {"extend": "extend", "extension": "extend", "increase": "extend",
                 "reduce": "shorten", "shorten": "shorten", "revise": None, "change": None}
VERB_RE = re.compile(r"(?:Requests?\s+(?:to|for)\s+)?(Extend|Extension|Increase|Reduce|Shorten|Revise|Change)\b", re.IGNORECASE)

# Reason keyword dictionaries, exactly as specified in the task (COST had no keyword list in the task text; the
# list below is my own choice, flagged here so it can be swapped).
REASON_PATTERNS = {
    "PARTS": re.compile(r"\b(parts?|kits?|lead[- ]time|supplier|availabilit(?:y|ies)|spares?|back[- ]?order(?:ed)?)\b", re.IGNORECASE),
    "MAINT": re.compile(r"\bC[- ]check\b|\bheavy\s+maintenance\b|\bscheduled\s+maintenance\b|\bspecial\s+visit\b", re.IGNORECASE),
    "HARMONIZE": re.compile(r"\bEASA\b|\bDGAC\b|\bTCCA\b|\bforeign\b|\bmatch(?:ing|es)?\s+(?:the\s+)?(?:EASA\s+)?AD\b", re.IGNORECASE),
    "NOFAIL": re.compile(r"\bno\s+failures?\b|\bno\s+findings?\b|\bnot\s+experienced\b", re.IGNORECASE),
    # COST: not specified by the task; my own reasonable choice, flagged.
    "COST": re.compile(r"\bcosts?\b|\bcostly\b|\bexpensive\b|\bburden(?:some)?\b|\beconomic\s+hardship\b", re.IGNORECASE),
}
# "the response says the manufacturer states parts will be available" -- a specific, narrow pattern by design
# (deliberately conservative: it looks for an explicit "<manufacturer-like actor> states ... parts/kits ...
# available", not any mention of parts availability anywhere in the response).
OEM_PARTS_RE = re.compile(
    r"(manufacturer|OEM|type\s+certificate\s+holder|Boeing|Airbus|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+(?:Aerospace|Corporation|Company|Inc\.?))"
    r"[^.]{0,90}\b(parts?|kits?|units?)\b[^.]{0,60}\b(?:will\s+be|are|is|become)\b[^.]{0,30}\bavailable\b",
    re.IGNORECASE,
)

PARA_REF_RE = re.compile(r"paragraph\s*\(([a-z])\)(?:\((\d+)\))?", re.IGNORECASE)

def tag_reasons(block_text: str):
    return sorted(name for name, pat in REASON_PATTERNS.items() if pat.search(block_text))

# ---------------------------------------------------------------------------------------------------- FAA response

AGREE_RE = re.compile(r"\bwe\s+agree\b|\bthe\s+FAA\s+agrees?\b|\bpartially\s+agrees?\b|\bhave\s+(?:revised|changed)\b|\bhas\s+been\s+revised\b", re.IGNORECASE)
DISAGREE_RE = re.compile(r"\bwe\s+disagree\b|\bthe\s+FAA\s+disagrees?\b|\bdo(?:es)?\s+not\s+agree\b|\bhas\s+not\s+(?:been\s+)?(?:revised|changed)\b|\bno\s+change(?:d)?\b|\bmoot\b|\bwithdraw(?:n|s)?\b", re.IGNORECASE)
MOOT_RE = re.compile(r"\bmoot\b|\bno\s+longer\s+(?:necessary|needed|applicable)\b|\bwithdraw(?:n|s)?\s+(?:the\s+)?request\b", re.IGNORECASE)

def response_span(block_text: str, request_end_pos: int) -> str:
    """The part of the block after the request sentence -- where the FAA's reply lives (see module docstring:
    heading, request, and FAA reply are all inside ONE block in these documents)."""
    return block_text[request_end_pos:]

def split_request_sentence(block_text: str) -> tuple[str, int]:
    """First sentence containing a 'request(s)' verb, THAT ALSO ENDS BEFORE THE FAA'S OWN VERDICT (agree/disagree
    language), after the heading line; returns (that sentence, its end offset in block_text) so response_span()
    can grab everything after it. BUG 3 fix #2, confirmed on 01-18017 and 01-1662: without the verdict-position
    requirement, the old code could grab the FAA's own paraphrase of the request ("Insufficient data were
    submitted to support the commenter's request") or standard AMOC boilerplate ("the FAA may approve requests
    for adjusting the compliance time"), both of which contain the word 'request' but are not the original ask.
    Falls back to the whole block if no such sentence is found (heading-only blocks, a request phrased without
    the word 'request', or one where the FAA's verdict comes first with no distinct pre-verdict 'request' sentence
    at all -- in which case there is nothing safe to call the request sentence, and callers must cope with that)."""
    body = block_text.split("\n", 1)[1] if "\n" in block_text else ""
    am, dm = AGREE_RE.search(body), DISAGREE_RE.search(body)
    verdict_pos = min((m.start() for m in (am, dm) if m), default=len(body))
    for m in re.finditer(r"[^.]*?\brequests?\b[^.]*\.", body, re.IGNORECASE):
        if m.end() <= verdict_pos:
            return m.group(0), (block_text.find(body) + m.end())
    return body[:300], len(block_text)

# ------------------------------------------------------------------------------------------------------- one rule

def extract_rule(doc_number: str, pub_date: str, text: str):
    """Returns a list of request-row dicts (one per distinct request found), possibly empty. Splits a block into
    sub-block rows when it discusses more than one distinct paragraph reference (BUG 3 fix #1)."""
    rows = []
    for heading, block, line_idx in find_request_blocks(text):
        for split_key, subblock in split_subblocks(block):
            rows.append(_extract_from_span(doc_number, pub_date, heading, subblock, split_key))
    return rows

def _extract_from_span(doc_number: str, pub_date: str, heading: str, block: str, split_key):
    """The original per-block extraction logic, now run once per sub-block span (see split_subblocks)."""
    vm = VERB_RE.search(heading)
    verb = vm.group(1).lower() if vm else None
    direction = VERB_DIRECTION.get(verb)
    req_sentence, req_end = split_request_sentence(block)
    resp = response_span(block, req_end)
    req_start = req_end - len(req_sentence)   # req_sentence is a contiguous slice of block ending at req_end

    p_val = p_unit = r_val = r_unit = None
    p_span = r_span = None    # absolute spans IN `block`, for the overlap check below (BUG 3 fix #3)
    f_search_start = 0     # where in `resp` F_preamble extraction should start (see widening branch below)
    ftm = FROM_TO_RE.search(block)
    # FROM_TO_RE alone is not a safe signal that the FIRST number is P and the SECOND is R: a "from A to B"
    # span can also describe a RANGE across several commenters' distinct asks ("suggestions ... range from
    # 10 months to 2 years"), which is not a proposed->requested pair at all. Require a revise/extend-type
    # verb (the same REQUEST_ANCHOR used below) within ANCHOR_WINDOW chars before the "from", so a bare
    # "range from" or "vary from" is rejected. This was not a hypothetical: it is exactly what produced the
    # wrong P/R pair on 00-18039 before this fix.
    # A REQUEST_ANCHOR word present anywhere in the preceding window is not sufficient by itself: on
    # 00-18039, "extending" appears earlier in the SAME sentence ("commenters' suggestions for extending the
    # compliance time range from 10 months to 2 years") but modifies a different clause than this specific
    # "from...to..." span, which is a RANGE across commenters, not a proposed->requested delta. The word
    # directly touching "from" is the real signal: reject immediately if it is "range"/"ranging"/"vary"/
    # "varies"/"varying"/"spread", confirmed as the exact construction that produced the wrong P/R pair here.
    pre_from = block[max(0, ftm.start() - ANCHOR_WINDOW): ftm.start()] if ftm else ""
    is_range = ftm and re.search(r"\b(range|ranging|var(?:y|ies|ying)|spread)\s*$", pre_from, re.IGNORECASE)
    if ftm and not is_range and REQUEST_ANCHOR.search(pre_from):
        p_val, p_unit = float(ftm.group(1).replace(",", "")), norm_unit(ftm.group(2))
        r_val, r_unit = float(ftm.group(3).replace(",", "")), norm_unit(ftm.group(4))
    else:
        r_val, r_unit, r_local_span = anchored_time(req_sentence, REQUEST_ANCHOR)
        if r_local_span: r_span = (req_start + r_local_span[0], req_start + r_local_span[1])
        if r_val is None:
            # The request sentence itself often just says "...be extended" with the actual figure given in
            # the NEXT sentence (observed on real documents, e.g. 96-3613, 96-23243: "...requests that the
            # compliance time be extended." followed by a separate sentence with the number). Widen the
            # search into the rest of the block, but stop at the first sign the FAA's OWN reply has started
            # (agree/disagree language), so a granted/denied figure is never mistaken for the request itself.
            # F_preamble extraction below then starts searching AFTER that same cut point, for the same
            # reason in reverse: so the request's own restated number can never be mistaken for the FAA's.
            # Still anchored: an unanchored number in this span (e.g. a flight-cycle count mentioned for an
            # unrelated reason) must not be grabbed as R just because it is the first number present.
            am, dm = AGREE_RE.search(resp), DISAGREE_RE.search(resp)
            cut = min((m.start() for m in (am, dm) if m), default=len(resp))
            r_val, r_unit, r_local_span = anchored_time(resp[:cut], REQUEST_ANCHOR)
            if r_val is not None:
                f_search_start = cut
                r_span = (req_end + r_local_span[0], req_end + r_local_span[1])
        if p_val is None:
            # P: anchored on "propos-", searched over the WHOLE block (the true proposed figure can appear
            # either in the commenter's own restatement or, as on 00-18039, in a separate FAA sentence:
            # "The FAA proposed a compliance time of 3,000 flight hours").
            p_val, p_unit, p_span = anchored_time(block, PROPOSE_ANCHOR)
        # BUG 3 fix #3, confirmed on 97-3844 and 05-12635: P's "propos-" anchor and R's request-verb anchor can
        # both fire on the exact same number (one word can satisfy both anchors within their windows of the same
        # figure), producing P == R when there is really only ONE anchored number in the block, not two distinct
        # ones. If the two spans overlap, keep only P (the more specific anchor: "propos-" is rarer than a
        # generic extend/request/revise verb) and blank out R rather than duplicate the same figure into both.
        if p_span and r_span and p_span[0] < r_span[1] and r_span[0] < p_span[1]:
            r_val, r_unit = None, None

    para = PARA_REF_RE.search(block)
    para_ref = f"({para.group(1)})" + (f"({para.group(2)})" if para.group(2) else "") if para else (
        f"({split_key[0]})" + (f"({split_key[1]})" if split_key[1] else "") if split_key else None)

    if direction is None and p_val is not None and r_val is not None and p_unit == r_unit:
        direction = "extend" if r_val > p_val else ("shorten" if r_val < p_val else "unclear")
    elif direction is None:
        direction = "unclear"

    # Agree/disagree/moot framing is searched over the WHOLE (sub-)block, not just the text after the request
    # sentence: in some documents the FAA's verdict sentence ("The FAA disagrees...") comes BEFORE the
    # sentence that (re)states the request in full (observed on a real document, 2020-23947: "The FAA
    # disagrees with increasing the compliance time... The FAA infers that DAL's ... request to increase...").
    # Restricting this search to the post-request span would have silently missed the verdict there.
    denied = bool(DISAGREE_RE.search(block)) and not AGREE_RE.search(block)
    moot = bool(MOOT_RE.search(block))
    # BUG 3 fix #4, confirmed on 98-24059: prefer a number in a sentence with an explicit agree/grant verb over
    # one that is merely a suggestion or alternative under discussion (preferred_time(), defined above).
    f_val, f_unit = preferred_time(resp[f_search_start:])
    if f_val is None and not moot:
        # No new figure stated. If the FAA explicitly disagreed / made no change, the outcome is "unchanged
        # from proposed" (this is what DENY means numerically); F = P by construction, not an assumption that
        # the request failed for any other reason. If neither agree nor disagree language is found either,
        # this stays unresolved -> UNPARSEABLE downstream (no invented figure).
        if denied and p_val is not None:
            f_val, f_unit = p_val, p_unit

    return {
            "document_number": doc_number, "publication_date": pub_date, "decade": decade_of(pub_date),
            "heading": heading, "verb": verb, "direction": direction, "paragraph_ref": para_ref,
            "proposed_value": p_val, "proposed_unit": p_unit,
            "requested_value": r_val, "requested_unit": r_unit,
            "faa_preamble_value": f_val, "faa_preamble_unit": f_unit,
            "faa_regtext_value": None, "faa_regtext_unit": None,   # filled by fill_regtext()
            "denied_language": denied, "moot": moot,
            "reasons": ";".join(tag_reasons(block)),
            "faa_cites_oem_parts": bool(OEM_PARTS_RE.search(resp)),
            "request_sentence": req_sentence.strip().replace("\n", " ")[:400],
            "response_excerpt": resp.strip().replace("\n", " ")[:400],
    }

def fill_regtext(row: dict, full_text: str):
    """Best-effort F_regtext: find the codified paragraph matching row['paragraph_ref'] in the amendatory/
    regulatory text near the end of the document, and take the first time expression inside it. Heuristic,
    documented as such; its disagreement rate with F_preamble is Step 2b's redundancy check, not swept under."""
    ref = row["paragraph_ref"]
    if not ref: return
    letter_digit = re.match(r"\(([a-z])\)(?:\((\d+)\))?", ref, re.IGNORECASE)
    if not letter_digit: return
    letter, digit = letter_digit.group(1), letter_digit.group(2)
    pat = re.compile(rf"^\(\s*{re.escape(letter)}\s*\)" + (rf"[^\n]{{0,80}}\(\s*{re.escape(digit)}\s*\)" if digit else ""),
                     re.IGNORECASE | re.MULTILINE)
    matches = list(pat.finditer(full_text))
    if not matches: return
    if digit is None and len(matches) > 1:
        # BUG 3 fix #5, confirmed on 01-1662: a bare top-level letter ("(b)") with no sub-paragraph digit can be
        # reused elsewhere in the document for an unrelated requirement (e.g. a different paragraph (b) covering
        # reporting rather than the compliance time being discussed here). With no digit to disambiguate which
        # occurrence is the right one, guessing (the old code took the LAST) risks grabbing the wrong provision.
        # Leave faa_regtext blank rather than guess; a digit-qualified reference is unaffected by this check.
        return
    # take the LAST match: discussion text cites "(g)(1) of the NPRM" earlier in the doc, the codified amendment
    # (what we want) is the one nearer the end.
    m = matches[-1]
    window = full_text[m.start(): m.start() + 400]
    v, u = first_time(window)
    row["faa_regtext_value"], row["faa_regtext_unit"] = v, u

def infer_denied_proposed(row: dict):
    """Call AFTER fill_regtext(). If the request was denied (no change made) and P was never found in prose, the
    codified regulatory text IS the proposed time (nothing changed, so final == proposed, by definition of DENY --
    not an assumption). Also backfills F_preamble the same way when the FAA's reply never restated a figure."""
    if row["denied_language"] and row["faa_regtext_value"] is not None:
        if row["proposed_value"] is None:
            row["proposed_value"], row["proposed_unit"] = row["faa_regtext_value"], row["faa_regtext_unit"]
        if row["faa_preamble_value"] is None:
            row["faa_preamble_value"], row["faa_preamble_unit"] = row["faa_regtext_value"], row["faa_regtext_unit"]

# --------------------------------------------------------------------------------------------------------- outcome

def classify(row: dict) -> str:
    p, ru, fu = row["proposed_value"], row["requested_unit"], row["faa_preamble_unit"]
    r = row["requested_value"]; f = row["faa_preamble_value"]
    if p is None or r is None or f is None: return "UNPARSEABLE"
    if row["proposed_unit"] != ru or ru != fu: return "UNPARSEABLE"   # "when units match" -- task's own gate
    if r == p: return "UNPARSEABLE"
    c = (f - p) / (r - p)
    row["concession_ratio"] = round(c, 4)
    if c <= 0: return "DENY"
    if c >= 1: return "FULL"
    return "PARTIAL"

# ------------------------------------------------------------------------------------------------------------ run

def run(out_csv, review_csv):
    pop = load_population()
    print(f"population: {len(pop)} contested rules")
    all_rows, no_block, fetch_fail = [], [], []
    for i, r in enumerate(pop, 1):
        text = fetch_text(r["document_number"], r["publication_date"])
        if text is None:
            fetch_fail.append(r["document_number"]); continue
        rows = extract_rule(r["document_number"], r["publication_date"], text)
        if not rows:
            no_block.append(r["document_number"]); continue
        for row in rows:
            fill_regtext(row, text)
            infer_denied_proposed(row)
            row["outcome"] = classify(row)
            row.setdefault("concession_ratio", "")
            all_rows.append(row)
        if i % 100 == 0: print(f"  ...{i}/{len(pop)}", file=sys.stderr)

    fields = ["document_number", "publication_date", "decade", "heading", "verb", "direction", "paragraph_ref",
              "proposed_value", "proposed_unit", "requested_value", "requested_unit",
              "faa_preamble_value", "faa_preamble_unit", "faa_regtext_value", "faa_regtext_unit",
              "outcome", "concession_ratio", "denied_language", "moot", "reasons", "faa_cites_oem_parts",
              "request_sentence", "response_excerpt"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for row in all_rows: w.writerow(row)

    mismatches = [row for row in all_rows if row["faa_regtext_value"] is not None and row["faa_preamble_value"] is not None
                 and (row["faa_regtext_value"], row["faa_regtext_unit"]) != (row["faa_preamble_value"], row["faa_preamble_unit"])]
    with open(review_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for row in mismatches: w.writerow(row)

    n_rules_with_rows = len({row["document_number"] for row in all_rows})
    print(f"\nrules with >=1 detected request block: {n_rules_with_rows} / {len(pop)}  ({100*n_rules_with_rows/len(pop):.1f}%)")
    print(f"rules with NO heading match at all: {len(no_block)}")
    print(f"fetch failures: {len(fetch_fail)}  {fetch_fail}")
    print(f"total request rows: {len(all_rows)}")
    print("outcome distribution (rows):", dict(Counter(row['outcome'] for row in all_rows)))
    print(f"preamble/regtext mismatches: {len(mismatches)} of {sum(1 for row in all_rows if row['faa_regtext_value'] is not None and row['faa_preamble_value'] is not None)} comparable rows")
    print(f"\nwrote {out_csv}, {review_csv}")
    return dict(n_rules=len(pop), n_with_blocks=n_rules_with_rows, no_block=no_block, fetch_fail=fetch_fail,
               all_rows=all_rows, mismatches=mismatches)

# --------------------------------------------------------------------------------------------------------- CLI

COMMENT_TIME_RE = re.compile(
    r"(?is)\b(commenters?|requested?s?|asks?|argues?)\b[^.]{0,150}\bcompliance\s+time\b|"
    r"\bcompliance\s+time\b[^.]{0,150}\b(commenters?|requested?s?|asks?|argues?)\b"
)

def missaudit(n_per_decade=60, seed=20260924):
    """Step 2c: among rules where find_request_blocks() found NOTHING, sample n per decade and grep for
    commenter/request language near 'compliance time' anywhere in the document. A hit there means the heading
    regex likely missed a real contested request phrased differently (a different heading template, or the
    request made without a dedicated heading at all); report the ESTIMATED miss rate by decade -- this is a
    sample-based estimate with a real confidence interval, not a count, and it is exactly what decides whether a
    decade trend is safe to claim."""
    import random
    pop = load_population()
    by_doc = {r["document_number"]: r for r in pop}
    no_block_docs = []
    for r in pop:
        t = fetch_text(r["document_number"], r["publication_date"])
        if t is None: continue
        if not find_request_blocks(t): no_block_docs.append(r["document_number"])
    by_dec = defaultdict(list)
    for dn in no_block_docs: by_dec[decade_of(by_doc[dn]["publication_date"])].append(dn)
    rng = random.Random(seed)
    results = {}
    for dec, docs in sorted(by_dec.items()):
        sample = docs[:]; rng.shuffle(sample); sample = sample[:n_per_decade]
        hits = []
        for dn in sample:
            t = fetch_text(dn, by_doc[dn]["publication_date"])
            if t and COMMENT_TIME_RE.search(t): hits.append(dn)
        p = len(hits) / len(sample) if sample else 0.0
        # Wilson 95% CI on the sampled miss-rate proportion
        lo, hi = wilson_ci(len(hits), len(sample))
        results[dec] = {"no_block_total": len(docs), "sampled": len(sample), "grep_hits": len(hits),
                        "estimated_miss_rate": round(p, 3), "ci95": (round(lo, 3), round(hi, 3)), "examples": hits[:5]}
        print(f"{dec}: {len(docs)} rules with no heading match; sampled {len(sample)}; "
              f"{len(hits)} show request+'compliance time' language anyway -> estimated miss rate {p:.1%} (95% CI {lo:.1%}-{hi:.1%})")
    decs = sorted(results)
    rates = [results[d]["estimated_miss_rate"] for d in decs]
    trending = rates == sorted(rates) or rates == sorted(rates, reverse=True)
    print(f"\nmonotonic across decades: {trending} ({decs} -> {rates})")
    if trending and len(set(rates)) > 1:
        print("A trend claim over decades in Step 3 is UNSAFE without correcting for this: the heading-miss rate itself moves with decade.")
    json.dump(results, open(ROOT / "missaudit.json", "w"), indent=1)
    return results

def wilson_ci(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return max(0.0, center - half), min(1.0, center + half)

VAL40_ROW_RE = re.compile(r"^\|\s*([\w-]+)\s*\|\s*(GRANTED|DENIED|UNCLEAR)\b", re.IGNORECASE | re.MULTILINE)

def parse_val40(path):
    """Doc # -> hand-verified outcome, coarse 3-way (GRANTED includes partial and full, per val40_results.md's
    own notes; UNCLEAR also covers the one MOOT case)."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    out = {}
    for m in VAL40_ROW_RE.finditer(text):
        out[m.group(1)] = m.group(2).upper()
    return out

def document_verdict(rows_for_doc):
    """Collapse this document's (possibly several) extracted request rows to ONE coarse verdict, comparable to the
    hand labels' GRANTED/DENIED/UNCLEAR: GRANTED if any row resolved to FULL or PARTIAL, DENIED if at least one row
    resolved to DENY and none resolved FULL/PARTIAL, else NO_OUTCOME (no block found, or every row UNPARSEABLE --
    reported as its own category, not silently folded into a wrong answer, since 'the extractor found nothing' and
    'the extractor found something and got it wrong' are different failures)."""
    if not rows_for_doc: return "NO_OUTCOME"
    outcomes = [r["outcome"] for r in rows_for_doc]
    if any(o in ("FULL", "PARTIAL") for o in outcomes): return "GRANTED"
    if any(o == "DENY" for o in outcomes): return "DENIED"
    return "NO_OUTCOME"

def validate40(val_path, zip_path=None):
    """Step 2a: check extractor output against the 40 hand-verified outcomes. `zip_path` is accepted for interface
    compatibility (per the module docstring's originally sketched CLI) but is not needed: this project's govinfo
    cache already holds these 40 documents' text, fetched during Round 1/2's full runs, and fetch_text() re-fetches
    live from govinfo if a document is ever missing from the cache."""
    hand = parse_val40(val_path)
    print(f"hand-verified outcomes loaded: {len(hand)}")
    pop = {r["document_number"]: r for r in load_population()}
    missing_from_pop = [d for d in hand if d not in pop]
    if missing_from_pop:
        print(f"WARNING: {len(missing_from_pop)} val40 documents are not in the 568-rule contested population: {missing_from_pop}")

    all_rows = []
    for dn in hand:
        pd = pop.get(dn, {}).get("publication_date")
        if pd is None: continue
        t = fetch_text(dn, pd)
        if t is None: continue
        rows = extract_rule(dn, pd, t)
        for row in rows:
            fill_regtext(row, t); infer_denied_proposed(row); row["outcome"] = classify(row)
        all_rows.append((dn, rows))

    agree = disagree = no_outcome = 0
    disagreements, no_outcome_docs = [], []
    for dn, rows in all_rows:
        pred = document_verdict(rows)
        truth = hand[dn]
        if pred == "NO_OUTCOME":
            no_outcome += 1; no_outcome_docs.append((dn, truth))
            continue
        # UNCLEAR/MOOT hand labels are not scored against a forced GRANTED/DENIED prediction (neither counts as
        # a clean agreement or a clean disagreement); reported on their own line, not folded into either count.
        if truth == "UNCLEAR":
            print(f"  UNCLEAR (hand) vs {pred} (extractor): {dn} -- not counted as agree or disagree")
            continue
        if pred == truth:
            agree += 1
        else:
            disagree += 1
            disagreements.append((dn, truth, pred, rows))
    scored = agree + disagree   # excludes UNCLEAR and NO_OUTCOME from the denominator, reported separately
    print(f"\nAGREEMENT: {agree} of {scored} scored documents (GRANTED/DENIED only, n=40 total, "
          f"{sum(1 for v in hand.values() if v == 'UNCLEAR')} UNCLEAR excluded from scoring, {no_outcome} NO_OUTCOME)")
    print(f"gate check (>= 34 of 40 required, per the original combined gate): "
          f"{'PASS' if agree >= 34 else 'FAIL'} ({agree} of 40)")
    if disagreements:
        print("\nDISAGREEMENTS (hand vs extractor):")
        for dn, truth, pred, rows in disagreements:
            detail = "; ".join(f"{r['outcome']}(P={r['proposed_value']}{r['proposed_unit']},R={r['requested_value']}{r['requested_unit']},F={r['faa_preamble_value']}{r['faa_preamble_unit']})" for r in rows) or "no rows"
            print(f"  {dn}: hand={truth}  extractor={pred}  [{detail}]")
    if no_outcome_docs:
        print("\nNO_OUTCOME (extractor found no block, or every row UNPARSEABLE) -- hand label for reference:")
        for dn, truth in no_outcome_docs: print(f"  {dn}: hand={truth}")
    result = {"n_hand": len(hand), "agree": agree, "disagree": disagree, "no_outcome": no_outcome,
              "scored": scored, "gate_34_of_40": agree >= 34,
              "disagreements": [{"doc": dn, "hand": truth, "extractor": pred} for dn, truth, pred, _ in disagreements],
              "no_outcome_docs": [{"doc": dn, "hand": truth} for dn, truth in no_outcome_docs]}
    json.dump(result, open(ROOT / "validate40.json", "w"), indent=1)
    print("\nwrote validate40.json")
    return result

def redundancy_report(results_csv):
    rows = list(csv.DictReader(open(results_csv, encoding="utf-8")))
    have_both = [r for r in rows if r["faa_preamble_value"] and r["faa_regtext_value"]]
    agree = [r for r in have_both if (r["faa_preamble_value"], r["faa_preamble_unit"]) == (r["faa_regtext_value"], r["faa_regtext_unit"])]
    print(f"F_preamble vs F_regtext: {len(agree)} agree of {len(have_both)} comparable ({100*len(agree)/max(1,len(have_both)):.1f}%)")
    print(f"({len(have_both) - len(agree)} mismatches were written to review_list.csv by 'run', not used in outcome classification)")
    return {"comparable": len(have_both), "agree": len(agree)}

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run"); s.add_argument("--out", default=str(ROOT / "results.csv")); s.add_argument("--review", default=str(ROOT / "review_list.csv"))
    s = sub.add_parser("missaudit"); s.add_argument("--n", type=int, default=60)
    s = sub.add_parser("redundancy"); s.add_argument("--results", default=str(ROOT / "results.csv"))
    s = sub.add_parser("validate40"); s.add_argument("--val", required=True); s.add_argument("--zip", default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.out, a.review)
    elif a.cmd == "missaudit":
        missaudit(a.n)
    elif a.cmd == "redundancy":
        redundancy_report(a.results)
    elif a.cmd == "validate40":
        validate40(a.val, a.zip)

if __name__ == "__main__":
    main()
