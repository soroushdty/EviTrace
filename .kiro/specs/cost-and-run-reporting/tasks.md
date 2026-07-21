# Implementation Plan: cost-and-run-reporting

## Overview

The work proceeds bottom-up: configuration and artifact locations first, then the measurement layer inside the OpenAI client, then the three independent pipeline capabilities (stage control, pricing, run recording), then the two reporting artifacts, then resume persistence and per-output provenance, then orchestrator wiring, and finally the cross-cutting guards that prove nothing regressed.

Two hard constraints bind every task: the OpenAI client must never import from the pipeline package (so the price table can never reach it), and the cached shared prompt prefix must remain byte-identical. Both are asserted by tasks in the final group.

## Tasks

- [ ] 1. Foundation: configuration surface and artifact locations

- [ ] 1.1 Add the pricing, stage-switch, and run-reporting configuration sections
  - Add the three new top-level configuration sections covering model prices per token class with a currency, version, effective date, and staleness horizon; per-stage enable switches; and run-reporting toggles including the completed-output overwrite setting
  - Register all three new top-level keys with the configuration loader's known-key set so loading does not reject them
  - Add a loader that resolves these sections with environment-over-file-over-default precedence, including the comma-separated disabled-stage list and the boolean overwrite override, reusing the existing boolean parsing convention
  - Ship prices as zero placeholders so the repository asserts no provider price it cannot keep current
  - Observable: loading the configuration with the three new sections present succeeds and returns the resolved pricing, stage, and run-reporting settings; the corresponding environment variables override the file values
  - Do not touch the existing cache-diagnostics configuration section; it already exists and is already registered, and only its reader is unwired (handled in 6.1)
  - _Requirements: 3.1, 3.2, 3.3, 7.1, 10.3_
  - _Boundary: config_utils_

- [ ] 1.2 Declare run-scoped locations for the three new artifacts
  - Add run-scoped path constants for the cost report, run manifest, and run audit log alongside the existing artifact path constants
  - Observable: the three constants resolve inside the current run output folder and sit beside the existing flagged-fields and token-report locations
  - _Requirements: 5.1, 11.4_
  - _Boundary: path_utils_

- [ ] 2. Measurement layer: per-call facts and failure accounting

- [ ] 2.1 Extend the per-call telemetry record with document, timing, attempt, and outcome facts
  - Add optional fields for document identifier, call duration, attempt number, total attempts, outcome, error class, and truncated error detail, each with a default so every existing construction site keeps working
  - Define the run-level and unknown document sentinels and the closed outcome vocabulary
  - Add a reliability aggregation that reports failed attempts, calls that succeeded after at least one retry, and terminally failed calls, per stage and for the run, with run-level counts equal to the sum of the per-stage counts
  - Keep every existing field, its order, and all existing aggregation behavior untouched
  - Observable: a record constructed with only the pre-existing arguments still validates, and the reliability aggregation returns per-stage plus one run-level entry for a mixed set of success and failure records
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5_
  - _Boundary: telemetry_

- [ ] 2.2 Time every model attempt and record successes, failed attempts, and terminal failures
  - Measure elapsed wall-clock time around each awaited model call in both retry paths and attach it to the recorded outcome
  - Forward the already-available paper name as the document identifier, substituting the run-level sentinel when it is absent or unknown
  - Record a failure entry for every failed attempt in both retry loops, carrying stage, model, document, attempt number, duration, error class, and a truncated error summary with credential-like tokens elided; record a terminal-failure entry carrying the total attempt count when retries are exhausted
  - Count tokens reported by a failed attempt, and record zero counts when a failed attempt reports none
  - Keep the recording helpers non-raising so a telemetry defect can never fail an extraction, and keep the existing retry and raise semantics unchanged
  - Observable: a mocked client that fails twice then succeeds yields exactly two failed-attempt entries and one success entry with attempt three, all carrying a document identifier and a duration
  - _Requirements: 1.1, 1.2, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4_
  - _Boundary: api_client_

- [ ] 2.3 Test the extended telemetry and failure accounting
  - Cover defaulting of every new field, the outcome invariants, and the reliability counts
  - Cover an always-failing mocked client producing one entry per attempt plus a terminal-failure entry with the total attempt count
  - Cover degradation when usage, duration, or document identity cannot be determined: a warning is logged, the field is marked unknown, and processing continues
  - Observable: the new test modules pass and the existing telemetry test modules pass unmodified
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5_
  - _Boundary: telemetry, api_client_

- [ ] 3. Pipeline capabilities: stage control, pricing, run recording

- [ ] 3.1 (P) Implement the stage registry and enable/disable resolution
  - Declare the required stages, the optional stages, and the documented stage ordering used by all reporting
  - Resolve the operator's switches into a disabled set, refusing any request to disable a required stage by dropping it and logging an error naming the stage
  - Treat the existing cache-prewarm setting as authoritative for the warmup stage and combine it with the new switch rather than competing with it, so an unmodified configuration behaves exactly as before
  - Accept unknown stage names so future stages need no change here
  - Observable: disabling an optional stage is reflected in the resolved disabled set, disabling a required stage is refused with an error log, and an empty configuration yields today's behavior
  - _Requirements: 7.1, 7.2, 7.5, 7.6_
  - _Boundary: stage_control_
  - _Depends: 1.1_

- [ ] 3.2 (P) Implement the price table and per-call cost estimation
  - Load the price table into an immutable structure carrying its version, currency, effective date, unit, and availability, plus the set of models rejected as invalid
  - Compute a per-call estimate by applying the per-token-class unit prices to cached input, uncached input, and output token counts using exact decimal arithmetic, rounded at the call level to a documented precision so aggregate sums are exact
  - Degrade rather than raise: an unpriced model yields an unavailable zero estimate and is collected for reporting; an absent, empty, or unreadable table marks the whole stamp unavailable; a missing, non-numeric, or negative unit price removes that model and logs a warning naming the model and the offending field
  - Mark the table stale when its effective date is older than the configured horizon, and also when the date cannot be parsed, logging the date and horizon
  - Record the version, currency, effective date, and staleness marker on the stamp for stamping onto artifacts
  - Observable: a populated table produces non-zero estimates whose three token-class components sum exactly to the call total; each degradation path produces a warning and a usable table rather than an exception
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 4.1_
  - _Boundary: pricing_
  - _Depends: 1.1_

- [ ] 3.3 (P) Implement the run recorder for stage spans and audit events
  - Provide a run-scoped, thread-safe recorder holding the run identifier, start time, and a strictly increasing sequence of lifecycle events for stage start, completion, skip, rerun, and failure
  - Provide a stage span that emits a start event on entry and a completion event on normal exit, and a failure event carrying the exception class before re-raising
  - Provide skip recording restricted to the closed reason vocabulary of disabled-by-configuration, already-complete, not-applicable, and prerequisite-unavailable, plus rerun recording carrying an attempt number and reason
  - Derive per-stage and per-document elapsed time from matched start/completion pairs, leaving unmatched starts as incomplete spans that contribute no duration
  - Restrict event payloads to identifiers, stage names, outcomes, reasons, and counts, excluding document text, extracted values, and secrets
  - Write the audit log atomically at end of run, ordered by sequence, returning nothing and logging an error on write failure; a failed event append logs a warning and lets the run continue
  - Observable: a recorder exercised with nested spans, a raising span, skips, and reruns produces a strictly ordered event list and correct per-stage durations, and writes a parseable audit-log file
  - _Requirements: 4.5, 4.6, 7.3, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 12.5_
  - _Boundary: run_recorder_
  - _Depends: 1.2_

- [ ] 3.4 Test stage control, pricing, and run recording
  - Cover the required-stage refusal, the optional-stage switch, the prewarm interaction, and unknown stage acceptance
  - Cover price arithmetic, unpriced models, the three table-defect paths, and both staleness triggers
  - Cover span emission, exception handling inside a span, sequence monotonicity, the closed skip vocabulary, payload restriction, and non-fatal append and write failures
  - Observable: the three new test modules pass and report the behaviors above without touching the network or the filesystem outside a temporary directory
  - _Requirements: 3.4, 3.5, 3.6, 3.7, 7.1, 7.2, 7.5, 11.1, 11.2, 11.3, 11.5, 11.6, 11.7_
  - _Boundary: stage_control, pricing, run_recorder_

- [ ] 4. Reporting artifacts

- [ ] 4.1 Aggregate telemetry and stage spans into the cost projection
  - Fold priced telemetry records into per-call lines and into per-document, per-stage, per-stage-within-document, and run-level totals covering estimated cost and all four token classes
  - Report document count, mean estimated cost per document, and each stage's share of the run total
  - Take elapsed time per stage and for the run from the recorder rather than keeping a second set of timers, so stages that issue no model calls still report elapsed time and a zero estimate
  - Group by whatever stage names appear in telemetry or recorder events with no allowlist, and report per-stage failure, retry-success, and terminal-failure counts
  - Treat the stage label as an open string: downstream features contribute new labels by emitting them, with no registration step, so introduce no allowlist, enum, or closed literal type of stage names anywhere in the reporting path, and use the declared stage ordering purely as a display-ordering hint that never filters or buckets an unrecognised label
  - Split each stage's input cost into its cached and uncached portions
  - Observable: for a synthetic collector and recorder, per-stage estimated costs and per-document estimated costs each sum exactly to the reported run total, and a stage present only in recorder events appears with a zero estimate and a non-zero elapsed time
  - _Requirements: 2.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.8, 4.9, 6.3_
  - _Boundary: cost_report_
  - _Depends: 3.2, 3.3_

- [ ] 4.2 Emit the cost report artifact, its degradation states, and the run-log summary
  - Stamp every artifact with the price-table version, currency, effective date, staleness marker, and an explicit estimate label, and list unpriced models, disabled stages, and warnings
  - Include the cache section reporting the configured key prefix, retention, threshold, per-stage hit rates, and any prompt-prefix stability violations observed during the run
  - Take the cache settings, the stability violations, the price table, and the resolved stage switches as arguments supplied by the caller, so this module reads no configuration and no files of its own
  - Expose the prefix-drift condition as returnable data alongside the existing logging behavior, so the report can embed it without duplicating the detection logic
  - Emit an explicit telemetry-unavailable status with null totals rather than zero-valued totals when no records exist
  - Order per-stage rows by the documented stage ordering then alphabetically, per-document rows by document identifier, and per-call rows by document, stage, timestamp, and attempt
  - Write atomically so a partial file never replaces a valid one, returning nothing and logging an error with the path and cause on failure rather than raising
  - Assert that a document recorded as skipped-and-complete carries no per-call cost lines in the same run
  - Log a concise summary containing the run total, mean cost per document, top stages by cost, and the reliability counts
  - Observable: a populated run writes a parseable cost report containing all required sections; an empty collector writes the unavailable-status variant; a write failure logs an error and leaves the previous file intact
  - _Requirements: 2.6, 3.8, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.2, 6.4, 7.4, 10.6, 12.5_
  - _Boundary: cost_report_
  - _Depends: 2.1, 3.1, 3.2, 3.3, 4.1_

- [ ] 4.3 (P) Capture code revision, environment, and tool versions as degradable elements
  - Read the code revision and working-tree-modified flag from the version-control checkout under a bounded timeout, degrading with a stated reason when unavailable
  - Read interpreter version, platform, and declared dependency versions using distribution metadata without importing any of those packages, so the lazy-import rule for heavy optional dependencies holds
  - Record the external parsing service as an endpoint with its configured location and, when known, its reported version
  - Represent every captured element uniformly as a value plus an availability status and an optional reason
  - Observable: capture succeeds in a normal checkout, and in an environment without version control or with an uninstalled optional dependency it returns elements marked unavailable with reasons instead of raising
  - _Requirements: 8.4, 8.5, 12.1, 12.2_
  - _Boundary: run_manifest_

- [ ] 4.4 Establish the shared run-provenance source, then assemble, canonicalize, and write the run manifest
  - Define a single immutable run-provenance value built once at run start, carrying the run identifier, schema version, extraction-map hash, per-stage resolved model names, prompt version identifiers and content hashes, the disabled stage list, and the estimate label, plus a projection of itself suitable for stamping onto a per-document output
  - Make this value the only producer of those version fields: the manifest and the per-document stamp both read it, so their equality holds by construction rather than by two agreeing computations, mirroring the rule already applied to identity hashes
  - Build the manifest from that provenance value plus the run timestamps, the resolved configuration after all overrides, the captured environment elements, the enabled stage set, and the per-document identity hashes
  - Reuse the identity hashes the pipeline already computes rather than computing a second identity
  - Replace every credential-bearing value with a presence flag so no secret appears anywhere in the artifact
  - Record the determinism block: seeds or their absence, the declared deterministic and non-deterministic local steps with reasons, and an explicit statement that model responses are not covered by the determinism guarantee
  - Provide a canonical form that removes the run identifier, timestamps, durations, and every element marked unavailable
  - Order documents by identifier and all declared lists alphabetically, and write atomically, returning nothing and logging an error on failure rather than raising
  - Keep the artifact run-scoped, carrying no per-claim or per-evidence lineage, and readable without access to the source tree or configuration file
  - Treat this artifact as the project's sole reproducibility manifest: no second manifest is built anywhere, and the audit-package spec consumes this file by reference rather than re-deriving its contents, so any change to its shape, key set, element envelope, or filename is a downstream contract change
  - Observable: a run writes a parseable manifest containing every required element, with no credential values present; the provenance projection and the manifest report identical version values for the same run; and two manifests differing only in identifier, timestamps, durations, and unavailable elements produce equal canonical forms
  - _Requirements: 7.6, 8.1, 8.2, 8.3, 8.6, 8.7, 8.8, 9.5, 12.3, 12.4, 12.5, 12.6_
  - _Boundary: run_manifest_
  - _Depends: 3.1, 4.3_

- [ ] 4.5 Test the two reporting artifacts
  - Cover the aggregation sum invariants, unknown-stage handling, zero-cost stage presence, and the cache split
  - Cover the telemetry-unavailable status, the disabled-stage marker, the estimate labelling, the stable ordering, and the non-raising write-failure path
  - Cover manifest completeness, credential redaction, degradable elements, canonical equality, and the determinism declarations
  - Observable: the new report test modules pass and assert artifact content rather than merely that a file was created
  - _Requirements: 4.6, 4.8, 4.9, 5.1, 5.2, 5.4, 5.5, 7.4, 8.2, 8.4, 8.5, 8.7, 12.1, 12.2, 12.3, 12.4, 12.6_
  - _Boundary: cost_report, run_manifest_

- [ ] 5. Resume persistence and per-output provenance

- [ ] 5.1 Persist per-document identity and answer the resume question
  - Write the computed identity fields and the resolved absolute output path into a completed manifest entry under a dedicated nested key, leaving the existing status, error, and failed-chunk keys untouched
  - Provide a resume decision that returns skip only when the entry is complete, the recorded output path resolves to a readable and parseable output, and every identity field matches; and that names the mismatching identity element in its reason when it does not
  - Return a reprocess decision with a distinct reason when the recorded output is missing or unreadable, and short-circuit to reprocess when the explicit overwrite setting is enabled
  - Resolve completeness against the recorded output path rather than the current run folder, so a completed output from an earlier run is actually found
  - Observable: a manifest entry written by one run is correctly resolved as skip by a second run, and each of the reprocess reasons is reachable and distinguishable in the returned decision
  - _Requirements: 8.3, 10.1, 10.3, 10.4, 10.5_
  - _Boundary: manifest_

- [ ] 5.2 Forward call identity, gate optional stages, and stamp provenance on per-document outputs
  - Pass the paper name as the document identifier and supply the chunk's domain group into the model call arguments, filling a parameter that exists today but is never provided
  - Consult the stage registry before the warmup call and before the validation-repair loop, and when a stage is disabled skip its model calls, record a disabled-by-configuration skip event, and continue producing extraction output
  - Receive the stage registry, the run recorder, and the shared run-provenance value as explicit arguments from the caller, reading no configuration of its own for these concerns
  - Attach a provenance envelope key to each per-document output built from the shared provenance value's projection — computing none of those version fields locally — leaving the extracted field records unchanged in shape
  - Assert that the per-document output envelope still passes final-output validation with the new key present; changing either schema singleton is out of boundary, so if validation rejects the envelope the work stops and the question returns to design rather than being patched here
  - Persist the identity block and resolved output path into the manifest entry on successful write
  - Observable: a processed document's output file contains the provenance key with values matching the run manifest while its field records are byte-comparable to the pre-change shape, and disabling the repair stage produces output plus a skip event and no repair model call
  - _Requirements: 1.3, 7.2, 7.3, 7.7, 9.1, 9.2, 9.3, 9.4, 9.5_
  - _Boundary: pdf_processor_
  - _Depends: 3.1, 3.3, 4.4, 5.1_

- [ ] 6. Integration: run lifecycle wiring

- [ ] 6.1 Wire the run recorder, resume gate, and configured cache threshold into the orchestrator
  - Construct the run recorder, the resolved stage registry, and the shared run-provenance value alongside the existing telemetry collector at run start, and pass all four explicitly into per-document processing, introducing no module-level state
  - Wrap the document extraction and per-document local phases in stage spans, using stage names drawn from the registry's declared ordering so no run-lifecycle span is filed as an unknown stage, and without touching the quality-control package
  - Collect the cache settings and the prefix-drift condition at end of run for the reporting layer, so the reporting layer itself reads no configuration
  - Apply the resume decision before dispatching each document, recording the resulting skip, reprocess, or overwrite event with its reason
  - Read the configured cache-rate threshold and pass it to the cache diagnostics check, replacing today's always-default behavior, falling back to the documented default with a warning when the configured value is missing or out of range
  - Observable: a run over a corpus with one already-complete document dispatches only the remaining documents, and the audit log contains a start and completion span for every dispatched document plus a skip event for the complete one
  - _Requirements: 4.5, 6.5, 6.6, 7.6, 8.1, 10.1, 10.2, 10.3, 10.4, 10.5, 11.1_
  - _Boundary: orchestrator_
  - _Depends: 3.1, 3.3, 4.4, 5.1_

- [ ] 6.2 Emit the three run artifacts from the existing end-of-run block
  - Extend the existing end-of-run block, after the unchanged token-report generation, to load the price table, supply it together with the collected cache settings, prefix-drift condition, and stage registry to the cost report build, write the cost report, build and write the run manifest from the run-provenance value established at run start, and write the audit log
  - Guard each of the three writes independently so a failure in one logs an error and does not prevent the others or invalidate already-written extraction outputs
  - Keep the run manifest write ahead of any audit-package collection or slot contribution that references it: the audit-export capability records the manifest by path and content hash rather than re-deriving it, so it must observe a fully written file; when the manifest write fails, that reference degrades to unavailable with a stated reason rather than pointing at a missing or partial file
  - Regenerate all run-scoped reports for the current run regardless of how many documents were skipped on resume
  - Contribute the built cost report into the audit package's cost-report slot, naming this spec as the supplier, whenever an audit-package builder is supplied as an optional argument; mark the slot absent with a stated reason instead when the report is unavailable or its build failed; contribute nothing and change no behaviour when no builder is supplied, which is the shipped default
  - Do not assemble, order, seal, hash, or validate the audit package, and do not declare or rename any slot: this task contributes one artifact into a slot owned by this spec and nothing further
  - Emit the human-readable cost summary to the run log after the artifacts are written
  - Observable: a completed run leaves the cost report, run manifest, and audit log in the run output folder alongside the unchanged token report, forcing one write to fail still produces the other two, and a stub audit-package builder receives exactly one cost-report contribution naming this spec as its supplier
  - _Requirements: 5.1, 5.2, 5.3, 5.5, 8.6, 10.7, 11.4_
  - _Boundary: orchestrator_
  - _Depends: 4.2, 4.4, 6.1_

- [ ] 7. Validation: invariants, guards, and regressions

- [ ] 7.1 Add property tests for the cost and artifact invariants
  - Assert that for arbitrary record sets and price tables, per-stage and per-document estimated costs each sum to the run total, that adding a record never decreases the total, and that a zero-token record contributes zero cost
  - Assert that every stage appearing in telemetry or recorder events appears exactly once in the per-stage rows, including stage names the feature has never seen
  - Assert that for arbitrary attempt sequences the retry-success and terminal-failure counts never exceed the number of distinct calls and that run-level counts equal the per-stage sums
  - Assert that the three artifacts built twice from the same shuffled inputs serialize identically after canonicalization, and that two manifests differing only in volatile elements have equal canonical forms
  - Observable: the property test modules pass under the project's property-testing settings and fail if the sum invariant is deliberately broken
  - _Requirements: 2.5, 4.6, 4.8, 4.9, 12.3, 12.5_
  - _Boundary: cost_report, run_manifest, telemetry_

- [ ] 7.2 Add the prompt-cache stability and dependency-direction guards
  - Guard prefix stability, not file immutability: the prompt-construction module may be edited by other specs (a sibling spec adds an optional routed-evidence parameter to the user-message builder that is byte-identical when left unset), so the assertion is on the shared prefix and the default-path payload, never on the file being unmodified
  - Assert the shared paper prefix's source text and its returned output are unchanged for a fixed evidence package, and that it is byte-identical across warmup, extraction chunk, and synthesis message construction for the same evidence package
  - Assert the default-path payload — every message built with all newly-added optional parameters left at their defaults — is byte-identical to the recorded pre-change payload
  - Add a source-level guard asserting that no pricing, cost, run-identifier, or timestamp symbol appears in the prompt-construction module
  - Confirm the existing cross-package dependency test still passes unmodified, proving the price table has not leaked into the model client
  - Observable: the stability and dependency guards pass while an additive optional parameter with an unchanged default payload is present, and the guard fails if a run identifier is deliberately injected into the shared prefix or the default payload changes by one byte
  - _Requirements: 1.4, 5.7, 6.1_
  - _Boundary: prompts, api_client, telemetry_

- [ ] 7.3 Add resume-safety and output-provenance integration tests
  - Assert that a second run over a corpus whose outputs are already complete issues no model calls for those documents, leaves their files unchanged, records already-complete skip events, and reports no per-call cost for them
  - Assert that enabling the explicit overwrite setting reprocesses and records the overwrite, and that a changed identity element reprocesses with that element named
  - Assert that the per-document provenance key matches the run manifest for the same run, lists the disabled stages, and leaves the extracted field records unchanged
  - Assert that a disabled optional stage appears in the cost report with a disabled marker and a zero estimate
  - Observable: the integration test modules pass using mocked model calls only, with no network access
  - _Requirements: 7.4, 9.1, 9.3, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.6_
  - _Boundary: orchestrator, pdf_processor, manifest, cost_report_
  - _Depends: 6.2_

- [ ] 7.4 Confirm the token report and existing suites are unchanged
  - Run the existing token-report, token-budget, token-efficiency-regression, manifest, prompt, and API-client suites without modifying them
  - Confirm the token report's top-level and per-stage structure and its file location are unchanged and that the additional telemetry keys are purely additive
  - Observable: the full fast suite passes from the repository root with no edits to any pre-existing test module
  - _Requirements: 1.4, 5.7_
  - _Boundary: token_report, telemetry_
  - _Depends: 6.2_
