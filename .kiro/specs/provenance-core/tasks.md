# Implementation Plan — provenance-core

- [ ] 1. Foundation: package scaffold, dependency rules, configuration and output paths

- [ ] 1.1 Create the `src/provenance/` package skeleton and declare its dependency direction
  - Create the package directory with an `__init__.py` placeholder re-export surface and the `adapters/` subpackage, plus the mirrored `tests/src/provenance/` directory.
  - Add eight forbidden-import pairs to the AST dependency-direction rule table: `provenance` must not import `agents`, `pipeline`, `pdf_extractor`, or `quality_control`; and `quality_control`, `agents`, `pdf_extractor`, and `text_processing` must not import `provenance`.
  - Add one named test per new pair alongside the existing per-pair tests so the exhaustive test and the named tests stay consistent.
  - Observable: the fast test suite passes with the eight new dependency-direction tests collected and green, and `import provenance` succeeds from the repo root.
  - _Requirements: 1.5_
  - _Boundary: ProvenanceModels_

- [ ] 1.2 Register the provenance configuration block and run-scoped output directory
  - Add a `provenance` defaults mapping covering the enable flag, output subdirectory, severity overrides, fail-on-severity policy, and strict stage-contract flag.
  - Register `provenance` as a known top-level configuration key so configuration loading does not reject it, and add a loader that returns the merged provenance settings from a passed-in config mapping.
  - Add the run-scoped provenance output directory constant using the existing run-output-path helper, and add the `provenance` block to the shipped configuration file.
  - Observable: loading the shipped configuration returns provenance settings with documented defaults and raises no unknown-key error; the resolved output directory sits under the current run folder.
  - _Requirements: 1.3, 8.5_

- [ ] 2. Definition layer: vocabularies, records, identity, and stage contracts

- [ ] 2.1 Implement the privacy label and disclosure decision carrier
  - Define the single carrier record with privacy state, opaque label, opaque decision, supplying component, supply timestamp, and rejection reason, plus a shared unknown-state constant.
  - Implement attachment that stores supplied values verbatim, returns the unknown constant when nothing is supplied, and never infers or defaults a label.
  - Implement structural-only conformance checking that rejects malformed input with a reason and leaves the label unset, performing no interpretation of label or decision values.
  - Observable: unit tests show verbatim storage, unknown state on absent input, rejected state with a reason on malformed input, and that the module contains no comparison against any label value.
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - _Boundary: PrivacyCarrierModule_

- [ ] 2.2 Implement the provenance record models and closed vocabularies
  - Define the closed vocabularies for node kind, edge kind, evidence kind, anchor precision, derivation kind, determinism, severity, completeness state, claim support, and claim origin.
  - Define the frozen record types for source anchor, base node, source node, evidence node, claim node, derivation step, validation record, edge, and run identity, each embedding the privacy carrier.
  - Define every cross-layer type, that is, every type consumed by a layer other than the one that produces it: validation finding, the default severity table, missing segment, stage contract, artifact completeness, the immutable recorded-run snapshot, the chain-validation report, and the completeness report. The two reports belong here because the graph container carries them while sitting earlier in the dependency order than the modules that compute them.
  - Enforce the namespaced extension-field key convention at construction so extension metadata is preserved but visibly distinct from core fields.
  - Observable: unit tests construct one instance of every record type and every cross-layer type, confirm immutability and value equality, confirm a non-namespaced extension key is rejected, enumerate every vocabulary member, and confirm the default severity table has an entry for every finding code named in the design.
  - _Requirements: 1.1, 3.2, 4.1, 4.3, 5.1, 6.2, 7.1, 7.2, 9.1, 10.1, 10.4_
  - _Depends: 2.1_
  - _Boundary: ProvenanceModels_

- [ ] 2.3 Implement identifier derivation and parsing
  - Implement derivation of source identifiers from a supplied document fingerprint, run identifiers from the supplied identity fields, claim identifiers scoped by source and field index with a revision suffix, and validation identifiers.
  - Implement evidence identifier formatting that scopes the pipeline-assigned local identifier by source without altering it, plus total round-trip parsing back to source and local identifier.
  - Implement content-addressed derivation-step identifiers over stage, kind, and sorted input and output identifiers, and raise a dedicated invalid-identifier error for malformed input.
  - Compute no document, configuration, or extraction-map fingerprint anywhere in this module; those values are consumed from the caller.
  - Observable: unit tests confirm round-trip totality, rejection of a local identifier containing the scope separator, and that derivation-step identifiers are invariant under permutation of inputs and outputs.
  - _Requirements: 3.1, 3.3, 3.6, 4.2, 4.7, 4.8, 6.2_
  - _Depends: 2.2_
  - _Boundary: IdentityService_

- [ ] 2.4 (P) Implement the stage-contract declaration table and stage ordering
  - Declare, as data, the record kinds each pipeline stage must emit and the artifact scope each stage covers.
  - Declare the deterministic stage ordering used later by the event-log projection.
  - Document, alongside the table, that downstream specs introducing new pipeline stages extend both the contract table and the ordering in place and continue to route records through the recorder, rather than bypassing the recorder or suppressing the unknown-stage error; keep the vocabulary closed and the drift test in force.
  - Observable: unit tests confirm every declared stage has a non-empty required-record-kind set and appears exactly once in the ordering, and that a stage name absent from both tables is rejected at record time rather than silently accepted.
  - _Requirements: 2.1, 2.3, 7.6_
  - _Depends: 2.2_
  - _Boundary: StageContracts_

- [ ] 3. Emission layer: append-only recorder and pipeline adapters

- [ ] 3.1 Implement the append-only provenance recorder
  - Provide record methods for source, evidence, claim, derivation, and validation records, plus issue recording and per-stage complete and failed marks; expose no update or delete method.
  - Make duplicate recording of an identical record a no-op and duplicate recording of a conflicting record produce a duplicate-identity finding while keeping the first record.
  - Expose an issue-sink callable suitable for the chunk validator, and an immutable snapshot that returns equal values on repeated calls.
  - Support a disabled mode whose methods no-op while still returning derived identifiers, and reject recording against an undeclared stage.
  - Guard all mutation with a single lock so concurrent per-document recording cannot interleave, and copy under that same lock when snapshotting.
  - Observable: unit tests confirm append-only behavior, idempotent duplicate handling, conflicting-duplicate findings, snapshot stability, disabled-mode no-op, and that the recorder performs no file or network access; a concurrency test recording from multiple threads confirms every record survives and the snapshot count matches the number of recorded records.
  - _Requirements: 1.2, 1.6, 2.1, 5.4_
  - _Depends: 2.3, 2.4_
  - _Boundary: ProvenanceRecorder_

- [ ] 3.2 (P) Implement the source and run identity adapter
  - Convert a checkpoint-identity mapping into a source node carrying the document fingerprint, source format, source label, configuration fingerprint, schema fingerprint, and model identifiers.
  - Build the run identity from the per-document identity mappings, computing no fingerprints of its own.
  - Produce a valid source identity from content fingerprints alone when descriptive metadata is absent, and copy source metadata without evaluating disclosure.
  - Land tests in the identity-adapter test module only; the sole file shared with the sibling adapter tasks is the adapters re-export module, to which this task appends exactly one line.
  - Observable: unit tests build a source node and run identity from a literal identity mapping, confirm a metadata-free mapping still yields a source identity, and confirm the module performs no hashing of document or configuration content.
  - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7_
  - _Depends: 2.3_
  - _Boundary: Adapters/identity_adapter_

- [ ] 3.3 (P) Implement the evidence adapter
  - Convert evidence-index item mappings into evidence nodes carrying evidence kind, source reference, rank score, and producing backend, adopting the item identifier as the local identifier unchanged.
  - Derive anchor precision as exact when page and coordinates are both present, approximate when coordinates are absent but page or section path is present or the structural path is the weak fallback form, and absent otherwise, enumerating the absent anchor kinds in the latter case.
  - Copy unrecognized item keys into parser metadata rather than discarding them, and return a node with absent precision rather than raising on a malformed item.
  - Land tests in the evidence-adapter test module only; append exactly one line to the shared adapters re-export module.
  - Observable: unit tests over literal item mappings covering the exact, approximate, and absent cases produce the documented precision and missing-anchor lists, and an item with unknown and missing keys converts without raising.
  - _Requirements: 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 10.2_
  - _Depends: 2.3_
  - _Boundary: Adapters/evidence_adapter_

- [ ] 3.4 (P) Implement the claim adapter
  - Convert reconstructed field mappings into claim nodes with field index, field name, value digest, confidence, and citing edges to each referenced evidence node.
  - Classify claim support as unsupported when no evidence reference is present, missing-anchor when referenced evidence exists but carries no usable anchor, and supported otherwise, keeping the two failure states distinct.
  - Emit findings for unknown, duplicated, and ambiguous references while retaining the claim, and set a non-model origin for fields produced without a model call.
  - Emit revision edges linking a synthesized or merged claim to each contributing claim.
  - Land tests in the claim-adapter test module only; append exactly one line to the shared adapters re-export module.
  - Observable: unit tests confirm all three support classifications, that a claim with an unknown reference is retained alongside a finding, that a prefilled field carries the non-model origin, and that a merged claim links to every contributor.
  - _Requirements: 5.1, 5.2, 5.5, 5.6, 5.7_
  - _Depends: 2.3_
  - _Boundary: Adapters/claim_adapter_

- [ ] 3.5 (P) Implement the derivation adapter
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

- [ ] 4.1 Implement the typed graph builder with partial preservation
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

- [ ] 4.2 Implement the reference and structural chain-validation checks
  - Implement the checks for dangling references, unrepresented sources, evidence outside the run context, missing anchors, invalid identifiers, and non-conforming privacy carriers, plus duplicate, ambiguous, and unsupported-claim findings.
  - Construct and populate an instance of the models-owned chain-validation report container; declare no dataclass in this module. When a check cannot run, report the run as not completed with a recorded reason rather than as valid.
  - Not parallel with the graph builder: this task consumes the graph type produced by 4.1 and writes the same validation module that 4.3 extends.
  - Observable: one unit test per finding code in this group builds the minimal graph that triggers it and asserts the code is emitted; a further test confirms the not-completed path reports a reason and does not report the run valid.
  - _Requirements: 5.3, 8.1, 8.2, 8.3, 8.4, 8.6, 9.6_
  - _Depends: 4.1_
  - _Boundary: ChainValidator_

- [ ] 4.3 Implement the topology checks and severity classification policy
  - Implement iterative cycle detection over derivation and revision edges, orphan-node detection, and inconsistent-derivation detection, avoiding recursion so a pathological chain cannot exhaust the stack.
  - Apply the default severity table to every finding, apply per-code overrides supplied from configuration, and order findings deterministically by severity rank, code, and node identifier.
  - Not parallel with 4.2: both tasks write the same validation module.
  - Observable: unit tests confirm a cyclic graph yields the cycle finding, each finding code carries its documented default severity, a supplied override changes exactly that code's severity, and repeated validation of one graph returns findings in identical order.
  - _Requirements: 8.4, 8.5_
  - _Depends: 4.2_
  - _Boundary: ChainValidator_

- [ ] 4.4 (P) Implement the ordered event-log projection
  - Derive an ordered sequence of events from the graph using the declared stage ordering, node-kind rank, and node identifier as a total ordering key, with no clock and no randomness.
  - Persist nothing and read nothing; every event field must be derivable from the graph alone.
  - Observable: unit tests confirm two projections of the same graph are equal, no two events share a sequence number, and the module performs no file access.
  - _Requirements: 7.6_
  - _Depends: 4.1_
  - _Boundary: Projection_

- [ ] 4.5 Implement provenance completeness computation
  - Diff the stage-contract declarations against recorded stage status and graph content to detect stages that did not emit their declared record kinds.
  - Honor the strict stage-contract setting: when strict, a recorded stage absent from the contract table yields an undetermined state; when not strict, it is ignored.
  - Produce completeness states at run, document, and claim level, each carrying the missing record kinds and the responsible stages.
  - Classify an anchorless-evidence claim as incomplete for the missing-anchor reason and a reference-free claim as incomplete for the unsupported reason, never merging the two, and classify artifacts covered by a failed stage or a missing segment as undetermined with the recorded reason.
  - Return data only, producing no formatted report, no file, and no rendered output; the caller attaches the returned report to the graph using the graph builder's attachment function.
  - Observable: unit tests confirm the three-level states, the distinct missing-anchor and unsupported reasons, undetermined propagation from a failed stage, both strict-flag behaviors, and that no file is written.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - _Depends: 4.1, 4.3_
  - _Boundary: CompletenessComputer_

- [ ] 5. Persistence: schema-versioned artifact serialization

- [ ] 5.1 Implement graph serialization with the schema version guard
  - Implement conversion of the graph to and from a plain mapping carrying schema identity, schema version, run identity, nodes, edges, missing segments, and the validation and completeness reports the graph already carries; the write path takes the graph as its only data argument.
  - Preserve namespaced extension fields unchanged on both directions, and preserve unknown keys of a compatible major version into extension fields rather than dropping them.
  - Accept artifacts whose major schema version matches, and raise a dedicated unrecognized-version error naming the version found and the versions supported for any other major, materializing nothing in that case.
  - Serialize whatever validation records the graph already carries so a new validation method needs no core type change; attaching those records belongs to the graph builder, not here.
  - Observable: unit tests confirm a full round trip preserves the graph and its extension fields, a compatible-minor artifact with unknown keys loads with those keys preserved, and an incompatible-major artifact raises the unrecognized-version error.
  - _Requirements: 7.3, 7.5, 10.3, 10.4, 10.5, 10.6_
  - _Depends: 4.5_
  - _Boundary: Serialization_

- [ ] 5.2 Implement the run artifact write and read path
  - Write exactly one graph artifact per run to the run-scoped provenance directory using a temporary file followed by an atomic replace, mirroring the existing checkpoint write pattern.
  - Provide the matching read path, and define no interoperable or external export format.
  - Observable: an integration test writes a graph, reads it back to an equal graph, and confirms rewriting an identical graph produces a byte-identical file.
  - _Requirements: 1.3, 7.7_
  - _Depends: 5.1_
  - _Boundary: Serialization_

- [ ] 6. Pipeline integration

- [ ] 6.1 Add the optional issue sink to chunk-output validation and field reconstruction
  - Add an optional issue-sink parameter to per-item validation, chunk-output validation, and field reconstruction.
  - When a sink is supplied, record unknown, duplicated, and ambiguous evidence references as findings and retain the affected item instead of raising or silently dropping it.
  - When no sink is supplied, preserve the existing raise and silent-drop behavior exactly.
  - Observable: tests confirm that with a sink the claim survives and findings are recorded for each reference problem, and that without a sink the existing tests for raise and silent-drop behavior still pass unchanged.
  - _Requirements: 5.3, 5.4_
  - _Depends: 3.1_
  - _Boundary: PipelineIntegration_

- [ ] 6.2 Emit evidence nodes from the evidence index on both cache paths
  - Add an optional recorder parameter to evidence bundle construction and emit evidence nodes through the evidence adapter.
  - Emit on the cache-hit path as well as the cache-miss path so a cached run is not reported provenance-incomplete.
  - Observable: an integration test runs bundle construction twice against the same cached bundle and confirms both runs record the same evidence node identifiers.
  - _Requirements: 4.1, 4.7_
  - _Depends: 6.1_
  - _Boundary: PipelineIntegration_

- [ ] 6.3 Emit the source node and reconciliation-derived derivation steps from the extraction pipeline
  - Emit the source node for each document from the checkpoint identity mapping, and record the run identity once per run.
  - Convert the reconciled record's provenance mapping into derivation steps on the pipeline side, leaving the quality-control package unmodified.
  - Observable: an integration test over a fabricated bundle confirms one source node per document and at least one reconciliation derivation step is recorded, and the dependency-direction suite still passes.
  - _Requirements: 1.1, 3.1, 3.5, 6.1_
  - _Depends: 6.2_
  - _Boundary: PipelineIntegration_

- [ ] 6.4 Emit claim nodes and extraction derivation steps from the per-PDF processor
  - Emit claim nodes for merged extracted fields with their citing edges, and emit per-chunk and synthesis derivation steps marked probabilistic with the model identifier in effect.
  - Emit locally prefilled fields with the non-model origin, and record discard steps with their removal reason where content is dropped.
  - Pass the recorder's issue sink into every production call to chunk-output validation and field reconstruction on both the chunk path and the synthesis path, so reference issues are recorded on the real call path and not only in tests.
  - Perform all emission after prompt assembly and after response parsing so no provenance value can reach the shared prompt prefix.
  - Observable: an integration test over a fabricated chunk result set confirms one claim node per merged field, probabilistic derivation steps carrying the model identifier, prefilled fields carrying the non-model origin, and that a chunk citing an unknown evidence identifier produces a recorded finding while the field survives into the merged output.
  - _Requirements: 5.1, 5.7, 6.1, 6.7_
  - _Depends: 6.3_
  - _Boundary: PipelineIntegration_

- [ ] 6.5 Wire the recorder lifecycle into the orchestrator
  - Construct one recorder per run from the resolved configuration and pass it explicitly down the call chain, introducing no module-level state.
  - After processing completes, build the graph, run chain validation, attach the validation report and records to the graph, compute completeness, attach the completeness report to the graph, and write the run artifact, which takes the graph as its only data argument.
  - Log one run-level summary line covering node counts, findings by severity, and the run completeness state, and honor the disabled setting by skipping artifact production without changing extraction behavior.
  - Honor the fail-on-severity setting: when configured and the findings reach that severity or worse, log at error level and record a provenance failure in the run summary, while still writing the extraction output because provenance never aborts extraction.
  - Observable: a run over fabricated inputs produces a provenance artifact in the run output directory containing validation records and the computed completeness states, and the same run with provenance disabled produces no artifact and no behavior change.
  - _Requirements: 1.3, 1.4, 2.2, 8.7_
  - _Depends: 6.4_
  - _Boundary: PipelineIntegration_

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
