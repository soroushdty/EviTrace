# Brief: cost-and-run-reporting

## Problem
Users running EviTrace over a corpus cannot answer the two questions that decide whether the tool is usable: what did this run cost, and what exactly produced these outputs. Token counts exist but are not money, so there is no per-document or per-project budget signal, no view of which stage is expensive, and no basis for turning a stage off. Separately, outputs carry no self-describing record of the configuration, prompt versions, and model versions behind them — results cannot be compared across experiments or defended in a methods section.

## Current State
- `src/agents/openai/telemetry.py` records per-request `TelemetryRecord`s (input/output/cached/uncached tokens, `stage`, model, `PromptFingerprint` with `stable_prefix_hash` + `prompt_version`) and aggregates them into `StageSummary`. It records **no latency and no retry count** — grepping `latency|retry_count|elapsed` under `src/agents/openai/` returns nothing.
- `src/pipeline/token_report.py` writes `token_report.json` per run: totals, per-stage breakdown, top-5 most expensive requests by tokens, raw records, optional delta vs. a prior report. `src/pipeline/token_budget.py` enforces token ceilings.
- **Cost accounting does not exist.** Grepping `src/` for `cost|usd|pric` returns only docstring prose (`grobid_manager.py`, `extraction_pipeline.py`) and the unrelated `"cost_reduction"` literal in `quality_control/checks/task_quality.py`. No price table, no currency, no per-call cost estimate.
- Reproducibility metadata is partial and internal: `compute_identity()` (`src/pipeline/manifest.py:104`) hashes PDF content, config, `extraction_map.json`, model id, schema version — for staleness detection, not as an exported run manifest. No git commit, environment capture, parser versions, or run-timestamp artifact.
- `src/pipeline/extraction_report.py::generate_flagged_fields_report()` writes `outputs/flagged_fields.csv` (`FLAGGED_FIELDS_FILE`, `path_utils.py:93`).

## Desired Outcome
Every API call logs model, stage, document ID, input/output/cached tokens, latency, and a cost estimate (multiagent R23.1); failures and retries log error, retry count, and incremental token/cost impact (R23.3); per-document and project-level token summaries exist (R23.4); stage-level cost summaries cover parsing, routing, extraction, verification, repair, and total (R23.5); nonessential model calls can be disabled to reduce cost (R23.6). Each run emits a manifest capturing configuration, schema version, parser versions, model names, prompt versions, and timestamp (R27.1–R27.2); model version is preserved per output (R27.3); resumed runs never overwrite completed outputs unless configured (R27.4); skipped or rerun stages are recorded in the audit log (R27.5).

## Approach
Extend the telemetry and report machinery that already exists rather than building a parallel one: add latency and retry fields to `TelemetryRecord`, add a declarative config-driven price table (per model, per token class, cached vs. uncached) so price changes are config edits not code edits, and derive cost as a projection over existing records. Run metadata reuses `compute_identity()`'s hashes and promotes them into an exported run manifest. Cost is always an *estimate*, labelled as such, with the price-table version recorded — never presented as a billed figure.

## Scope
- **In**: latency, retry, and error capture in telemetry; a versioned, configurable price table; per-call, per-document, per-stage, and per-project cost estimation; cost columns in `token_report.json` or a sibling cost report; stage-disable switches for nonessential calls; a run reproducibility manifest (config snapshot, resolved model names, prompt versions, schema version, parser/tool versions, git commit, environment, timestamp); no-overwrite-on-resume semantics; audit-log entries for skipped or rerun stages; ownership of multiagent R23.2 — stable prompt structures and configured cache keys wherever prompt caching is supported, asserted and reported here as part of cache logging rather than left as an unowned cross-spec constraint; and multiagent R27.6 — deterministic local processing where possible (fixed seeds, stable ordering, pinned tool versions), reported through the run reproducibility manifest.
- **Out**: cost *enforcement* or hard budget aborts beyond what `token_budget.py` already does; any re-litigation of thresholds set by the archived `token-efficient-extraction` spec; cost dashboards or visualization; billing integration and provider usage-API reconciliation; the full audit package (multiagent R22), owned by `provenance-audit-export`.

## Boundary Candidates
- Telemetry *capture* (inside `src/agents/openai/`) vs. cost *derivation and reporting* (inside `src/pipeline/`) — the price table must not leak into the API client.
- Run manifest generation vs. the provenance graph — the manifest is run-scoped; per-claim derivation lineage belongs to `provenance-core`.
- Prompt version identity (already in `PromptFingerprint`) vs. prompt templates as first-class versioned, storable artifacts (R27.2).

## Out of Boundary
- Evidence-level or claim-level lineage, chain validation, and tamper-evidence.
- Ablation orchestration that toggles agents for evaluation (multiagent R25) — `evaluation-harness` consumes the stage-disable switches defined here but owns the experiment design.
- Any change to `_shared_paper_prefix` content or ordering.

## Upstream / Downstream
- **Upstream**: `src/agents/openai/telemetry.py`, `src/agents/openai/api_client.py`, `src/pipeline/token_report.py`, `token_budget.py`, `src/pipeline/manifest.py`, `src/utils/config_utils.py`.
- **Downstream**: `evaluation-harness` (cost and runtime per ablation, multiagent R25.7 and success metric 9), `provenance-audit-export` (embeds cost report and run manifest), `reviewer-ui` (displays run/cost summaries), `multiagent-extraction` (per-agent cost attribution).

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` R-X-2 (reproducibility manifest: git commit, environment, seeds, resolved config, per-artifact hashes) — this spec is that requirement's implementation home and must de-duplicate against it, not restate it. Also serves R-LLM-4 (no silently dropped failed call) via retry/error logging.
- **Adjacent**: `provenance-core` and `provenance-audit-export` — the run manifest must be a projection consumable by the graph, never a competing store; archived `token-efficient-extraction`.

## Constraints
`_shared_paper_prefix` byte-stability must survive every change to the LLM call path: telemetry or cost hooks must not inject run IDs, timestamps, or cost metadata into the cached prefix. New top-level YAML keys (price table, stage toggles) must register in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`, with env > yaml > default override order. Dependency direction: `agents` must not import `pipeline`, so cost derivation cannot live behind the API client. No real OpenAI calls in tests. Provider prices drift — the price table must be versioned and its staleness surfaced, never silently assumed current.
