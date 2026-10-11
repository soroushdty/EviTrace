# Design Document — provenance-core

## Overview

**Purpose**: This feature makes evidence traceability a first-class, inspectable subsystem. It introduces `src/provenance/`, a leaf package that defines the provenance record model (sources, evidence nodes, claims, derivation steps, validation results), the identity rules for those records, the typed graph assembled from them, a chain validator that reports broken chains by severity, and a completeness computer that answers "is this artifact fully traceable?" as data rather than prose.

**Users**: Biomedical AI researchers, clinical evaluators, and institutional reviewers consume the output indirectly through downstream specs. The direct consumers are `privacy-core` (populates the privacy carrier), `provenance-audit-export` (reports completeness and validation findings), `public-private-provenance` (consults the carrier), `evidence-routing`, and `reviewer-ui` (consume evidence node identity).

**Impact**: Identity-bearing artifacts that exist today as incidental by-products — the ranked evidence index, the manifest identity, the chunk-output `loc` check, the reconciler's flat provenance dict — are **promoted** into typed provenance records. No parallel store is created, no second fingerprint set is computed, and no second evidence numbering scheme is introduced. The extraction pipeline gains explicit emission calls; everything else is additive.

### Goals

- One canonical definition of evidence node identity for the entire project.
- A typed, schema-versioned provenance graph that is interpretable without pipeline internals.
- Chain validation with per-finding severity, and a completeness state computed at claim, document, and run level.
- A pinned, inert privacy label/decision carrier that provenance stores but never interprets.
- The xtrace append-only decision ledger delivered as a **projection** of this graph, not a second store.

### Non-Goals

- Audit packages, export formats, and reporting of the completeness state — `provenance-audit-export`.
- Public/private views, disclosure coordination, vaults, commitments, tamper-evidence — `public-private-provenance`.
- Any sensitivity classification or disclosure policy evaluation — `privacy-core`.
- Graph visualization, a query language, a UI, or a graph database.
- Replacing `src/artifact_generation/w3c_annotation.py` as the sole W3C annotation producer.

## Boundary Commitments

### This Spec Owns

- The provenance record model and its type vocabulary: node kinds, edge kinds, evidence kinds, derivation kinds, anchor precision, severity, completeness state, privacy state.
- Identity derivation for source nodes, run identity, evidence nodes, claim nodes, and derivation steps.
- The append-only in-run record sink (`ProvenanceRecorder`) and the stage-contract declaration table.
- Graph assembly, including partial-graph preservation and missing-segment reporting.
- Chain validation and severity classification of findings.
- **Computation** of the provenance-completeness state at claim, document, and run level.
- The `PrivacyCarrier` structure carried by every node, and its structural conformance check.
- The in-repo JSON serialization of the graph, its schema identity, and its schema-version compatibility rule.
- The ordered event-log projection derived from the graph.
- The `provenance` configuration block and the `src/provenance` dependency-direction rules.

### Out of Boundary

- **Reporting** the completeness state, rendering audit artifacts, or defining an interoperable export format — `provenance-audit-export` consumes `CompletenessReport` and `ChainValidationReport` as-is.
- **Populating** the privacy carrier or defining the label/decision vocabulary — `privacy-core`.
- **Consulting** the carrier to decide disclosure, and any public/private view — `public-private-provenance`.
- Changing the model-facing `loc` vocabulary, the chunk-output JSON contract, `_shared_paper_prefix`, or any prompt content — with one declared exception: the caption-sentence location fix in `evidence_index.py` (PipelineIntegration) changes the `page`/`coords` values of the affected items, and those values are serialized into the paper evidence package that `_shared_paper_prefix` embeds. It is a prompt-content change, scoped to those two values on those items.
- Changing extraction behaviour, with one declared exception that applies **only when provenance is enabled**: when repair retries are exhausted and only reference problems remain (unknown/duplicate/ambiguous `loc` ids, no other schema defect), the output is retained and a finding is recorded (5.4) instead of failing the document with `RepairExhaustedError`. With provenance disabled (`provenance.enabled: false`) the orchestrator passes no provenance object and behaviour is byte-identical to before.
- Fixing the pre-existing duplication of `_compute_extraction_map_hash` between `manifest.py` and `evidence_index.py`; this spec adds no third computation but does not consolidate the existing two.
- Changing the raise semantics of `validate_chunk_output` on the existing call path.
- Any change to `src/quality_control/`, `src/agents/`, `src/pdf_extractor/`, or `src/text_processing/`.

### Allowed Dependencies

- `src/provenance/` may import **only** `src/utils/` and the Python standard library (in practice only `utils.logging_utils`). It is a leaf package, peer to `text_processing`.
- `src/pipeline/` is the sole integration point and may import `provenance`.
- `quality_control`, `agents`, `pdf_extractor`, and `text_processing` must **not** import `provenance`.
- No new third-party dependency. No graph library, no crypto library, no RDF library. `hashlib` and `json` from the standard library only.

### Revalidation Triggers

- Any change to `EvidenceNode.node_id` composition or to the `source_id#local_id` convention — breaks `evidence-routing`, `privacy-core`, `public-private-provenance`, `reviewer-ui`.
- Any change to `PrivacyCarrier` field names or to the "stored verbatim, never inferred" rule — breaks `privacy-core` and `public-private-provenance`.
- Any change to `CompletenessReport` or `ChainValidationReport` shape — breaks `provenance-audit-export`.
- A major bump of `PROVENANCE_SCHEMA_VERSION` — breaks every artifact reader.
- Adding a node kind, edge kind, or derivation kind to the closed vocabularies.
- **Adding or renaming a stage in `STAGE_CONTRACTS` or `STAGE_ORDER`** — this is the expected growth path, not an exceptional one: `evidence-routing` adds seven stages (index build, local retrieval, locator, route QC, counterfactual, adjudication, pack assembly) and `multiagent-extraction` adds seven more (its extraction, verification, adjudication, and repair stages). Each such spec **extends both tables in place** — adding a `StageContract` entry and an ordered `STAGE_ORDER` position — and routes its records through `ProvenanceRecorder`. Bypassing the recorder, catching `UnknownStageError` to skip declaration, or maintaining a private stage list are all prohibited. The vocabulary stays closed and the contract-drift test (every recorded stage appears in both tables) stays in force; a spec that adds a stage must extend the tables and re-run that test, and must re-verify `compute_completeness` under `stage_contracts_strict`.
- Any change to the `derivation_from_stage_event` signature, or to the stage→`DerivationKind` mappings downstream specs pin against it.
- Any change to the `src/provenance` dependency direction rules.
- Any change to the anchor-precision rule (Adapters) or to the evidence-node content rule (content-bearing keys never enter `parser_metadata`) — `reviewer-ui` must revalidate against both: an `absent` anchor may still carry text-search hints (section path, weak structural path) but its precision is never upgraded, and evidence text is not in the artifact — reviewer-ui reads it from the evidence cache (see Security Considerations).
- Provenance `RunIdentity.run_id` is `run:<sha16>` over the configuration fingerprint, schema fingerprint, and model ids, so runs with identical configuration share it and `created_at` distinguishes them. It is **not** `cost-and-run-reporting`'s `run_id` (the run folder name); a cross-spec consumer that joins the two must map them explicitly, never conflate them.

## Architecture

### Existing Architecture Analysis

The pipeline already produces four artifacts that are provenance records in all but name:

| Existing artifact | Location | Becomes | Gap this spec closes |
|---|---|---|---|
| Evidence item `dict` with `id`/`section_path`/`page`/`coords`/`xpath`/`source_pdf`/`score` | `src/pipeline/evidence_index.py` | `EvidenceNode` | No type, no anchor-absence marker, no anchor-precision, no producing backend |
| `ManifestIdentity` (`pdf_content_hash`, `config_hash`, `extraction_map_hash`, `model_id`, `schema_version`) | `src/pipeline/manifest.py` | `SourceNode` + `RunIdentity` | Scoped to cache staleness, not identity; not linkable to evidence |
| `loc` reference check against `valid_location_ids`; `evidence_map` resolution | `src/pipeline/validator.py` | Claim→evidence edges + reference issues | Raises on one path, silently drops on the other; nothing is recorded |
| `_build_provenance_dict` flat extractor-name dict | `src/quality_control/reconciler.py` | `DerivationStep` records | Not a graph; no edges, node types, or derivation steps |

Constraints that must be preserved: `_shared_paper_prefix` byte-stability; no global mutation (config passed explicitly); heavy optional deps lazily imported (not applicable here — none are used); all shared dataclasses of a package live in one module with a single owner.

### Architecture Pattern & Boundary Map

Selected pattern: **leaf domain package with mapping-based adapters**. The core knows nothing about PDFs, GROBID, or model providers; adapters accept plain mappings, so no forbidden import is ever needed. `pipeline` performs all wiring.

```mermaid
graph TB
    subgraph PipelinePackage
        Orchestrator
        ExtractionPipeline
        EvidenceIndex
        PdfProcessor
        ChunkValidator
    end
    subgraph ProvenancePackage
        Adapters
        Recorder
        Models
        Identity
        PrivacyCarrier
        GraphBuilder
        ChainValidator
        Completeness
        Projection
        Serialization
        Contracts
    end
    subgraph UtilsPackage
        ConfigUtils
        PathUtils
        LoggingUtils
    end
    Orchestrator --> Recorder
    Orchestrator --> GraphBuilder
    Orchestrator --> ChainValidator
    Orchestrator --> Completeness
    Orchestrator --> Serialization
    ExtractionPipeline --> Adapters
    EvidenceIndex --> Adapters
    PdfProcessor --> Adapters
    ChunkValidator --> Recorder
    Adapters --> Models
    Adapters --> Identity
    Recorder --> Models
    GraphBuilder --> Models
    ChainValidator --> Models
    Completeness --> Models
    Completeness --> Contracts
    Contracts --> Models
    Recorder --> Contracts
    Projection --> Contracts
    Projection --> Models
    Serialization --> Models
    Models --> PrivacyCarrier
    Adapters --> Contracts
    Adapters --> PrivacyCarrier
    Adapters --> LoggingUtils
    Recorder --> LoggingUtils
    GraphBuilder --> LoggingUtils
    ChainValidator --> LoggingUtils
    Completeness --> LoggingUtils
    Serialization --> LoggingUtils
    Orchestrator --> ConfigUtils
    Orchestrator --> PathUtils
```

**Architecture Integration**

- **Dependency direction** (exhaustive; every module of the package appears exactly once): `utils → provenance.privacy_carrier → provenance.models → provenance.identity → provenance.contracts → provenance.recorder → provenance.graph → provenance.validation → provenance.completeness → provenance.projection → provenance.serialization`, with `provenance.adapters` depending on `models`, `identity` and `privacy_carrier` (plus `contracts` for the derivation adapter, which validates stage names), and importing `utils.logging_utils`. `identity` imports only the standard library; `serialization` does not import `path_utils` (the caller passes the directory). Each module imports only from modules to its left. `privacy_carrier` precedes `models` because every node embeds a carrier; `contracts` precedes `completeness` because the completeness diff consumes `STAGE_CONTRACTS`. `pipeline` sits above all of provenance and is the only caller.
- **Domain boundaries**: record *definition and identity* (models, identity, privacy_carrier) is separate from record *emission* (recorder, adapters), which is separate from *assembly* (graph), which is separate from *analysis* (validation, completeness, projection), which is separate from *persistence* (serialization). These are the four boundary candidates named in the brief, and they are the parallel-safe task seams.
- **Existing patterns preserved**: single-module ownership of shared dataclasses (mirrors `quality_control/models.py`); explicit config passing; run-scoped output paths via `resolve_run_output_path`; closed string vocabularies (mirrors `YearResolution.provenance`); AST-enforced dependency direction.
- **New components rationale**: every module above maps to exactly one requirement group; none exists speculatively.

### Technology Stack

| Layer | Choice / Version | Role in Feature | Notes |
|-------|------------------|-----------------|-------|
| Runtime | Python 3.12.x | Package language | Matches repo pin |
| Domain model | `dataclasses` (frozen) + `typing.Literal` | Node/edge/report records and closed vocabularies | Mirrors `quality_control/models.py`; `from __future__ import annotations` throughout |
| Identity | `hashlib.sha256` (stdlib) | Derivation-step and run-identity digests | Document/config/schema fingerprints are **consumed** from `ManifestIdentity`, never recomputed |
| Persistence | `json` (stdlib) → `outputs/run_<ts>/provenance/` | Graph artifact | Same run-scoped pattern as `evidence_cache` |
| Config | `configs/config.yaml` top-level `provenance:` block | Enable flag, output subdir, severity policy | Must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` |
| Tests | pytest + Hypothesis | Unit, property, AST boundary tests | Hypothesis for identity determinism and projection purity |

No new third-party dependency is introduced.

## File Structure Plan

### Directory Structure

```
src/provenance/
├── __init__.py                 # Public surface; re-exports models, recorder, graph, reports
├── models.py                   # All record types, closed vocabularies, and every cross-layer type:
│                               # ValidationFinding, DEFAULT_SEVERITIES, MissingSegment,
│                               # StageContract, ArtifactCompleteness, RecordedRun,
│                               # ChainValidationReport, CompletenessReport
├── privacy_carrier.py          # PrivacyCarrier + structural conformance check. Inert by construction.
├── identity.py                 # Source, run, evidence, claim, derivation ID derivation and parsing
├── contracts.py                # STAGE_CONTRACTS declaration table + stage ordering
├── recorder.py                 # ProvenanceRecorder: append-only sink; snapshot to RecordedRun
├── graph.py                    # ProvenanceGraph; build_graph(); attach_validation/attach_completeness
├── validation.py               # validate_chain() only; declares no dataclass
├── completeness.py             # compute_completeness() only; declares no dataclass
├── projection.py               # project_event_log(): ordered append-only view of the graph
├── serialization.py            # graph_to_dict / graph_from_dict / write_graph / read_graph; schema version guard
└── adapters/
    ├── __init__.py             # Adapter re-exports
    ├── identity_adapter.py     # ManifestIdentity mapping -> SourceNode, RunIdentity
    ├── evidence_adapter.py     # Evidence-index item dicts -> EvidenceNode
    ├── claim_adapter.py        # Reconstructed field dicts -> ClaimNode + cites_evidence edges
    └── derivation_adapter.py   # QC provenance dict and stage events -> DerivationStep
```

Adapters take `Mapping[str, Any]` / `Sequence[Mapping[str, Any]]` only. This is what keeps `provenance` importable without `pipeline`.

```
tests/src/provenance/
├── test_provenance_models.py            # vocabulary closure, frozen records, extension namespacing
├── test_provenance_privacy_carrier.py   # inertness, verbatim storage, rejection path
├── test_provenance_identity.py          # ID derivation + round-trip; determinism property tests
├── test_provenance_recorder.py          # append-only semantics, idempotent duplicates, stage marks
├── test_provenance_graph_builder.py     # typed assembly, partial preservation, missing segments
├── test_provenance_validation.py        # each finding kind, severity classification, unvalidated path
├── test_provenance_completeness.py      # claim/document/run states, missing-anchor vs unsupported
├── test_provenance_projection.py        # deterministic order, purity, no-parallel-store assertion
├── test_provenance_serialization.py     # schema metadata, namespaced extensions, version guard
├── test_provenance_adapter_identity.py     # identity adapter
├── test_provenance_adapter_evidence.py     # evidence adapter, incl. all three anchor-precision cases
├── test_provenance_adapter_claim.py        # claim adapter, incl. all three support classifications
├── test_provenance_adapter_derivation.py   # derivation adapter, incl. many-to-one and one-to-many
└── test_provenance_import_isolation.py     # imports cleanly with pipeline/QC absent from sys.modules
```

One test module per adapter, so the four adapter tasks share no test file. The only file they share is `src/provenance/adapters/__init__.py`, to which each adapter task appends exactly one re-export line.

### Modified Files

- `src/utils/config_utils.py` — add `_PROVENANCE_DEFAULTS`, register `"provenance"` in `_ALL_KNOWN_TOP_LEVEL_KEYS`, add `load_provenance_config(config)`.
- `src/utils/path_utils.py` — add `PROVENANCE_DIR = resolve_run_output_path("provenance")`. It is the **default** artifact directory only and is currently unused by code; the effective directory is the validated `provenance.output_subdir` resolved by `run_provenance.artifact_directory` (see Configuration).
- `configs/config.yaml` — add the `provenance:` block.
- `tests/test_dependency_directions.py` — add 8 `FORBIDDEN_PAIRS` entries plus one named test per pair.
- `src/pipeline/validator.py` — add optional `issue_sink` to `_validate_extraction_item`, `validate_chunk_output`, and `reconstruct_fields`. Default `None` preserves current behavior exactly.
- `src/pipeline/evidence_index.py` — add optional `recorder` keyword to `build_or_load_evidence_bundle`; emit evidence nodes via `evidence_adapter.evidence_node_from_item` after the bundle is built (both cache-hit and cache-miss paths). Add the pure header helper `extract_header_metadata` (PipelineIntegration), which the author/year prefill also reads. Fix caption-sentence locations (PipelineIntegration).
- `src/pipeline/extraction_pipeline.py` — emit the source node from the document's `ManifestIdentity` (computed by PipelineIntegration via `manifest.compute_identity`; no production call site computes it today), and convert `unified.content["provenance"]` into derivation steps via `derivation_adapter`.
- `src/pipeline/pdf_processor.py` — emit claim nodes and per-chunk/synthesis derivation steps; mark prefilled fields 1–2 as non-model origin, citing the header metadata node their value came from; mark claim-scoped stages per document.
- `src/pipeline/orchestrator.py` — own the recorder lifecycle: construct per run, pass explicitly, then build graph, validate, mark `chain_validation`, compute completeness over a post-validation snapshot, resolve the artifact directory, and write the artifact. As implemented, the lifecycle code lives in `run_provenance.py` and the orchestrator makes three short calls (`start_run_provenance`, per-document hooks, `finish_run_provenance`).
- **New pipeline modules** (added during implementation, beside the files above, so none of the existing modules grows by the full emission code):
  - `src/pipeline/document_provenance.py` — run planning (`plan_run_documents`, `build_run_identity`, `identity_config`) and per-document emission of the source node, the QC `reconcile` derivation step and the `document_extraction` / `quality_control` stage marks (`emit_document_provenance`, `emit_document_failure`, `DocumentProvenance`).
  - `src/pipeline/claim_provenance.py` — claim nodes, `metadata_prefill` / `extraction_chunk` / `synthesis` derivation steps, revisions, header citations and the per-document wrapped issue sink (`DocumentClaims`, `claim_issue_sink`, `header_citations`, `plan_field_candidates`).
  - `src/pipeline/run_provenance.py` — validated settings (`resolve_provenance_settings`, `validated_output_subdir`, `artifact_directory`), `start_run_provenance`, `RunProvenance` per-document hooks, `finish_run_provenance`, `ProvenanceRunSummary`.
- `.kiro/steering/config.md`, `configs/README.md`, `.kiro/steering/testing.md`, `src/pipeline/README.md`, `src/utils/README.md`, `src/provenance/README.md`, `CHANGELOG.md` — documentation of the above.
- Tests beyond the plan above: `tests/src/provenance/` additionally holds `test_provenance_contracts.py`, `test_provenance_attribution.py`, `test_provenance_properties.py`, `test_provenance_prompt_prefix_regression.py`, `test_provenance_stage_contract_drift.py` and `test_provenance_end_to_end.py`; `tests/src/pipeline/` holds `test_evidence_index_provenance.py`, `test_evidence_index_caption_location.py`, `test_extraction_pipeline_provenance.py`, `test_claim_provenance.py`, `test_process_pdf_provenance.py`, `test_repair_retry_provenance.py`, `test_validator_issue_sink.py`, `test_run_provenance.py`, `test_orchestrator_provenance.py`, `test_provenance_end_to_end.py`; `tests/src/utils/test_provenance_config.py`.

## System Flows

### Record emission through to artifact

```mermaid
sequenceDiagram
    participant Orch as Orchestrator
    participant Rec as ProvenanceRecorder
    participant Ad as Adapters
    participant GB as GraphBuilder
    participant CV as ChainValidator
    participant CP as Completeness
    participant Ser as Serialization
    Orch->>Rec: create with RunIdentity
    Orch->>Ad: source node from ManifestIdentity
    Ad-->>Rec: record source
    Orch->>Ad: evidence nodes from evidence index items
    Ad-->>Rec: record evidence
    Orch->>Ad: derivation steps from QC provenance
    Ad-->>Rec: record derivation
    Orch->>Ad: claim nodes from reconstructed fields
    Ad-->>Rec: record claim and cites edges
    Orch->>Rec: mark stage complete or failed
    Orch->>GB: build graph from snapshot
    GB-->>Orch: graph plus missing segments
    Orch->>CV: validate chain
    CV-->>Orch: findings by severity
    Orch->>CP: compute completeness with stage contracts
    CP-->>Orch: claim document and run states
    Orch->>Ser: write graph artifact
```

Key decisions not visible in the diagram: emission happens strictly **after** prompt assembly and **after** response parsing, so no provenance value can reach `_shared_paper_prefix`; a stage that emits nothing does not abort assembly (1.6) but is detected by the completeness diff (2.1); and validation results are themselves recorded back into the graph as validation nodes (8.7) before serialization.

### Claim support classification

```mermaid
stateDiagram-v2
    [*] --> Received
    Received --> Unsupported: no evidence reference
    Received --> HasReferences: one or more references
    HasReferences --> ReferenceIssue: unknown or duplicate or ambiguous id
    ReferenceIssue --> HasReferences: claim retained with issue recorded
    HasReferences --> Unsupported: no reference resolves
    HasReferences --> MissingAnchor: evidence exists but no cited node usable
    HasReferences --> Supported: at least one cited node usable
    Unsupported --> [*]
    MissingAnchor --> [*]
    Supported --> [*]
```

`Unsupported` and `MissingAnchor` are distinct terminal states — this is the whole point of 5.6 and 2.4. A reference issue never destroys the claim (5.4). A cited node is *usable* when its anchor precision is `exact` or `approximate` (rule under Adapters); a claim with mixed citations is `supported` if **any** cited node is usable. A claim whose references all fail to resolve is `unsupported` and carries a `dangling_reference` finding per unresolved reference.

## Requirements Traceability

| Requirement | Summary | Components | Interfaces | Flows |
|-------------|---------|------------|------------|-------|
| 1.1, 1.2, 1.3, 1.4, 1.5, 1.6 | First-class subsystem; stage-agnostic emission; separate storage; traversable path; no network need; tolerate silent stages | ProvenanceModels, ProvenanceRecorder, GraphBuilder, Serialization | `ProvenanceRecorder`, `build_graph`, `write_graph`, `ProvenanceGraph.path_from_claim` | Record emission |
| 2.1, 2.2, 2.3, 2.4, 2.5, 2.6 | Completeness computation at three levels; responsible stage; missing-anchor vs unsupported; undetermined; data not report | CompletenessComputer, StageContracts | `compute_completeness`, `STAGE_CONTRACTS`, `CompletenessReport` | Record emission, Claim support classification |
| 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7 | Source and run identity from fingerprints; single fingerprint set; no disclosure decision | IdentityService, IdentityAdapter, ProvenanceModels | `derive_source_id`, `derive_run_id`, `source_node_from_identity`, `RunIdentity` | Record emission |
| 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8 | Evidence node model, anchors, anchor-absence, approximate precision, backend, adopted ID, sole identity definition | EvidenceAdapter, IdentityService, ProvenanceModels | `evidence_nodes_from_items`, `format_evidence_id`, `parse_evidence_id`, `SourceAnchor` | Record emission |
| 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7 | Claim records; unsupported marking; reference verification and issues; revision links; anchor vs no-reference; non-model origin | ClaimAdapter, ChainValidator, ProvenanceModels, modified pipeline validator | `claim_nodes_from_fields`, `issue_sink`, `ClaimNode.support`, `ClaimNode.origin` | Claim support classification |
| 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7 | Derivation steps; stage and kind; probabilistic vs deterministic; many-to-one and one-to-many; removal reasons | DerivationAdapter, ProvenanceModels, IdentityService | `DerivationStep`, `derive_derivation_id`, `derivations_from_qc_provenance` | Record emission |
| 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7 | Typed graph; distinct node types; self-interpretable; partial preservation; schema metadata; ledger as projection; no export format | GraphBuilder, Projection, Serialization | `build_graph`, `ProvenanceGraph`, `project_event_log`, `PROVENANCE_SCHEMA_ID` | Record emission |
| 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7 | Chain validation; severity classification; unvalidated state; attachable results | ChainValidator, ProvenanceModels | `validate_chain`, `ValidationFinding`, `DEFAULT_SEVERITIES`, `ChainValidationReport` | Record emission |
| 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7 | Single privacy carrier; verbatim storage; unknown default; no policy; provenance of the label; structural rejection; absent carrier tolerated | PrivacyCarrierModule, ProvenanceModels, ChainValidator | `PrivacyCarrier`, `check_carrier_conformance` | Claim support classification |
| 10.1, 10.2, 10.3, 10.4, 10.5, 10.6 | New evidence kinds; parser metadata; attachable validations; namespaced extensions; version-forward; unrecognized version reported | ProvenanceModels, Serialization, EvidenceAdapter | `EvidenceKind`, `extensions`, `graph_from_dict`, `UnrecognizedSchemaVersionError` | Record emission |

## Components and Interfaces

| Component | Domain/Layer | Intent | Req Coverage | Key Dependencies (P0/P1) | Contracts |
|-----------|--------------|--------|--------------|--------------------------|-----------|
| ProvenanceModels | Definition | All record types and closed vocabularies | 1, 3, 4, 5, 6, 7, 9, 10 | PrivacyCarrierModule (P0) | State |
| PrivacyCarrierModule | Definition | Inert privacy label/decision carrier | 9 | none | Service, State |
| IdentityService | Definition | Derive and parse all provenance IDs | 3, 4, 5, 6 | ProvenanceModels (P0) | Service |
| StageContracts | Definition | Declarative table of what each stage must emit | 2 | ProvenanceModels (P0) | State |
| ProvenanceRecorder | Emission | Append-only in-run record sink | 1, 2, 5 | ProvenanceModels (P0), IdentityService (P0) | Service, State |
| Adapters | Emission | Convert pipeline mappings into records | 3, 4, 5, 6, 10 | ProvenanceModels (P0), IdentityService (P0) | Service |
| GraphBuilder | Assembly | Assemble typed graph; preserve partial results | 1, 7 | ProvenanceRecorder (P0) | Service |
| ChainValidator | Analysis | Validate chains; classify findings by severity | 5, 8, 9 | GraphBuilder (P0) | Service |
| CompletenessComputer | Analysis | Compute completeness at three levels | 2 | GraphBuilder (P0), StageContracts (P0), ChainValidator (P1) | Service |
| Projection | Analysis | Ordered append-only view of the graph | 7 | GraphBuilder (P0) | Service |
| Serialization | Persistence | JSON artifact with schema identity and version guard | 1, 7, 10 | GraphBuilder (P0), PathUtils (P1) | Batch, State |
| PipelineIntegration | Integration | Wire emission and lifecycle into the pipeline | 1, 2, 3, 4, 5, 6 | all provenance components (P0) | Service |

### Definition Layer

#### ProvenanceModels

| Field | Detail |
|-------|--------|
| Intent | Single owner of every provenance record type, every closed vocabulary, and every type shared across more than one layer |
| Requirements | 1.1, 1.2, 3.2, 4.1, 4.3, 5.1, 6.2, 7.1, 7.2, 9.1, 10.1, 10.4 |

**Responsibilities & Constraints**
- **Ownership rule (precise form)**: `models.py` owns (a) every provenance *record* type, (b) every closed vocabulary, and (c) every type consumed by more than one layer — specifically `ValidationFinding`, `DEFAULT_SEVERITIES`, `RecordedRun`, `MissingSegment`, `StageContract`, `ArtifactCompleteness`, `ChainValidationReport`, and `CompletenessReport`. Three modules outside `models.py` declare a dataclass, and the list is exhaustive: `PrivacyCarrier` (`privacy_carrier.py`, which precedes `models.py` in the dependency order because every node embeds a carrier), `ProvenanceGraph` (`graph.py`), and `ProvenanceEvent` (`projection.py`). Nothing else declares a provenance dataclass.
- Placing `ValidationFinding`, `RecordedRun`, and `DEFAULT_SEVERITIES` here is load-bearing: the recorder (emission layer) and the claim adapter must construct findings and snapshots *before* the validation layer exists in the dependency order.
- Placing `ChainValidationReport` and `CompletenessReport` here is equally load-bearing: `ProvenanceGraph` carries both reports and `graph.py::attach_validation` accepts one, yet `graph.py` sits *before* `validation.py` and `completeness.py` in the dependency order. Declaring the report containers in `models.py` keeps every import pointing leftward.
- All records are `@dataclass(frozen=True)`. Immutability is what makes the recorder append-only in practice.
- Vocabularies are `Literal` unions — closed by design (7.1). Adding a member is a Revalidation Trigger.
- Every node carries `extensions: Mapping[str, Any]` whose keys must match `x-<namespace>-<field>`; a non-conforming key is rejected at construction (10.4).

**Dependencies**
- Outbound: PrivacyCarrierModule — every node embeds a carrier (P0)
- External: none

**Contracts**: State [x]

##### State Management

```python
NodeKind = Literal["source", "evidence", "claim", "derivation", "validation"]
EdgeKind = Literal["cites_evidence", "anchored_in", "derived_from",
                   "has_input", "has_output", "revises", "validates", "belongs_to_source"]
# anchored_in and derived_from are declared but never produced by this spec:
# belongs_to_source / has_input / has_output are derived by GraphBuilder,
# cites_evidence / revises by the claim adapter, validates by attach_validation.
EvidenceKind = Literal["sentence", "figure_caption", "table", "metadata"]
AnchorPrecision = Literal["exact", "approximate", "absent"]
DerivationKind = Literal["extract", "normalize", "chunk", "filter", "merge",
                         "synthesize", "reconcile", "remove", "prefill", "annotate"]
Determinism = Literal["deterministic", "probabilistic"]
Severity = Literal["critical", "error", "warning", "info"]
CompletenessState = Literal["complete", "incomplete", "undetermined"]
ClaimSupport = Literal["supported", "unsupported", "missing_anchor"]
ClaimOrigin = Literal["model", "prefill", "human", "import"]

@dataclass(frozen=True)
class SourceAnchor:
    precision: AnchorPrecision
    page: int | None = None
    coords: tuple[float, float, float, float] | None = None
    section_path: str | None = None
    char_span: tuple[int, int] | None = None
    structural_path: str | None = None
    missing: tuple[str, ...] = ()          # anchor kinds explicitly absent (4.4)

@dataclass(frozen=True)
class ProvenanceNode:
    node_id: str
    kind: NodeKind
    run_id: str
    privacy: PrivacyCarrier
    extensions: Mapping[str, Any]

@dataclass(frozen=True)
class SourceNode(ProvenanceNode):
    document_fingerprint: str
    source_format: str
    source_label: str
    config_fingerprint: str
    schema_fingerprint: str
    model_ids: tuple[str, ...]

@dataclass(frozen=True)
class EvidenceNode(ProvenanceNode):
    local_id: str                          # pipeline-assigned, verbatim (4.7)
    evidence_kind: EvidenceKind
    source_id: str
    anchor: SourceAnchor
    producing_backend: str | None
    rank_score: int | None
    parser_metadata: Mapping[str, Any]     # (10.2)

@dataclass(frozen=True)
class ClaimNode(ProvenanceNode):
    source_id: str
    field_index: int
    field_name: str
    value_digest: str
    origin: ClaimOrigin
    support: ClaimSupport
    cited_evidence_ids: tuple[str, ...]
    confidence: str | None

@dataclass(frozen=True)
class DerivationStep(ProvenanceNode):
    stage: str
    derivation_kind: DerivationKind
    determinism: Determinism
    model_id: str | None
    input_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    removal_reason: str | None = None
    removed_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class ValidationRecord(ProvenanceNode):
    check_name: str
    finding_ids: tuple[str, ...]
    completed: bool

@dataclass(frozen=True)
class ProvenanceEdge:
    edge_id: str
    kind: EdgeKind
    from_id: str
    to_id: str
    attributes: Mapping[str, Any]

@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    config_fingerprint: str
    schema_fingerprint: str
    model_ids: tuple[str, ...]
    document_fingerprints: Mapping[str, str]
    created_at: str

# --- Cross-layer types (owned here, not by the layer that analyses them) ---

@dataclass(frozen=True)
class ValidationFinding:
    finding_id: str
    code: str
    severity: Severity
    node_id: str | None
    edge_id: str | None
    message: str
    details: Mapping[str, Any]

DEFAULT_SEVERITIES: Mapping[str, Severity] = {...}   # see ChainValidator for the table

@dataclass(frozen=True)
class MissingSegment:
    stage: str
    record_kind: str
    reason: str
    affected_ids: tuple[str, ...]

@dataclass(frozen=True)
class StageContract:
    stage: str
    required_record_kinds: tuple[str, ...]
    scope: Literal["run", "document", "claim"]

@dataclass(frozen=True)
class ArtifactCompleteness:
    state: CompletenessState
    reason: str | None
    missing_record_kinds: tuple[str, ...]
    responsible_stages: tuple[str, ...]

@dataclass(frozen=True)
class RecordedRun:
    run: RunIdentity
    nodes: Mapping[str, ProvenanceNode]
    edges: tuple[ProvenanceEdge, ...]
    findings: tuple[ValidationFinding, ...]
    stage_status: Mapping[str, Literal["complete", "failed", "not_run"]]   # run-wide
    stage_failure_reasons: Mapping[str, str]                                # run-wide, first reason
    document_stage_status: Mapping[str, Mapping[str, Literal["complete", "failed", "not_run"]]] = {}
                                                    # source_id -> stage -> status
    document_stage_failure_reasons: Mapping[str, Mapping[str, tuple[str, ...]]] = {}
                                                    # source_id -> stage -> every distinct reason, in recording order

@dataclass(frozen=True)
class ChainValidationReport:
    completed: bool
    incompletion_reason: str | None
    findings: tuple[ValidationFinding, ...]
    counts_by_severity: Mapping[Severity, int]

@dataclass(frozen=True)
class CompletenessReport:
    run: ArtifactCompleteness
    documents: Mapping[str, ArtifactCompleteness]
    claims: Mapping[str, ArtifactCompleteness]
```

- Preconditions: `extensions` keys match `x-<ns>-<field>`; `node_id` non-empty.
- Postconditions: records are hashable and comparable by value.
- `RecordedRun` is an in-memory snapshot and is never serialized, so the per-document stage-status fields are not a `PROVENANCE_SCHEMA_VERSION` change. Both default to empty, so a run that marks stages only run-wide behaves exactly as before.
- Invariants: no record exposes a mutation method; vocabularies are exhaustive.

**Implementation Notes**
- Integration: `src/provenance/__init__.py` re-exports these. Outside `models.py`, only three modules declare a provenance dataclass, and the list is exhaustive: `privacy_carrier.py` (`PrivacyCarrier`), `graph.py` (`ProvenanceGraph`), and `projection.py` (`ProvenanceEvent`).
- Validation: a test enumerates each `Literal` union and asserts the serializer round-trips every member.
- Risks: vocabulary growth pressure from downstream specs — mitigated by `extensions` and by listing vocabulary additions as a Revalidation Trigger.

#### PrivacyCarrierModule

| Field | Detail |
|-------|--------|
| Intent | Define the inert carrier by which privacy labels and disclosure decisions attach to provenance nodes |
| Requirements | 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7 |

**Responsibilities & Constraints**
- Defines exactly one carrier structure (9.1). No second mechanism for attaching privacy metadata exists anywhere in this design.
- `label` and `decision` are **opaque strings** to provenance. Their vocabulary is owned by `privacy-core`.
- Stores supplied values verbatim (9.2); never normalizes, maps, defaults, or infers (9.4).
- Absent label ⇒ `state == "unknown"`, never a substituted value (9.3).
- Conformance checking is **structural only**: required fields present and correctly typed. A structurally invalid carrier yields `state == "rejected"` with `rejection_reason`, and the caller records a `privacy_carrier_invalid` finding (9.6). The node itself is retained.

**Dependencies**
- Inbound: ProvenanceModels — embeds the carrier on every node (P0)
- Outbound: none
- External: none

**Contracts**: Service [x] / State [x]

##### Service Interface

```python
PrivacyState = Literal["unknown", "labeled", "rejected"]

@dataclass(frozen=True)
class PrivacyCarrier:
    state: PrivacyState = "unknown"
    label: str | None = None
    decision: str | None = None
    supplied_by: str | None = None     # component that supplied it (9.5)
    supplied_at: str | None = None     # ISO 8601 timestamp (9.5)
    rejection_reason: str | None = None

UNKNOWN_PRIVACY: PrivacyCarrier = PrivacyCarrier()

def attach_privacy(
    carrier_input: Mapping[str, Any] | None,
    *,
    supplied_by: str,
    supplied_at: str,
) -> PrivacyCarrier: ...

def check_carrier_conformance(carrier_input: Mapping[str, Any]) -> tuple[bool, str | None]: ...
```

- Preconditions: `carrier_input` is `None` or a mapping.
- Postconditions: `None` input ⇒ `UNKNOWN_PRIVACY`; conforming input ⇒ `state == "labeled"` with `label`/`decision` byte-identical to input; non-conforming ⇒ `state == "rejected"` with a reason and `label is None`.
- Invariants: this module contains no branch on the *value* of `label` or `decision`.

**Implementation Notes**
- Integration: `privacy-core` will call `attach_privacy`; until it exists, every node carries `UNKNOWN_PRIVACY` and the graph still assembles and validates (9.7).
- Validation: a test asserts the module's source contains no comparison against any label literal, guarding 9.4 mechanically.
- Risks: pressure to "just add a default label" — explicitly prohibited by 9.3.

#### IdentityService

| Field | Detail |
|-------|--------|
| Intent | Sole derivation and parsing point for every provenance identifier |
| Requirements | 3.1, 3.3, 3.5, 3.6, 4.2, 4.7, 4.8, 5.1, 6.2 |

**Responsibilities & Constraints**
- Derives IDs; never computes document, config, or extraction-map fingerprints — those arrive from `ManifestIdentity` (3.6).
- Evidence IDs adopt the pipeline-assigned local ID verbatim and scope it by source: `f"{source_id}#{local_id}"` (4.7).
- Derivation-step IDs are content-addressed over `(stage, kind, sorted inputs, sorted outputs)`, making recording idempotent and cross-run comparable (3.3).

**Dependencies**
- Inbound: Adapters, Recorder (P0)
- External: `hashlib` (stdlib) (P2)

**Contracts**: Service [x]

##### Service Interface

```python
def derive_source_id(document_fingerprint: str) -> str: ...            # "src:<fingerprint>"
def derive_run_id(identity_fields: Mapping[str, str]) -> str: ...      # "run:<sha256[:16]>"
def format_evidence_id(source_id: str, local_id: str) -> str: ...      # "<source_id>#<local_id>"
def parse_evidence_id(node_id: str) -> tuple[str, str]: ...            # -> (source_id, local_id)
def derive_claim_id(source_id: str, field_index: int, revision: int = 0) -> str: ...
def derive_derivation_id(stage: str, kind: str,
                         input_ids: Sequence[str], output_ids: Sequence[str]) -> str: ...
def derive_validation_id(run_id: str, check_name: str) -> str: ...
def derive_value_digest(value: object) -> str: ...                     # ClaimNode.value_digest: sha256 of canonical JSON ["claim-value", value]
def derive_text_digest(text: str) -> str: ...                          # sha256 hex of UTF-8 text; evidence parser_metadata["text_sha256"]
```

`derive_text_digest` exists so that `hashlib` stays confined to `identity.py` (Boundary test 3); the evidence adapter calls it rather than hashing. It digests evidence text the pipeline already holds — it is not a document, config, or extraction-map fingerprint (3.6).

`derive_run_id` digests the run-wide configuration fingerprint, schema fingerprint, and model ids only — not document fingerprints, schema version, or `created_at` — so same-configuration runs share a `run_id` (see Revalidation Triggers for the distinction from `cost-and-run-reporting`'s run folder name).

- Preconditions: `local_id` is non-empty and contains no `#`.
- Postconditions: `parse_evidence_id(format_evidence_id(s, l)) == (s, l)` for all valid inputs.
- Invariants: all functions are pure; no clock, no randomness, no filesystem access.
- Errors: `InvalidIdentifierError` on malformed input.

**Implementation Notes**
- Validation: Hypothesis property tests for round-trip totality and for order-independence of `derive_derivation_id` under input/output permutation.
- Risks: a downstream spec inventing its own evidence ID — mitigated by 4.8 and by exporting `format_evidence_id` as the only sanctioned constructor.

#### StageContracts

| Field | Detail |
|-------|--------|
| Intent | Declare, as data, what each pipeline stage must emit and in what order stages run |
| Requirements | 2.1, 2.3, 7.6 |

**Responsibilities & Constraints**
- `STAGE_CONTRACTS: Mapping[str, StageContract]` where `StageContract` names the required record kinds and the artifact scope (`run`, `document`, or `claim`).
- `STAGE_ORDER: tuple[str, ...]` gives the deterministic ordering used by the event-log projection (7.6).
- Absence is only detectable against a declaration; therefore the table, not the stage, is authoritative.
- **Declared stages** (in `STAGE_ORDER` order; `src/provenance/contracts.py`):

  | Stage | Required record kinds | Scope | Emission point |
  |---|---|---|---|
  | `document_extraction` | source | document | `extraction_pipeline` |
  | `quality_control` | derivation | document | `extraction_pipeline` |
  | `evidence_index` | evidence | document | `evidence_index` |
  | `metadata_prefill` | claim, derivation | claim | `pdf_processor` |
  | `extraction_chunk` | claim, derivation | claim | `pdf_processor` |
  | `synthesis` | claim, derivation | claim | `pdf_processor` |
  | `chain_validation` | validation | run | `orchestrator` |

  `validation_repair` and `cache_warmup` (pipeline telemetry stage labels, also listed by cost-and-run-reporting) are **not** provenance stages: cache warm-up produces no record, and output repaired by validation repair is attributed to the `extraction_chunk` or `synthesis` stage whose output it repairs. `chain_validation` is marked by PipelineIntegration after `validate_chain` returns (see PipelineIntegration).
- **Extension protocol for downstream specs**: the vocabulary is closed but the table is *extensible*. A spec that introduces new pipeline stages (`evidence-routing`, `multiagent-extraction`) adds one `StageContract` entry per stage to `STAGE_CONTRACTS` and one ordered position per stage to `STAGE_ORDER`, in the same commit that adds the emitting code. It does **not** bypass `ProvenanceRecorder`, does not suppress or catch `UnknownStageError` to avoid declaring, and does not keep a private stage table. Extending the tables is a Revalidation Trigger for this spec but is *not* a `PROVENANCE_SCHEMA_VERSION` bump: `stage` is a free-form string field on `DerivationStep`, so a new stage name is forward-compatible with existing readers.

**Contracts**: State [x]

**Implementation Notes**
- Validation: a test asserts every stage name recorded by `ProvenanceRecorder` appears in `STAGE_CONTRACTS` and in `STAGE_ORDER`, catching table drift. This test is what forces a downstream spec to extend the tables rather than route around them, and it must be re-run whenever a stage is added or renamed.

### Emission Layer

#### ProvenanceRecorder

| Field | Detail |
|-------|--------|
| Intent | Append-only in-run sink that pipeline stages write records into |
| Requirements | 1.2, 1.5, 1.6, 2.1, 2.3, 2.5, 5.4 |

**Responsibilities & Constraints**
- Append-only: exposes only `record_*` and `mark_stage_*`; no update or delete method exists.
- Recording the same `node_id` twice with identical content is a no-op; with differing content it records a `duplicate_identity` finding (default severity `error`) and keeps the first (idempotent retry safety).
- **Stage status is run-wide or per document.** `source_id=None` marks the stage for the whole run (`stage_status`, `stage_failure_reasons`; first reason kept) — used for `chain_validation` and for failures before any document is known. A `source_id` marks that one document (`document_stage_status`, `document_stage_failure_reasons`) and does **not** change the run-wide status. Both levels are escalate-only (`not_run < complete < failed`); per document every distinct failure reason is kept, in recording order.
- Holds an `issue_sink` callable that `src/pipeline/validator.py` can pass so bad `loc` references become recorded issues while the claim is retained (5.4).
- `enabled=False` makes every method a no-op returning the derived ID, so the pipeline never branches on provenance being off.
- Performs no I/O and no network access (1.5).
- **Concurrency**: PDF-level `asyncio` tasks record concurrently. All mutation is guarded by a single `threading.Lock` held only around dictionary and list appends; records are plain immutable values, so no record is ever partially visible. `snapshot()` copies under the same lock.

**Dependencies**
- Inbound: PipelineIntegration (P0)
- Outbound: ProvenanceModels (P0), IdentityService (P0), `utils.logging_utils` (P2)

**Contracts**: Service [x] / State [x]

##### Service Interface

```python
class ProvenanceRecorder:
    def __init__(self, run: RunIdentity, *, enabled: bool = True) -> None: ...
    def record_source(self, node: SourceNode) -> str: ...
    def record_evidence(self, nodes: Sequence[EvidenceNode]) -> tuple[str, ...]: ...
    def record_claim(self, node: ClaimNode, edges: Sequence[ProvenanceEdge]) -> str: ...
    def record_derivation(self, step: DerivationStep) -> str: ...
    def record_validation(self, record: ValidationRecord) -> str: ...
    def record_issue(self, finding: ValidationFinding) -> None: ...
    def issue_sink(self) -> Callable[[str, str, Mapping[str, Any]], None]: ...
    def mark_stage_complete(self, stage: str, *, source_id: str | None = None) -> None: ...
    def mark_stage_failed(self, stage: str, reason: str, *, source_id: str | None = None) -> None: ...
    def snapshot(self) -> RecordedRun: ...
```

- Preconditions: `stage` is declared in `STAGE_CONTRACTS`; `source_id`, when given, is a valid source id (`src:<fingerprint>`).
- Postconditions: `snapshot()` returns an immutable `RecordedRun` carrying nodes, edges, findings, run-wide and per-document stage status; calling it twice returns equal values.
- Invariants: the recorder never removes a previously recorded record.
- Errors: `UnknownStageError` when a stage is not declared (in both enabled and disabled mode); `InvalidIdentifierError` for a malformed `source_id`. The issue sink raises `ValueError` for a finding code outside `DEFAULT_SEVERITIES`.
- Issue-sink contract (`(code, message, details)`): the claim is named only via `details["node_id"]`, which the caller sets to `derive_claim_id(source_id, field_index)`; `details["reference"]` is the raw `loc` value, never stripped or casefolded; `reference_kind` is omitted or `"evidence"`. Only `dangling_reference`, `duplicate_reference`, and `ambiguous_reference` are passed, classified as the claim adapter classifies them.

**Implementation Notes**
- Integration: constructed once per run in `orchestrator.py` and passed explicitly — no module-level singleton, per the no-global-mutation rule.
- Risks: concurrent recording from PDF-level `asyncio` tasks — mitigated by keeping all mutation behind a single lock and recording only plain values.

#### Adapters

| Field | Detail |
|-------|--------|
| Intent | Convert existing pipeline mappings into provenance records without importing `pipeline` |
| Requirements | 1.3, 3.1, 3.2, 3.4, 3.5, 3.7, 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2, 5.5, 5.6, 5.7, 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 10.2 |

**Responsibilities & Constraints**
- Accept `Mapping`/`Sequence[Mapping]` only. This is what keeps the dependency direction legal and what makes the adapters unit-testable with literal dicts.
- `evidence_adapter` maps the item dict field-for-field (`id`→`local_id`, `type`→`evidence_kind` with unknown types recorded as `sentence` and logged, `xpath`→`structural_path`, `score`→`rank_score`), records every available anchor kind on `SourceAnchor` (4.3), and derives `SourceAnchor.precision` by this single rule:
  - `exact` — `page` **and** `coords` both present;
  - `approximate` — not exact, but at least one *real locator* is present: a `page`, a `char_span`, or a structural path that addresses a single element (the `//*[@xml:id='…']` form) (4.5);
  - `absent` — no real locator. A `section_path`, a weak `.//{tag}` structural path, or `coords` without a page are still recorded on the anchor but do **not** by themselves make it usable (4.4).

  `missing` enumerates every anchor kind with no value, for `approximate` and `absent` alike (4.4). A node is *usable* for claim support iff its precision is `exact` or `approximate`. An `absent` anchor may still carry text-search hints (section path, weak structural path) for a reviewer, but its precision is never upgraded.
- **Content rule: content-bearing keys never enter `parser_metadata`.** `evidence_adapter` copies unrecognized item keys into `parser_metadata` (10.2) **except** the declared deny-list `{"text", "annotations", "source_pdf"}`: `text` and `annotations` carry document content, and `source_pdf` is redundant with `SourceNode.source_label`/`document_fingerprint` while an absolute path leaks the operator's filesystem. When `text` is a string, the adapter adds `parser_metadata["text_sha256"] = identity.derive_text_digest(text)` and `parser_metadata["text_chars"] = len(text)`, so a reader can verify recovered text without the artifact carrying it.
- `claim_adapter` sets `origin="prefill"` for fields produced without a model call (5.7) and classifies `support` per the claim-support state machine (5.2, 5.6). Classification fails closed: a cited known id with no entry, or an invalid entry, in `evidence_anchors` is anchorless; `supported` if **any** cited node is usable; all cited nodes anchorless ⇒ `missing_anchor`; every reference unresolved ⇒ `unsupported` plus a `dangling_reference` finding per reference. `value_digest` is `identity.derive_value_digest(value)`. Input keys `revision` and `contributing_claim_ids` produce the claim id's `@r<n>` suffix and its `revises` edges (5.5).
- `derivation_adapter` marks model-performed steps `probabilistic` with the model id, and rule-based steps `deterministic` (6.3, 6.4); it emits one step with many inputs for merges (6.5) and reuses a single input node across many steps for fan-out (6.6); removals carry `removal_reason` and `removed_ids` (6.7).
- `identity_adapter` consumes `ManifestIdentity.to_dict()` and computes no fingerprint of its own (3.6); it copies source metadata without evaluating disclosure (3.7), and produces a source identity from fingerprints alone when descriptive metadata is absent (3.4). It consumes `pdf_content_hash`, `config_hash`, `extraction_map_hash`, and `model_id`; every other key is kept on the `SourceNode` as extension `x-manifest-<key>` when `<key>` satisfies the extension-key charset, and otherwise under the single fallback extension `x-manifest-unnamespaced` (a mapping of raw key → value, mirroring the derivation adapter's `x-qc-unnamespaced`). Values must be JSON-native. `output_path` (`x-manifest-output_path`) is relative to the run output directory and is Requirement 1.3's explicit, resolvable link from the source record to the extraction output; `schema_version` is kept as `x-manifest-schema_version`.
- `run_identity_from_identities` adopts the sorted set of per-document `model_id`s plus `additional_model_ids` (kw-only, default `()`), through which PipelineIntegration supplies the synthesis model when it differs from the chunk model (3.5). The run id therefore changes with the synthesis model.

**`derivation_from_stage_event` — call contract for downstream specs**

This is the adapter every downstream spec calls to record a stage event, so its contract is stated exhaustively here and is binding on callers:

- **`kind` is required and positional, and its value space is the closed `DerivationKind` literal.** There is no default and no inference from `stage`. A caller that omits `kind`, or passes a string outside `DerivationKind`, is a contract violation, not a tolerated extension. Each spec that adds stages must pin an explicit stage→`DerivationKind` mapping in its own design and keep that mapping in one place, so a given stage always produces the same `derivation_kind`.
- **Determinism is the `Determinism` literal (`"deterministic"` | `"probabilistic"`), never a `bool`.** The adapter derives it from `model_id` when the caller does not state it (`model_id is not None` ⇒ `"probabilistic"`), and a caller that states it explicitly must pass the literal. Passing `True`/`False` is rejected; there is no bool→literal coercion, because a bool loses the distinction between "rule-based" and "model-free but still sampled".
- **Any field that is not declared on `DerivationStep` must be carried in `extensions`, under the `x-<ns>-<field>` naming rule** (10.4), where `<ns>` is the owning spec's namespace. `DerivationStep` carries exactly `stage`, `derivation_kind`, `determinism`, `model_id`, `input_ids`, `output_ids`, `removal_reason`, `removed_ids`, plus the `ProvenanceNode` base fields. Fields such as `prompt_version` or `replaced_field_indices` are **not** on the record and must be passed as, e.g., `x-multiagent-prompt_version` and `x-multiagent-replaced_field_indices`. Non-conforming keys are rejected at construction; unrecognized *conforming* keys round-trip through serialization unchanged (10.5).
- **Extending `DerivationKind` is a schema-version-bumping change.** Adding a member is a Revalidation Trigger and requires a `PROVENANCE_SCHEMA_VERSION` bump, because existing readers validate the artifact against the closed union. A downstream spec that needs a new derivation semantic should first attempt to express it with an existing kind plus a namespaced extension; only if that is genuinely unfaithful does it request a vocabulary addition here. This is the opposite of the stage tables, which extend freely because `stage` is a free-form string.

**Contracts**: Service [x]

##### Service Interface

```python
def source_node_from_identity(identity: Mapping[str, str], *, source_label: str,
                              source_format: str, run_id: str) -> SourceNode: ...
def run_identity_from_identities(identities: Mapping[str, Mapping[str, str]],
                                 *, created_at: str,
                                 additional_model_ids: Sequence[str] = ()) -> RunIdentity: ...
def evidence_node_from_item(item: Mapping[str, Any], *, source_id: str,
                            run_id: str, producing_backend: str | None) -> EvidenceNode: ...
def evidence_nodes_from_items(items: Sequence[Mapping[str, Any]], *, source_id: str,
                              run_id: str, producing_backend: str | None) -> list[EvidenceNode]: ...
def claim_nodes_from_fields(fields: Sequence[Mapping[str, Any]], *, source_id: str,
                            run_id: str, known_evidence_ids: AbstractSet[str],
                            origin: ClaimOrigin,
                            evidence_anchors: Mapping[str, SourceAnchor]
                            ) -> tuple[list[ClaimNode], list[ProvenanceEdge], list[ValidationFinding]]: ...
def derivations_from_qc_provenance(provenance: Mapping[str, Any], *, source_id: str,
                                   run_id: str) -> list[DerivationStep]: ...
def derivation_from_stage_event(stage: str, kind: DerivationKind, *, run_id: str,
                                inputs: Sequence[str], outputs: Sequence[str],
                                model_id: str | None = None,
                                determinism: Determinism | None = None,
                                removal_reason: str | None = None,
                                removed_ids: Sequence[str] = (),
                                extensions: Mapping[str, Any] | None = None
                                ) -> DerivationStep: ...
```

`kind` is required and closed; `determinism` is the `Determinism` literal (defaulting to `"probabilistic"` when `model_id` is given and `"deterministic"` otherwise) and never a `bool`; every field not declared on `DerivationStep` travels in `extensions` under `x-<ns>-<field>`.

- Preconditions: mappings use the key names emitted by the current pipeline; unknown keys are preserved (evidence: in `parser_metadata`, minus the content deny-list; identity: as `x-manifest-*`; QC provenance: as `x-qc-*`), never rejected. `kind` is a member of `DerivationKind`; `determinism`, when supplied, is a member of `Determinism`; every `extensions` key matches `x-<ns>-<field>`. `evidence_anchors` maps full evidence node ids (`<source_id>#<local_id>`) to their anchors, built from the same nodes as `known_evidence_ids`.
- Postconditions: adapters are pure and total over malformed **content** — a malformed anchor, kind, score, or extra key yields a node (with `precision="absent"` when no real locator is usable), never an exception. Malformed **identity** is the exception, and the caller converts it:
  - `evidence_node_from_item` raises `InvalidIdentifierError` when an item has no usable id (not a mapping; `id` missing, non-string, empty, or containing `#`); the caller turns it into a recorded `invalid_identifier` issue. `evidence_nodes_from_items` instead skips such items (DEBUG per item, one WARNING per call) because its list return cannot carry findings; PipelineIntegration therefore calls `evidence_node_from_item` per item.
  - `source_node_from_identity` / `run_identity_from_identities` raise `InvalidIdentifierError` on a missing or malformed fingerprint, on disagreeing run-wide fingerprints, or on empty input; the caller catches it and degrades (never aborts extraction).
  - Every adapter raises on caller errors (wrong argument types, malformed `source_id`/`run_id`).
- Invariants: no adapter imports from `pipeline`, `quality_control`, `agents`, or `pdf_extractor`.

**Implementation Notes**
- Integration: called from `evidence_index.py`, `extraction_pipeline.py`, and `pdf_processor.py`; each call site passes the recorder explicitly.
- Validation: adapter tests use literal dicts copied from the real emitted shapes, including the `page=None, coords=None` case and the weak-xpath case.
- Risks: the pipeline's item shape drifting — mitigated by a test asserting the adapter tolerates unknown and missing keys without raising.

### Assembly Layer

#### GraphBuilder

| Field | Detail |
|-------|--------|
| Intent | Assemble a typed provenance graph, preserving whatever succeeded |
| Requirements | 1.3, 1.4, 1.6, 2.5, 7.1, 7.2, 7.3, 7.4, 7.5 |

**Responsibilities & Constraints**
- Every node and edge carries an explicit kind from the closed vocabulary (7.1, 7.2).
- Per-record construction is individually guarded: a record that cannot be materialized becomes a `MissingSegment(stage, record_kind, reason, affected_ids)` and the rest of the graph is still produced (7.4). Construction never raises to the caller.
- A stage that emitted nothing produces no nodes and does not abort assembly (1.6).
- **A failed stage is a missing segment** (2.5, 7.4): a run-wide failure yields one `MissingSegment(stage, record_kind="stage", reason, affected_ids=<all of that stage's derivation-step ids>)`; a per-document failure yields one `record_kind="stage"` segment per `(stage, source_id)` with `affected_ids = (source_id, <that document's derivation-step ids of the stage>)`. Failed records are attributed by the shared stage-attribution rule below; a record with no attributable stage gets `"unattributed"`, which is not a declared stage.
- **Shared attribution rules (single owner).** `graph.py` owns, as public functions, the only implementations of (a) **document attribution** — the source id a node identifier belongs to (a source node is its own document; an evidence or claim node's `source_id`, else the `src:` scope of its identifier; derivation steps and validation records belong to no single document) and the set of documents a derivation step touches through its input and output ids — and (b) **stage attribution** — a derivation step's own `stage`; otherwise the earliest `STAGE_ORDER` stage among the derivation steps that output the node, excluding in-place steps (the node is among that step's inputs and outputs); otherwise the sole declared stage whose contract requires the node's kind; otherwise `"unattributed"`. GraphBuilder (missing-segment stages, per-document stage segments), Projection (event stages), CompletenessComputer (document scoping, responsible stages) and ChainValidator (the source a claim or reference belongs to) all call these functions and keep no copy of either rule. Two tests enforce it: an import guard (none of those modules imports the identifier-shape constants or parsers from `provenance.identity`, or `STAGE_ORDER`) and a generated agreement test over many random graphs (missing-segment stage = event stage = completeness responsible stage; validator and completeness agree on documents).
- **Derives structural edges** from record fields; no adapter emits them: `belongs_to_source` (evidence/claim → `source_id`), `has_input` and `has_output` (derivation step ↔ its `input_ids`/`output_ids`). `anchored_in` and `derived_from` are declared in `EdgeKind` but never produced by this spec. A derived edge to an id that is not a node is reported as dangling, so PipelineIntegration passes only node ids as derivation inputs/outputs.
- Defines `PROVENANCE_SCHEMA_ID` and `PROVENANCE_SCHEMA_VERSION` (in `graph.py`; Serialization imports them) and attaches them to the graph (7.5).
- **Owns `attach_validation` and `attach_completeness`** (8.7): they return a new graph carrying the reports, and `attach_validation` additionally adds `ValidationRecord` nodes and `validates` edges. Serialization merely writes what the graph already holds; it never attaches anything.
- Exposes `path_from_claim(claim_id)` returning the claim → evidence → source chain, which is the traversable path of 1.4.
- Stores references only; document content is never copied onto any node (content rule under Adapters), keeping the artifact separate from but linkable to raw model outputs (1.3) — the source node's `x-manifest-output_path` is the run-relative link to the extraction output.

**Contracts**: Service [x]

##### Service Interface

`MissingSegment`, `RecordedRun`, `ChainValidationReport`, and `CompletenessReport` are all declared in `models.py`; only `ProvenanceGraph` is local to this module. The graph **carries** both reports, so the serialized artifact is a pure function of the graph and the round-trip postcondition holds.

```python
@dataclass(frozen=True)
class ProvenanceGraph:
    schema_id: str
    schema_version: str
    run: RunIdentity
    nodes: Mapping[str, ProvenanceNode]
    edges: tuple[ProvenanceEdge, ...]
    missing_segments: tuple[MissingSegment, ...]
    validation: ChainValidationReport | None = None
    completeness: CompletenessReport | None = None
    def nodes_of_kind(self, kind: NodeKind) -> tuple[ProvenanceNode, ...]: ...
    def edges_from(self, node_id: str) -> tuple[ProvenanceEdge, ...]: ...
    def path_from_claim(self, claim_id: str) -> tuple[ProvenanceNode, ...]: ...

def build_graph(recorded: RecordedRun) -> ProvenanceGraph: ...
def attach_validation(graph: ProvenanceGraph,
                      report: ChainValidationReport) -> ProvenanceGraph: ...
def attach_completeness(graph: ProvenanceGraph,
                        report: CompletenessReport) -> ProvenanceGraph: ...
```

`attach_validation` returns a new graph with `validation` set **and** with one `ValidationRecord` node plus its `validates` edges added (8.7). `attach_completeness` sets `completeness` only; completeness is a computed view, not a node kind. Both are total, non-mutating, and idempotent — attaching twice with the same report yields an equal graph.

- Postconditions: `build_graph` never raises; `len(missing_segments) > 0` iff some record failed to materialize **or** some stage is marked failed (run-wide or for a document).
- Invariants: `nodes` is keyed by `node_id`; edge order is preserved (graph equality depends on it). Every edge endpoint either exists in `nodes` or is reported by the validator — as `dangling_reference`, or as `source_not_represented` for a source id — with one qualification: an edge leaving a record that failed to materialize is debris of that record, already explained by its `MissingSegment`, and is not re-reported (a live node pointing at such a record is reported, flagged `covered_by_missing_segment`).

**Implementation Notes**
- Integration: `attach_validation` returns a new graph carrying validation nodes, satisfying 8.7 without mutating the original; `attach_completeness` is its completeness counterpart and adds no node kind.
- Risks: silently swallowing construction errors — mitigated by requiring every swallowed error to produce a `MissingSegment` with a non-empty `reason`, asserted by test.

### Analysis Layer

#### ChainValidator

| Field | Detail |
|-------|--------|
| Intent | Detect broken chains and classify each finding by severity |
| Requirements | 5.3, 5.4, 5.7, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.6 |

**Responsibilities & Constraints**
- Check set: `dangling_reference` (8.1), `source_not_represented` (8.2), `evidence_out_of_run_context` (8.3), `graph_cycle`, `orphan_node`, `missing_anchor`, `invalid_identifier`, `inconsistent_derivation` (8.4), plus `duplicate_reference`, `ambiguous_reference`, `unsupported_claim`, `prefill_unsupported` (5.3, 5.4, 5.7) and `privacy_carrier_invalid` (9.6). `duplicate_identity` is not graph-derived; the recorder records it and it arrives through `recorded_findings`.
- Every finding carries a severity from `DEFAULT_SEVERITIES` (declared in `models.py`, so emission-layer components can stamp a severity at construction time), overridable via `provenance.severity_overrides` (8.5).
- Default severities: `dangling_reference`/`graph_cycle`/`invalid_identifier` → `critical`; `source_not_represented`/`evidence_out_of_run_context`/`inconsistent_derivation`/`privacy_carrier_invalid`/`duplicate_identity` → `error`; `unsupported_claim`/`prefill_unsupported`/`missing_anchor`/`duplicate_reference`/`ambiguous_reference` → `warning`; `orphan_node` → `info`.
  - `duplicate_identity` is `error` because two same-content PDFs collide on one source id: the second record is dropped while completeness would otherwise report the document complete.
  - `prefill_unsupported` is emitted **in place of** `unsupported_claim` for an `unsupported` claim with `origin="prefill"` — a prefilled value that came from a fallback (paper id as author; year from PDF metadata, first-page text, filename, or `"nr"`) and so cites nothing. It is a dedicated code so the policy is overridable on its own. Adding a finding code is not a schema-version change: codes are free strings in the artifact.
- **Recorded findings** (5.4): findings recorded during the run (`RecordedRun.findings` — recorder `duplicate_identity`, issue-sink findings, claim-adapter reference findings) cannot be reconstructed from the graph, so the caller passes them as `recorded_findings`. They are merged with graph-derived findings and deduplicated first by `finding_id`, then by reference problem `(code, node_id, normalized details["reference"], reference_kind)` — a local evidence id is scoped to the claim's source so the issue sink's full id and the claim adapter's local id of one `loc` meet. Each surviving finding lists the ids it absorbed in `details["merged_finding_ids"]`. A recorded finding with a known code is re-stamped to its effective severity; an unknown code keeps its own.
- Override entries naming an unknown code or a non-`Severity` value are ignored with a WARNING; validation never raises over them and `completed` stays true.
- `orphan_node`: a node connected by no edge to **another node** — `validates` edges, self-loops, and edges to non-node ids do not count; `ValidationRecord` nodes are exempt.
- If a check cannot run, the report records `completed=False` with a reason and the run is reported **unvalidated**, never valid (8.6).
- Cycle detection is an iterative search over derivation and revision dependencies; iterative rather than recursive so a pathological chain cannot exhaust the stack. One `graph_cycle` finding per strongly connected component. An id that is both input and output of **one** step is an in-place transform (e.g. filter or annotate over canonical ids), not a cycle; cycles across steps are still reported. `inconsistent_derivation` fires only for a step with no usable inputs and no outputs.

**Contracts**: Service [x]

##### Service Interface

`ValidationFinding`, `DEFAULT_SEVERITIES`, and `ChainValidationReport` are all declared in `models.py` — the first two because the recorder and the claim adapter construct findings before this layer exists in the dependency order, the third because `ProvenanceGraph` carries it. This module declares no dataclass; it contributes only the function below.

```python
def validate_chain(graph: ProvenanceGraph, *,
                   severity_overrides: Mapping[str, Severity] | None = None,
                   recorded_findings: Sequence[ValidationFinding] = ()
                   ) -> ChainValidationReport: ...
```

- Postconditions: never raises; `completed is False` implies the caller must treat the run as unvalidated; `findings` is ordered deterministically by `(severity_rank, code, node_id, edge_id, finding_id)`; `counts_by_severity` counts merged findings and lists non-zero severities only.
- Invariants: validation is read-only with respect to the graph.

**Implementation Notes**
- Validation: one test per finding code, each constructing the minimal graph that triggers it.
- Risks: severity inflation making every run "critical" — mitigated by the default table above and by counting per severity in the report.

#### CompletenessComputer

| Field | Detail |
|-------|--------|
| Intent | Compute the provenance-completeness state as queryable data at three levels |
| Requirements | 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 5.7 |

**Responsibilities & Constraints**
- Diffs `STAGE_CONTRACTS` against `RecordedRun` stage status and graph content to detect stages that did not emit what they declared (2.1).
- Produces `run`, per-document, and per-claim states (2.2), each with `missing_record_kinds` and `responsible_stages` (2.3).
- A claim whose cited evidence exists but none of it is usable (`precision == "absent"`) is `incomplete` with reason `missing_anchor`; a claim with no resolvable reference is `incomplete` with reason `unsupported`. The two are never merged (2.4). The mixed-citation rule is the claim adapter's: one usable cited node suffices.
- **Prefill policy** (5.7): a claim with `origin="prefill"` that is `incomplete` — reason `unsupported` (a fallback value that cites nothing) or `missing_anchor` (a TEI-header value citing a header metadata node, which GROBID gives no coordinates or `xml:id`) — is reported `incomplete` with its reason **at claim level only**; it does not by itself make its document or the run incomplete. The signal is carried by the `prefill_unsupported` finding (warning) for the unsupported case and by the existing `missing_anchor` finding on the cited header node. A prefill claim that is `undetermined`, and every model-origin claim, follows the normal aggregation.
- **Stage status is evaluated per (document, stage).** The effective status is the worse of the run-wide `stage_status` and that document's `document_stage_status` (`not_run < complete < failed`). Only effectively `complete` stages are diffed against their contracts; `not_run` is never "missing" (e.g. `num_chunks = 1` runs no `extraction_chunk`). A claim-scoped stage that is effectively complete for a document must have emitted claim **and** derivation records for that document (per document, not per claim), else that document is `incomplete` with reason `missing_records`. Claims are tied to a stage through the derivation steps that output them.
- A stage effectively `failed` for a document, or a `MissingSegment` covering an artifact, yields `undetermined` with the recorded reason rather than `complete` (2.5). A **per-document** failure makes only that document — and the claims its stage segment covers — undetermined; other documents stay determinable. A **run-wide** failure makes every document with that stage's steps undetermined, and always makes the run `undetermined` — including for a stage that recorded no derivation steps (e.g. `document_extraction`, or `evidence_index` with only evidence nodes). A per-document status for a `source_id` with no source node makes the run `undetermined`, naming the id.
- `stage_contracts_strict` (from `provenance.stage_contracts_strict`): when true, a recorded stage absent from `contracts` yields `undetermined`; when false, it is ignored. A non-`bool` value raises `TypeError`; PipelineIntegration validates the YAML value first.
- Returns data only. It produces no string report, no CSV, and no file (2.6) — `provenance-audit-export` owns all of that.

**Contracts**: Service [x]

##### Service Interface

`ArtifactCompleteness` and `CompletenessReport` are both declared in `models.py` — the latter because `ProvenanceGraph` carries it. This module declares no dataclass; it contributes only the function below.

```python
def compute_completeness(graph: ProvenanceGraph, recorded: RecordedRun,
                         report: ChainValidationReport | None = None,
                         *, contracts: Mapping[str, StageContract] = STAGE_CONTRACTS,
                         stage_contracts_strict: bool = True
                         ) -> CompletenessReport: ...
```

- Postconditions: state rank is `complete < incomplete < undetermined`. A document state is the most severe of its claims (subject to the prefill policy) and of its own document-scoped contract gaps and failures. The run state is **at least as severe as its worst document**, and can be worse: run-scoped contract gaps, validation `completed=False`, unattributable missing segments, and undeclared (strict) or run-wide failed stages each make it `incomplete` or `undetermined`; an **empty run** (no recorded documents — e.g. every document resumed) is `undetermined`, never `complete`.
- `recorded` must be a snapshot taken **after** PipelineIntegration marks `chain_validation`, so the run-scoped `chain_validation` contract is diffed against its real status.

**Implementation Notes**
- Risks: the contract table drifting from the pipeline — mitigated by the drift test declared under StageContracts.

#### Projection

| Field | Detail |
|-------|--------|
| Intent | Derive an ordered, append-only view of run decisions from the graph |
| Requirements | 7.6 |

**Responsibilities & Constraints**
- Pure function of the graph. Regenerating twice from the same graph yields identical output.
- Ordering key: `(STAGE_ORDER.index(stage), node_kind_rank, node_id)` — total and deterministic without a clock.
- No event is persisted independently of the graph artifact; this de-duplicates xtrace `R-GOV-1` by making the ledger a projection rather than a parallel store.

**Contracts**: Service [x]

##### Service Interface

```python
@dataclass(frozen=True)
class ProvenanceEvent:
    sequence: int
    stage: str
    event_kind: str
    node_id: str
    node_kind: NodeKind
    summary: Mapping[str, Any]

def project_event_log(graph: ProvenanceGraph) -> tuple[ProvenanceEvent, ...]: ...
```

- Invariants: every field of every event is derivable from the graph; the projection module writes no file.

**Implementation Notes**
- Validation: a purity property test (two projections of one graph are equal) and a test asserting `projection.py` performs no I/O.

### Persistence Layer

#### Serialization

| Field | Detail |
|-------|--------|
| Intent | Read and write the graph artifact with schema identity and a version compatibility guard |
| Requirements | 1.3, 7.3, 7.5, 7.7, 10.3, 10.4, 10.5, 10.6 |

**Responsibilities & Constraints**
- Uses `PROVENANCE_SCHEMA_ID = "evitrace/provenance-graph"` and `PROVENANCE_SCHEMA_VERSION = "1.0.0"` (SemVer, matching the `MANIFEST_SCHEMA_VERSION` convention), imported from `provenance.graph`, which defines them. `SUPPORTED_SCHEMA_MAJORS = (1,)`.
- The artifact is self-describing: node kinds, edge kinds, and the vocabulary version are embedded, so it is interpretable without pipeline internals (7.3).
- Namespaced extension fields round-trip unchanged and are visibly distinct from core fields (10.4).
- **Unknown keys of a compatible-minor artifact** (10.5) are preserved where a free-form slot exists: on a node under the extension `x-compat-unknown` (`COMPAT_UNKNOWN_KEY`, a mapping of raw key → value), in edge `attributes`, and in finding `details`. Elsewhere — top level, run identity, missing segments, report containers, completeness entries — they are named (by path) in one WARNING and dropped. Consequently a future minor version **must not** add keys at the top-level, run, segment, or report levels unless it adds an `extensions` slot at the same level in that same change; otherwise it is a major bump. Vocabulary additions are unreadable by older same-major readers and are likewise a major bump.
- Same major version ⇒ interpret. Different major version ⇒ raise `UnrecognizedSchemaVersionError` naming the version found and the versions supported (10.6). A malformed record in a compatible artifact raises `MalformedProvenanceArtifactError`. It never partially materializes an artifact. A graph read from disk keeps the schema version it was read with.
- Writer contract: free-form values (`extensions`, `parser_metadata`, edge `attributes`, finding `details`) must be JSON-native — lists not tuples or sets, `str` keys, finite floats. Tuples read back as lists; sets raise `TypeError` on write.
- Serializes whatever `ValidationRecord` nodes the graph already carries, so a new validation method needs no core type change (10.3). Attaching those nodes is GraphBuilder's job, not this module's.
- Writes exactly one artifact per run to the directory its caller passes; it reads **no** configuration. PipelineIntegration resolves that directory from `provenance.output_subdir` (`PROVENANCE_DIR` is the default only). Defines **no** interoperable or external export format (7.7).

**Contracts**: Batch [x] / State [x]

##### Batch / Job Contract

- Trigger: end of `run_pipeline()`, after validation and completeness.
- Input / validation: a single `ProvenanceGraph` with validation and completeness already attached. The artifact is a pure function of that one argument — serialization takes no second report parameter.
- Output / destination: `<run output dir>/<output_subdir>/provenance_graph.json`, by default `outputs/run_<ts>/provenance/provenance_graph.json`; the directory arrives as `write_graph`'s `path` argument.
- Idempotency & recovery: written to a temp file then `os.replace`, mirroring `save_manifest`. Re-running over an identical graph produces a byte-identical file.

##### Service Interface

```python
def graph_to_dict(graph: ProvenanceGraph) -> dict[str, Any]: ...
def graph_from_dict(payload: Mapping[str, Any]) -> ProvenanceGraph: ...
def write_graph(graph: ProvenanceGraph, path: Path) -> Path: ...
def read_graph(path: Path) -> ProvenanceGraph: ...
class UnrecognizedSchemaVersionError(Exception): ...
class MalformedProvenanceArtifactError(ValueError): ...
COMPAT_UNKNOWN_KEY = "x-compat-unknown"
SUPPORTED_SCHEMA_MAJORS: tuple[int, ...] = (1,)
```

- Postconditions: `graph_from_dict(graph_to_dict(g)) == g` for any graph built by `build_graph`, including one that has since passed through `attach_validation` and `attach_completeness` — which is the state every persisted artifact is in.

**Implementation Notes**
- Risks: growth on large corpora — mitigated by storing references and digests, never document content, on any node (content rule under Adapters).

### Integration Layer

#### PipelineIntegration

| Field | Detail |
|-------|--------|
| Intent | Wire recorder lifecycle and emission call sites into the existing pipeline |
| Requirements | 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.5, 3.1, 3.5, 4.1, 4.3, 4.5, 4.7, 5.1, 5.3, 5.4, 5.5, 5.7, 6.1, 6.7 |

**Responsibilities & Constraints**
- `orchestrator.py` constructs one `ProvenanceRecorder` per run from the resolved config and passes it explicitly down the call chain — no global, no module-level singleton. `enabled` is passed as a real `bool`.
- **Identity and run scope.** PipelineIntegration computes each document's `ManifestIdentity` itself via `manifest.compute_identity(pdf_path, config)` (no production call site computes it today) and builds `RunIdentity` with `run_identity_from_identities(..., additional_model_ids=(synthesis_model,))` when the synthesis model differs from the chunk model (3.5). Manifest sentinels `"nohash"` / `"no_extraction_map"` are treated as missing fingerprints, never as identities. **Resumed documents** — those `process_pdf` skips because the manifest already marks them complete — are excluded from this run's provenance: they are not in `RunIdentity.document_fingerprints` and get no source node. A run in which every document is resumed (or has no usable fingerprint) is an empty run: `compute_completeness` would read it `undetermined`, but as implemented no artifact is written for it (INFO log), because `run_identity_from_identities` refuses an empty document set and `start_run_provenance` returns `None` ("run without provenance"). Two PDFs with the same content fingerprint share one source id: the source is recorded once, a WARNING names both labels, and any conflicting record the second produces surfaces as `duplicate_identity` (error).
- `extraction_pipeline.py` emits the source node from that identity's `to_dict()` (unconsumed keys become `x-manifest-*` extensions via the adapter) and converts `unified.content["provenance"]` into derivation steps. **`quality_control` is not modified**; the conversion happens on the pipeline side.
- `evidence_index.py` emits evidence nodes on **both** the cache-hit and cache-miss paths, so a cached run is not silently provenance-incomplete. It calls `evidence_node_from_item` per item and turns each `InvalidIdentifierError` into a recorded `invalid_identifier` issue. An item whose `section_path` equals the pipeline's `_METADATA_SECTION` (the TEI title) is converted with evidence kind `metadata` (4.1) — a pipeline-side override on a copy of the item; the evidence-index item format, the evidence cache, and the prompt are unchanged.
- **Header metadata evidence.** A pure helper in `evidence_index.py`, `extract_header_metadata(tei_xml) -> HeaderMetadata`, parses the TEI header once and returns the first-author value with its element and the publication year with its matched `<date>` element (the element `_extract_publication_year_from_tei` selects). The author prefill and the TEI-header branch of `resolve_publication_year` read their values from this helper, so prefilled values and their citations cannot drift. From `bundle.tei_xml` (present on both cache paths) PipelineIntegration mints one `metadata` evidence item per found element, with reserved local ids `M-first-author` and `M-publication-date` (never produced by the S/T/F numbering), and records them as evidence of stage `evidence_index`. These items exist only in provenance: they are never added to `bundle.evidence_items`, the evidence cache, or any prompt.
  - GROBID header elements carry neither `coords` nor `xml:id` (verified on all three TEI fixtures), so under the anchor rule a header metadata node is `absent` and a TEI-sourced prefill claim citing it classifies `missing_anchor`. The helper assigns no assumed page; instead the CompletenessComputer prefill policy covers `missing_anchor` as well as `unsupported`, so such a claim is incomplete at claim level only (decision 2026-10-09).
- `pdf_processor.py` emits claim nodes and per-chunk/synthesis derivation steps, passing `evidence_anchors={n.node_id: n.anchor}` built from the same evidence nodes as `known_evidence_ids`, and populating `revision`/`contributing_claim_ids` for synthesized or merged claims so `revises` edges (5.5) are produced on the real path. Prefilled fields 1–2 are emitted with `origin="prefill"` under stage `metadata_prefill` (a `prefill` derivation step whose outputs are the prefilled claim ids). Each prefilled field's `location` cites the header metadata node its value came from — `M-first-author` when the author came from the TEI header, `M-publication-date` when the year's provenance is `tei_header` — and a value from a fallback (paper id as author; year from PDF metadata, first-page text, filename, or `"nr"`) cites nothing. Derivation inputs and outputs are node ids only.
- **Per-document stage marks.** Every document-scoped and claim-scoped stage is marked with `source_id`: `mark_stage_complete(stage, source_id=...)` once the document's records for that stage are recorded (one producing step per document per claim stage marked complete, `metadata_prefill` included), `mark_stage_failed(stage, reason, source_id=...)` when that document's stage fails. Run-wide marks (`source_id=None`) are used only for `chain_validation` and for failures before any document is known.
- `validator.py` gains an optional `issue_sink`; when supplied, unknown/duplicate `loc` IDs are recorded as findings and the claim is retained; when omitted, behavior is unchanged byte-for-byte.
- **The sink must actually be passed at the production call sites** in `pdf_processor.py` (the `validate_chunk_output` and `reconstruct_fields` calls on both the chunk path and the synthesis path). Adding the parameter without wiring it would leave 5.3 and 5.4 satisfied only in tests. The validator has no `source_id`, so `pdf_processor` wraps the sink per document and injects `details["node_id"] = derive_claim_id(source_id, field_index)` and the raw `loc` as `details["reference"]` (issue-sink contract under ProvenanceRecorder).
- **Run end (orchestrator).** After processing: snapshot, `build_graph`, `validate_chain(graph, severity_overrides=..., recorded_findings=recorded.findings)`, `attach_validation`; then mark `chain_validation` run-wide — `complete`, or `failed` with the incompletion reason when the report is not `completed` — take a **post-validation snapshot**, and call `compute_completeness(validated_graph, post_validation_snapshot, stage_contracts_strict=...)` with the YAML value validated as a `bool`; `attach_completeness`; write. Every provenance exception (`UnknownStageError`, `InvalidIdentifierError`, serialization errors, any other) is caught, logged, and degraded — provenance never aborts extraction.
- **Artifact directory.** PipelineIntegration resolves the directory with `run_provenance.artifact_directory(settings)` (`resolve_run_output_path(<validated output_subdir>)`) and passes it to `write_graph`. An `output_subdir` that is empty/blank, contains NUL, is absolute, contains a `..` segment, names the run folder itself (`.`, `outputs`), or resolves outside the run folder is rejected: a WARNING names the value and the default `"provenance"` is used.
- **Caption-sentence locations.** In `_build_items_from_tei`, GROBID's `<figDesc>` holds `<div><p><s>`, so the body `div` loop emits caption sentences as `sentence` items whose `<s>`/`<p>` carry no `coords` — `page=None`, `coords=None`, weak `.//s` path, which the anchor rule makes `absent`. Such a sentence inherits the `page` and `coords` of its enclosing `<figure>` when its own and its `<p>`'s coords are absent. Item ids stay stable: no item is added, removed, or renumbered. Existing evidence caches keep the old shape until regenerated (the cache key does not change). `page` and `coords` **are** prompt bytes — `select_paper_evidence` copies both into each selected item and `serialise_evidence_package` emits them into the paper evidence package embedded in `_shared_paper_prefix` — so this is a declared prompt-content change (see Out of Boundary); the prefix stays byte-stable across chunks of one PDF.
- All emission occurs after prompt assembly and after response parsing. No provenance value may enter `_shared_paper_prefix`.
- **Out-of-scope pipeline defect (follow-up, not planned here):** fallback author and year prefills are labelled confidence `"h"` (`pdf_processor` sets `"h"` for any non-empty value other than `"nr"`; `resolve_publication_year` also returns `"h"` for PDF-metadata years), overstating confidence for values that cite no evidence.

**Dependencies**
- Outbound: every provenance component (P0); `utils.config_utils`, `utils.path_utils` (P1)

**Contracts**: Service [x]

**Implementation Notes**
- Integration: when `provenance.enabled` is `false` the orchestrator constructs no recorder and passes `provenance=None` down the call chain, so behaviour is byte-identical to a build without provenance. The recorder's own `enabled=False` no-op mode still exists (every method returns the derived ID) for direct users of `ProvenanceRecorder`.
- Validation: an end-to-end test with a fabricated `QCBundle` and evidence bundle asserts a graph is produced, validated, and written; a separate regression test asserts `_shared_paper_prefix` output is unchanged by provenance emission (its baseline is taken after the declared caption-location change).
- Risks: `asyncio` PDF-level concurrency interleaving records — mitigated by the recorder's internal lock and value-only records.

##### Implementation deviations (recorded at validation)

What the code does today, where it differs from or fixes the wording above:

- **Signatures.** `build_or_load_evidence_bundle(qc_context, config, *, recorder=None, source_id=None)` (the caller passes the document's `src:<fingerprint>` id; a missing or malformed one logs a WARNING and emits nothing); `build_qc_bundle(pdf_path, pdf_name, qc_config, *, provenance=None)`; `process_pdf(..., collector=None, provenance=None)`; `RepairRetryLoop(..., provenance_sink=None)`; `run_pipeline(..., provenance_config=None)` (the merged `provenance` block; `None` reads `configs/config.yaml`); `pdf_processor.is_resumed(pdf_name, manifest)` applies the same test `process_pdf` uses to skip a completed document (manifest status `complete` and a parseable output file). `validate_chunk_output` / `reconstruct_fields` take `issue_sink=None`.
- **Data types.** `EvidenceBundle` gains `year_provenance` and `provenance_evidence_nodes` (both `compare=False, repr=False`). `evidence_index` exposes `HeaderMetadata` (`first_author`, `first_author_element`, `publication_year`, `publication_date_element`), `extract_header_metadata`, `HEADER_AUTHOR_EVIDENCE_ID` (`M-first-author`) and `HEADER_DATE_EVIDENCE_ID` (`M-publication-date`).
- **Extension key.** The chunk number of an `extraction_chunk` step is carried as the namespaced extension `x-pipeline-chunk` (int) on the derivation step.
- **Repair loop.** Inside `RepairRetryLoop` validation stays strict and sink-less (so unknown-id repair is unchanged); the sink is applied to the final accepted output and to both production `reconstruct_fields` calls. Exhaustion-retain (see Out of Boundary) applies only with a sink.
- **Disabled and empty runs.** Disabled, no recordable document, an unbuildable run identity, or any planning failure all mean `provenance=None`: `build_qc_bundle` and `process_pdf` are called with exactly their pre-provenance arguments and no artifact is written. A fully resumed run therefore writes no artifact (INFO log) rather than an artifact reading `undetermined`.
- **Settings validation** lives in `run_provenance.resolve_provenance_settings`: non-bool `enabled`/`stage_contracts_strict` -> WARNING and `true`; null `severity_overrides` -> `{}` (other non-mapping ignored with a WARNING); `fail_run_on_severity` outside `{null, "critical", "error"}` -> none. The YAML block is read through `config_utils._load_config_yaml` (private) because `load_local_config()` drops it.
- **Run summary.** `fail_run_on_severity` logs at ERROR and sets `ProvenanceRunSummary.failed`; the "run summary" is that dataclass plus one INFO line.

## Data Models

### Domain Model

Aggregates: `RunIdentity` is the root; a `SourceNode` is the per-document aggregate root; `EvidenceNode`, `ClaimNode`, and `DerivationStep` belong to a source; `ValidationRecord` belongs to the run.

```mermaid
graph LR
    RunIdentity --> SourceNode
    SourceNode --> EvidenceNode
    SourceNode --> ClaimNode
    ClaimNode --> EvidenceNode
    DerivationStep --> EvidenceNode
    DerivationStep --> ClaimNode
    RunIdentity --> ValidationRecord
    ValidationRecord --> ClaimNode
```

Invariants:
- Every `EvidenceNode` and `ClaimNode` references exactly one `SourceNode` (8.2).
- `EvidenceNode.node_id` always parses back to `(source_id, local_id)` (4.7).
- A `DerivationStep` has at least one input or at least one output; a step with neither is `inconsistent_derivation`.
- The derivation and revision subgraph is acyclic; a cycle is a `critical` finding (8.4).
- Every node carries a `PrivacyCarrier`, defaulting to `UNKNOWN_PRIVACY` (9.3).

### Logical Data Model

| Entity | Key | Cardinality | Notes |
|---|---|---|---|
| RunIdentity | `run_id` | 1 per run | Digest of `ManifestIdentity` fields shared across documents (config, schema, model ids); shared by same-configuration runs, `created_at` distinguishes them; not the run folder name |
| SourceNode | `src:<document_fingerprint>` | N per run | Fingerprint consumed from `ManifestIdentity.pdf_content_hash` |
| EvidenceNode | `<source_id>#<local_id>` | N per source | `local_id` verbatim from the evidence index; header metadata nodes use the reserved `M-first-author` / `M-publication-date` |
| ClaimNode | `claim:<source_id>#f<field_index>[@r<n>]` | N per source | One per extracted field; revisions add `@r<n>` |
| DerivationStep | `drv:<sha256[:16]>` | N per run | Content-addressed; idempotent on retry |
| ValidationRecord | `val:<run_id>:<check_name>` | N per run | Attached post-validation |
| ProvenanceEdge | `<kind>:<from>-><to>` | N | Deduplicated by key |

### Data Contracts & Integration

**Configuration (`configs/config.yaml`)**

```yaml
provenance:
  enabled: true
  output_subdir: "provenance"
  severity_overrides: {}        # finding code -> severity
  fail_run_on_severity: null    # null | "critical" | "error"
  stage_contracts_strict: true
```

Registered in `_ALL_KNOWN_TOP_LEVEL_KEYS`, defaults in `_PROVENANCE_DEFAULTS`, read via `load_provenance_config(config)`. Each key has exactly one consumer:

| Key | Consumer |
|---|---|
| `enabled` | `ProvenanceRecorder` (no-op mode) and PipelineIntegration (artifact production) |
| `output_subdir` | PipelineIntegration — `run_provenance.artifact_directory` resolves `resolve_run_output_path(output_subdir)` and passes it to `write_graph`; empty/blank, NUL, absolute, `..`-containing, run-folder-naming (`.`, `outputs`) or run-folder-escaping values fall back to `"provenance"` with a WARNING. Serialization reads no config |
| `severity_overrides` | `validate_chain` (per-code severity override) |
| `fail_run_on_severity` | PipelineIntegration — when set, a run whose findings include that severity or worse is logged at ERROR and `ProvenanceRunSummary.failed` is set (there is no separate run-summary artifact and `run_pipeline`'s return type is unchanged); extraction output is still written, because provenance never aborts extraction |
| `stage_contracts_strict` | `compute_completeness` (kw-only argument, passed by PipelineIntegration after validating the value is a `bool`) — when true, a stage absent from `STAGE_CONTRACTS` yields `undetermined`; when false, it is ignored |

**Artifact schema (abbreviated)**

```json
{
  "schema_id": "evitrace/provenance-graph",
  "schema_version": "1.0.0",
  "run": {"run_id": "...", "config_fingerprint": "...", "schema_fingerprint": "...",
          "model_ids": ["..."], "document_fingerprints": {}, "created_at": "..."},
  "nodes": [{"node_id": "...", "kind": "evidence", "run_id": "...",
             "privacy": {"state": "unknown"}, "extensions": {}}],
  "edges": [{"edge_id": "...", "kind": "cites_evidence", "from_id": "...", "to_id": "..."}],
  "missing_segments": [],
  "validation": {"completed": true, "findings": [], "counts_by_severity": {}},
  "completeness": {"run": {"state": "complete"}, "documents": {}, "claims": {}}
}
```

**Compatibility rule**: readers accept any artifact whose major version is in `SUPPORTED_SCHEMA_MAJORS`. Unknown keys are preserved where a free-form slot exists (node `extensions` under `x-compat-unknown`, edge `attributes`, finding `details`) and elsewhere named in a WARNING and dropped (see Serialization); adding keys at the top-level, run, segment, or report levels therefore requires a major bump or an `extensions` slot added in the same change. A differing major raises `UnrecognizedSchemaVersionError`.

## Error Handling

### Error Strategy

Provenance failures must never abort extraction — an unrecoverable provenance error degrades to a recorded finding plus an `incomplete` or `undetermined` completeness state.

| Category | Example | Response |
|---|---|---|
| Malformed input record | Evidence item with no real locator (no page, no char span, no `xml:id` structural path — a section path or weak `.//{tag}` path alone does not count) | `SourceAnchor(precision="absent", missing=(...))`; `missing_anchor` finding (warning) |
| Invalid identifier | `local_id` containing `#` | `InvalidIdentifierError` from `evidence_node_from_item`; PipelineIntegration records an `invalid_identifier` issue (critical) and the rest of the graph builds. A record that reaches the builder but cannot be materialized becomes a `MissingSegment` |
| Unknown stage | Stage name absent from `STAGE_CONTRACTS` | `UnknownStageError` at record time (enabled or not); caught by integration, logged, and the stage is reported `undetermined` |
| Stage failed for one document | One PDF's synthesis call fails | `mark_stage_failed(stage, reason, source_id=...)`; that document and the claims its stage segment covers are `undetermined`; other documents stay determinable |
| Duplicate identity | Two same-content PDFs; conflicting re-record of one id | First record kept; `duplicate_identity` finding (error) |
| Broken chain | Claim cites a nonexistent evidence ID | `dangling_reference` finding (critical); the claim is retained (5.4) |
| Non-conforming privacy carrier | Wrong field types | `state="rejected"` plus `privacy_carrier_invalid` (error); node retained |
| Validation cannot run | Node index unavailable | `completed=False` with reason; run reported unvalidated (8.6) |
| Unknown schema major | Artifact from a future version | `UnrecognizedSchemaVersionError`; no partial materialization (10.6) |
| Provenance disabled | `provenance.enabled: false` | No recorder is constructed (`provenance=None` is passed down); pipeline behavior is byte-identical |

### Monitoring

All provenance logging goes through `utils.logging_utils.get_logger(__name__)`; `print` is never used. One INFO line per run summarizes node counts, findings by severity, and the run completeness state. Individual findings are logged at DEBUG to avoid flooding a large corpus run.

## Testing Strategy

### Unit Tests

1. `format_evidence_id`/`parse_evidence_id` round-trip for every ID shape, and rejection of a `local_id` containing `#` (4.7, 4.8).
2. `attach_privacy` stores label and decision byte-identically, returns `UNKNOWN_PRIVACY` for `None`, and returns `state="rejected"` with a reason for a malformed mapping (9.2, 9.3, 9.6).
3. `evidence_nodes_from_items` classifies anchor precision as `exact`, `approximate`, and `absent` across the real emitted item shapes — including section-path-only and weak-xpath-only items classifying `absent` — enumerates `missing` anchor kinds, and keeps `text`/`annotations`/`source_pdf` out of `parser_metadata` while adding `text_sha256`/`text_chars` (4.3, 4.4, 4.5, 10.2).
4. `derive_derivation_id` is invariant under input and output permutation and stable across processes (6.2, 3.3).
5. `compute_completeness` distinguishes a no-reference claim (`unsupported`) from an anchorless-evidence claim (`missing_anchor`) (2.4).
6. `graph_from_dict(graph_to_dict(g)) == g`, including namespaced extension round-trip (10.4, 10.5).
7. `derivation_from_stage_event` rejects a `kind` outside `DerivationKind` and a `determinism` passed as a `bool`, defaults determinism from `model_id`, and carries a non-`DerivationStep` field (e.g. `x-multiagent-prompt_version`) through `extensions` unchanged while rejecting the same field un-namespaced (6.2, 6.3, 10.4).

### Integration Tests

1. Recorder → graph → validator → completeness → artifact over a fabricated multi-document run: artifact written, schema metadata present, run completeness computed (1.3, 7.5, 2.2).
2. A stage marked failed mid-run still yields a graph containing every other stage's nodes, with a `MissingSegment` and an `undetermined` completeness state; a stage failed for **one** document makes only that document undetermined while the others stay determinable (1.6, 7.4, 2.5).
3. `validate_chain` produces one finding of each code from purpose-built minimal graphs, with the documented default severity for each (8.1–8.5).
4. `validator.py` with an `issue_sink` records unknown-`loc` findings and retains the claim; without a sink, current raise and silent-drop behavior is byte-for-byte unchanged (5.3, 5.4).
5. `project_event_log` over a graph is deterministic across two invocations and contains no field absent from the graph, proving the ledger is a projection (7.6).
6. Every node carries `UNKNOWN_PRIVACY` when no privacy component exists, and the graph still assembles and validates (9.7).

### Boundary and Regression Tests

1. AST dependency-direction tests for all 8 new pairs: `provenance` ↛ {`agents`, `pipeline`, `pdf_extractor`, `quality_control`}; {`quality_control`, `agents`, `pdf_extractor`, `text_processing`} ↛ `provenance`. `pipeline` is the sole integrator and may import `provenance`.
2. `src/provenance/` imports cleanly with `pipeline` and `quality_control` absent from `sys.modules`, and with no network access (1.5).
3. No module under `src/provenance/` computes a document, config, or extraction-map fingerprint — asserted by AST scan for `hashlib` use outside `identity.py` (3.6).
4. `privacy_carrier.py` contains no comparison against any privacy label literal (9.4).
5. `_shared_paper_prefix` output is unchanged by provenance emission (prompt-cache stability constraint); the baseline includes the declared caption-location change, the only prompt-content change this spec makes.
6. Every stage name the recorder accepts is declared in both `STAGE_CONTRACTS` and `STAGE_ORDER` (contract-drift guard).

### Property Tests (Hypothesis)

1. Evidence ID round-trip totality over arbitrary valid `(source_id, local_id)` pairs.
2. `build_graph` never raises for arbitrary well-typed record sets, and every unmaterialized record appears in exactly one `MissingSegment`.
3. `project_event_log` ordering is a total order — no two events share a sequence number.

## Security Considerations

- Provenance stores content fingerprints, identifiers, digests, and anchors. The artifact is **content-free for all nodes**: claim nodes carry a value digest, evidence nodes carry `text_sha256`/`text_chars` instead of text, and content-bearing keys (`text`, `annotations`, `source_pdf`) never enter `parser_metadata`. This bounds what a shared artifact could leak; absolute filesystem paths are excluded for the same reason (`x-manifest-output_path` is run-relative).
- Evidence text remains recoverable from the evidence cache (`evidence_cache_dir`, run-scoped by default; file `<paper stem>_<document fingerprint>_<schema fingerprint>.evidence.json`, item by local id — the same SHA-256 values the source node carries) and can be verified against `text_sha256`. That cache, not the artifact, is the source for `reviewer-ui`'s evidence text (a reviewer-ui revalidation item).
- Provenance makes **no** disclosure decision and applies **no** redaction (3.7, 9.4). The artifact written by this spec is an internal artifact; producing any shareable view is `public-private-provenance`'s responsibility.
- Test fixtures use synthetic identifiers and lorem text only — no PHI, credentials, or real patient data.
- No credential, API key, or model response body is recorded; derivation steps carry only the model identifier string.
