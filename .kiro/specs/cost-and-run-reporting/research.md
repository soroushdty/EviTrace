# Research & Design Decisions — cost-and-run-reporting

## Summary

- **Feature**: `cost-and-run-reporting`
- **Discovery Scope**: Extension (integration-focused light discovery over an existing, working pipeline)
- **Key Findings**:
  1. Telemetry exists but is thinner than the brief implies. `TelemetryRecord` (`src/agents/openai/telemetry.py:59-117`) carries no `document_id`, no duration, no attempt number, and no API-error field. `pdf_name` reaches `extract_chunk` but is never forwarded into `_record_telemetry` (`api_client.py:301`), so nothing on a record identifies the paper. The `domain_group` parameter exists on `extract_chunk` but **no caller ever passes it**.
  2. Failed attempts are invisible. `_record_telemetry` is invoked only on the success path (`api_client.py:425`, `:523`). Both retry loops — `_call_api_with_retries` (`:219`) and the duplicated inline loop inside `extract_chunk` (`:516-568`) — swallow the failed attempt entirely. This is a live violation of `xtrace-toolkit` R-LLM-4.
  3. There is no cost anything. No price table, no currency, no per-call estimate anywhere under `src/`.
  4. `compute_identity()` (`manifest.py:104`) produces exactly the hashes a reproducibility manifest needs (pdf content, config, extraction map, model id, schema version) — but **no call site persists them**, and `load_manifest_with_identity_check()` (`manifest.py:239`) has **no caller**. The reproducibility work is largely a matter of wiring existing, tested machinery rather than writing new hashing.
  5. Resume is already broken for a reason unrelated to overwriting: `OUTPUT_DIR` is a fresh `run_<dd-mm-yy_HH-MM-SS>` folder computed at import (`path_utils.py:60,87`), so a manifest entry marked `complete` by a previous run can never find its output file and the document is silently reprocessed.
  6. An end-of-run hook already exists at `orchestrator.py:157-204` (cache diagnostics, stage summary logs, `generate_token_report`, optional CSV). This is the natural insertion point; no new lifecycle needs inventing.
  7. `cache_diagnostics.threshold` is present in `configs/config.yaml:86` and registered in `_ALL_KNOWN_TOP_LEVEL_KEYS`, but is **never read** — `check_cache_diagnostics()` always uses its hardcoded `50.0` default. Requirement 6.5 fixes this.
  8. There is no audit-log facility in `src/utils/logging_utils.py`. The closest surrogate today is the `telemetry_records` array inside `token_report.json`.

## Research Log

### Telemetry capture surface and the dependency-direction constraint

- **Context**: The brief forbids the price table from leaking into the API client, and `tests/test_dependency_directions.py` forbids `agents` from importing `pipeline`.
- **Sources Consulted**: `src/agents/openai/telemetry.py`, `src/agents/openai/api_client.py`, `tests/test_dependency_directions.py`, `.kiro/steering/testing.md`.
- **Findings**: `telemetry.py` imports only `hashlib`, `threading`, `dataclasses`, and `utils.logging_utils`. `api_client.py` imports `utils.config_utils` and `utils.logging_utils`. `utils` is therefore the only shared substrate between `agents` and `pipeline`.
- **Implications**: Cost derivation must be a pure projection performed in `src/pipeline/`, reading `TelemetryRecord` objects the collector already exposes via `all_records()`. `agents` records *facts* (tokens, duration, attempt, outcome); `pipeline` applies *prices*. No new cross-package import is introduced in either direction.

### Where per-call latency can be measured

- **Context**: R23.1 requires latency; nothing measures it today.
- **Findings**: There are exactly two `await _client.responses.create(...)` sites — one inside `_call_api_with_retries` (used only by `warm_pdf_cache`) and one inside `extract_chunk`'s duplicated inline loop. Both are already inside a per-attempt `try` block with an `attempt` loop variable in scope.
- **Implications**: A `time.perf_counter()` pair around each awaited call, plus one shared recording helper invoked from the success path and both exception paths, covers every model call in the codebase. The duplicated retry loop is not refactored away — that would be gold-plating and would risk the retry semantics — but both loops call the same recording helper so failure accounting cannot diverge.

### Local (non-model) stage elapsed time

- **Context**: Requirement 4.5 requires elapsed time per stage including stages that issue no model calls, and Requirement 4.6 requires zero-cost stages to appear rather than be omitted.
- **Findings**: There is no timing instrumentation anywhere except `main.py:77` (`_t_start`) and the "Time elapsed" line printed by `extraction_report._print_summary`. Local work (`build_qc_bundle`, evidence indexing) is invoked from `orchestrator._bounded` and `pdf_processor.process_pdf`.
- **Implications**: Stage timing and the Run_Audit_Log are the same measurement. A stage span emits a `stage_started` and a `stage_completed` audit event; elapsed time is derived from the pair. This collapses two requirement areas (4.5 and 11.1) onto one mechanism instead of two parallel timers.

### Reproducibility inputs available without new dependencies

- **Context**: Requirement 8.2 needs code revision, environment, parser/tool versions, prompt versions, schema version.
- **Sources Consulted**: `src/pipeline/manifest.py`, `src/agents/openai/prompts.py`, `src/agents/validator.py`, `requirements.txt`, Python 3.12 stdlib `importlib.metadata`.
- **Findings**: Code revision is obtainable from `git rev-parse HEAD` plus `git status --porcelain` via `subprocess`; both may legitimately fail (no git, no checkout). Distribution versions are obtainable from `importlib.metadata.version(<dist-name>)` **without importing the package**, which is essential because `paddleocr`, `torch`, and `faiss` must never be imported at module level. Prompt version identifiers already exist as `PROMPT_VERSION`/`prompt_cache_key_prefix`, and prompt content hashes are computable from `get_system_prompt()` and the prompt-builder module source. GROBID's version is a runtime property of an external service, not a package.
- **Implications**: Every manifest element must be individually degradable (Requirement 8.5). The manifest records `{"value": ..., "status": "available"}` or `{"value": null, "status": "unavailable", "reason": ...}` uniformly, so a missing git checkout never blocks the artifact.

### Prompt-cache stability under telemetry extension

- **Context**: Hard constraint from the brief, the roadmap, and `CLAUDE.md`.
- **Findings**: `_shared_paper_prefix(source_package)` (`prompts.py:27-45`) derives entirely from `source_package`. Telemetry is computed *after* the response returns and never feeds prompt construction. The `prompt_cache_key` request parameter is built by `paper_cache_key()` from a hash of `source_package` only.
- **Implications**: No change in this spec touches prompt construction. Requirement 6.1 is therefore enforced as a *guard*, not a change: an existing-behavior preservation test asserting the prefix is byte-identical before and after this feature's changes, and an AST/text check that no pricing, run-id, or timestamp symbol appears in `prompts.py`.

### Existing artifact-writing conventions

- **Findings**: `token_report._atomic_write_json` (`:162`) and `manifest.save_manifest` (`:293`) both use tmp-file + `os.replace`. `path_utils` centralises artifact paths (`FLAGGED_FIELDS_FILE`, `MANIFEST_FILE`).
- **Implications**: New artifacts reuse the same atomic-write pattern and declare their paths in `path_utils`, satisfying Requirement 5.6 with an established mechanism rather than a new one.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Decision |
|--------|-------------|-----------|---------------------|----------|
| Cost computed inside `api_client` | Attach a cost figure to each record at call time | One pass; cost visible in logs immediately | Forces the price table into `agents`; violates the brief's boundary; makes re-pricing an old run impossible | Rejected |
| Cost as a post-hoc projection in `pipeline` | `agents` records tokens/duration/outcome; `pipeline` applies the price table at report time | Preserves dependency direction; re-pricing a run is a config edit; price table stays operator-facing | Cost is not visible mid-run | **Selected** |
| Separate `src/reporting/` package | New top-level package for all run artifacts | Clean namespace | Adds a package and new dependency-direction rules for four small modules that only ever serve the pipeline | Rejected (simplification) |
| New audit-log subsystem in `utils` | Generic append-only event log | Reusable by future specs | Speculative; `provenance-core` owns the general graph. A run-scoped recorder is the smallest thing that satisfies 11.1-11.7 | Rejected in favour of a run-scoped `RunRecorder` in `pipeline` |

## Design Decisions

### Decision: Cost is a projection, telemetry is the fact table

- **Context**: R23.1 asks for a cost estimate "logged" per call, but the price table must not enter `agents`.
- **Selected Approach**: `TelemetryRecord` gains only observable facts (`document_id`, `duration_seconds`, `attempt`, `total_attempts`, `outcome`, `error_class`, `error_detail`). `src/pipeline/pricing.py` maps `(model, token class) -> unit price`; `src/pipeline/cost_report.py` folds records into per-call/per-document/per-stage/run aggregates.
- **Rationale**: Keeps the AST dependency tests green, makes an old run re-priceable when prices change, and keeps the price table where operators edit configuration.
- **Trade-offs**: Cost is unavailable until the run ends. Accepted: the brief explicitly excludes budget aborts.

### Decision: One stage-span mechanism serves timing, the audit log, and skip reporting

- **Context**: Requirements 4.5, 7.3, 10.2, 10.4, 10.5, 11.1-11.5 all describe "something happened to a stage".
- **Selected Approach**: A single `RunRecorder` exposes `stage_span(stage, document_id)`, `record_skip(...)`, `record_rerun(...)`, `record_failure(...)`. Elapsed time is derived from the started/completed event pair; the Cost_Report reads stage elapsed time from the recorder rather than keeping its own timers.
- **Rationale**: Generalization lens — five requirement areas are one underlying capability. Two parallel timing systems would inevitably disagree.
- **Trade-offs**: The cost report gains a dependency on the recorder. Acceptable: both are run-scoped and constructed together in the orchestrator.

### Decision: Resume keys on the manifest-recorded output path, not on the current run folder

- **Context**: Requirement 10.1 is unimplementable as written today because `OUTPUT_DIR` is fresh per run.
- **Selected Approach**: On completion, the manifest entry persists the `ManifestIdentity` fields **and** the resolved absolute output path. On resume, `load_manifest_with_identity_check()` — which already exists and is currently unused — validates the entry against the freshly computed identity, and the completeness check resolves the recorded path rather than `OUTPUT_DIR / f"{pdf}.extracted.json"`.
- **Rationale**: Build-vs-adopt lens: the identity comparison and staleness logic already exist and are tested (`tests/src/pipeline/test_manifest_identity.py`). Only the wiring and the persisted path are new.
- **Trade-offs**: Manifest entries grow. Mitigated by writing identity under a single nested `identity` key so existing readers of `status` are unaffected.

### Decision: Optional stages are declared in a registry, not inferred

- **Context**: Requirement 7.5 requires refusing to disable a stage that is required to produce output.
- **Selected Approach**: `src/pipeline/stage_control.py` declares `REQUIRED_STAGES = {"extraction_chunk", "synthesis"}` and `OPTIONAL_STAGES = {"cache_warmup", "validation_repair"}`. `cache_warmup` subsumes the existing `enable_prewarm` setting rather than competing with it: the existing setting remains authoritative and the stage registry reads it.
- **Rationale**: An explicit registry makes 7.5 testable and gives `evaluation-harness` a named hook. Inference from call sites would be untestable.
- **Trade-offs**: A future stage must register itself. Recorded as a revalidation trigger.

### Decision: Cost report reports unknown stages without configuration

- **Context**: Requirement 4.9, plus multiagent R23.5's stage list (parsing, routing, verification, repair) which names stages that do not exist yet.
- **Selected Approach**: Aggregation groups by whatever `stage` string appears in telemetry or in recorder events, with no allowlist. A documented stable ordering (known stages first in declared order, then unknown stages alphabetically) keeps output diffable.
- **Rationale**: `evidence-routing` and `multiagent-extraction` will add `routing` and `verification` stages; neither should require editing this feature.

### Decision: Manifest elements are individually degradable

- **Selected Approach**: Every manifest element is `{"value": <any>, "status": "available" | "unavailable", "reason": <string|null>}`.
- **Rationale**: Satisfies 8.5 uniformly and makes the canonical-comparison test in 12.3 trivial — elements marked unavailable are excluded from the comparison by status, not by an ad-hoc field list.

## Risks & Mitigations

- **Adding fields to `TelemetryRecord` changes `token_report.json`'s `telemetry_records` entries** — Mitigated by making every new field optional with a default, leaving all existing keys and the `TokenReport` top-level/`per_stage` schemas untouched, and by keeping `tests/src/pipeline/test_token_report*.py` passing unmodified.
- **Failure records could double-count tokens** — A failed attempt that reports usage is real spend and must count; a retried call that later succeeds must not have its successful attempt counted twice. Mitigated by `outcome` being a closed vocabulary and by the sum invariant in Requirement 4.8 being asserted as a property test.
- **`subprocess` git calls in a sandboxed or non-git environment** — Mitigated by the degradable-element contract and a hard timeout on the subprocess call.
- **Per-output provenance key could break final-output schema validation** — `risk-remediation` Requirement 1 is actively repairing final-output validation. Mitigated by adding provenance under a single new top-level key in the per-document output envelope, leaving the validated field records untouched, and by an explicit implementation task to confirm the envelope-level schema (if any) accepts it.
- **Price drift makes reported cost wrong without anyone noticing** — Mitigated by the mandatory `effective_date` plus staleness horizon (Requirement 3.7) and by the `cost_basis: "estimate"` label on every artifact.

## References

- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` §Requirement 23, §Requirement 27 — source acceptance criteria.
- `.kiro/specs/xtrace-toolkit/requirements.md` R-X-2, R-LLM-4 — de-duplicated into Requirements 8 and 2 respectively.
- `.kiro/specs/archive/token-efficient-extraction/design.md` — the telemetry, token-report, and token-budget contracts this feature extends.
- `.kiro/steering/config.md` — env > yaml > default rule and `_ALL_KNOWN_TOP_LEVEL_KEYS` registration requirement.
- `.kiro/steering/testing.md` — mocking rules (no real OpenAI calls), property-test conventions, dependency-direction enforcement.
