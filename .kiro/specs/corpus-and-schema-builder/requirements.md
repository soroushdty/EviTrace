# Requirements Document

## Introduction

EviTrace currently processes whatever PDFs happen to sit in a single configured directory, against a 62-field extraction map that is a checked-in repository file. There is no project concept, no admission step, no per-document identity that a reviewer can refer to, no way to change the field list without editing the repository, and no way to bring in evidence a reviewer already extracted elsewhere. Running a second review means a second checkout, and invalid inputs surface mid-pipeline instead of at the point of admission.

This feature introduces the reviewer-facing organising layer that the extraction pipeline currently lacks: named projects that own their own corpus, configuration, and field schema; PDF admission with validation and content-derived document identifiers; a document status vocabulary with a corpus-level rollup; a user-definable and versioned extraction-field schema whose versions are pinned to the outputs produced under them; schema import with human-mediated column mapping; and import of externally produced evidence, matched back to canonical document text with an unresolved queue for what cannot be placed.

Everything here is headless. Operations are reachable from command-line entry points and callable as libraries. The existing 62-field extraction map becomes the default seed schema version rather than being removed, so a reviewer who never authors a schema gets today's behaviour.

## Boundary Context

- **In scope**: project records and per-project configuration profiles; PDF admission, validation, and content-derived document identity; per-document metadata, status vocabulary, status history, and corpus rollup; the extraction-field definition model including criticality, data type, allowed values, evidence requirement, and review instructions; schema versioning and version pinning of prior outputs; schema import and column mapping from CSV, Excel, JSON, and YAML; external evidence import, matching to canonical document text using page and citation hints, and the unresolved evidence queue as readable data.
- **Out of scope**: LLM-assisted generation of candidate extraction fields and the mandatory human approval flow around it (deferred until an approved LLM call path exists); any interactive or graphical surface, including the manual linking surface for unresolved evidence; multi-user access control and reviewer identity; parser, OCR, and canonical-document production, which already exist; behavioural enforcement of the criticality flag during routing, extraction, verification, and repair; production of W3C annotation artifacts; and defining the identity scheme for evidence nodes.
- **Adjacent expectations**: canonical document text and page-level locations are produced by the existing extraction stages and are consumed here, never re-derived. Downstream extraction, reporting, and review capabilities are expected to read field criticality, evidence requirements, and the pinned schema version from this feature rather than from a repository file. A sensitivity label may be attached to an admitted document by a separate capability; this feature stores it and leaves it unset otherwise. Annotation candidates produced here are inputs to the existing single producer of annotation artifacts, not a second producer.

## Requirements

### Requirement 1: Project Record and Configuration Profile

**Objective:** As an evidence-synthesis reviewer, I want to create and open named projects that carry their own research question and configuration, so that I can run more than one review from a single installation without a second checkout.

#### Acceptance Criteria

1. When a reviewer creates a project, the Corpus Service shall store a project identifier, name, description, research question, owner, creation timestamp, and configuration profile, and shall report the created project identifier.
2. If a reviewer creates a project using a name already in use, then the Corpus Service shall reject the request and report the conflicting existing project identifier.
3. The Corpus Service shall keep each project's record, corpus, schema versions, imported evidence, and outputs isolated, such that operating on one project neither reads nor modifies another project's data.
4. When a project's configuration profile omits a setting, the Corpus Service shall use the installation-level value for that setting.
5. When a reviewer opens a project, the Corpus Service shall report the project record together with the effective configuration profile in use.
6. If a reviewer references a project identifier that does not exist, then the Corpus Service shall reject the request and report that the project was not found.
7. Where no project is specified by the caller, the Corpus Service shall operate against a default project so that existing single-corpus usage continues to work unchanged.

### Requirement 2: Document Admission and Validation

**Objective:** As an evidence-synthesis reviewer, I want PDFs admitted into a project with stable identifiers and validated up front, so that I can refer to documents unambiguously and learn about bad inputs before a run starts.

#### Acceptance Criteria

1. When a reviewer admits a PDF into a project, the Corpus Service shall compute a stable document identifier derived solely from the file's content, and shall report it.
2. When a reviewer admits a file whose content is identical to a document already admitted to the same project, the Corpus Service shall keep the single existing document record, shall not create a duplicate, and shall report the admission as a duplicate naming the existing document identifier.
3. When a PDF is admitted successfully, the Corpus Service shall record the original filename, file size in bytes, page count, admission timestamp, and processing status.
4. If an admitted file cannot be read as a PDF, then the Corpus Service shall reject the file, leave the project's corpus unchanged, and report a rejection reason naming the file and the validation failure.
5. If an admitted PDF is readable but contains zero pages, or is encrypted such that its pages cannot be read, then the Corpus Service shall reject the file and report the specific reason.
6. The Corpus Service shall complete admission validation before any extraction stage processes a document, so that invalid inputs are reported at admission rather than mid-pipeline.
7. When a document is admitted, the Corpus Service shall record a sensitivity label attribute for that document and shall leave it unset when no value is supplied.

### Requirement 3: Batch Admission with Per-Document Audit Trails

**Objective:** As an evidence-synthesis reviewer, I want to admit many PDFs at once without losing per-document accountability, so that a partially bad batch neither blocks the good files nor hides what happened to each one.

#### Acceptance Criteria

1. When a reviewer admits multiple files in one batch, the Corpus Service shall evaluate each file independently and record a separate admission outcome for each.
2. If one or more files in a batch fail validation, then the Corpus Service shall still admit the remaining valid files and report per-file outcomes including each rejection reason.
3. When a batch admission completes, the Corpus Service shall report the number of files admitted, the number recognised as duplicates, and the number rejected.
4. The Corpus Service shall record every admission event with its timestamp and outcome, such that the full admission history of any single document can be read back.
5. When the same batch is admitted a second time, the Corpus Service shall produce the same set of document identifiers and shall not create additional document records.

### Requirement 4: Document Status Vocabulary and Corpus Rollup

**Objective:** As an evidence-synthesis reviewer, I want a single corpus-level view of where every document stands, so that I can see at a glance what is done, what failed, and what I flagged.

#### Acceptance Criteria

1. The Corpus Service shall assign every admitted document exactly one status drawn from a documented vocabulary that distinguishes uploaded, parsed, extracted, reviewed, failed, and manually flagged.
2. When a processing stage reports an outcome for a document, the Corpus Service shall update that document's status and record the transition with a timestamp and the reporting stage.
3. If a requested status transition is not permitted by the documented vocabulary, then the Corpus Service shall reject the transition and leave the current status unchanged.
4. When a reviewer manually flags a document, the Corpus Service shall record the flag together with the reviewer-supplied reason.
5. When a reviewer requests corpus status for a project, the Corpus Service shall report the number of documents in each status together with the identifiers of the failed and flagged documents.
6. The Corpus Service shall retain each document's full status history rather than only its latest status.

### Requirement 5: Extraction Field Definition

**Objective:** As an evidence-synthesis reviewer, I want to define the items to be extracted for my review, so that the pipeline extracts values relevant to my question rather than a fixed built-in list.

#### Acceptance Criteria

1. When a reviewer defines an extraction field, the Schema Service shall store a field identifier, field name, description, expected data type, criticality, allowed values, evidence requirement, and review instructions.
2. If a field definition omits a required attribute or declares a data type outside the supported set, then the Schema Service shall reject the definition and report the offending field and attribute.
3. If two fields within one schema share a field identifier or a field name, then the Schema Service shall reject the schema and report the duplicate.
4. If a field declares allowed values that are inconsistent with its declared data type, then the Schema Service shall reject the definition and report the inconsistent values.
5. The Schema Service shall make each field's criticality and evidence requirement readable by schema consumers, and shall not itself alter routing, extraction, verification, or repair behaviour on the basis of those attributes.
6. When a reviewer assigns fields to groups, the Schema Service shall preserve each field's group label and the field ordering used for extraction.
7. When a schema is requested for use by an extraction run, the Schema Service shall release it only if it passes validation, and shall otherwise refuse release and report every validation error.

### Requirement 6: Schema Versioning and Output Pinning

**Objective:** As an evidence-synthesis reviewer, I want schema changes to create new versions rather than rewrite history, so that results already extracted remain interpretable against the schema that produced them.

#### Acceptance Criteria

1. When a reviewer saves a change to a project's schema, the Schema Service shall create a new schema version and shall leave all prior versions unchanged.
2. When an extraction run begins, the Schema Service shall record the exact schema version identifier used by that run.
3. While outputs produced under an earlier schema version exist, the Schema Service shall resolve those outputs against the schema version under which they were produced.
4. If a caller attempts to modify an existing schema version in place, then the Schema Service shall reject the modification and report that changes must create a new version.
5. When a reviewer requests a comparison of two schema versions, the Schema Service shall report which fields were added, which were removed, and which attributes changed.
6. The Schema Service shall provide the existing 62-field extraction map as the default seed schema version of a new project, so that a project created without any authored fields behaves exactly as the current pipeline does.

### Requirement 7: Schema Import and Column Mapping

**Objective:** As an evidence-synthesis reviewer, I want to import my field list from a spreadsheet or structured file, so that I do not have to re-enter a schema I already maintain elsewhere.

#### Acceptance Criteria

1. When a reviewer imports a schema from a CSV, Excel, JSON, or YAML source, the Schema Importer shall map source columns or keys onto extraction-field attributes and report the mapping it applied.
2. If a source column cannot be resolved to exactly one extraction-field attribute, then the Schema Importer shall hold the import unfinished and report each ambiguous or unmatched column together with its candidate attributes, rather than choosing on the reviewer's behalf.
3. When a reviewer supplies an explicit column-to-attribute mapping for a held import, the Schema Importer shall apply that mapping and complete the import.
4. When an import is completed, the Schema Importer shall validate the resulting fields against the extraction-field rules and shall create a new schema version only if validation passes.
5. If the component required to read the supplied source format is not installed, then the Schema Importer shall reject the import and report which optional component must be installed.
6. If an import source is malformed or contains no field rows, then the Schema Importer shall reject the import, leave the project's schema unchanged, and report the parse failure.

### Requirement 8: External Evidence Import and Origin Metadata

**Objective:** As an evidence-synthesis reviewer, I want to import evidence I extracted in a screening spreadsheet or another tool, so that the system can annotate and validate it alongside evidence it produced itself.

#### Acceptance Criteria

1. When a reviewer imports external evidence from a CSV, Excel, or JSON source, the Evidence Importer shall map imported columns onto fields of the project's schema and report any unmapped columns.
2. If an imported evidence row targets a field absent from the schema version it is imported against, then the Evidence Importer shall reject that row, report the unknown field, and continue processing the remaining rows.
3. If an imported evidence row references a document that is not in the project's corpus, then the Evidence Importer shall reject that row and report the unresolved document reference.
4. When evidence is imported, the Evidence Importer shall record for each item an origin descriptor that identifies it as externally imported and names its source, distinguishable from evidence generated within the system.
5. The Evidence Importer shall store imported evidence as items separate from system-generated evidence for the same field, and shall never overwrite or merge into system-generated evidence.
6. When an import completes, the Evidence Importer shall report the number of evidence items matched, the number left unresolved, and the number rejected.

### Requirement 9: Matching Imported Evidence to Canonical Text

**Objective:** As an evidence-synthesis reviewer, I want imported evidence snippets located in the actual document text, so that imported claims are anchored to a source position like system-generated ones.

#### Acceptance Criteria

1. When imported evidence carries a text snippet, the Evidence Matcher shall attempt to locate that snippet within the canonical text of the referenced document and shall report a match score for the attempt.
2. When imported evidence supplies a page number or citation locator, the Evidence Matcher shall use it to restrict or prioritise the searched region before searching the remainder of the document.
3. When a snippet matches canonical text at or above the configured confidence threshold, the Evidence Matcher shall produce an annotation candidate recording the document identifier, the matched location, the match score, and the hints used.
4. If more than one location matches at or above the threshold, then the Evidence Matcher shall record every such location as a competing candidate rather than selecting one silently.
5. If a supplied page hint disagrees with the best available match, then the Evidence Matcher shall record the disagreement on the candidate and discard neither the hint nor the match.
6. The Evidence Matcher shall produce location-resolved annotation candidates only, and shall not emit annotation artifacts itself.
7. If canonical text is unavailable for a referenced document, then the Evidence Matcher shall skip matching for that document's evidence and report the reason.

### Requirement 10: Unresolved Evidence Queue

**Objective:** As an evidence-synthesis reviewer, I want evidence that could not be placed to be held rather than dropped, so that nothing I imported is silently lost and a later review step can resolve it.

#### Acceptance Criteria

1. If imported evidence cannot be matched to a document location at or above the configured threshold, then the Evidence Importer shall place the item into the project's unresolved evidence queue together with the failure reason and the best partial matches found.
2. When a reviewer requests the unresolved evidence queue, the system shall report each queued item with its source, target field, snippet, supplied hints, and failure reason.
3. When a caller supplies a resolved location for a queued item, the system shall record the resolution, mark the item resolved, and retain that the resolution was supplied externally rather than computed.
4. The system shall retain unresolved items until they are resolved or explicitly discarded, and shall not drop them when an import is repeated.
5. When the same evidence item is imported again, the system shall not create a second queue entry for it.
6. The system shall expose the unresolved queue as readable data only, and shall not provide an interactive linking surface.

### Requirement 11: Headless Operation and Backward Compatibility

**Objective:** As an operator running EviTrace on a server, I want every new capability driveable from the command line and the existing flow left working, so that adopting projects and custom schemas does not require a graphical environment or break current runs.

#### Acceptance Criteria

1. The system shall expose project creation, document admission, corpus status reporting, schema authoring, schema import, evidence import, and unresolved queue inspection through command-line entry points that require no graphical environment.
2. If a command-line operation fails, then the system shall report a non-zero exit status together with a message naming the failing item and the reason.
3. While no project has been created, the system shall continue to run the existing directory-based extraction flow against the default seed schema, so that current usage is unaffected.
4. The system shall keep spreadsheet and Excel reading capability optional, such that every operation not requiring those formats succeeds when the corresponding component is absent.
5. When two operations target the same project concurrently, the system shall prevent interleaved writes from corrupting the project record, corpus index, schema versions, or unresolved evidence queue.
6. The system shall report every rejection, held import, and unresolved item through its normal logging channel in addition to the operation result, so that batch runs remain auditable without interactive inspection.
