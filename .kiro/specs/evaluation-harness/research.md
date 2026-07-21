# Research & Design Decisions — evaluation-harness

## Summary

- **Feature**: `evaluation-harness`
- **Discovery Scope**: Complex Integration — a new package that consumes the published contracts of five upstream specs and executes the existing pipeline unchanged.
- **Key Findings**:
  - Every ablation the brief asks for is already reachable as configuration. `MultiagentConfig` exposes `second_extractor_enabled`, `verifier_enabled`, `repair_enabled` and a feature-level `enabled`; `RoutingConfig` exposes `enabled` and the counterfactual model/bounds; `cost-and-run-reporting` ships a `stage_control` registry with `REQUIRED_STAGES`/`OPTIONAL_STAGES`. The ablation runner therefore needs **no** new pipeline switch for R25.3–R25.6 — only for the two full-document baselines.
  - The **only** genuine pipeline modification this feature requires is a package-source override for the baselines. `process_pdf` builds one paper-level evidence package shared by every chunk; a full-document baseline needs that package built from full text instead of the ranked bundle. Everything else is read-only.
  - Metric availability is the dominant design force, not metric computation. Five of the twelve success metrics (6, 7, 8, 9, 10, 12) depend on specs that are either downstream (`reviewer-ui`) or sibling (`provenance-audit-export`, `agreement-statistics`, `cost-and-run-reporting`). A catalogue whose entries carry availability plus a named reason is the only honest shape.
  - `src/text_processing/` already ships everything Tier 1 and Tier 2 of the semantic cascade need — `WhitespaceNormalizer`, `AggressiveNormalizer`, `UnicodeNormalizer`, `SimpleWordTokenizer` — and `SemanticMatcher`/`EmbeddingProcessor` provide a lazily-imported Tier 3. No new dependency is required, and the heavy path stays optional and off by default.
  - `agreement-statistics` explicitly flags its parser-risk thresholds as uncalibrated and names this spec as the calibrator (`agreement-statistics/research.md:136-137, 153`). The correct resolution is a *recommendation* output here, not a threshold write — the owning spec keeps the decision.

## Research Log

### Which ablation switches already exist

- **Context**: R25.3–R25.6 demand per-agent disablement. If any agent lacked a switch, this spec would have to modify the agent — which its own boundary forbids.
- **Sources Consulted**: `.kiro/specs/multiagent-extraction/design.md` (`MultiagentConfig`, §Config Layer; Goals bullet "Each agent is independently disableable, so `evaluation-harness` can ablate the stage graph without editing code"), `.kiro/specs/evidence-routing/design.md` (`RoutingConfig`), `.kiro/specs/cost-and-run-reporting/design.md` (`stage_control`), `src/utils/config_utils.py`.
- **Findings**:
  - Agent 0c (counterfactual locator): governed by `evidence_routing` config; `max_counterfactual_calls_per_document` and `counterfactual_model` exist, and routing as a whole has `enabled`. A dedicated counterfactual disable is expressed by setting the per-document counterfactual bound to zero, which the routing design already treats as a recorded suppression rather than a failure.
  - Agent 1B, 1c, 3: `second_extractor_enabled`, `verifier_enabled`, `repair_enabled` — each independent, each recorded on `MultiagentResult.effective_config`, and 14.4 of that spec guarantees the remaining stages still complete.
  - Stage-level optionality (`cache_warmup`, `validation_repair`) is owned by `stage_control` and is the declared hook for this spec.
- **Implications**: The `AblationMatrix` is a table of configuration overlays, not code. R10.3 (error when a switch is not exposed) becomes a real, testable guard rather than a formality, because the matrix validates each overlay key against the loaded configuration surface before executing.

### What a full-document baseline actually costs architecturally

- **Context**: R25.1–R25.2 require full-document extraction. The current pipeline never sends full text.
- **Sources Consulted**: `src/pipeline/pdf_processor.py` (`process_pdf`), `src/agents/openai/prompts.py` (`_shared_paper_prefix`, `build_cache_warmup_message`), `src/pipeline/evidence_index.py`, `.kiro/specs/multiagent-extraction/design.md` §Existing Architecture Analysis, CLAUDE.md prompt-cache principle.
- **Findings**: `_shared_paper_prefix` wraps exactly one variable — the serialized paper package. The cache-stability rule is *byte-identity across warmup, chunks, and synthesis for the same document*, not any constraint on the package's content. A full-text package built once per document therefore satisfies the rule unchanged; what it breaks is cache *economics*, not cache *correctness*.
- **Implications**: The baseline is implemented as an injected evidence-package builder, not as a fork of `process_pdf`. `process_pdf` gains one optional `evidence_package_builder` parameter whose default reproduces today's behaviour byte-for-byte. This is declared in the design as the only pipeline modification *this spec* makes — `provenance-audit-export` and `cost-and-run-reporting` also modify the pipeline, injecting an unrelated `AuditPackageBuilder`, which is why the parameter name is deliberately distinct. It is guarded by a regression test asserting byte-identical package bytes when the override is absent.

### Semantic equivalence: what the literature-standard options are and why an LLM judge was rejected

- **Context**: Open Question 7 blocks the `semantic` comparison mode and therefore metrics 1–5 on every free-text field.
- **Sources Consulted**: `src/text_processing/{normalizers,tokenizers,matchers,embedding}.py`; `.kiro/specs/agreement-statistics/design.md` §ComparisonNormalizer (the project's existing normalization-into-comparison-units contract); the standing product boundary "guaranteed extraction correctness without human validation" in `.kiro/steering/roadmap.md`.
- **Findings**:
  - The project already has a two-pass normalization ladder (whitespace → aggressive) with declared scores, used by `LexicalMatcher`. Reusing that ladder makes the harness's Tier 1/Tier 2 consistent with how the rest of the system already decides "same text".
  - `SemanticMatcher` requires a FAISS index and an `embed_fn`; both are lazily imported and optional throughout the codebase. Making Tier 3 default-off keeps the fast suite free of `torch`/`faiss`.
  - An LLM-as-judge tier was considered and rejected. It would (a) make the evaluation of an LLM extraction system depend on an unvalidated LLM judgment, which cannot be reported as independent evidence in a methodology paper; (b) introduce cost and non-determinism into the measurement layer; (c) require routing evaluation content through the privacy gateway, which is out of this spec's dependency set.
  - Numeric tokens are the failure mode that pure similarity handles worst: "median follow-up 12 months" and "median follow-up 21 months" are lexically near-identical and semantically opposite. A hard numeric-token equality gate is therefore placed *above* every similarity score.
- **Implications**: The cascade is deterministic in its default configuration, which satisfies R3.8 without carving out the semantic mode. The undetermined band plus bounded metric reporting is what makes the design honest where the cascade cannot decide.

### Human review level required for a publishable validation claim

- **Context**: Open Question 8 blocks the Stage 2 design and the reference-standard schema.
- **Sources Consulted**: `.kiro/specs/evaluation-harness/brief.md` §Two-Stage Evaluation Plan; the standing product boundaries in `.kiro/steering/roadmap.md`; `agreement-statistics` `min_units` default of 10 and `MeasurementLevel`/`UndefinedReason` vocabulary.
- **Findings**: The dominant risk is not statistical, it is presentational — a single-annotator development table being reported as validation. That risk is eliminated only if the review level is a *required, recorded, and enforced* attribute of the reference standard rather than a note in a methods section.
- **Implications**: Three levels (A/B/C), enforcement at grading time, and a `validation_grade` stamp carried onto every metric result and every export. The minimum counts (20 documents, 100 adjudicated comparison units per reported field group) are configurable and recorded, deliberately mirroring how `agreement-statistics` treats `min_units`: a declared, recorded, recalibratable bar rather than a hidden constant.

### Which benchmark datasets are appropriate first

- **Context**: Open Question 9 blocks R24.1 because the importer's shape depends on the chosen source.
- **Findings**: Any answer that names one dataset makes the importer wrong the first time a second dataset appears. The stable answer is a selection *rule* plus a priority order, combined with an importer that takes a declared column-to-field mapping. The PHI boundary and licence redistributability are hard filters, not preferences: fixtures live in the repository, so a non-redistributable or patient-level source is disqualified at the repository level, not at the analysis level.
- **Implications**: R1 is written in terms of a declared mapping and a rejected-row path. R1.8 makes the PHI filter a first-class import rejection so the boundary is enforced in code rather than in prose.

### Venue ordering and its architectural consequence

- **Context**: Open Question 10 determines which manuscript tables are built first.
- **Findings**: Stage 1 is self-contained — it needs no reference standard and no review surface, so it is reachable the day `multiagent-extraction` ships. Stage 2's prospective half is blocked on `reviewer-ui`, and its retrospective half is blocked on benchmark annotation. Choosing the benchmark paper first would put the whole first deliverable behind two external blockers.
- **Implications**: Methodology paper first. Stage 1 becomes the default export profile (R14.2), Stage 2 an opt-in profile. This is a build-ordering decision as much as a publishing one and is recorded as a revalidation trigger.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Configuration-overlay matrix over the shipped pipeline | Each ablation is a validated overlay applied to the loaded config; the runner calls the existing orchestrator once per configuration | The evaluated system is the shipped system; no second extraction path; ablations stay correct as the pipeline evolves | Requires every ablated component to expose a switch; a missing switch becomes a hard error | **Selected** |
| Forked evaluation pipeline | A parallel orchestrator built for evaluation | Full control over instrumentation | Measures a system nobody ships; drifts silently; explicitly forbidden by the brief | Rejected |
| Post-hoc log analysis only | Derive everything from existing artifacts without re-running | Zero execution cost | Cannot ablate at all; R25 unreachable | Rejected |
| Pluggable comparison modes behind one registry | Six built-in modes registered under a common contract, extensible without editing existing modes | Satisfies R3.2 directly; each mode independently testable; mirrors the project's existing ABC-plus-registry convention | One more indirection than a match statement | **Selected** |
| Single similarity score for free text | One threshold over one similarity function | Simple | Cannot express "numbers differ" or "cannot decide"; produces confidently wrong free-text accuracy | Rejected in favour of the tiered cascade |

## Design Decisions

### Decision: Metric availability is part of the metric type, not a side channel

- **Context**: Six of twelve success metrics depend on specs that do not exist yet or are siblings, and R6.1 requires all twelve presented as one catalogue.
- **Alternatives Considered**:
  1. Omit unavailable metrics from the catalogue — the reader cannot tell "not computed" from "not applicable".
  2. Report unavailable metrics as zero — actively misleading in a manuscript.
  3. Carry availability and a named reason on every metric entry.
- **Selected Approach**: Every metric value is a record carrying `value`, `availability`, `unavailable_reason`, `bounds`, and the counts that produced it. Unavailability reasons are a closed vocabulary (`review_surface_absent`, `audit_state_absent`, `parser_risk_flags_absent`, `cost_artifact_absent`, `agreement_undefined`, `zero_denominator`, `not_applicable_to_stage`, `incomparable_identity`).
- **Rationale**: Makes R5.8, R6.4, R6.5, R6.6, R7.4, R8.3, and R14.5 one mechanism instead of seven special cases.
- **Trade-offs**: Every consumer must handle the envelope; a bare float is never returned.
- **Follow-up**: Extend the reason vocabulary when `provenance-audit-export` and `reviewer-ui` land, rather than loosening it to free text.

### Decision: The semantic cascade is deterministic by default; the embedding screen can only widen doubt

- **Context**: Open Question 7; R3.8 requires deterministic comparison; R4.4–R4.6 constrain the screen.
- **Alternatives Considered**:
  1. Embedding similarity as a first-class match decider.
  2. LLM judge.
  3. Deterministic tiers with an optional screen that only produces `undetermined`.
- **Selected Approach**: (3). Tier 1 normalization equality → match. Tier 2 content-token overlap ≥ threshold **and** identical numeric token multisets → match; differing numeric tokens → immediate mismatch regardless of score. Tier 3, off by default, may only move a near-threshold pair into `undetermined`. Critical fields bypass Tier 3 entirely and go to adjudication.
- **Rationale**: Preserves determinism in the shipped default, keeps `torch`/`faiss` out of the fast test suite, and prevents the single worst free-text failure mode (matching values whose numbers differ).
- **Trade-offs**: More undetermined pairs than a permissive similarity threshold would produce — deliberately, since an undetermined pair is reported as a bound rather than guessed.
- **Follow-up**: Calibrate the Tier 2 threshold against the Level C benchmark once it exists; the threshold is recorded on every comparison so recalibration is measurable.

### Decision: Bounded metrics rather than imputed ones

- **Context**: R4.9. Undetermined pairs must not silently become correct or incorrect.
- **Selected Approach**: Every affected metric reports `point` (adjudicated only), `lower` (undetermined = mismatch), `upper` (undetermined = match), and `undetermined_count`. When the queue is empty, `lower == point == upper`.
- **Rationale**: A manuscript can quote the interval honestly; a fully adjudicated benchmark collapses it to a single number with no special-casing.
- **Trade-offs**: Three numbers per metric in the export.

### Decision: This spec's only pipeline modification is an injected evidence-package builder

- **Context**: R11.1–R11.2 need full-document extraction; R15.5 forbids behavioural change.
- **Selected Approach**: `process_pdf` accepts an optional package-builder override. Absent the override, the serialized package bytes are byte-identical to today's, asserted by a regression test. The override is supplied only by the baseline configuration.
- **Rationale**: One narrow, testable seam instead of either a fork or a scattering of `if baseline:` branches.
- **Trade-offs**: `pipeline` gains a parameter that only the harness uses. Declared as a revalidation trigger.
- **Follow-up**: If `evidence-routing` later introduces its own package-source abstraction, collapse the two rather than keeping both.

### Decision: Parser-risk recalibration is a recommendation, not a write

- **Context**: `agreement-statistics` names this spec as the calibrator of its uncalibrated risky/skip thresholds, but owns them.
- **Selected Approach**: The harness stratifies accuracy by published parser-risk state, reports the observed thresholds and the accuracy gap between strata, and emits a recommendation record. It never writes a threshold.
- **Rationale**: Keeps single ownership intact and keeps the recalibration decision auditable rather than automatic.

### Decision: Configuration identity is content-derived and comparability is enforced

- **Context**: R10.4, R12.8, R13.5, R6.8.
- **Selected Approach**: A configuration's identity is a hash over its resolved switch settings. A *comparability key* is a separate hash over the extraction schema version, prompt versions, corpus sample membership, and price-table version, read from the published run manifest. Two configurations may be differenced only when their comparability keys match; otherwise the pair is marked incomparable with a reason.
- **Rationale**: Turns "metrics are not comparable across configurations" from a caveat into an enforced precondition.

## Risks & Mitigations

- **A full ablation sweep exhausts the budget** — deterministic sampling, per-configuration and matrix cost ceilings evaluated against the published cost artifact, mandatory ceiling on baseline configurations, and a partial-matrix report instead of an all-or-nothing run.
- **Baseline configurations degrade prompt-cache economics and trip token budgets** — baselines are opt-in, isolated to their own output location, size-limited per document with recorded skips, and explicitly forbidden from relaxing the existing budget thresholds.
- **A development-grade reference standard is presented as validation** — review level is required at registration, enforced at grading, and stamped on every metric result and export; below Level C the artifact carries a not-validation-grade marking.
- **The semantic threshold silently drives free-text accuracy** — the deciding tier and its score are recorded on every comparison, the threshold is recorded on every metric, and undetermined pairs are reported as bounds.
- **Upstream specs ship later than this one** — every consumed artifact has a declared absent-path that produces an unavailable metric with a named reason, and no consumed artifact is required for the harness to start.
- **PHI enters a fixture through an imported benchmark** — declared-PHI sources are rejected at import; exports marked shareable exclude document text, evidence text, reference values, and reviewer identities; sharing suitability defaults to the most restrictive value.
- **The harness is enabled in a production run and changes its outcome** — the harness is disabled by default, writes only under its own output location, and a boundary test asserts it never overwrites an extraction output, cost artifact, run manifest, or audit artifact.

## References

- `.kiro/specs/evaluation-harness/brief.md` — problem, twelve Success Metrics, two-stage plan, four open questions.
- `.kiro/steering/roadmap.md` — open-question ownership, standing product boundaries, cross-cutting NFRs, the R24.6 caveat.
- `.kiro/specs/multiagent-extraction/{requirements,design}.md` — per-agent enable switches, `FieldDecisionRecord`, `AnswerVerdict`, `MultiagentResult`.
- `.kiro/specs/evidence-routing/design.md` — `RoutingConfig`, route traces, counterfactual bounds.
- `.kiro/specs/agreement-statistics/design.md` — `RaterFieldOutput` input contract, published `metrics_hierarchy` shapes, parser-risk thresholds flagged for recalibration.
- `.kiro/specs/cost-and-run-reporting/design.md` — `cost_report.json`, `run_manifest.json`, `stage_control` registry.
- `.kiro/specs/provenance-audit-export/requirements.md` — audit-completeness state this spec consumes.
- `.kiro/specs/corpus-and-schema-builder/design.md` — `SchemaVersionStore`, run-to-version pinning.
