---
inclusion: always
---

# Roadmap

## Overview

EviTrace today is a working single-agent extraction pipeline: multi-backend PDF extraction, four-stage quality control, W3C annotation, and chunked LLM extraction against a 62-field map. Three large idea documents (now archived to `.kiro/specs/archive/original-idea-documents/`) described where it goes next — a provenance subsystem, a privacy/disclosure layer, and a full multi-agent + reviewer-UI product vision. None had ever been turned into specs, and two of them contradicted the existing `xtrace-toolkit` spec.

This roadmap decomposes all three into independently shippable specs, ordered by dependency. The governing decision is **headless core first, reviewer UI as a later consumer** — the extraction, provenance, privacy, and multi-agent capabilities are built as libraries with no GUI assumption, and the annotation UI is specced last, on top of them. This resolves the contradiction between `evitrace_multiagent.md` (which requires a PDF annotation GUI) and `xtrace-toolkit` NFR-2 (which ruled a GUI out permanently) without discarding either.

## Approach Decision

- **Chosen**: Headless core first, UI sequenced last. Build provenance and privacy as foundational subsystems, then the multi-agent routing/extraction track, then evaluation and the reviewer UI as consumers of the stable core.
- **Why**:
  - The two focused docs (privacy, provenance) are ~90–100% greenfield and are *expansions of two stub requirements* inside the multiagent doc (multiagent R26 is a 7-bullet stub of the entire privacy doc; R22+R27 stub the provenance doc). Building the depth versions first means the umbrella doc's stubs get satisfied properly rather than twice.
  - Privacy's disclosure layer and provenance's public/private views both presuppose a provenance graph exists to have views *of*. Specifying disclosure before the graph means designing against a data model that does not exist.
  - A UI built before the core stabilizes would couple to interfaces still in motion. Deferring it costs nothing and de-risks it.
- **Rejected alternatives**:
  - *GUI-first (multiagent is current, xtrace re-scoped underneath it)* — rejected: would front-load the least-defined requirements (multiagent's Usability NFRs are the vaguest in any of the three docs) on top of a core with a known correctness blocker.
  - *Headless permanently (xtrace as-written wins, UI dropped)* — rejected: discards a genuine product requirement rather than sequencing it. `xtrace-toolkit` NFR-2 is amended instead.
  - *Coarse decomposition (one privacy spec + one provenance spec)* — rejected: each would generate 20+ tasks, past the threshold where a single spec stops being reviewable.

## Scope

- **In**: A provenance subsystem (evidence nodes, derivation graph, chain validation, audit artifacts, export, tamper-evidence); a privacy subsystem (secrets, sensitivity classification, disclosure policy gate, LLM gateway, redaction/pseudonymization, vault, commitments); proper inter-rater agreement statistics; the multi-agent track (evidence routing, counterfactual agents, blind second extraction, verification, repair); corpus/schema management; cost and run reporting; an evaluation/ablation harness; and a reviewer annotation UI.
- **Out**: The `xtrace-toolkit` packaging/licensing re-architecture (its own existing spec, tracked below as an update, not re-specced here). Legal compliance certification of any kind — the privacy work provides controls and audit surfaces, never a compliance claim. Any re-litigation of the token-budget thresholds set by the completed `token-efficient-extraction` spec.
- **Out (product-level, carried verbatim from the multiagent doc's "Out of Scope for Initial Version")**: (1) fully autonomous systematic review generation without human approval; (2) automated clinical recommendations from extracted evidence; (3) guaranteed extraction correctness without human validation; (4) OCR-heavy scanned-document workflows beyond fallback support; (5) full meta-analysis automation, unless separately implemented and validated. These are standing product boundaries — no spec below may quietly re-admit one.

## Source Documents and Citation Convention

The three originating documents are archived at `.kiro/specs/archive/original-idea-documents/`: `evitrace_multiagent.md`, `privacy_requirements.md`, `provenance_requirements.md`. They are historical inputs, not living specs — the briefs and specs below supersede them. They are retained because roughly 300 requirement citations across the briefs resolve against them.

**All three documents number their requirements `R1`–`R14`+, so a bare `R14` is ambiguous.** Always cite with an explicit prefix: `multiagent R14`, `priv R14`, `prov R14`. Several briefs still use bare `R<n>` in prose where the owning document is obvious from context; prefer the prefixed form when editing them.

The parked, deliberately-unread `merckle tree.md` idea now lives at `.kiro/specs/public-private-provenance/parked-merkle-tree-idea.md`, beside the spec that will own it (prov R10). It remains unread and unspecced by design.

## Cross-Cutting Non-Functional Requirements

Carried from the multiagent doc; these bind every spec below rather than any single one. Specs must not restate them, but must not violate them either.

- **Performance.** Process PDFs in parallel subject to API rate and local compute limits; process extraction packs in parallel when token/request limits allow; expose configurable document-level *and* API-call-level concurrency; avoid sending full PDFs to downstream extraction and verification agents unless necessary. (The last one is the load-bearing rationale for the entire `evidence-routing` track.)
- **Reliability.** Retry transient API errors with exponential backoff; preserve partial progress in a manifest; allow failed documents *or failed fields* to resume without rerunning the project; fail safely on poor parser output, malformed API responses, or invalid evidence IDs.
- **Usability.** Owned by `reviewer-ui` and already recorded in its brief: status timeline; visual distinction between model-generated, imported, and human-edited evidence; visible uncertainty via confidence, verification status, parser risk, and review state; keyboard shortcuts for accept/edit/reject.
- **Maintainability.** Keep parser, routing, extraction, verification, repair, UI, and export modules separated; ship tests for schema validation, route QC, extraction QC, agreement computation, and final merging; expose configuration for models, prompts, thresholds, critical fields, and output options.

## Open Questions (unresolved)

Carried verbatim from the multiagent doc. None are settled; each is a design input to the spec named after it. Resolve them in that spec's requirements phase rather than by silent default.

1. Which parser should be the default canonical source for biomedical PDFs with complex tables? → *parser-ensemble verification (Direct Implementation), `evidence-routing`*
2. How much dual extraction is needed after calibration? → *`multiagent-extraction`*
3. Which fields require mandatory Agent 1c verification? → *`multiagent-extraction`*
4. What threshold should trigger parser-counterfactual audit? → *`agreement-statistics` (multiagent R5.8)*
5. Should Agent 0 route fields individually or by field group? → *`evidence-routing`*
6. What is the best balance between evidence quote length and paragraph-ID-only evidence? → *`evidence-routing`*
7. How should semantic equivalence be measured for free-text extracted values? → *`evaluation-harness`*
8. What level of human review is required for publishable validation? → *`evaluation-harness`*
9. Which benchmark datasets are appropriate for the first evaluation? → *`evaluation-harness`*
10. Which venue should be targeted first: JBI methodology paper or benchmark paper? → *`evaluation-harness`*

## Constraints

- Python 3.12.x; `src/`-layout package; no configured linter/formatter.
- The existing dependency-direction rules are enforced by AST-based tests (`tests/test_dependency_directions.py`) and must hold: `quality_control` must not import `agents`/`pipeline`/`pdf_extractor`; `text_processing` must not import `quality_control`. New `src/provenance/` and `src/privacy/` packages must declare and respect their own direction before any cross-package import lands.
- Heavy optional dependencies stay lazily imported inside function bodies, never at module level.
- `_shared_paper_prefix` prompt-cache stability must survive every change to the LLM call path — this directly constrains the privacy LLM gateway (privacy R9), which sits in front of `src/agents/openai/api_client.py`.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`.
- No PHI, credentials, or real patient data enters the repository or any test fixture.

## Boundary Strategy

- **Why this split**: Each document contained multiple independent responsibility seams that were only co-located because they were written in one sitting. Splitting on those seams means the highest-risk research problems (PHI detection, leakage-risk estimation, tamper-evidence) are isolated into their own specs and cannot block the deterministic, shippable parts (evidence nodes, policy gate, audit artifacts).
- **Shared seams to watch**:
  - **Privacy ↔ provenance is mutually recursive as written.** Provenance R7.5 defers disclosure decisions to the privacy module; privacy R1.3 provides labels and decisions to the provenance module. The resolution is one-directional — **privacy decides, provenance consumes** — and that interface must be pinned in `provenance-core`'s design, not left for the two specs to settle independently.
  - **Provenance ↔ xtrace-toolkit.** xtrace `R-GOV-1` (append-only ledger) and `R-X-2` (reproducibility manifest) are flat-event-log versions of what `provenance-core` builds as a graph. De-duplicate at design time: the ledger becomes a projection of the graph, not a parallel store.
  - **Evidence node identity** is consumed by privacy (R3.4, R5.3), evidence routing, and the UI. It is defined once, in `provenance-core`, and never redefined.
  - **Tamper-evidence (provenance R10)** is the intended home for the parked `merckle tree.md` idea. That file is explicitly marked immature and must stay unread and unspecced until `public-private-provenance` is reached.
  - **Two requirements deliberately straddle a seam and must be split, not duplicated.** Prov R1.4 and R13 both describe the "provenance-incomplete" state: `provenance-core` *computes* it, `provenance-audit-export` *reports* it. Prov R12.1 (privacy labels attached to provenance nodes): `provenance-core` owns the carrier field on the node model, `public-private-provenance` owns the consultation logic. Confirm both splits during the requirements phase rather than letting each spec claim the whole requirement.
  - **Bootstrapping the privacy↔provenance interface.** `privacy-core` depends on `provenance-core`, yet `provenance-core` must pin the interface it will consume before `privacy-core` exists. Resolution: `provenance-core` defines the label/decision carrier structure; `privacy-core` populates it. This must be explicit in `provenance-core`'s design.

## Existing Spec Updates

- [x] xtrace-toolkit — §7 "GUI / interactive viewer (permanently out)" amended to "out of scope for this spec", pointing at the `reviewer-ui` spec. **NFR-2 itself was left unchanged and is not in conflict**: it constrains extraction and QC to run headlessly, which a separate UI *consumer* does not violate. Done.
- [ ] xtrace-toolkit — de-duplicate `R-GOV-1` (append-only ledger) and `R-X-2` (reproducibility manifest) against `provenance-core` at design time; note that `R-QC-3` is satisfied by `agreement-statistics`. Dependencies: provenance-core, agreement-statistics
- [ ] risk-remediation — already specced (requirements generated, not approved). Its Requirement 1 fixes final-output writes being silently rejected. Nothing downstream is verifiable until this ships. Dependencies: none

## Direct Implementation Candidates

- [ ] multiagent R3–R6 (parser ensemble, canonical document representation, parser QC, cleaning) — **largely** built as `src/pdf_extractor/` + `src/quality_control/`, but "already built" was asserted at the requirement level and does not hold clause-by-clause. Verify each clause below and close out or re-open; do not spec wholesale. The clauses least likely to be satisfied today, and their real owners if they turn out to be gaps:
  - **multiagent R3.7–R3.8** — page-by-page/section-by-section parser comparison, and parser risk flags on affected pages/paragraphs/tables/fields. Consumed by `evidence-routing`; metrics owned by `agreement-statistics`.
  - **multiagent R4.7–R4.8** — preserve page-level bounding boxes for later annotation, and where no coordinates exist still preserve page/text identifiers and **mark annotation precision as approximate**. Consumed by `provenance-core` (evidence anchors, prov R3.4 anchor-absence) and `reviewer-ui`. Note `risk-remediation` Requirement 4 is fixing a related OCR bbox defect — reconcile.
  - **multiagent R5.3–R5.8** — parser *agreement* metrics (token overlap, numeric-token overlap, table-detection, section-heading, text-presence agreement), parser-risky page marking, and the escalation rules. Owned by `agreement-statistics` (metrics) and `evidence-routing` (R5.6/R5.8 consumption).
  - **multiagent R5.9** — save a parser QC report into the audit package. Owned by `provenance-audit-export`.
  - **multiagent R6.4–R6.6** — record removed blocks and removal reasons; preserve or flag removed sections that may hold evidence (data availability, funding); keep raw parser output in the audit package. These are provenance derivation events — owned by `provenance-core` (prov R5) and `provenance-audit-export`.
- [ ] multiagent R26 secrets-in-env / no-keys-in-logs bullets — already satisfied by `config_utils.py` env handling; the remainder is absorbed by `privacy-core`.
- [ ] Correct stale prose in `src/pipeline/README.md` and `src/utils/README.md`: they still describe `generate_qc_report()` writing `outputs/qc_report.csv`. The real entry point is `extraction_report.py::generate_flagged_fields_report()` writing `outputs/flagged_fields.csv` (`FLAGGED_FIELDS_FILE`, `src/utils/path_utils.py:93`). Docs-only fix.
- [ ] Correct the manifest status vocabulary wherever it is documented: actual failure statuses are `failed_qc_pipeline` (`orchestrator.py:130`) and `failed_chunks` with a `failed_chunks` list (`pdf_processor.py:1132`) — not `failed_chunk_<n>`. Note this contradicts `risk-remediation` Requirement 5.3, which specifies `failed_chunk_{n}`; reconcile during that spec's design.

## Specs (dependency order)

- [ ] provenance-core — evidence nodes, source identity, claim→evidence links, derivation tracking, typed provenance graph, chain validation (prov R1–R6, R8, R14). Dependencies: none
- [ ] agreement-statistics — real inter-rater agreement replacing the current binary pass/fail ratio: Cohen's kappa (multiagent R15.4), weighted kappa (R15.5), Gwet-style metrics (R15.6), plus output normalization for comparison (R15.1–15.2); Krippendorff's alpha comes from xtrace R-QC-3's worked example, not from R15. Fixes a live violation of xtrace R-QC-3 at **three** sites — `builtin_impls/inter_rater_report.py:38-44` (binary pass/fail ratio), `adjudicator.py:278` `_compute_agreement_score` (word-overlap fraction feeding a 0.15-weighted term), `checks/extractor_agreement.py:206` `agreement_rate` (match/total ratio) — and fills `iaa_calculator.py:48-55`, a live stub whose config hook already exists but returns `{metric: None}`. Dependencies: none
- [ ] corpus-and-schema-builder — project/corpus management, batch upload with per-document audit trails, user-defined extraction schema builder, external evidence import (multiagent R1–R2, R20). Two carve-outs: R20.6 ("the UI SHALL allow users to manually link unresolved evidence") cannot be delivered headlessly — import, matching, and the unresolved queue land here, the linking surface lands in `reviewer-ui`. R2.3–R2.4 (LLM-assisted schema generation) need an LLM call path that must sit behind the `privacy-core` gateway, which is downstream of this dependency-free spec; scoped out here and revisited after `privacy-core`. Dependencies: none
- [ ] cost-and-run-reporting — per-stage token and cost accounting, run-level reproducibility reporting (multiagent R23, R27). Note R23.1 is not a thin projection over existing telemetry: no `latency`/`retry_count`/`elapsed` is recorded anywhere in `src/agents/openai/`, so the telemetry extension is real work alongside the price table. Dependencies: none
- [ ] privacy-core — secret/key management, sensitivity classification, disclosure policy gate, external LLM gateway, privacy audit trail, fail-closed behavior, anti-overclaiming documentation (priv R1–R5, R9, R11, R14–R16). Dependencies: provenance-core
- [ ] provenance-audit-export — versioned run-level audit artifacts, interoperable export with partial-fidelity reporting, "provenance-incomplete" as a distinct reportable state (prov R9, R11, R13; multiagent R22). Dependencies: provenance-core
- [ ] evidence-routing — local retrieval hints, locator agent (Agent 0), route quality control, counterfactual locator (Agent 0c), route adjudication (multiagent R7–R11). Note: R11.3/R11.5 (token-capped evidence packs, deterministic trimming with discarded-ID recording) overlap the pruning logic delivered by the completed `token-efficient-extraction` spec in `pdf_processor.py`. That seam must be pinned at design time — extend the existing pruning, do not build a second one. Dependencies: provenance-core
- [ ] privacy-transformations — redaction, pseudonymization, minimization, date-shifting, leakage-risk evaluation (priv R6–R8). Highest research risk; acceptance criteria need tightening before design. Dependencies: privacy-core
- [ ] public-private-provenance — public vs private provenance views, disclosure coordination, private identity/evidence vault, cryptographic commitments, tamper-evidence (prov R7, R10, R12; priv R10, R12, R13). Home of the parked merkle-tree idea. Dependencies: provenance-core, privacy-core
- [ ] multiagent-extraction — route-fed targeted extraction with mandatory citation (Agent 1A, R12), blind second extractor (R13), extraction QC (R14), verifier agent (R16), answer adjudication (R17), repair agent (R18), plus the escalation policy in R15.9. **R15.1–15.8 belong to `agreement-statistics`, not here** — only R15.9 is scoped to this spec. Dependencies: evidence-routing, agreement-statistics
- [ ] evaluation-harness — human benchmark mode, ablation harness, success metrics (multiagent R24–R25). Caveat: R24.6 (prospective review timing and correction-burden capture) needs a human review surface, which `reviewer-ui` provides *later*. Scope R24.6 here as instrumentation hooks only; the Stage-2 evaluation is not executable until `reviewer-ui` lands. Dependencies: multiagent-extraction
- [ ] reviewer-ui — PDF-centered annotation and review interface, evidence-linking surface (R20.6), merger/export surface (multiagent R19, R21, Usability NFRs). Sequenced last by explicit decision. Introduces an entirely new technology surface: there is no UI, no web framework, and no such dependency in `requirements.txt` today, and no framework has been chosen — that choice belongs to this spec's design phase. It must consume provenance evidence anchors rather than re-deriving them, and must never become a second producer of W3C annotations (`src/artifact_generation/w3c_annotation.py` is the sole producer). R21.1–R21.5 (merger/export) stay headless-callable; R21.7's cost-report content is owned by `cost-and-run-reporting`. Dependencies: corpus-and-schema-builder, multiagent-extraction, provenance-core
