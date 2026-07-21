# Brief: multiagent-extraction

## Problem
A single LLM extractor is a single point of failure: when it is confidently wrong,
nothing downstream disagrees with it. Researchers doing systematic review need to
know which extracted values are *defensible* — supported by cited evidence,
corroborated by an independent rater, and survivable under adversarial challenge —
and today EviTrace can tell them none of that. Every accepted field carries the
same unearned credibility.

## Current State
- One extraction pass exists. `src/agents/` holds a single provider (`openai/`:
  `api_client.py`, `prompts.py`, `telemetry.py`) plus `validator.py`. There is no
  second extractor, no verifier, no answer adjudicator.
- **Multiagent R12 (targeted extraction) partially exists.** Chunks already receive
  per-chunk evidence packages rather than the full PDF
  (`evidence_index.build_chunk_evidence_package` → `pdf_processor.process_pdf`),
  and outputs are already compact and locally validated against the expected
  `field_index` set, key schema, confidence enum, and `loc` ID membership. What is
  missing from R12 is packs built from *routes* (supplied by `evidence-routing`)
  and mandatory evidence-ID citation for every non-not-reported value (R12.5).
- **`RepairRetryLoop` in `src/pipeline/pdf_processor.py:471` is NOT multiagent R18.**
  It repairs *malformed output* — parse and schema-validation failures — by
  re-prompting with a targeted repair message. Multiagent R18's Agent 3 repairs
  *wrong answers*: schema-valid output that QC or the verifier judged unsupported,
  contradicted, or incomplete. These are different triggers, different inputs, and
  different action sets (`revised|kept_original|marked_not_reported|manual_review`,
  R18.2). Do not conflate them; the existing loop stays as the malformed-output
  layer beneath the new one.
- `src/pipeline/orchestrator.py` runs *chunk* concurrency, not *agent* concurrency —
  there is no stage graph over 1A/1B/1c/3.
- QC exists (`src/quality_control/`) but validates parser branches, not extracted
  answers against cited evidence (R14.3–R14.6).

## Desired Outcome
Each field's final value is the output of an auditable decision: targeted
extraction from a route-derived pack (R12), a blind second extraction where policy
requires it (R13), deterministic extraction QC including quote-to-evidence fuzzy
matching (R14), verifier adjudication of support status (R16.3), a local
adjudicator that accepts / repairs / escalates using evidence validity, agreement,
and criticality (R17), and a targeted repair agent for the residue (R18) — with the
decision rule, inputs, and provenance stored for every field (R17.7).

## Approach
Sequential gated stages with deterministic gates between every LLM call: 1A always,
1B by policy (calibration = all fields, production = critical/low-confidence/
parser-risky/low-agreement/random QA, R13.1–R13.2), then local extraction QC, then
1c and 3 only on fields the gates flag. Agent 1B is blind to 1A (R13.3) and should
vary framing/snippet order/model to decorrelate errors (R13.4). Evidence validity
is never overridden by agent agreement (R15.8, R17.5).

## Scope
- **In**: route-fed targeted extraction (R12), blind second extractor and its
  sampling policy (R13), extraction QC checks and escalation eligibility (R14),
  Agent 1c verifier with the seven verdict states (R16), local answer adjudication
  (R17), Agent 3 answer repair plus post-repair re-QC (R18).
- **Out**: agreement statistic *computation* (R15 — owned by `agreement-statistics`;
  this spec consumes its outputs and applies the escalation policy in R15.9);
  route selection (R7–R11, owned by `evidence-routing`); benchmark comparison and
  ablation (R24–R25, owned by `evaluation-harness`); reviewer UI (R19, R21).

## Boundary Candidates
- Extraction agents (1A, 1B) vs. deterministic extraction QC.
- Verification (1c, LLM judgement) vs. adjudication (local, deterministic rules).
- Answer repair (R18) vs. the existing malformed-output `RepairRetryLoop`.
- Sampling/escalation policy configuration vs. the agents that policy invokes.

## Out of Boundary
- Computing Cohen's/weighted kappa, Gwet's AC1, Krippendorff's alpha.
- Choosing evidence locations, or re-ranking the evidence index.
- Human review UX; only the `manual_review` terminal state is produced here.

## Upstream / Downstream
- **Upstream**: `evidence-routing` (extraction packs, route traces, parser risk
  flags), `agreement-statistics` (normalized comparison and kappa outputs),
  `provenance-core` (evidence node identity, decision provenance).
- **Downstream**: `evaluation-harness` (needs each agent independently
  disableable, R25.3–R25.6), `reviewer-ui` (renders verification status, review
  queue), `provenance-audit-export` (consumes per-field decision records).

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` R-LLM-3 (bounded automatic schema repair) — Agent 3
  is the semantic tier above it; R-GOV-2/R-GOV-3 review-queue routing is the
  consumer of the `manual_review` state. This spec covers the part of the
  multiagent doc that `xtrace-toolkit` does not.
- **Adjacent**: `agreement-statistics`, `evidence-routing`, `cost-and-run-reporting`
  (per-agent token/cost attribution), `risk-remediation` (final-output writes must
  actually land before any of this is verifiable).

## Constraints
- `_shared_paper_prefix` byte-stability: four new agent roles multiply prompt
  variants: each role needs its own stable, cache-friendly prefix, and none may
  inject filenames, timestamps, chunk numbers, or run IDs.
- Cost is the dominant design constraint — 1A+1B+1c+3 is up to four full LLM passes
  per field. Gating (R13.2, R16.1, R18.1) is a requirement, not an optimization.
- Dependency direction: `agents` must not import `quality_control`/`pipeline`/
  `pdf_extractor`; extraction QC and adjudication live outside `agents`.
- Prompt templates must be versioned (R27.2) so agreement and ablation numbers
  remain attributable across runs.
