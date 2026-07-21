# Brief: agreement-statistics

## Problem
EviTrace reports a number labelled "agreement" that is not an agreement statistic. Researchers,
reviewers, and anyone reading a QC report are being handed a chance-uncorrected ratio under a name
that implies chance correction. For a tool whose entire value proposition is auditability, publishing
a mislabelled reliability figure is a credibility defect, and it directly violates a requirement the
project has already written down for itself.

## Current State
This is a **live violation of xtrace R-QC-3**, which states the system "SHALL compute a named,
statistically-defined inter-rater agreement statistic (e.g. Krippendorff's α) and SHALL NOT label a
binary pass/fail ratio as 'agreement'." The default implementation does exactly the prohibited thing:
`src/quality_control/builtin_impls/inter_rater_report.py:38-44` — `InterRaterReport.compute()` emits
`1.0 if a.status == b.status else 0.0` per extractor pair, i.e. literally a binary pass/fail
comparison stored under `pairwise` agreement scores. Two further mislabels exist:
`src/quality_control/adjudicator.py:278` `_compute_agreement_score()` is a bag-of-words overlap
fraction feeding a 0.15-weighted term in branch scoring, and
`src/quality_control/checks/extractor_agreement.py:206` computes
`agreement_rate = (exact + near matches) / primary_sentence_count`, a raw match ratio.
`src/quality_control/iaa_calculator.py:48-55` is a stub: it reads metric names from
`config.quality_control.iaa_calculator.agreement_metrics` and returns `{metric: None}` — the hook
exists and computes nothing. Grep for `krippendorff|cohen|kappa|gwet` across `src/`, `tests/`, and
`configs/` returns **zero hits**. The good news: the rater → `iaa_calculator.py` → `adjudicator.py` →
`reconciler.py` chain is intact and `InterRaterMetrics` (`src/quality_control/models.py:139`) is an
ABC with `compute(reports)`, so real statistics are a substitution, not a rewrite.

## Desired Outcome
Named, chance-corrected statistics computed by the IAA stage with declared assumptions and reported
alongside the raw percent agreement they replace; every remaining ratio renamed to what it actually
measures; xtrace R-QC-3 satisfied and testable.

## Approach
Implement concrete `InterRaterMetrics` subclasses behind the existing ABC — one per statistic — and
fill in the `iaa_calculator` stub to select them from `agreement_metrics` config. Statistic choice is
driven by data type: Cohen's/weighted kappa for two raters on categorical and ordinal labels,
Krippendorff's alpha for the general/missing-data case, Gwet's AC1 for the prevalence-imbalanced
binary case where kappa is known to collapse. Percent agreement stays, but reported as percent
agreement. Pure-Python implementations validated against published worked examples — no new
dependency for four well-specified formulas.

## Scope
- **In**: normalization of two extractors' per-field outputs for comparison, covering value,
  evidence, confidence, support status, and not-reported agreement (multiagent R15.1–15.2); percent
  agreement, correctly named (R15.3); Cohen's kappa for categorical labels (R15.4); weighted kappa
  for ordered confidence labels (R15.5); Gwet's AC1 / prevalence-robust metrics for imbalanced binary
  labels (R15.6); Krippendorff's alpha per xtrace R-QC-3's named example; disagreement rates broken
  out by field, field group, and document (R15.7); parser *agreement* metrics — token overlap,
  numeric-token overlap, table-detection agreement, section-heading agreement, and text-presence
  agreement — plus marking low-agreement pages as parser-risky and the high-agreement
  skip-parser-counterfactual signal (multiagent R5.3–R5.5, R5.7), owned here because this spec owns
  agreement computation; all five stratification dimensions of multiagent R15.7, where parser-risk
  status becomes populated once multiagent R5 lands (reported as undefined until then) and
  criticality comes from the critical-field designation owned by `corpus-and-schema-builder`;
  renaming the three existing mislabels; degenerate-
  case handling (single rater, zero variance, all-agree, empty comparison set) with an explicit
  undefined/insufficient-data result rather than a silent 1.0.
- **Out**: escalation policy that consumes low agreement (multiagent R15.9) — that is a
  `multiagent-extraction` concern; this spec supplies the numbers and the config surface only.
  Human-vs-agent benchmark collection (`evaluation-harness`). Changing adjudication decision logic —
  only the mislabelled `agreement_score` term is renamed, its weight and behaviour unchanged.
  Reporting UI.

## Boundary Candidates
- Comparison normalization: turning two extraction outputs into aligned rating vectors, separate
  from any statistic.
- The statistic implementations themselves, one class per metric behind `InterRaterMetrics`.
- Metric selection/config in `iaa_calculator.py` — which statistic applies to which label type.
- Disagreement breakdown and report emission.
- Renaming pass over `adjudicator.py` and `checks/extractor_agreement.py`.

## Out of Boundary
- Deterministic evidence validity checks — agreement must never override them (multiagent R15.8).
- Any change to adjudication or reconciliation semantics.
- Deterministic single-parser QC metrics (multiagent R5.1–R5.2) and the parser QC report artifact
  (multiagent R5.9) — inputs to the agreement metrics owned here, not produced here.
- Acting on parser-risk flags: stricter extraction/verification for critical fields on parser-risky
  pages and parser-counterfactual/human-review escalation (multiagent R5.6, R5.8) — owned by
  `evidence-routing`. This spec computes and publishes the flags only.

## Upstream / Downstream
- **Upstream**: none blocking — ships independently of provenance and privacy; the criticality
  stratification dimension reads `corpus-and-schema-builder`'s critical-field flag when available.
- **Downstream**: `evidence-routing` (consumes parser-risky page flags for multiagent R5.6/R5.8);
  `multiagent-extraction` (blind second extractor, escalation policy — a declared
  dependency in the roadmap); `evaluation-harness`; `xtrace-toolkit` (R-QC-3 closes);
  `cost-and-run-reporting` / QC report consumers.

## Existing Spec Touchpoints
- **Extends**: `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` R15.1–15.9; satisfies
  `.kiro/specs/xtrace-toolkit/requirements.md` R-QC-3, which the roadmap's "Existing Spec Updates"
  entry marks for annotation once this ships.
- **Adjacent**: `multiagent-extraction` (owns escalation); `evaluation-harness` (owns benchmarks);
  xtrace R-QC-4 per-field flag CSV (adjacent output, not this spec's).

## Constraints
- `quality_control` must not import `agents`, `pipeline`, or `pdf_extractor` — enforced by AST tests
  in `tests/test_dependency_directions.py`. All work stays inside `src/quality_control/`.
- Shared QC dataclasses live in `src/quality_control/models.py`; import from there only. New concrete
  implementations belong in `src/quality_control/builtin_impls/`, matching `InterRaterReport`'s
  placement, and are loaded by fully-qualified class path from config like the other ABC extension
  points.
- `agreement_metrics` config lives under `quality_control.iaa_calculator`; any new top-level YAML key
  must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`.
- Backward compatibility: `InterRaterReport.pairwise` is the current shipped shape. Either keep it
  populated under an honest name or version the report; do not silently change an existing field's
  meaning.
- Python 3.12.x; prefer stdlib implementations over adding a dependency for four formulas.
