# Implementation Plan

- [ ] 1. Foundation: configuration, shared types, and the evaluation output tree

- [ ] 1.1 Add the evaluation configuration section, register it, and implement strict resolution
  - Add a configuration section covering the feature enable flag, the evaluation stage, the reference-standard location and mapping path, the validation-grade minimum document and per-group unit counts, the per-field comparison-mode overrides, the numeric absolute and relative tolerances, the semantic token-overlap threshold and undetermined margin, the embedding screen's enablement, threshold, and model identity, the adjudication queue location, the ablation matrix membership, the sampling fraction and seed, the per-configuration and matrix cost ceilings, the baseline document size limit, the reviewer-ui action log location, the review-timing session idle gap, the manual-extraction baseline duration, and the export profile, output location, and sharing suitability
  - Register the new top-level key so the local configuration loader accepts it instead of rejecting the run
  - Resolve the section into an immutable settings object, applying a documented default for every omitted value and retaining the fully resolved mapping for later reporting
  - Reject an invalid value with an error naming the setting and the offending value before anything executes, including a baseline named in the matrix with no per-configuration ceiling declared
  - Default the feature to disabled and default sharing suitability to the most restrictive value
  - Observable: loading a configuration file with the new section returns a fully populated settings object whose resolved mapping lists every defaulted value; loading one with a sampling fraction above one, an unknown stage, or a baseline without a ceiling each raise an error naming that setting; loading one with an unknown sibling top-level key still raises the existing unknown-key error
  - _Requirements: 15.1, 15.2, 15.3_
  - _Boundary: EvaluationConfig_

- [ ] 1.2 Define the evaluation data model and closed vocabularies
  - Define frozen records for reference values and their presence state, row rejections, review-level declarations, validation grades, reference standards and import results, field comparisons, adjudication items, metric bounds, metric values and entries, the recalibration recommendation, the metric catalogue, derived review timing events, run identities, configuration descriptors, skipped documents, configuration results, deltas, configuration rows, and the aggregate evaluation result
  - Define the closed vocabularies for comparison mode, comparison outcome, mismatch kind, semantic tier, value presence, review level, evaluation stage, metric availability, unavailability reason, configuration kind, ablation target, baseline kind, sharing suitability, and configuration status
  - Make the metric value the only way a number leaves the feature, carrying availability, an unavailability reason, bounds, numerator, denominator, excluded count, and context
  - Import the agreement module's rater output and comparison unit types rather than redeclaring them, and redeclare no upstream extraction, routing, cost, or manifest type; in particular declare no review action record, action vocabulary, or review status vocabulary — those belong to `reviewer-ui`, and the derived timing record is defined against them
  - Include the two additional unavailability reasons for unreconstructable review timing and absent usability data in the closed vocabulary
  - Observable: a test constructs one instance of every record, asserts all are immutable with tuple collections, asserts no record or field name collides with an upstream type name, and asserts the metric value's required context keys are present
  - _Requirements: 1.2, 1.5, 2.1, 3.9, 4.9, 5.9, 6.1, 7.1, 10.4, 12.5, 13.1, 14.4_
  - _Boundary: EvaluationModels_

- [ ] 1.3 Add the evaluation output tree resolver and confined artifact writing
  - Add a resolver for the evaluation output location and for per-configuration subdirectories beneath it
  - Provide an atomic artifact write helper that leaves any previously valid artifact in place when a write fails and reports the failure naming the artifact
  - Add a guard that refuses any write resolving outside the evaluation output tree
  - Observable: writing an artifact twice replaces it atomically; a simulated write failure leaves the prior artifact readable and reports the artifact name; a write targeting the extraction output, cost artifact, run manifest, or audit artifact location is refused
  - _Requirements: 14.7, 15.8_
  - _Boundary: evaluation output tree resolver_

- [ ] 2. Reference standard: mapping, import, and review-level grading

- [ ] 2.1 (P) Implement the declared column-to-field mapping and its validation
  - Represent a mapping as an identity, a declared layout, a pinned schema version, and per-column bindings naming a role and, where applicable, a target field
  - Support both the one-column-per-field layout and the field-reference-plus-value layout, choosing between them from the declaration rather than by detection
  - Validate every target field against the pinned extraction schema version at load time and raise naming the offending column when one does not exist
  - Carry the not-reported token list and the declaration of whether the source contains patient-level data
  - Observable: a well-formed mapping loads and exposes its bindings; a mapping naming a field absent from the pinned version raises naming that column; a column absent from the mapping is reported as unmapped rather than inferred
  - _Requirements: 1.1, 1.4_
  - _Boundary: ReferenceMappingSpec_

- [ ] 2.2 Implement the reference-standard importer
  - Read a mapped source into reference values keyed by document and field, deriving a stable value identity so a repeat import updates rather than duplicates the stored standard
  - Distinguish an asserted value, an explicit not-reported marker, an empty value, and an absent row as four separate states
  - Resolve document references by identifier, original filename, or content-hash prefix, rejecting an ambiguous filename as a row rejection rather than guessing
  - Reject rows naming a document outside the evaluated corpus or a field outside the pinned schema version with a named reason, and continue importing the remaining rows
  - Abort the whole import before reading any row when the mapping declares the source to contain patient-level data, naming that declaration as the reason
  - Report accepted rows, rejected rows, unmapped columns, covered documents, covered fields, and the rejection list, and record the source identity, timestamp, schema version, and mapping used on the stored standard
  - Observable: importing a fixture with one unknown document row and one unknown field row yields two rejections with reasons and imports the rest; importing the same fixture twice yields identical value identities and one stored standard; a mapping declaring patient data aborts before any row is read
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8_
  - _Boundary: ReferenceStandardImporter_

- [ ] 2.3 Implement review-level enforcement and validation grading
  - Require a declared review level at registration and reject a standard that declares none
  - Require the annotator count, both blinding states, the adjudication rule, and the adjudicated document and per-group unit counts whenever the dual-annotator-adjudicated level is declared, rejecting an incomplete declaration
  - Grade a standard as validation-grade only when the level is dual-annotator-adjudicated, both blinding states hold, the adjudicated document count meets the configured minimum, and the field group's adjudicated unit count meets the configured minimum, listing every group that falls short
  - Expose a gate requiring the human-human agreement result to be recorded before any system comparison result is released at the dual-annotator-adjudicated level
  - Expose the labelling vocabulary so results are described as agreement with the reference standard and never as correct or verified
  - Observable: a declaration with no level and an incomplete dual-annotator declaration each raise; a dual-annotator standard below the document minimum grades as not validation-grade naming the reason; a standard built with visibility of system output grades as not validation-grade; single-annotator and imported-benchmark standards never grade as validation-grade
  - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 2.7_
  - _Boundary: ReviewLevelPolicy_

- [ ] 3. Comparison engine: modes, the semantic cascade, and per-pair comparison

- [ ] 3.1 (P) Define the comparison-mode contract and its registry
  - Define an abstract comparison mode taking a comparison input and returning a verdict carrying the outcome, the mismatch kind, the score, the deciding tier, the threshold in effect, and the resolver identity
  - Provide a per-run registry that resolves a mode by name and accepts a new mode without any change to an existing one
  - Mark each mode as deterministic or not, so the determinism guarantee can be asserted per mode
  - Observable: registering a fresh mode and resolving it by name succeeds without editing any built-in mode; resolving an unknown mode name raises naming it
  - _Requirements: 3.2_
  - _Boundary: ComparisonModeRegistry_

- [ ] 3.2 (P) Implement the five deterministic built-in comparison modes
  - Implement raw-string comparison and normalization-ladder comparison, reusing the project's existing whitespace and aggressive normalizers and reporting which pass matched as the score
  - Implement numeric-tolerance comparison that matches only within the configured absolute or relative tolerance and reports a distinct unparseable-numeric mismatch when either side carries no parseable numeric content
  - Implement categorical comparison against the field's declared category set, distinguishing an out-of-vocabulary value from a wrong-category value
  - Implement not-reported comparison that treats mutual absence as a match and distinguishes an asserted value against a not-reported reference from a not-reported answer against an asserted reference
  - Keep every mode pure and total, performing no input or output, reading no clock, and using no randomness
  - Observable: a table-driven test covers each mode's match, each of its distinct mismatch kinds, and a repeat call returning an identical verdict
  - _Requirements: 3.4, 3.5, 3.6, 3.8_
  - _Boundary: BuiltinComparisonModes_

- [ ] 3.3 (P) Implement the tiered semantic equivalence resolver
  - Attempt normalization equality first and record a match at that tier when it succeeds
  - Parse numeric tokens from both sides and record an immediate mismatch when the numeric token multisets differ, above and independent of every similarity score
  - Compute symmetric content-token overlap after stopword and punctuation removal and record a match only at or above the configured threshold
  - Apply the optional embedding screen only when it is enabled, only when the overlap score is within the configured margin of the threshold, and only to move a pair into the undetermined band, never to record a match
  - Never apply the screen to a field designated critical, and refer every unsettled semantic decision on a critical field straight to adjudication
  - Import the embedding path lazily inside the method body and make no external model call of any kind, recording the resolver version and, when the screen ran, the embedding model identity
  - Observable: two values differing only in a number mismatch despite a near-total overlap score; with the screen disabled the embedding module is never imported and the resolver never returns undetermined from the screen; with the screen enabled a near-threshold pair returns undetermined and never a match; a critical field returns the critical-referral tier; a test patching the provider client module asserts it is never touched
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - _Boundary: SemanticEquivalenceResolver_

- [ ] 3.4 Implement the adjudication queue
  - Store undetermined pairs keyed by reference standard, document, and field, carrying both values and the tier that produced the band, updating an existing entry rather than appending a second
  - Apply a supplied human result to an item, recording the adjudicator identity and timestamp and moving the item out of the outstanding set while retaining it
  - Expose the outstanding items and the adjudicated results as reads only, with no prompt, editor, or server of any kind
  - Observable: enqueuing the same pair twice yields one entry; applying a result marks the item adjudicated with its adjudicator and timestamp and removes it from the outstanding set while it remains readable; the module exposes no input or server function
  - _Requirements: 4.7, 4.8_
  - _Boundary: AdjudicationQueue_

- [ ] 3.5 Implement the field comparison engine
  - Assign a comparison mode per field from an explicit configuration override where one exists and otherwise from the extraction schema's declared field format, and record the assigned mode on every comparison
  - Produce exactly one comparison per document-and-field pair in the union of reference-covered and system-covered pairs, recording a one-sided pair as reference-only or system-only and never as a match
  - Attach the upstream-read evidence-support state, terminal state, parser-risk state, and criticality to each comparison without recomputing any of them
  - Refuse to compare a reference standard and a system output whose pinned schema versions differ, reporting the pairing as incomparable with a named reason
  - Observable: comparing fixture inputs produces one record per union pair with the mode and the deciding score recorded on each; a repeat comparison over the same inputs is byte-identical for every deterministic mode; a schema-version mismatch yields an incomparable result rather than comparisons
  - _Requirements: 3.1, 3.3, 3.7, 3.8, 3.9_
  - _Boundary: FieldComparisonEngine_
  - _Depends: 3.1, 3.2, 3.3, 3.4_

- [ ] 4. Metrics: accuracy, consumed artifacts, agreement, and the catalogue

- [ ] 4.1 (P) Implement the accuracy and completeness metrics with bounds
  - Compute field-level agreement with the reference standard overall, per field, and per field group, each carrying its numerator, denominator, and excluded count
  - Compute completeness over reference-covered pairs, not-reported agreement separately from asserted-value agreement, and critical-field agreement over schema-designated critical fields
  - Report an adjudicated point estimate together with a lower bound counting undetermined pairs as mismatches and an upper bound counting them as matches, plus the undetermined count, collapsing to one number when no undetermined pairs exist
  - Report a metric whose denominator is zero or whose inputs are absent as unavailable with a named reason rather than as zero
  - Record the reference standard identity, configuration identity, mode assignment, thresholds, and counts on every computed metric
  - Observable: a fixture comparison set yields overall, per-field, and per-group metrics whose counts sum correctly; adding undetermined pairs spreads the bounds and leaves the point estimate unchanged; an empty field group reports unavailable with the zero-denominator reason rather than a zero
  - _Requirements: 4.9, 5.1, 5.2, 5.5, 5.6, 5.8, 5.9_
  - _Boundary: AccuracyMetricsCalculator_

- [ ] 4.2 (P) Implement the consumed-artifact views, run identity, and the comparability key
  - Implement a cost view reading the published run cost artifact for a configuration's output location and exposing total cost, per-document cost, elapsed time, and per-stage shares without performing any price arithmetic or token counting
  - Implement an audit-completeness view, a parser-risk view resolving page-level risk to fields through the adjudicated route, and a route-quality view exposing route verdicts, evidence-support state, criticality, and terminal states
  - Give each view a declared absent path returning a named unavailability reason rather than raising, and let no view write anything
  - Read the run identity from the published run manifest without re-deriving it, and derive a comparability key from the schema version, sorted prompt versions, sorted sampled document identifiers, and price-table version
  - Observable: each view returns unavailable with its own named reason when its artifact is missing and returns a populated payload when a fixture artifact is present; two identical identities produce the same comparability key and any single differing input produces a different one
  - _Requirements: 6.3, 6.4, 6.5, 6.6, 6.8, 15.6, 15.7_
  - _Boundary: ConsumedArtifactViews_

- [ ] 4.3 (P) Implement the human-versus-system agreement adapter
  - Emit one rater output per rater per document-and-field pair using the declared human-reference and system rater names, and emit the two human annotator raters for the human-human comparison
  - Populate only the fields the agreement module's input contract declares, adding none and renaming none
  - Normalize the rater outputs into comparison units through the agreement package's normalizer and call the agreement module's public `compute_agreement(units, config)` entry point directly, copying the returned mapping into a metric entry; there is no published human-versus-system agreement artifact to read, so waiting for one would leave this metric permanently unavailable
  - Where a published agreement result does exist (the agent-versus-agent case), read it rather than recompute it
  - Compute no percent agreement, no chance-corrected statistic, and no stratification in this module, implement no agreement statistic, and never override an undefined reason code returned by the agreement module
  - Import the agreement entry point inside the function body, and report the entry as unavailable with the agreement-undefined reason when the entry point is unavailable or the rater outputs cannot be formed
  - Observable: emitted rater outputs contain only contract fields and use the declared rater names; with the agreement entry point patched, the adapter calls it once and copies its mapping into a metric entry unchanged; with the entry point unimportable the result is unavailable with the agreement-undefined reason rather than an error; a guard test asserts no statistic is computed in this module and that the agreement modules reached import nothing from `pipeline`, `agents`, `pdf_extractor`, or `evaluation`
  - _Requirements: 2.3, 6.2_
  - _Boundary: HumanSystemAgreementAdapter_

- [ ] 4.4 Compute the support-derived and review-rate metrics from upstream state
  - Compute the unsupported-answer rate from the evidence-support state recorded upstream, without re-running any evidence check
  - Compute evidence-support agreement over accepted fields only and state the acceptance criterion the denominator used in the metric's context
  - Compute the manual-review rate by field group from the terminal states recorded upstream
  - Report each of these as unavailable with the route-traces-absent reason when the upstream state does not exist
  - Observable: a fixture carrying upstream support and terminal states yields the three metrics with correct denominators and a stated acceptance criterion; removing the upstream state flips all three to unavailable with the same named reason
  - _Requirements: 5.3, 5.4, 5.7_
  - _Boundary: AccuracyMetricsCalculator, ConsumedArtifactViews_
  - _Depends: 4.1, 4.2_

- [ ] 4.5 Build the twelve-entry metric catalogue and the recalibration recommendation
  - Assemble exactly twelve entries in metric-number order regardless of what is available, each carrying its value, its availability, and a named reason when absent
  - Map each metric to its source and mark reference-standard-dependent entries as not applicable to the stage rather than unavailable when the stage does not admit them
  - Stratify agreement by the published parser-risk state and emit a recalibration recommendation naming the observed thresholds and the agreement difference between strata, without writing any threshold
  - Use agreement wording throughout so no metric name contains a correctness or verification claim
  - Observable: running the builder with every input absent still yields exactly twelve entries, each unavailable with the correct named reason; supplying each input in turn flips exactly one entry to available; the recalibration recommendation appears only when stratification is available and changes no configured threshold
  - _Requirements: 2.7, 5.8, 6.1, 6.7_
  - _Boundary: MetricCatalogueBuilder_
  - _Depends: 4.1, 4.2, 4.3, 4.4_

- [ ] 5. Review instrumentation hooks

- [ ] 5.1 Derive the review timing contract from the reviewer-ui action log and implement its recording sink
  - Declare `ReviewTimingEvent` as a **derived** record covering the run, configuration, document, field, derived session, reviewer, reconstructed start and recorded end, timing basis, duration, decision projection, source action kind and identifier, the two values copied from reviewer-ui's per-field projection (`projected_original_model_value` and `projected_current_value`), and any side-channel usability rating; define no action vocabulary and no log, and reconstruct no before/after value from the action log — `reviewer-ui` owns `ReviewEvent`, the `ActionKind` vocabulary, and the append-only log
  - Implement the adapter that reads reviewer-ui action records, filters to field targets, groups by reviewer and document, orders by the log's `sequence`, derives sessions by the configured idle gap, reconstructs each interval from the preceding action in the same session, and records the timing basis; map `field_index` to `field_id` through the pinned schema version, dropping, counting, and warning about anything unresolvable
  - Apply the `ActionKind` → decision projection table, treating an unmapped action kind as a hard error naming it and excluding non-decision and queue-target actions from correction-burden denominators
  - Copy `current_value` and `original_model_value` verbatim from reviewer-ui's optional per-field projection onto each derived event; when no projection is supplied leave both `None` and count the field out of the correction-burden denominator, never inferring a value change from an `edit` action
  - Validate derived events and store them keyed by run, configuration, document, field, and source action identifier, updating rather than duplicating on a repeated key
  - Produce no review action internally, expose no review, annotation, or adjudication surface, and accept action records only as supplied data
  - Emit a standing note into the evaluation output stating that the prospective human-in-the-loop evaluation is not executable until reviewer-ui lands and review actions have been recorded
  - Observable: a fixture reviewer-ui action log yields one timing event per field action with `field_index` mapped to `field_id`; a session's first action carries the session-boundary basis and a null duration; a gap over the idle threshold starts a new session; a queue-target action is excluded; an unknown action kind raises naming it; an unresolvable `field_index` is dropped, counted, and warned about; replaying the same log twice yields the same event set; a guard test asserts the module exposes no server, prompt, or input function; the standing note appears in the evaluation result's warnings
  - _Requirements: 7.1, 7.2, 7.5, 7.6_
  - _Boundary: ReviewActionLogAdapter, ReviewTimingSink_

- [ ] 5.2 Compute the correction-burden and time-saved metrics from derived review timing events
  - Compute human correction burden as the proportion and count of reviewed fields whose `projected_current_value` differs from `projected_original_model_value` — both copied from reviewer-ui's per-field projection by the adapter and never recomputed, and never derived from action-log before/after reconstruction
  - Compute time saved as the declared manual-extraction baseline minus the reconstructed review duration, per field and aggregated, excluding events with no reconstructable duration from the denominator and carrying the covered and total counts as bounds
  - Report both as unavailable with the review-surface-absent reason, and never as zero, when a run carries no action log; report time saved as unavailable with the timing-unreconstructable reason when no duration can be reconstructed; report the usability component as unavailable with the usability-data-absent reason when no ratings are supplied
  - Observable: a fixture timing-event list carrying projected values yields a correction burden matching a hand-computed count and a time-saved value matching the declared baseline arithmetic; a list of only session-boundary events yields correction burden available and time saved unavailable with the timing-unreconstructable reason; events with no projected values yield correction burden unavailable rather than a burden inferred from decisions; an empty list yields both metrics unavailable with the review-surface-absent reason rather than zero
  - _Requirements: 7.3, 7.4_
  - _Boundary: AccuracyMetricsCalculator, ReviewTimingSink_
  - _Depends: 5.1, 4.1_

- [ ] 6. Evaluation stage modes

- [ ] 6.1 Implement the Stage 1 system-validation mode
  - Evaluate without a reference standard, reporting parser outcomes, route quality, evidence support, audit completeness, and the ablation comparison by reading them through the consumed views and computing none of them
  - Mark every reference-standard-dependent catalogue entry as not applicable to this stage rather than unavailable
  - Mark an outcome as not applicable naming the disabled stage when the stage it depends on is disabled in the evaluated configuration, and continue
  - Observable: Stage 1 completes with no reference standard registered and produces a twelve-entry catalogue in which reference-dependent entries are not applicable; disabling an upstream stage in a fixture configuration marks the dependent outcome not applicable naming that stage
  - _Requirements: 8.1, 8.2, 8.3, 8.5_
  - _Boundary: Stage1Evaluator_
  - _Depends: 4.5_

- [ ] 6.2 Implement the Stage 2 benchmark mode
  - Require a registered reference standard and report an error naming the missing standard when none exists, producing no Stage 2 results
  - Report field-level agreement, evidence support, human-versus-system agreement, and final human-verified extraction quality where the corresponding inputs exist
  - Keep the retrospective and prospective halves as separate result blocks that are never merged into one figure
  - Complete the retrospective half and mark the prospective half blocked with a named reason when no reviewer-ui action log exists, without failing the evaluation
  - Observable: Stage 2 without a registered standard raises naming it; Stage 2 with a fixture standard and no reviewer-ui action log completes and reports the prospective half blocked; the two halves appear as separate blocks in the result
  - _Requirements: 9.1, 9.2, 9.3, 9.5_
  - _Boundary: Stage2Evaluator_
  - _Depends: 3.5, 4.5, 5.1_

- [ ] 7. Ablation execution

- [ ] 7.1 Implement the ablation matrix, its overlays, and configuration identity
  - Define the built-in matrix members covering the full system, each of the four individually disabled agents, and the two full-document baselines, keeping both baselines out of the default matrix
  - Express each member as an overlay of declared configuration keys only, naming no code path
  - Validate every overlay key against the loaded configuration surface before execution and raise naming the switch when one is unrecognised, executing no configuration
  - Derive a stable configuration identity from the canonically serialized overlay and assign each configuration its own output directory beneath the evaluation tree
  - Allow the matrix to be restricted to a subset from configuration without redefining it
  - Observable: each built-in member produces the expected overlay of declared keys; the same overlay yields the same identity across constructions; an unrecognised switch raises naming it; the default matrix contains neither baseline; a restricted matrix yields only the requested members
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 11.3_
  - _Boundary: AblationMatrix_

- [ ] 7.2 Add the package-builder seam to the document processing entry point
  - Add one optional `evidence_package_builder` parameter to the per-document processing entry point, defaulting to the current behaviour; the name must not be shortened to `package_builder`, which would collide with the unrelated `AuditPackageBuilder` injected into the same call path by `provenance-audit-export` and `cost-and-run-reporting`
  - Build the paper evidence package through the supplied builder when one is given and through the existing path otherwise
  - Leave the shared paper prefix construction, the cache warmup message, and the token budget enforcement untouched
  - Make no other change to the pipeline in this spec; this is the only pipeline modification this spec makes, and other specs' pipeline modifications are out of its scope rather than forbidden by it
  - Observable: a regression test asserts that with no builder supplied the serialized paper package bytes are byte-identical to the pre-change baseline; with a stub builder supplied the stub's output is used and is byte-identical across every request for that document; the parameter is named `evidence_package_builder` and no other parameter of the entry point shares that role
  - _Requirements: 11.5_
  - _Boundary: document processing entry point evidence-package seam_

- [ ] 7.3 Implement the full-document baseline package builder
  - Build one paper evidence package per document from the full document text rather than from the ranked evidence bundle, once per document and reused across every request for that document, supplied through the `evidence_package_builder` seam
  - Provide the one-shot variant requesting every field in a single chunk and the chunked variant keeping the existing chunk structure over the same full-text package
  - Skip a document whose estimated package size exceeds the configured limit, recording the skip and its reason, and read the existing budget thresholds to make that decision without altering or re-deriving them
  - Observable: the same document, text, and prefilled fields yield byte-identical package output on repeat; an oversized fixture document yields no package and a recorded skip reason; a test asserts no budget threshold value is written or recomputed
  - _Requirements: 11.1, 11.2, 11.5, 11.6, 11.8_
  - _Boundary: BaselinePackageBuilder_
  - _Depends: 7.2_

- [ ] 7.4 Implement matrix execution with deterministic sampling and output isolation
  - Execute each configuration by invoking the existing pipeline entry point with the overlay applied and the configuration's own output directory, implementing no second extraction path
  - Select the sampled documents deterministically from the recorded seed and sampling fraction, use the same sample for every configuration in the matrix, and record the sample membership on every result
  - Record the fully resolved effective configuration and the run identity on each configuration result
  - Observable: a stub pipeline entry point records the overlay and output directory it received per configuration; a fixed seed yields the same sample across configurations and across repeat runs; each result carries its effective configuration and its own output directory
  - _Requirements: 10.5, 10.7, 12.1, 12.2_
  - _Boundary: AblationRunner_
  - _Depends: 7.1, 4.2_

- [ ] 7.5 Enforce cost ceilings and isolate document and configuration failures
  - Refuse to start a baseline configuration for which no per-configuration cost ceiling is declared
  - Evaluate the per-configuration and matrix ceilings against the published run cost artifact after each configuration and, where the artifact updates during a run, between documents
  - Stop starting new work in the affected scope on reaching a ceiling, record which configurations completed and which were not attempted, and return the partial matrix rather than discarding it
  - Record a failed document as skipped for that configuration and exclude it from that configuration's denominators, and record a failed configuration with a reason while the matrix continues
  - Report the excluded and skipped document counts alongside every configuration's results
  - Observable: a baseline without a ceiling is refused before execution; a fixture cost artifact exceeding the per-configuration ceiling marks that configuration cost-ceiling-reached while the matrix still reports; a failing fixture document is excluded from denominators and the configuration completes; a failing configuration keeps its record and the next configuration runs
  - _Requirements: 11.4, 11.7, 12.3, 12.4, 12.5, 12.6_
  - _Boundary: AblationRunner_
  - _Depends: 7.4_

- [ ] 7.6 Implement matrix resumability and identity-based invalidation
  - Reuse a recorded configuration result when its configuration identity, schema version, prompt versions, and sampled document set all still match
  - Discard a recorded result and re-execute the configuration when any of those identities differ
  - Observable: resuming an interrupted matrix skips the already-completed configuration without invoking the pipeline entry point; changing the schema version, a prompt version, or the sample in the fixture forces that configuration to re-execute
  - _Requirements: 12.7, 12.8_
  - _Boundary: AblationRunner_
  - _Depends: 7.4_

- [ ] 8. Reporting and manuscript export

- [ ] 8.1 Implement the per-configuration comparison report
  - Produce one row per configuration carrying agreement, evidence support, cost, runtime, and manual-review rate, plus the configuration identity, its switch settings, and the attempted, completed, and excluded document counts
  - Compute each ablated configuration's difference from the full-system configuration for every reported metric, naming the baseline configuration on each difference
  - Keep an incomplete configuration's row, marked with its status and recorded reason, rather than omitting it
  - Emit a difference only when both rows' comparability keys match, and otherwise mark the pair incomparable with a named reason and carry no value
  - Attach no significance value, confidence interval, or hypothesis-test result anywhere, and record the review level and validation grade on every row that depended on a reference standard
  - Observable: a fixture matrix yields one row per configuration including the failed one; differences appear only between comparable rows and an incomparable pair carries a reason and no value; a lexical test asserts no report key contains a significance, p-value, or confidence-interval term
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_
  - _Boundary: ConfigurationComparisonReporter_
  - _Depends: 7.5, 2.3_

- [ ] 8.2 Implement the manuscript exporter with its two profiles
  - Produce the metric catalogue, the per-configuration comparison, and the per-field accuracy breakdown as machine-readable artifacts
  - Produce the Stage 1 table set as the default profile and the Stage 2 table set only when that profile is requested
  - Keep the per-field breakdown's leading columns identical to the existing per-field flag export's row shape so both are readable by one tool
  - Stamp every artifact with the run identities, configuration identities, extraction schema version, prompt versions, reference-standard identity, review level and validation grade, and the harness version, marking any artifact below the dual-annotator-adjudicated level as not validation-grade
  - Carry an unavailable, undefined, or bounded metric state into the export rather than substituting a number, exclude document text, evidence text, reference values, and reviewer identities from any artifact marked shareable, and default sharing suitability to the most restrictive value
  - Observable: a default export produces only the Stage 1 artifacts and a requested Stage 2 export adds the second profile; every artifact carries a complete stamp; an unavailable metric survives export as unavailable; a shareable artifact contains no reference value string; a simulated write failure names the artifact and leaves the prior artifact intact
  - _Requirements: 2.6, 8.4, 9.4, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_
  - _Boundary: ManuscriptExporter_
  - _Depends: 8.1, 1.3_

- [ ] 9. Orchestration and entry points

- [ ] 9.1 Implement the evaluation harness service and its disabled path
  - Sequence configuration resolution, matrix construction and validation, sample selection, execution, comparison, catalogue construction, reporting, and export
  - Return immediately and write nothing when the feature is disabled, leaving the pipeline's behaviour exactly as it is today
  - Apply only the declared configuration switches of the configuration under evaluation and touch no extraction, routing, verification, repair, or quality-control behaviour
  - Observable: with the feature disabled a full pipeline fixture run produces byte-identical outputs to a run without the feature and no evaluation artifact appears; with it enabled the sequence completes and produces the expected artifacts; a boundary test asserts no evaluation module calls a stage function other than the pipeline entry point
  - _Requirements: 15.4, 15.5_
  - _Boundary: EvaluationHarnessService_
  - _Depends: 6.1, 6.2, 8.2_

- [ ] 9.2 Add the evaluation command-line entry point
  - Expose a reference-import subcommand taking a source, a mapping, and a review-level declaration and reporting the import counts
  - Expose a run subcommand taking a stage, an optional matrix subset, a sample setting, and ceilings
  - Expose an export subcommand taking a profile and an output location
  - Observable: each subcommand runs against a fixture project and prints its counts or artifact paths; an unknown matrix member, a missing baseline ceiling, and a missing reference standard in Stage 2 each exit with an error naming the cause
  - _Requirements: 1.7, 10.6, 14.2_
  - _Boundary: evaluation CLI_
  - _Depends: 9.1_

- [ ] 10. Validation

- [ ] 10.1 Add the boundary and dependency-direction tests
  - Assert that no module under the pipeline, agents, quality-control, text-processing, or PDF-extractor packages imports the evaluation package
  - Assert that no evaluation module computes an agreement statistic, a price, a token count, or a parser-risk flag; calling the agreement module's public entry point is permitted and implementing a statistic is not
  - Assert that the agreement modules the evaluation package is permitted to import stay leaf-pure — importing nothing from the pipeline, agents, PDF-extractor, or evaluation packages — so the widened allowlist adds only the permitted evaluation-to-quality-control edge
  - Assert that no evaluation module declares a review action record, an action-kind vocabulary, or a review status vocabulary, all of which belong to the reviewer surface
  - Assert that every evaluation write resolves under the evaluation output tree and that no extraction output, cost artifact, run manifest, or audit artifact is overwritten
  - Observable: the boundary suite fails when a deliberately added reverse import, statistic computation, or out-of-tree write is introduced, and passes on the shipped tree
  - _Requirements: 6.2, 15.5, 15.8_
  - _Boundary: evaluation boundary tests_
  - _Depends: 9.1_

- [ ] 10.2 (P) Add the determinism and claim-restraint contract tests
  - Assert that every deterministic comparison mode and the semantic cascade in its default configuration return identical verdicts on repeat over a generated input set
  - Assert that no report or export key, header, or metric name contains a significance, p-value, or confidence-interval term, or the words correct or verified
  - Assert that with the embedding screen disabled no heavy optional dependency module is imported anywhere in the fast suite
  - Observable: the contract suite passes on the shipped tree and fails when a nondeterministic comparison, a significance key, or a top-level heavy import is introduced
  - _Requirements: 2.7, 3.8, 13.6_
  - _Boundary: evaluation contract tests_
  - _Depends: 3.5, 8.2_

- [ ] 10.3 Add the end-to-end fixture evaluation for both stages
  - Run a Stage 1 evaluation end to end over a fixture corpus with a stub pipeline entry point, asserting the twelve-entry catalogue, the per-configuration report, and the default export profile
  - Run a Stage 2 evaluation end to end over the same fixture plus a Level C reference standard and a review-event list, asserting the retrospective and prospective blocks, the bounded metrics, and the opt-in export profile
  - Assert that the fixture corpus, reference standard, and derived review timing events contain no patient-level data
  - Observable: both end-to-end runs complete and produce stamped artifacts whose metric availabilities match the fixtures supplied; removing the reference standard turns the Stage 2 run into a named error and leaves the Stage 1 run unaffected
  - _Requirements: 8.1, 9.1, 14.1, 14.2_
  - _Boundary: evaluation end-to-end tests_
  - _Depends: 10.1, 10.2_
