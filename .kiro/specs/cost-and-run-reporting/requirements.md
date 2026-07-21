# Requirements Document

## Project Description (Input)
Users running EviTrace over a corpus cannot answer the two questions that decide whether the tool is usable: what did this run cost, and what exactly produced these outputs. Token counts exist (`src/agents/openai/telemetry.py`, `src/pipeline/token_report.py`) but are not money, so there is no per-document or per-project budget signal, no view of which stage is expensive, and no basis for turning a stage off. No latency, retry count, error, or document identifier is recorded anywhere in `src/agents/openai/`, and a failed API attempt produces no telemetry record at all. Cost accounting does not exist — no price table, no currency, no per-call estimate. Separately, outputs carry no self-describing record of the configuration, prompt versions, and model versions behind them, so results cannot be compared across experiments or defended in a methods section; `compute_identity()` in `src/pipeline/manifest.py` hashes inputs for staleness detection only, is never persisted, and is never exported as a run manifest.

This spec extends the existing telemetry and report machinery rather than building a parallel one: latency/retry/error/document capture in telemetry, a versioned operator-editable price table, cost derived as a projection over telemetry records, stage-disable switches for nonessential model calls, and an exported per-run reproducibility manifest. It covers multiagent R23.1–R23.6 and R27.1–R27.6, is the implementation home for `xtrace-toolkit` R-X-2, serves `xtrace-toolkit` R-LLM-4, and owns the cost-report content that `reviewer-ui` (multiagent R21.7) merely surfaces. Cost is always an *estimate*, labelled as such, with the price-table version recorded — never a billed figure.

## Introduction

This feature makes an EviTrace run financially and methodologically self-describing. It closes two gaps that today prevent an operator from deciding whether to run the tool over a corpus, and prevent a researcher from defending or reproducing a result.

The first gap is money. Telemetry already attributes tokens to stages, but tokens are not currency, so nobody can say what a document cost, which stage dominated spend, or what turning a stage off would save. This spec adds the missing per-call facts (elapsed time, document identity, attempt number, failure reason), a versioned price table that operators edit as configuration rather than code, and a cost report that projects estimated cost per call, per document, per stage, and per run.

The second gap is reproducibility. Nothing an EviTrace run writes today records the resolved configuration, model versions, prompt versions, schema version, parser versions, code revision, or environment behind its outputs. This spec emits a per-run reproducibility manifest, stamps version provenance onto each per-document output, records when stages were skipped or rerun, protects completed outputs from being overwritten on resume, and reports which parts of local processing are deterministic.

Cost figures produced here are always estimates derived from a locally configured price table. They are never billed amounts and are never reconciled against a provider usage API.

## Boundary Context

- **In scope**: extension of per-call telemetry with elapsed time, document identity, attempt number, and failure classification; recording of failed and retried API calls that today vanish; a versioned, operator-editable price table with staleness signalling; estimated cost derivation at call, document, stage, and run level; a run-scoped cost report artifact; reporting of prompt-cache stability and configured cache keys; switches that disable nonessential model calls; a run-scoped reproducibility manifest covering resolved configuration, schema version, parser and tool versions, model names, prompt versions, code revision, environment, and timestamps; version provenance stamped on each per-document output; refusal to overwrite completed outputs when a run is resumed; audit-log entries for skipped and rerun stages; and a determinism statement covering seeds, stable ordering, and pinned tool versions.
- **Out of scope**: cost enforcement, hard budget aborts, or any change to the token ceilings already enforced by the existing token-budget stage; re-litigation of thresholds established by the completed token-efficient-extraction work; cost dashboards, charts, or any visual surface; billing integration and reconciliation against provider usage APIs; the full audit package; ablation or experiment orchestration; evidence-level or claim-level lineage and tamper-evidence; and any change to the content or ordering of the cached shared prompt prefix.
- **Adjacent expectations**: the extraction pipeline continues to emit token telemetry and a token report, and continues to enforce token budgets, with their current behavior unchanged. The reviewer interface and the audit-export capability are consumers that display or embed the cost report and run manifest produced here; they do not produce their own. The evaluation capability consumes the stage-disable switches defined here but owns experiment design. The provenance capability owns per-claim derivation lineage; this feature's manifest is run-scoped and must remain consumable by it rather than competing with it.

## Glossary

- **Run**: One end-to-end invocation of the EviTrace pipeline over a set of PDFs, writing into a single run-scoped output directory.
- **Stage**: A named unit of pipeline work. Model-calling stages today are `extraction_chunk`, `synthesis`, `validation_repair`, and `cache_warmup`. Local (non-model) stages include extraction/parsing and quality control. New stage names may be introduced by other features without changing this feature's reporting behavior.
- **Document_ID**: The stable identifier of the PDF a unit of work belongs to, as already used to name that document's output file.
- **Telemetry_Record**: The existing per-API-call record capturing stage, model, timestamp, token counts, and prompt fingerprint, as extended by this feature.
- **Price_Table**: Operator-editable configuration mapping a model name to unit prices per token class (uncached input, cached input, output), carrying a currency, a version identifier, and an effective date.
- **Cost_Estimate**: A currency amount derived by applying the Price_Table to recorded token counts. Always an estimate, never a billed amount.
- **Cost_Report**: The run-scoped artifact reporting Cost_Estimates and elapsed time at call, document, stage, and run level.
- **Run_Manifest**: The run-scoped artifact describing what produced the run's outputs: resolved configuration, versions, code revision, environment, and timestamps.
- **Run_Audit_Log**: The run-scoped append-only record of stage-level lifecycle events, including stages that were skipped, disabled, or rerun.
- **Optional_Stage**: A model-calling stage that an operator may disable without preventing the run from producing per-document extraction outputs.

## Requirements

### Requirement 1: Per-Call Telemetry Extension

**Objective:** As a pipeline operator, I want every model call to record who it was for, how long it took, and which attempt it was, so that cost and latency can be attributed to a specific document and stage rather than to the run as a whole.

#### Acceptance Criteria

1. When a model call completes, the Pipeline shall record on that call's Telemetry_Record the Document_ID the call was issued for, the elapsed wall-clock duration of the call in seconds, and the 1-based attempt number of the attempt that produced the result.
2. When a model call is issued on behalf of no single document, the Pipeline shall record a documented run-level Document_ID placeholder rather than omitting the field.
3. When a model call belongs to an extraction chunk, the Pipeline shall record the chunk's domain group alongside the already-recorded field index range.
4. The Pipeline shall preserve every telemetry field, stage label, and aggregation behavior that exists before this feature, so that the existing token report remains valid and its consumers are unaffected.
5. If a duration, attempt number, or Document_ID cannot be determined for a completed call, then the Pipeline shall record the record with that field marked unknown, log a warning identifying the stage and model, and continue processing.
6. The Pipeline shall not cause an extraction to fail because telemetry capture failed.

### Requirement 2: Failure and Retry Accounting

**Objective:** As a pipeline operator, I want failed and retried model calls to appear in the record with their token and cost impact, so that a run's true consumption is visible and no failure is silently dropped.

#### Acceptance Criteria

1. When a model call attempt fails, the Pipeline shall record a Telemetry_Record for that attempt containing the stage, model, Document_ID, attempt number, elapsed duration, an error classification, and an error message summary.
2. When a failed attempt reports token usage, the Pipeline shall record those tokens so that the attempt's incremental token and cost impact is included in run totals.
3. When a failed attempt reports no token usage, the Pipeline shall record the attempt with zero token counts and a zero Cost_Estimate rather than omitting the attempt.
4. When all retry attempts for a call are exhausted, the Pipeline shall record the terminal failure, including the total number of attempts made for that call.
5. The Pipeline shall report, per stage and per run, the number of failed attempts, the number of calls that ultimately succeeded after at least one retry, and the number of calls that terminally failed.
6. The Pipeline shall ensure that no model-call failure is observable only in transient console output; every failure recorded by this feature shall be retrievable from a run-scoped artifact after the run ends.

### Requirement 3: Versioned Price Table

**Objective:** As a pipeline operator, I want model prices to live in editable configuration with an explicit version and effective date, so that price changes are a configuration edit and stale prices are visible rather than silently assumed current.

#### Acceptance Criteria

1. The Pipeline shall read model prices from operator-editable configuration that declares, per model name, a unit price for uncached input tokens, cached input tokens, and output tokens.
2. The Price_Table shall declare a currency code, a version identifier, and an effective date that apply to all of its entries.
3. The Pipeline shall apply the environment-over-configuration-over-default precedence already used for other settings when resolving Price_Table settings.
4. If a model observed in telemetry has no Price_Table entry, then the Pipeline shall report that model's cost as unpriced, count its tokens in token totals, exclude it from currency totals, and log a warning naming the model.
5. If the Price_Table is absent, empty, or unreadable, then the Pipeline shall still produce a Cost_Report in which all Cost_Estimates are marked unavailable, and shall log a warning stating that no prices were configured.
6. If a Price_Table entry has a missing, non-numeric, or negative unit price, then the Pipeline shall treat that model as unpriced, log a warning naming the model and the offending price field, and continue the run.
7. When the Price_Table's effective date is older than a configured staleness horizon at the time a run completes, the Pipeline shall mark the Cost_Report as based on a potentially stale Price_Table and log a warning stating the effective date and the horizon.
8. The Pipeline shall record the Price_Table version identifier, currency, and effective date in every artifact that reports a Cost_Estimate.

### Requirement 4: Cost Estimation and Attribution

**Objective:** As a researcher planning a corpus run, I want estimated cost attributed per call, per document, per stage, and for the whole run, so that I can set a budget, see which stage dominates spend, and judge what turning a stage off would save.

#### Acceptance Criteria

1. When a Telemetry_Record carries token counts and its model has a Price_Table entry, the Pipeline shall derive a Cost_Estimate for that call by applying the per-token-class unit prices to the record's uncached input, cached input, and output token counts.
2. The Pipeline shall report Cost_Estimate and token totals aggregated per Document_ID, per stage, per stage-within-document, and for the run as a whole.
3. The Pipeline shall report, for the run as a whole, the number of documents processed and the mean estimated cost per document.
4. The Pipeline shall report, per stage, the stage's share of total estimated run cost.
5. The Pipeline shall report elapsed wall-clock time per stage and for the whole run, including for stages that issue no model calls, so that local processing effort is visible even where its Cost_Estimate is zero.
6. When a stage issues no model calls, the Pipeline shall report that stage with a zero Cost_Estimate rather than omitting it.
7. The Pipeline shall label every reported currency amount as an estimate derived from the configured Price_Table and shall not present it as a billed or invoiced amount.
8. The Pipeline shall ensure that the sum of per-stage Cost_Estimates and the sum of per-document Cost_Estimates each equal the reported run total for the same currency.
9. When a stage name that this feature does not know in advance appears in telemetry, the Pipeline shall report it as its own stage row without requiring a change to the reporting configuration. The stage label is an open string: downstream features contribute new stage labels by emitting them, with no registration step, and the Pipeline shall not introduce an allowlist, enum, or any other closed set that could reject or drop an unrecognised stage label.

### Requirement 5: Cost Report Artifact

**Objective:** As a researcher, I want a single machine-readable cost artifact written at the end of every run, so that reporting, review, and audit-export consumers read one authoritative source instead of re-deriving cost.

#### Acceptance Criteria

1. When a run completes, the Pipeline shall write a Cost_Report into the run's output directory.
2. The Cost_Report shall contain the run identifier, run start and end timestamps, Price_Table version, currency, effective date, staleness marker, per-call cost lines, per-document totals, per-stage totals, per-stage-within-document totals, run totals, and the failure and retry counts required by Requirement 2.
3. When a run completes, the Pipeline shall also emit a concise human-readable cost summary to the run log containing the run total estimated cost, the mean estimated cost per document, and the top stages by estimated cost.
4. If no telemetry is available for a run, then the Pipeline shall write a Cost_Report whose status states that telemetry was unavailable, rather than writing zero-valued totals that would read as a free run.
5. If writing the Cost_Report fails, then the Pipeline shall log an error identifying the target path and the cause, and shall not invalidate the run's already-written extraction outputs.
6. The Pipeline shall write the Cost_Report such that a partially written file is never left in place of a previously valid one.
7. The Pipeline shall keep the existing token report unchanged in shape and location, so that cost reporting is additive rather than a breaking change to token reporting.

### Requirement 6: Prompt Cache Stability and Cache Reporting

**Objective:** As a pipeline operator, I want the run to assert and report that prompt caching was actually configured and stable, so that a silent loss of cache reuse shows up as a reported fact rather than as an unexplained cost increase.

#### Acceptance Criteria

1. The Pipeline shall keep the cached shared prompt prefix byte-identical across cache-warmup, extraction, and synthesis calls for the same document, and shall not place any run identifier, timestamp, cost metadata, price-table version, or telemetry field inside it.
2. When a run completes, the Pipeline shall report the configured prompt cache key prefix and cache retention setting that were in effect.
3. When a run completes, the Pipeline shall report, per stage, the observed cache hit rate and the estimated cost attributable to cached versus uncached input tokens.
4. If two calls in the same stage and prompt version within a run produce different stable-prefix fingerprints, then the Pipeline shall record a cache-stability violation in the Cost_Report identifying the stage, the prompt version, and the differing fingerprints, in addition to the warning already logged.
5. When a cache-rate threshold is configured, the Pipeline shall use the configured value when deciding whether to report a low-cache-rate condition, rather than an unconfigurable built-in value.
6. If the configured cache-rate threshold is missing or outside its valid range, then the Pipeline shall use the documented default, log a warning naming the invalid value, and continue.

### Requirement 7: Disabling Nonessential Model Calls

**Objective:** As a pipeline operator on a budget, I want to switch off model calls that are not required to produce extraction output, so that I can trade a known capability for a known saving.

#### Acceptance Criteria

1. The Pipeline shall expose configuration that enables or disables each Optional_Stage independently, defaulting every Optional_Stage to its current enabled or disabled state so that an unmodified configuration behaves exactly as before.
2. When an Optional_Stage is disabled, the Pipeline shall issue no model calls for that stage and shall still produce per-document extraction outputs.
3. When an Optional_Stage is disabled, the Pipeline shall record a skip event for that stage in the Run_Audit_Log stating that it was disabled by configuration.
4. When an Optional_Stage is disabled, the Pipeline shall report that stage in the Cost_Report with a disabled marker and a zero Cost_Estimate, rather than omitting it.
5. If configuration requests disabling a stage that is required to produce extraction output, then the Pipeline shall refuse the request, log an error naming the stage, and continue with that stage enabled.
6. The Pipeline shall record in the Run_Manifest which Optional_Stages were enabled and which were disabled for the run.
7. The Pipeline shall not silently degrade output quality when a stage is disabled; every per-document output produced under a disabled Optional_Stage shall carry a marker naming the stages that were disabled for its run.

### Requirement 8: Run Reproducibility Manifest

**Objective:** As a researcher writing a methods section, I want each run to emit a manifest describing exactly what produced its outputs, so that results can be compared across experiments and defended without reconstructing the run from memory.

#### Acceptance Criteria

1. When a run starts, the Pipeline shall begin a Run_Manifest recording the run identifier and the run start timestamp in ISO 8601 UTC.
2. When a run completes, the Pipeline shall write the Run_Manifest into the run's output directory containing: the fully resolved configuration in effect after environment and command-line overrides; the extraction schema version and a content hash of the extraction field map; resolved model names for each model-calling stage; prompt version identifiers and content hashes for each prompt template used; versions of the extraction backends and external tools used; the code revision of the running checkout together with whether the working tree was modified; the interpreter version, platform, and versions of the declared runtime dependencies; the set of documents processed with their content hashes; and the run end timestamp.
3. The Pipeline shall record in the Run_Manifest the same per-document identity hashes the pipeline already computes for staleness detection, rather than computing a second, competing identity.
4. The Pipeline shall exclude API keys, credentials, and any other secret from the Run_Manifest, recording only whether each credential was present.
5. If any manifest element cannot be determined, then the Pipeline shall record that element as unavailable with a stated reason and shall still write the Run_Manifest.
6. If writing the Run_Manifest fails, then the Pipeline shall log an error identifying the target path and the cause, and shall not invalidate the run's already-written extraction outputs.
7. The Pipeline shall write the Run_Manifest as a self-contained artifact that can be read without access to the pipeline's source code or its configuration file.
8. The Pipeline shall treat the Run_Manifest as run-scoped only and shall not record per-claim or per-evidence derivation lineage in it.

### Requirement 9: Per-Output Version Provenance

**Objective:** As a reviewer comparing outputs produced before and after a model change, I want each document's output to state the versions that produced it, so that a stale output is distinguishable from a current one without consulting the run that made it.

#### Acceptance Criteria

1. When a per-document extraction output is written, the Pipeline shall record within it the model name used for each model-calling stage that contributed to it, the prompt version identifiers used, the extraction schema version, the extraction field map hash, and the identifier of the run that produced it.
2. When the configured model changes between runs, the Pipeline shall preserve the model version recorded on each previously written output rather than rewriting it.
3. When an output is written under a configuration in which one or more Optional_Stages were disabled, the Pipeline shall record the names of those disabled stages within that output.
4. The Pipeline shall record version provenance on a per-document output without altering the extracted field records themselves, so that existing consumers of the field data continue to work.
5. The Pipeline shall record the same version values in the per-document output and in the Run_Manifest for the run that produced it.

### Requirement 10: Resume Safety

**Objective:** As an operator resuming an interrupted corpus run, I want completed outputs left alone unless I explicitly ask otherwise, so that a resume cannot destroy work that already succeeded.

#### Acceptance Criteria

1. When a run resumes and a document is recorded as complete with a readable, valid output present, the Pipeline shall not overwrite that output and shall not re-issue model calls for that document.
2. When the Pipeline skips a document because its output is already complete, the Pipeline shall record a skip event for that document in the Run_Audit_Log stating the reason.
3. Where an explicit overwrite setting is enabled, the Pipeline shall reprocess and overwrite completed outputs and shall record the overwrite in the Run_Audit_Log.
4. When a completed output is stale because the document content, resolved configuration, extraction field map, model, or schema version differs from the values recorded for it, the Pipeline shall reprocess that document and shall record the reprocessing and the changed identity element in the Run_Audit_Log.
5. If a recorded-complete output is missing or unreadable, then the Pipeline shall reprocess that document and record the reason in the Run_Audit_Log.
6. The Pipeline shall not report a document as skipped and complete in the same run in which it also reports a Cost_Estimate for new model calls against that document.
7. The Pipeline shall apply resume safety to per-document extraction outputs only, and shall continue to write fresh run-scoped reports for each run.

### Requirement 11: Run Audit Log for Skipped and Rerun Stages

**Objective:** As an auditor reviewing a run, I want a durable record of which stages ran, which were skipped, and which were rerun, so that the shape of the run is reconstructable after the fact.

#### Acceptance Criteria

1. When a stage starts, completes, is skipped, or is rerun for a document or for the run, the Pipeline shall append an event to the Run_Audit_Log recording the timestamp, the stage name, the Document_ID where applicable, the outcome, and the reason.
2. When a stage is skipped, the Pipeline shall record a reason drawn from a documented set that distinguishes at least: disabled by configuration, already complete, not applicable to this document, and prerequisite unavailable.
3. When a stage is rerun after a failure or a repair, the Pipeline shall record the rerun with its attempt number and the reason for the rerun.
4. When a run completes, the Pipeline shall write the Run_Audit_Log into the run's output directory.
5. The Pipeline shall append Run_Audit_Log events in the order they occur and shall not rewrite or delete an event once appended within a run.
6. The Pipeline shall exclude document text, extracted values, and secrets from Run_Audit_Log events, recording only identifiers, stage names, outcomes, reasons, and counts.
7. If appending a Run_Audit_Log event fails, then the Pipeline shall log a warning and continue the run.

### Requirement 12: Deterministic Local Processing Reporting

**Objective:** As a researcher attempting to reproduce a run, I want the run to state which local processing was deterministic and under what settings, so that I know which differences between two runs are expected and which are not.

#### Acceptance Criteria

1. The Pipeline shall record in the Run_Manifest the random seeds in effect for any local processing step that uses randomness, or shall state that the step uses none.
2. The Pipeline shall record in the Run_Manifest the pinned versions of the local tools and models whose output could change the run's results.
3. The Pipeline shall produce identical Run_Manifest content, excluding timestamps, run identifier, durations, and any element recorded as unavailable, for two runs over the same documents with the same resolved configuration, code revision, and environment.
4. The Pipeline shall record in the Run_Manifest which local processing steps are declared deterministic and which are declared non-deterministic, with a stated reason for each non-deterministic step.
5. The Pipeline shall order every list within the Cost_Report, the Run_Manifest, and the Run_Audit_Log by a documented stable key, so that two comparable runs produce diffable artifacts.
6. The Pipeline shall not claim determinism for model calls, and shall state in the Run_Manifest that model responses are outside the determinism guarantee.
