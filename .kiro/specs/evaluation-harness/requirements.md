# Requirements Document

## Project Description (Input)

The multiagent architecture is a research claim: that routing, dual extraction, verification, and repair each improve extraction quality enough to justify their cost. Today that claim is untestable. There is no reference-standard comparison, no per-component ablation, and no way to produce the accuracy, cost, and correction-burden numbers that the twelve success metrics and the two-stage research evaluation plan demand. Reporting exists but is operational rather than evaluative: `extraction_report.py`, `token_report.py`, and the flagged-fields CSV describe what a run *did*; none compares it to a reference standard. There is no importer for human-extracted benchmark tables, no field-level comparison engine, no comparison-mode abstraction, and no ablation switching — `configs/config.yaml` has no notion of a baseline mode and the orchestrator runs one fixed pipeline shape.

What should change: a researcher can point a harness at a corpus plus a human reference standard and obtain manuscript-ready tables covering field-level accuracy, completeness, unsupported-answer rate, evidence-support accuracy, critical-field accuracy, and human-versus-system agreement; and can rerun the same corpus with the counterfactual locator, the second extractor, the verifier, or the repair agent individually disabled, and against one-shot and chunked full-document baselines, reporting accuracy, evidence support, cost, runtime, and manual-review rate per configuration. The harness observes the shipped system; it never forks it, and every added agent becomes a falsifiable expense.

## Introduction

This spec delivers the evaluation half of the multi-agent track: multiagent R24 (human benchmark mode) and multiagent R25 (ablation harness), plus the twelve Success Metrics as this spec's metric catalogue and the two-stage evaluation plan as its two evaluation modes.

The feature has two separable halves. A **comparison engine** imports a human reference standard, compares it field by field against system output under six declared comparison modes, and computes the accuracy and support metric set. An **ablation runner** builds a configuration matrix that disables individual agents or substitutes a full-document baseline, executes the *existing* pipeline under each configuration, and reports accuracy, evidence support, cost, runtime, and manual-review rate per configuration. The runner never forks the pipeline: ablations are configuration switches already exposed by `multiagent-extraction`, `evidence-routing`, and `cost-and-run-reporting`, so the evaluated system is the shipped system.

**Four roadmap Open Questions are resolved here rather than by silent default.**

- **Open Question 7 — how semantic equivalence should be measured for free-text extracted values.** Resolved: by a deterministic-first tiered cascade with an explicit undetermined band, and never by an LLM judge in the default path. Tier 1 is normalization-based equality (case, whitespace, Unicode, punctuation). Tier 2 is content-token overlap above a configured threshold, gated by an absolute rule that the numeric tokens of the two values must be identical — a free-text value whose numbers differ is never equivalent, whatever its wording. Tier 3 is an optional, disabled-by-default embedding-similarity screen that can only move a pair into an **undetermined** band; it can never by itself declare a match, and it is never applied to a field designated critical. Every pair that lands undetermined, and every semantic decision on a critical field, is written to a human adjudication queue. Undetermined pairs are never silently counted as correct or incorrect: each affected metric is reported as an adjudicated point estimate together with a lower bound (undetermined counted as mismatch) and an upper bound (undetermined counted as match). Rationale, recorded so it is not re-litigated silently: an LLM judge would make the evaluation of an LLM system depend on an unvalidated LLM judgment and could not be reported as independent evidence.
- **Open Question 8 — what level of human review is required for publishable validation.** Resolved: three declared review levels, recorded on every reference standard and stamped on every export. **Level A** (single annotator, no adjudication) is development-grade only and is never labelled validation-grade. **Level B** (a previously published human-extracted table imported as-is, single source of truth, disagreements not adjudicated) supports benchmark-comparison claims only. **Level C** (two independent annotators working blind to each other and to system output, with third-reviewer adjudication of every disagreement, human-human agreement reported before any system comparison is reported, and configured minimum document and comparison-unit counts) is the level required for any result presented as validation. Any export below Level C carries an explicit not-validation-grade marking. No result is ever labelled "correct": accuracy is always agreement with the reference standard, consistent with the standing product boundary that extraction correctness is never guaranteed without human validation.
- **Open Question 9 — which benchmark datasets are appropriate for the first evaluation.** Resolved by selection criteria rather than by naming one dataset, with a declared priority order. **First**: an internally authored, redistributable development benchmark built from openly licensed biomedical documents already admitted to the project corpus and annotated at Level C — the only source whose field set is guaranteed to match the pinned extraction schema and whose licensing the project controls. **Second**: retrospective reuse of published data-extraction tables from openly licensed systematic reviews whose included studies are themselves openly licensed, imported at Level B through an explicit column-to-field mapping. **Permanently excluded**: any source containing patient-level data, clinical registry extracts, or protected health information, and any source whose licence forbids redistribution of the fixture. Because the specific source must not be baked in, the importer is required to accept tabular and record-oriented reference tables through a declared mapping rather than a fixed column layout.
- **Open Question 10 — which venue to target first.** Resolved: the methodology paper first, the benchmark paper second. The consequence binds this spec: Stage 1 system-validation output is the primary deliverable and the default manuscript export profile, and Stage 2 benchmark output is a second, opt-in profile that is not on the critical path for the first manuscript.

**Scope caveat carried from the roadmap.** Multiagent R24.6 (prospective review timing, correction burden, and usability capture) requires a human review surface that `reviewer-ui` provides later. It is scoped here as **instrumentation hooks only**: a declared event contract, a sink that records events, and metric definitions that consume them. Stated plainly: **the prospective half of the Stage-2 evaluation is not executable until `reviewer-ui` lands**, and Success Metrics 6 and 7 report as unavailable with a named reason until then.

Requirement coverage relative to the archived source document `evitrace_multiagent.md`: multiagent R24.1–R24.7 and R25.1–R25.7 in full; the twelve Success Metrics as a catalogue with per-metric availability; multiagent R27.1–R27.3 as a consumption clause only.

## Boundary Context

- **In scope**: Importing a human reference standard through a declared column-to-field mapping and recording its provenance, review level, annotator count, blinding state, and adjudication rule; grading a reference standard against the three review levels and refusing a validation-grade label below Level C; field-level comparison of system output against a reference standard under six comparison modes (exact, normalized, categorical, numeric-tolerance, semantic, not-reported), including per-field mode assignment and the recording of which mode decided each comparison; the tiered semantic-equivalence cascade, its undetermined band, its adjudication queue, and bounded metric reporting; the accuracy, completeness, unsupported-answer, evidence-support, and critical-field metric set, and manual-review rate by field group; human-versus-system agreement expressed as rater outputs handed to the agreement module; the twelve-metric catalogue with per-metric availability and named unavailability reasons; the review-timing, correction-burden, and usability event contract and its recording sink; the Stage 1 system-validation evaluation mode and the Stage 2 benchmark evaluation mode; the ablation configuration matrix, per-agent ablation switches, and the two full-document baseline configurations; deterministic document sampling, per-configuration and per-matrix cost ceilings, and matrix-level resumability; the per-configuration comparison report; the manuscript-ready export and its two profiles; and the evaluation configuration surface.
- **Out of scope**: Authoring the benchmark content itself — the harness imports and grades a reference standard, it does not create annotations. The agents being ablated, and any change to extraction, routing, verification, or repair behaviour: this feature switches declared configuration and observes results, and never alters what a stage does. Computing any agreement statistic — percent agreement, Cohen's kappa, weighted kappa, Gwet's AC1, Krippendorff's alpha, degenerate-case handling, and stratification are owned by `agreement-statistics`; this feature supplies rater outputs and reads published results. Token accounting, price tables, cost arithmetic, and run-manifest content — owned by `cost-and-run-reporting`; this feature reads its published run artifacts and never re-derives a price or a token count. Defining what a complete audit trail is, or producing audit artifacts — owned by `provenance-audit-export`. Producing parser-risk flags — owned by `agreement-statistics` and consumed through `evidence-routing`. Any human review, annotation, or adjudication user interface — owned by `reviewer-ui`; this feature emits an adjudication queue and consumes review events, and provides no surface for either. Statistical significance testing, power analysis, or study design beyond metric computation. Re-litigating the token-budget thresholds set by the completed `token-efficient-extraction` spec when a baseline configuration proves more expensive.
- **Adjacent expectations**: This feature expects `multiagent-extraction` to publish, per field, one decision record carrying the final answer, its cited evidence identifiers, its verdict issues, its verification verdict, its repair action, and its terminal state, and to expose an independent enable switch for the second extractor, the verifier, and the repair agent; it consumes those records and switches verbatim. It expects `evidence-routing` to expose an independent enable switch for the counterfactual locator and to publish per-field route traces including evidence support and criticality. It expects `agreement-statistics` to accept rater outputs in its published input shape and to publish agreement results with unit counts and undefined reason codes; the harness reads those results and never recomputes them, and it treats the agreement module's uncalibrated default thresholds as **inputs to recalibration**: the harness measures parser-risk impact on final accuracy and reports a recalibration recommendation, but changing those defaults remains the agreement module's decision. It expects `cost-and-run-reporting` to publish a per-run cost report and run manifest covering per-stage cost, runtime, reliability, and the prompt, schema, model, and configuration identity of the run; the harness reads both and refuses to compare configurations whose recorded identities are incomparable. It expects `provenance-audit-export` to publish, per field, whether its audit trail is complete; until it does, the audit-completeness metric reports as unavailable. It expects `corpus-and-schema-builder` to own corpus membership and the pinned extraction schema version a reference standard is keyed to. It expects `reviewer-ui` to emit review events matching the contract declared here; until it exists, the correction-burden and time-saved metrics report as unavailable rather than as zero.
- **Standing product boundaries**: This feature does not admit fully autonomous systematic review generation without human approval, automated clinical recommendations from extracted evidence, guaranteed extraction correctness without human validation, OCR-heavy scanned-document workflows beyond fallback support, or meta-analysis automation. It makes no compliance claim of any kind. No protected health information, patient-level data, credential, or real patient record may enter any reference standard, fixture, adjudication queue, or exported artifact held in this repository.

## Requirements

### Requirement 1: Reference-Standard Import

**Objective:** As a researcher, I want to import a human-extracted reference table without editing the importer for each dataset, so that the first benchmark choice does not become a hard-coded assumption.

#### Acceptance Criteria

1. When a reference table is imported, the evaluation harness shall accept tabular and record-oriented sources through an explicitly declared mapping from source columns to extraction-schema fields, and shall not require a fixed column layout.
2. When a reference table is imported, the evaluation harness shall record the source identity, the import timestamp, the pinned extraction schema version the reference is keyed to, and the declared mapping used, and shall carry them on the imported reference standard.
3. If a source row references a document that is not in the evaluated corpus, or a field that is not in the pinned extraction schema version, then the evaluation harness shall reject that row with a named reason and shall continue importing the remaining rows.
4. If a source column is not covered by the declared mapping, then the evaluation harness shall record that column as unmapped and shall not infer a field for it.
5. When a reference value is empty or explicitly marks a field as not reported, the evaluation harness shall distinguish those two states from each other and from an absent row.
6. When the same source is imported again, the evaluation harness shall produce the same reference-value identities and shall update rather than duplicate the existing reference standard.
7. When an import completes, the evaluation harness shall report the counts of accepted rows, rejected rows, unmapped columns, and covered documents and fields.
8. The evaluation harness shall reject an import whose source is declared to contain patient-level data or protected health information, and shall name that declaration as the rejection reason.

### Requirement 2: Reference-Standard Review Level and Validation Grading

**Objective:** As a researcher preparing a manuscript, I want the level of human review behind a reference standard recorded and enforced, so that a development-grade annotation can never be presented as validation evidence.

#### Acceptance Criteria

1. When a reference standard is registered, the evaluation harness shall require a declared review level of single-annotator, imported-benchmark, or dual-annotator-adjudicated, and shall reject a reference standard that declares none.
2. When a reference standard declares the dual-annotator-adjudicated level, the evaluation harness shall require the number of annotators, the recorded blinding state of each annotator with respect to the other annotator and to system output, the adjudication rule applied to disagreements, and the adjudicated document and comparison-unit counts.
3. Where a reference standard declares the dual-annotator-adjudicated level, the evaluation harness shall report human-human agreement for that reference standard before reporting any system comparison against it.
4. If a reference standard declaring the dual-annotator-adjudicated level falls below the configured minimum document count or the configured minimum comparison-unit count for a reported field group, then the evaluation harness shall report that field group as below the validation threshold and shall not mark its results validation-grade.
5. If a reference standard was constructed with visibility of system output, then the evaluation harness shall record the reference standard as not blind and shall not mark results computed against it validation-grade.
6. When any metric result or export is produced, the evaluation harness shall stamp it with the review level of the reference standard it used, and shall mark it not validation-grade whenever that level is below dual-annotator-adjudicated.
7. The evaluation harness shall describe every accuracy result as agreement with the reference standard and shall never describe an extracted value as correct or verified.

### Requirement 3: Field-Level Comparison and Comparison Modes

**Objective:** As a researcher, I want each field compared under a mode appropriate to that field, so that a date, a category, a count, and a free-text summary are not judged by the same rule.

#### Acceptance Criteria

1. When a system output and a reference standard are compared, the evaluation harness shall produce one comparison outcome per document-and-field pair covered by either side.
2. The evaluation harness shall support the exact, normalized, categorical, numeric-tolerance, semantic, and not-reported comparison modes, and shall allow additional modes to be registered without changing the existing ones.
3. When a field is compared, the evaluation harness shall select its comparison mode from the extraction schema's declared field format, shall allow that selection to be overridden per field by configuration, and shall record on the comparison which mode decided it.
4. Where the numeric-tolerance mode applies, the evaluation harness shall treat two values as matching only when their parsed numeric content agrees within the configured absolute or relative tolerance, and shall report a mismatch when either side carries no parseable numeric content.
5. Where the categorical mode applies, the evaluation harness shall compare values against the field's declared category set after normalization, and shall report a value outside that set as an out-of-vocabulary mismatch distinct from a wrong-category mismatch.
6. Where the not-reported mode applies, the evaluation harness shall treat agreement on the absence of a value as a match, and shall report an asserted value against a not-reported reference, and a not-reported system answer against an asserted reference, as distinct mismatch kinds.
7. When a document-and-field pair is present in the reference standard but absent from system output, or present in system output but absent from the reference standard, the evaluation harness shall record that as a distinct outcome and shall not count it as a match.
8. When the same system output and reference standard are compared again under the same configuration, the evaluation harness shall produce identical outcomes for every deterministic comparison mode.
9. When a comparison completes, the evaluation harness shall record for each pair the reference value, the system value, the mode applied, the outcome, and the score or reason that produced the outcome.

### Requirement 4: Semantic Equivalence and Undetermined Outcomes

**Objective:** As a researcher, I want free-text equivalence decided by a declared, auditable cascade rather than by an opaque judgment, so that free-text accuracy numbers are defensible.

#### Acceptance Criteria

1. When the semantic mode is applied to a pair of values, the evaluation harness shall first attempt normalization-based equality and shall record a match at that tier when it succeeds.
2. When normalization-based equality fails, the evaluation harness shall compute content-token overlap and shall record a match only when the overlap is at or above the configured threshold and the numeric tokens of the two values are identical.
3. If the numeric tokens of two values differ, then the evaluation harness shall record a mismatch regardless of any similarity score.
4. Where the optional embedding-similarity screen is enabled, the evaluation harness shall use it only to move a pair into the undetermined band and never to record a match on its own.
5. The evaluation harness shall not apply the embedding-similarity screen to a field designated critical, and shall route every semantic decision on a critical field to human adjudication.
6. The evaluation harness shall not invoke any external language model to decide semantic equivalence in the default path, and shall record the resolver identity and version used for every semantic decision.
7. When a pair lands in the undetermined band, the evaluation harness shall record the outcome as undetermined, shall add the pair to the human adjudication queue with both values and the tier that produced the band, and shall not count it as a match or a mismatch.
8. When a human adjudication result is supplied for a queued pair, the evaluation harness shall apply that result to the comparison, shall record the adjudicator identity and timestamp, and shall remove the pair from the outstanding queue.
9. When a metric is affected by undetermined pairs, the evaluation harness shall report the adjudicated point estimate together with a lower bound counting undetermined as mismatch and an upper bound counting undetermined as match, and shall report the count of undetermined pairs alongside them.

### Requirement 5: Accuracy, Completeness, and Support Metrics

**Objective:** As a researcher, I want the accuracy and evidence-support metric set computed from the comparison outcomes, so that the manuscript numbers derive from recorded comparisons rather than from ad-hoc counting.

#### Acceptance Criteria

1. When comparisons are available, the evaluation harness shall compute field-level extraction accuracy overall, per field, and per field group, in each case reporting the numerator, the denominator, and the excluded counts.
2. When comparisons are available, the evaluation harness shall compute completeness as the proportion of reference-covered document-and-field pairs for which the system produced any answer.
3. When system output carries cited evidence, the evaluation harness shall compute the unsupported-answer rate as the proportion of asserted values that carry no valid cited evidence or that failed the evidence support check recorded upstream, and shall not recompute that check itself.
4. When system output carries cited evidence, the evaluation harness shall compute evidence-support accuracy over accepted fields only, and shall state the acceptance criterion the denominator used.
5. When comparisons are available, the evaluation harness shall compute not-reported accuracy separately from asserted-value accuracy.
6. When comparisons are available, the evaluation harness shall compute critical-field accuracy over fields designated critical in the pinned extraction schema, and shall report it separately from overall accuracy.
7. When system output is available, the evaluation harness shall compute the manual-review rate by field group from the terminal states recorded upstream.
8. If a metric's denominator is zero or its inputs are unavailable, then the evaluation harness shall report that metric as undefined with a named reason rather than reporting zero.
9. The evaluation harness shall record, for every computed metric, the reference standard identity, the configuration identity, the comparison-mode assignment, and the counts that produced it.

### Requirement 6: Agreement and Cross-Spec Metric Consumption

**Objective:** As a researcher, I want the twelve success metrics presented as one catalogue with honest availability, so that a metric that cannot yet be computed is visibly absent rather than silently wrong.

#### Acceptance Criteria

1. The evaluation harness shall present all twelve success metrics as a single catalogue, each entry carrying its value, its availability, and, when unavailable, a named reason.
2. When human-versus-system agreement is requested, the evaluation harness shall emit the human reference and the system answer as rater outputs in the shape the agreement module consumes, shall read the agreement results that module publishes, and shall compute no agreement statistic itself.
3. When cost per document and cost per accepted field are requested, the evaluation harness shall derive them from the published run cost artifact and shall not re-derive any price, token count, or currency conversion.
4. If the published run cost artifact is absent or reports its own telemetry as unavailable, then the evaluation harness shall report the cost metrics as unavailable with that reason and shall continue computing the remaining metrics.
5. When audit-trail completeness is requested, the evaluation harness shall read the per-field audit-completeness state published by the audit subsystem, and shall report the metric as unavailable with a named reason while that state does not exist.
6. When parser-risk impact on final extraction accuracy is requested, the evaluation harness shall stratify accuracy by the published parser-risk state of the evidence each field relied on, shall report the metric as unavailable with a named reason while those flags do not exist, and shall not compute or override a risk flag.
7. When parser-risk stratification is available, the evaluation harness shall report a recalibration recommendation for the parser-risk thresholds in force, naming the thresholds observed and the accuracy difference between risky and non-risky strata, and shall not change those thresholds.
8. If an upstream artifact this feature consumes is present but was produced under a different extraction schema version, prompt version, or configuration identity than the run being evaluated, then the evaluation harness shall report the affected metrics as incomparable with a named reason rather than combining them.

### Requirement 7: Human Review Timing and Correction-Burden Instrumentation

**Objective:** As a researcher planning the prospective annotator study, I want the timing and correction contract fixed now, so that the review surface can emit the right events the day it exists.

#### Acceptance Criteria

1. The evaluation harness shall declare a review event contract covering the reviewed document and field, the review session identity, the reviewer identity, the start and end times of the review, the review decision, the pre-review and post-review values, and any usability rating supplied.
2. Where review events are supplied, the evaluation harness shall record them against the run and configuration they belong to and shall make them available to metric computation.
3. When review events are available, the evaluation harness shall compute human correction burden as the proportion and count of reviewed fields whose value was changed, and time saved versus manual extraction as the difference between recorded review time and a declared manual-extraction baseline time.
4. If no review events exist for a run, then the evaluation harness shall report the correction-burden and time-saved metrics as unavailable with a reason naming the absent review surface, and shall not report them as zero.
5. The evaluation harness shall provide no review, annotation, or adjudication user interface, and shall accept review events only as supplied data.
6. The evaluation harness shall record in its own output that the prospective human-in-the-loop half of the Stage 2 evaluation is not executable while the review surface does not exist.

### Requirement 8: Stage 1 System-Validation Evaluation Mode

**Objective:** As a researcher targeting a methodology paper, I want an evaluation mode that needs no human reference standard, so that architecture validation is reachable before benchmark annotation is finished.

#### Acceptance Criteria

1. Where the Stage 1 evaluation mode is selected, the evaluation harness shall run without a reference standard and shall report parser-ensemble outcomes, route quality outcomes, evidence-support outcomes, audit completeness, and the ablation comparison for the evaluated corpus.
2. When Stage 1 runs, the evaluation harness shall read the route quality and parser outcomes published upstream and shall compute none of them itself.
3. When Stage 1 runs, the evaluation harness shall report every metric in the catalogue that does not require a reference standard, and shall mark the reference-standard-dependent metrics as not applicable to this stage rather than unavailable.
4. When Stage 1 completes, the evaluation harness shall produce the Stage 1 table set as its default manuscript export profile.
5. If an upstream stage required by a Stage 1 outcome is disabled in the evaluated configuration, then the evaluation harness shall report that outcome as not applicable naming the disabled stage, and shall continue.

### Requirement 9: Stage 2 Benchmark Evaluation Mode

**Objective:** As a researcher targeting a benchmark paper, I want a mode that evaluates the system against human-extracted data, so that retrospective benchmark claims and, later, the prospective study share one harness.

#### Acceptance Criteria

1. Where the Stage 2 evaluation mode is selected, the evaluation harness shall require a registered reference standard and shall report field-level accuracy, evidence support, human-versus-system agreement, and final human-verified extraction quality where the corresponding inputs exist.
2. When Stage 2 runs against an imported benchmark, the evaluation harness shall report the retrospective results independently of the prospective results and shall not merge them into a single figure.
3. If Stage 2 is selected and no review events exist, then the evaluation harness shall complete the retrospective half, shall report the prospective half as blocked with a named reason, and shall not fail the evaluation.
4. When Stage 2 completes, the evaluation harness shall produce the Stage 2 table set as an opt-in manuscript export profile that is not produced by default.
5. If Stage 2 is selected and no reference standard is registered, then the evaluation harness shall report an error naming the missing reference standard and shall not produce Stage 2 results.

### Requirement 10: Ablation Configuration Matrix

**Objective:** As a researcher, I want each agent turned off individually through the shipped configuration, so that the ablated system is the same system and the comparison means something.

#### Acceptance Criteria

1. The evaluation harness shall define an ablation matrix containing at minimum the full-system configuration and one configuration each with the counterfactual locator, the second extractor, the verifier, and the repair agent individually disabled.
2. When a configuration is applied, the evaluation harness shall express the ablation only as declared configuration switches already exposed by the extraction, routing, and stage-control subsystems, and shall not modify pipeline behaviour or code paths to achieve it.
3. If a requested ablation names a switch that the current configuration surface does not expose, then the evaluation harness shall report an error naming the switch and shall not execute that configuration.
4. When a configuration is defined, the evaluation harness shall assign it a stable identity derived from its switch settings, so that the same configuration yields the same identity across runs.
5. When a configuration executes, the evaluation harness shall record the fully resolved effective configuration for that run alongside its results.
6. The evaluation harness shall allow the matrix to be restricted to a chosen subset of configurations without redefining the matrix.
7. The evaluation harness shall keep each configuration's outputs separate from every other configuration's outputs and from the outputs of ordinary production runs.

### Requirement 11: Full-Document Baseline Configurations

**Objective:** As a researcher, I want one-shot and chunked full-document baselines available for comparison, so that the routed architecture is measured against the naive alternative it claims to beat.

#### Acceptance Criteria

1. The evaluation harness shall provide a one-shot baseline configuration that extracts every field in a single request built from the full document text rather than from routed evidence.
2. The evaluation harness shall provide a chunked baseline configuration that extracts fields in the existing chunk structure from the full document text rather than from routed evidence.
3. The evaluation harness shall make both baseline configurations opt-in and shall never select either as part of a default run.
4. When a baseline configuration executes, the evaluation harness shall require a declared per-run cost ceiling and shall refuse to start the configuration when no ceiling is declared.
5. When a baseline configuration executes, the evaluation harness shall keep the document-level prompt material stable across the requests of a single document, so that the existing prompt-cache guarantee is not violated.
6. If a document's full text exceeds the configured baseline request size limit, then the evaluation harness shall skip that document for that configuration, shall record the skip and its reason, and shall exclude it from that configuration's denominators.
7. When baseline results are reported, the evaluation harness shall report the excluded and skipped document counts alongside them.
8. The evaluation harness shall not alter, relax, or re-derive the token-budget thresholds set by the existing budget enforcement when a baseline proves more expensive.

### Requirement 12: Ablation Execution, Sampling, and Cost Ceilings

**Objective:** As an operator paying per token, I want a sweep that can be sampled, capped, and resumed, so that a full matrix cannot silently consume the budget.

#### Acceptance Criteria

1. When a matrix is executed, the evaluation harness shall execute each configuration by invoking the existing pipeline with that configuration's switches and shall not implement a second extraction path.
2. Where document sampling is configured, the evaluation harness shall select the sampled documents deterministically from a recorded seed, shall use the same sample for every configuration in the matrix, and shall record the sample membership.
3. The evaluation harness shall enforce a configured per-configuration cost ceiling and a configured matrix-level cost ceiling, evaluated against the published run cost artifact.
4. If a cost ceiling is reached during a matrix, then the evaluation harness shall stop starting new work for the affected scope, shall record which configurations completed and which were not attempted, and shall report the partial matrix rather than discarding it.
5. If a single document fails within a configuration, then the evaluation harness shall record that document as failed for that configuration, shall exclude it from that configuration's denominators, and shall continue with the remaining documents.
6. If a whole configuration fails, then the evaluation harness shall record the configuration as failed with a reason and shall continue with the remaining configurations.
7. When a matrix is resumed after an interruption, the evaluation harness shall reuse the recorded results of configurations that already completed and shall not re-execute them.
8. If a recorded configuration result was produced under a different extraction schema version, corpus sample, or prompt version than the current matrix, then the evaluation harness shall discard it and re-execute that configuration rather than reusing it.

### Requirement 13: Per-Configuration Comparison Report

**Objective:** As a researcher, I want one report that puts every configuration side by side, so that the cost of each agent can be weighed against what it bought.

#### Acceptance Criteria

1. When a matrix completes, the evaluation harness shall produce one report row per configuration carrying accuracy, evidence support, cost, runtime, and manual-review rate.
2. When a report row is produced, the evaluation harness shall include the configuration identity, its switch settings, the documents attempted, the documents completed, and the documents excluded.
3. When more than one configuration completed, the evaluation harness shall report each ablated configuration's difference from the full-system configuration for every reported metric.
4. If a configuration did not complete, then the evaluation harness shall include its row marked incomplete with the recorded reason and shall not omit it from the report.
5. If two configurations are not comparable because their recorded schema, prompt, or corpus identities differ, then the evaluation harness shall mark the comparison incomparable with a named reason rather than reporting a difference.
6. The evaluation harness shall report differences as observed differences and shall attach no significance claim, confidence interval, or hypothesis-test result to them.
7. When the report is produced, the evaluation harness shall record the review level and validation grading of any reference standard used, on every row that depended on it.

### Requirement 14: Manuscript-Ready Export

**Objective:** As a researcher writing the paper, I want the numbers exported in a form a manuscript table can consume directly, so that transcription by hand is not the last step.

#### Acceptance Criteria

1. When an export is requested, the evaluation harness shall produce the metric catalogue, the per-configuration comparison, and the per-field accuracy breakdown as machine-readable artifacts.
2. The evaluation harness shall produce the Stage 1 table set as its default export profile and the Stage 2 table set as an opt-in profile.
3. When a per-field export is produced, the evaluation harness shall keep it compatible with the existing per-field flag export's row shape so that the two can be read by the same tooling.
4. When any artifact is exported, the evaluation harness shall stamp it with the run identities, the configuration identity, the extraction schema version, the prompt versions, the reference-standard identity and review level, and the harness version.
5. When a metric is unavailable, undefined, or bounded, the evaluation harness shall carry that state into the export rather than emitting a bare number.
6. The evaluation harness shall exclude document text, evidence text, reference values, and reviewer identities from any export marked as shareable, and shall default an export's sharing suitability to the most restrictive value.
7. If writing an export artifact fails, then the evaluation harness shall report the failure naming the artifact and shall leave any previously valid artifact in place.

### Requirement 15: Configuration, Run Identity, and Observation-Only Behaviour

**Objective:** As an operator, I want the harness controllable from configuration and provably incapable of changing what it measures, so that enabling evaluation cannot alter a production run.

#### Acceptance Criteria

1. The evaluation harness shall expose in configuration the evaluation stage, the reference-standard location and review-level minimums, the per-field comparison-mode overrides, the numeric tolerances, the semantic thresholds and the embedding screen's enablement, the ablation matrix membership, the sampling fraction and seed, the per-configuration and matrix cost ceilings, the baseline request size limit, and the export profile and output location.
2. When configuration omits a setting, the evaluation harness shall apply a documented default and shall record the effective value with the run's results.
3. If a configured setting is invalid, then the evaluation harness shall report an error naming the setting and the invalid value and shall not start the evaluation.
4. Where the evaluation harness is disabled in configuration, the pipeline shall run to completion exactly as it does today and shall produce no evaluation artifact.
5. When the evaluation harness runs, it shall not modify extraction, routing, verification, repair, or quality-control behaviour beyond applying the declared configuration switches of the configuration under evaluation.
6. When a configuration is evaluated, the evaluation harness shall record the run identities, the extraction schema version, the prompt versions, the model identities, and the configuration hash from the published run artifacts, and shall not re-derive them.
7. If a required upstream run artifact is missing for a configuration, then the evaluation harness shall report the affected results as unavailable naming the missing artifact and shall continue with the remaining results.
8. The evaluation harness shall keep every artifact it writes under its own output location and shall not overwrite an extraction output, a cost artifact, a run manifest, or an audit artifact.
