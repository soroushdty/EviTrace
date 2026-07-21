# Requirements Document

## Project Description (Input)
A single LLM extractor is a single point of failure: when it is confidently wrong, nothing downstream disagrees with it. Researchers doing systematic review need to know which extracted values are *defensible* — supported by cited evidence, corroborated by an independent rater, and survivable under adversarial challenge — and today EviTrace can tell them none of that. Every accepted field carries the same unearned credibility.

Today one extraction pass exists. Chunks already receive per-chunk evidence packages rather than the full PDF, and outputs are already compact and locally validated against the expected field set, key schema, confidence enum, and evidence-identifier membership. What is missing is extraction packs built from *routes*, mandatory evidence-identifier citation for every non-not-reported value, a blind second extractor, deterministic quality control of extracted *answers* (rather than of parser branches), an adversarial verifier, a deterministic answer adjudicator, and a targeted repair agent for wrong — as opposed to malformed — answers.

This feature makes each field's final value the output of an auditable decision: targeted extraction from a route-derived pack, a blind second extraction where policy requires it, deterministic extraction quality control including quote-to-evidence fuzzy matching, verifier adjudication of support status, a local adjudicator that accepts, repairs, or escalates using evidence validity, agreement, and criticality, and a targeted repair agent for the residue — with the decision rule, its inputs, and its provenance stored for every field.

## Introduction

This spec delivers the answer-production half of the multi-agent track: multiagent R12 (Agent 1A targeted extraction with mandatory citation), R13 (blind second extractor), R14 (extraction quality control), R16 (Agent 1c counterfactual answer verifier), R17 (local answer adjudication), R18 (Agent 3 answer repair), and **R15.9 only** from R15 — the escalation policy that acts on low agreement. R15.1 through R15.8 belong to `agreement-statistics` and are neither restated nor reimplemented here.

The layer is a sequence of gated stages with a deterministic gate on both sides of every model call: Agent 1A always runs, Agent 1B runs only where the dual-extraction policy requires it, deterministic extraction quality control judges both, Agent 1c challenges only fields the gates select, a deterministic adjudicator decides accept/repair/manual-review, and Agent 3 repairs only the residue and is re-checked by the same quality control. Cost is the dominant constraint — four model passes per field is the worst case the gates exist to prevent.

Two roadmap Open Questions are resolved here rather than by silent default:

- **Open Question 2 — how much dual extraction is needed after calibration.** Resolved: dual extraction is governed by an explicit two-mode policy. In calibration mode Agent 1B runs on every field sent to Agent 1A. In production mode Agent 1B runs on (a) every field designated critical, (b) every field whose adjudicated route is empty, low-confidence, or marked as requiring stricter handling, (c) every field whose route depends on a page published as parser-risky, (d) every field belonging to a field group whose published chance-corrected agreement on the value dimension is below a configured release threshold or that has not yet accumulated a configured minimum number of comparison units, and (e) a configured random quality-assurance fraction of the remaining fields, selected deterministically from a recorded seed. A field group leaves mandatory dual extraction only when both its accumulated comparison-unit count and its published agreement statistic clear their configured thresholds; the defaults are a minimum of 50 comparison units and a value-dimension statistic of at least 0.80. The counts and statistics themselves are read from `agreement-statistics`; only the policy that reads them is owned here.
- **Open Question 3 — which fields require mandatory Agent 1c verification.** Resolved: verification is mandatory, and never suppressed by the per-document call budget, for (a) every field designated critical, including one answered as not reported, (b) every field whose extraction quality control raised a blocking issue, (c) every field on which the two extractors disagree on the value or support-status dimension, and (d) every field whose adjudicated route was marked as requiring stricter handling by the routing layer. Verification is discretionary, subject to the per-document budget and a configured confidence threshold, for low-confidence and not-reported non-critical fields. All other fields are not verified. When the budget forces a discretionary verification to be skipped, the skip and its reason are recorded rather than silently dropped.

Requirement coverage relative to the archived source document `evitrace_multiagent.md`: multiagent R12, R13, R14, R16, R17, R18 in full, and multiagent R15.9 as a consumption clause only.

## Boundary Context

- **In scope**: Building each extraction request from the routing layer's adjudicated routes and extraction packs; the compact answer contract and the requirement that every non-not-reported value cite at least one evidence identifier; absence answers that cite the locations at which absence was verified; bounded schema repair and pack flagging for malformed extractor output; unconditional persistence of every agent's raw output; the blind second extractor, its blindness guarantee, its decorrelation levers, and the dual-extraction sampling policy including its calibration and production modes; deterministic extraction quality control covering coverage, duplicate field identifiers, evidence-identifier validity, empty values, confidence labels, quote-to-evidence fuzzy matching, high-confidence answers with missing or unsupported evidence, numeric fields without numeric content, and critical fields that are low-confidence, not reported, unsupported, or parser-risky; the mapping from a quality-control issue to its escalation destination; the escalation policy that increases verification, dual extraction, or manual review when published agreement is low; verifier gating, its request contract, its seven verdict states, and the constraint that it never rewrites fields wholesale; deterministic answer adjudication over evidence validity, agreement, verification, and criticality, producing accept, repair, or manual-review with the decision rule, its inputs, and its provenance recorded per field; the targeted answer repair agent, its four permitted actions, its bounded inputs, and mandatory re-running of extraction quality control on repaired fields; the per-agent prompt prefixes, their versioning, and the per-document call budgets; the configuration surface including independent disablement of each agent; and failure isolation and resumability across the stage graph.
- **Out of scope**: Computing any agreement statistic — normalization into comparison units, percent agreement, Cohen's kappa, weighted kappa, Gwet's AC1, Krippendorff's alpha, degenerate-case handling, and disagreement stratification are owned by `agreement-statistics`; this feature supplies rater outputs to it and reads its published results. Computing parser agreement metrics, parser-risky page marking, or the counterfactual-audit threshold — also owned by `agreement-statistics`. Choosing evidence locations, building document indices, route quality control, counterfactual location, route adjudication, extraction pack assembly, or evidence trimming — owned by `evidence-routing`; this feature consumes packs and route traces without modifying them. Defining evidence node identity, claim records, derivation records, or the provenance graph — owned by `provenance-core`. Repair of malformed model output — the existing bounded schema-repair loop remains the layer beneath this feature and is not replaced. Benchmark comparison, ablation execution, and success metrics — owned by `evaluation-harness`. Any human review surface, review queue interface, merger or export surface — owned by `reviewer-ui`; this feature produces only the manual-review terminal state. Formatting or exporting any audit report — owned by `provenance-audit-export`. Per-stage cost and token report content — owned by `cost-and-run-reporting`.
- **Adjacent expectations**: This feature expects the routing layer to publish, per field, exactly one adjudicated route carrying its ordered evidence, its primary evidence identifiers, its criticality designation, its resolved parser-risk state, and its stricter-handling marking, together with token-capped extraction packs and the record of every discarded evidence identifier; it consumes those verbatim and never re-derives them. It expects evidence identifiers to be the identifiers already assigned by the document pipeline. It expects `agreement-statistics` to publish per-field-group agreement results, their unit counts, and their undefined reason codes; while those results are absent, this feature treats agreement as unknown and applies the documented unknown-agreement behavior rather than assuming agreement is high. It expects the per-field criticality designation to come from the extraction schema; while criticality is absent, a documented default is applied and recorded as defaulted. It expects the external model call path, including any privacy gateway placed in front of it, to remain the single path through which model calls are made. It expects final extraction outputs to be persisted successfully by the pipeline; until that holds, none of this feature's outputs are observable.
- **Standing product boundaries**: This feature does not admit fully autonomous systematic review generation without human approval, automated clinical recommendations from extracted evidence, guaranteed extraction correctness without human validation, OCR-heavy scanned-document workflows beyond fallback support, or meta-analysis automation. No accepted field may be presented as verified truth; acceptance means the decision rule that produced it is recorded and defensible, not that the value is correct.

## Requirements

### Requirement 1: Route-Fed Targeted Extraction

**Objective:** As a reviewer, I want the primary extractor to work from route-derived evidence packs rather than the whole paper, so that every answer is produced from locations that were deliberately chosen and recorded.

#### Acceptance Criteria

1. When adjudicated routes and extraction packs are available for a document, the multiagent extraction service shall build each primary extraction request from those packs rather than from the full document text.
2. When an extraction pack is unavailable for a field because routing was disabled or failed, the multiagent extraction service shall fall back to the pipeline's existing evidence selection for that field and shall record that the field was extracted without a route.
3. When the primary extractor returns an answer, the multiagent extraction service shall require compact output carrying the field index, the extracted value, the cited evidence identifiers, a short supporting quote or evidence phrase, and a confidence label, and nothing else.
4. The multiagent extraction service shall reject primary extractor output that carries field names, domain groups, document names, or any other metadata that the pipeline reattaches locally, and shall treat that output as a contract violation.
5. When the primary extractor returns evidence identifiers, the multiagent extraction service shall verify that every identifier exists among the identifiers supplied to that request and shall report an invalid identifier as a named issue rather than accepting it.
6. If primary extractor output fails schema validation, then the multiagent extraction service shall retry, repair, or flag the affected pack according to configuration, using the pipeline's existing bounded malformed-output repair path rather than a second one.
7. When a primary extraction call completes, the multiagent extraction service shall persist that call's raw output, the request that produced it, and the model identity in effect, in a form retrievable for the run.

### Requirement 2: Mandatory Evidence Citation and Absence Answers

**Objective:** As an institutional reviewer, I want every asserted value to name the evidence it came from, so that no accepted answer is unattributable to a location in the document.

#### Acceptance Criteria

1. When an extractor returns a value other than the configured not-reported value, the multiagent extraction service shall require at least one cited evidence identifier for that value.
2. If an extractor returns a non-not-reported value with no cited evidence identifier, then the multiagent extraction service shall record an uncited-value issue for that field and shall not accept the value on the strength of the extractor's confidence.
3. When the evidence for a field is absent from the routed locations, the multiagent extraction service shall require the configured not-reported value together with the evidence identifiers at which the absence was checked, where those locations are available.
4. When an absence answer carries no verification identifiers because the route supplied none, the multiagent extraction service shall record the absence as unverified rather than as verified.
5. When a field is pre-filled by the pipeline without a model call, the multiagent extraction service shall not request extraction for it and shall record that the field was excluded from extraction and why.

### Requirement 3: Blind Second Extraction

**Objective:** As a researcher, I want an independent second extraction that cannot see the first answer, so that agreement between the two means something.

#### Acceptance Criteria

1. When the second extractor is invoked for a field, the multiagent extraction service shall exclude the first extractor's value, quote, confidence, and cited identifiers from that request.
2. The multiagent extraction service shall require the second extractor to return the same compact output contract as the primary extractor.
3. Where decorrelation levers are configured, the multiagent extraction service shall vary the second extractor's prompt framing, evidence snippet order, or model identity relative to the primary extractor, and shall record which levers were applied.
4. When a second extraction call completes, the multiagent extraction service shall persist its raw output separately from the primary extractor's raw output and shall associate both with the field they answer.
5. When both extractors have produced an answer for a field, the multiagent extraction service shall emit one rater output record per extractor per field, in the form the agreement statistics module consumes, and shall not compute any agreement statistic itself.
6. If the second extraction call fails or its output fails validation after the configured retry limit, then the multiagent extraction service shall record the second extraction as not completed with a reason, shall retain the primary answer, and shall continue processing the remaining fields.

### Requirement 4: Dual-Extraction Policy

**Objective:** As an operator paying per token, I want dual extraction targeted by an explicit policy rather than applied to everything, so that the second opinion is bought where it changes a decision.

#### Acceptance Criteria

1. Where calibration mode is configured, the multiagent extraction service shall invoke the second extractor for every field sent to the primary extractor unless the second extractor is disabled.
2. Where production mode is configured, the multiagent extraction service shall invoke the second extractor for every field designated critical, every field whose adjudicated route is empty, low-confidence, or marked as requiring stricter handling, every field whose route depends on a page published as parser-risky, and every field in a field group that has not cleared the configured release thresholds.
3. Where production mode is configured, the multiagent extraction service shall additionally select a configured fraction of the remaining fields for dual extraction by a deterministic selection derived from a recorded seed, so that the same run configuration selects the same fields on a repeated run.
4. The multiagent extraction service shall treat a field group as having cleared the release thresholds only when its published comparison-unit count is at or above the configured minimum and its published chance-corrected agreement on the value dimension is at or above the configured threshold.
5. If no published agreement result exists for a field group, or the published result is undefined, then the multiagent extraction service shall treat that group as not cleared and shall record the reason as unknown agreement rather than treating it as cleared.
6. When the second extractor is selected or not selected for a field, the multiagent extraction service shall record the decision and the rule that produced it for every field.
7. The multiagent extraction service shall enforce a configurable upper bound on second-extraction calls per document and shall record when that bound suppressed a selected call.
8. The multiagent extraction service shall not compute, adjust, or override any published agreement statistic, unit count, or undefined reason code.

### Requirement 5: Extraction Quality Control

**Objective:** As a reviewer, I want extracted answers checked deterministically against their cited evidence before anything is accepted, so that unsupported or malformed answers are caught without another model call.

#### Acceptance Criteria

1. When extractor outputs are received for a document, the extraction quality control stage shall validate them against the declared answer schema before any answer is adjudicated.
2. The extraction quality control stage shall detect missing coverage of a requested field, duplicate field identifiers, invalid evidence identifiers, empty values, and disallowed confidence labels, and shall report each as a named issue.
3. When an answer carries a short quote, the extraction quality control stage shall fuzzy-match that quote against the text of the evidence it cites and shall report a quote-mismatch issue when the match score falls below the configured threshold.
4. When an answer carries a high confidence label together with missing evidence identifiers or evidence that failed the quote match, the extraction quality control stage shall report an unsupported-high-confidence issue.
5. When a field's expected value format calls for a number and the extracted value contains no numeric content, the extraction quality control stage shall report a missing-numeric-content issue.
6. When a field designated critical is answered with low confidence, with the not-reported value, without supporting evidence, or from a route depending on a page published as parser-risky, the extraction quality control stage shall report a critical-field-at-risk issue naming which condition applied.
7. When extraction quality control completes for a field, the extraction quality control stage shall record a pass or fail status together with the full issue list, for passing and failing fields alike.
8. The extraction quality control stage shall reach the same verdicts for the same answers, evidence, and configuration on repeated runs, and shall invoke no model.

### Requirement 6: Escalation Eligibility

**Objective:** As an operator, I want each quality-control issue to have a declared destination, so that a detected problem always produces a next action instead of a log line.

#### Acceptance Criteria

1. When extraction quality control reports an issue for a field, the multiagent extraction service shall assign that field to verification, to repair, or to manual review according to the configured mapping from issue kind to destination, and shall record the assignment.
2. When a field carries several issues, the multiagent extraction service shall assign the most severe configured destination among them and shall record every issue that contributed.
3. If a reported issue kind has no configured destination, then the multiagent extraction service shall assign the documented default destination, shall log a warning naming the issue kind, and shall continue.
4. When a field passes extraction quality control and is selected by no other gate, the multiagent extraction service shall record that the field was eligible for acceptance without further model calls.

### Requirement 7: Low-Agreement Escalation Policy

**Objective:** As a researcher, I want low measured agreement to change how much scrutiny a field group receives, so that escalation is driven by evidence rather than by intuition.

#### Acceptance Criteria

1. When the published agreement result for a field group falls below the configured escalation threshold, the multiagent extraction service shall increase verification coverage, dual extraction coverage, or manual-review assignment for that group according to configuration, and shall record which escalation was applied.
2. When an escalation is applied on agreement grounds, the multiagent extraction service shall record the published statistic name, its value, its unit count, and the threshold in force at the time of the decision.
3. If the published agreement result for a field group is undefined or absent, then the multiagent extraction service shall apply the documented unknown-agreement escalation and shall record that agreement was unknown rather than low.
4. The multiagent extraction service shall never use an agreement value to relax an escalation, to override a deterministic evidence validity check, or to accept a field that quality control failed.
5. The multiagent extraction service shall apply the agreement escalation policy as configuration and shall neither compute nor modify the statistics it reads.

### Requirement 8: Verifier Gating

**Objective:** As an operator, I want the verifier invoked on a declared set of fields rather than on everything, so that adversarial checking is affordable and its coverage is auditable.

#### Acceptance Criteria

1. The multiagent extraction service shall invoke the verifier for every field designated critical, every field whose extraction quality control raised an issue mapped to verification, every field on which the two extractors disagree on the extracted value or the support status, and every field whose adjudicated route was marked as requiring stricter handling.
2. Where discretionary verification is configured, the multiagent extraction service shall additionally consider fields answered with confidence below the configured threshold and non-critical fields answered as not reported.
3. The multiagent extraction service shall enforce a configurable upper bound on verification calls per document, shall never let that bound suppress a mandatory verification, and shall record every discretionary verification the bound suppressed together with the reason.
4. When verification selection completes, the multiagent extraction service shall record for every field whether it was selected, which rule selected it, and whether the call was made.
5. If the mandatory verification set for a document exceeds the configured call bound, then the multiagent extraction service shall still perform every mandatory verification and shall record that the bound was exceeded on mandatory grounds.

### Requirement 9: Verifier Contract and Verdicts

**Objective:** As a reviewer, I want a verifier that judges support and challenges the answer rather than rewriting the extraction, so that verification remains a separate, auditable opinion.

#### Acceptance Criteria

1. When the verifier is invoked for a field, the multiagent extraction service shall supply it the field definition, the candidate answer or answers, the cited evidence, the alternative evidence available from the adjudicated route, and the quality-control issues raised for that field.
2. When the verifier returns a verdict, the multiagent extraction service shall require exactly one of supported, unsupported, contradicted, incomplete, alternative found, not-reported supported, or needs manual review.
3. The multiagent extraction service shall reject verifier output that proposes a new value for a field it was not asked to verify, and shall treat that output as a contract violation.
4. When the verifier proposes a better value, the multiagent extraction service shall require supporting evidence identifiers and a rationale with it, and shall reject a proposal that carries neither.
5. When the verifier returns unsupported, contradicted, incomplete, or alternative found, the multiagent extraction service shall send that field to answer adjudication and shall make it eligible for repair.
6. When a verification call completes, the multiagent extraction service shall persist its raw output, the request that produced it, and the model identity in effect, and shall associate them with the field verified.
7. If a verification call fails or its output fails validation after the configured retry limit, then the multiagent extraction service shall record the verification as not completed with a reason and shall adjudicate the field without a verdict rather than discarding the field.

### Requirement 10: Answer Adjudication

**Objective:** As a reviewer, I want the final accept, repair, or review decision made by a deterministic rule over evidence validity, agreement, verification, and criticality, so that every accepted field is defensible and every rejection is explainable.

#### Acceptance Criteria

1. When the primary answer, any second answer, the quality-control verdict, the published agreement result, and any verification verdict are available for a field, the answer adjudicator shall decide whether that field is accepted, sent for repair, or marked for manual review.
2. The answer adjudicator shall reach its decision without invoking any model and shall produce identical decisions for identical inputs on repeated runs.
3. Where a field is not designated critical and its answer is supported, carries valid evidence identifiers, and passed quality control, the answer adjudicator shall accept it without repair.
4. Where a field is designated critical, the answer adjudicator shall apply the configured stricter acceptance criteria and shall not accept it under the criteria used for non-critical fields.
5. If the two extractors disagree on a field's value, then the answer adjudicator shall require verification, repair, or manual review for that field and shall not accept either answer on agreement grounds alone.
6. If a field's cited evidence identifiers are invalid or its quote failed to match the cited evidence, then the answer adjudicator shall not accept the answer on the strength of agreement between agents.
7. If a field remains unresolved after verification and repair, then the answer adjudicator shall mark it for manual review.
8. When the answer adjudicator makes a decision, it shall record the decision rule applied, every input the rule consumed, and the provenance of those inputs, for every field.
9. When adjudication completes for a document, the answer adjudicator shall have produced exactly one decision record per field sent to extraction, including fields whose stages failed.

### Requirement 11: Answer Repair

**Objective:** As a reviewer, I want wrong or unresolved answers repaired with a targeted call rather than a full re-extraction, so that only the problematic fields consume additional model budget.

#### Acceptance Criteria

1. When a field is sent for repair, the multiagent extraction service shall supply the repair agent only that field's definition, its current answer, its quality-control issues, the verifier critique where one exists, and the evidence snippets for its adjudicated route.
2. When the repair agent responds, the multiagent extraction service shall require exactly one of the configured actions: revised, kept original, marked not reported, or manual review.
3. When the repair agent revises a field, the multiagent extraction service shall require a value, cited evidence identifiers, a short quote, a confidence label, and a rationale, and shall reject a revision missing any of them.
4. When the repair agent cannot resolve a field, the multiagent extraction service shall record the field as marked for manual review.
5. When repair completes for a field, the multiagent extraction service shall run extraction quality control again on the repaired answer before that answer is adjudicated.
6. When a repaired answer fails extraction quality control again, the multiagent extraction service shall mark the field for manual review rather than repairing it a second time beyond the configured repair attempt limit.
7. When a repair call completes, the multiagent extraction service shall persist its raw output and the final repair decision, and shall associate both with the field repaired.
8. The multiagent extraction service shall keep answer repair separate from the pipeline's existing malformed-output repair path and shall not route schema-invalid output into the answer repair agent.

### Requirement 12: Prompt Cache Stability, Prompt Versioning, and Call Accounting

**Objective:** As an operator paying per token, I want the new agents to preserve the existing prompt cache and to carry versioned prompts, so that adding agents neither multiplies cost nor makes recorded agreement numbers unattributable.

#### Acceptance Criteria

1. The multiagent extraction service shall not alter the shared paper prefix used by the existing extraction calls, and that prefix shall remain byte-identical across warmup, extraction chunks, and synthesis for the same document.
2. When route-derived material is added to a primary extraction request, the multiagent extraction service shall place it after the shared paper prefix and shall not place any of it inside that prefix.
3. When a second extraction, verification, or repair agent is invoked, the multiagent extraction service shall use that agent's own stable prefix and shall place all request-specific material after it.
4. When several requests are issued to the same agent for the same document, the multiagent extraction service shall keep that agent's prefix byte-identical across those requests.
5. The multiagent extraction service shall record a prompt template version for every agent request and shall carry that version on the request's recorded output.
6. If an agent's prompt template version changes, then the multiagent extraction service shall not reuse recorded outputs produced under the previous version when resuming a run.
7. When a run completes, the multiagent extraction service shall record the number of calls made by each agent, the fields they covered, and the gate rule that triggered each non-primary call.
8. The multiagent extraction service shall issue all model calls through the pipeline's existing single external call path, concurrency limits, and retry behavior rather than its own.

### Requirement 13: Decision Records and Raw Output Retention

**Objective:** As an institutional reviewer, I want each field's decision and every agent's raw output retained as queryable data, so that a disputed value can be reconstructed after the run.

#### Acceptance Criteria

1. When a field completes the stage sequence, the multiagent extraction service shall record a per-field decision record naming the stages that ran, the answers each produced, the quality-control issues, the verification verdict, the repair action, the final decision, and the decision rule.
2. The multiagent extraction service shall persist every agent's raw output unconditionally, independent of log level, and shall associate each raw output with the field and the stage that produced it.
3. If persisting a raw output or a decision record fails, then the multiagent extraction service shall record the persistence failure and shall continue the run rather than discarding the answers.
4. When a stage transforms, replaces, or discards an answer, the multiagent extraction service shall emit a derivation record naming the inputs, the outputs, the stage, and whether the stage was deterministic or model-driven.
5. The multiagent extraction service shall reference evidence using the project's single evidence node identity and shall not define a local identifier scheme for evidence.
6. Where the provenance subsystem is disabled or unavailable, the multiagent extraction service shall continue extracting and shall record that provenance emission did not occur.
7. The multiagent extraction service shall make decision records and stage outcomes available as queryable run data and shall not format, render, or export any audit report.

### Requirement 14: Configuration, Ablation, Failure Handling, and Resumability

**Objective:** As an operator, I want every agent independently controllable and every failure contained, so that one bad field or one bad document costs a field or a document rather than the run.

#### Acceptance Criteria

1. The multiagent extraction service shall expose in configuration the dual-extraction mode and its release thresholds, the random quality-assurance fraction and its seed, the per-agent model identities and retry limits, the quality-control thresholds including the quote match threshold, the issue-to-destination mapping, the verification gating thresholds, the per-document call bounds for each agent, the repair attempt limit, and the agreement escalation thresholds.
2. When configuration omits a setting, the multiagent extraction service shall apply a documented default and shall record the effective value in the run's record.
3. If a configured setting is invalid, then the multiagent extraction service shall report an error naming the setting and the invalid value and shall not start extraction for the run.
4. Where any individual agent is disabled in configuration, the multiagent extraction service shall run the remaining stages to completion, shall record that the agent was disabled, and shall not treat its absent output as a failure.
5. Where the whole feature is disabled in configuration, the pipeline shall run to completion using its existing single-extractor behavior and shall record that multiagent extraction was not performed.
6. If a stage fails for a single field, then the multiagent extraction service shall record the failure against that field, shall carry that field forward to adjudication with the outputs it does have, and shall continue processing the remaining fields.
7. If processing fails for an entire document, then the multiagent extraction service shall record the document as extraction-failed with the reason and shall continue processing the remaining documents.
8. When a run is resumed after an interruption, the multiagent extraction service shall reuse recorded answers, verdicts, and decisions for fields already completed rather than re-issuing the model calls that produced them.
9. If a recorded artifact was produced under a different extraction schema, a different document fingerprint, or a different prompt template version, then the multiagent extraction service shall discard it and re-run the affected stages rather than reusing it.
