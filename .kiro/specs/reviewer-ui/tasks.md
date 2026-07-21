# Implementation Plan

- [ ] 1. Foundation: packages, configuration, shared types, and boundary enforcement

- [ ] 1.1 Create the two package skeletons, the configuration block, and the optional dependency extra
  - Create `src/review/` and `src/reviewer_ui/` with `__init__.py` files that re-export nothing yet, plus the `export/`, `routes/`, and `static/` subdirectories
  - Register `review` in `_ALL_KNOWN_TOP_LEVEL_KEYS` and add `load_review_config()` resolving env over yaml over default for every key in the design's configuration block
  - Add the `review:` block to `configs/config.yaml` with commented defaults, and add project review-directory and export-directory resolvers to `path_utils`
  - Declare the `ui = ["fastapi>=0.115", "uvicorn>=0.30"]` optional extra in `pyproject.toml` and a matching commented block in `requirements.txt`
  - Observable completion: `load_local_config()` accepts a config file containing the `review:` block, `load_review_config()` returns every documented key with its default, and `pip install -e ".[ui]"` resolves while a default install still succeeds
  - _Requirements: 1.3, 1.4_

- [ ] 1.2 Define the review error hierarchy and every shared dataclass and vocabulary
  - Implement `errors.py` with `ReviewError` and the subclasses named in the error-handling table, each carrying a stable machine-readable `code`, and an `exit_code_for` mapping
  - Implement `models.py` with every frozen dataclass and `Literal` vocabulary from the design's State Management block, including the action payload table's required keys as a declarative mapping
  - Keep `ReviewActionKind` and `QueueActionKind` disjoint and expose `ActionKind` as their union
  - Observable completion: a test asserts every dataclass is frozen, every `Literal` member round-trips through the serializer, and no field name collides with a `provenance` node field or a `corpus` record field
  - _Requirements: 4.1, 4.8, 5.1, 6.2, 7.1, 9.2, 9.3, 11.2_

- [ ] 1.3 Add dependency-direction and internal-layer boundary tests for both packages
  - Append the fourteen new `FORBIDDEN_PAIRS` entries to `tests/test_dependency_directions.py` plus one named test per pair, covering both directions between the two new packages and `pipeline`, `quality_control`, `pdf_extractor`, `agents`, `text_processing`, `corpus`, `provenance`, and `artifact_generation`
  - Add assertions that no module under `src/review/` imports `fastapi`, `uvicorn`, or `reviewer_ui`
  - Add `tests/steering/test_review_internal_direction.py` asserting the declared intra-package module order for both packages by AST
  - Observable completion: the boundary suite passes against the empty skeletons and fails when a deliberately introduced forbidden import is added
  - _Requirements: 1.3, 11.7, 12.8_

- [ ] 2. Reviewer identity and the append-only review record

- [ ] 2.1 Implement reviewer identity resolution, anonymization, and timestamping
  - Resolve the reviewer through explicit argument, then `REVIEW_REVIEWER_ID`, then configuration, with no fallback to an operating-system user or a literal unknown value
  - Generate the project salt once into `review/identity_salt.json` and never regenerate it; derive the anonymized identifier as a truncated salted digest and set `ReviewerRef.anonymized`
  - Emit all timestamps as UTC ISO 8601 with an explicit offset from a single method
  - Observable completion: the anonymized identifier is byte-identical across two constructions and differs across two projects, and resolving with no configured identity raises `ReviewerIdentityRequiredError` instead of defaulting
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_
  - _Boundary: ReviewerIdentity_

- [ ] 2.2 Implement the append-only event store over the project lock
  - Implement `append`, `read_events`, `last_sequence`, `write_snapshot`, and `read_snapshot`; expose no update and no delete method
  - Assign the monotonic sequence at append time, deduplicate by `action_id`, and stamp every event with the artifact key and schema version in force
  - Reuse the corpus project lock and atomic write helpers; make the JSONL append the commit point and the snapshot a derived write-through file
  - Observable completion: appending two events with the same `action_id` writes one line and reports the second as already present, and a failed snapshot write leaves the appended event durable and rebuildable
  - _Requirements: 5.1, 5.2, 8.1, 8.5, 8.6, 14.2_
  - _Boundary: ReviewEventStore_
  - _Depends: 2.1_

- [ ] 2.3 Implement the pure projection from events to current review state
  - Implement `project_review` with no clock, no filesystem access, and no randomness, producing `FieldReviewState` per document and field
  - Set `original_model_value` once from the first observing event and append a `ValueRevision` per edit, retaining every intermediate value with its reviewer, timestamp, and reason
  - Skip an uninterpretable event into a `ProjectionAnomaly` and continue; mark a field whose events carry a different artifact key as superseded
  - Observable completion: replaying a recorded log reproduces the stored snapshot exactly, and a log containing one malformed line yields the remaining state plus one reported anomaly
  - _Requirements: 5.3, 5.4, 8.2, 8.4, 8.7, 10.6_
  - _Boundary: ReviewProjection_

- [ ] 3. Anchor resolution and page geometry

- [ ] 3.1 Implement page geometry reading without rasterization
  - Read page count, width, height, and rotation through `pdfplumber` only; extract no text and render no image
  - Cache geometry per document for the process lifetime and report an unreadable or absent document as a null result rather than raising
  - Observable completion: geometry for a synthetic multi-page fixture returns the expected dimensions, an absent file returns a null result, and no test observes `fitz` or an image buffer
  - _Requirements: 2.1, 6.7_
  - _Boundary: PageGeometryProvider_

- [ ] 3.2 Implement anchor-to-view resolution and manual location validation
  - Map a provenance source anchor onto a `ViewAnchor` using the four render modes, copying precision verbatim and never deriving, upgrading, or downgrading it
  - Convert absolute top-left-origin rectangles into clamped fractional rectangles; degrade a zero-area, out-of-range, or unsupported-rotation case to an approximate page anchor with the reason on `geometry_note`
  - Produce truncated `search_text` for the approximate text mode as a display hint only, and implement `validate_location` for reviewer-supplied manual locations
  - Observable completion: a table-driven test over exact, approximate-with-page, approximate-with-text, absent, zero-area, out-of-range-page, rotated-page, and missing-file inputs yields the expected mode for each, and no case reports `coordinate` with a non-exact precision
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.7, 9.3_
  - _Boundary: AnchorResolver_
  - _Depends: 3.1_

- [ ] 4. Upstream artifact ingestion

- [ ] 4.1 (P) Implement the extraction result provider over published multiagent artifacts
  - Parse `decisions.json`, `verdicts.json`, `verifications.json`, and `answers.json` into `ExtractedFieldResult` values without importing `pipeline`
  - Fall back to the single-extractor extracted-JSON output when the multiagent artifacts are absent, reporting the richer signals as unavailable
  - Report every absent key in `unavailable` with a reason and never substitute a default; ignore unknown keys rather than rejecting them
  - Observable completion: fixture dicts built from the documented multiagent shapes yield populated results, and removing the verification artifact yields a result whose verification status is reported unavailable rather than null-as-value
  - _Requirements: 1.6, 9.6, 9.7_
  - _Boundary: ExtractionResultProvider_

- [ ] 4.2 (P) Implement the provenance provider over the serialized graph
  - Read the provenance graph through the provenance package's reader and expose evidence nodes, their anchors, claim-to-evidence edges, and the completeness state
  - Compute, adjust, and re-derive nothing; report an absent or unreadable graph as unavailable with a reason
  - Observable completion: a fixture graph yields evidence nodes whose anchors round-trip unchanged into the resolver's input shape, and a missing graph file yields an unavailable result rather than an exception
  - _Requirements: 1.6, 2.5_
  - _Boundary: ProvenanceProvider_

- [ ] 4.3 (P) Implement the corpus provider and the provider set container
  - Wrap the corpus services for project record, document records, status history, corpus rollup, schema version and field definitions, unresolved queue, and document path resolution
  - Define no status vocabulary and no schema vocabulary locally; expose `ArtifactProviderSet` as the single frozen container downstream services take
  - Observable completion: a fixture project yields document records with corpus-assigned statuses, a chronological history, a rollup naming failed and flagged documents, and the pinned schema version for a document
  - _Requirements: 1.5, 10.2, 10.3, 10.4, 10.5_
  - _Boundary: CorpusProvider_

- [ ] 5. Review command handling

- [ ] 5.1 Implement the review action service
  - Resolve the reviewer before any other validation, then validate the action kind against the closed vocabulary and the payload against the action payload table
  - Verify the named document, field, and evidence identifiers exist; reject a missing target with the item named and the log unchanged
  - Record a re-extraction request into the queue file without invoking any agent, recompute the snapshot after a successful append, and return the new field state and review status
  - Log every rejection at warning level in addition to returning it, and report an unpersisted action as not recorded
  - Observable completion: one accepted and one rejected case per action kind passes, a repeated `action_id` appends once and reports already-applied, and a simulated append failure leaves `actions.jsonl` byte-identical
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 5.5, 14.2, 14.6_
  - _Boundary: ReviewActionService_
  - _Depends: 2.2, 2.3, 4.1, 4.3_

- [ ] 5.2 Implement manual evidence creation and unresolved-evidence linking
  - Require at least one of page number, character span, paragraph identifier, or note for manual evidence, validate the location, and mark the origin as human-created
  - Resolve and discard queue items through the corpus queue service inside the same lock acquisition that appends the review event, so the two records cannot diverge
  - List queued items with their source, target field, snippet, hints, failure reason, and best partial matches exactly as stored, and produce location-resolved records only
  - Observable completion: linking an item records a manual resolution in the corpus queue and a matching event in the review log, discarding retains the item in a discarded state, and an invalid location leaves both stores unchanged
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_
  - _Boundary: EvidenceLinker_
  - _Depends: 3.2, 4.3_

- [ ] 6. Merger, stamping, and export

- [ ] 6.1 Implement the record merger
  - Iterate the pinned schema version's fields rather than the answer set, emitting a not-extracted record with its reason for any field without an answer and an unmapped record for any answer without a field
  - Populate every attribute the design's `FullRecord` names, including anchor precision, evidence origin, criticality, parser risk, quality-control issue codes, adjudication outcome, and decision rule, carrying both the original and current values from the projection
  - Record every unavailable signal with a reason, mark superseded review state, isolate a per-document failure into a document outcome, and order records deterministically
  - Observable completion: two merges of identical inputs compare equal, every pinned-schema field appears exactly once, and a deliberately broken document yields a failed outcome while the other documents still merge
  - _Requirements: 8.7, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 14.1_
  - _Boundary: RecordMerger_
  - _Depends: 2.3, 3.2, 4.3_

- [ ] 6.2 Implement the export stamp and the optional artifact catalog
  - Build the stamp from the run's recorded schema version, model identities, parser identities, and pipeline configuration, wrapping each in an available or unavailable envelope with a reason, and deriving nothing
  - Copy only an allowlisted configuration subset and skip any credential-shaped key
  - Discover the quality-control report, agreement report, cost report, and audit package as references carrying kind, path, presence, media type, and an absence reason; serve their bytes unchanged and generate no substitute
  - Observable completion: a run with a missing model record still produces a written stamp with that entry marked unavailable, and an absent cost report is reported unavailable rather than as an artifact containing no findings
  - _Requirements: 7.6, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_
  - _Boundary: ExportStamp, ArtifactCatalog_
  - _Depends: 4.3_

- [ ] 6.3 (P) Implement the JSON and manual-review-queue exporters
  - Write a per-document JSON file and a master JSON file spanning the project, embedding the stamp and the artifact references in the master file
  - Write the manual-review queue as both JSON and a delimited table, covering every field awaiting attention with the reason it is queued
  - Write through the atomic temp-file-then-replace helper so a failed write leaves the previous file intact and names the failing output
  - Observable completion: exporting a fixture project writes all four files, re-exporting identical inputs produces byte-identical files, and a forced write failure leaves the prior file parseable
  - _Requirements: 12.1, 12.2, 12.6, 12.7, 14.1_
  - _Boundary: JsonExporter, QueueExporter_
  - _Depends: 6.1, 6.2_

- [ ] 6.4 (P) Implement the review table exporter with optional spreadsheet output
  - Define one shared column contract used by both the delimited table and the workbook so the two cannot diverge
  - Always write the delimited table; import the spreadsheet component inside the function body and raise a named optional-dependency error when it is absent, writing no partial workbook
  - Observable completion: the workbook and the delimited table carry identical rows, and with the spreadsheet component mocked absent the workbook call raises an error naming the package and the extra while the delimited table remains written
  - _Requirements: 12.3, 12.4, 12.5, 12.7_
  - _Boundary: TableExporter_
  - _Depends: 6.1, 6.2_

- [ ] 6.5 Implement the headless review command-line entry point
  - Build the subcommand tree for review state, field history, queue listing and resolution, action recording, merge, export, and artifact listing, with global project and config flags
  - Emit JSON on stdout by default with a table alternative, send diagnostics to the logger only, and map each error class to its own non-zero exit code
  - Observable completion: every subcommand runs through `main(argv)` returning 0 on success and its mapped code on failure, and importing the module leaves the web runtime out of `sys.modules`
  - _Requirements: 1.3, 8.3, 11.7, 12.8, 14.6_
  - _Boundary: ReviewCLI_
  - _Depends: 5.1, 5.2, 6.3, 6.4_

- [ ] 7. Local web adapter

- [ ] 7.1 Build the application factory, dependency wiring, launcher, and bind guard
  - Implement the application factory mounting a router registry and the static directory, plus the uniform error envelope mapping each error class to its documented status code
  - Implement request-scoped construction of the domain services so no route builds a store or a lock itself
  - Create the router registry the factory iterates, so each later route task appends exactly one entry rather than editing the factory
  - Implement the launcher: bind to the configured host, refuse a non-loopback host unless explicitly allowed, name the setting in the refusal, and report the bound address
  - Observable completion: the application starts against a fixture project and serves the static index, a non-loopback host is refused by name, and starting with the web runtime absent reports which component to install
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 14.1_
  - _Boundary: ReviewApp, UiLauncher_
  - _Depends: 1.1_

- [ ] 7.2 (P) Implement the field view builder and the field and evidence routes
  - Compose extraction results, review state, schema field definitions, and evidence views into a field view carrying confidence, verification status, parser risk, review status, criticality, manual-review reason, quality-control issue codes, and adjudication outcome as recorded
  - Report unavailable signals as unavailable rather than as defaults, pass supplied disclosure labels through unmodified, and serve evidence views with their resolved view anchors
  - Implement filtering by review status, confidence, verification status, and criticality, returning the excluded count, and expose per-field value history
  - Observable completion: the field endpoint returns its declared model for a fixture document, a filtered request reports a non-zero excluded count, and a field with no navigable evidence returns the recorded reason
  - _Requirements: 3.3, 3.4, 3.7, 9.1, 9.4, 9.5, 9.6, 9.7, 14.5_
  - _Boundary: FieldViewBuilder, FieldRoutes, EvidenceRoutes_
  - _Depends: 7.1, 4.1, 4.3, 3.2, 2.3_

- [ ] 7.3 (P) Implement the project and document routes including document byte streaming
  - Serve the project summary with the corpus rollup, the document list with status and review progress, and the document detail with chronological status history and failure reasons
  - Stream document bytes for rendering, resolving the path only through the corpus provider so an identifier can never be joined into a path directly
  - Return field and evidence payloads unaffected when the document file itself is unavailable
  - Observable completion: the document list carries statuses and unreviewed-field counts, the detail view carries an ordered history, and requesting a document whose file is missing returns a named unavailable response while its fields still load
  - _Requirements: 2.6, 10.1, 10.2, 10.3, 10.4, 10.6_
  - _Boundary: ProjectRoutes, DocumentRoutes_
  - _Depends: 7.1, 4.3, 2.3_

- [ ] 7.4 (P) Implement the review action route
  - Accept the action request model, delegate to the action service, and return the recorded event and the new field state
  - Map an already-applied action identifier to a conflict response and a payload violating the action table to an unprocessable-entity response, each with the documented error envelope
  - Observable completion: each action kind posts successfully against a fixture document, a repeated action identifier returns the conflict status without a second log line, and an unknown field returns a not-found response with the field named
  - _Requirements: 4.1, 4.6, 4.7, 5.4_
  - _Boundary: ActionRoutes_
  - _Depends: 7.1, 5.1_

- [ ] 7.5 (P) Implement the unresolved evidence queue routes
  - Serve the queue with each item's full recorded context, and implement the link and discard endpoints over the evidence linker
  - Return an invalid location as an unprocessable-entity response leaving both the queue and the review log unchanged
  - Observable completion: listing returns fixture items with hints and partial matches, linking returns the resolved item, discarding returns the item in a discarded state, and an invalid location changes nothing
  - _Requirements: 6.3, 6.4, 6.5_
  - _Boundary: QueueRoutes_
  - _Depends: 7.1, 5.2_

- [ ] 7.6 (P) Implement the export, artifact, and metadata routes
  - Trigger merge and export from the export endpoint and return the written paths per format
  - Serve the artifact reference list and stream an individual artifact's bytes as produced, reporting an absent artifact as unavailable with a reason
  - Serve the keyboard shortcut map and the workspace disclaimers stating that acceptance records a human decision and is not a correctness or clinical claim
  - Observable completion: posting an export request writes the files and returns their paths, requesting an absent artifact returns a named unavailable response, and the metadata endpoint returns a non-empty shortcut map and the disclaimer text
  - _Requirements: 3.6, 12.1, 12.2, 13.1, 13.3, 14.3_
  - _Boundary: ExportRoutes, ArtifactRoutes, MetaRoutes_
  - _Depends: 7.1, 6.2, 6.3_

- [ ] 8. Browser workspace

- [ ] 8.1 Vendor the PDF renderer and build the workspace shell
  - Vendor the prebuilt PDF renderer under the static vendor directory together with its license file and a `VENDOR.md` recording the pinned version and its provenance
  - Implement the workspace document, the fetch wrapper over the JSON API, the client view state holding no authority, and the bootstrap that routes between the document, timeline, and queue views
  - Reference only same-origin relative paths in every asset so the workspace needs no network access
  - Define the shell's mount points and view registry so each later view task adds its own module file plus one registration entry rather than editing the shell
  - Observable completion: opening the served index renders the shell against a fixture project with no outbound request, and a grep over the client assets finds no absolute external URL
  - _Requirements: 1.7, 2.6_
  - _Boundary: StaticViewer_
  - _Depends: 7.1_

- [ ] 8.2 (P) Implement page rendering and the highlight overlay
  - Render pages through the vendored renderer and always build the text layer, since it is what makes approximate text placement and reviewer text selection possible
  - Draw one positioned element per evidence view sized from the fractional rectangle multiplied by the rendered page box, keeping overlapping highlights individually selectable
  - Give each render mode a distinct visual treatment plus a textual label so approximation is never conveyed by colour alone, and give evidence origin a second orthogonal visual channel plus a label
  - Navigate to a field's evidence on selection and move between multiple locations for one field
  - Observable completion: a fixture document renders coordinate, approximate-page, approximate-text, and absent evidence with four distinguishable treatments, and selecting a field scrolls the viewer to its first linked location
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7, 3.1, 3.2, 9.2_
  - _Boundary: PdfViewer, HighlightOverlay_
  - _Depends: 8.1, 7.2_

- [ ] 8.3 (P) Implement the inspection panel
  - Display the field name, extracted value, evidence text, confidence, verification status, and review status on highlight selection, and the recorded reason when a field has no navigable evidence
  - Show both the original model value and the current human value whenever they differ, labelling which is the human-edited value
  - Render confidence, verification status, parser risk, review state, criticality, anchor precision, and evidence origin, showing an unavailable signal as unavailable rather than as a default
  - Render the acceptance disclaimer alongside the action controls
  - Observable completion: selecting a highlight on a fixture document populates every listed element, an edited field shows both values with the human value labelled, and a field with a missing confidence signal shows it as unavailable
  - _Requirements: 3.3, 3.4, 5.6, 9.1, 9.2, 9.3, 9.6, 14.3_
  - _Boundary: InspectionPanel_
  - _Depends: 8.1, 7.2_

- [ ] 8.4 (P) Implement the document status timeline view
  - Render the document list with current status and review progress, the per-document chronological status history with each transition's timestamp, reporting stage, and failure reason, and the corpus rollup with failed and flagged identifiers
  - Show the count of fields still unreviewed for each document
  - Observable completion: the timeline view for a fixture project shows every document with a status, an ordered history for the selected document, and a rollup whose counts match the corpus provider's response
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6_
  - _Boundary: TimelineView_
  - _Depends: 8.1, 7.3_

- [ ] 8.5 (P) Implement the unresolved evidence linking surface
  - Render each queued item with its source, target field, snippet, supplied hints, failure reason, and best partial matches, and offer link and discard controls posting to the queue endpoints
  - Report a rejected link inline without clearing the reviewer's entered location
  - Observable completion: the queue view lists fixture items with full context, linking one removes it from the unresolved list and shows it as resolved, and discarding one shows it in a discarded state
  - _Requirements: 6.3, 6.4, 6.5_
  - _Boundary: QueueSurface_
  - _Depends: 8.1, 7.5_

- [ ] 8.6 (P) Implement keyboard shortcuts, the help overlay, and the safety disclaimers
  - Bind accept, edit, and reject for the focused field plus next-field and previous-field movement, suppressing every binding while a text input holds focus
  - Render a discoverable help overlay built from the shortcut map served by the metadata endpoint
  - Render the workspace disclaimers and ensure no view offers synthesis generation, narrative generation, or a clinical recommendation
  - Observable completion: each bound key triggers its action on the focused field, typing in the edit input triggers no review action, the help overlay lists every served binding, and a review of the client modules finds no generation affordance
  - _Requirements: 3.5, 3.6, 14.3, 14.4_
  - _Boundary: KeyboardShortcuts_
  - _Depends: 8.1, 7.6, 8.3_

- [ ] 9. Integration and validation

- [ ] 9.1 Verify the action record, replay, and headless read path end to end
  - Record actions of several kinds, discard the snapshot, replay the log, and assert the projection equals the state the workspace displayed
  - Read the same state, history, and queue back through the command-line entry point with no web runtime installed
  - Observable completion: the replayed projection compares equal to the snapshot, and the command-line state output matches the API response for the same project
  - _Requirements: 8.1, 8.2, 8.3_
  - _Depends: 6.5, 7.4_

- [ ] 9.2 Verify the export formats end to end
  - Merge a fixture project and assert per-document JSON, master JSON, the delimited table, both queue exports, and the stamp are written with consistent content
  - Assert the workbook path raises the named optional-dependency error with the component mocked absent while the delimited table remains written, and that a forced write failure leaves the prior file intact and names the failing output
  - Observable completion: all six outputs exist with matching row counts, and both failure paths behave as specified without losing an already-written output
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_
  - _Depends: 6.3, 6.4_

- [ ] 9.3 Verify the HTTP contract and the bind guard
  - Exercise every endpoint in the API contract table against a fixture project and assert each returns its declared model
  - Assert a missing document file still returns field and evidence payloads, an already-applied action returns the conflict status, a payload violating the action table returns the unprocessable-entity status, and a non-loopback host is refused by name
  - Skip the whole module cleanly when the web runtime is absent so a default install runs green
  - Observable completion: the route suite passes with the web runtime installed and is skipped with a clear reason without it
  - _Requirements: 1.1, 1.2, 1.6, 2.6, 3.3, 3.7, 4.7, 10.1_
  - _Depends: 7.2, 7.3, 7.4, 7.5, 7.6_

- [ ] 9.4 Verify the standing boundaries, headless isolation, logging surface, and fixture policy
  - Assert no module under either package imports the annotation module or constructs an annotation record or JSON-LD document
  - Assert the merger, exporters, and command-line module import cleanly with the web runtime and the spreadsheet component removed from `sys.modules`
  - Assert rejections, failed exports, unavailable artifacts, projection anomalies, and superseded records each emit a warning, and that no module calls `print`
  - Assert every fixture and bundled asset is synthetic, with no real paper, patient data, or credential, and that the vendored directory carries its license and version record
  - Observable completion: all four boundary suites pass, and each fails when its violation is deliberately introduced
  - _Requirements: 1.3, 1.7, 11.7, 12.8, 14.6, 14.7_
  - _Depends: 6.5, 8.1_

- [ ]* 9.5 Add property tests for projection purity, geometry conversion, and merge totality
  - Assert projecting an arbitrary valid event sequence twice yields equal state and never mutates its input, covering acceptance criteria 8.2
  - Assert fractional rectangle conversion always lands within the unit interval for arbitrary in-page rectangles and page sizes, covering acceptance criteria 2.1
  - Assert merging arbitrary well-formed inputs never raises and emits each pinned-schema field exactly once, covering acceptance criteria 11.6 and 11.8
  - Observable completion: the property suite runs under the project's existing property-test configuration and passes without shrinking to a failure
  - _Requirements: 2.1, 8.2, 11.6, 11.8_
  - _Depends: 2.3, 3.2, 6.1_
