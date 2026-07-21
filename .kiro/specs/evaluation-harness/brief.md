# Brief: evaluation-harness

Source document: `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md`
(sections "Success Metrics", "Proposed Research Evaluation", "Open Questions").
All requirement citations below use the explicit `multiagent R<n>` prefix — all three
original idea documents number their requirements R1–R14, so bare `R<n>` is ambiguous.

## Problem
The multiagent architecture is a research claim: that routing, dual extraction,
verification, and repair each improve extraction quality enough to justify their
cost. Right now that claim is untestable. There is no reference-standard
comparison, no per-component ablation, and no way to produce the accuracy /
cost / correction-burden numbers the twelve success metrics (enumerated in
`## Success Metrics` below) and the two-stage research evaluation plan
(`## Two-Stage Evaluation Plan` below) demand. Without the harness, every added agent is an
unfalsifiable expense.

## Current State
- Reporting exists but is operational, not evaluative: `src/pipeline/
  extraction_report.py`, `token_report.py`, and `generate_qc_report()` →
  `outputs/qc_report.csv`. All of these describe what the run *did*, none compare
  it to a reference standard.
- There is no importer for human-extracted benchmark tables, no field-level
  comparison engine, no comparison-mode abstraction (exact / normalized /
  categorical / numeric-tolerance / semantic / not-reported, multiagent R24.3).
- Normalizers and matchers that a comparison engine can reuse already exist in
  `src/text_processing/` (normalizers, tokenizers, matchers, embedding) —
  the harness should build on them, not reimplement.
- No ablation switching exists. `configs/config.yaml` has no notion of a
  baseline mode; `src/pipeline/orchestrator.py` runs one fixed pipeline shape.
  The one-shot and chunked full-PDF baselines (multiagent R25.1–R25.2) are not reachable
  from configuration.
- No human-review timing, correction-burden, or usability instrumentation
  (multiagent R24.6) — there is no review surface to instrument yet.

## Desired Outcome
A researcher can point the harness at a corpus plus a human reference standard
and get manuscript-ready tables (multiagent R24.7): field-level accuracy, completeness,
unsupported-answer rate, evidence-support accuracy, and critical-field accuracy
(multiagent R24.4), plus human-vs-system agreement (multiagent R24.5). The same harness can rerun the
corpus with Agent 0c, 1B, 1c, or 3 individually disabled and against one-shot
and chunked full-PDF baselines, reporting accuracy, evidence support, cost,
runtime, and manual-review rate per configuration (multiagent R25.7).

## Success Metrics
The twelve metrics below are reproduced verbatim from the "Success Metrics" section
of the source document and form this spec's metric catalogue. They are the definitive
list — no other document needs to be consulted to enumerate them.

1. Field-level extraction accuracy against human reference standard.
2. Evidence-support accuracy for accepted fields.
3. Unsupported-answer rate.
4. Not-reported accuracy.
5. Critical-field accuracy.
6. Human correction burden.
7. Time saved versus manual extraction.
8. Inter-agent and human-system agreement.
9. Cost per PDF and cost per accepted field.
10. Percentage of fields with complete audit trails.
11. Manual-review rate by field group.
12. Parser-risk impact on final extraction accuracy.

**Cross-spec computability.** Not every metric is computable from this spec alone:
- Metrics 6 and 7 (human correction burden; time saved versus manual extraction)
  require human timing and correction telemetry from `reviewer-ui`. Until it lands,
  scope them here as instrumentation hooks only (multiagent R24.6).
- Metric 8 (inter-agent and human-system agreement) consumes the kappa/alpha
  implementations owned by `agreement-statistics`.
- Metric 9 (cost per PDF and per accepted field) consumes the token/cost accounting
  primitives owned by `cost-and-run-reporting`.
- Metric 10 (percentage of fields with complete audit trails) requires the audit-trail
  records produced by `provenance-audit-export`; it is not computable until that
  spec defines what "complete" means.
- Metric 12 (parser-risk impact on final extraction accuracy) requires parser-risk
  flags from the parser-ensemble / routing work in `multiagent-extraction`; without
  those flags there is nothing to stratify accuracy by.
- Metrics 1–5 and 11 are computable inside this spec given a reference standard.

## Two-Stage Evaluation Plan
The source document specifies that the framework "should be evaluated in two stages".
These are the two evaluation modes the harness must support; their contents are:

- **Stage 1 — Methodology and System Validation.** Evaluates whether the architecture
  works as designed. It should include parser ensemble testing, route quality
  assessment, extraction accuracy, evidence support, ablation studies, and audit
  completeness on a development corpus. This stage is self-contained: it needs no
  human reference standard, so it is the mode reachable first.
- **Stage 2 — Benchmark and Human-in-the-Loop Evaluation.** Evaluates the framework
  against retrospective human-extracted benchmark data and a prospective human
  annotator study. Outcomes should include accuracy, time savings, correction burden,
  usability, and final human-verified extraction quality. The prospective half is
  blocked on `reviewer-ui`; the retrospective half needs only imported benchmark
  tables (multiagent R24.1).

## Approach
Two separable halves matching the two-stage evaluation plan above: a **comparison
engine** (benchmark import + pluggable comparison modes + metric computation) and
an **ablation runner** (a configuration matrix that turns each agent off and
executes the existing pipeline unchanged). The runner must not fork the pipeline —
ablations are config flags consumed by `multiagent-extraction`'s stage gates, so
the evaluated system is the shipped system.

## Scope
- **In**: reference-standard import (multiagent R24.1); field-level comparison across all six
  comparison modes (multiagent R24.2–R24.3); the accuracy/support/completeness metric set
  (multiagent R24.4–R24.5); prospective review timing, correction burden, and usability data
  capture hooks (multiagent R24.6); manuscript-ready export (multiagent R24.7); one-shot and chunked
  full-PDF baselines (multiagent R25.1–R25.2); per-agent ablation switches (multiagent R25.3–R25.6);
  the per-configuration report (multiagent R25.7); the twelve Success Metrics enumerated in the
  `## Success Metrics` section above as the metric catalogue; the Stage 1 and Stage 2
  evaluation modes described in `## Two-Stage Evaluation Plan` above.
- **Out**: authoring the benchmark data itself; the agents being ablated;
  agreement-statistic implementations (owned by `agreement-statistics`; consumed
  here for metric 8); token/cost accounting primitives (owned by
  `cost-and-run-reporting`; consumed here for metric 9); the review UI that
  timing data would come from (owned by `reviewer-ui`).

## Boundary Candidates
- Benchmark ingestion and reference-standard schema vs. comparison execution.
- Comparison modes (per-field, pluggable) vs. metric aggregation and reporting.
- Ablation configuration matrix vs. run execution and result collection.
- Stage-1 system-validation metrics (route quality, audit completeness) vs.
  Stage-2 human-benchmark metrics.

## Out of Boundary
- Any change to extraction, routing, verification, or repair behaviour — the
  harness observes, it does not alter.
- Statistical significance testing or study design beyond metric computation.
- Re-litigating `token-efficient-extraction` budget thresholds when baselines
  turn out more expensive.

## Upstream / Downstream
- **Upstream**: `multiagent-extraction` (the agents being ablated and the per-field
  decision records), `evidence-routing` (Agent 0c ablation), `agreement-statistics`,
  `cost-and-run-reporting`, `provenance-audit-export` (audit-completeness metric).
- **Downstream**: manuscript reporting and the Stage-2 human annotator study;
  `reviewer-ui` (source of multiagent R24.6 timing/correction telemetry once it exists).

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` R-X-2 (reproducibility manifest) — each ablation
  run must be identifiable by manifest; R-QC-4 (per-field flag CSV) is the closest
  existing artifact and the export format should stay compatible. This spec covers
  the part of the multiagent doc that `xtrace-toolkit` does not.
- **Adjacent**: `agreement-statistics` (owns kappa/alpha), `cost-and-run-reporting`
  (owns token and cost accounting), `corpus-and-schema-builder` (owns corpus
  definition and the schema versions benchmarks are keyed to).

## Constraints
- Baseline modes (multiagent R25.1–R25.2) send full-PDF text to the model, which conflicts
  with `_shared_paper_prefix` cache assumptions and with the token budgets set by
  `token-efficient-extraction`; baselines must be explicitly opt-in, isolated, and
  cost-capped rather than a default path.
- Cost: a full ablation sweep is N configurations × the full corpus × up to four
  LLM passes per field. Sampling and per-run cost ceilings are required.
- No real patient data or PHI may enter benchmark fixtures.
- Prompt and schema versions must be recorded per run (multiagent R27.1–R27.3) or metrics are
  not comparable across configurations.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS`
  (`src/utils/config_utils.py`).

## Open Questions (UNRESOLVED — design inputs to this spec)
Four of the source document's ten Open Questions are direct design inputs to this
spec. They are quoted verbatim below and are **unresolved**: each must be answered
during this spec's requirements phase, because the metric definitions and the
evaluation plan cannot be finalised without them.

1. **Q7 (multiagent Open Question 7)** — "How should semantic equivalence be
   measured for free-text extracted values?" Blocks the `semantic` comparison mode
   (multiagent R24.3) and therefore metrics 1–5 on all free-text fields.
2. **Q8 (multiagent Open Question 8)** — "What level of human review is required for
   publishable validation?" Blocks the Stage-2 design (how many annotators, what
   adjudication) and the reference-standard schema.
3. **Q9 (multiagent Open Question 9)** — "Which benchmark datasets are appropriate
   for the first evaluation?" Blocks reference-standard import (multiagent R24.1);
   the importer's shape depends on the chosen benchmark's format.
4. **Q10 (multiagent Open Question 10)** — "Which venue should be targeted first:
   JBI methodology paper or benchmark paper?" Determines whether Stage 1 or Stage 2
   output is the primary deliverable, and hence which manuscript-ready tables
   (multiagent R24.7) are built first.

The remaining six open questions (multiagent Open Questions 1–6) are design inputs to
`multiagent-extraction` and `evidence-routing`, not to this spec.
