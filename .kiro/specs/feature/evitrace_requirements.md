# Requirements Document

## Introduction

EviTrace is an evidence-routed, human-in-the-loop framework for auditable biomedical literature extraction and evidence synthesis. The system allows users to define extraction items, upload biomedical PDFs, parse and segment those PDFs, route each extraction item to likely evidence locations, extract structured values with LLM agents, verify and repair uncertain outputs, and review all findings inside a PDF-centered annotation interface.

The framework is designed for academic evidence synthesis workflows, including systematic reviews, scoping reviews, methodological reviews, clinical AI reviews, and biomedical informatics studies. Its central contribution is traceability: every accepted extracted value should be linked to source evidence in the original PDF, with an auditable record of parser provenance, evidence routing, model outputs, counterfactual checks, human edits, and final adjudication.

This document defines the functional, methodological, and technical requirements for implementing EviTrace as a research-grade software system.

## Requirements

### Requirement 1: Project and Corpus Management

**User Story:** As a reviewer, I want to create a project and upload biomedical PDFs so that I can organize evidence extraction around a specific research question or review objective.

#### Acceptance Criteria

1. WHEN a user creates a project THEN the system SHALL store a project name, description, research question, owner, creation timestamp, and configuration profile.
2. WHEN a user uploads PDFs THEN the system SHALL compute a stable document identifier using a file hash.
3. WHEN a PDF is uploaded THEN the system SHALL store filename, file size, page count, upload timestamp, and processing status.
4. IF the uploaded file is not a valid PDF THEN the system SHALL reject the file and report the validation error.
5. WHEN multiple PDFs are uploaded THEN the system SHALL support batch processing while preserving per-document audit trails.
6. WHEN a project is opened THEN the system SHALL display corpus-level status including uploaded, parsed, extracted, reviewed, failed, and manually flagged documents.

---

### Requirement 2: Extraction Schema Builder

**User Story:** As a reviewer, I want to define the items to be extracted so that the system can extract values relevant to my review.

#### Acceptance Criteria

1. WHEN a user creates an extraction field THEN the system SHALL store field ID, field name, description, expected data type, criticality, allowed values, evidence requirement, and review instructions.
2. WHEN a field is marked critical THEN the system SHALL apply stricter routing, extraction, verification, and review policies.
3. WHEN a user provides a research question THEN the system SHOULD support LLM-assisted generation of candidate extraction fields.
4. WHEN the system proposes fields using an LLM THEN the user SHALL be required to approve or edit the schema before extraction begins.
5. WHEN a schema is imported from CSV, Excel, JSON, or YAML THEN the system SHALL map imported columns to the internal extraction-field schema.
6. IF imported fields are ambiguous THEN the system SHALL present them for human mapping before processing.
7. WHEN schema changes occur after extraction THEN the system SHALL version the schema and preserve the extraction version used for prior outputs.

---

### Requirement 3: Parser Ensemble

**User Story:** As a researcher, I want the system to parse PDFs using multiple parsers so that downstream extraction is less vulnerable to one parser's failure mode.

#### Acceptance Criteria

1. WHEN a PDF is processed THEN the system SHALL attempt structured parsing using GROBID or an equivalent scholarly-document parser when available.
2. WHEN local parsing is performed THEN the system SHALL also support PyMuPDF and pdfplumber extraction paths.
3. WHEN a parser produces text THEN the system SHALL store parser-specific raw outputs before cleaning.
4. WHEN pdfplumber detects tables THEN the system SHALL preserve table candidates as structured table objects when possible.
5. WHEN PyMuPDF produces block-level or word-level output THEN the system SHALL preserve block/page coordinates when available.
6. IF a PDF appears scanned or text coverage is poor THEN the system SHALL flag the document for OCR or manual review.
7. WHEN multiple parser outputs exist THEN the system SHALL compare parser outputs page-by-page and section-by-section.
8. WHEN parser disagreement is detected THEN the system SHALL assign parser risk flags to affected pages, paragraphs, tables, or fields.

---

### Requirement 4: Canonical Document Representation

**User Story:** As a system developer, I want parser outputs normalized into a canonical representation so that all downstream agents and UI components use stable evidence IDs.

#### Acceptance Criteria

1. WHEN parsing completes THEN the system SHALL generate a canonical document object containing sections, paragraphs, tables, figures, captions, pages, and parser provenance.
2. WHEN text is segmented THEN each paragraph SHALL receive a stable paragraph ID.
3. WHEN tables are identified THEN each table SHALL receive a stable table ID.
4. WHEN figure captions are identified THEN each caption SHALL receive a stable figure or caption ID.
5. WHEN section headings are detected THEN each section SHALL receive a section ID, title, section type, and page range.
6. WHEN canonical text is generated THEN each text unit SHALL retain source parser metadata.
7. WHEN coordinates are available THEN the canonical object SHALL preserve page-level bounding boxes for later PDF annotation.
8. IF no coordinates are available for an evidence unit THEN the system SHALL still preserve page and text identifiers and mark annotation precision as approximate.

---

### Requirement 5: Parser Quality Control and Interobserver Statistics

**User Story:** As a reviewer, I want to know whether PDF-to-text extraction was reliable so that I can decide when extra verification is needed.

#### Acceptance Criteria

1. WHEN parser outputs are generated THEN the system SHALL compute deterministic parser QC metrics.
2. Parser QC metrics SHALL include character count, word count, empty-page count, replacement-character count, `(cid:)` artifact count, heading detection, table detection, and repeated header/footer detection.
3. WHEN multiple parser outputs exist THEN the system SHALL compute parser agreement metrics.
4. Parser agreement metrics SHOULD include token overlap, numeric-token overlap, table-detection agreement, section-heading agreement, and text-presence agreement.
5. WHEN a page has low parser agreement THEN the system SHALL mark that page as parser-risky.
6. WHEN a critical field is routed to a parser-risky page THEN the system SHALL require stricter extraction or verification.
7. WHEN parser agreement is high THEN the system MAY skip parser counterfactual review for that page.
8. WHEN parser agreement is low or table/numeric content is at risk THEN the system SHOULD trigger a parser-counterfactual audit or human review.
9. WHEN parser QC is complete THEN the system SHALL save a parser QC report as part of the audit package.

---

### Requirement 6: Cleaning and Reference Stripping

**User Story:** As a reviewer, I want irrelevant PDF boilerplate removed so that agents focus on article content and spend fewer tokens.

#### Acceptance Criteria

1. WHEN canonical text is cleaned THEN the system SHALL remove repeated headers, footers, page numbers, copyright notices, download notices, and publisher boilerplate when reliably identifiable.
2. WHEN a references or bibliography section is identified THEN the system SHALL remove reference-list entries from model-facing content by default.
3. WHEN in-text citation markers occur inside meaningful article text THEN the system SHOULD preserve them unless they impair readability.
4. WHEN cleaning removes content THEN the system SHALL preserve a record of removed blocks and removal reasons.
5. IF a removed section may contain relevant evidence, such as data availability or funding, THEN the system SHALL preserve it or flag it for user review.
6. WHEN cleaned text is generated THEN the original raw parser output SHALL remain available in the audit package.

---

### Requirement 7: Local Retrieval and Field Heuristics

**User Story:** As the system, I want to generate deterministic candidate evidence locations before LLM routing so that routing is more reliable and less expensive.

#### Acceptance Criteria

1. WHEN a document is indexed THEN the system SHALL build a section index, paragraph index, table index, and caption index.
2. WHEN an extraction schema is available THEN the system SHALL generate field-specific retrieval hints using field names, descriptions, synonyms, and expected evidence locations.
3. WHEN local retrieval runs THEN the system SHOULD retrieve candidate paragraphs, sections, tables, and captions for each field or field group.
4. WHEN local retrieval results are generated THEN they SHALL be passed to Agent 0 as hints, not as final answers.
5. IF local retrieval finds candidates ignored by Agent 0 THEN those candidates SHALL be eligible for Agent 0c counterfactual routing or local route adjudication.

---

### Requirement 8: Agent 0 Evidence Locator

**User Story:** As a reviewer, I want the system to identify where each field is likely answered before extraction so that extracted answers are grounded in document-specific evidence.

#### Acceptance Criteria

1. WHEN Agent 0 runs THEN it SHALL receive the cleaned canonical document, document outline, table/caption index, field schema, and local retrieval hints.
2. Agent 0 SHALL not extract final field values.
3. Agent 0 SHALL return route objects linking field indices or field groups to primary evidence IDs, backup evidence IDs, pages, section names, confidence, risk flags, and a routing rationale.
4. WHEN a field is likely absent THEN Agent 0 SHALL still identify the most relevant locations to verify absence.
5. WHEN Agent 0 returns IDs THEN every ID SHALL refer to canonical document objects.
6. IF Agent 0 output fails schema validation THEN the system SHALL retry or route the document to repair/manual review according to configuration.
7. WHEN Agent 0 completes THEN the raw locator output SHALL be stored in the audit package.

---

### Requirement 9: Local Route QC

**User Story:** As a reviewer, I want routes checked before extraction so that the extractor does not rely on missing, implausible, or irrelevant evidence locations.

#### Acceptance Criteria

1. WHEN Agent 0 outputs routes THEN local route QC SHALL verify that every extraction field is covered.
2. Route QC SHALL detect missing field indices, duplicate field coverage, invalid field IDs, invalid evidence IDs, empty primary evidence, and missing confidence labels.
3. Route QC SHALL detect routes that point only to references, footers, boilerplate, or removed content.
4. Route QC SHALL verify that critical fields have backup evidence locations or counterfactual review.
5. Route QC SHOULD evaluate route plausibility using field-type heuristics.
6. WHEN route QC fails for a field THEN the system SHALL send that field to Agent 0c, local retrieval expansion, or manual review.
7. WHEN route QC completes THEN the system SHALL save pass/fail status and issues for each route.

---

### Requirement 10: Agent 0c Counterfactual Locator

**User Story:** As a reviewer, I want a counterfactual locator to challenge weak or critical evidence routes so that the system is less likely to miss relevant evidence.

#### Acceptance Criteria

1. WHEN a route is critical, low-confidence, parser-risky, or fails route QC THEN the system SHALL consider Agent 0c routing.
2. Agent 0c SHALL receive the original route, field definitions, document outline, local retrieval candidates, and selected snippets.
3. Agent 0c SHALL not extract final field values.
4. Agent 0c SHALL identify alternative or missing evidence locations, or confirm that the original route appears sufficient.
5. WHEN Agent 0c identifies alternatives THEN those alternatives SHALL be passed to the local route adjudicator.
6. WHEN Agent 0c completes THEN its output SHALL be saved in the audit package.

---

### Requirement 11: Local Route Adjudication

**User Story:** As a system developer, I want final extraction packs built deterministically so that extractors receive compact, relevant, and traceable context.

#### Acceptance Criteria

1. WHEN Agent 0 and Agent 0c outputs are available THEN the local route adjudicator SHALL merge primary, backup, counterfactual, and local retrieval candidates.
2. The adjudicator SHALL preserve route provenance for each evidence ID.
3. The adjudicator SHALL cap extraction-pack token size according to configuration.
4. The adjudicator SHALL prioritize primary route evidence, counterfactual alternatives for critical fields, and table/caption evidence when relevant.
5. IF the evidence pack exceeds token limits THEN the system SHALL trim using deterministic priority rules and record discarded IDs.
6. WHEN extraction packs are produced THEN each pack SHALL contain field definitions, evidence snippets, route trace, parser risk flags, and document metadata.

---

### Requirement 12: Agent 1A Targeted Extraction

**User Story:** As a reviewer, I want the system to extract values from targeted evidence packs so that each output is supported by specific document locations.

#### Acceptance Criteria

1. WHEN Agent 1A runs THEN it SHALL receive extraction packs rather than the full PDF whenever possible.
2. Agent 1A SHALL return compact structured output containing field index, extracted value, evidence IDs, short quote or evidence phrase, and confidence.
3. Agent 1A SHALL not output field names, domain groups, PDF names, or other metadata that can be reattached locally.
4. WHEN evidence is absent THEN Agent 1A SHALL return a configured not-reported value and evidence IDs used to verify absence when possible.
5. WHEN Agent 1A returns an answer THEN it SHALL cite at least one evidence ID for every non-not-reported value.
6. IF Agent 1A output fails schema validation THEN the system SHALL retry, repair, or flag the pack according to configuration.
7. WHEN Agent 1A completes THEN raw output SHALL be saved in the audit package.

---

### Requirement 13: Agent 1B Independent Extractor

**User Story:** As a researcher, I want an independent second extraction for critical or uncertain fields so that inter-rater agreement can be estimated and disagreements escalated.

#### Acceptance Criteria

1. WHEN the system is in calibration mode THEN Agent 1B SHALL run on all configured fields unless disabled.
2. WHEN the system is in production mode THEN Agent 1B SHALL run on critical fields, low-confidence routes, parser-risky fields, historically low-agreement fields, or random QA samples according to configuration.
3. Agent 1B SHALL be blind to Agent 1A's answer.
4. Agent 1B SHOULD use different prompt framing, snippet order, or model configuration when feasible to reduce correlated errors.
5. Agent 1B SHALL return the same compact structured output format as Agent 1A.
6. WHEN Agent 1B completes THEN raw output SHALL be saved separately from Agent 1A output.

---

### Requirement 14: Extraction QC

**User Story:** As a reviewer, I want extracted values validated locally so that unsupported or malformed outputs are caught before finalization.

#### Acceptance Criteria

1. WHEN extraction outputs are received THEN local extraction QC SHALL validate schema compliance.
2. Extraction QC SHALL verify requested field coverage, duplicate field IDs, valid evidence IDs, non-empty values, and allowed confidence labels.
3. Extraction QC SHALL fuzzy-match short quotes against cited evidence text when quotes are provided.
4. Extraction QC SHALL flag high-confidence answers with missing or unsupported evidence.
5. Extraction QC SHALL flag numeric fields without numeric content when a number is expected.
6. Extraction QC SHALL flag critical fields that are low-confidence, not reported, unsupported, or parser-risky.
7. WHEN extraction QC identifies issues THEN those fields SHALL be eligible for Agent 1c verification, Agent 3 repair, or manual review.

---

### Requirement 15: Inter-Rater Agreement Statistics

**User Story:** As a researcher, I want agreement statistics between extraction agents and humans so that reliability can be measured and escalation policies can be evidence-driven.

#### Acceptance Criteria

1. WHEN Agent 1A and Agent 1B both extract a field THEN the system SHALL normalize their outputs for agreement comparison.
2. Normalized comparison SHALL include value agreement, evidence agreement, confidence agreement, support status, and not-reported agreement.
3. The system SHALL compute percent agreement for compared fields.
4. The system SHOULD compute Cohen's kappa for categorical labels where appropriate.
5. The system SHOULD compute weighted kappa for ordered confidence labels where appropriate.
6. The system SHOULD compute Gwet-style or prevalence-robust agreement metrics for highly imbalanced binary labels where implemented.
7. The system SHALL report disagreement rates by field, field group, document, parser-risk status, and criticality.
8. Agreement SHALL not override deterministic evidence validity checks.
9. WHEN agreement is low for a field group THEN the system SHALL increase verification, dual extraction, or manual review according to configuration.

---

### Requirement 16: Agent 1c Counterfactual Answer Verifier

**User Story:** As a reviewer, I want a verifier to challenge extracted answers so that confident but unsupported outputs are detected.

#### Acceptance Criteria

1. WHEN fields are critical, disputed, low-confidence, not reported, parser-risky, or fail QC THEN the system SHALL consider Agent 1c verification.
2. Agent 1c SHALL receive the field definition, candidate answer(s), cited evidence, alternative evidence, and QC issues.
3. Agent 1c SHALL determine whether an answer is supported, unsupported, contradicted, incomplete, alternative_found, not_reported_supported, or needs_manual_review.
4. Agent 1c SHALL not freely rewrite all fields.
5. WHEN Agent 1c identifies a better value THEN it SHALL provide supporting evidence IDs and rationale.
6. WHEN Agent 1c marks a field unsupported, contradicted, incomplete, or alternative_found THEN the field SHALL be sent to local answer adjudication and possibly Agent 3 repair.
7. WHEN Agent 1c completes THEN its verification output SHALL be saved in the audit package.

---

### Requirement 17: Local Answer Adjudication

**User Story:** As a reviewer, I want final decisions to be made using evidence, agreement, verification, and criticality so that accepted outputs are defensible.

#### Acceptance Criteria

1. WHEN Agent 1A, Agent 1B, extraction QC, agreement statistics, and Agent 1c verification are available THEN local adjudication SHALL decide whether each field is accepted, repaired, or sent to manual review.
2. IF an answer is supported, evidence-valid, and noncritical THEN the system MAY accept it without repair.
3. IF an answer is critical THEN the system SHALL apply stricter acceptance criteria.
4. IF Agent 1A and Agent 1B disagree THEN the system SHALL require verification, repair, or manual review.
5. IF evidence IDs are invalid or unsupported THEN the system SHALL not accept the answer solely on agent agreement.
6. IF a field remains unclear after verification and repair THEN the system SHALL mark it for manual review.
7. WHEN a decision is made THEN the system SHALL store the decision rule, inputs, and provenance.

---

### Requirement 18: Agent 3 Repair

**User Story:** As a reviewer, I want failed or uncertain fields repaired using targeted evidence so that only problematic fields consume additional model calls.

#### Acceptance Criteria

1. WHEN a field requires repair THEN Agent 3 SHALL receive only the relevant field definition, current answer, QC issues, verifier critique, and evidence snippets.
2. Agent 3 SHALL return one of the configured actions: revised, kept_original, marked_not_reported, or manual_review.
3. WHEN Agent 3 revises a field THEN it SHALL provide a value, evidence IDs, short quote, confidence, and rationale.
4. WHEN Agent 3 cannot resolve a field THEN it SHALL mark the field for manual review.
5. WHEN repair completes THEN the system SHALL run extraction QC again on repaired fields.
6. Agent 3 raw output and final repair decisions SHALL be saved in the audit package.

---

### Requirement 19: PDF Reader and Annotation UI

**User Story:** As a human reviewer, I want to see extracted evidence highlighted in the PDF so that I can verify, edit, or reject findings efficiently.

#### Acceptance Criteria

1. WHEN extracted evidence has coordinates THEN the PDF reader SHALL highlight the evidence span on the corresponding page.
2. WHEN coordinates are unavailable THEN the PDF reader SHALL show page-level or text-search-based approximate highlights.
3. WHEN a user selects an extraction field THEN the PDF reader SHALL navigate to the linked evidence location.
4. WHEN a highlight is selected THEN the UI SHALL display field name, extracted value, evidence text, confidence, verification status, and review status.
5. The UI SHALL allow users to accept, edit, reject, mark not reported, add evidence, or request re-extraction.
6. WHEN a user edits a value or evidence span THEN the system SHALL preserve both the original model output and human-edited value.
7. WHEN a user adds manual evidence THEN the system SHALL link the evidence to a page, text span, paragraph ID, or manual note.
8. WHEN review actions occur THEN the system SHALL store reviewer ID, timestamp, action, and optional comment.

---

### Requirement 20: External Evidence Import

**User Story:** As a reviewer, I want to import evidence from spreadsheets or external tools so that the system can annotate and validate evidence generated elsewhere.

#### Acceptance Criteria

1. WHEN a user imports CSV, Excel, JSON, or another supported evidence format THEN the system SHALL map imported fields to the project extraction schema.
2. WHEN imported evidence includes text snippets THEN the system SHALL attempt fuzzy matching to canonical document text.
3. WHEN imported evidence includes page numbers or citations THEN the system SHALL use them as matching hints.
4. WHEN imported evidence maps to a PDF location THEN the system SHALL generate annotation candidates.
5. IF imported evidence cannot be mapped THEN the system SHALL place it in an unresolved evidence queue.
6. The UI SHALL allow users to manually link unresolved evidence to PDF locations.
7. Imported evidence SHALL preserve origin metadata distinct from in-app LLM-extracted evidence.

---

### Requirement 21: Final Merger and Output Generation

**User Story:** As a researcher, I want final outputs exported in structured formats so that I can use them for evidence synthesis, review tables, and downstream analysis.

#### Acceptance Criteria

1. WHEN extraction and review are complete THEN the local merger SHALL expand compact agent outputs into full records using the extraction schema.
2. Final records SHALL include field index, field name, domain group, extracted value, evidence text, evidence IDs, page numbers, confidence, verification status, review status, and provenance.
3. The system SHALL support per-PDF JSON output.
4. The system SHOULD support master JSON output across all PDFs.
5. The system SHOULD support CSV or Excel export for review tables.
6. The system SHALL support export of manual-review queues.
7. The system SHALL support optional QC reports, agreement reports, cost reports, and audit packages.
8. WHEN outputs are generated THEN the system SHALL record schema version, model versions, parser versions, and pipeline configuration.

---

### Requirement 22: Audit Package

**User Story:** As a researcher, I want every processing stage saved so that results are reproducible and defensible.

#### Acceptance Criteria

1. WHEN a document is processed THEN the system SHALL create an audit package for that document.
2. The audit package SHALL include input manifest, parser outputs, canonical document, cleaned document, parser QC, route map, route QC, counterfactual route output, final route map, extraction outputs, agreement report, verification output, repair output, final extraction, cost report, and logs.
3. WHEN a human edits an extraction THEN the audit package SHALL preserve original model output and edited output.
4. WHEN an output is accepted THEN the audit package SHALL identify the route and evidence supporting that output.
5. WHEN a field is sent to manual review THEN the audit package SHALL preserve the reason.
6. The system SHALL allow users to export audit packages for reproducibility, manuscript supplement, or reviewer inspection.

---

### Requirement 23: Cost, Token, and Cache Logging

**User Story:** As a user, I want transparent cost and token reporting so that I can estimate API usage and optimize the workflow.

#### Acceptance Criteria

1. WHEN an API call is made THEN the system SHALL log model name, stage, document ID, input tokens, output tokens, cached tokens if available, latency, and cost estimate.
2. WHEN prompt caching is supported THEN the system SHALL use stable prompt structures and cache keys where configured.
3. WHEN an API call fails or retries THEN the system SHALL log the error, retry count, and additional token/cost impact.
4. The system SHALL provide per-document and project-level token summaries.
5. The system SHALL provide stage-level cost summaries for parsing, routing, extraction, verification, repair, and total cost.
6. The system SHALL support disabling nonessential model calls to reduce cost.

---

### Requirement 24: Human Benchmark and Validation Mode

**User Story:** As a researcher, I want to evaluate the framework against human benchmarks so that I can validate the system scientifically.

#### Acceptance Criteria

1. WHEN validation mode is enabled THEN the system SHALL support importing human reference-standard extraction tables.
2. WHEN human benchmark data are available THEN the system SHALL compare system outputs against the reference standard at field level.
3. The system SHALL support exact, normalized, categorical, numeric-tolerance, semantic, and not-reported comparison modes.
4. The system SHALL compute field-level accuracy, completeness, unsupported-answer rate, evidence support accuracy, and critical-field accuracy.
5. The system SHOULD compute human-vs-system agreement statistics where applicable.
6. The system SHALL support prospective human review timing, correction burden, and usability data collection.
7. WHEN benchmark results are generated THEN the system SHALL export tables suitable for manuscript reporting.

---

### Requirement 25: Baseline and Ablation Evaluation

**User Story:** As a researcher, I want to compare the full framework against simpler baselines so that the contribution of each component can be measured.

#### Acceptance Criteria

1. The system SHALL support a one-shot full-PDF extraction baseline.
2. The system SHALL support a chunked full-PDF extraction baseline.
3. The system SHALL support disabling Agent 0c to evaluate the counterfactual locator contribution.
4. The system SHALL support disabling Agent 1B to evaluate the second-extractor contribution.
5. The system SHALL support disabling Agent 1c to evaluate answer verification contribution.
6. The system SHALL support disabling Agent 3 to evaluate repair contribution.
7. The system SHALL support reporting accuracy, evidence support, cost, runtime, and manual-review rate for each ablation.

---

### Requirement 26: Security, Privacy, and Data Governance

**User Story:** As a researcher working with biomedical literature and possibly unpublished documents, I want safe handling of files, API calls, and project data.

#### Acceptance Criteria

1. The system SHALL not expose API keys in logs, exports, or UI.
2. The system SHALL support environment-variable or secure-secret storage for API keys.
3. The system SHALL allow users to choose local-only parsing before any API call.
4. The system SHALL make clear which document text is sent to external APIs.
5. The system SHALL support redaction or exclusion rules for sensitive documents when configured.
6. The system SHALL preserve project-level access controls if deployed in a multi-user environment.
7. The system SHALL store human review actions with reviewer identity or configured anonymized reviewer ID.

---

### Requirement 27: Reproducibility and Configuration

**User Story:** As a researcher, I want runs to be reproducible so that results can be audited and compared across experiments.

#### Acceptance Criteria

1. WHEN a run starts THEN the system SHALL save configuration, schema version, parser versions, model names, prompt versions, and run timestamp.
2. WHEN prompts are used THEN the system SHALL version prompt templates.
3. WHEN models are changed THEN the system SHALL preserve the model version used for each output.
4. WHEN a run is resumed THEN the system SHALL not overwrite completed outputs unless explicitly configured.
5. WHEN pipeline stages are skipped or rerun THEN the system SHALL record that action in the audit log.
6. The system SHALL support deterministic local processing where possible.

---

## Non-Functional Requirements

### Performance

1. The system SHOULD process PDFs in parallel subject to API rate limits and local compute limits.
2. The system SHOULD process extraction packs in parallel when token and request limits allow.
3. The system SHALL allow users to configure document-level and API-call-level concurrency.
4. The system SHOULD avoid sending full PDFs to downstream extraction and verification agents unless necessary.

### Reliability

1. The system SHALL retry transient API errors with exponential backoff.
2. The system SHALL preserve partial progress in a manifest.
3. The system SHALL allow failed documents or failed fields to be resumed without rerunning the entire project.
4. The system SHALL fail safely when parser output is poor, API responses are malformed, or evidence IDs are invalid.

### Usability

1. The UI SHALL present a clear document-processing status timeline.
2. The UI SHALL distinguish model-generated, imported, and human-edited evidence.
3. The UI SHALL make uncertainty visible using confidence, verification status, parser risk, and review state.
4. The UI SHOULD support keyboard shortcuts for accept/edit/reject workflows.

### Maintainability

1. The codebase SHALL separate parser, routing, extraction, verification, repair, UI, and export modules.
2. The codebase SHALL include tests for schema validation, route QC, extraction QC, agreement computation, and final merging.
3. The system SHALL expose configuration for models, prompts, thresholds, critical fields, and output options.

## Out of Scope for Initial Version

1. Fully autonomous systematic review generation without human approval.
2. Automated clinical recommendations from extracted evidence.
3. Guaranteed extraction correctness without human validation.
4. OCR-heavy scanned-document workflows beyond fallback support.
5. Full meta-analysis automation, unless separately implemented and validated.

## Success Metrics

1. Field-level extraction accuracy against human reference standard.
2. Evidence-support accuracy for accepted fields.
3. Unsupported-answer rate.
4. Not-reported accuracy.
5. Critical-field accuracy.
6. Human correction burden.
7. Time saved versus manual extraction.
8. Inter-agent and human-system agreement.
9. Cost per PDF and cost per accepted field.
10. Percentage of fields with complete audit trails.
11. Manual-review rate by field group.
12. Parser-risk impact on final extraction accuracy.

## Proposed Research Evaluation

The framework should be evaluated in two stages.

### Stage 1: Methodology and System Validation

This stage evaluates whether the architecture works as designed. It should include parser ensemble testing, route quality assessment, extraction accuracy, evidence support, ablation studies, and audit completeness on a development corpus.

### Stage 2: Benchmark and Human-in-the-Loop Evaluation

This stage evaluates the framework against retrospective human-extracted benchmark data and a prospective human annotator study. Outcomes should include accuracy, time savings, correction burden, usability, and final human-verified extraction quality.

## Open Questions

1. Which parser should be the default canonical source for biomedical PDFs with complex tables?
2. How much dual extraction is needed after calibration?
3. Which fields require mandatory Agent 1c verification?
4. What threshold should trigger parser-counterfactual audit?
5. Should Agent 0 route fields individually or by field group?
6. What is the best balance between evidence quote length and paragraph-ID-only evidence?
7. How should semantic equivalence be measured for free-text extracted values?
8. What level of human review is required for publishable validation?
9. Which benchmark datasets are appropriate for the first evaluation?
10. Which venue should be targeted first: JBI methodology paper or benchmark paper?
