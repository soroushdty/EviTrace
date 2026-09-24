# Feature Validation Report — `risk-remediation`

**Status (2026-09-24): GO.** The 10.1 NO-GO was resolved on 2026-09-23 — the spec owner chose
option (a) and criterion 10.1 was reworded (see "Resolution" at the end of §1); the feature was
re-validated on 2026-09-24 (see §7). The original verdict is kept
below as the record: *NO-GO on one acceptance criterion (10.1). Everything else passes.*

Date: 2026-09-22 · Range validated: `a57294d..49a3f66` (37 commits, 34/34 sub-tasks)
Method: full-suite execution, two independent validation agents (requirements coverage;
design/boundary), ~33 mutations, plus controller-run measurements. Nothing is pushed;
all work is local on `main`.

---

## 1. The 10.1 decision (resolved 2026-09-23 — option (a))

**Requirement 10.1** — *"When an evidence package is built using default configuration for a
paper whose substantive text is 30,000 characters, the evidence index shall select at least
60 percent of that substantive text."*

**It is not satisfied at the shipped defaults (150 items / 30 000 chars), and as worded it is
unsatisfiable while any finite item cap exists.**

Measured directly against `select_paper_evidence(bundle, fields, max_items=150, max_chars=30000)`
with synthetic bundles of exactly 30 000 substantive characters:

| paper shape | selected items | coverage | meets 0.6? | binding cap |
|---|---|---|---|---|
| 150 × 200 chars | 150 | 1.000 | yes | items |
| 200 × 150 chars | 150 | 0.750 | yes | items |
| **300 × 100 chars** | 150 | **0.500** | **no** | items |
| **600 × 50 chars** | 150 | **0.250** | **no** | items |
| **1000 × 30 chars** | 150 | **0.150** | **no** | items |
| 183 × 172 chars (bioRxiv-like) | 150 | 0.820 | yes | items |

**Why it is structurally unsatisfiable.** The item cap bounds `selected_chars` at
`max_items × mean_item_length`. For a fixed 30 000-char corpus, coverage is
`min(max_items, n) / n` whenever the item cap binds — so for any finite `max_items` there is a
paper (enough short sentences) that falls below 0.6. Only papers whose mean evidence item is
**≥ 120 characters** clear the floor at 150/30 000.

**Why the pinned test passes.** The design (approved at the design gate) pinned the 10.1 test to
the bioRxiv reference fixture, which averages ~172 chars per item → 0.946. Real-fixture coverage
at defaults: bioRxiv **0.946**, PLOS ONE **0.699**, arXiv **0.311** (arXiv is a 97 k-char paper,
outside 10.1's premise).

### The two options

**(a) Reword 10.1** to name the reference corpus or a sentence-length premise.
- Matches what was actually designed, implemented and tested.
- Zero code change; edit `requirements.md` 10.1, `design.md` EvidenceCoverage, and the config
  comment/README wording that already says "whichever cap binds first".
- Honest about the real contract: the floor holds for papers with typical scientific sentence
  lengths, not for every 30 000-char corpus.

**(b) Drop (or greatly raise) the item cap** and let the 30 000-char cap alone prune.
- Then coverage at exactly 30 000 substantive chars is 1.000 for any sentence distribution, and
  10.1 becomes universally true.
- Requirement 10.5 still holds — ranking and the character cap both remain, so this is not
  "satisfying 10.1 by disabling pruning".
- Costs more input tokens on short-sentence papers (more items at the same character budget);
  30 000 chars ≈ 7 500 tokens, still far below the 100 000-token `extraction_chunk` budget.
- Touches: `configs/config.yaml`, `config_utils.py` defaults, `configs/README.md`,
  `.kiro/steering/config.md`, and the tests that pin 150 (`test_openai_config_keys.py`,
  `test_evidence_coverage.py`, `test_token_efficiency_regression.py`).

Not recommended: leaving it as-is silently. The criterion's own objective is "fields are not
missed because relevant sentences were pruned away", which is exactly the short-sentence case.

### Resolution (2026-09-23)

The spec owner chose **option (a)**. `requirements.md` 10.1 now names the reference paper — the
bioRxiv fixture `tests/fixtures/grobid_tei/biorxiv_2020.03.24.004655.tei.xml` — as its premise,
and states that for other papers the 60 % floor is monitored (10.4 records the shortfall), not
guaranteed. The original wording is preserved in the reworded criterion's note. No code or
config value changed; the item cap stays at 150.

The **sentence-length** variant of (a) was rejected after measurement: selection ranks by keyword
relevance, not length, so a mean-length premise does not bound coverage. A synthetic
30 000-char paper of 100 × 30-char high-scoring items plus 150 × 180-char low-scoring items
(mean exactly 120 chars) selected 12 000 chars → **0.40**. That case is now pinned by
`test_mean_item_length_does_not_guarantee_the_floor` in
`tests/src/pipeline/test_evidence_coverage.py`, so the reason for the wording cannot go stale.

Re-measured at `2066ea6` (2026-09-23), defaults 150 / 30 000: bioRxiv 0.946, PLOS ONE 0.699,
arXiv 0.311 — unchanged. Also updated: `design.md` (traceability row and EvidenceCoverage
note), the coverage-rationale wording in `configs/config.yaml`, `configs/README.md` and
`.kiro/steering/config.md` (now say the floor is guaranteed only on the reference paper; a
doc-site test enforces it), `spec.json`, the `tasks.md` banner, and `CHANGELOG.md`.

The §3 drifts, §4 pre-existing issues and §5 open questions are unaffected by this decision and
remain as recorded.

---

## 2. Validation results

| Check | Result |
|---|---|
| Full suite incl. slow | **1920 passed, 3 skipped**, exit 0 (pre-spec baseline 1470 / 2) |
| Slow-only | 22 passed |
| Dependency directions | 9 passed |
| Migration pair | 64 passed |
| TBD/TODO grep (feature files) | clean (0) |
| Secrets grep | clean (0) |
| Smoke boot | `main.py --help` and `pdf_extractor --help` both exit 0 |
| Blocked tasks | none (34/34 complete) |
| Requirements coverage | **55/55 criteria** have implementing code *and* a killing test — 0 gaps, 0 untested |
| Boundary audit | **no violations** |
| Design drift | none; 17/17 components present at designed names, all reachable |

**Boundary evidence.** Byte-identical across the range: `configs/structure_schema.json`,
`final_output_schema.json`, `agent_schema.json`, `extraction_map.json`, the whole `src/agents/`
tree (so `_shared_paper_prefix` is untouched), `scan_detector.py`, and the evidence-cache format.
`models.py`'s entire diff is four lines adding `ABC` to three class headers — no dataclass field
changed. The 15 changed files under `src`/`configs` match the File Structure Plan exactly, with
none unplanned and none planned-but-untouched. The only new cross-package import is the designed
`pipeline → pdf_extractor.parse_tei_coords`.

**Cross-task seams verified shape-by-shape**: block provenance → paragraph → sentence → alignment
entry → annotation region/marking; adjudication `primary_extractor` → branch roles → structural
layer; `select_paper_evidence` stats → manifest record; `extract_with_repair` params → synthesis
call → failure record; page sidecar → unified routing builder.

---

## 3. Six non-blocking drifts

| # | Issue |
|---|---|
| **10.2** | Worded about the **token** budget; the new test exercises the **character** cap. Both enforcement points work, but no test spans them (i.e. shows that when the token budget binds, coverage drops and the budget holds). |
| **2.4** | "Rationale auditable from the output alone" is untrue for the orchestrated pipeline: `content["provenance"]` reaches disk only through `artifact_generation/extraction_artifact.py`, whose sole caller is the **standalone** `pdf_extractor` CLI. `main.py`'s `outputs/<paper>.extracted.json` is LLM fields only; `qc_report.csv` has no provenance column. The rationale *is* guarded in memory (a mutation killed 5 tests). |
| **4.3** | (a) The 12.3 preservation test is deliberately tautological on offsets — forcing `start=end=0` leaves it green; it pins selector types/order/quote/target only. Real offsets are guarded by the 5.3 unit file and the 12.1 end-to-end test. (b) A native offset *miss* now yields quote-only instead of `TextPositionSelector{0,0}`; permitted by "may be improved" but not pinned anywhere as deliberate. |
| **5.4** | Limit parity is mutation-proven (one shared `RepairRetryLoop`; giving synthesis its own limit killed 5 tests). Failure-**shape** parity is inferred — both stages call the same `_make_failure_record` and assert the same constant, never compared side by side. Separately, the accepted deviation (10.1 impl note) leaves synthesis asymmetric at the **initial** budget check: `protected_evidence_ids` is forwarded for synthesis and `None` for chunks, so synthesis can hard-raise `TokenBudgetExceededError` where a chunk would mitigate. Untested; documented only in `tasks.md`. |
| **1.4** | Both concrete skip paths are tested, but the universal claim ("every skipped write attributable to a logged failure") rests on `EXPECTED_STATUSES`, a substring scan of module *source* that a comment can satisfy. A newly added silent `return None` before the write would be caught by nothing. |
| **3.4** | No time/path/randomness in the id recipe, but `document_source` is filesystem-derived and the two entry points disagree: the orchestrator passes `pdf_path.stem`, the standalone extractor passes `.name` → **different annotation ids for the same paper depending on entry point**. |

Sub-criterion notes: **3.3** has both hops guarded (reconciler occurrence counter; id recipe) but
no end-to-end link — the 12.1 fixture has no duplicated sentence text, so its id-uniqueness
assertion is inert for 3.3; a zeroed counter would ship as colliding ids. **4.9**'s implementation
predicate is "page has no primary paragraph text", a proxy for the criterion's "page classified
scanned" — equivalent in practice, divergence untested.

---

## 4. Pre-existing issues found (not regressions)

- `build_chunk_evidence_package` is imported at `pdf_processor.py:14` but **never called**;
  disabling both its caps breaks no test. R10's per-chunk caps have no live consumer.
- `parse_grobid_tei` / `_parse_tei_to_blocks` / `GROBID._parse_coords` are dormant on the
  production path (`extract_with_grobid(parse_blocks=False)`). Production coordinates come from
  the two mirrors in `evidence_index.py` and `quality_control.py`;
  `test_coords_cross_agreement.py` is the single point keeping the three copies in sync.
- `rater.observe` is dead code (explicit design non-goal).
- `tests/src/pipeline/test_pdf_processor_helpers.py` and `test_repair_retry.py` cannot be
  collected in isolation (`agents.openai` MagicMock ordering); they pass at directory/suite level.
- `configs/config.yaml` selects the `nltk_punkt` sentence backend but `nltk` is commented out in
  `requirements.txt` and absent from the venv.
- `tests/src/pdf_extractor/test_w3c_annotation.py` tests `src/artifact_generation/` — mirror-path
  convention oddity that predates this spec.

---

## 5. Two questions deliberately left open

1. **Adjudication `confidence` semantics.** Now that extractor names are distinct, the built-in
   formula yields `confidence ≤ 1/N` and ties break by index order (index 0 wins when all pass;
   an all-fail run still elects index 0 at 0.0). No requirement governs `confidence` and no `src/`
   consumer branches on it. `test_builtin_impls_identity.py:71` pins `1/3` and must be relaxed if
   the formula changes.
2. **`content["segments"]` / `["pages"]`** remain primary-only while `exact_text` includes the OCR
   blocks used for sentences. Verified inert: `content["segments"]` has **zero** consumers in `src/`.

---

## 6. Where to resume

- Spec documents: `.kiro/specs/risk-remediation/{requirements,design,tasks,research}.md`;
  per-task detail and every accepted deviation is in `tasks.md` → `## Implementation Notes`.
- `CHANGELOG.md` leads with the operator-visible behaviour changes (annotation ids change, cache
  hits now OCR scanned pages, per-page QC metrics shift, stale evidence caches, config defaults,
  manifest keys, GROBID coordinates).
- Commits: `git log --oneline a57294d..HEAD` — one task per commit, each independently reviewed
  (one rejection at task 6.1, remediated and re-approved).
- To re-run validation after a decision: `python -m pytest -q -m ""`, then
  `/kiro-validate-impl risk-remediation`.

---

## 7. Re-validation after the 10.1 decision (2026-09-24) — **GO**

| Check | Result |
|---|---|
| Full suite incl. slow (`python -m pytest -q -m ""`) | **1921 passed, 3 skipped**, exit 0 (+1 = the new 10.1 rationale test) |
| Dependency directions + migration pair | 73 passed |
| TBD/TODO grep (lines added since `a57294d`) | clean (0) |
| Secrets grep (lines added since `a57294d`) | 1 false positive (`first_token = case.coords.split(...)`, a coords parser test variable) |
| Smoke boot | `main.py --help` and `pdf_extractor --help` both exit 0 |
| Blocked tasks | none (34/34 sub-tasks `[x]`) |
| Production code since the §2 validation (`49a3f66`) | **unchanged** — `git diff 49a3f66 -- src main.py configs` touches only `configs/README.md` and comment lines of `configs/config.yaml` |
| Designed-immutable files vs `a57294d` | still byte-identical (schemas, `extraction_map.json`, `src/agents/`, `scan_detector.py`) |

Because no production code changed, the §2 judgment results (requirements coverage 55/55, no
boundary violations, no design drift, cross-task seams verified) carry forward unchanged. The one
coverage change is 10.1: its reworded premise is exactly what
`test_biorxiv_fixture_at_defaults_covers_at_least_60_percent` asserts, and the "monitored, not
guaranteed" clause is backed by the 10.4 shortfall record and by
`test_mean_item_length_does_not_guarantee_the_floor`. The §3 drifts, §4 pre-existing issues
and §5 open questions remain non-blocking and unchanged.
