# Requirements Document

## Project Description (Input)

Human reviewers are the final authority on every extracted value, but today they have no way to do that work. Verifying a field means opening `outputs/<paper>.extracted.json` in a text editor and hunting for the quoted sentence in the PDF by hand — the exact task evidence anchors were built to eliminate. Accepting, editing, or rejecting a value requires hand-editing JSON, which destroys the distinction between model output and human correction. The system therefore cannot deliver human-in-the-loop review, and none of the human-facing success metrics (correction burden, time saved, manual-review rate) can be measured.

There is no user interface of any kind in the repository: no web framework, no HTTP server, no templates, no static assets, no client toolchain. Outputs are files only. Export is thin — the existing CSV exporter flattens extracted JSON to field name and value alone, with no evidence text, evidence identifiers, pages, confidence, verification status, review status, or provenance. No reviewer identity, review action, edit history, or manual-review surface exists.

This feature introduces a reviewer workspace: a document-centred review surface that renders the source PDF, highlights the evidence behind each extracted value, and lets a reviewer accept, edit, reject, mark not reported, add evidence, or request re-extraction — with every action recorded against a reviewer identity and a timestamp, and with the original model output preserved beside every human correction. It also delivers the final merger that expands compact agent output into full records and the export surfaces that turn a reviewed corpus into per-document JSON, master JSON, review tables, and manual-review queues.

## Introduction

This spec covers multiagent R19 (PDF reader and annotation interface), R21 (final merger and output generation), R20.6 (the manual evidence-linking surface that `corpus-and-schema-builder` deliberately carved out and left as a read-only unresolved queue), multiagent R26.7 (review actions carry reviewer identity or a configured anonymized reviewer identifier), and the four Usability non-functional requirements: a document-processing status timeline, visual distinction between model-generated, imported, and human-edited evidence, visible uncertainty through confidence, verification status, parser risk, and review state, and keyboard shortcuts for accept, edit, and reject.

The feature is sequenced last by explicit roadmap decision — headless core first, interface as a consumer. It is a **view and command surface over artifacts that already exist**. It consumes evidence anchors produced by `provenance-core` rather than re-deriving them from the PDF; it consumes extracted values, confidence, verification verdicts, and adjudication outcomes produced by `multiagent-extraction`; it consumes projects, documents, schema versions, and the unresolved evidence queue produced by `corpus-and-schema-builder`; and it renders the cost report whose content is owned by `cost-and-run-reporting`.

Two separations are load-bearing and are stated here as requirements rather than left to design. First, the **review decision store is headless**: review actions are recorded as replayable, auditable data that can be read, projected, and exported with the interface absent. Second, the **merger and the exporters are library functions**: they are callable from a command line and from other code without a browser, a display server, or the interface's runtime being installed. The interactive surface triggers them; it does not own them.

## Boundary Context

- **In scope**: launching a local, single-reviewer review workspace and keeping it strictly optional to the headless pipeline; rendering a source document with evidence highlighting, including an explicitly-labelled approximate fallback where coordinates are unavailable; navigation from a field to its evidence and from a highlight back to its field; the inspection panel showing field name, extracted value, evidence text, confidence, verification status, and review status; the review action set (accept, edit, reject, mark not reported, add evidence, request re-extraction) and keyboard shortcuts for accept, edit, and reject; preservation of the original model output alongside every human edit, with a full edit history; creation of manual evidence linked to a page, text span, paragraph identifier, or note; the interactive linking surface for unresolved imported evidence; the reviewer identity model, including a configured anonymized reviewer identifier; the append-only review decision store, its replay, and its auditability without the interface; the document-processing status timeline; the display of evidence origin and of uncertainty signals; the final merger that expands compact agent output into full records; export of per-document JSON, master JSON, CSV and Excel review tables, and manual-review queues; surfacing of optional quality-control, agreement, cost, and audit artifacts by reference; and stamping outputs with the schema version, model identities, parser identities, and configuration in effect.
- **Out of scope**: producing, re-deriving, or correcting evidence anchors, coordinates, or annotation artifacts — the existing sole producer of annotation artifacts remains the only producer, and this feature consumes its output; defining evidence node identity, claim records, derivation records, or the provenance graph; running agents — "request re-extraction" enqueues work and never executes it; computing agreement statistics, cost figures, token counts, parser risk, verification verdicts, or adjudication outcomes; the audit package format; the content of the cost report; authentication, authorisation, multi-tenancy, hosted deployment, and concurrent multi-reviewer conflict resolution; deciding what a viewer is permitted to see — disclosure and redaction decisions are made elsewhere and rendered here; benchmark, ablation, and success-metric reporting.
- **Adjacent expectations**: this feature expects evidence anchors, anchor precision, and anchor-absence markers to arrive from the provenance subsystem and to be rendered as received, including the marking of approximate precision; it expects extracted values, confidence labels, verification verdicts, adjudication outcomes, and per-field decision records to arrive from the extraction layer as recorded run data; it expects projects, admitted documents, document status vocabulary and history, schema versions, imported-evidence origin metadata, and the unresolved evidence queue to arrive from the corpus layer; it expects the cost report and the run reproducibility record to be produced elsewhere and only displayed here; it expects that where a document was processed without bounding-box coordinates, page and text identifiers are still preserved upstream so an approximate highlight remains possible. It does not own, and must not absorb, any of those producers' behaviour.
- **Standing product boundaries**: this feature does not admit fully autonomous systematic review generation without human approval, automated clinical recommendations derived from extracted evidence, guaranteed extraction correctness without human validation, OCR-heavy scanned-document workflows beyond fallback support, or meta-analysis automation. No value presented in the workspace or written by an exporter may be presented as verified truth; acceptance records a human decision, it does not certify correctness.

## Requirements

### Requirement 1: Local Review Workspace and Runtime Isolation

**Objective:** As an evidence-synthesis reviewer, I want to open a review workspace on my own machine without changing how the pipeline runs, so that gaining a review interface never costs me the ability to run extraction headlessly on a server.

#### Acceptance Criteria

1. When a reviewer starts the review workspace, the Review Server shall serve the workspace on the local machine only and shall report the address at which it is available.
2. The Review Server shall bind to a loopback address by default and shall require an explicit opt-in before listening on any other network interface.
3. While the review workspace runtime components are not installed, the extraction pipeline, the quality-control stages, the merger, and every exporter shall continue to run to completion without a display server and without any review-specific component present.
4. If a reviewer starts the review workspace while its runtime components are absent, then the Review Server shall refuse to start and shall report which component must be installed and how.
5. When the review workspace is started against a project, the Review Server shall operate only on that project's documents, schema versions, extraction outputs, and review records.
6. If a required upstream artifact for a document is absent or unreadable, then the Review Server shall present the document with the missing artifact named and shall not present partial data as complete.
7. The Review Server shall serve every asset it needs from local files and shall not require network access to render a document or to record a review action.

### Requirement 2: Evidence Rendering and Highlight Fidelity

**Objective:** As a human reviewer, I want to see extracted evidence highlighted on the page of the source document, so that I can verify a value against the paper instead of searching for a quoted sentence by hand.

#### Acceptance Criteria

1. When an evidence item carries bounding-box coordinates, the Review Workspace shall render a highlight over the corresponding region of the corresponding page.
2. When an evidence item carries no bounding-box coordinates but carries a page identifier, a text span, or a quotable phrase, the Review Workspace shall render an approximate highlight derived from a page-level or text-search location.
3. When a highlight is approximate, the Review Workspace shall mark it as approximate in a way that is visually and textually distinguishable from a coordinate-derived highlight, and shall not present it as pixel-accurate anchoring.
4. When an evidence item carries an explicit anchor-absence marker, the Review Workspace shall report which anchor kinds are missing and shall offer no highlight rather than inventing one.
5. The Review Workspace shall render highlights from the anchors supplied by the provenance subsystem and shall not recompute, adjust, or infer an anchor from the document itself.
6. If the source document file for a highlight is unavailable, then the Review Workspace shall report the document as unavailable and shall continue to present the extracted values and evidence text it does have.
7. When several evidence items overlap on a page, the Review Workspace shall keep each item individually selectable rather than collapsing them into a single highlight.

### Requirement 3: Field–Evidence Navigation and Inspection

**Objective:** As a human reviewer, I want to move between a field and its evidence in one step and see everything relevant to a decision in one place, so that reviewing a field does not require reconstructing its context.

#### Acceptance Criteria

1. When a reviewer selects an extraction field, the Review Workspace shall navigate the document view to that field's linked evidence location.
2. When a field's evidence spans more than one location, the Review Workspace shall present every linked location and shall let the reviewer move between them.
3. When a reviewer selects a highlight, the Review Workspace shall display the field name, the extracted value, the evidence text, the confidence label, the verification status, and the review status for the field that cites it.
4. When a field has no linked evidence location, the Review Workspace shall report that the field has no navigable evidence and shall state the recorded reason.
5. The Review Workspace shall provide keyboard shortcuts for accepting, editing, and rejecting the field currently in focus, and for moving focus to the next and previous field.
6. The Review Workspace shall present the list of keyboard shortcuts in the workspace so that a reviewer can discover them without external documentation.
7. When a reviewer filters the field list by review status, confidence, verification status, or criticality, the Review Workspace shall present only matching fields and shall report how many fields the filter excluded.

### Requirement 4: Review Action Set

**Objective:** As a human reviewer, I want to accept, edit, reject, mark not reported, add evidence, or request re-extraction for a field, so that my judgement is captured by the system rather than lost in a side channel.

#### Acceptance Criteria

1. When a reviewer acts on a field, the Review Service shall accept exactly one of accept, edit, reject, mark not reported, add evidence, link evidence, request re-extraction, or comment, and shall reject any other action.
2. When a reviewer edits a field's value, the Review Service shall require the replacement value and shall record it as a human-supplied value distinct from the extracted value.
3. When a reviewer rejects a field, the Review Service shall record the rejection together with the reviewer-supplied reason and shall leave the extracted value readable.
4. When a reviewer marks a field as not reported, the Review Service shall record that decision as a review outcome and shall not present it as an extractor output.
5. When a reviewer requests re-extraction for a field, the Review Service shall record the request in a queue and shall not invoke any extraction, verification, or repair agent.
6. If a review action names a document, field, or evidence item that does not exist in the project, then the Review Service shall reject the action, shall leave the review record unchanged, and shall report what was not found.
7. If a review action is submitted twice with the same action identifier, then the Review Service shall record it once and shall report the action as already applied.
8. When a review action is recorded, the Review Service shall update the field's review status to a value drawn from a documented review-status vocabulary.

### Requirement 5: Original Output Preservation and Edit History

**Objective:** As an institutional reviewer, I want the model's original answer preserved beside every human correction, so that a reviewed dataset never becomes indistinguishable from an unreviewed one.

#### Acceptance Criteria

1. When a reviewer edits a field's value or evidence links, the Review Service shall retain the original extractor output unchanged and shall store the human-supplied value separately.
2. The Review Service shall never overwrite, delete, or rewrite a recorded extractor output, a recorded evidence item, or a previously recorded review action.
3. When a field has been edited more than once, the Review Service shall retain every intermediate value together with the reviewer, timestamp, and reason for each change.
4. When a reviewer requests a field's history, the Review Service shall report the original extractor value, every subsequent human value, and the action that produced each.
5. When a reviewer reverts a field, the Review Service shall record the reversion as a further action rather than removing the actions it reverses.
6. When a field's current value differs from its original extractor value, the Review Workspace shall show both and shall label which is the human-edited value.

### Requirement 6: Manual Evidence and Unresolved Evidence Linking

**Objective:** As a human reviewer, I want to attach evidence the system missed and place imported evidence the matcher could not locate, so that nothing I know about a paper is unrepresentable and nothing I imported is silently lost.

#### Acceptance Criteria

1. When a reviewer adds manual evidence for a field, the Review Service shall link that evidence to a page, a text span, a paragraph identifier, or a free-text note, and shall require at least one of those links.
2. When manual evidence is recorded, the Review Service shall mark its origin as human-created and shall keep it distinguishable from model-generated and imported evidence.
3. When a reviewer opens the unresolved evidence queue, the Review Workspace shall present each queued item with its source, target field, snippet, supplied hints, failure reason, and the best partial matches recorded for it.
4. When a reviewer links an unresolved evidence item to a document location, the Review Service shall record the resolution together with the reviewer identity, and shall record that the location was supplied by a human rather than computed.
5. When a reviewer discards an unresolved evidence item, the Review Service shall retain the item with a discarded state and the supplied reason rather than deleting it.
6. When manual or newly linked evidence is recorded, the Review Service shall produce it as a location-resolved evidence record and shall not produce an annotation artifact.
7. If a reviewer supplies a location that does not exist in the referenced document, then the Review Service shall reject the link and shall report the invalid location.

### Requirement 7: Reviewer Identity and Review Action Recording

**Objective:** As an institutional reviewer, I want every human action attributed and timestamped, so that a review record is defensible and can be audited after the fact.

#### Acceptance Criteria

1. When any review action is recorded, the Review Service shall store the reviewer identifier, the action timestamp, the action kind, the document and field affected, and any reviewer-supplied comment.
2. Where an anonymized reviewer identifier is configured, the Review Service shall record that identifier in place of the reviewer's own identifier and shall record that the identity was anonymized.
3. The Review Service shall produce the same anonymized identifier for the same reviewer across actions and across sessions, so that a reviewer's actions remain groupable without being attributable to a named person.
4. If no reviewer identity is configured or supplied, then the Review Service shall refuse to record the action and shall report that a reviewer identity is required.
5. The Review Service shall record action timestamps in a single documented time representation so that actions from different sessions are comparable.
6. The Review Service shall not record credentials, access tokens, or model provider keys in any review record or exported artifact.

### Requirement 8: Headless Review Decision Store and Replay

**Objective:** As an operator, I want the review record to be readable, replayable, and exportable with the interface switched off, so that review data is durable data rather than the internal state of an application.

#### Acceptance Criteria

1. The Review Service shall record review actions in an append-only form in which no recorded action is modified or removed by a later action.
2. When the recorded actions for a project are replayed in order, the Review Service shall produce the same current review state as the state the workspace displayed.
3. The Review Service shall expose the current review state, the action history, and the manual-review queue through command-line entry points that require no graphical environment and no review workspace runtime.
4. If the recorded action log contains an action the Review Service cannot interpret, then it shall report the offending action and shall continue projecting the remaining actions rather than failing the whole projection.
5. When two operations write review records for the same project concurrently, the Review Service shall prevent interleaved writes from corrupting the action log or the projected state.
6. When a review record is written, the Review Service shall record the extraction artifact and schema version the review was performed against, so that a review performed against superseded output is identifiable.
7. If review records exist for output produced under a different extraction artifact or schema version, then the Review Service shall report those records as referring to superseded output rather than silently applying them to current output.

### Requirement 9: Uncertainty, Origin, and Review-State Display

**Objective:** As a human reviewer, I want the workspace to show me how much to trust each value and where each piece of evidence came from, so that I spend my attention where the system is least certain.

#### Acceptance Criteria

1. The Review Workspace shall display, for every field, the confidence label, the verification status, the parser risk state, and the review status.
2. The Review Workspace shall visually distinguish model-generated evidence, externally imported evidence, and human-created evidence from one another.
3. When an evidence item's anchor precision is approximate or absent, the Review Workspace shall display that precision alongside the evidence.
4. When a field is designated critical by the extraction schema, the Review Workspace shall indicate that designation.
5. When a field was marked for manual review by the extraction layer, the Review Workspace shall indicate that state and shall report the recorded reason.
6. If an uncertainty signal is unavailable for a field, then the Review Workspace shall display it as unavailable and shall not substitute a default value.
7. The Review Workspace shall present a field's decision inputs — the quality-control issues raised, the verification verdict, and the adjudication outcome — as recorded, and shall not recompute or reinterpret them.

### Requirement 10: Document Processing Status Timeline

**Objective:** As an evidence-synthesis reviewer, I want a clear view of where each document stands, so that I know what is ready to review, what failed, and what I flagged.

#### Acceptance Criteria

1. When a reviewer opens a project, the Review Workspace shall present each document with its current processing status and its review progress.
2. When a reviewer opens a document, the Review Workspace shall present that document's status history in chronological order, showing each transition, its timestamp, and the stage that reported it.
3. The Review Workspace shall present the corpus-level counts of documents in each status together with the identifiers of failed and flagged documents.
4. When a document failed at a processing stage, the Review Workspace shall display the recorded failure reason.
5. The Review Workspace shall derive the status timeline from the recorded status history and shall neither define a status vocabulary of its own nor alter a recorded status.
6. When review actions have been recorded for a document, the Review Workspace shall show how many of the document's fields remain unreviewed.

### Requirement 11: Final Merger — Compact Output to Full Records

**Objective:** As a researcher, I want compact agent output expanded into full records that carry everything a reviewer or a downstream analysis needs, so that outputs are usable without reconstructing them from several artifacts.

#### Acceptance Criteria

1. When extraction and review data are available for a document, the Merger shall expand the compact agent output into one full record per field using the schema version that run was pinned to.
2. Each full record produced by the Merger shall carry the field index, the field name, the domain group, the extracted value, the evidence text, the evidence identifiers, the page numbers, the confidence label, the verification status, the review status, and the provenance of the value.
3. When a field has been edited by a reviewer, the Merger shall carry both the original extractor value and the current human-supplied value on the full record, with the reviewer and timestamp of the change.
4. When a field's evidence carries an approximate or absent anchor, the Merger shall carry the anchor precision on the full record rather than omitting the location.
5. If a compact answer references a field that is absent from the pinned schema version, then the Merger shall record the field as unmapped and shall continue expanding the remaining fields.
6. If a compact answer is missing for a field defined in the pinned schema version, then the Merger shall emit a record for that field marked as not extracted together with the recorded reason.
7. The Merger shall be callable without the review workspace runtime, without a display server, and without a browser.
8. The Merger shall produce identical full records for identical inputs on repeated invocations.

### Requirement 12: Export of Review Outputs

**Objective:** As a researcher, I want reviewed results exported in the formats my synthesis and analysis work already uses, so that the review does not end at the edge of the tool.

#### Acceptance Criteria

1. When a reviewer or an operator exports a document, the Export Service shall write a per-document JSON file containing that document's full records.
2. When a corpus export is requested, the Export Service shall write a single master JSON file spanning every document in the project.
3. When a review table export is requested, the Export Service shall write a delimited table in which each row carries a document and field together with the full-record attributes.
4. Where spreadsheet export is requested and the optional spreadsheet component is installed, the Export Service shall write a spreadsheet workbook equivalent in content to the delimited table.
5. If the optional spreadsheet component is absent, then the Export Service shall report which component must be installed, shall not write a partial workbook, and shall leave the other export formats available.
6. When a manual-review queue export is requested, the Export Service shall write every field awaiting human attention together with the reason it is awaiting attention.
7. If writing an export fails, then the Export Service shall leave any previously written export file intact and shall report the failing output by name and reason.
8. The Export Service shall be callable from a command-line entry point requiring no graphical environment and no review workspace runtime.

### Requirement 13: Optional Artifact Surfacing and Output Stamping

**Objective:** As an institutional reviewer, I want the quality, agreement, cost, and audit artifacts reachable from the review surface and every export stamped with what produced it, so that a result can be interpreted and reproduced later.

#### Acceptance Criteria

1. Where a quality-control report, an agreement report, a cost report, or an audit package exists for a run, the Review Workspace shall present it and shall identify the artifact it is presenting.
2. The Review Workspace and the Export Service shall present the content of those artifacts as produced and shall not compute, recompute, adjust, or reformat the figures they contain.
3. If an optional artifact is absent for a run, then the Review Workspace shall report it as unavailable together with the reason and shall not display an empty artifact as an artifact containing no findings.
4. When an export is written, the Export Service shall stamp it with the extraction schema version, the model identities, the parser identities, and the pipeline configuration recorded for the run that produced the data.
5. The Export Service shall take those stamped identities from the records produced by the run and shall not derive, recompute, or infer them.
6. If a stamped identity is unavailable for a run, then the Export Service shall record it as unavailable with a reason and shall still write the export.
7. Where an artifact export is requested, the Export Service shall reference or copy the existing artifact and shall not generate a substitute for it.

### Requirement 14: Failure Handling, Safety Boundaries, and Data Handling

**Objective:** As an operator, I want the review surface to fail safely and to make no claim it cannot support, so that a broken artifact costs a document rather than a session and a reviewed dataset is not mistaken for a validated one.

#### Acceptance Criteria

1. If rendering, projecting, or exporting fails for one document, then the Review Service shall record the failure against that document and shall continue serving and exporting the remaining documents.
2. If a review action cannot be persisted, then the Review Service shall report the failure to the reviewer, shall not report the action as recorded, and shall leave the prior review state intact.
3. The Review Workspace shall state that accepted values reflect a recorded human decision and shall not present any value as verified truth or as a clinical recommendation.
4. The Review Workspace shall not generate a systematic review, a synthesis narrative, or a clinical recommendation from the extracted evidence.
5. The Review Workspace shall render disclosure and redaction outcomes as supplied by the components that decide them and shall make no disclosure decision of its own.
6. The Review Service shall report every rejected action, failed export, unavailable artifact, and superseded review record through its normal logging channel in addition to the operation result.
7. The Review Workspace and the Review Service shall use no real patient data, protected health information, or credentials in any demonstration asset, fixture, or bundled sample.
