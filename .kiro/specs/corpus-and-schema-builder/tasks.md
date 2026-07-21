# Implementation Plan — corpus-and-schema-builder

- [ ] 1. Foundation: package scaffold, shared models, project storage primitives, configuration, packaging

- [ ] 1.1 Create the corpus package scaffold with its typed error hierarchy and exit-code mapping
  - Add the package directory with sub-packages for schema, tables, and evidence, each with an `__init__.py` that re-exports only public names
  - Define the root error type plus input, validation, state, and environment error subclasses, each carrying a stable machine-readable code, a message naming the offending item, and optional structured details; include the unsupported-record-version error
  - Provide a single function mapping an error to its category exit code, following the design's grouping: input errors share one code, validation another, state another, environment another, and anything unexpected the generic code
  - Observable: importing the package succeeds on a clean checkout, and a table-driven test asserts every declared error class maps to the exit code its category specifies and that every class carries a unique machine-readable code
  - _Requirements: 11.2_
  - _Boundary: errors_

- [ ] 1.2 Define the shared dataclasses and enumerations for the whole package
  - Add the project, document, event, schema version, field, held import, run, imported evidence, annotation candidate, match outcome, and unresolved item models in one module, following the single-models-module convention used by quality control
  - Include the data type, criticality, evidence requirement, and document status enumerations, and give every persisted record a record-version string
  - Implement the record-version read rule: a reader accepts its own version and raises the unsupported-record-version error for anything newer rather than misparsing
  - Observable: every model round-trips through JSON serialization without loss, and loading a record whose declared version is newer than the reader supports raises the named error instead of returning a partly populated object
  - _Requirements: 1.1, 2.3, 4.1, 5.1, 8.4, 10.1_
  - _Boundary: models_

- [ ] 1.3 Implement project directory layout resolution
  - Resolve every project file and directory from a project root: project record, documents index, event log, admitted file copies, canonical text artifacts, schema versions and head pointer, held imports, materialized legacy maps, evidence imports, candidates, unresolved queue, run records, and the lock directory
  - Validate the project identifier against a strict pattern and refuse any resolved path that escapes the project root
  - Observable: a test supplying identifiers containing path separators, parent references, or empty strings gets a rejection, and every resolved path for a valid identifier is confirmed to sit under the project root
  - _Requirements: 1.3_
  - _Boundary: paths_

- [ ] 1.4 Implement the project lock, atomic writes, and the JSON/event store
  - Acquire an exclusive lock per project by atomic directory creation, recording owner process, host, and acquisition time, with bounded retry and a timeout error naming the recorded owner
  - Provide a forced-release operation that reports the recorded owner before removing a stale lock directory, for use by the recovery subcommand
  - Write every JSON record through a temporary file in the same directory followed by an atomic replace; append events one JSON object per line to the event log; provide reads for records and for events filtered by document
  - Observable: a test interrupting a write leaves the previous file intact and parseable, a second lock holder times out with an error naming the first holder's recorded process, and forced release makes the lock acquirable again
  - _Requirements: 3.4, 4.6, 11.5_
  - _Boundary: locking, store_

- [ ] 1.5 Register the projects configuration key and its loader
  - Add the new top-level key to the known-top-level-key set in the configuration loader so that configuration loading no longer rejects it
  - Add a loader returning the projects root, default project name, match threshold, maximum candidate count, and lock timeout, with defaults applied and unknown sub-keys rejected
  - Add the corresponding commented block to the shipped configuration file
  - Observable: loading a configuration file containing the new block succeeds and returns the documented defaults for omitted sub-keys; loading one with a misspelled sub-key fails with a message naming it
  - _Requirements: 1.4, 11.5_
  - _Boundary: config_utils, configs/config.yaml_

- [ ] 1.6 Declare the optional spreadsheet-reader extra in packaging metadata
  - Add the new optional-dependency extra for the spreadsheet reader to the project metadata, and add the matching commented line beside the existing optional block in the requirements file
  - Keep the core dependency set unchanged so a default install gains no new required package
  - Observable: the extra is installable by name, the default dependency list is byte-unchanged apart from the new comment, and the extra's name is exactly the one the missing-dependency error will quote
  - _Requirements: 11.4_
  - _Boundary: pyproject.toml, requirements.txt_

- [ ] 1.7 Extend dependency-direction enforcement for the new package
  - Add the forbidden pairs preventing the new package from importing quality control, agents, pipeline, or the PDF extractor, and preventing quality control, text processing, agents, and the PDF extractor from importing it
  - Add a cross-cutting structural test asserting the package's internal layer order by abstract syntax tree analysis: errors, then models, then paths, then locking, then store, then domain services, then the command-line layer
  - Observable: the dependency-direction suite passes on the scaffold, and a deliberately introduced upward import in a scratch fixture makes the internal-direction test fail with a message naming the file and the offending import
  - _Requirements: 11.3_
  - _Boundary: tests/test_dependency_directions.py, tests/steering_

- [ ] 2. Extraction schema model, versioning, and legacy compatibility

- [ ] 2.1 (P) Implement the extraction field model and its validation rules
  - Store field identifier, name, description, data type, criticality, allowed values, evidence requirement, review instructions, group label, ordinal, and examples
  - Reject missing required attributes, unsupported data types, allowed values declared on a non-categorical type, allowed values inconsistent with the declared type, duplicate identifiers, and duplicate case-insensitive whitespace-normalised names; return every error rather than the first
  - Expose criticality and evidence requirement as readable attributes without acting on them
  - Observable: a schema containing five distinct defects yields five errors in one validation call, and a valid schema has unique identifiers, unique normalised names, and dense unique ordinals
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  - _Boundary: ExtractionField, FieldValidator_
  - _Depends: 1.2_

- [ ] 2.2 Implement the immutable schema version store with diffing and run pinning
  - Derive the version identifier from the version ordinal and a content hash of the canonically serialized field list; write each version file exactly once and refuse to overwrite one
  - Maintain a head pointer that recovers to the highest complete ordinal when missing or dangling, and keep prior versions readable indefinitely
  - Re-validate before releasing a version for a run, refusing release and reporting every error; record and resolve the version pinned to a run; report added, removed, and attribute-changed fields between two versions
  - Observable: saving a change produces a new identifier while the prior version's bytes are unchanged; an in-place modification attempt raises the immutability error; a run record resolves back to the exact version it pinned
  - _Requirements: 5.7, 6.1, 6.2, 6.3, 6.4, 6.5_
  - _Boundary: SchemaVersionStore_

- [ ] 2.3 Implement the legacy extraction-map adapter in both directions
  - Convert the shipped 62-field map into the new model with the documented attribute mapping, defaulting criticality to standard and evidence requirement to required, and never inferring allowed values from the examples prose
  - Implement the inverse projection producing the exact seven-key entries in ascending field order, and materialize a chosen version to a legacy-shaped file inside the project
  - Observable: converting the shipped map and projecting it back yields the shipped entries unchanged, with 62 fields and no allowed values set
  - _Requirements: 6.6, 11.3_
  - _Boundary: LegacyMapAdapter_

- [ ] 3. Project aggregate and corpus management

- [ ] 3.1 Implement project lifecycle, seeding, and effective configuration
  - Create, open, and list projects; derive a stable project identifier from the name by slugification with a numeric suffix on collision; store name, description, research question, owner, creation timestamp, and configuration profile
  - Reject a duplicate name with an error naming the existing project identifier, and reject an unknown project identifier with a not-found error
  - Seed each newly created project with version one built from the shipped extraction map and set its head pointer, so a project is usable with no authoring step; if the seed source is missing or unreadable, fail with a clear error and leave no half-created project directory
  - Compute the effective configuration as a deep merge of the installation configuration under the project profile, returned to the caller and never written back to any global; resolve a missing project selection to the configured default project, creating and seeding it on first use
  - Observable: creating a project and immediately requesting its head schema returns a 62-field version; creating two projects with the same name yields a conflict error naming the first identifier; opening a project reports the record and the merged configuration with installation values filling every key the profile omits
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 6.6_
  - _Depends: 2.2, 2.3_
  - _Boundary: ProjectStore_

- [ ] 3.2 (P) Implement document identity and admission-time PDF validation
  - Compute the document identifier as the lowercase hexadecimal SHA-256 of the file's bytes, read in fixed-size chunks
  - Validate in fixed short-circuiting order: PDF magic bytes, non-zero size, the PDF opens, page count above zero, not password protected; emit a distinct reason code at each stop and report the page count and byte size on success
  - Use only the core PDF reader so validation works without the optional AGPL extra
  - Observable: a test asserts the computed identifier equals the pipeline manifest's content hash for the same bytes, and each of the six reason codes fires for its own fixture
  - _Requirements: 2.1, 2.4, 2.5, 2.6_
  - _Boundary: DocumentAdmissionService_
  - _Depends: 1.4_

- [ ] 3.3 Implement single and batch admission with per-document audit trails
  - Record filename, byte size, page count, admission timestamp, status, and an unset sensitivity label for each admitted document; copy the accepted file to a content-addressed location inside the project
  - Treat re-admission of identical content as a duplicate outcome that keeps the single existing record
  - Evaluate each file in a batch independently, continue past rejections, append an event per file, write the documents index once at the end under one lock, and report admitted, duplicate, and rejected counts with per-file reasons
  - Observable: a mixed batch of valid, invalid, and duplicate files admits only the valid ones and returns correct counts; re-running the same batch adds no records and yields identical identifiers; the event log reconstructs each document's admission history
  - _Requirements: 2.2, 2.3, 2.7, 3.1, 3.2, 3.3, 3.4, 3.5_
  - _Boundary: DocumentAdmissionService_

- [ ] 3.4 Implement the corpus status vocabulary, transitions, history, and rollup
  - Implement the six-value status vocabulary and the permitted-transition table exactly as designed, rejecting any other transition and leaving the stored status unchanged
  - Map pipeline stage outcomes onto corpus statuses through a single table that is total over the pipeline's manifest vocabulary including the numbered chunk-failure form, defaulting unrecognised outcomes to failed while preserving the raw outcome in the event
  - Record manual flags with a reviewer-supplied reason and a retained prior status, permit unflagging only back to that status, append every accepted and rejected transition to the event log, and report the rollup with per-status counts plus failed and flagged identifiers
  - Runs after admission because it mutates the same documents index and event log that admission writes; it is not parallel-safe with 3.3
  - Observable: a test walks every permitted edge successfully and every non-edge is refused with the status unchanged; the mapping table is asserted total over the manifest status set
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - _Depends: 1.4, 3.3_
  - _Boundary: CorpusStatusService_

- [ ] 4. Import readers, schema import, and external evidence import

- [ ] 4.1 (P) Implement the table reader for CSV, JSON, YAML, and Excel
  - Infer format from the file suffix unless given explicitly; return header order and header-keyed rows; parse YAML safely and accept both array-shaped and single-keyed-object-shaped JSON and YAML sources
  - Import the spreadsheet reader inside the function body only, and raise the missing-optional-dependency error naming the package and the declared extra when it is absent
  - Raise distinct errors for an unparsable source and for a source with no data rows
  - Observable: with the spreadsheet reader mocked absent, reading a workbook raises the named error while CSV, JSON, and YAML reads still succeed, and importing the module leaves the spreadsheet package out of the loaded-module table
  - _Requirements: 7.5, 7.6, 11.4_
  - _Depends: 1.6_
  - _Boundary: TableReader_

- [ ] 4.2 Implement schema import with column mapping and held imports
  - Map source headers onto field attributes via a normalised alias table, marking a column confident only when it matches exactly one attribute
  - When any column is ambiguous or unmatched, write a held import recording every unresolved column with its candidate attributes and sample values, create no fields and no version, and report the held state
  - Complete a held import from an explicit column-to-attribute mapping, re-running mapping and validation, and create a version only when validation passes; leave the head pointer untouched on failure and report every error
  - Observable: importing a source with one ambiguous header returns a held result naming that header and its candidates with no new version; supplying the mapping produces a new version whose fields match the source rows
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6_
  - _Depends: 2.2, 4.1_
  - _Boundary: SchemaImporter_

- [ ] 4.3 (P) Implement the canonical text provider
  - Define the provider contract returning full text, per-page texts, and optional structural blocks for a document, in the argument shapes the existing lexical matcher expects
  - Implement the file-backed provider reading the project's canonical text artifact, returning nothing when the artifact is absent, and never opening the PDF as a fallback
  - Observable: a document with a canonical artifact returns populated text and page texts; a document without one returns nothing, which is the sole unavailable condition consumed downstream
  - _Requirements: 9.1, 9.7_
  - _Depends: 1.3_
  - _Boundary: CanonicalTextProvider_

- [ ] 4.4 Implement hint-aware evidence matching with competing candidates
  - Search hinted pages first, then pages implied by a citation locator, then the rest of the document, recording which hints were used
  - Run a normalised containment pass using the existing lexical matcher, then a graded similarity pass using the existing comparison processor, continuing past the first hit up to the configured candidate cap
  - Record document, page, character span, score, match pass, hints used, and a hint-conflict marker on each candidate; sort by descending score; report matched, below-threshold, unavailable-text, or too-short outcomes with the best partial matches retained
  - Observable: a snippet occurring on two pages above threshold yields two candidates rather than one, and a page hint disagreeing with the best match sets the conflict marker while keeping both
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - _Depends: 4.3_
  - _Boundary: EvidenceMatcher_

- [ ] 4.5 (P) Implement the unresolved evidence queue
  - Key entries by evidence item identifier so a repeated import updates the existing entry instead of adding a second
  - List entries with source, target field, snippet, supplied hints, failure reason, and best partial matches; record an externally supplied resolution as manual and mark the item resolved; support explicit discard
  - Retain resolved and discarded entries rather than deleting them, and expose reads only with no interactive surface
  - Observable: an item enqueued twice appears once; resolving records the supplied location and a manual resolution source; listing after resolution and discard still returns those entries under their new status
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_
  - _Depends: 1.4_
  - _Boundary: UnresolvedEvidenceQueue_

- [ ] 4.6 Implement evidence row mapping, reference resolution, and per-row rejection
  - Read an evidence source, map its columns onto fields of a specified or head schema version, and report the columns that map to nothing
  - Resolve each row's document reference by identifier, filename, or content-hash prefix, treating an ambiguous filename reference as a row rejection rather than a guess
  - Reject rows targeting a field absent from the pinned version and rows referencing a document outside the corpus individually, recording the reason and continuing with the remaining rows
  - Observable: a source mixing valid rows, an unknown field, an unknown document, and an ambiguous filename yields resolved items for the valid rows only, with one distinctly reasoned rejection per bad row and the unmapped columns listed
  - _Requirements: 8.1, 8.2, 8.3_
  - _Depends: 2.2, 3.3, 4.1_
  - _Boundary: EvidenceImporter_

- [ ] 4.7 Implement imported-evidence persistence, origin metadata, and outcome routing
  - Attach an origin descriptor naming the import kind, source, import identifier, timestamp, and row number, plus a reserved unset evidence-node carrier; store imported items in their own import record and never write into or merge with system-generated evidence
  - Derive item identity from document, field, normalised snippet, and source so re-import is idempotent; route above-threshold match outcomes to persisted annotation candidates and every other outcome to the queue with its reason and best partial matches
  - Report matched, unresolved, and rejected counts together with the schema version identifier used
  - Observable: importing a mixed file returns the three counts and re-importing the same file creates no duplicate annotation candidate and no second queue entry, while system-generated evidence for the same field is byte-unchanged
  - _Requirements: 8.4, 8.5, 8.6, 10.1, 10.5_
  - _Depends: 4.4, 4.5, 4.6_
  - _Boundary: EvidenceImporter_

- [ ] 5. Headless interface and pipeline integration

- [ ] 5.1 Build the command-line parser with project, document, and corpus subcommands
  - Implement the argument parser with a global project selector and configuration path, plus subcommands for project create, open, list, and forced unlock; document add, list, show, flag, and unflag; and corpus status
  - Emit JSON to standard output by default with a table alternative, and route every diagnostic through the shared logger rather than direct printing
  - Observable: each subcommand runs end to end against a temporary projects root and returns zero, the flag subcommand requires and records a reason, and forced unlock prints the recorded lock owner before releasing
  - _Requirements: 11.1, 11.2_
  - _Depends: 3.1, 3.3, 3.4_
  - _Boundary: CorpusCLI_

- [ ] 5.2 Add schema subcommands to the command-line interface
  - Add show, versions, diff, import, import-complete, and materialize subcommands to the existing parser tree
  - Render the held-import result so an operator can read every unresolved column and its candidate attributes, and accept the completing mapping as repeatable key-value arguments
  - Observable: an ambiguous import followed by an import-complete invocation creates a new version, and diff prints added, removed, and changed fields between two versions
  - _Requirements: 11.1, 6.5, 7.1, 7.3_
  - _Depends: 4.2, 5.1_
  - _Boundary: CorpusCLI_

- [ ] 5.3 Add evidence and queue subcommands to the command-line interface
  - Add evidence import plus queue list, resolve, and discard subcommands to the existing parser tree, with the queue commands reading and updating stored records only
  - Print the matched, unresolved, and rejected counts and the per-row rejection reasons from an import
  - Observable: importing an evidence file then listing the queue shows the unresolved items with their reasons, and resolving one moves it out of the unresolved listing while remaining retrievable
  - _Requirements: 11.1, 8.6, 10.2, 10.3_
  - _Depends: 4.7, 5.2_
  - _Boundary: CorpusCLI_

- [ ] 5.4 Wire the module entry point, error-to-exit-code handling, and the logging surface
  - Add the module main so the package is runnable as a module, catching every error at the top level, logging it, printing the structured payload, and returning the mapped category exit code, with unexpected exceptions logged with a traceback under the generic code
  - Emit a warning record for every admission rejection, row rejection, held import, and queue entry, and truncate document identifiers only in log lines
  - Observable: running the module with a failing scenario from each error category returns that category's documented exit code, and a test asserts no module in the package calls direct printing while each rejection path emits a warning record
  - _Requirements: 11.1, 11.2, 11.6_
  - _Depends: 5.1, 5.2, 5.3_
  - _Boundary: CorpusCLI, errors_

- [ ] 5.5 Integrate the project into the existing pipeline entry point
  - Add an optional project selector to the existing entry point; without it, resolve inputs exactly as today from the configured directory and the shipped extraction map
  - With it, resolve admitted document paths from the project corpus, materialize the pinned schema version to the legacy shape, write the run record pinning that version, invoke the existing pipeline runner unchanged, and afterwards report each document's outcome to the corpus status service
  - Keep this the only module importing both the new package and the pipeline; change no pipeline module, prompt builder, or extraction-map loader
  - Observable: a run without the selector produces identical argument resolution to today, and a run with it leaves a run record naming the schema version and corpus statuses updated from the pipeline outcomes
  - _Requirements: 4.2, 6.2, 6.3, 11.3_
  - _Depends: 3.1, 3.4, 2.2, 2.3_
  - _Boundary: main.py_

- [ ] 5.6 Persist canonical text artifacts from completed runs
  - After a project-scoped run, project each processed document's already-produced canonical full text, per-page texts, and structural blocks into the project's canonical text artifact, in the shape the provider reads
  - Produce no canonical text of its own and add no parsing: this is a projection of existing pipeline output into the project directory, performed at the same integration boundary as the status report-back
  - Skip and log documents whose run produced no usable text rather than writing an empty artifact
  - Observable: after a project-scoped run, evidence matching for a processed document returns real candidates instead of the unavailable-text outcome, and a document that failed its run has no artifact written
  - _Requirements: 9.1, 9.7_
  - _Depends: 4.3, 5.5_
  - _Boundary: main.py_

- [ ] 6. Validation

- [ ] 6.1 Verify legacy schema fidelity and prompt-path compatibility
  - Assert the seeded version's legacy projection is byte-identical to the shipped extraction map, including key order, ordering by field index, and serialization formatting
  - Assert a materialized version file loads through the existing extraction-map grouping logic without error and produces the same chunk assignment as the shipped file
  - Observable: both assertions pass against the unmodified shipped map, and mutating any one field in the seeded version makes the byte-identity assertion fail
  - _Requirements: 6.6, 11.3_
  - _Depends: 2.3, 3.1, 5.5_

- [ ] 6.2 (P) Verify concurrency safety and write atomicity
  - Exercise two concurrent writers admitting overlapping batches into one project and assert a consistent documents index with no lost record
  - Assert a blocked writer past the timeout raises the lock error naming the recorded owner, and that an interrupted write leaves the previous file intact and parseable
  - Observable: both tests pass repeatedly without flakiness under the default suite
  - _Requirements: 11.5_
  - _Depends: 1.4, 3.3_
  - _Boundary: locking, store_

- [ ] 6.3 (P) Verify optional-dependency isolation and package import hygiene
  - Assert the package imports cleanly with the spreadsheet reader, the OCR extra, and the semantic extras all absent, and that no heavy or optional package appears in the loaded-module table after import
  - Assert the missing-dependency error names both the package and the extra exactly as declared in packaging metadata
  - Observable: the suite passes in an environment with only the core dependencies installed
  - _Requirements: 11.4_
  - _Depends: 1.6, 4.1_
  - _Boundary: TableReader, package imports_

- [ ] 6.4 End-to-end project lifecycle integration test
  - Drive one flow through the command-line entry point: create two projects, admit a mixed batch into each, confirm isolation, import an ambiguous schema and complete it, seed canonical text artifacts, import a mixed evidence file, inspect the corpus rollup and the unresolved queue, resolve one queued item, and discard another
  - Assert cross-project isolation, correct counts at each stage, retained history, and the documented non-zero exit codes for the deliberate failure cases
  - Observable: the full flow runs headlessly against a temporary projects root with synthetic fixtures containing no real patient data, and every asserted count and status matches the design's stated behaviour
  - _Requirements: 1.3, 3.3, 4.5, 7.2, 7.3, 8.6, 10.2, 10.3, 11.1, 11.2_
  - _Depends: 5.4, 5.6_
