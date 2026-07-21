# Requirements Document

## Project Description (Input)
Biomedical AI researchers, clinical evaluators, and institutional reviewers cannot currently answer "which exact span of which exact document version supports this extracted field?" without reading EviTrace's internals. Traceability exists only as incidental by-products of unrelated stages, so an output that lost its evidence link is indistinguishable from one that never had it (prov R1.4). That makes extraction results undefensible in review settings.

`provenance-core` introduces a first-class provenance subsystem: source objects, evidence nodes, claims, derivation steps, and validation results represented as typed, graph-compatible records; the pipeline emits them as it runs; a chain validator reports missing, orphan, cyclic, and unanchored structures by severity; and every downstream spec consumes one canonical evidence-node identity instead of inventing its own. It also pins the privacy-to-provenance interface — **privacy decides, provenance consumes** — by defining the label and disclosure-decision carrier structure that provenance nodes carry.

## Introduction

This spec establishes provenance as core infrastructure rather than an incidental logging side effect. It defines the provenance record model (sources, evidence nodes, claims, derivation steps, validation results), the identity rules for those records, the typed graph assembled from them, and the chain validator that reports broken evidence chains by severity.

The design principle is **promote, do not duplicate**. Identity-bearing artifacts already exist in the pipeline — the ranked evidence index assigns per-run evidence identifiers, the run checkpoint already fingerprints document content and resolved configuration, and the chunk validator already checks model-cited evidence references. Those artifacts are adopted as provenance records rather than shadowed by a parallel store. This same rule resolves the overlap with the existing `xtrace-toolkit` spec: its append-only decision ledger (`R-GOV-1`) becomes an ordered projection of this graph, and its per-run reproducibility manifest (`R-X-2`) shares this spec's single set of run fingerprints instead of computing its own.

Two requirements deliberately straddle a seam with adjacent specs and are split here rather than duplicated. The "provenance-incomplete" state (prov R1.4, prov R13) is **computed** by this spec and **reported** by `provenance-audit-export`. Privacy labels on provenance nodes (prov R12.1) have their **carrier structure** owned here and their **consultation and disclosure logic** owned by `public-private-provenance`, with population owned by `privacy-core`.

Requirement coverage relative to the archived source document `provenance_requirements.md`: prov R1, R2, R3, R4, R5, R6, R8, R14 in full; prov R13 computation half only; prov R12.1 carrier half only. Prov R7, R9, R10, R11, and the remainder of R12/R13 are out of scope and belong to `provenance-audit-export` and `public-private-provenance`.

## Boundary Context

- **In scope**: The provenance record model and its identity rules; source and run identity derived from content fingerprints; the evidence node model including source anchors and explicit anchor-absence; claim-to-evidence linking, unsupported-claim marking, and evidence-reference validation issues; derivation steps including the deterministic/probabilistic distinction and many-to-one and one-to-many relationships; typed graph assembly with partial-graph preservation and schema/version metadata; chain validation with severity classification; computation of the provenance-completeness state; the read-only carrier structure for privacy labels and disclosure decisions; namespaced extension metadata and version-forward interpretation.
- **Out of scope**: Run-level audit packages, export formats, and reporting of the provenance-completeness state (owned by `provenance-audit-export`). Public versus private view generation, disclosure coordination, evidence vaults, cryptographic commitments, and tamper-evidence (owned by `public-private-provenance`). Any sensitivity classification, disclosure policy evaluation, or decision about whether a node may be disclosed (owned by `privacy-core`). Graph visualization, a provenance query language, and any user interface. Replacing the existing sole producer of W3C annotation artifacts — this spec feeds it, it does not fork it.
- **Adjacent expectations**: This spec expects the extraction pipeline to continue producing a ranked evidence index with per-run identifiers and location anchors, and to continue recording per-run document, configuration, and schema fingerprints in its checkpoint; provenance adopts both rather than recomputing them. It expects `privacy-core` to populate the privacy carrier defined here, and `provenance-audit-export` to consume the completeness state and validation findings computed here without recomputing them. It expects `evidence-routing`, `reviewer-ui`, and the privacy specs to consume the evidence node identity defined here rather than defining their own. It does not own, and must not absorb, any of those consumers' behavior. Nothing produced by this spec is verifiable end to end until the `risk-remediation` fix for silently rejected final-output writes ships.
- **Standing product boundaries**: This spec does not admit autonomous systematic review generation, automated clinical recommendations, guaranteed extraction correctness without human validation, OCR-heavy workflows beyond fallback support, or meta-analysis automation.

## Requirements

### Requirement 1: First-Class Provenance Subsystem

**Objective:** As a biomedical AI researcher, I want provenance to exist as a first-class subsystem, so that evidence traceability is core infrastructure rather than an optional logging feature.

#### Acceptance Criteria

1. When the pipeline processes a source document, the provenance subsystem shall represent that document as a provenance source record.
2. When a pipeline stage produces evidence, claims, or transformations, the provenance subsystem shall accept those as provenance records without requiring the emitting stage to know how the records are later assembled.
3. When provenance records are persisted, the provenance subsystem shall store them separately from raw model output artifacts while retaining a resolvable link back to those artifacts.
4. When a reviewer inspects an output claim, the provenance subsystem shall expose a traversable path from that claim to the evidence records and the source record that supported it.
5. The provenance subsystem shall operate without requiring a network service or model-provider credential.
6. When a pipeline stage emits no provenance records, the provenance subsystem shall continue to assemble records from the stages that did emit them rather than aborting the run.

### Requirement 2: Provenance Completeness Computation

**Objective:** As a workflow operator, I want incomplete provenance to be computed and marked explicitly, so that an incomplete trace is never mistaken for validated evidence.

#### Acceptance Criteria

1. If a pipeline stage completes without emitting the provenance records its stage contract declares, then the provenance subsystem shall compute a provenance-incomplete state for each affected artifact.
2. When completeness is computed, the provenance subsystem shall record a completeness state at claim level, document level, and run level.
3. When an artifact is provenance-incomplete, the provenance subsystem shall record which expected record kinds were absent and which pipeline stage was responsible.
4. If an artifact has evidence references but no resolvable source anchor, then the provenance subsystem shall classify it as missing-anchor and shall not classify it as unsupported.
5. If completeness cannot be determined for an artifact, then the provenance subsystem shall record an undetermined state together with the reason rather than defaulting to complete.
6. The provenance subsystem shall expose the computed completeness state as queryable data and shall not format, render, or export a completeness report.

### Requirement 3: Source Document and Run Identity

**Objective:** As an evaluator, I want every source document and every run to have a stable identity, so that an output can be traced back to the exact document version and run configuration that produced it.

#### Acceptance Criteria

1. When a document enters the workflow, the provenance subsystem shall derive a stable source document identifier from the document's content fingerprint.
2. When a source record is created, the provenance subsystem shall record metadata sufficient to distinguish document versions, source formats, and the run in which the document was processed.
3. When document content changes between runs, the provenance subsystem shall produce a different source document identifier so that the new document state is distinguishable from prior states.
4. If a source document carries no usable descriptive metadata, then the provenance subsystem shall still create a source identity from content-derived fingerprints alone.
5. When a run begins, the provenance subsystem shall record a run identity that includes the resolved configuration fingerprint, the extraction schema fingerprint, and the model identifiers in effect.
6. The provenance subsystem shall maintain exactly one set of document, configuration, and schema fingerprints per run; a second, independently computed set shall not exist.
7. The provenance subsystem shall not decide whether any element of source metadata may be disclosed.

### Requirement 4: Evidence Node Model and Source Anchors

**Objective:** As a researcher, I want extracted evidence represented as structured evidence nodes with explicit anchors, so that claims link to specific evidence units in specific document locations rather than to whole documents.

#### Acceptance Criteria

1. When text, table content, figure-derived text, or document metadata is extracted from a source document, the provenance subsystem shall represent it as an evidence node carrying its evidence kind.
2. When an evidence node is created, the provenance subsystem shall assign it an evidence identifier that remains stable for the duration of the run context.
3. When location information is available, the evidence node shall preserve every available source anchor, including page, section path, character or token span, bounding box, and structural path.
4. If exact location information is unavailable for an evidence node, then the provenance subsystem shall record an explicit anchor-absence marker naming which anchor kinds are missing.
5. Where only approximate location information is available, the provenance subsystem shall mark the node's anchor precision as approximate rather than exact.
6. When an evidence node originates from a specific extraction backend, the provenance subsystem shall record the producing backend on that node.
7. When the pipeline has already assigned an identifier to an evidence item, the provenance subsystem shall adopt that identifier rather than issuing a competing identifier for the same item.
8. The provenance subsystem shall be the single definition of evidence node identity for the project; no consumer shall be required to derive its own.

### Requirement 5: Claim-to-Evidence Traceability

**Objective:** As a clinical AI evaluator, I want each generated claim to cite evidence nodes, so that unsupported or weakly supported model outputs can be identified.

#### Acceptance Criteria

1. When a model output contains a factual claim, the provenance subsystem shall represent that claim as a claim record linkable to one or more evidence nodes.
2. If a claim carries no evidence reference, then the provenance subsystem shall mark that claim as unsupported.
3. When a claim cites evidence, the provenance subsystem shall verify that each cited evidence identifier exists in the evidence set for that run.
4. If a cited evidence reference is invalid, missing, duplicated, or ambiguous, then the provenance subsystem shall record a provenance validation issue naming the claim, the offending reference, and the issue kind, and shall retain the claim rather than discarding it.
5. When a claim is revised, synthesized, or merged from multiple intermediate outputs, the provenance subsystem shall preserve a link from the resulting claim to each contributing claim.
6. The provenance subsystem shall distinguish a claim whose cited evidence lacks an anchor from a claim that cites no evidence at all.
7. When a claim's value was produced without a model call, the provenance subsystem shall record that origin so the claim is not attributed to a model.

### Requirement 6: Derivation and Transformation Tracking

**Objective:** As an infrastructure reviewer, I want transformations recorded explicitly, so that I can understand how raw evidence became model-visible evidence, intermediate claims, and final outputs.

#### Acceptance Criteria

1. When evidence or claims are transformed, summarized, chunked, normalized, filtered, merged, or removed, the provenance subsystem shall record a derivation step.
2. When a derivation step is recorded, it shall identify the input artifacts, the output artifacts, the transformation kind, and the responsible pipeline stage.
3. If a transformation is performed by a language model or other probabilistic component, then the derivation step shall be marked probabilistic and shall record the model identity in effect.
4. When a transformation is performed by a rule-based or deterministic component, the derivation step shall be marked deterministic.
5. When multiple inputs contribute to a single output, the provenance subsystem shall represent the relationship as one derivation step with multiple inputs.
6. When one input contributes to multiple outputs, the provenance subsystem shall represent every resulting relationship without duplicating the input artifact.
7. When content is removed or discarded by a pipeline stage, the derivation step shall record the removal reason and the identity of what was removed.

### Requirement 7: Typed Provenance Graph Construction

**Objective:** As a developer or researcher, I want provenance relationships to be graph-compatible and typed, so that evidence workflows can be inspected and audited without reading pipeline internals.

#### Acceptance Criteria

1. When provenance records are assembled, the provenance subsystem shall produce a graph in which every node and every edge carries an explicit type drawn from a declared type vocabulary.
2. The provenance graph shall represent sources, evidence nodes, claims, derivation steps, and validation results as distinct node types.
3. When a graph is materialized, its structure shall be interpretable from the graph content and its schema metadata alone, without access to pipeline internals.
4. If graph construction fails for part of a run, then the provenance subsystem shall preserve the successfully constructed portion and record which segments are missing and why.
5. When a graph is materialized, it shall carry schema identity and schema version metadata.
6. Where an ordered, append-only view of run decisions is required, it shall be derivable as a projection of the provenance graph, and the provenance subsystem shall not maintain a separate decision store.
7. The provenance subsystem shall not define an interoperable export format or a query language for the graph.

### Requirement 8: Provenance Chain Validation

**Objective:** As a system maintainer, I want provenance chains validated with severity-classified findings, so that broken evidence chains are detected early and triaged rather than reported as a single generic failure.

#### Acceptance Criteria

1. When a provenance graph is validated, the provenance subsystem shall verify that every referenced node exists in the graph.
2. When an evidence node references a source document, the provenance subsystem shall verify that the referenced source is represented in the graph.
3. When a claim references evidence, the provenance subsystem shall verify that the referenced evidence is available in the run context.
4. If graph cycles, orphan nodes, missing anchors, invalid identifiers, or inconsistent derivations are detected, then the provenance subsystem shall produce a validation report enumerating each finding with the identity of the node or edge involved.
5. When validation findings are produced, the provenance subsystem shall classify each finding by severity rather than reporting a single generic pass or fail.
6. If validation cannot be completed, then the provenance subsystem shall record the reason and report the run as unvalidated rather than as valid.
7. When validation completes, its results shall be attachable to the graph as validation-result records.

### Requirement 9: Privacy Label and Disclosure Decision Carrier

**Objective:** As a privacy-conscious investigator, I want provenance nodes to carry privacy labels and disclosure decisions produced elsewhere, so that traceability integrates with disclosure control without provenance ever deciding anything.

#### Acceptance Criteria

1. The provenance subsystem shall define exactly one carrier structure by which a privacy label and a disclosure decision can be associated with any provenance node.
2. When a privacy label or disclosure decision is supplied for a node, the provenance subsystem shall store it on that node unmodified.
3. While no privacy label has been supplied for a node, the provenance subsystem shall represent that node's privacy state as unknown and shall not infer a value.
4. The provenance subsystem shall not classify content sensitivity, evaluate disclosure policy, or decide whether a node may be disclosed.
5. When a consumer reads a node's privacy carrier, the provenance subsystem shall expose which component supplied the label and when it was supplied.
6. If a supplied privacy label or disclosure decision does not conform to the declared carrier structure, then the provenance subsystem shall reject it and record a validation issue rather than storing a partially understood value.
7. When the privacy carrier is absent for every node in a run, the provenance subsystem shall still assemble and validate the graph.

### Requirement 10: Extensibility and Version-Forward Interpretation

**Objective:** As a researcher, I want the provenance model to be extensible, so that new evidence types, parsers, and validation strategies can be added without redesigning the system or losing older artifacts.

#### Acceptance Criteria

1. When a new evidence kind is introduced, the provenance model shall represent it as its own kind rather than collapsing it into generic text evidence.
2. When a new parser or extraction branch is added, the provenance subsystem shall preserve that parser's own metadata on the nodes it produced.
3. When a new validation method is added, its results shall be attachable to provenance artifacts without changing the core node types.
4. If an extension supplies metadata outside the declared schema, then the provenance subsystem shall preserve it in a namespaced form that is clearly distinguishable from core fields.
5. When the provenance schema version changes, artifacts written under an earlier schema version shall remain interpretable through their recorded version metadata.
6. If an artifact declares a schema version the reader does not recognize, then the provenance subsystem shall report the unrecognized version rather than silently misinterpreting the artifact.
