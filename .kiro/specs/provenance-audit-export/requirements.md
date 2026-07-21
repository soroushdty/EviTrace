# Requirements Document

## Project Description (Input)
Academic evaluators, manuscript reviewers, and institutional reviewers have no artifact they can inspect after an EviTrace run finishes. The manifest that exists today is a cache-invalidation record — it carries no evidence references, no claim references, no transformation summary, and no validation status — and the final outputs (`outputs/<paper>.extracted.json`, the flagged-fields report, the W3C annotation export) are not versioned as provenance artifacts and never declare partial fidelity. As a result an incomplete trace and a complete one produce output files that look identical, which is precisely the failure mode the provenance track exists to eliminate.

`provenance-audit-export` makes every run emit a versioned, self-describing run-level provenance artifact containing source identities, evidence and claim references, transformation summaries, validation status, and a pinned reference to the run manifest that carries the reproducibility facts; makes that artifact exportable into at least one interoperable representation that names exactly which fields were omitted or transformed; assembles a per-document audit package whose slots are declared so an unfilled slot is reported rather than silently absent; and makes `provenance-incomplete` and `partially-traceable` first-class reportable states visible at claim, document, and run level. It is a serializer and reporter over the graph that `provenance-core` produces, never a second provenance data model.

## Introduction

This spec turns the provenance graph produced by `provenance-core` into artifacts a human outside the runtime can read, compare, and cite. It owns three things: the **run-level provenance artifact** and its versioning, the **interoperable export** together with the fidelity report that export produces, and the **reporting** of provenance status at claim, document, and run level. It additionally owns the **per-document audit package** — the container into which every pipeline stage's outputs, the parser QC report, and the retained raw parser output are placed.

The governing design principle is **project, do not duplicate**. Everything reported here is derived from the graph, the completeness report, and the chain-validation report that `provenance-core` already computes. `provenance-core` computes the provenance-completeness state and explicitly performs no rendering; this spec is the sole place that state becomes a reported, human- and tool-readable status. Where the graph already offers an ordered projection, that projection is exported rather than reimplemented. No second provenance store, no second fingerprint set, and no second evidence-identity scheme is introduced.

This spec also resolves two obligations that would otherwise ship twice. The `xtrace-toolkit` per-run reproducibility manifest (`R-X-2`: git commit, environment, seeds, resolved configuration) is **owned and produced by `cost-and-run-reporting` as `run_manifest.json`**, which ships earlier and already owns the price, stage, and telemetry facts that manifest carries; this spec consumes it by reference and adds only the per-artifact-file content hashes, which no other spec can produce because only this spec knows the full set of files it emitted. The `xtrace-toolkit` append-only decision ledger (`R-GOV-1`) is delivered here as an export of the core graph's event-log projection, not as a parallel store.

Requirement coverage relative to the archived source documents: prov R9 and prov R11 in full; prov R13 in full (its computation half comes from `provenance-core`, its reporting half is owned here); multiagent R22 in full for the container and its declared slots; multiagent R5.9; the audit-package half of multiagent R6.4–R6.6; the artifact-file-hash and reference half of xtrace `R-X-2` (its manifest half belongs to `cost-and-run-reporting`); and xtrace `R-GOV-1`.

## Boundary Context

- **In scope**: Production of a run-level provenance artifact and its contents; the reproducibility reference section of that artifact — a pinned reference to `cost-and-run-reporting`'s run manifest plus the per-artifact-file content hashes, not a second reproducibility manifest; artifact schema versioning and cross-run comparison support; marking an artifact's sharing suitability as internal or restricted; at least one interoperable, runtime-independent export representation preserving claim-to-evidence relationships; a machine-readable fidelity report naming every provenance field the export omitted or transformed; reporting of provenance status — complete, partially traceable, incomplete, undetermined, unvalidated — at claim, document, and run level, including stage-level failure reporting and the distinction between a missing anchor and an unsupported claim; the per-document audit package, its declared slot inventory, its explicit reporting of unfilled slots, retention of the parser QC report and the raw parser output, preservation of original alongside human-edited output, the route and evidence backing an accepted output, the recorded reason a field was sent to manual review, and export of the package for reproducibility, manuscript supplement, or reviewer inspection.
- **Out of scope**: The provenance graph, node and edge types, evidence identity, derivation records, chain validation, and the computation of the completeness state — all owned by `provenance-core` and consumed here unchanged. Public versus private view generation, redaction of artifact contents, cryptographic commitments, and any tamper-evidence or integrity marker — owned by `public-private-provenance`. Any decision about whether an artifact may be disclosed, and any sensitivity classification — owned by `privacy-core`; this spec records the decision it is given and never makes one. Token and cost accounting content — owned by `cost-and-run-reporting`; this spec owns only the slot that content occupies. The stage outputs themselves — produced by `evidence-routing`, `multiagent-extraction`, `agreement-statistics`, and the existing extraction pipeline; this spec defines the container, not the producers. Parser QC *metric definitions* and parser *agreement* statistics — owned by `agreement-statistics`; this spec retains and packages whatever report is handed to it. Any user interface for browsing artifacts or packages — owned by `reviewer-ui`. Re-litigating the token-budget thresholds set by the completed `token-efficient-extraction` spec.
- **Adjacent expectations**: This spec expects `provenance-core` to supply a materialized provenance graph carrying its chain-validation report and completeness report, and to keep the shape of those reports stable; it recomputes neither. It expects `provenance-core` to remain the single definition of evidence-node identity and the single owner of the run, document, configuration, and schema fingerprints, which this spec restates in artifacts without recomputing. It expects `privacy-core` to supply, where it exists, the disclosure decision that determines an artifact's sharing-suitability marking; while no such decision exists, the marking defaults to the most restrictive value rather than to shareable. It expects stage-producing specs to hand their outputs to the audit package through the declared slot contract rather than writing into the package directory directly. It expects `public-private-provenance` to consume the artifacts produced here as its redaction input, and `evaluation-harness` to consume run artifacts for cross-run comparison. Nothing produced by this spec is verifiable end to end until the `risk-remediation` fix for silently rejected final-output writes ships.
- **Standing product boundaries**: This spec does not admit autonomous systematic review generation, automated clinical recommendations, guaranteed extraction correctness without human validation, OCR-heavy workflows beyond fallback support, or meta-analysis automation. Producing an audit artifact is never a claim that the extraction it describes is correct.

## Requirements

### Requirement 1: Run-Level Provenance Artifact

**Objective:** As an academic or clinical evaluator, I want every completed run to emit a single run-level provenance artifact, so that I can review the evidence workflow after the fact without access to the running system.

#### Acceptance Criteria

1. When a workflow run completes, the audit-export subsystem shall produce exactly one run-level provenance artifact for that run.
2. When a run-level provenance artifact is produced, it shall include the source identity of every document processed in the run, references to the evidence records for those documents, references to the claim records derived from that evidence, a summary of the transformations applied, the validation status of the run, and the run metadata.
3. When the run-level provenance artifact is produced, its contents shall be derived from the provenance records the run already produced, and the audit-export subsystem shall not maintain a provenance store of its own.
4. If a run ends abnormally or a pipeline stage fails, then the audit-export subsystem shall still produce a run-level provenance artifact describing the portion of the run that completed.
5. If no provenance records were produced for a run, then the audit-export subsystem shall produce an artifact that states that no provenance was recorded rather than omitting the artifact.
6. When the run-level provenance artifact is produced, it shall reference the per-document audit packages belonging to that run.
7. The audit-export subsystem shall produce artifacts without requiring a network service or a model-provider credential.

### Requirement 2: Reproducibility Reference

**Objective:** As a researcher reproducing a published result, I want the run artifact to lead me to everything needed to identify the exact code, configuration, and inputs that produced it, so that a run can be re-attempted and its differences explained — reached through a single resolvable reference rather than a second, possibly disagreeing, copy.

#### Acceptance Criteria

1. When a run-level provenance artifact is produced, it shall record a resolvable reference to the run-reporting subsystem's per-run reproducibility manifest — the artifact that carries the source-code revision identifier, the runtime environment description, the resolved configuration in effect, the randomness and determinism settings in effect, and the model identifiers in effect — including that manifest's location, its version, and a content hash pinning the exact manifest the run produced, and it shall not duplicate that manifest's content.
2. When a run-level provenance artifact is produced, it shall record a content hash for every artifact file the run wrote.
3. If the reproducibility manifest cannot be located or read, then the audit-export subsystem shall record that the reference is unavailable together with the reason, rather than omitting the field, recording a placeholder, or substituting its own inline capture of the same facts.
4. When reproducibility metadata is recorded, the audit-export subsystem shall reuse the document, configuration, and schema fingerprints already established for the run and shall not compute a second set.
5. The audit-export subsystem shall capture no configuration mapping of its own for reproducibility purposes and shall therefore record no credential, API key, secret value, or model response body in the reproducibility section.
6. The per-run reproducibility manifest is owned and produced by the run-reporting subsystem; the audit-export subsystem shall consume it by reference and shall not produce a second reproducibility manifest, shall not resolve the source revision itself, and shall not capture the environment, dependency versions, resolved configuration, determinism settings, or model identifiers itself.

### Requirement 3: Artifact Versioning and Cross-Run Comparison

**Objective:** As an evaluator comparing pipeline variants, I want provenance artifacts to be versioned and comparable, so that I can tell what changed between two runs and still read artifacts written by older versions.

#### Acceptance Criteria

1. When a provenance artifact is produced, it shall declare its schema identity and its schema version.
2. When two run-level provenance artifacts for the same input documents are compared, the audit-export subsystem shall report which source identities, claim records, transformation summaries, validation statuses, and provenance statuses differ between them.
3. When two artifacts declaring different schema versions are compared, the audit-export subsystem shall report the version difference and shall report which compared elements are not comparable across those versions.
4. When the artifact schema version changes, artifacts written under an earlier version shall remain readable through their recorded version metadata.
5. If an artifact declares a schema version the reader does not support, then the audit-export subsystem shall report the unrecognized version and shall not present a partially interpreted artifact as if it were fully read.
6. When the same run is re-serialized without intervening changes, the audit-export subsystem shall produce an identical artifact.

### Requirement 4: Sharing-Suitability Marking

**Objective:** As a privacy-conscious investigator, I want every artifact to carry an explicit marking of whether it is suitable for external sharing, so that an internal artifact is never mistaken for a shareable one.

#### Acceptance Criteria

1. When an artifact or audit package is produced, it shall carry an explicit sharing-suitability marking.
2. If an artifact is not suitable for external sharing, then the audit-export subsystem shall mark it as internal or restricted.
3. While no disclosure decision has been supplied for a run's contents, the audit-export subsystem shall apply the most restrictive marking and shall record that no decision was available.
4. The audit-export subsystem shall not classify content sensitivity, evaluate disclosure policy, or decide whether an artifact may be shared.
5. When a sharing-suitability marking is recorded, the audit-export subsystem shall record which component supplied the underlying decision and when it was supplied.
6. The audit-export subsystem shall not remove, mask, or alter artifact contents on the basis of a sharing-suitability marking.

### Requirement 5: Interoperable Export

**Objective:** As a researcher, I want provenance exportable in an interoperable representation, so that claim-to-evidence relationships can be reviewed outside the EviTrace runtime.

#### Acceptance Criteria

1. When provenance is exported, the audit-export subsystem shall support at least one native structured export format.
2. When external review is expected, the audit-export subsystem shall produce a shareable provenance representation that can be read without the EviTrace runtime, its configuration, or its source documents.
3. When provenance is exported, the export shall preserve the relationship between each claim and the evidence records it cites, and the relationship between each evidence record and its source document.
4. When an export is generated, it shall include its schema identity and version information.
5. When an export is generated, it shall carry the same sharing-suitability marking as the artifact it was produced from.
6. When an export is generated, the audit-export subsystem shall not introduce a second producer of the project's existing annotation format.
7. When an export is requested for a run whose provenance is incomplete, the audit-export subsystem shall produce the export and shall carry the incomplete status into it rather than refusing to export.

### Requirement 6: Export Fidelity Reporting

**Objective:** As a reviewer relying on an exported file, I want the export to declare exactly what it could not represent, so that I never assume an absent field was absent from the original.

#### Acceptance Criteria

1. If an export format cannot represent all provenance fields, then the audit-export subsystem shall identify which fields were omitted and which were transformed.
2. When a fidelity report is produced, it shall be machine-readable data accompanying the export rather than only a human-readable note.
3. When a fidelity report is produced, it shall name, for each omitted or transformed element, the provenance element concerned and the reason it could not be represented directly.
4. If a provenance record kind has no defined representation in an export format, then the audit-export subsystem shall report that kind as omitted rather than silently dropping its records.
5. When an export represents a provenance element only approximately, the audit-export subsystem shall report it as transformed rather than as preserved.
6. When an export represents every provenance element without loss, the audit-export subsystem shall still produce a fidelity report stating that nothing was omitted or transformed.

### Requirement 7: Provenance Status Reporting

**Objective:** As a workflow operator, I want provenance failures and gaps reported explicitly at every level, so that users never mistake an incomplete trace for validated evidence.

#### Acceptance Criteria

1. When provenance generation fails, the audit-export subsystem shall report the failure together with the pipeline stage at which it occurred.
2. When only partial provenance is available for an output, the audit-export subsystem shall report that output as partially traceable.
3. If evidence anchors are missing for a claim, then the audit-export subsystem shall report that claim as having a missing anchor and shall report it distinctly from a claim that cites no evidence at all.
4. When validation could not be completed, the audit-export subsystem shall report the recorded reason and shall report the run as unvalidated rather than as valid.
5. When provenance status is reported, it shall be reported at claim level, at document level, and at run level.
6. When provenance status is reported, the audit-export subsystem shall use the completeness and validation results already computed for the run and shall not recompute or override them.
7. When provenance status is reported, a status of complete shall be distinguishable in the reported output from a status of incomplete, partially traceable, undetermined, and unvalidated.
8. Where a run's provenance status is anything other than complete, that status shall appear in the run-level provenance artifact, in every export produced from it, and in each affected document's audit package.

### Requirement 8: Per-Document Audit Package

**Objective:** As a researcher, I want every processing stage's output saved per document, so that results are reproducible and defensible.

#### Acceptance Criteria

1. When a document is processed, the audit-export subsystem shall create an audit package for that document.
2. The audit package shall provide a declared slot for each of the input manifest, the parser outputs, the canonical document, the cleaned document, the parser quality-control report, the route map, the route quality control, the counterfactual route output, the final route map, the extraction outputs, the agreement report, the verification output, the repair output, the final extraction, the cost report, and the run logs.
3. When an audit package is assembled, the audit-export subsystem shall record the inventory of its declared slots together with which slots were filled and which were not.
4. If a declared slot was not filled, then the audit-export subsystem shall record it as absent together with the reason, and shall not present the package as complete.
5. When a producing stage supplies content for a slot, the audit-export subsystem shall record which stage supplied it and shall retain the content unmodified.
6. When a slot's content belongs to a capability that is not enabled or not yet implemented for the run, the audit-export subsystem shall record that slot as not applicable and shall distinguish it from a slot that was expected but missing.
7. The audit-export subsystem shall not itself generate the content of any stage output slot.

### Requirement 9: Parser Quality Control and Raw Parser Output Retention

**Objective:** As a reviewer judging whether extraction was reliable, I want the parser quality-control report and the untouched parser output kept with the document's audit package, so that I can check what the cleaned, model-facing text was derived from.

#### Acceptance Criteria

1. When parser quality control has completed for a document, the audit-export subsystem shall save the parser quality-control report into that document's audit package.
2. When cleaned text is generated for a document, the audit-export subsystem shall retain the original raw parser output in that document's audit package.
3. When more than one extraction backend produced output for a document, the audit-export subsystem shall retain each backend's raw output distinguishably rather than retaining only the selected one.
4. When content was removed during cleaning, the audit-export subsystem shall include the recorded removed blocks and their removal reasons in the audit package.
5. Where a removed section was flagged as potentially evidence-bearing, the audit-export subsystem shall report that flag in the audit package.
6. If parser quality control did not run or produced no report, then the audit-export subsystem shall record that slot as absent together with the reason.
7. The audit-export subsystem shall not compute, alter, or reinterpret parser quality-control metrics or removal decisions.

### Requirement 10: Accepted-Output, Human-Edit, and Manual-Review Records

**Objective:** As a manuscript reviewer, I want to see which evidence backed each accepted value and what a human changed, so that human and model contributions to a result are distinguishable.

#### Acceptance Criteria

1. When an output is accepted, the audit package shall identify the route and the evidence records supporting that output.
2. When a human edits an extraction, the audit package shall preserve both the original model output and the edited output.
3. When a human-edited output is preserved, the audit package shall record that the value's origin is human rather than model.
4. When a field is sent to manual review, the audit package shall preserve the recorded reason it was sent.
5. If an accepted output has no route or supporting evidence recorded, then the audit-export subsystem shall report that output as unsupported in the audit package rather than omitting the entry.
6. The audit-export subsystem shall not decide whether an output is accepted, edit any output, or select the route backing an output.

### Requirement 11: Audit Package Export and Portability

**Objective:** As a researcher preparing a manuscript supplement, I want to export an audit package as a self-contained bundle, so that a reviewer can inspect a document's full processing history without access to my system.

#### Acceptance Criteria

1. When a user requests an audit package export, the audit-export subsystem shall produce a self-contained bundle for the selected document or documents.
2. When an audit package is exported, the bundle shall be readable without the EviTrace runtime and shall declare its schema identity and version.
3. When an audit package is exported, the bundle shall include the package's slot inventory, its unfilled-slot records, and its provenance status.
4. When an audit package is exported, the bundle shall carry the sharing-suitability marking of the package it was produced from.
5. If an audit package is incomplete, then the audit-export subsystem shall export it with its incompleteness declared rather than refusing the export.
6. When the same audit package is exported twice without intervening changes, the audit-export subsystem shall produce an identical bundle.

### Requirement 12: Append-Only Decision Log Export

**Objective:** As a governance reviewer, I want an ordered, append-only log of the decisions a run made, so that the sequence of routing, extraction, quality-control, and review decisions can be audited without reading pipeline internals.

#### Acceptance Criteria

1. When a run completes, the audit-export subsystem shall export an ordered, append-only log of the run's recorded decisions.
2. When the decision log is exported, it shall be derived from the run's existing provenance records, and the audit-export subsystem shall not maintain a separate decision store.
3. When the decision log is exported twice from the same run records, the audit-export subsystem shall produce an identical log.
4. When each decision entry is exported, it shall identify the pipeline stage, the record it concerns, and its position in the run's order.
5. Where an append-only decision ledger is expected by another part of the project, it shall be satisfied by this export rather than by a second ledger implementation.
