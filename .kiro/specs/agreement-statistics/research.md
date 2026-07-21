# Research & Design Decisions — agreement-statistics

## Summary

- **Feature**: `agreement-statistics`
- **Discovery Scope**: Extension (integration-focused discovery into an existing, partially-stubbed subsystem)
- **Key Findings**:
  - The inter-rater agreement stage is not merely wrong, it is unwired. `iaa_calculator.investigate()` has **no caller in `src/`** at all, and `ctx.iaa_metrics` is **never serialized** by `artifact_generation/extraction_artifact.py`. Whatever the IAA stage computes today reaches nothing.
  - `InterRaterMetrics.compute(reports: list[QualityMetrics]) -> None` cannot carry per-field data, so the brief's "one `InterRaterMetrics` subclass per statistic" sketch does not fit the shipped ABC. The design keeps the ABC untouched and introduces a narrower `AgreementStatistic` contract underneath a single orchestrating implementation.
  - `metrics_hierarchy` is the only QC surface that reaches disk, and two existing tests assert it holds **exactly three keys**. Publishing agreement results there is the lowest-friction path but is the one existing test contract this feature must break.
  - The `agreement_metrics` config key resolves to `[]` in every real run — `configs/config.yaml` has no `iaa_calculator` block at all, only `_QC_DEFAULTS` supplies one. The "empty list means percent agreement only" behaviour is therefore the default path, not an edge case.
  - `select_primary_branch` / `score_branch` / `BranchQualityScore` — the home of the 0.15-weighted `agreement_score` — have **no production caller**; they are exercised only by `tests/src/quality_control/test_reconciliation_properties.py`. The rename is correspondingly low-risk.

## Research Log

### Existing IAA machinery and its three mislabels

- **Context**: The brief names three mislabel sites and one stub. Each needed verification against current code before design.
- **Sources Consulted**: `src/quality_control/{models,iaa_calculator,adjudicator,quality_control}.py`, `src/quality_control/builtin_impls/inter_rater_report.py`, `src/quality_control/checks/extractor_agreement.py`, `src/artifact_generation/extraction_artifact.py`, `src/utils/config_utils.py`, `configs/config.yaml`, `tests/src/quality_control/`, `tests/test_migration_artifact_scrub_preservation.py`.
- **Findings**:
  - `InterRaterReport.compute` emits `1.0 if a.status == b.status else 0.0` per unordered branch pair into `pairwise: dict[str, float]`. Two branches that both *fail* score 1.0. There is no content comparison and no chance correction. The class has **zero test coverage** (`grep -rn InterRaterReport tests/` is empty), and the key format `f"{name_a}_vs_{name_b}"` collides when two branches share a `source`.
  - `adjudicator._compute_agreement_score` is an asymmetric bag-of-words precision: `len(branch_words & other_words) / len(branch_words)` pooled across all other branches, with no ordering, no page awareness, and no recall term. It returns `1.0` both when there is no other branch and when other branches contain no words — a lone branch is credited with full agreement.
  - `ExtractorAgreementCheck.agreement_rate = (exact + near) / primary_sentence_count` is recall against the primary only; unmatched candidate sentences do not penalize. Its "sentences" are whole block texts, and only *candidate* sentences are length-filtered — an asymmetry worth preserving deliberately or fixing deliberately, not by accident. Its disabled path emits a `status` key that its enabled path does not.
  - `iaa_calculator.investigate()` reads `config["quality_control"]["iaa_calculator"]["agreement_metrics"]` with unguarded chained indexing and returns `{metric: None}`. Its docstring still points at a `["investigator"]` config path that the code does not read.
- **Implications**: All three renames are safe. The `pairwise` field can keep working through an alias property. `investigate()`'s signature and return keys are pinned by a preservation test and must survive; only its `agreement_metrics` values change.

### Where agreement results can actually be published

- **Context**: Requirement 12.1 demands the results appear in run output. It was not obvious that any path existed.
- **Findings**: `unified_to_artifact` emits `pdf_name, pdf_id, pdf_uri, document_id, content, semantic, structural, alignment, branches, metrics_hierarchy` — `ctx.iaa_metrics` and `ctx.decision` are absent. `save_artifact` writes with `json.dump(..., default=str)`, so dataclasses placed in `metrics_hierarchy` would be stringified into unusable text. `src/pipeline/extraction_report.py` writes `outputs/flagged_fields.csv` from LLM field confidence and is unrelated to QC branch agreement.
- **Implications**: Publish through `metrics_hierarchy` as **plain dicts** via an explicit `as_dict()`. This keeps every change inside `quality_control` — no `artifact_generation` or `pipeline` edit is needed, which matters because the dependency-direction rule forbids `quality_control` importing them (the reverse direction would have been legal but is unnecessary).

### Test contracts that constrain the design

- **Findings**:
  - `tests/src/quality_control/test_qc_pipeline_integration.py` asserts `set(ctx.metrics_hierarchy.keys()) == {"extraction_coverage", "source_text_verification", "semantic_verification"}` in **two** places — a unit test (~L125) and a Hypothesis property test (~L495).
  - `tests/test_migration_artifact_scrub_preservation.py` calls `investigate()` with five positional dicts and asserts `decision == "deferred_to_adjudicator"` and the presence of the `agreement_metrics` key. It passes `agreement_metrics: ["metric_a"]` — an unknown name — so unknown-name handling must not raise.
  - No `conftest.py` exists under `tests/`; `sys.path` comes from the repo-root `conftest.py`. QC tests never construct `QCBundle` manually — they build `list[Candidate]` and call the pipeline.
- **Implications**: The unknown-metric-name path (Requirement 3.4) is exercised by an existing test on day one. The three-key assertion is the single deliberate contract break, scheduled into the same task that adds the keys.

### Dependency-direction constraints on parser agreement

- **Context**: Parser agreement needs section headings and table detection per page, but heading detection lives in `src/pdf_extractor/layout_utils.py`, which `quality_control` may not import.
- **Findings**: `quality_control.py::_extract_branch_payload(payload) -> (full_text, page_texts, blocks)` already normalizes every branch payload shape (block lists, dicts, TEI XML with `coords="page;x0,y0,x1,y1"`) into a per-page view, and `_build_native_page_texts` already builds a cross-branch page-text map. Block dicts carry only `text`, `page_index`, `block_bbox`, and `spans` — **no heading or table signal**. Critically, `_extract_tei_payload` *does* iterate `<head>` and `<figure>` elements separately from body paragraphs, so GROBID's own structural distinction is available at parse time and is then **discarded** when every element is flattened into an identical untyped block dict.
- **Implications**: A first draft of this design had the caller "supply headings and tables", which on inspection nothing could do — section-heading and table-detection agreement would have been permanently uncomputable, and because the skip signal requires both, `skip_parser_counterfactual` could never have been granted. The resolution is two-part: (1) stamp an optional `block_type` on blocks at TEI parse time, which is purely additive since block dicts are schema-open and no existing reader inspects the key; (2) add a `PageViewBuilder` that reads that tag and, when a branch carries no tag at all (pdfplumber, PyMuPDF, PaddleOCR today), marks the signal *unavailable* rather than emitting an empty heading list. The analyzer then reports the metric as undefined, which never marks a page risky and never grants skip eligibility — the conservative direction. Requirements 9.8, 9.9, and 10.8 were added to make this observable rather than an implementation detail.

### Statistic definitions

- **Context**: Four formulas must be implemented from scratch; getting `p_e` or the `n-1` correction wrong produces plausible but wrong numbers.
- **Sources Consulted**: The canonical primary sources for each statistic — Cohen (1960) for kappa; Cohen (1968) for weighted kappa; Gwet (2008) for AC1; Krippendorff, *Computing Krippendorff's Alpha-Reliability* (2011) for the coincidence-matrix formulation.
- **Findings** (fixed in `design.md`, restated here with the pitfalls):
  - **Cohen's kappa**: `k = (p_o - p_e)/(1 - p_e)`, `p_e = sum_k p_1k * p_2k`. Pitfall: kappa's *prevalence* and *bias* paradoxes — with a highly skewed marginal distribution, kappa collapses toward 0 even at 95%+ observed agreement. This is precisely why Requirement 5 exists.
  - **Weighted kappa**: `k_w = 1 - (sum w_ij O_ij)/(sum w_ij E_ij)`, linear `w_ij = |i-j|/(K-1)`, quadratic `w_ij = (i-j)^2/(K-1)^2`. Pitfall: the weight matrix must be a *disagreement* matrix in this formulation; using an agreement matrix silently inverts the result. Quadratic weighting is the more common default in the literature, but **linear** was chosen here — see Design Decisions.
  - **Gwet's AC1**: same shell as kappa but `p_e = (1/(K-1)) * sum_k pi_k (1 - pi_k)` with `pi_k = (p_1k + p_2k)/2`. For binary data this reduces to `2 pi (1 - pi)`. Pitfall: using raw category proportions rather than the rater-averaged `pi_k`.
  - **Krippendorff's alpha**: coincidence matrix where each unit with `m_u >= 2` ratings contributes `1/(m_u - 1)` per ordered pair of distinct ratings; `D_o = sum o_ck delta_ck^2`; `D_e = (1/(n-1)) sum n_c n_k delta_ck^2`; `alpha = 1 - D_o/D_e`. Pitfalls: omitting the `1/(m_u - 1)` weight (which is what makes missing data tolerable), omitting the `n-1` correction in `D_e` (which biases alpha for small samples), and the ordinal difference function, which is `(sum of n_g for g from c to k, minus (n_c + n_k)/2)^2` and depends on the *observed* category frequencies rather than on rank distance alone.
  - Closed-form variance and confidence-interval formulas are published for all four, but this feature reports point estimates only — see Non-Goals.
- **Implications**: Each implementation is validated against a published worked example. The fixture format mandates a citation string alongside the input table, expected value, and tolerance, so no fixture can be added without a traceable source.

### Verified worked examples

A dedicated research pass located and **independently recomputed in exact rational arithmetic** every value below, confirming each against its published figure. These are the fixtures the design's Testing Strategy names.

| Fixture | Table | Verified values |
|---------|-------|-----------------|
| `vcd::SexualFun` 4x4, n=91 (Hout, Duncan & Sobel 1987 via Agresti) | `[[7,7,2,3],[2,8,3,7],[1,5,4,9],[2,8,9,14]]` | kappa 0.129330 (ASE 0.068599, z 1.885); linear weighted 0.237381 (ASE 0.078316, z 3.031). Both match the published vcd output. |
| `vcd::MSPatients` two 4x4 | Winnipeg `[[38,5,0,1],[33,11,3,0],[10,14,5,6],[3,7,3,10]]`; New Orleans `[[5,3,0,0],[3,11,4,0],[2,13,3,4],[1,2,4,14]]` | kappa 0.207942 / 0.296517; linear weighted 0.379731 / 0.477273. **Point values only** — the published ASEs on that page do not reproduce (generated in 2012 under an old vcd); the same ASE code reproduces SexualFun and irrCAC to 5 significant figures. |
| `irrCAC::cont3x3abstractors` 3x3, n=100 | `[[13,0,0],[0,20,7],[0,4,56]]` | quadratic weighted kappa 0.892157, SE 0.035352 (published 0.8922 / 0.0354) |
| Gwet 2008 paradox 2x2, n=125 | `[[118,5],[2,0]]` | percent agreement 0.944, Cohen kappa −0.023392, Gwet AC1 0.940776, Krippendorff alpha −0.024691 |
| Gwet 2014 3x3, n=100 (cross-validated, four statistics from one input) | `[[75,1,4],[5,4,1],[0,0,10]]` | percent agreement 0.890000, Cohen kappa 0.676471, Gwet AC 0.867570, Krippendorff alpha 0.676900 |
| Warrens paradox pair, 3x3, n=30 | W1 `[[1,15,1],[3,0,3],[2,3,2]]`; W2 `[[1,1,1],[3,17,3],[2,0,2]]` | quadratic weighted kappa exactly 0.000000 for both, despite p_o(quad) 0.700000 vs 0.841667; Gwet AC2 0.152276 / 0.693878. W2's third row was reconstructed as the only completion consistent with the printed marginals and statistics. |
| Krippendorff canonical, 4 observers x 12 units, 7 missing | See the reliability matrix in the source; 41 present values, **40 pairable**, coincidence matrix marginals 9/13/10/5/3 | nominal 0.743421, ordinal 0.815388, interval 0.849107, ratio 0.797403 (published 0.743 / 0.815 / 0.849 / 0.797) |
| Krippendorff binary, 2 observers x 10 | coincidence `[[10,4],[4,2]]` | 0.095238 (published 0.095) |
| SAS 242-2009 prevalence paradox 2x2 | `[[95,4],[1,0]]` | kappa −0.016260, PABAK 0.90, prevalence index 0.95, bias index 0.03, positive agreement 0.974359 |

Verified degenerate behaviour: `[[10,0],[0,0]]` gives kappa `0/0` undefined while AC1 is legitimately 1.0 and alpha is undefined (`D_e = 0`); `[[6,0],[4,0]]` forces kappa to exactly 0.0 regardless of observed agreement. Gwet's AC1 has no division-by-zero for two or more categories — only a single category or an empty table needs guarding. `sklearn` warns and returns NaN on undefined kappa; R's `irr::kappa2` and `vcd::Kappa` return NaN.

**Sources with known errors — do not use as oracles**: Gwet, *Intrarater Reliability* (Wiley Encyclopedia of Clinical Trials, 2008), Table 5, prints `pe = 0.75x0.84 + 0.21x0.16` where `0.21` should be `0.25` (correct kappa 0.666667, not 0.673). Shankar & Bangdiwala (2014, PMC4236536) prints an AC1 chance-agreement formula with wrong exponents that does not reproduce its own table. The `vcd::Kappa` documentation misprints the Fleiss/Cohen/Everitt page range.

**Not obtained**: the in-paper worked examples of Cohen (1960) and Cohen (1968) themselves — every route was paywalled. No number may be cited as coming from those papers directly; the fixtures above come from `vcd`, `irrCAC`, Gwet's handbook, and Krippendorff's own paper.

Closed-form variance is published for kappa, weighted kappa (Fleiss, Cohen & Everitt 1969), and Gwet's AC1 (Gwet 2008). **Krippendorff's alpha has no commonly published closed-form variance** — Hayes & Krippendorff (2007) prescribe bootstrapping. This asymmetry is one more reason this feature reports point estimates only.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| One `InterRaterMetrics` subclass per statistic (brief's sketch) | Each statistic is a pipeline-level strategy | Uses only the shipped extension point; no new ABC | `compute(reports: list[QualityMetrics])` cannot carry per-field data; would force an ABC signature change, breaking the shipped contract; five stage implementations cannot be combined in one run | **Rejected** |
| Narrow `AgreementStatistic` ABC under one orchestrating `InterRaterMetrics` impl | Statistics are pure functions over rating vectors; one implementation wires them | Shipped ABC untouched; statistics testable in isolation against published examples; multiple statistics per run | Adds one ABC | **Selected** |
| Free functions, no ABC | Simplest possible | No uniform way to declare assumptions or applicability, which Requirement 3 demands per statistic | Would push assumption metadata into a parallel lookup table | Rejected |
| Extend `ExtractorAgreementCheck` for parser agreement | Reuse the existing comparison check | Its unit is the whole block text, it is sentence-recall shaped, and it is not page-aware — three mismatches with Requirement 9 | Would entangle a rename target with new work | Rejected |

## Design Decisions

### Decision: Keep `InterRaterMetrics` unchanged; add a narrower statistic contract beneath it

- **Context**: The brief proposes one `InterRaterMetrics` subclass per statistic. The ABC's `compute(reports: list[QualityMetrics]) -> None` receives branch quality reports, not field extractions.
- **Alternatives Considered**: (1) change the ABC signature; (2) pass comparison units through a module-level or bundle-level side channel; (3) constructor injection into a single orchestrating implementation.
- **Selected Approach**: (3). `AgreementReport(InterRaterMetrics)` takes `units` and `config` in its constructor and orchestrates `AgreementStatistic` implementations; `compute(reports)` still fulfils the shipped contract by recording branch status concordance.
- **Rationale**: The shipped ABC is a public extension point documented in steering; changing its signature would be a silent breaking change for any user subclass. Constructor injection is already the pattern used for `ExtractorAgreementCheck`'s matchers.
- **Trade-offs**: One extra ABC, and a deliberate deviation from the brief's sketch that must be flagged to reviewers.
- **Follow-up**: Confirm at implementation that no existing subclass of `InterRaterMetrics` outside `builtin_impls/` exists.

### Decision: Publish through `metrics_hierarchy`, accepting the three-key contract break

- **Context**: Requirement 12.1/12.2 demand the results reach run output; `ctx.iaa_metrics` reaches nothing today.
- **Alternatives Considered**: (1) extend `unified_to_artifact` to serialize `iaa_metrics` and `decision`; (2) nest results under the existing `semantic_verification` key; (3) add two new top-level `metrics_hierarchy` keys.
- **Selected Approach**: (3), with the two existing exact-key assertions updated in the same task.
- **Rationale**: (1) touches `artifact_generation` and would also drag `ctx.decision` into the artifact — a larger blast radius for no benefit. (2) is dishonest naming, which is exactly the defect this spec exists to fix. (3) is the smallest honest change.
- **Trade-offs**: Breaks one existing test contract, in two places. Mitigated by scheduling it as an explicit task and keeping the key names in the Revalidation Triggers list.

### Decision: Nest configuration under `quality_control`, adding no new top-level YAML key

- **Context**: The brief and the roadmap both flag `_ALL_KNOWN_TOP_LEVEL_KEYS` as a registration requirement for new keys.
- **Findings**: `config_utils.py:511` validates **top-level** keys only; nested keys under `quality_control` are unvalidated.
- **Selected Approach**: `quality_control.iaa_calculator.*` (extending the existing block) and `quality_control.parser_agreement.*` (new nested block). `_ALL_KNOWN_TOP_LEVEL_KEYS` is untouched; `_QC_DEFAULTS` gains both.
- **Rationale**: Keeps agreement configuration where every other QC stage's configuration lives, and avoids a global-config edit for a subsystem-local concern.
- **Trade-offs**: Nested keys get no unknown-key validation. Accepted — this matches every other QC block.

### Decision: Linear weighting as the weighted-kappa default

- **Context**: Requirement 4.3 requires a documented default.
- **Alternatives Considered**: quadratic (the more common literature default) or linear.
- **Selected Approach**: **Linear**, configurable to quadratic.
- **Rationale**: The ordered dimension here is extraction confidence, whose levels (`h`/`m`/`l`/`nr` in the existing LLM output schema) are coarse ordinal labels, not an interval scale. Quadratic weighting assumes the distance between adjacent levels is meaningfully squared, which over-credits near-misses on a four-level label. Linear is the conservative choice, and a conservative reliability estimate is the right default for a tool whose purpose is auditability.
- **Trade-offs**: Reported values will be lower than a quadratic-weighted equivalent; anyone comparing against a published quadratic figure must set the option.

### Decision: Resolution of roadmap Open Question 4 — parser-counterfactual audit threshold

- **Context**: Open Question 4 ("What threshold should trigger parser-counterfactual audit?") is assigned to this spec and must be resolved rather than defaulted silently.
- **Alternatives Considered**:
  1. A single composite page-agreement score with one cut point.
  2. Per-metric thresholds with a single risky/not-risky split.
  3. Per-metric thresholds with two independent bands: a risky floor and a separate high-agreement skip ceiling.
- **Selected Approach**: (3). Risky when token overlap < 0.80, numeric-token overlap < 0.95, or any table-detection or text-presence disagreement. Skip-eligible when token overlap >= 0.95 with full numeric, table, heading, and text-presence agreement. Counterfactual audit recommended when a page is risky, and additionally on any numeric-token or table-detection disagreement regardless of the other metrics.
- **Rationale**: A composite score (1) averages away exactly the signal that matters — a page can have 0.97 token overlap and one wrong number, and the composite hides it. Numeric and table disagreements are given independent trigger authority because their downstream cost is unrecoverable: a corrupted effect size cannot be caught by prose review, whereas a few percent of token drift routinely reflects benign hyphenation and ligature differences. Two independent bands (3) rather than one split (2) leave a deliberate middle band meaning "compare normally", so that raising the skip bar does not mechanically widen the risky set.
- **Trade-offs**: Five thresholds instead of one is more configuration surface. The defaults are uncalibrated first estimates — they are stated, recorded on every emitted record, and configurable precisely so `evaluation-harness` can calibrate them against measured extraction accuracy.
- **Follow-up**: Re-open this decision once `evaluation-harness` can measure parser-risk impact on final extraction accuracy (multiagent Success Metric 12). Changing the defaults is listed as a Revalidation Trigger.

### Decision: Version the extractor-agreement report rather than dual-populate

- **Context**: Requirement 11.5 permits either keeping the shipped field or versioning the report.
- **Selected Approach**: `InterRaterReport` keeps `pairwise` as an alias property (its meaning is unchanged, so keeping it is honest). `ExtractorAgreementCheck` instead drops `agreement_rate`, adds `sentence_match_rate`, and stamps `report_version: 2`, also adding the `status` key its enabled path currently omits.
- **Rationale**: `pairwise` names a shape, not a claim, so it can stay. `agreement_rate` *is* the false claim and cannot be kept under any reading of Requirement 11.4. The check is disabled by default, so version-bumping it has no live consumer.
- **Trade-offs**: Two different backward-compatibility mechanisms in one feature. Justified because the two fields differ in whether the name itself is the defect.

## Risks & Mitigations

- **Worked-example values transcribed incorrectly, producing a test that passes against a wrong implementation** — mitigated three ways: every fixture value in this document was independently recomputed in exact rational arithmetic before being recorded; the fixture format requires a citation alongside the number; and one fixture (Gwet 2014 3x3) exercises all four statistics from a single published input, so a transcription error in one implementation shows up as a disagreement with the others.
- **Weight-direction and pairable-count traps produce plausible wrong numbers with no error raised** — both are pinned by named negative values in the design's Testing Strategy (−0.167067 for the weight-direction trap) and by the small-n Krippendorff fixture, and both are additionally guarded by an assertion inside the implementation.
- **No second rater exists yet, so Requirements 1–8 have no live data path** — mitigated by making the per-field statistics report `NO_COMPARISON_DATA` explicitly rather than appearing computed-and-perfect, and by making the units the unit of test rather than the pipeline.
- **Over-aggressive value normalization silently inflates agreement** — every applied rule is recorded on the unit, and a dedicated test asserts the recorded rule set.
- **The `metrics_hierarchy` key-set change is discovered late and leaves the suite red** — the test update is bound into the same task as the key addition.
- **Uncalibrated parser thresholds produce too many or too few risky pages** — thresholds are configurable, recorded on every record, and explicitly flagged for recalibration by `evaluation-harness`.
- **Renaming `agreement_score` alters branch selection** — a behaviour-preservation test asserts `BranchQualityScore.composite` is numerically identical before and after; the code path additionally has no production caller today.

## References

- Cohen, J. (1960). *A Coefficient of Agreement for Nominal Scales.* — Cohen's kappa, `p_e` from marginal products.
- Cohen, J. (1968). *Weighted kappa: nominal scale agreement with provision for scaled disagreement or partial credit.* — weighted kappa and the disagreement weight matrix.
- Gwet, K. L. (2008). *Computing inter-rater reliability and its variance in the presence of high agreement.* — AC1 and the prevalence paradox.
- Krippendorff, K. (2011). *Computing Krippendorff's Alpha-Reliability.* — coincidence-matrix formulation, nominal and ordinal difference functions, the `n-1` correction.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — Requirement 5 (parser QC and agreement) and Requirement 15 (inter-rater agreement statistics); Open Question 4.
- `.kiro/specs/xtrace-toolkit/requirements.md` — R-QC-3, satisfied by this spec.
- `.kiro/steering/roadmap.md` — spec ownership split, Open Question 4 assignment, dependency-direction constraints.
