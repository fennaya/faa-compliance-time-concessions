# FAA Compliance-Time Concessions
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23000344.svg)](https://doi.org/10.5281/zenodo.23000344)
## The question

When an aircraft operator formally objects to the deadline an FAA Airworthiness Directive sets for fixing a safety problem, and asks for more time, how often does the FAA actually grant it? And has that changed over the three decades these directives span? This is a plain empirical question nobody appears to have measured: it requires reading the "Discussion of Comments" section of hundreds of Federal Register final rules, finding the paragraph where a commenter contested the compliance time, and recording what was proposed, what was requested, and what the FAA actually decided.

## The real contribution: how to know whether to trust an automated extractor

The interesting part of this project is not the final number, it is the path to trusting it.

A deterministic regex extractor was built first: heading detection, anchored number extraction, unit-family matching, all hand tuned against Federal Register prose. It was validated against a 40-document hand-labeled ground truth set across three rounds of debugging, with nine distinct failure causes identified and documented as they were found. After all of that it reached 6 correct verdicts out of 40 (6 of the 7 documents it could even score; the rest either had no detected heading or hit one of the nine documented failure modes). That is reported as a ceiling, not a bug list waiting for a tenth fix.

The regex pipeline was then replaced with an LLM extractor, keeping the same frozen heading-detection step since that part was never the problem, and that replacement was validated the same way, against the same 40 hand-labeled documents, before a single document outside that set was touched. It reached 31 agreements out of 31 scoreable documents: 100% of what it could be checked against.

The transferable point is not "use an LLM." It is: build a way to check an automated extractor against ground truth before trusting anything it produces at scale, and report the honest failure rate of the first approach instead of quietly discarding it. The full failure history, all nine causes and why five were fixed and four were not, is in `findings.md`.

## Headline results

Every number below carries its own provenance in `findings.md`: extracted with `openai/gpt-oss-120b` (pinned, temperature 0), validated to 31/31 agreement against the hand-labeled set, on the 458 of 568 rules (80.6%) where the heading-detection step found at least one contested compliance-time request.

- **Grant rate declines by decade:** 42.7% (1990s) to 39.5% (2000s) to 20.0% (2010s) to 22.2% (2020s), among resolved documents (Wilson 95% CI shown in `findings.md` and `figures/decade_grant_rate.png`).
- **That decline is not a detection artifact.** Structural heading-detection coverage was checked in the same decade buckets and does not fall alongside the grant-rate decline; it is flat to slightly rising (79.8%, 79.1%, 81.3%, 86.4%), the opposite of what would be needed to explain the pattern as a side effect of which requests the extractor could see. This rules out one specific artifact. It does not make the decline a causal finding about FAA policy.
- **Requests citing parts availability (PARTS) or maintenance-interval alignment (MAINT) are granted less often** than the rest of the resolved population, and both differences survive a Bonferroni correction across the five reason tags tested (Fisher exact p = 0.0071 and p = 0.0019).
- **The OEM-parts question is reported as underpowered, with no headline number.** Only 21 resolved documents cite an OEM parts constraint, right at the boundary of what was pre-specified as a usable sample, so no rate is asserted from it.

## Limitations, stated plainly

- **80.6% structural coverage.** 110 of 568 rules (19.4%) have no detected heading block under the frozen regex and are invisible to this entire pipeline before extraction ever runs. This does not shrink at scale and does not concentrate in any one decade.
- **Single model, single run.** All extraction used one pinned Groq model at temperature 0, run once. No cross-model or repeated-run agreement check was performed beyond the hand-labeled validation.
- **Descriptive, not causal.** The decade trend and the reason-tag comparisons are associations observed in this corpus, not evidence of what caused the FAA to decide as it did. Reason tags are assigned by keyword match on the same text the model read, not judged by the model itself.
- **The validation denominator shrank twice, and both times are reported rather than smoothed over.** The 40-document hand-labeled sample has 7 documents with no detected heading block, so the real ceiling is 33, not 40. A later aggregation fix, excluding extracted rows with no attached number from a document's verdict, also removed 2 previously correctly scored documents as a side effect of fixing 2 previously wrong ones, landing the final validated agreement at 31 of 31 scoreable documents. See `findings.md` (Round 5) for the full before and after evidence on both changes.

## Reproducibility

```
uv run --no-project python extractor_llm.py val40   # re-run validation against val40_results.md
uv run --no-project python extractor_llm.py full     # re-run the full 568-rule extraction
uv run --no-project --with matplotlib python figures/make_chart.py
```

- Requires `GROQ_API_KEY` set in the environment. It is never read from a file and never written to one.
- The model is pinned in code, `openai/gpt-oss-120b` via `api.groq.com`, temperature 0, for reproducibility.
- Every API response is cached to `llm_logs/` (validation) or `llm_logs_full/` (full run) as `<document>_<block>.json` before being processed. A re-run checks that cache first, so an interrupted run resumes for free and never re-pays for a completed extraction. These directories are gitignored, since they are regenerable and not needed to reproduce the published CSVs, but they populate locally on your own re-run.
- Groq's on-demand tier caps usage at 200,000 tokens on a rolling 24-hour window, not a fixed daily reset. The original full run needed dozens of manual resume cycles over roughly two days for this reason. This is a documented operational constraint of running the free tier at this scale, not a defect in the extraction.
- `extractor.py` and `extractor_regex_v3.py` are the deterministic pipeline that reached the 6/40 ceiling, kept for the record and because the LLM pipeline imports its heading-detection step unchanged.
- `step3_analysis.py` reproduces every number in the Headline Results section above from `results_llm_full.csv` alone, with no API calls.

## Files

| File | What it is |
|---|---|
| `findings.md` | The full write-up, all five rounds, including every failure and negative result |
| `val40_results.md` | The 40-document hand-labeled validation set |
| `extractor.py`, `extractor_regex_v3.py` | The deterministic regex pipeline (reached 6/40) |
| `extractor_llm.py` | The LLM pipeline (reached 31/31 on the same validation set) |
| `step3_analysis.py` | Reproduces the headline statistics from `results_llm_full.csv` |
| `results_llm_full.csv` | One row per extracted request, full 568-rule population |
| `results_llm_val40.csv`, `llm_val40_report.json` | Validation run output and report |
| `figures/decade_grant_rate.png` | The one chart |

## License

Code: MIT (see `LICENSE`). Analysis, findings, and data, including the hand-labeled validation set: CC BY 4.0 (see `LICENSE-analysis`). Citation: `CITATION.cff`.
