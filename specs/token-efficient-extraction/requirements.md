# Requirements Document

## Introduction

The EviTrace extraction pipeline is input-token bound, with a 32.9% prompt-cache hit rate and an average of 7,568 uncached input tokens per request across 119 model calls per run. This feature reduces uncached input tokens, increases prompt-cache utilization, makes token consumption attributable to specific pipeline stages, and introduces deterministic merge logic to eliminate unnecessary synthesis model calls — all while preserving extraction quality, validation pass rate, and evidence citation completeness.

## Glossary

- **Pipeline**: The end-to-end EviTrace extraction system orchestrated by `src/pipeline/orchestrator.py` that processes PDFs through evidence indexing, chunked LLM extraction, synthesis, and validation.
- **Stage**: A logical unit of work within the Pipeline that issues one or more OpenAI API requests. Stages include `extraction_chunk`, `synthesis`, `validation_repair`, `cache_warmup`, and `finalization`.
- **Stable_Prefix**: The byte-identical leading portion of a prompt that is shared across all chunk calls for the same prompt version, enabling server-side prompt caching. Contains system instructions, extraction rules, output contract, confidence definitions, and schema.
- **Dynamic_Suffix**: The trailing portion of a prompt that varies per chunk or per PDF. Contains paper-specific evidence, field definitions, and prior candidate summaries.
- **Evidence_ID**: A stable string identifier assigned to a discrete unit of evidence (sentence, paragraph, figure caption, or table) within the evidence index, used for deduplication and provenance tracking.
- **Token_Budget**: A configured maximum number of estimated input tokens permitted for a prompt at a given Stage before the system applies pruning, splitting, or rejection.
- **Deterministic_Merge**: A rule-based process that combines non-conflicting extraction results from multiple chunks without invoking the LLM, producing the same output for the same inputs regardless of execution order.
- **Prompt_Fingerprint**: A pair of cryptographic hashes — one for the Stable_Prefix and one for the Dynamic_Suffix — used to diagnose cache misses and detect unintended prompt drift.
- **Token_Report**: A structured artifact written after each Pipeline run summarizing per-stage token usage, cache rates, and cost attribution.
- **Telemetry_Record**: A single request-level JSON object capturing token counts, stage label, model name, timestamp, field range, and Prompt_Fingerprint for one OpenAI API call.
- **Conflict**: A state where two or more chunks produce different extracted values for the same field index, requiring LLM adjudication.
- **Repair_Prompt**: A targeted follow-up prompt sent after chunk validation failure, containing only the validation errors and affected field definitions rather than the full original prompt context.

## Requirements

### Requirement 1: Stage-Level Token Telemetry

**User Story:** As a pipeline maintainer, I want token usage attributed to pipeline stages so that I can identify the highest-cost components and track efficiency over time.

#### Acceptance Criteria

1. WHEN an OpenAI API request completes, THE Pipeline SHALL record a Telemetry_Record containing input tokens, output tokens, cached input tokens, uncached input tokens (computed as input tokens minus cached input tokens), total tokens, model name, request timestamp in ISO 8601 UTC format, and Stage name.
2. WHEN a request is part of an extraction chunk, THE Pipeline SHALL include the field index start, field index end, and domain group in the Telemetry_Record.
3. WHEN a request is part of synthesis, validation repair, retry, or finalization, THE Pipeline SHALL label the Telemetry_Record with the corresponding Stage name.
4. WHEN a Pipeline run completes, THE Pipeline SHALL produce an aggregate summary grouped by Stage containing total input tokens, total output tokens, total cached input tokens, total uncached input tokens, request count, and mean cache rate per Stage computed as total cached input tokens divided by total input tokens for that Stage expressed as a value between 0.0 and 1.0.
5. WHEN telemetry is recorded, THE Pipeline SHALL include the Prompt_Fingerprint in the Telemetry_Record, where Prompt_Fingerprint consists of a SHA-256 hash of the stable system-prompt prefix (truncated to 16 hex characters) and a prompt-version identifier string matching the prompt_cache_key_prefix from configuration.
6. IF telemetry recording fails due to a missing usage field in the API response, THEN THE Pipeline SHALL log a warning and continue processing without interrupting the extraction.
7. WHEN a Pipeline run completes, THE Pipeline SHALL write the aggregate summary to the run log and make per-request Telemetry_Records available in the audit package for that run.

### Requirement 2: Stable Prompt Prefix for Cache Reuse

**User Story:** As a pipeline maintainer, I want shared instructions and schema text placed in a stable prompt prefix so repeated calls benefit from server-side prompt caching.

#### Acceptance Criteria

1. WHEN extraction chunk prompts are constructed, THE Pipeline SHALL place all shared instructions, extraction rules, output contract, confidence definitions, and the paper-level evidence package before any Dynamic_Suffix content (extraction map, prior chunk outputs, or terminal instruction).
2. WHEN the paper-level evidence package is serialized into the Stable_Prefix, THE Pipeline SHALL emit evidence items sorted by their stable string ID in ascending lexicographic order so that the serialized bytes are identical across calls for the same paper within a single run.
3. WHEN field definitions are included in the extraction map portion of the prompt, THE Pipeline SHALL order them by field index in ascending numeric order.
4. WHEN prompts include configuration values, THE Pipeline SHALL include only values relevant to the model task and exclude runtime metadata such as timestamps, run IDs, chunk numbers, or PDF file names from the Stable_Prefix.
5. WHEN prompt construction logic changes, THE Pipeline SHALL provide automated tests that verify the Stable_Prefix is byte-identical across at least two chunk calls constructed with different chunk_fields lists but the same source_package input.
6. FOR ALL extraction chunk calls within a single Pipeline run using the same paper-level evidence package, THE Stable_Prefix SHALL produce byte-identical output when encoded as UTF-8.
7. WHEN the system prompt is loaded from agent_schema.json, THE Pipeline SHALL cache the loaded string in memory and return the same object reference on every subsequent call within the process lifetime, ensuring the system-message portion of the prompt is identical across all chunk calls.

### Requirement 3: Evidence Deduplication Across Calls

**User Story:** As a pipeline maintainer, I want shared evidence represented compactly so the same evidence text is not repeatedly sent as uncached input across chunk calls.

#### Acceptance Criteria

1. WHEN evidence items are indexed from GROBID TEI XML, THE Pipeline SHALL assign Evidence_IDs using a deterministic positional scheme (S for sentences, T for tables, F for figure captions followed by a zero-padded 6-digit counter) such that parsing the same TEI XML produces identical Evidence_IDs across runs.
2. WHEN a paper-level evidence package is built for extraction, THE Pipeline SHALL select evidence items up to the configured `max_evidence_items_per_chunk` (default 250) and `max_evidence_chars_per_chunk` (default 60,000 characters) limits, ranking items by section score and keyword overlap with the union of all extraction fields.
3. WHEN the paper-level evidence package is serialized, THE Pipeline SHALL emit items in stable Evidence_ID sort order so that the serialized JSON is byte-identical across all chunk calls for the same paper, enabling prompt-prefix cache hits on every call after the first.
4. WHEN a field extraction result references evidence, THE Pipeline SHALL include Evidence_IDs in the output `loc` field as a list of strings sufficient to reconstruct full evidence provenance from the evidence index cached on disk.
5. IF the evidence index cache file for a paper already exists and the PDF content hash matches, THEN THE Pipeline SHALL reuse the cached evidence index without re-parsing, preserving Evidence_ID stability across pipeline runs.

### Requirement 4: Compact Synthesis Input

**User Story:** As a pipeline maintainer, I want synthesis to operate on compact candidate summaries rather than replaying full extraction context so that synthesis prompts consume fewer input tokens.

#### Acceptance Criteria

1. WHEN synthesis is invoked, THE Pipeline SHALL NOT include full prior chunk prompt text or full evidence packages in the synthesis prompt.
2. WHEN synthesis receives field candidates, THE Pipeline SHALL format each candidate as a compact record containing only field index, field name, candidate value, confidence label, Evidence_IDs, and a short evidence snippet limited to 200 characters truncated at the nearest word boundary when conflict resolution requires context.
3. WHEN a field has a single candidate that passed chunk-level validation with no Conflict, THE Pipeline SHALL skip LLM synthesis for that field and use the candidate value directly.
4. WHEN multiple candidates for the same field produce a Conflict, THE Pipeline SHALL send only the conflicting fields and their candidates to the synthesis model for adjudication, excluding all non-conflicting fields from the synthesis prompt.
5. IF a field has zero candidates that passed chunk-level validation, THEN THE Pipeline SHALL record the field with a not-reported value and confidence "nr" without invoking synthesis for that field.
6. WHEN synthesis completes, THE Pipeline SHALL produce output conforming to the existing final extraction JSON schema (compact keys: i, v, loc, c) with all required keys preserved.
7. THE Pipeline SHALL limit the number of candidates sent to synthesis per conflicting field to a maximum of 5 candidates, selecting those with the highest confidence labels when more exist.

### Requirement 5: Deterministic Merge Before LLM Synthesis

**User Story:** As a pipeline maintainer, I want obvious merge cases resolved without model calls so synthesis tokens are reserved for genuinely ambiguous cases.

#### Acceptance Criteria

1. WHEN all chunk outputs that provide a value for a field produce string-equivalent values after leading/trailing whitespace trimming and internal whitespace collapsing, THE Pipeline SHALL merge them deterministically without invoking the LLM, using the value from the lowest-indexed chunk as the canonical string form.
2. WHEN a field has no extracted value (null, empty string, or absent key) and an empty loc list across all chunks, THE Pipeline SHALL assign the configured not-reported value and confidence label "nr" without model synthesis.
3. WHEN duplicate Evidence_IDs appear across chunks for the same field, THE Pipeline SHALL deduplicate them deterministically preserving the union of unique IDs sorted in ascending lexicographic order.
4. WHEN confidence labels differ across chunks but extracted values are string-equivalent after whitespace normalization, THE Pipeline SHALL select the highest confidence label using the ordering h > m > l > nr.
5. WHEN one or more chunks provide a non-empty value for a field and remaining chunks provide no value (null, empty string, or absent key) for that same field, THE Pipeline SHALL treat the field as non-conflicting and merge using the non-empty value without invoking the LLM.
6. WHEN Deterministic_Merge resolves all fields (indices 1 through 62) without any Conflict, THE Pipeline SHALL skip the synthesis model call entirely.
7. FOR ALL inputs where Deterministic_Merge is applied, THE Pipeline SHALL produce identical output when given the same chunk results regardless of chunk execution order.

### Requirement 6: Validation-Repair Loop With Minimal Context

**User Story:** As a pipeline maintainer, I want malformed chunk outputs repaired using only validation errors and affected fields to avoid expensive full-context retries.

#### Acceptance Criteria

1. WHEN chunk validation fails, THE Pipeline SHALL construct a Repair_Prompt containing only the validation error messages, the affected field definitions (field index range and compact schema format), and the invalid output fragment (the raw model response that failed validation) rather than repeating the full original prompt with evidence context.
2. WHEN a Repair_Prompt is constructed, THE Pipeline SHALL estimate its input token count using a character-to-token ratio of 4 characters per token and verify the estimated token count is strictly less than the original chunk prompt token count.
3. WHEN repair succeeds with a valid output on any attempt up to the configured maximum (default: 3), THE Pipeline SHALL replace the entire chunk response with the repaired output, preserving any fields from other chunks that already passed validation independently.
4. IF repair fails after the configured maximum repair attempts (default: 3), THEN THE Pipeline SHALL record the chunk as failed with error metadata including chunk number, last error message, error type (parse or schema), and attempt count, and SHALL continue processing remaining chunks for the same PDF.
5. WHEN repair requests are logged in telemetry, THE Pipeline SHALL record them with Stage name "validation_repair" distinguishable from original "extraction_chunk" requests, including the attempt number (1-based) and the error type that triggered the repair.

### Requirement 7: Token Budget Enforcement

**User Story:** As a pipeline maintainer, I want explicit token budgets so unexpectedly large prompts do not silently drive cost spikes.

#### Acceptance Criteria

1. WHEN constructing any prompt, THE Pipeline SHALL estimate input token count using a characters-divided-by-4 heuristic before dispatching the request to the OpenAI API.
2. WHEN a prompt exceeds the configured Token_Budget for its Stage, THE Pipeline SHALL apply mitigation strategies in the following order: (a) evidence pruning by reducing max_evidence_items_per_chunk and max_evidence_chars_per_chunk until the estimate is within budget, (b) request splitting into field groups of at most 5 fields per sub-request, (c) rejection with a diagnostic log message if the estimate still exceeds the budget after (a) and (b).
3. WHEN synthesis input exceeds its Token_Budget, THE Pipeline SHALL fall back to conflict-only synthesis by removing fields whose extracted values are identical across all prior extraction chunks from the prompt.
4. WHEN a request exceeds its Token_Budget after all pruning strategies are exhausted, THE Pipeline SHALL log at WARNING level the Stage name, estimated token count, budget limit, and the top three contributing prompt sections (system prompt, evidence package, field definitions, prior context) ranked by estimated token size.
5. THE Pipeline SHALL provide default Token_Budget values of 100000 tokens for extraction_chunk, 20000 tokens for validation_repair, 120000 tokens for synthesis, and 10000 tokens for cache_warmup under the `token_budgets` key in configs/config.yaml.
6. IF a Token_Budget configuration value is missing, non-integer, zero, or negative, THEN THE Pipeline SHALL use the documented default for that Stage and log a warning indicating the Stage name and the invalid value that was replaced.

### Requirement 8: Prompt Fingerprinting and Cache Diagnostics

**User Story:** As a pipeline maintainer, I want to know why cache utilization changes between calls and runs so I can detect and fix unintended prompt drift.

#### Acceptance Criteria

1. WHEN a prompt is constructed, THE Pipeline SHALL compute a SHA-256 hash over the UTF-8 encoded bytes of the Stable_Prefix and a separate SHA-256 hash over the UTF-8 encoded bytes of the Dynamic_Suffix.
2. WHEN token usage is logged in a Telemetry_Record, THE Pipeline SHALL include the Stable_Prefix hash and a prompt-version identifier string of at most 64 characters that uniquely identifies the current prompt template version.
3. WHEN a Stage has completed at least 3 requests within a run and the observed cache rate for that Stage falls below a configured threshold (default: 50%), THE Pipeline SHALL log a cache diagnostics warning including the Stage name, observed cache rate as a percentage, and the configured threshold.
4. WHEN prompts within the same Stage and prompt version produce different Stable_Prefix hashes within a single run, THE Pipeline SHALL log a diagnostic warning including the Stage name, prompt-version identifier, and the two or more distinct Stable_Prefix hash values observed.
5. WHEN prompt templates or shared instructions change between releases, THE Pipeline SHALL assign a new prompt-version identifier value that is distinct from all prior values used in the same output directory.
6. IF the configured cache rate threshold is missing or not a number between 0 and 100, THEN THE Pipeline SHALL use the default threshold of 50% and log a warning indicating the fallback.

### Requirement 9: Token-Efficiency Regression Tests

**User Story:** As a pipeline maintainer, I want automated tests to prevent future changes from increasing token usage beyond acceptable thresholds.

#### Acceptance Criteria

1. WHEN token-efficiency tests run against fixture PDFs, THE Pipeline SHALL verify that estimated uncached input tokens per request do not exceed 5,000 tokens (the configured baseline threshold default).
2. WHEN prompt templates are modified, THE Pipeline SHALL provide tests that compute the byte-level longest common prefix ratio between the Stable_Prefix before and after the change, and fail if that ratio drops below 90%.
3. WHEN synthesis is invoked on fixture outputs where all fields have non-conflicting values (no field index has differing extracted values across chunks after whitespace normalization), THE Pipeline SHALL provide tests verifying that no synthesis model call is made.
4. WHEN evidence is pruned from a chunk prompt, THE Pipeline SHALL provide tests verifying that all Evidence_IDs referenced by fields with confidence label "h" remain present in the pruned prompt.
5. WHEN a Token_Report is generated, THE Pipeline SHALL provide tests validating the report conforms to the Token_Report JSON schema and that the sum of per-Stage input tokens, output tokens, cached input tokens, and uncached input tokens each equal their corresponding overall totals in the report.
6. IF a token-efficiency regression test fails, THEN THE Pipeline SHALL include in the test failure output the measured value, the threshold value, and the Stage or prompt component that caused the breach.

### Requirement 10: Run-Level Token Report Artifact

**User Story:** As a pipeline user, I want a concise token-efficiency report after each run so I can track cost and efficiency trends over time.

#### Acceptance Criteria

1. WHEN a Pipeline run completes, THE Pipeline SHALL write a Token_Report as a JSON file named `token_report.json` to the run output directory.
2. WHEN the Token_Report is generated, THE Pipeline SHALL include the following top-level aggregate fields: total input tokens, total output tokens, total cached input tokens, total uncached input tokens, overall cache rate computed as (total cached input tokens / total input tokens), output-to-input ratio computed as (total output tokens / total input tokens), per-Stage usage breakdown, and the top five most expensive requests ranked by total tokens in descending order.
3. WHEN the Token_Report includes a per-Stage usage breakdown, THE Pipeline SHALL include for each Stage: stage name, total input tokens, total output tokens, total cached input tokens, total uncached input tokens, request count, and mean cache rate for that Stage.
4. IF request-level telemetry data is available for the run, THEN THE Token_Report SHALL include both the raw Telemetry_Record array and the aggregated per-Stage summary.
5. IF a prior `token_report.json` file exists in the same output directory at the time of report generation, THEN THE Pipeline SHALL include a delta comparison showing the change in overall cache rate, change in average uncached input tokens per request, and change in total tokens relative to that prior report.
6. IF no token usage data is available for a run, THEN THE Pipeline SHALL write a `token_report.json` containing a status field indicating that telemetry was unavailable rather than producing empty or zero-valued metric fields.
