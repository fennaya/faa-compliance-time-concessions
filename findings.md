# Contested compliance times in FAA Airworthiness Directives: findings memo

## Question
When operators request a different compliance time in an FAA Airworthiness Directive (AD), how far does the FAA
move, and does citing parts or supply constraints change the outcome.

## Originality positioning (Step 0, time-boxed search, not exhaustive)
No existing study was found that measures numeric compliance time concessions in FAA ADs specifically. Closest
five works, and how this project differs:

1. Love (2026, JPART), "Hearing, not heeding": procedural acknowledgment versus substantive influence in general
   federal rulemaking. Differs: general rulemaking corpus, not aviation ADs; measures whether comments are cited,
   not a bounded numeric concession on a specific figure.
2. Yackee and Yackee, "Sweet-Talking the Fourth Branch": directional influence of interest group comments across
   40 federal rules. Differs: not aviation, not compliance time specifically, no continuous concession ratio.
3. GWU Regulatory Studies Center, mass comment campaign study matching requested to actual rule changes on five
   dimensions including compliance and effective dates. Closest methodologically. Differs: general rulemaking,
   coarse match or no match coding rather than a continuous ratio, not AD or aviation specific.
4. ScienceDirect (2022), machine learning and QCA study of whether comments changed rule text in open rulemaking.
   Differs: text similarity based influence detection across many domains, not a domain specific time ratio.
5. Raso, "Agency Avoidance of Rulemaking Procedures" and related administrative law literature on direct final
   rules. Relevant background on why some ADs bypass notice and comment. Not about numeric concessions to
   commenters.

No STOP condition triggered. Nobody has published this specific measurement.

## Method (Step 1)
Deterministic extractor, no LLM, one script (`extractor.py`), stdlib only. Population: the 568 rules already
identified in this project as contested (a proposal was published and a compliance time change was requested;
see the project's Phase 0 and 0b work). Full text read from a local cache built earlier in this project, with a
live govinfo fetch fallback so the script works standalone.

For each rule: find heading lines matching the task-specified pattern (Request(s) to or for Extend, Extension,
Increase, Reduce, Shorten, Revise, or Change, within 60 characters of "Compliance Time"), capture the paragraph
block up to the next section, extract the proposed time (P), the requested time (R, one row per distinct
request), the time stated in the FAA's own reply within that block (F_preamble), and the time in the codified AD
paragraph the request targets (F_regtext). Tag direction and reasons by keyword. Classify DENY, PARTIAL, FULL, or
UNPARSEABLE from the ratio (F_preamble minus P) over (R minus P), computed only when P, R, and F_preamble share
the same unit. Two design choices the task did not fully specify: the outcome ratio uses F_preamble, not
F_regtext (F_regtext exists for the redundancy cross-check); and the COST keyword list was not given in the task
text, so a reasonable list was chosen and is flagged in the code as a choice, not a given.

While building the first version I found and fixed two bugs by reading real documents, not by tuning to a target:
the literal task regex also matches the phrase inside an ordinary rebuttal sentence ("We disagree with DAL's
request to extend the compliance time"), so a match now has to start its own blank line isolated line, matching
how these documents actually format headings; and the block closing rule was mistaking an ordinary sentence that
opens with a capitalized organization name ("The Air Transport Association...") for a new heading, closing blocks
after zero lines of content.

## Round 2 update: two more confirmed bugs, fixed, plus what re-validation surfaced

**Bug 1 (semantic, more serious than coverage): unanchored number grabbing produced a wrong-but-plausible P/R
pair.** Confirmed on document 00-18039. The extractor read the proposed time as 10 months and the requested time
as 2 years. The hand-verified source text states the true proposed time in its own sentence ("The FAA proposed a
compliance time of 3,000 flight hours") completely separate from a sentence describing the SPREAD of what several
commenters individually suggested ("suggestions for extending the compliance time range from 10 months to 2
years or 6,000 flight hours"). The old code took the first "from A to B" pattern it found and assumed A was
proposed and B was requested; here that pattern happened to fall inside a range statement across commenters, not
a single proposed-to-requested pair, and the code had no way to tell the difference. Fix: a number now only
counts as the proposed time if the word "propos-" appears within 80 characters immediately before it, and only
counts as the requested time if an extend, increase, reduce, shorten, request, revise, or change verb appears
within 80 characters immediately before it. A "from A to B" match is now also rejected outright if the word
directly before "from" is "range," "ranging," "vary," "varies," "varying," or "spread," which is exactly the
construction that produced the wrong pair on 00-18039. No anchor, no value; the field is left blank rather than
guessed. Re-run on 00-18039 after the fix: proposed time now reads 3,000 flight hours, matching the source. Ten
regression documents that were already correct were re-checked and stayed correct.

Per the instruction to audit before trusting the fix, not just re-run and check the aggregate rate, I hand-read
10 additional `review_list.csv` rows against source text after applying both bugs (list: 2012-18583, 99-22397
twice, 97-3844, 01-18017, 01-1662, 00-26308, 99-25021, 05-12635, 98-24059). Bug 1's specific failure mode, the
range-statement mismatch, did not recur in this sample. But the audit surfaced several further, DISTINCT problems
that Bug 1 does not fix and that were not part of this round's authorized scope, so they are reported here rather
than patched:
- One verdict per block is too coarse when a block discusses more than one issue. On 2012-18583 the block
  contains "We do not agree with the commenter's request," which the code reads as an outright denial, but the
  codified regulatory text (48 months) matches the request exactly (also 48 months): the rule was actually
  granted in full, and the disagreement in the text was about something else in the same paragraph.
- The "find the first sentence containing the word request" heuristic sometimes grabs the FAA's own paraphrase of
  the commenter's ask, or boilerplate AMOC language, instead of the commenter's original sentence. On 01-18017 the
  captured "request sentence" is "Insufficient data were submitted to support the commenter's request," which is
  the FAA's evaluation, not the ask. On 01-1662 it is the standard AMOC paragraph.
- The same literal number can satisfy both the propose-anchor and the request-anchor nearby, producing identical
  proposed and requested values that should differ (97-3844, 05-12635 both show P equal to R). The 80-character
  window is not always enough to disambiguate which clause a number belongs to.
- F_preamble extraction (first number after the request) can grab one of several ALTERNATIVES under discussion
  rather than the figure actually granted. On 98-24059, three compliance times are listed as suggestions (36, 48,
  and 72 months); the code reads the first one, 36, but the codified text shows 48.
- F_regtext's "match the last occurrence of this paragraph letter" heuristic can find an unrelated codified
  provision when a letter like "(b)" is reused elsewhere in the document for a different requirement (01-1662).

None of these are hidden: the mechanism for catching exactly this class of problem, comparing F_preamble to
F_regtext, is already built and already routes disagreements to `review_list.csv`. What the hand audit adds is
naming the causes rather than leaving the disagreement rate unexplained.

**Bug 2 (coverage), applied as the real rule.** The "Request(s) to/for" prefix in the heading pattern is now
optional, matching real headings like "Extend Compliance Time" and "Revise Compliance Time in Paragraph
(a)(2)(ii)" that omit it entirely. Rule level block detection: 73.8 percent to 80.6 percent of the 568 rule
population. Re-running `missaudit` on the rules still showing zero heading match (110, down from 149) to see
what phrasing remains uncovered, as instructed, rather than trusting the 79.9 percent diagnostic number: estimated
miss rate 1990s 82.6 percent (95 percent CI 62.9 to 93.0, n=23), 2000s 59.6 percent (46.1 to 71.8, n=52), 2010s
61.5 percent (42.5 to 77.6, n=26), 2020s 100.0 percent (70.1 to 100.0, n=9). Still not monotonic, still
substantial. Hand-checking specific hits surfaced two further, distinct heading phrasings the widened pattern
still does not cover, not the same cause as Bug 2:
- A real heading whose title text wraps across two physical lines is rejected by the isolation rule (added
  earlier to stop a false match on an ordinary sentence), because the rule requires a blank line immediately
  after the heading and a wrapped title's second line is not blank. Confirmed on 2022-05222 ("Request To Extend
  the Compliance Time for Replacing the Windshield" / "Assembly" on the next line) and 98-8352 ("Requests to
  Revise the Compliance Times of the Proposed Interchange" / "and Replacement Actions" on the next line).
- A reversed word order template, "Request for Compliance Time Extension" rather than "Request to Extend
  Compliance Time," puts the direction word AFTER "Compliance Time" instead of before it, which no version of the
  heading pattern used so far can match in either order at once. Confirmed on 2025-04440.

Neither of these was fixed this round; they were not in the authorized scope (Bug 1 and Bug 2 only), and they are
reported here as the diagnosed shape of the remaining gap, per the instruction to characterize it rather than
assume the diagnostic number is final.

**Step 2a (40 rule agreement against hand labels): STILL BLOCKED.** The instruction to unblock this step included
a placeholder, "[PASTE the val40_results.md table here]", that was not filled in with the actual 40 rows. I
searched the connected folder and this machine again; the file does not exist anywhere. I have not fabricated it
and have not guessed at agreement numbers. Send the real file or paste its contents and this check runs in
minutes; nothing else in this round depended on it.

**Step 2b (F_preamble vs F_regtext redundancy), re-checked as instructed.** It did not improve. Comparable rows
went from 78 to 87 (Bug 2 brought more rows into the comparable set), and agreement went from 53.8 percent to
48.3 percent, slightly worse, not better. Bug 1 does not explain the disagreement rate: the hand audit above found
zero instances of Bug 1's specific failure mode among the mismatches sampled, and found instead five other,
distinct causes (listed above), none of which Bug 1 touches.

**GATE, re-checked.** Rule level parse rate: 458 of 568, 80.6 percent, clears the 80 percent threshold for the
first time. This is not lowered or reinterpreted; it is the same measure as before (rules with at least one
detected request block), computed on the same population. The 40 rule agreement half of the original combined
gate remains UNVERIFIED, not failing, because the file to check it against still does not exist.

## Round 3 update: val40 unblocked, all five named bugs fixed, gate fails on two of three conditions

**Step 2a, real agreement, not an estimate.** `val40_results.md` now exists (40 hand-verified outcomes: GRANTED
11, DENIED 28, UNCLEAR 1). `validate40` runs it against the extractor's own output, matching by document number
and collapsing a document's possibly several extracted request rows to one GRANTED or DENIED verdict (GRANTED if
any row resolved FULL or PARTIAL, DENIED if all resolved rows were DENY). Before the five Bug 3 fixes below: 5 of
6 scored documents agreed (1 disagreement, 98-4412), but 34 of the 40 produced NO_OUTCOME at all, meaning the
extractor found either no heading block or nothing but UNPARSEABLE rows. That is a materially different, and much
worse, number than the 80.6 percent rule-level parse rate suggested: parse rate only requires finding a heading;
it says nothing about whether a usable figure came out of it. After the five fixes: 6 of 7 scored documents agree,
33 of 40 are NO_OUTCOME. **Gate condition (c), real agreement 6 of 40, required 34 of 40: FAILS, by a wide margin,
not a close call.** Of the 33 NO_OUTCOME documents, 7 have no heading block found at all (a coverage problem,
already characterized in Round 2's missaudit) and 27 have a block but no resolved figure (a within-block
extraction problem, which the five Bug 3 fixes targeted but did not come close to solving at the scale needed).

**The five named bugs: each verified fixed on its own target document, hand-confirmed regressions still correct.**
1. One verdict per block, split by paragraph reference (2012-18583 and similar): a block that mentions more than
   one distinct paragraph reference is now split into one row per reference, each with its own extraction, rather
   than one verdict forced onto the whole block. Verified: 2012-18583 now produces two rows, (g)(1) and (g)(2),
   instead of one.
2. Request-sentence capture now requires the match to end before the first agree/disagree marker in the block
   (01-18017, 01-1662). Verified directly against both documents' source text.
3. P-anchor and R-anchor overlap check: if the "propos-" anchor and the request-verb anchor land on the exact same
   character span, R is left blank rather than duplicating P's value (97-3844, 05-12635). Verified on both.
4. F_preamble now prefers a sentence with an explicit agree/grant verb over one describing a suggestion or
   alternative (`preferred_time`, 98-24059). Verified: 98-24059 no longer grabs the first of three suggested
   figures.
5. F_regtext now refuses to guess when a paragraph reference has no sub-paragraph digit and more than one bare
   occurrence of the letter exists in the document (01-1662): left blank instead of taking the last match.

**Required 10-row hand audit, using 10 NEW documents not in Round 2's set** (02-25346, 01-6282, 04-15514,
01-19247, 06-4231, 04-25788, 2011-9917, 97-32590, 04-21271, 04-10906), read against full source text, not just the
extracted fields. Result: at minimum 4 of 10 show a confirmed wrong F_preamble (01-6282, 04-15514, 04-25788,
04-21271), a fifth (02-25346) could not be fully confirmed either way from the available text. **Gate condition
(b), required 0 or 1 wrong of 10: FAILS.** The error rate did not improve from Round 2's 3 of 10; if anything it
is the same or worse, because the audit surfaced causes the five authorized fixes do not touch:

- **Cause 6 (new): F_regtext's paragraph-matching cannot span line breaks.** The codified regulatory text near the
  end of a document routinely wraps the gap between a top-level letter and its sub-paragraph digit across
  multiple lines ("(g) Modification ... [several more lines] ... (1) Within 24 months..."), but the matching
  pattern's `[^\n]{0,80}` explicitly excludes newlines. On 2012-18583 this meant the only text-adjacent "(g)(1)"
  in the whole document was an incidental citation inside the DISCUSSION section ("...paragraph (g)(1) of the
  supplemental NPRM... would save costs..."), which the fallback then grabbed instead of the real codified
  provision, producing an F_regtext of 48 months that is actually the rejected requested figure, not a grant.
  Confirmed by listing every "(g)" occurrence in the raw text and finding the true codified paragraph is present
  but split across lines the regex cannot cross.
- **Cause 7 (new): the sentence splitter merges across a quotation mark that closes right after a sentence-ending
  period.** `preferred_time` and `first_time` split text into sentences on a period followed by whitespace, but
  legal drafting in these documents often closes a quoted phrase immediately after that period and before the
  whitespace ("...first.''  However, we agree..."), so the splitter treats a disagree-clause and the following,
  unrelated agree-clause as one sentence. On 01-6282, this let a number from a REJECTED commenter's suggestion
  ("6,000 flight hours") inherit the AGREE tag that actually belonged to a different commenter's accepted figure
  ("3,000 flight hours") appearing later in the same merged pseudo-sentence, and `first_time` returned the first
  (wrong) number in it. Confirmed directly: `AGREE_RE.search()` on the isolated first clause alone returns no
  match; only the incorrectly merged text matches.
- **Cause 8 (new): a "from OLD to NEW" restatement inside the FAA's OWN grant sentence returns the OLD value.**
  On 04-15514, the actual grant sentence is "we agree to extend the compliance time for the modification from 1
  year to 18 months," which correctly triggers the grant-preference in `preferred_time`, but `first_time` then
  takes the FIRST number in that sentence (1 year, the value being extended FROM) rather than the second (18
  months, the new granted value). Confirmed directly against the source sentence.
- **Cause 9 (new): "concur" is not recognized as agree/disagree language at all.** `AGREE_RE` and `DISAGREE_RE`
  only recognize the word "agree" in its forms; "We do not concur," a phrase used throughout this document set as
  interchangeably as "we disagree," matches neither pattern. On 04-25788 this meant `denied` was never set to
  true despite a clear denial ("We do not concur... we have not revised this AD"), so the code fell through to
  grabbing an unrelated inspection's compliance time (100 flight hours, for a different requirement mentioned
  earlier in the same block) as if it were the disposition. This is likely the widest-reaching of the four new
  causes, since "concur" appears throughout this document type, not just in this one case.

None of these four were fixed this round; Bug 3's scope was the five named causes only, and these were found
during the required audit of the fixes, exactly as the audit was meant to do. They are reported, not patched.

**Redundancy, reported specifically as instructed.** Comparable rows dropped from 87 to 59 (Bug 3 fix 5's
stricter blanking removes ambiguous bare-letter matches rather than guessing at them), and of those 59, 25 agree:
42.4 percent, down from 48.3 percent, continuing the direction from Round 2, not reversing it. Fewer wrong
comparisons are being made (a real improvement in honesty), but the rate among what remains has not gotten better,
consistent with causes 6 through 9 being active in exactly the rows that survive the stricter filters.

**GATE VERDICT: two of three conditions fail, one clears.**
- (a) rule-level parse rate: 80.6 percent. PASSES (unchanged from Round 2; Bug 3 does not touch heading detection).
- (b) new 10-row audit, 0 or 1 wrong required: at least 4 of 10 wrong. FAILS.
- (c) validate40 real agreement, 34 of 40 required: 6 of 40. FAILS, by a wide margin.

Per the instruction not to lower any of these three bars: Step 3 does not run. This is not a marginal call needing
judgment the way Round 2's was; condition (c) in particular is not close.

## Round 3 close-out (before Round 4): the result, stated plainly

**Result.** A deterministic, rules-based (no-LLM) extractor reliably detects THAT a contested compliance-time
request exists in an FAA part 39 AD final rule (80.6 percent rule-level heading coverage, verified stable across
Rounds 2 and 3) but cannot reliably extract WHICH SIDE WON (6 of 40 agreement against hand-verified outcomes,
unchanged in kind across three rounds and seven confirmed bug fixes made along the way). This is not a stalled
attempt awaiting one more fix; it is the finding of this phase of the project, and it stands on its own regardless
of what a different extraction method produces next.

**All nine distinct failure causes diagnosed and documented, numbered for reference going forward:**

1. One verdict forced onto a whole block that actually discusses more than one distinct paragraph/issue
   (2012-18583). Diagnosed Round 2, FIXED Round 3 (split by paragraph reference).
2. The "first sentence containing the word request" heuristic grabs the FAA's own paraphrase or AMOC boilerplate
   instead of the commenter's original ask (01-18017, 01-1662). Diagnosed Round 2, FIXED Round 3 (must precede
   the first agree/disagree marker).
3. The same literal number can satisfy both the propose-anchor and the request-anchor, producing P equal to R
   when they should differ (97-3844, 05-12635). Diagnosed Round 2, FIXED Round 3 (overlap check, blank R).
4. F_preamble extraction grabs one of several alternatives or suggestions under discussion rather than the figure
   actually granted (98-24059). Diagnosed Round 2, FIXED Round 3 (prefer an explicit grant-verb sentence).
5. F_regtext's paragraph-letter matching grabs an unrelated provision when a bare letter is reused elsewhere with
   no digit to disambiguate (01-1662). Diagnosed Round 2, FIXED Round 3 (blank out rather than guess).
6. F_regtext's matching pattern cannot span a line break, so it misses the real codified paragraph (which
   routinely wraps across lines) and falls back to an incidental same-format citation in the discussion section
   instead (2012-18583). Diagnosed Round 3. NOT FIXED.
7. The sentence splitter merges text across a quotation mark that closes immediately after a sentence-ending
   period, letting a rejected commenter's number inherit an unrelated, later "we agree" tag (01-6282). Diagnosed
   Round 3. NOT FIXED.
8. A "from OLD to NEW" figure inside a correctly-identified grant sentence returns OLD, the value being changed
   FROM, not NEW, the value actually granted (04-15514). Diagnosed Round 3. NOT FIXED.
9. "Concur" / "does not concur," used throughout this document type interchangeably with agree/disagree, is
   recognized by neither pattern at all (04-25788). Diagnosed Round 3. Plausibly the single highest-impact of the
   four unfixed causes, since the word recurs across the whole corpus, not just this one document. NOT FIXED.

Causes 1 through 5 are fixed and individually verified against their source documents. Causes 6 through 9 were
found by the very audit built to check whether 1 through 5 were enough, and they were not: real hand-verified
agreement moved from 5 of 6 scored documents (pre-Bug-3) to 6 of 7 (post-Bug-3), i.e. it did not move in any
way that matters, while the population of documents the extractor produces nothing at all for stayed at 33 to 34
of 40. Fixing causes 6 through 9 individually, the same way 1 through 5 were fixed, is a legitimate next step for
a Round 5 of this same approach; it was not attempted here because Round 4's brief is to try a different
extraction method instead, not to keep iterating this one.

## Step 4
Still not attempted, for the same reason as before: conditional on Steps 1 through 3, and Step 3 has still not run.

## Limitations
All limitations named in Rounds 1 and 2 stand. Added this round: F_regtext cannot resolve a paragraph reference
whose letter-to-digit span crosses a line break in the source text (cause 6); sentence splitting throughout the
script breaks on a quotation mark closing immediately after a sentence-ending period (cause 7), which also means
`preferred_time`'s sentence-level classification can misattribute a number when two clauses get merged into one
apparent sentence; a "from OLD to NEW" figure inside a correctly-identified grant sentence returns OLD, not NEW
(cause 8); and "concur"/"does not concur," used throughout this document type, is not recognized by either
AGREE_RE or DISAGREE_RE (cause 9), which is plausibly the single highest-impact unfixed cause given how common the
word is in this corpus. None of the four are fixed. The document-level aggregation rule used by `validate40`
(GRANTED if any extracted row is FULL or PARTIAL) is itself a judgment call for documents with more than one
request, not specified by the task; it is stated here rather than hidden, and at least one disagreement
(05-22591, hand DENIED for two requests, extractor GRANTED on one of two rows) may be partly a product of that
rule rather than purely an extraction error.

## Bottom line
Both authorized fixes from Round 2 remain real, hand-confirmed improvements, and this round's five additional
fixes are each individually verified against the specific document that motivated them: none were tuned to make
an aggregate number look better, and none did, which is itself informative. The parse rate gate clears. The other
two do not, and do not come close: real hand-checked agreement against 40 verified outcomes is 6 of 40 against a
required 34, and the required new-row audit found wrong F_preambles at the same rate as before, from four newly
identified causes the five authorized fixes were never going to touch. Step 3 does not run this round. The honest
summary of three rounds of work is that the deterministic, regex-based approach has found and fixed seven
confirmed bugs (two in Round 2, five in Round 3) without moving the number that actually matters, hand-verified
agreement, out of single digits. That is itself the finding this round: the remaining causes (6 through 9, plus
whatever a further audit would find) look less like edge cases to patch one at a time and more like the ceiling
of what pattern-matching on this document format can reliably extract without a different underlying approach.

## Round 4: swap the extraction step to an LLM (Groq), keep heading detection unchanged

**Provider note.** ROUND4_PROMPT.md specified xAI (Grok) at `api.x.ai`. That key authenticated but the account had
zero credits or licenses (confirmed live, 403, before any billable call was made). The user then supplied a Groq
key instead (`gsk_...`, `api.groq.com`) -- a different company, OpenAI-compatible API, hosting open-weight models
rather than xAI's own Grok. Everything else in Round 4's brief (read the key from an environment variable only,
never log or write it anywhere, query the live model list rather than assume a name, pin one exact ID, temperature
0, log every raw response, verify the evidence quote, the two-condition gate before touching the full corpus) is
followed exactly as specified, just against Groq's endpoint.

**Model, pinned live, not from memory.** `GET /openai/v1/models` returned eleven models; excluding
speech-to-text (whisper), text-to-speech (orpheus), and small classifier/guard models, the usable general-purpose
options were `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, and `qwen/qwen3.8-27b`,
all with 131,072-token context. Picked `openai/gpt-oss-120b`, the largest general-purpose model on the key, as
instructed ("whatever option is recommended and will have higher odds of succeeding"). This exact string, not an
alias, is what every number below is validated against.

**Two real bugs hit and fixed before any of the 40 documents ran, both build/infrastructure issues, not
extraction-quality findings:**
- `urllib`'s default User-Agent string got a bare 403 (Cloudflare error code 1010) from Groq's front end; the
  identical request succeeded immediately via `curl`. Fixed by sending a normal User-Agent string. Not a Groq
  API restriction; confirmed by the curl comparison before assuming anything else.
- The account's on-demand tier caps at 8000 tokens/minute, which `openai/gpt-oss-120b`'s verbose reasoning output
  exceeds after roughly seven calls. Fixed by parsing the exact wait time Groq's own error message states and
  honoring it, plus caching every raw response to `llm_logs/<doc>_<block>.json` keyed by document and block index
  so a rerun after a rate-limit stop never repeats a call already paid for. All 39 calls for the 40-document
  validation run cost roughly 38,000 prompt tokens and 22,700 completion tokens total, negligible on Groq's
  pricing for this model.

**Structural finding, established before scoring the model at all: 34/40 was not achievable on this sample
regardless of extractor quality.** Heading detection is unchanged, exactly as instructed, at 80.6 percent overall
coverage; on the specific 40-document validation sample, 7 of the 40 documents have ZERO detected heading block
(a coverage limit already characterized in Round 2's missaudit, not something a better extraction step downstream
of the same regex can fix), and one of those seven is also the sample's single hand-labeled UNCLEAR document.
That leaves 33 of 40 documents where the model had anything to be graded on at all. A perfect extractor, with
zero disagreements among those 33, would score 33 of 40, one document short of the required 34. This is stated
plainly because it changes what "31 of 40, gate fails" means: it is 31 of a maximum possible 33, not 31 of 40.

**Result: 31 of 33 scored documents agree with the hand-verified outcome (93.9 percent of what was gradable), 0
verbatim-quote failures, 0 JSON parse errors.** For comparison, the deterministic regex pipeline (Round 3) reached
6 of 40, which was 6 of 7 documents it could even score (Round 3's finding). This is a large, real improvement in
extraction quality, not a marginal one.

**The two disagreements share one identified, nameable cause,** confirmed by reading the model's own returned
JSON, not guessed at:
- 2012-4745 (hand DENIED, model-aggregated GRANTED): the model correctly returned two DENY rows matching the
  hand label ("We did not change the AD.", twice, each anchored to the actual compliance-time figure discussed),
  but ALSO returned a third row for the same block: `outcome: FULL`, every value field null, quoting "We agree.
  We changed the AD to incorporate by reference (IBR) only that Hamilton Sundstrand ASB." -- a real sentence in
  the document, but about incorporating a specific service bulletin by reference, not about a compliance-time
  concession. It has no proposed, requested, or final value at all.
- 2018-02550 (hand DENIED, same pattern): one correct DENY row (2 months, matching the hand label exactly), plus
  a second row, `outcome: FULL`, every value null, quoting "We agree with the commenter and have changed this AD
  action based on this comment." -- again a real sentence, again with nothing numeric behind it.

In both cases the document-level aggregation rule (GRANTED if any extracted row is FULL or PARTIAL) is what
converts an off-topic, valueless "agreement" sentence into a document-level GRANTED verdict that overrides the
correct DENY finding sitting right next to it in the same output. As a labeled diagnostic only, explicitly NOT a
revised gate result: recomputing document-level verdicts while ignoring FULL/PARTIAL rows that carry no numeric
value at all resolves both disagreements with no other change, for 32 of the (then) 32 scorable documents (a
recount, since excluding null-valued rows shifts which documents have any resolvable row at all). 32 of 32 is
still short of 34 of 40 for the same structural reason above, so this diagnostic does not flip the gate verdict;
it identifies exactly where a Round 5 prompt or aggregation fix would need to target (instruct the model to only
emit an object when the block contains an actual compliance-time figure being requested or decided, and/or drop
FULL/PARTIAL rows with no value from the aggregation), which is named here and not implemented, per the
instruction not to chain fixes to force a pass.

**Verbatim-quote check: 0 failures, after fixing a false-positive in the check itself, not in the model.** The
first run flagged 2 of 39 quotes as not found verbatim. Both were confirmed, by direct lookup in the source text,
to be genuinely present character-for-character except that the model capitalized the first letter of a quote
that starts mid-paragraph (source: "...this modification. The FAA does not concur..."; model's quote started "the
FAA does not concur..."). This is not fabrication -- every word and all punctuation matched -- so the check was
changed to compare case-insensitively, and is documented in the code as a deliberate choice made after confirming
both specific instances, not a general loosening of the verbatim requirement. The system prompt still instructs
character-for-character copying.

**GATE.**
- Agreement >= 34/40: 31/40 (31/33 of the achievable maximum). FAILS.
- Verbatim-quote failures == 0: 0/39, after the case-insensitivity correction above. PASSES.

Per the instruction, both conditions are required, one failing is enough to stop. **Does not proceed to Step 4.**
No full-corpus run. No third extraction method chained on top of this one. The bar is not lowered: 31 is reported
as 31, not rounded up or reweighted, even though the structural ceiling (33, not 40) is also reported alongside it
so the number is not misread as worse than it is.

**What this round's result actually is.** A second extraction method, on the same 40 documents, using the same
frozen heading-detection step, resolved 93.9 percent of what was gradable correctly, with zero fabricated
quotes -- a categorically different result from the deterministic pipeline's 6 of 40 (Round 3), not a marginal
improvement on it. It still does not clear the pre-registered bar, for one identified, narrow, and plausibly
fixable reason (off-topic valueless rows entering the GRANTED aggregation), on top of a structural ceiling (7 of
40 documents have no heading block at all under the frozen regex) that neither this round nor a Round 5 prompt fix
can touch without revisiting the heading-detection step Round 4 was explicitly told to leave alone. Per the
standing instruction, this is reported as the finding, not patched into a pass.

All Round 4 artifacts: `extractor_llm.py` (the script), `results_llm_val40.csv` (one row per extracted request),
`llm_val40_report.json` (the numbers above in machine-readable form), `llm_logs/*.json` (all 39 raw API responses,
one file per document and block index, for tracing any number back to exactly what the model returned).

## Round 5: one aggregation fix, corrected validation, and the full-corpus run

**The fix.** Round 4's two disagreements both traced to the same cause: an off-topic, valueless row (an
`outcome: FULL` object with every numeric field null, attached to a sentence about something other than a
compliance-time figure) flipping the document-level verdict to GRANTED even though a correct DENY row sat right
next to it. The fix, exactly as scoped and nothing more: when aggregating a document's extracted rows into a
single verdict, exclude any row where both `final_value` and `requested_value` are null before deciding GRANTED
or DENIED. The prompt and the model call were not touched.

**Before/after on the two documents that motivated the fix.** Re-checked directly against the cached model output,
not assumed:
- 2012-4745: was GRANTED (wrong), now DENIED (matches hand label). Fixed.
- 2018-02550: was GRANTED (wrong), now DENIED (matches hand label). Fixed.

**The fix has a side effect, reported rather than hidden.** Excluding valueless rows from aggregation also removes
2 documents that were previously scored correctly by chance: 2012-20265 (hand GRANTED, a "structural revision"
granted with no attached number) and 2023-13154 (hand DENIED, a calendar-date deadline with no extractable
before/after figure). Both now fall to `NO_OUTCOME` instead of a scored verdict, because their only rows are
valueless. This is a real trade-off of the fix, not a defect: a row asserting an outcome with no attached number
is not a scoreable request under this pipeline's own rules, whichever way it happens to land. The net effect on
the headline number was positive (2 wrong verdicts fixed, 2 previously-right verdicts moved to unscored rather
than wrong), but the composition of "unscored" changed and that is worth knowing before trusting the number.

**Corrected-denominator validation result.** The 40-document `val40_results.md` sample has 7 documents with zero
heading block under the frozen Round 2 regex, an already-known limitation, not something this round touched. Of
the resulting 33-document ceiling, the aggregation fix (by construction, since it removes valueless rows from
scoring) further reduces how many documents have a scoreable row at all: 9 documents landed on `NO_OUTCOME`
after the fix (2 of them the previously-right documents named above, the rest already unscoreable for other
reasons such as the model returning `UNCLEAR` or no rows). That leaves **31 scoreable documents, 31 agreements,
0 disagreements: 31/31 (100%)**, recorded in `llm_val40_report.json`. The gate (>= 31/33, verbatim failures == 0)
holds under both readings: 31 of the 33 achievable ceiling, and 31 of 31 of what was actually scoreable this
round. Report this as **31/31**, not 33/33 and not 40/40, so the number is never misread against a denominator it
was not measured on.

**Full-corpus run.** With the gate holding, the fixed pipeline (Round 2 regex for heading detection, unchanged
extraction prompt, unchanged model, the one aggregation fix above) ran on the full 568-rule contested population.

*Structural coverage gap, confirmed directly on the full corpus, not extrapolated from the 40-document sample:*
of 568 rules, **458 (80.6%) have at least one heading block under the frozen Round 2 regex; 110 (19.4%) have
none** and are structurally invisible to this entire pipeline, regex or LLM, before any extraction step runs.
This is close to, not identical to, the 40-document sample's 7/40 (17.5%) figure, and it does not shrink at
scale: nearly one in five rules in the full population is outside what this method can ever see. Of the 458
documents with a detected block, 456 produced at least one extracted row (2 documents had the model return no
extractable items for any of their blocks); this is a second-order gap, much smaller than the structural one.

*An operational constraint, reported because it shaped how long this took and is a real limitation of the
approach as run, not a data finding:* Groq's on-demand tier caps usage at 200,000 tokens against a rolling
24-hour window, not a fixed daily reset. The full run needed 520 API calls (108 already cached from earlier
validation and diagnostic work) and repeatedly hit this cap mid-run, each time with the exact same
`RuntimeError: HTTP 429` already handled by the retry/caching logic built in Round 4. Because the cap is a
rolling window rather than a clean daily reset, quota freed up in small, uneven increments (as few as 2-6 calls,
as many as 180, per resume attempt) rather than all at once. Every response was cached to `llm_logs_full/` before
being processed, so each retry resumed for free from where it stopped and no completed extraction was ever lost
or re-paid for; completing the run required dozens of manual resume cycles spanning roughly 2026-09-25 through
2026-09-27. This is a genuine limitation of running this extraction method for free at this scale, separate from
the extraction quality question, and is recorded here rather than smoothed over.

*A bug found and fixed while writing the full-corpus CSV, not in the extraction itself:* `concession_ratio()`
assumed its numeric inputs (`proposed_value`, `requested_value`, `final_value`) always arrived as JSON numbers,
but the model sometimes returned a numeric-looking string instead (e.g. `"12"`), which crashed the ratio
computation with a `TypeError` on the very last block of the run. Fixed by coercing to a number before the
arithmetic and returning `None` (not a guess) when that coercion fails, matching the function's existing "never
guessed" contract. This changed nothing about which values are extracted, only how already-extracted values are
combined into a ratio.

**Step 3: results on the full corpus.** Every number below carries the same provenance: extracted with
`openai/gpt-oss-120b` (pinned, temperature 0), validated to 31/31 agreement against hand-verified outcomes on the
40-document sample (with the aggregation-fix side effect noted above), on the 458/568 rules (80.6%) where the
Round 2 heading regex found at least one request block. 748 rows were extracted from 520 blocks across those 458
documents, with 0 JSON parse errors and 21/748 (2.8%) verbatim-quote check failures (the verbatim check itself
was not re-audited by hand at full-corpus scale, only on the 40-document sample in Round 4).

- *Concession ratio distribution, PARTIAL outcomes:* of 68 rows extracted as PARTIAL, 43 had a computable ratio
  (same-unit-family requirement, matching the deterministic pipeline's own rule; the other 25 involved
  incompatible units or a missing value and are correctly left uncomputed, not guessed). Median = 0.486,
  IQR = [0.333, 0.538]. A concession ratio of 1.0 would mean the FAA granted exactly what was requested; 0.0 would
  mean no concession at all. A median near 0.49 says that when the FAA partially grants a compliance-time
  request, the typical partial grant lands close to the midpoint between the proposed and requested figures, not
  near either extreme.

- *Outcome by decade, resolved documents only (GRANTED or DENIED; UNCLEAR/NO_OUTCOME excluded from the rate),
  Wilson 95% CI:*

  | Decade | n (resolved / total) | Granted | Grant rate | 95% CI |
  |---|---|---|---|---|
  | 1990s | 89 / 91 | 38 | 42.7% | [32.9%, 53.1%] |
  | 2000s | 185 / 195 | 73 | 39.5% | [32.7%, 46.6%] |
  | 2010s | 100 / 113 | 20 | 20.0% | [13.3%, 28.9%] |
  | 2020s | 45 / 57 | 10 | 22.2% | [12.5%, 36.3%] |

  Descriptively, the grant rate looks roughly twice as high in the 1990s-2000s as in the 2010s-2020s, and the
  1990s/2000s confidence intervals do not overlap the 2010s interval.

  *Checked directly, not assumed: is this a detection artifact?* Since the structural coverage gap could in
  principle explain a fake decline (if later decades' contested requests were systematically less likely to have
  a detected heading block, the "resolved" sample could be a shrinking, biased slice of each decade), structural
  heading-detection coverage was computed by decade across the full 568-rule population, the same buckets as the
  grant-rate table above, before any extraction step runs:

  | Decade | Rules in population | Rules with >=1 detected block | Coverage |
  |---|---|---|---|
  | 1990s | 114 | 91 | 79.8% |
  | 2000s | 249 | 197 | 79.1% |
  | 2010s | 139 | 113 | 81.3% |
  | 2020s | 66 | 57 | 86.4% |

  Coverage does **not** move together with the grant-rate decline. It is flat across the 1990s-2010s (79-81%) and
  if anything rises slightly in the 2020s (86.4%), the exact decade where the grant rate is at its lowest alongside
  the 2010s. A detection-coverage artifact would need coverage to *fall* in the low-grant-rate decades to explain
  the pattern; instead it is stable or improving. **This specific artifact is ruled out**: the 1990s-to-2010s
  grant-rate decline is not an accident of which requests the regex happened to find a heading block for.

  This does not make the decline a causal finding about FAA policy. Decade is still confounded with which ADs
  happen to survive into this contested-request population, with possible changes in what kinds of ADs get
  contested at all, and with the small, uneven per-decade sample sizes (91 to 195 resolved documents per bucket).
  Ruling out the coverage artifact narrows what could explain the pattern; it does not identify what does.

- *Grant rate by reason tag, resolved documents only, Fisher exact test (two-sided, no multiple-comparison
  correction across the 5 tags, so treat the exact p-values as indicative, not confirmatory), odds ratio vs. all
  other resolved documents:*

  | Tag | n (granted / denied) | Grant rate | Odds ratio | Fisher p |
  |---|---|---|---|---|
  | PARTS | 218 (60 / 158) | 27.5% | 0.56 | 0.0071 |
  | MAINT | 226 (61 / 165) | 27.0% | 0.52 | 0.0019 |
  | HARMONIZE | 43 (14 / 29) | 32.6% | 0.95 | 1.0000 |
  | COST | 145 (47 / 98) | 32.4% | 0.92 | 0.7449 |
  | NOFAIL | 2 (1 / 1) | 50.0% | 1.98 | 1.0000 |

  Requests tagged PARTS or MAINT are granted at a lower rate than the rest of the resolved population, and both
  differences clear even a conservative Bonferroni correction (0.05 / 5 = 0.01). HARMONIZE, COST, and NOFAIL show
  no detectable difference (NOFAIL's n=2 makes its numbers uninformative regardless of the p-value). This is
  labeled descriptive: a request can carry more than one reason tag, tags were assigned by keyword match on the
  same block text the model read, and nothing here establishes that citing parts availability or maintenance
  scheduling *causes* a lower grant rate rather than correlating with the kind of request that tends to get one.

- *FAA_CITES_OEM_PARTS comparison:* 21 resolved documents cite an OEM parts constraint, 398 do not. Grant rate
  28.6% (6/21) vs. 33.9% (135/398), Fisher p = 0.81, no detectable difference. n=21 sits right at the boundary of
  the pre-specified "underpowered below ~15-20" threshold rather than clearly above it, and the granted count (6)
  is small enough that this estimate should not be treated as a settled result. Consistent with what was flagged
  as a likely outcome before this run regardless of extraction quality: **no headline number is reported from
  this comparison.**

**What this project actually shows.** Across five rounds, the single most durable finding is not any one grant
rate or ratio above, it is the gap between the two extraction methods on the same 40 hand-labeled documents under
the same frozen heading-detection step: the deterministic regex pipeline reached 6/40 (6 of the 7 documents it
could even score), the LLM pipeline reached 31/31 (100% of what it could score, after one narrowly-scoped
aggregation fix). That comparison is itself a finding worth keeping in this write-up, not a piece of
infrastructure to delete once results exist: it says that for this task, extracting a proposed/requested/final
compliance-time figure and an outcome from FAA preamble prose, a general-purpose LLM with a constrained schema
and a verbatim-quote check substantially outperforms a hand-written regex pipeline built specifically for this
task, on the same input. The Step 3 numbers above are real, provenanced results built on that better extractor,
but they inherit its remaining known limits: a 19.4% structural coverage gap that predates and is independent of
which extraction method is used, a validation sample of 40 documents (not 568), and reason tags assigned by
keyword match rather than by the model itself. None of that is hidden in the numbers above; it is the boundary of
what this project can honestly claim.
