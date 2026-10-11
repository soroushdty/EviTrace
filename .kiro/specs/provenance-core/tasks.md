# Implementation Plan — provenance-core

- [ ] 1. Foundation: package scaffold, dependency rules, configuration and output paths

- [x] 1.1 Create the `src/provenance/` package skeleton and declare its dependency direction
  - Create the package directory with an `__init__.py` placeholder re-export surface and the `adapters/` subpackage, plus the mirrored `tests/src/provenance/` directory.
  - Add eight forbidden-import pairs to the AST dependency-direction rule table: `provenance` must not import `agents`, `pipeline`, `pdf_extractor`, or `quality_control`; and `quality_control`, `agents`, `pdf_extractor`, and `text_processing` must not import `provenance`.
  - Add one named test per new pair alongside the existing per-pair tests so the exhaustive test and the named tests stay consistent.
  - Observable: the fast test suite passes with the eight new dependency-direction tests collected and green, and `import provenance` succeeds from the repo root.
  - _Requirements: 1.5_
  - _Boundary: ProvenanceModels_

- [x] 1.2 Register the provenance configuration block and run-scoped output directory
  - Add a `provenance` defaults mapping covering the enable flag, output subdirectory, severity overrides, fail-on-severity policy, and strict stage-contract flag.
  - Register `provenance` as a known top-level configuration key so configuration loading does not reject it, and add a loader that returns the merged provenance settings from a passed-in config mapping.
  - Add the run-scoped provenance output directory constant using the existing run-output-path helper, and add the `provenance` block to the shipped configuration file.
  - Observable: loading the shipped configuration returns provenance settings with documented defaults and raises no unknown-key error; the resolved output directory sits under the current run folder.
  - _Requirements: 1.3, 8.5_

- [ ] 2. Definition layer: vocabularies, records, identity, and stage contracts

- [x] 2.1 Implement the privacy label and disclosure decision carrier
  - Define the single carrier record with privacy state, opaque label, opaque decision, supplying component, supply timestamp, and rejection reason, plus a shared unknown-state constant.
  - Implement attachment that stores supplied values verbatim, returns the unknown constant when nothing is supplied, and never infers or defaults a label.
  - Implement structural-only conformance checking that rejects malformed input with a reason and leaves the label unset, performing no interpretation of label or decision values.
  - Observable: unit tests show verbatim storage, unknown state on absent input, rejected state with a reason on malformed input, and that the module contains no comparison against any label value.
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - _Boundary: PrivacyCarrierModule_

- [x] 2.2 Implement the provenance record models and closed vocabularies
  - Define the closed vocabularies for node kind, edge kind, evidence kind, anchor precision, derivation kind, determinism, severity, completeness state, claim support, and claim origin.
  - Define the frozen record types for source anchor, base node, source node, evidence node, claim node, derivation step, validation record, edge, and run identity, each embedding the privacy carrier.
  - Define every cross-layer type, that is, every type consumed by a layer other than the one that produces it: validation finding, the default severity table, missing segment, stage contract, artifact completeness, the immutable recorded-run snapshot, the chain-validation report, and the completeness report. The two reports belong here because the graph container carries them while sitting earlier in the dependency order than the modules that compute them.
  - Enforce the namespaced extension-field key convention at construction so extension metadata is preserved but visibly distinct from core fields.
  - Observable: unit tests construct one instance of every record type and every cross-layer type, confirm immutability and value equality, confirm a non-namespaced extension key is rejected, enumerate every vocabulary member, and confirm the default severity table has an entry for every finding code named in the design.
  - _Requirements: 1.1, 3.2, 4.1, 4.3, 5.1, 6.2, 7.1, 7.2, 9.1, 10.1, 10.4_
  - _Depends: 2.1_
  - _Boundary: ProvenanceModels_

- [x] 2.3 Implement identifier derivation and parsing
  - Implement derivation of source identifiers from a supplied document fingerprint, run identifiers from the supplied identity fields, claim identifiers scoped by source and field index with a revision suffix, and validation identifiers.
  - Implement evidence identifier formatting that scopes the pipeline-assigned local identifier by source without altering it, plus total round-trip parsing back to source and local identifier.
  - Implement content-addressed derivation-step identifiers over stage, kind, and sorted input and output identifiers, and raise a dedicated invalid-identifier error for malformed input.
  - Compute no document, configuration, or extraction-map fingerprint anywhere in this module; those values are consumed from the caller.
  - Observable: unit tests confirm round-trip totality, rejection of a local identifier containing the scope separator, and that derivation-step identifiers are invariant under permutation of inputs and outputs.
  - _Requirements: 3.1, 3.3, 3.6, 4.2, 4.7, 4.8, 6.2_
  - _Depends: 2.2_
  - _Boundary: IdentityService_

- [x] 2.4 (P) Implement the stage-contract declaration table and stage ordering
  - Declare, as data, the record kinds each pipeline stage must emit and the artifact scope each stage covers.
  - Declare the deterministic stage ordering used later by the event-log projection.
  - Document, alongside the table, that downstream specs introducing new pipeline stages extend both the contract table and the ordering in place and continue to route records through the recorder, rather than bypassing the recorder or suppressing the unknown-stage error; keep the vocabulary closed and the drift test in force.
  - Observable: unit tests confirm every declared stage has a non-empty required-record-kind set and appears exactly once in the ordering, and that a stage name absent from both tables is rejected at record time rather than silently accepted.
  - _Requirements: 2.1, 2.3, 7.6_
  - _Depends: 2.2_
  - _Boundary: StageContracts_

- [ ] 3. Emission layer: append-only recorder and pipeline adapters

- [x] 3.1 Implement the append-only provenance recorder
  - Provide record methods for source, evidence, claim, derivation, and validation records, plus issue recording and per-stage complete and failed marks; expose no update or delete method.
  - Make duplicate recording of an identical record a no-op and duplicate recording of a conflicting record produce a duplicate-identity finding while keeping the first record.
  - Expose an issue-sink callable suitable for the chunk validator, and an immutable snapshot that returns equal values on repeated calls.
  - Support a disabled mode whose methods no-op while still returning derived identifiers, and reject recording against an undeclared stage.
  - Guard all mutation with a single lock so concurrent per-document recording cannot interleave, and copy under that same lock when snapshotting.
  - Observable: unit tests confirm append-only behavior, idempotent duplicate handling, conflicting-duplicate findings, snapshot stability, disabled-mode no-op, and that the recorder performs no file or network access; a concurrency test recording from multiple threads confirms every record survives and the snapshot count matches the number of recorded records.
  - _Requirements: 1.2, 1.6, 2.1, 5.4_
  - _Depends: 2.3, 2.4_
  - _Boundary: ProvenanceRecorder_

- [x] 3.2 (P) Implement the source and run identity adapter
  - Convert a checkpoint-identity mapping into a source node carrying the document fingerprint, source format, source label, configuration fingerprint, schema fingerprint, and model identifiers.
  - Build the run identity from the per-document identity mappings, computing no fingerprints of its own.
  - Produce a valid source identity from content fingerprints alone when descriptive metadata is absent, and copy source metadata without evaluating disclosure.
  - Land tests in the identity-adapter test module only; the sole file shared with the sibling adapter tasks is the adapters re-export module, to which this task appends exactly one line.
  - Observable: unit tests build a source node and run identity from a literal identity mapping, confirm a metadata-free mapping still yields a source identity, and confirm the module performs no hashing of document or configuration content.
  - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7_
  - _Depends: 2.3_
  - _Boundary: Adapters/identity_adapter_

- [x] 3.3 (P) Implement the evidence adapter
  - Convert evidence-index item mappings into evidence nodes carrying evidence kind, source reference, rank score, and producing backend, adopting the item identifier as the local identifier unchanged.
  - Derive anchor precision as exact when page and coordinates are both present, approximate when coordinates are absent but page or section path is present or the structural path is the weak fallback form, and absent otherwise, enumerating the absent anchor kinds in the latter case.
  - Copy unrecognized item keys into parser metadata rather than discarding them, and return a node with absent precision rather than raising on a malformed item.
  - Land tests in the evidence-adapter test module only; append exactly one line to the shared adapters re-export module.
  - Observable: unit tests over literal item mappings covering the exact, approximate, and absent cases produce the documented precision and missing-anchor lists, and an item with unknown and missing keys converts without raising.
  - _Requirements: 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 10.2_
  - _Depends: 2.3_
  - _Boundary: Adapters/evidence_adapter_

- [x] 3.4 (P) Implement the claim adapter
  - Convert reconstructed field mappings into claim nodes with field index, field name, value digest, confidence, and citing edges to each referenced evidence node.
  - Classify claim support as unsupported when no evidence reference is present, missing-anchor when referenced evidence exists but carries no usable anchor, and supported otherwise, keeping the two failure states distinct.
  - Emit findings for unknown, duplicated, and ambiguous references while retaining the claim, and set a non-model origin for fields produced without a model call.
  - Emit revision edges linking a synthesized or merged claim to each contributing claim.
  - Land tests in the claim-adapter test module only; append exactly one line to the shared adapters re-export module.
  - Observable: unit tests confirm all three support classifications, that a claim with an unknown reference is retained alongside a finding, that a prefilled field carries the non-model origin, and that a merged claim links to every contributor.
  - _Requirements: 5.1, 5.2, 5.5, 5.6, 5.7_
  - _Depends: 2.3_
  - _Boundary: Adapters/claim_adapter_

- [x] 3.5 (P) Implement the derivation adapter
  - Convert the reconciliation provenance mapping and per-stage events into derivation steps carrying stage, derivation kind, inputs, and outputs.
  - Mark model-performed steps probabilistic with the model identifier in effect and rule-based steps deterministic.
  - Represent many-to-one contribution as a single step with multiple inputs, represent one-to-many contribution without duplicating the input artifact, and record removal reason and removed identifiers for discard steps.
  - Make the stage-event entry point's contract unambiguous for downstream callers: the derivation kind is a required argument drawn from the closed derivation-kind vocabulary with no inference from the stage name, the determinism marking is the closed determinism literal and never a boolean, and any field the derivation-step record does not declare must be supplied through the namespaced extension mapping under the `x-<namespace>-<field>` rule.
  - Land tests in the derivation-adapter test module only; append exactly one line to the shared adapters re-export module.
  - Observable: unit tests confirm the determinism marking on both paths, that a merge yields one step with all inputs, that fan-out reuses a single input identifier across steps, that a removal step carries a non-empty reason, that an out-of-vocabulary derivation kind and a boolean determinism argument are both rejected, and that an undeclared field round-trips only when namespaced.
  - _Requirements: 6.1, 6.3, 6.4, 6.5, 6.6, 6.7_
  - _Depends: 2.3_
  - _Boundary: Adapters/derivation_adapter_

- [ ] 4. Assembly and analysis: graph, validation, completeness, projection

- [x] 4.1 Implement the typed graph builder with partial preservation
  - Assemble nodes and edges from a recorder snapshot into a graph carrying schema identity, schema version, and run identity, with every node and edge bearing an explicit kind.
  - Guard each record's construction individually so a record that cannot be materialized becomes a missing-segment entry naming stage, record kind, reason, and affected identifiers while the rest of the graph is still produced.
  - Provide lookup by node kind, outgoing-edge lookup, and a claim-to-evidence-to-source traversal, and never raise to the caller.
  - Define the graph container with fields for the attached validation and completeness reports, so the serialized artifact is a pure function of the graph alone.
  - Implement attachment of validation results, returning a new graph carrying the validation report plus validation records and their edges without mutating the original, and implement the matching completeness attachment; this module, not serialization, owns both.
  - Both attachments are total, non-mutating, and idempotent: attaching the same report twice yields an equal graph.
  - Observable: an integration test builds a graph from a snapshot containing one unmaterializable record and confirms the remaining nodes are present, exactly one missing segment with a non-empty reason exists, and the claim traversal returns the full chain; a further test confirms attaching validation and completeness yields a new graph carrying both reports, adds validation records, and leaves the original unchanged.
  - _Requirements: 1.4, 1.6, 7.1, 7.2, 7.4, 7.5, 8.7_
  - _Depends: 3.1, 3.2, 3.3, 3.4, 3.5_
  - _Boundary: GraphBuilder_

- [x] 4.2 Implement the reference and structural chain-validation checks
  - Implement the checks for dangling references, unrepresented sources, evidence outside the run context, missing anchors, invalid identifiers, and non-conforming privacy carriers, plus duplicate, ambiguous, and unsupported-claim findings.
  - Construct and populate an instance of the models-owned chain-validation report container; declare no dataclass in this module. When a check cannot run, report the run as not completed with a recorded reason rather than as valid.
  - Not parallel with the graph builder: this task consumes the graph type produced by 4.1 and writes the same validation module that 4.3 extends.
  - Observable: one unit test per finding code in this group builds the minimal graph that triggers it and asserts the code is emitted; a further test confirms the not-completed path reports a reason and does not report the run valid.
  - _Requirements: 5.3, 8.1, 8.2, 8.3, 8.4, 8.6, 9.6_
  - _Depends: 4.1_
  - _Boundary: ChainValidator_

- [x] 4.3 Implement the topology checks and severity classification policy
  - Implement iterative cycle detection over derivation and revision edges, orphan-node detection, and inconsistent-derivation detection, avoiding recursion so a pathological chain cannot exhaust the stack.
  - Apply the default severity table to every finding, apply per-code overrides supplied from configuration, and order findings deterministically by severity rank, code, and node identifier.
  - Not parallel with 4.2: both tasks write the same validation module.
  - Observable: unit tests confirm a cyclic graph yields the cycle finding, each finding code carries its documented default severity, a supplied override changes exactly that code's severity, and repeated validation of one graph returns findings in identical order.
  - _Requirements: 8.4, 8.5_
  - _Depends: 4.2_
  - _Boundary: ChainValidator_

- [x] 4.4 (P) Implement the ordered event-log projection
  - Derive an ordered sequence of events from the graph using the declared stage ordering, node-kind rank, and node identifier as a total ordering key, with no clock and no randomness.
  - Persist nothing and read nothing; every event field must be derivable from the graph alone.
  - Observable: unit tests confirm two projections of the same graph are equal, no two events share a sequence number, and the module performs no file access.
  - _Requirements: 7.6_
  - _Depends: 4.1_
  - _Boundary: Projection_

- [x] 4.5 Implement provenance completeness computation
  - Diff the stage-contract declarations against recorded stage status and graph content to detect stages that did not emit their declared record kinds.
  - Honor the strict stage-contract setting: when strict, a recorded stage absent from the contract table yields an undetermined state; when not strict, it is ignored.
  - Produce completeness states at run, document, and claim level, each carrying the missing record kinds and the responsible stages.
  - Classify an anchorless-evidence claim as incomplete for the missing-anchor reason and a reference-free claim as incomplete for the unsupported reason, never merging the two, and classify artifacts covered by a failed stage or a missing segment as undetermined with the recorded reason.
  - Return data only, producing no formatted report, no file, and no rendered output; the caller attaches the returned report to the graph using the graph builder's attachment function.
  - Observable: unit tests confirm the three-level states, the distinct missing-anchor and unsupported reasons, undetermined propagation from a failed stage, both strict-flag behaviors, and that no file is written.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - _Depends: 4.1, 4.3_
  - _Boundary: CompletenessComputer_

- [x] 4.6 Scope stage status to individual documents
  - Add an optional source-identifier keyword to the stage complete and failed marks: without it the mark stays run-wide (used for chain validation and for failures before any document is known); with it the mark applies to that document only and leaves the run-wide status unchanged. Reject a malformed source identifier.
  - Extend the recorded-run snapshot with per-document stage status and per-document failure reasons, both defaulting to empty; keep per-document marks escalate-only and keep every distinct failure reason per document and stage, in recording order. The snapshot is never serialized, so the schema version does not change.
  - Make the graph builder emit one stage missing segment per failed stage and document, naming the document and that document's steps of the stage; keep the single segment for a run-wide failure.
  - Make completeness evaluate each document and stage on the worse of the run-wide and per-document status, so a per-document failure leaves only that document and the claims its segment covers undetermined, and report a run with no recorded documents as undetermined.
  - Observable: tests show a failure marked for one of two documents leaves the other document determinable and the failed one undetermined with its own reason, one missing segment per failed document, run-wide marks behaving exactly as before, every per-document reason kept, and an empty run reported undetermined.
  - _Requirements: 2.1, 2.2, 2.5, 7.4_
  - _Depends: 4.5_
  - _Boundary: ProvenanceRecorder, ProvenanceModels, GraphBuilder, CompletenessComputer_

- [x] 4.7 Tighten evidence anchors and content, and keep checkpoint metadata, in the adapters
  - Classify anchor precision by the single design rule: exact needs page and coordinates; approximate needs at least one real locator (page, character span, or a structural path addressing one element by `xml:id`); a section path, a weak tag-only structural path, or coordinates without a page are recorded but leave the anchor absent; list every missing anchor kind.
  - Keep text, annotations, and the source PDF path out of parser metadata as a declared deny-list; when text is present add its digest and character count, computing the digest through a new public text-digest helper in the identity module so hashing stays there.
  - Keep every unconsumed checkpoint-identity key on the source node as an `x-manifest-<key>` extension, with one fallback extension for keys outside the extension-key charset; accept additional model identifiers when building the run identity so a distinct synthesis model is part of it.
  - Update the completeness and validation tests whose fixtures relied on a section path alone counting as approximate.
  - Observable: adapter tests show section-path-only and weak-path-only items classified absent with their missing kinds listed, a page alone or an `xml:id` path alone classified approximate, no evidence node's parser metadata containing text, annotations, or the source PDF path while it carries the text digest and length, a source node carrying the output-path and schema-version extensions, and a run identity whose model identifiers and run identifier change with an additional synthesis model.
  - _Requirements: 1.3, 2.4, 3.2, 3.5, 4.4, 4.5, 10.2_
  - _Depends: 4.6_
  - _Boundary: Adapters/evidence_adapter, Adapters/identity_adapter, IdentityService_

- [x] 4.8 Apply the duplicate-identity severity and the prefill completeness policy
  - Raise the default severity of the duplicate-identity finding to error.
  - Add a dedicated, overridable prefill-unsupported finding code with default severity warning, emitted instead of the unsupported-claim finding for an unsupported claim of prefill origin.
  - In completeness aggregation, report a prefill-origin claim that is incomplete — unsupported, or missing an anchor because it cites a header metadata node without coordinates — as incomplete with its reason at claim level, but do not let it alone make its document or the run incomplete; an undetermined prefill claim and every model-origin claim follow the normal rule.
  - Observable: tests show the duplicate-identity default is error, an unsupported prefill claim yields one prefill-unsupported warning and no unsupported-claim finding, an override changes that code's severity alone, and a document whose only gaps are an unsupported prefill claim and a missing-anchor prefill claim is complete while both claims are reported incomplete.
  - _Requirements: 2.2, 5.2, 5.7, 8.5_
  - _Depends: 4.7_
  - _Boundary: ProvenanceModels, ChainValidator, CompletenessComputer_

- [x] 4.9 Close the run-wide failure gap and give attribution a single owner
  - Make a run-wide stage failure always make the run undetermined with the recorded reason, including for a stage that recorded no derivation steps, while per-document failures and run-wide behaviour for documents stay as they are.
  - Move document attribution (the source a node identifier belongs to, and the documents a derivation step touches) and stage attribution (own stage; else earliest declared stage among non-in-place producing steps; else the sole contract stage for the node's kind; else unattributed) into public functions owned by the graph module, and make the graph builder, the event-log projection and the completeness computation call them instead of keeping their own copies.
  - Add a guard test that fails if the projection or completeness modules re-implement either rule, and behavioural tests showing the three consumers agree on the document and stage of the same records, including in-place steps and records produced by several stages.
  - Observable: tests show a run-wide failure of a stage with no derivation steps makes the run undetermined; a missing segment, its event-log event and its completeness entry name the same stage for a record produced by an extraction-chunk step and a synthesis step and for an in-place step's input; and the guard test fails when a private copy of either rule is added to the projection or completeness module.
  - _Requirements: 2.1, 2.3, 2.5, 7.4, 7.6_
  - _Depends: 4.8_
  - _Boundary: GraphBuilder, Projection, CompletenessComputer, ChainValidator_

- [ ] 5. Persistence: schema-versioned artifact serialization

- [x] 5.1 Implement graph serialization with the schema version guard
  - Implement conversion of the graph to and from a plain mapping carrying schema identity, schema version, run identity, nodes, edges, missing segments, and the validation and completeness reports the graph already carries; the write path takes the graph as its only data argument.
  - Preserve namespaced extension fields unchanged on both directions, and preserve unknown keys of a compatible major version into extension fields rather than dropping them.
  - Accept artifacts whose major schema version matches, and raise a dedicated unrecognized-version error naming the version found and the versions supported for any other major, materializing nothing in that case.
  - Serialize whatever validation records the graph already carries so a new validation method needs no core type change; attaching those records belongs to the graph builder, not here.
  - Observable: unit tests confirm a full round trip preserves the graph and its extension fields, a compatible-minor artifact with unknown keys loads with those keys preserved, and an incompatible-major artifact raises the unrecognized-version error.
  - _Requirements: 7.3, 7.5, 10.3, 10.4, 10.5, 10.6_
  - _Depends: 4.5_
  - _Boundary: Serialization_

- [x] 5.2 Implement the run artifact write and read path
  - Write exactly one graph artifact per run to the run-scoped provenance directory using a temporary file followed by an atomic replace, mirroring the existing checkpoint write pattern.
  - Provide the matching read path, and define no interoperable or external export format.
  - Observable: an integration test writes a graph, reads it back to an equal graph, and confirms rewriting an identical graph produces a byte-identical file.
  - _Requirements: 1.3, 7.7_
  - _Depends: 5.1_
  - _Boundary: Serialization_

- [ ] 6. Pipeline integration

- [x] 6.1 Add the optional issue sink to chunk-output validation and field reconstruction
  - Add an optional issue-sink parameter to per-item validation, chunk-output validation, and field reconstruction.
  - When a sink is supplied, record unknown, duplicated, and ambiguous evidence references as findings and retain the affected item instead of raising or silently dropping it.
  - When no sink is supplied, preserve the existing raise and silent-drop behavior exactly.
  - Observable: tests confirm that with a sink the claim survives and findings are recorded for each reference problem, and that without a sink the existing tests for raise and silent-drop behavior still pass unchanged.
  - _Requirements: 5.3, 5.4_
  - _Depends: 3.1_
  - _Boundary: PipelineIntegration_

- [x] 6.2 Emit evidence nodes from the evidence index on both cache paths
  - Add an optional recorder parameter to evidence bundle construction and emit evidence nodes through the single-item evidence adapter, turning each item without a usable identifier into a recorded invalid-identifier issue instead of a silent skip.
  - Emit on the cache-hit path as well as the cache-miss path so a cached run is not reported provenance-incomplete.
  - Record the title item (the metadata-section item) as metadata evidence by overriding its kind on the pipeline side, leaving the evidence-index item format, the evidence cache, and the prompt unchanged.
  - Add the pure header-metadata helper that returns the first-author value and the matched publication date together with their header elements; make the author prefill and the header branch of year resolution read their values from it; and from the bundle's TEI mint one metadata evidence item per found element under the reserved local identifiers, recorded only in provenance and never added to the bundle's items, the evidence cache, or any prompt.
  - Mark the evidence-index stage per document.
  - Observable: an integration test runs bundle construction twice against the same cached bundle and confirms both runs record the same evidence node identifiers, including the title as metadata evidence and the header author and date nodes; an item with a malformed identifier yields a recorded invalid-identifier issue; and the prefilled values and the shared prompt prefix bytes are unchanged.
  - _Requirements: 4.1, 4.7, 8.4_
  - _Depends: 6.1, 4.8_
  - _Boundary: PipelineIntegration_

- [ ] 6.3 Emit the source node and reconciliation-derived derivation steps from the extraction pipeline
  - Compute each document's checkpoint identity on the pipeline side (no production call site computes it today), treat the manifest's missing-hash sentinels as missing fingerprints, and emit the source node through the identity adapter so unconsumed keys, including the run-relative output path, land as manifest extensions.
  - Build the run identity once per run from the documents actually processed: exclude documents the manifest already marks complete (resumed), and pass the synthesis model as an additional model identifier when it differs from the chunk model.
  - Record a source shared by two same-content PDFs once and log a warning naming both, leaving any conflicting later record to surface as a duplicate-identity finding.
  - Convert the reconciled record's provenance mapping into derivation steps on the pipeline side, leaving the quality-control package unmodified, and mark the document-extraction and quality-control stages per document.
  - Observable: an integration test over a fabricated bundle confirms one source node per processed document carrying the output-path extension, no source node or fingerprint for a resumed document, a run identity listing the synthesis model when it differs, one source and a warning for two same-content PDFs, and at least one reconciliation derivation step; the dependency-direction suite still passes.
  - _Requirements: 1.1, 1.3, 3.1, 3.5, 6.1_
  - _Depends: 6.2_
  - _Boundary: PipelineIntegration_

- [ ] 6.4 Emit claim nodes and extraction derivation steps from the per-PDF processor
  - Emit claim nodes for merged extracted fields with their citing edges, passing the anchors of the same evidence nodes used as the known evidence set, and emit per-chunk and synthesis derivation steps marked probabilistic with the model identifier in effect, using only node identifiers as step inputs and outputs.
  - Populate the revision and contributing-claim inputs for synthesized or merged claims so revision links are produced on the real path.
  - Emit locally prefilled fields with the non-model origin under a prefill derivation step, each citing the header metadata node its value came from (the first-author node for a header author, the publication-date node for a header year) and citing nothing when the value came from a fallback; record discard steps with their removal reason where content is dropped.
  - Mark each claim-scoped stage complete or failed per document, with one producing step per document for every stage marked complete.
  - Pass the recorder's issue sink into every production call to chunk-output validation and field reconstruction on both the chunk path and the synthesis path, wrapped per document so each issue names its claim identifier and carries the raw reference, so reference issues are recorded on the real call path and not only in tests.
  - Perform all emission after prompt assembly and after response parsing so no provenance value can reach the shared prompt prefix.
  - Observable: an integration test over a fabricated chunk result set confirms one claim node per merged field, probabilistic derivation steps carrying the model identifier, prefilled fields carrying the non-model origin and citing a header node only for header-sourced values, a revision link from a synthesized claim to each contributor, a failure in one document leaving the others determinable, and that a chunk citing an unknown evidence identifier produces a recorded finding naming the claim while the field survives into the merged output.
  - _Requirements: 2.5, 5.1, 5.4, 5.5, 5.7, 6.1, 6.7_
  - _Depends: 6.3_
  - _Boundary: PipelineIntegration_

- [ ] 6.5 Wire the recorder lifecycle into the orchestrator
  - Construct one recorder per run from the resolved configuration, passing the enable flag as a real boolean, and pass it explicitly down the call chain, introducing no module-level state.
  - After processing completes, build the graph, run chain validation with the configured severity overrides and the recorded findings, and attach the validation report and records to the graph; then mark the chain-validation stage run-wide as complete, or failed with the reason when validation did not complete, take a post-validation snapshot, compute completeness from it with the strict stage-contract setting validated as a boolean, and attach the completeness report to the graph.
  - Resolve the artifact directory from the configured output subdirectory under the run output folder, rejecting an empty, absolute, or parent-traversing value with a warning and falling back to the default, and write the run artifact there; the write takes the graph as its only data argument.
  - Catch every provenance exception (unknown stage, invalid identifier, serialization, or any other), log it, and degrade, because provenance never aborts extraction.
  - Log one run-level summary line covering node counts, findings by severity, and the run completeness state, and honor the disabled setting by skipping artifact production without changing extraction behavior.
  - Honor the fail-on-severity setting: when configured and the findings reach that severity or worse, log at error level and record a provenance failure in the run summary, while still writing the extraction output because provenance never aborts extraction.
  - Observable: a run over fabricated inputs produces a provenance artifact in the configured run output subdirectory containing validation records, a complete chain-validation stage, and the computed completeness states; an invalid output subdirectory falls back to the default with a warning; an injected provenance error is logged without aborting extraction; and the same run with provenance disabled produces no artifact and no behavior change.
  - _Requirements: 1.3, 1.4, 2.2, 8.5, 8.6, 8.7_
  - _Depends: 6.4_
  - _Boundary: PipelineIntegration_

- [x] 6.6 (P) Give caption sentences the enclosing figure's location
  - In the evidence index, give sentence items emitted from inside a figure caption the page and coordinates of the enclosing figure when neither the sentence nor its paragraph carries coordinates.
  - Keep item identifiers stable: add, remove, or renumber no item. Existing evidence caches keep the old shape until regenerated.
  - Treat this as a declared prompt-content change: page and coordinates are copied into the paper evidence package embedded in the shared prompt prefix, so the affected items' bytes change (the prefix stays byte-stable across the chunks of one paper).
  - Parallel caveat: 6.2 edits the same evidence-index module, so run 6.6 before 6.2 starts or after it lands, never concurrently.
  - Observable: on the three TEI fixtures every caption sentence item carries a page and coordinates and the item identifier sequence is unchanged, and the shared prompt prefix of one paper is still byte-identical across its chunks.
  - _Requirements: 4.3, 4.4, 4.5_
  - _Boundary: PipelineIntegration (src/pipeline/evidence_index.py only)_

- [ ] 7. Validation: boundary, property, regression, and end-to-end coverage

- [ ] 7.1 (P) Add boundary and isolation tests for the provenance package
  - Assert the package imports cleanly with the pipeline and quality-control modules absent from the module cache and without any network access.
  - Assert no module under the package computes a document, configuration, or extraction-map fingerprint outside the identity module, by scanning imports and hashing calls.
  - Assert the privacy carrier module contains no comparison against any privacy label value, and that no consumer defines a second evidence identifier construction.
  - Ordering caveat for this whole group: 7.1, 7.2, and 7.3 run in parallel with each other, each in its own test module, but all three run only after 6.5 completes.
  - Observable: the boundary test module passes and fails loudly when a hashing call is added outside the identity module.
  - _Requirements: 1.5, 3.6, 4.8, 9.4_
  - _Depends: 6.5_
  - _Boundary: test_provenance_import_isolation (read-only over ProvenanceModels, PrivacyCarrierModule, IdentityService)_

- [ ] 7.2 (P) Add property-based tests for identity, graph assembly, and projection
  - Assert evidence identifier round-trip totality over arbitrary valid source and local identifier pairs.
  - Assert graph assembly never raises for arbitrary well-typed record sets and that every unmaterialized record appears in exactly one missing segment.
  - Assert the event-log projection ordering is a total order with no shared sequence numbers.
  - Observable: the property test module passes at the configured example count and is collected in the fast suite.
  - _Requirements: 3.3, 4.2, 7.4, 7.6_
  - _Depends: 6.5_
  - _Boundary: provenance property-test modules (read-only over IdentityService, GraphBuilder, Projection)_

- [ ] 7.3 (P) Add regression guards for prompt-cache stability and stage-contract drift
  - Assert the shared paper prompt prefix output is byte-identical before and after provenance integration for the same inputs.
  - Assert every stage name the recorder accepts is declared in both the stage-contract table and the stage ordering.
  - Observable: both guards pass, and the contract-drift guard fails when a stage is recorded without being declared.
  - _Requirements: 2.3, 7.6_
  - _Depends: 6.5_
  - _Boundary: provenance regression-test modules (read-only over StageContracts, PipelineIntegration)_

- [ ] 7.4 Add the end-to-end provenance integration test
  - Exercise the full path from recorder through graph assembly, validation, completeness, and artifact write over a fabricated multi-document run using synthetic identifiers and text only.
  - Cover the degraded paths: a stage marked failed still yields a graph containing every other stage's nodes with a missing segment and an undetermined completeness state; and with no privacy component present every node carries the unknown carrier while the graph still assembles and validates.
  - Assert the claim-to-evidence-to-source traversal resolves for a supported claim in the written artifact.
  - Observable: the end-to-end test passes in the fast suite without GROBID, a model provider key, or network access.
  - _Requirements: 1.4, 1.6, 2.5, 9.7_
  - _Depends: 7.1, 7.2, 7.3_
  - _Boundary: provenance end-to-end test module (read-only over PipelineIntegration, GraphBuilder, CompletenessComputer)_

## Implementation Notes

- 1.1: design.md "Testing Strategy → Boundary and Regression Tests" item 1 says agents/pipeline/pdf_extractor/quality_control must not import provenance; that contradicts "Allowed Dependencies" (pipeline is the sole integrator) and omits text_processing. Implemented per task text + Allowed Dependencies: the reverse rules cover quality_control, agents, pdf_extractor, text_processing; pipeline may import provenance. Design wording left for human correction.
- 1.1: the 8 provenance dependency tests call `_assert_source_package_exists` so a missing/renamed package fails instead of passing vacuously. `tests/src/provenance/.gitkeep` can be removed once a test module lands there.
- 1.2: design names both `PROVENANCE_DIR = resolve_run_output_path("provenance")` (hard-coded) and `provenance.output_subdir` (read by Serialization) without saying which wins. 5.2 should resolve the artifact dir via `resolve_run_output_path(cfg["output_subdir"])` (PROVENANCE_DIR = default only) so the key is not dead; 6.5 must pass the merged provenance settings down. `load_provenance_config` does no value validation (per design): consumers should tolerate `severity_overrides: null`.
- 2.1: carrier input shape is pinned to exactly {`label` (required str), `decision` (optional str/None)}; extra keys and decision-without-label are rejected (fails closed; compatible with privacy-core `carrier_input_from`). Stored values keep object identity. `PrivacyCarrier` has no `__post_init__` so deserialization can construct it directly.
- 2.2 (NEEDS HUMAN CONFIRMATION): `duplicate_identity` is named by the recorder design but absent from the ChainValidator default-severity table; `DEFAULT_SEVERITIES` sets it to `warning` (same as `duplicate_reference`). Confirm or change, and fix the design table.
- 2.2: extension keys must full-match `x-[a-z][a-z0-9]*-[A-Za-z0-9_][A-Za-z0-9_-]*` (`is_extension_key`); all downstream `x-multiagent-*` keys pass. 5.1's "preserve unknown keys into extensions" needs a fallback for JSON keys outside that charset.
- 2.2: mapping fields are frozen recursively (`_FrozenDict`, read-only list) for hashability; JSON returns nested tuples as lists, so 5.1 must re-tuple typed tuple fields for round-trip equality. Vocabulary membership, node kind/class agreement and non-empty node_id are NOT checked at construction — adapters (3.x), GraphBuilder (4.1) and readers (5.1) must do it.
- 2.3: valid source ids are `src:` + non-empty fingerprint with no `#` and no whitespace; format/parse/derive_claim_id enforce it (7.2's round-trip strategy must generate such ids). `derive_run_id` takes `Mapping[str, str]` only — the 3.2 identity adapter must stringify `model_ids` before digesting.
- 2.3 (follow-up): evidence-routing and multiagent-extraction designs call `provenance.format_evidence_id`, but `__init__.py` re-exports only models (design re-export list: models, recorder, graph, reports). Either re-export identity when the surface is finalized or downstream specs must use `provenance.identity.format_evidence_id`.
- 2.4 (NEEDS HUMAN CONFIRMATION): no provenance-core document names any stage, so `contracts.py` declares 7 chosen stages in order: document_extraction(source)/document, quality_control(derivation)/document, evidence_index(evidence)/document, metadata_prefill(claim,derivation)/claim, extraction_chunk(claim,derivation)/claim, synthesis(claim,derivation)/claim, chain_validation(validation)/run. Five match cost-and-run-reporting `KNOWN_STAGE_ORDER` and pdf_processor labels; `metadata_prefill` and `chain_validation` have no spec source. `UnknownStageError`/`require_declared_stage` live in contracts.py — 3.1 must call it (only DerivationStep carries `stage`).
- 2.4 → 4.5: ClaimNode has no `stage`; tie each claim to the stage whose DerivationStep.output_ids contain it (do not require every claim-scoped stage for every claim). Check only stages whose status is `complete`; skip `not_run` (num_chunks=1 yields no extraction_chunk).
- 2.4 → 6.5/4.5: validation records are attached after the snapshot, so `chain_validation` reads `not_run` unless 6.5 marks it complete and passes a post-validation snapshot, or 4.5 checks graph content. Never treat `not_run` as missing.
- 2.4 → 6.4: `metadata_prefill` commits 6.4 to emit a `prefill` DerivationStep (stage="metadata_prefill", outputs = prefilled claim ids) and mark the stage complete.
- 2.4 → 3.4/4.5 (design gap): prefilled fields 1–2 always have `location=[]`, so they classify `unsupported` and would make every document incomplete (document state = worst claim). 3.4/4.5 must decide how prefill-origin claims count — flag to human if the design gives no answer.
- 3.1 → 6.1/6.4: issue sink is `(code, message, details)`; the claim is named only via `details["node_id"]`. The validator has no source_id, so 6.4 must wrap the sink per document and inject `node_id = derive_claim_id(source_id, field_index)` (else 5.4 "naming the claim" fails on the real path). Unknown codes raise ValueError even when disabled — 6.1 must pass only dangling_reference / duplicate_reference / ambiguous_reference, with a test.
- 3.1 → 4.5: stage status is run-wide and escalate-only (not_run < complete < failed); one document's failure marks the stage failed for the run. `RecordedRun` also carries `stage_failure_reasons`. 6.5 must catch `UnknownStageError` (raised in both modes) and pass a real bool for `enabled`.
- 3.1 → 7.1: `import provenance` pulls in `utils.logging_utils` → `utils.path_utils`, which reads configs/config.yaml and imports yaml at import time (allowed: design lists utils.logging_utils as a recorder dependency).
- 3.2 (NEEDS HUMAN CONFIRMATION): the identity adapter tolerates but drops `schema_version`, `output_path` and unknown ManifestIdentity keys (SourceNode has no passthrough field; extensions need an `x-<ns>-` key), whereas the shared adapter precondition says "preserved". run_id digests config/schema fingerprints + model ids only (not schema_version, document fingerprints or created_at), so same-config runs share a run_id; created_at distinguishes them.
- 3.2 → 6.3/6.5: manifest sentinels `"nohash"` / `"no_extraction_map"` are taken verbatim as fingerprints (all unreadable PDFs → `src:nohash`); 6.3 should treat them as missing. `run_identity_from_identities` raises InvalidIdentifierError on disagreeing run-wide fingerprints or empty input — 6.5 must catch and degrade, never abort extraction.
- 3.3 → 6.2: `evidence_nodes_from_items` skips unidentifiable items (logged: DEBUG per item + one WARNING per call) because its list return type cannot carry findings/segments. To honour the design's "invalid identifier → MissingSegment" row, 6.2 must call `evidence_node_from_item` per item and turn each InvalidIdentifierError into a recorded `invalid_identifier` issue via `recorder.record_issue`. Real item keys: `type`→evidence_kind (unknown → "sentence", logged), `xpath`→structural_path, `score`→rank_score, `id`→local_id; anchor.missing is filled for approximate nodes too (completeness keys on precision == "absent").
- 3.3 (NEEDS HUMAN CONFIRMATION): (a) design conflict — Adapters rule makes section-path-only items `approximate`, Error Handling row says `absent` + missing_anchor; implemented the Adapters rule, so on real data (921 items over 3 TEI fixtures: 745 exact, 176 approximate, 0 absent) the `missing_anchor` claim state never fires. (b) Req 4.1 `metadata` evidence kind is never produced: evidence_index.py emits the title as type "sentence" (section "Metadata"); fix belongs in the pipeline. (c) per the copy-all rule (10.2) every evidence node's parser_metadata carries full sentence `text`, `source_pdf` (absolute path) and `annotations` into the artifact — a privacy/size decision for privacy-core or 5.x.
- 3.4 (signature deviation): `claim_nodes_from_fields` takes a REQUIRED kw-only `evidence_anchors: Mapping[str, SourceAnchor]` beyond the design signature (the design's `known_evidence_ids` cannot express anchoring). Classification fails closed: a cited known id with no/invalid anchor entry is anchorless; all anchorless → missing_anchor; approximate counts as usable; supported if ANY cited node is usable (4.5 must use the same mixed-citation rule). A claim whose references all fail to resolve → `unsupported` + critical `dangling_reference` (diagram gap). Value digest = public `identity.derive_value_digest` (canonical JSON of ["claim-value", value], sort_keys). "Ambiguous" = case/whitespace-insensitive near match of a known id (6.1 should align). Design should be amended to list `evidence_anchors`.
- 3.4 → 6.4: pass `evidence_anchors={n.node_id: n.anchor}` built from the same evidence nodes as `known_evidence_ids` (full `<source_id>#<local>` ids). Populate input keys `revision` and `contributing_claim_ids` for synthesis/merged claims, or `revises` edges (Req 5.5) exist only in tests — 6.4's requirement list omits 5.5. Avoid double reporting: 6.1's sink already records unknown/duplicate `loc` ids that `reconstruct_fields` still passes through, and the adapter re-reports them with a different finding_id — dedupe or pick one source.
- 3.4 (NEEDS HUMAN DECISION before 4.5): prefilled fields 1–2 carry origin="prefill" but `location=[]`, so they classify `unsupported`; with "document state = worst claim", every document becomes incomplete. Design is silent. Options: exempt prefill-origin claims from support/completeness, have prefill cite the TEI metadata evidence, or accept.
- 3.5: `derivations_from_qc_provenance` emits ONE deterministic `reconcile` step (stage quality_control, inputs=(source_id,), no outputs — branches/unified record are not nodes; consistent per design invariant: inconsistent only if neither inputs nor outputs). Every QC mapping key is preserved as `x-qc-<key>` (fallback `x-qc-unnamespaced`). What the QC step outputs (Req 6.2) is an open point for 6.3. `derivation_from_stage_event` validates the stage itself (UnknownStageError), rejects deterministic+model_id, ids-without-reason and `remove` without ids; a reason with no removed ids is normalized to None (DEBUG log) so downstream emitters (evidence-routing, multiagent) do not raise.
- DECISION (user, 2026-10-08) for prefill: prefilled fields 1–2 must CITE the paper's title/metadata evidence node so they classify like any other claim (no completeness exemption). 6.4 owns this: build their `location` from the TEI metadata evidence id(s) (the title item, currently type "sentence" in section "Metadata") and pass the matching anchors; 4.5 applies the normal rule. Remaining "NEEDS HUMAN CONFIRMATION" notes are to be listed in the final report for review at the end (user choice).
- 4.1: a `failed` stage also yields a `record_kind="stage"` MissingSegment (affected_ids = that stage's derivation-step ids) — deviates from the design's "iff some record failed" postcondition but required by Integration Test 2 / 7.4 / Req 2.5. 4.5 must follow those steps' outputs to find covered claims; failed records with no producing step are attributed to stage `"unattributed"` (not a declared stage — 4.5 must handle it). The builder itself derives `belongs_to_source`, `has_input`, `has_output` edges from record fields (no adapter emits them); `anchored_in`/`derived_from` are never produced. Builder edges may point at non-node ids, so 6.4 must pass node ids only as derivation inputs/outputs. PROVENANCE_SCHEMA_ID/VERSION are defined in graph.py — 5.1 must import them from `provenance.graph`. Graph equality depends on edge-tuple order; 5.1 must preserve it.
- 4.1 → 4.2/4.3/6.5 (design extension, listed for end review): `RecordedRun.findings` (recorder `duplicate_identity`, issue-sink findings, claim-adapter dangling/ambiguous/duplicate references — not reconstructible from the graph) never reach the report or artifact under the design as written, breaking Req 5.4 in practice. Fix: 4.2 adds kw-only `recorded_findings: Sequence[ValidationFinding] = ()` to `validate_chain`, merging them with graph-derived findings (dedupe by finding_id and the issue-sink vs claim-adapter double report from 3.4), counted in `counts_by_severity`; 4.3 applies overrides/ordering to them too; 6.5 passes `recorded.findings`.
- 4.2: `validate_chain(graph, *, recorded_findings=())` — `severity_overrides` and severity-ranked ordering are NOT yet on the signature (4.3 must add the design's kw-only `severity_overrides`, apply it to recorded findings too, replace the interim order `(code, node_id, edge_id, finding_id)` with `(severity_rank, code, node_id)`, and tolerate/reject recorded codes outside DEFAULT_SEVERITIES). Merge key = (code, node_id, normalized `details["reference"]`, `reference_kind`); local refs are scoped to the claim's source so sink full-ids and adapter local-ids meet. 6.1/6.4 sink calls MUST pass `node_id=derive_claim_id(source_id, field_index)`, `details["reference"]` = the raw loc (local or `format_evidence_id`, never stripped/casefolded), `reference_kind` omitted or "evidence", and the same classification as the claim adapter (dangling / duplicate / ambiguous = case/whitespace near-match). `counts_by_severity` lists non-zero severities only; `foreign_source` citation → evidence_out_of_run_context.
- 4.3: `validate_chain(graph, *, severity_overrides=None, recorded_findings=())`; order `(severity_rank, code, node_id, edge_id, finding_id)`. Recorded known codes are re-stamped to their default; unknown codes keep their severity; bad override entries are ignored with a WARNING (never raise, `completed` stays True). `orphan_node` (info, design defines none): a node connected by no edge to another node — `validates` edges, self-loops and edges to non-node ids don't count; validation records exempt. `graph_cycle`: one finding per strongly connected component over derivation/revision dependencies; an id that is both input and output of ONE step is an in-place transform (filter/annotate over canonical ids), not a cycle — cross-step cycles are still reported. `inconsistent_derivation` only when a step has no usable inputs and no outputs.
- 4.4 → 4.5: `project_event_log` attributes a non-derivation node to the EARLIEST declared STAGE_ORDER stage among steps that output it, excluding in-place steps (node in both inputs and outputs); else the sole contract stage for its kind; else "unattributed". `graph._producing_stages` (missing-segment attribution) instead takes the first producer by step node_id and counts in-place steps, so the two can disagree. 4.5 should adopt the projection's rule; aligning graph.py is a later cleanup. Event log key: (stage position, stage, NodeKind declaration rank, node_id); seq from 1; summary excludes parser_metadata/privacy/extensions; not re-exported from `provenance`.
- 4.5 (design amendments, listed for end review): `compute_completeness` gains kw-only `stage_contracts_strict: bool = True` (config table names it the sole consumer; non-bool raises TypeError — 6.5 must coerce/validate the YAML value). Run state is "at least as severe as its worst document" (run-scoped contract gaps, validation `completed=False`, unattributable segments, empty run, undeclared/failed stages can make it worse). A claim-scoped stage marked complete must have emitted claim+derivation records for EVERY document (per document, not per claim), else that document is incomplete. Reasons: missing_anchor / unsupported / missing_records / recorded failure text (missing kind "anchor" is not a NodeKind); aggregated run reasons can repeat fragments (cosmetic).
- 4.5 (end review): a failed stage makes EVERY document with that stage's steps undetermined (stage status is run-wide and the 4.1 stage segment lists all its steps) — one LLM failure in a 100-PDF run makes all 100 undetermined. Narrowing needs per-document failure marking (design change).
- 4.5 → 6.4/6.5: `pdf_processor.process_pdf` returns early for documents already complete in the manifest (resume); if those stay in `run.document_fingerprints` they are reported incomplete for evidence_index/metadata_prefill/extraction_chunk/synthesis — 6.x must exclude them or record them. 6.4 must emit one producing step per document per claim stage it marks complete (incl. metadata_prefill).
- 5.1 (NEEDS HUMAN CONFIRMATION): unknown keys of a compatible artifact are preserved only where a free-form slot exists (node extensions under `x-compat-unknown`, edge `attributes`, finding `details`). Keys at the top level, run identity, missing segments, report containers and completeness entries are dropped with one WARNING naming each path. Full preservation needs an `extensions` field on ProvenanceGraph/RunIdentity (core type change, Revalidation Trigger). A bad record in a compatible artifact raises `MalformedProvenanceArtifactError` (no partial graph). New public names: `UnrecognizedSchemaVersionError`, `MalformedProvenanceArtifactError`, `COMPAT_UNKNOWN_KEY`, `SUPPORTED_SCHEMA_MAJORS=(1,)`. A graph read from disk keeps the version it was read with.
- 5.1 → 6.x / downstream emitters: free-form values must be JSON-native (lists not tuples/sets, str keys, finite floats). Tuples read back as lists (breaks round-trip equality); sets/frozensets raise TypeError on write. 6.5 must catch serialization errors so provenance never aborts extraction. Vocabulary additions are unreadable by older same-major readers — treat them as a major bump or relax the reader.
- 5.1 → 5.2: `graph_to_dict` passes NaN/inf through; 5.2 must decide on `allow_nan=False` for a strict, byte-identical writer. 5.2 must also resolve the artifact dir from `provenance.output_subdir` (see 1.2 note).
- DECISIONS (user, 2026-10-09) — SUPERSEDES the 2026-10-08 prefill decision (prefill cites the title) and resolves the NEEDS HUMAN CONFIRMATION notes 2.2 (duplicate_identity), 2.4 (stage names: confirmed as is), 3.2 (identity keys), 3.3 (a)(b)(c) and 5.1 (unknown keys: accepted as implemented). design.md now states these and the deviations recorded above for 1.1–5.1 (claim-adapter signature, `validate_chain`/`compute_completeness` signatures, failed-stage segments, builder-derived edges, schema constants in graph.py, dependency rule, serialization compatibility). (A3) `duplicate_identity` default severity → error: same-content PDFs collide on one source id and the second record is dropped while completeness says complete (4.8). (B1) stage marks take kw-only `source_id` (None = run-wide, for chain_validation and pre-document failures); `RecordedRun` gains `document_stage_status` / `document_stage_failure_reasons` (default empty, escalate-only, every reason kept); a per-document failure gives one stage segment per (stage, source_id) and makes only that document and its covered claims undetermined; resumed documents are excluded from the run's provenance; an empty run is undetermined; RecordedRun is not serialized, so no schema bump (4.6, 6.3, 6.4). (B2) anchor rule: exact = page AND coords; approximate = a real locator (page, char_span, or an `xml:id` structural path); section path / weak `.//{tag}` path alone → absent; resolves 3.3(a) (4.7). (B3) `text`, `annotations`, `source_pdf` never enter parser_metadata; `text_sha256` (new `identity.derive_text_digest`) and `text_chars` added; artifact content-free for all nodes; evidence text comes from the evidence cache (reviewer-ui revalidation item); resolves 3.3(c) (4.7). (B4) title item recorded as kind `metadata` pipeline-side; pure header helper `extract_header_metadata` feeds both the prefill and provenance-only header metadata nodes (`M-first-author`, `M-publication-date`); prefill cites the header node its value came from, fallback values cite nothing; an unsupported prefill claim is incomplete at claim level only, plus a `prefill_unsupported` warning (overridable), and does not make its document or run incomplete; resolves 3.3(b) and the 3.4 prefill question (4.8, 6.2, 6.4). Out-of-scope pipeline follow-up: fallback author/year are labelled confidence "h". (B5) unconsumed ManifestIdentity keys kept as `x-manifest-<key>` (fallback `x-manifest-unnamespaced`); run-relative `output_path` is Req 1.3's link; 6.3 computes ManifestIdentity and passes the synthesis model via `additional_model_ids`; resolves 3.2 (4.7, 6.3). (B6) caption sentences inherit the enclosing figure's page/coords with stable ids; page/coords are prompt bytes (`select_paper_evidence` → `serialise_evidence_package` → `_shared_paper_prefix`), so this is a declared prompt-content change; 7.3's prefix baseline must be taken after 6.6 (6.6). Output directory: 6.5 resolves `provenance.output_subdir` and passes the path to `write_graph`; Serialization reads no config (supersedes the 1.2 and 5.1 → 5.2 notes saying 5.2 resolves it).
- 2026-10-09 OPEN (needs human decision): GROBID header elements carry no `coords` and no `xml:id` (all three TEI fixtures), so under B2 the header metadata nodes are `absent` and a TEI-sourced prefill claim classifies `missing_anchor` — not exempted by the B4 policy, so its document is incomplete. Options: the helper gives header items page 1 (the title item's existing convention), extend the prefill policy to `missing_anchor`, or accept. Choices made while writing design.md, for end review: names `extract_header_metadata`, `derive_text_digest`, `additional_model_ids`; reserved local ids `M-first-author` / `M-publication-date`; fallback extension `x-manifest-unnamespaced`; per-document reasons stored as `tuple[str, ...]`; `prefill_unsupported` replaces (not adds to) `unsupported_claim`; a caption sentence inherits the figure location only when neither it nor its `<p>` has coords; 6.2 now depends on 4.8 and 6.6 must not run concurrently with 6.2 (same module).
- DECISION (user, 2026-10-09) resolving the OPEN note above: extend the prefill policy — a prefill-origin claim that is `missing_anchor` (TEI-header value citing a header metadata node without coords/xml:id) is, like an unsupported one, incomplete at claim level only and does not by itself make its document or run incomplete; no assumed page is assigned to header metadata nodes (4.8).
- 4.6 (end review): a RUN-WIDE (`source_id=None`) failure of a document-scoped stage that produced no derivation steps (e.g. `document_extraction`, or `evidence_index` with only evidence nodes) contributes nothing, so the run can still read `complete` — conflicts with the amended CompletenessComputer postcondition ("run-wide failed stages each make it incomplete or undetermined"). Pre-existing at 202be28; unreachable in practice because run-wide marks are reserved for `chain_validation` and pre-document failures. Fix candidate: a run contribution in `check_failed_stages` for any run-wide failed stage. Also: `graph._document_of`/`_step_documents` duplicate completeness's document attribution (and 4.4's `_producing_stages` split) — share one helper in a cleanup. A per-document stage segment is recognized by a bare `src:` first affected id.
- 4.7: on the 3 TEI fixtures the adapter now yields exact/approximate/absent = 745/3/173 (titles approximate via page 1; figures/tables exact; the 173 absent are exactly the caption sentences, fixed by 6.6). The xml:id locator form is a string contract with `_tei_xpath` (`//*[@xml:id='<id>']`) — 6.x should add a pipeline-side test pinning it. `identity.derive_text_digest` = full sha256 hex of UTF-8 text (no domain separation). Identity adapter logs a WARNING when keys go to `x-manifest-unnamespaced`.
- 4.8: only `incomplete` gaps with reason `unsupported`/`missing_anchor` on a prefill claim stay claim-level (`completeness._Analysis.aggregated_claim_state`); other gaps on a prefill claim (e.g. `missing_records`) and `undetermined` still aggregate. `prefill_unsupported` replaces `unsupported_claim` only for origin "prefill" (human/import keep `unsupported_claim`); an anchorless header-citing prefill claim is signalled only by the `missing_anchor` finding on the header node. `duplicate_identity` is now `error` everywhere (recorder stamps from DEFAULT_SEVERITIES; validate_chain re-stamps old warnings).
- DECISION (user, 2026-10-09, after 4.8): the run-wide failure gap noted under 4.6 must be fixed, and document attribution AND stage attribution get a single owner (public functions in graph.py, using the projection's stage rule) so graph builder, projection and completeness cannot diverge — task 4.9. Supersedes the 4.4 note's "aligning graph.py is a later cleanup".
- 4.9: graph.py is the single owner of attribution — public `document_of`, `step_documents`, `stage_segment_document`, `is_claim_identifier`, `evidence_id_parts`, `UNATTRIBUTED_STAGE`, `stage_sort_key`, `producing_stages`, `sole_contract_stage`, `attribute_stage` (the projection's stage rule). GraphBuilder, Projection, CompletenessComputer and ChainValidator call them; their private copies are deleted. Enforced by an import/literal guard (consumers may not use identity `*_PREFIX`/`*_SEPARATOR`/`parse_*`, `STAGE_ORDER`, `src:`/`claim:` literals, or `"#"` parsing) and a Hypothesis agreement test (200 derandomized examples, ~1.7 s) that mutation probes showed catches stage- and document-rule copies the guard cannot see. A run-wide stage failure now always makes the run undetermined. Missing-segment stages changed to the shared rule (intended). Validator edge cases on malformed records now follow the shared rule (empty source_id placed by id shape; claim ids without `#` scoped). Remaining cosmetic: duplicated run reason for a run-wide failure of a document-scoped stage. The claim adapter still resolves raw `loc` refs via `identity.parse_evidence_id` (agrees with `document_of` for well-formed ids; adapters are not attribution consumers).
- 2026-10-09 cleanup (after 4.9): aggregated reasons list each fragment once — documents and the run combine their children's reason fragments, not the joined text (fixes e.g. "missing_anchor; unsupported; unsupported"); a run-wide failure states its reason once in the run, and the per-document hint "stage '<s>' recorded nothing for this document" is a separate fragment kept on the document only. Resolves the cosmetic notes under 4.5 and 4.9.
- 5.2 → 6.5: `write_graph(graph, directory)` writes `<directory>/provenance_graph.json` (`ARTIFACT_FILENAME`) and returns the file path; `read_graph` accepts the file or its directory. The writer is strict JSON (`allow_nan=False`, UTF-8 bytes, `indent=2`, one trailing newline, no `sort_keys` because `graph_to_dict` already orders) and encodes fully before any `mkdir`/temp file, so a failed write leaves no directory, no temp file and any previous artifact intact. Beyond the 5.1 note, it can raise `ValueError` (NaN/inf, names the path), `TypeError` (set/non-str key) and `OSError`; 6.5's catch-all must cover all three. The fixed `provenance_graph.json.tmp` name assumes one writer per run directory (same as `save_manifest`).
- 6.1 → 6.4: with a sink the validator no longer raises on unknown `loc` ids, so `RepairRetryLoop.extract_with_repair` (pdf_processor.py: `validate_chunk_output` at the initial attempt and each repair attempt) would stop repairing them. Keep the strict sink-less validation inside the repair loop; pass the node_id-wrapping sink only to the final accepted raw output (findings are buffered, so one sink-bearing call is idempotent) and to both production `reconstruct_fields` calls (chunk and synthesis); when repair is exhausted and the last error is purely "unknown evidence IDs", re-validate the last raw with the sink so the claim is retained (5.4) instead of raising `RepairExhaustedError`. Validate and reconstruct report the same (code, reference, kind, field_index), so `validate_chain` merges them once the wrapper adds `node_id`. The wrapper must `.get("field_index")` — it is omitted when `i` is not a non-negative real int — and skip or log rather than let `derive_claim_id` raise.
- 6.1 (known divergence, for 6.4): the validator judges `loc` against local ids only. A scoped reference (contains `#`) to the claim's own source is never an exact member, so the sink gets a `dangling_reference` (also for a case-different one, where the claim adapter says `ambiguous`), while the claim adapter treats an exact one as cited. The model only sees local ids, so this is rare; the 6.4 wrapper should drop dangling/ambiguous sink calls whose `reference` contains `#` and leave that judgement to the adapter. `validator._report_reference_issues` has no public adapter helper to reuse; agreement is pinned by `test_classification_matches_the_claim_adapter` (local ids only; no ß/SS case yet).
- 6.6: caption sentences are the sentence items under `figure/figDesc/div/p/s` (the `.//div` walk also matches that inner `<div>`); `_enclosing_figure_coords` gives them the nearest enclosing `<figure>`'s page/coords in the same `_parse_coords` representation the figure item uses, only when neither `<s>` nor its `<p>` has a `coords` attribute (a non-empty malformed value still counts as "carrying coordinates" and leaves page None — HEAD behaviour; parse-success semantics would be a small follow-up). On the 3 fixtures the evidence adapter now gives exact/approximate/absent = 918/3/0 (62/64/47 = 173 caption items changed on arxiv/biorxiv/plosone; ids, order and all other fields unchanged, differential-tested against HEAD). The evidence cache (`{paper_id}_{pdf_sha256}_{_EXTRACTION_MAP_HASH}.evidence.json`) has no shape/version marker and a hit returns stored items as-is, so old caches keep page/coords None for captions until regenerated and their prompt bytes differ from fresh ones for the same paper. One existing oracle (`_expected_sentence_pages` in `test_evidence_index_coords.py`) was updated because it encoded the old "paragraph without coords means page None". → 7.3: take the prefix baseline after this commit (c13a8d3 + 6.6); the current prefix-stability test only catches per-call variation, so 7.3 should add a literal golden of the prefix bytes.
- 6.2 → 6.3/6.4/6.5: `build_or_load_evidence_bundle(qc_context, config, *, recorder=None, source_id=None)` wraps the unchanged old body; the caller MUST pass the document's `src:<fingerprint>` id (a missing/malformed one logs a WARNING and emits nothing, no stage mark). `run_id` is read from `recorder.snapshot().run.run_id` (no public accessor; snapshot copies everything under the lock per document) — add a public `ProvenanceRecorder.run`/`run_id` property in 6.3 or 6.5 and switch evidence_index to it. Any emission exception is caught (WARNING) and the stage is marked failed for that `source_id`; only BaseException escapes. Items with a bad id become `invalid_identifier` findings (`finding:invalid_identifier:<source_id>:evidence[<pos>]`, critical, `node_id=None`, details hold only source_id/position/truncated item_id) and do NOT fail the stage. A document with zero items is still marked complete, so completeness reports it `incomplete` with missing kind `evidence`. Producing backend is "grobid" when `tei_xml` is non-blank else `None` (inferred from the current TEI on a cache hit). Header nodes `M-first-author`/`M-publication-date` are re-minted from `bundle.tei_xml` each call (type metadata, section "Metadata", xpath `_tei_xpath(elem)`, text = the prefilled surname/year so `text_sha256` digests the prefilled value; precision `absent`); on a cache hit the prefill comes from the cache file but the header nodes from the current TEI — they agree only while the TEI is unchanged. `EvidenceBundle` does not expose `year_provenance` (it is in the cache file only), so 6.4 needs it (e.g. read it from the cache file or add a field) to decide whether the year cites `M-publication-date` (provenance `tei_header`); a header author counts as TEI-sourced only if `prefilled_fields[1]` equals the helper's `first_author`. Evidence-index stage mark: complete per document after `record_evidence`. Measured on the 3 fixtures: S/T/F exact/approximate/absent 918/3/0 plus 5 absent header nodes (bioRxiv's TEI has no date); `extract_header_metadata`, `HeaderMetadata` (`first_author`, `first_author_element`, `publication_year`, `publication_date_element`) and `_select_publication_date` replace `_extract_publication_year_from_tei`.
- 6.2 (observation for 6.5/7.x): a recorded critical `invalid_identifier` finding has `node_id=None`, so it appears in the validation report but `compute_completeness` still reports the document `complete`; if that should degrade completeness, attribute it by `details["source_id"]`.
