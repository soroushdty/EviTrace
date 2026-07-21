# Implementation Plan — provenance-audit-export

- [ ] 1. Foundation: subpackage scaffold, configuration, output paths, records, and the single writer

- [ ] 1.1 Create the export subpackage scaffold, its configuration sub-blocks, and its run-scoped output directories
  - Create the export subpackage inside the existing provenance package, together with its container sub-package and the mirrored test directories, each with a placeholder public re-export surface.
  - Extend the existing provenance configuration defaults with nested export, audit-package, and sharing sub-blocks covering the enable flags, output subdirectory names, the interoperable-format selector, the decision-log flag, the raw-parser-output retention flag, the automatic-bundling flag, the fail-closed default sharing level, and the empty decision-to-level map. Introduce no new top-level configuration key, so the known-key registration is unchanged.
  - Add the run-scoped audit-package output directory constant beside the existing provenance directory constant, and add the three sub-blocks to the shipped configuration file.
  - Observable: loading the shipped configuration returns the export, audit-package, and sharing settings with their documented defaults and raises no unknown-key error; importing the export subpackage succeeds from the repo root; both resolved output directories sit under the current run folder.
  - _Requirements: 1.7_

- [ ] 1.2 Implement the export-side record types, closed vocabularies, and schema constants
  - Define the closed vocabularies for reported status, status level, sharing level, marking basis, field disposition, and slot state.
  - Define the frozen record types for status entries and the status report, the sharing marking, per-file artifact hashes and the reproducibility reference, the run artifact, the field mapping and fidelity entries and report, the interoperable export container, decision-log entries, slot specifications and slot records and the slot inventory, the accepted-output, human-edit, and manual-review records, the audit package, and the run-artifact diff.
  - Define three independently versioned schema identity and version constant pairs, one each for the run artifact, the interoperable export, and the audit package, so an export format can evolve without forcing an artifact version bump.
  - Reference upstream provenance records by identifier and digest only; declare no copy of any upstream record type and hold no evidence text on any record.
  - Observable: unit tests construct one instance of every record type, confirm immutability and value equality, enumerate every vocabulary member, and confirm the three schema constant pairs are distinct and independently versioned.
  - _Requirements: 1.2, 2.1, 3.1, 4.1, 6.2, 7.5, 8.2, 10.1, 11.3, 12.4_
  - _Boundary: ExportModels_

- [ ] 1.3 Implement canonical serialization, atomic writing, and the schema-version guard
  - Implement a canonical JSON form with sorted keys, a fixed indent, ASCII escaping, and a trailing newline, reading no clock so serialization is a pure function of its payload.
  - Implement atomic file writing using the existing temp-file-then-replace pattern with temporary-file cleanup on any interruption, and make this the only module in the subpackage that opens a file for writing besides the bundle writer.
  - Implement a version-guarded reader that interprets any payload whose major schema version matches the reader's while preserving unrecognized keys under a namespaced extension key, and that raises a dedicated unsupported-version error naming the version found and the versions supported without materializing anything partial.
  - Require every written payload to carry its schema identity and version as its leading keys so each file is self-describing.
  - Observable: unit tests confirm that writing the same payload twice yields byte-identical files, that a payload with a matching major version round-trips with unknown keys preserved, and that a differing major version raises the unsupported-version error and produces no partially interpreted result.
  - _Requirements: 1.3, 3.1, 3.4, 3.5, 3.6, 5.4, 11.2, 11.6_
  - _Boundary: CanonicalSerialization_
  - _Depends: 1.2_

- [ ] 2. Marking, status, and reproducibility projections

- [ ] 2.1 (P) Implement the fail-closed sharing-suitability marking
  - Derive the marking by reading only the privacy state, disclosure decision, supplying component, and supply time from the graph's privacy carriers, never the privacy label and never any branch on a label value.
  - Return the most restrictive configured level when no decision exists anywhere in the run, recording that no decision was available as the marking basis; record a decision that is absent from the configured decision-to-level map as restricted with an unmapped basis while retaining the decision string verbatim; resolve the most restrictive level when several nodes carry decisions.
  - Classify no content, evaluate no policy, and alter nothing on the basis of the resulting marking.
  - Observable: unit tests show the fail-closed default with its basis when no decision exists, verbatim retention of a supplied decision together with its supplier and supply time, restricted-with-unmapped-basis for an unmapped decision, and a source-level assertion that the module never references the privacy label field.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - _Boundary: SharingMarker_
  - _Depends: 1.2_

- [ ] 2.2 (P) Implement provenance status reporting as a projection of the upstream reports
  - Project the upstream completeness report, the upstream chain-validation report, and the recorded per-stage failure reasons onto the reported status vocabulary, calling no upstream computation entry point and performing no traversal that would constitute recomputation.
  - Map an incomplete validation to unvalidated at run level so it dominates every other outcome; map the undetermined upstream state to undetermined; map an upstream incomplete state affecting every child to incomplete and one affecting only some children to partially traceable; otherwise report complete.
  - Carry the upstream reason string through verbatim at claim level so the missing-anchor and unsupported classifications stay distinct without being re-derived, and surface the per-stage failure reasons so a provenance failure names the stage at which it occurred.
  - Emit one entry per claim, one per document, and one for the run, drawn from the completeness report's own keys.
  - Observable: unit tests produce each of the five reported states from purpose-built minimal reports, confirm no claim-level entry is ever partially traceable, confirm missing-anchor and unsupported remain distinguishable, and a source-level assertion confirms no upstream computation or validation entry point is imported.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  - _Boundary: StatusReporter_
  - _Depends: 1.2_

- [ ] 2.3 (P) Implement reproducibility reference collection
  - Resolve a reference to the run-reporting subsystem's per-run reproducibility manifest inside the run output directory: record its path relative to that directory, the manifest version it declares, and a content hash pinning the exact file, and copy none of its content.
  - Record an explicit unavailable marker together with its reason when the manifest cannot be located or read, rather than omitting the field, writing a placeholder path, or substituting an inline capture of the same facts.
  - Resolve no source-code revision and capture no environment description, dependency versions, configuration mapping, determinism settings, or model identifier list: the run-reporting subsystem owns that manifest, and this task consumes it by reference. Invoke no subprocess, read no version-control metadata, and hold no configuration mapping, so no credential can reach the record by construction.
  - Reuse the configuration, schema, and document fingerprints supplied by the upstream run identity rather than computing a second set, and compute content hashes and byte sizes for every file the run wrote under its output directory, sorted by relative path — the one reproducibility fact this spec uniquely contributes.
  - Observable: unit tests confirm that a fixture manifest resolves with its version and content hash pinned, that an absent manifest yields the unavailable marker with a reason and no placeholder, that exactly one of the reference path and its unavailability reason is populated, that the fingerprints on the result are identical to those supplied by the run identity, and a source-level assertion confirms no configuration loader import, no version-control path access, and no subprocess call.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - _Boundary: ReproducibilityCollector_
  - _Depends: 1.2_

- [ ] 3. Interoperable representation, fidelity reporting, and the decision log

- [ ] 3.1 Implement the export mapping table and the computed fidelity report
  - Declare a mapping table keyed by upstream node kind and field name, giving each entry a disposition of preserved, transformed, or omitted, the target vocabulary term where one exists, and a reason that is mandatory for anything other than preserved.
  - Compute the upstream field inventory from the upstream record type definitions at call time rather than enumerating it in source, and report any node kind or field absent from the table as omitted with an unmapped reason, so an unmapped kind surfaces as a reported omission instead of a silent drop.
  - Declare the currently known omissions with their reasons, covering anchor precision and anchor-absence markers, per-node privacy carriers, finding severities, stage contracts, and graph missing segments, and classify any element the export can represent only approximately as transformed rather than preserved.
  - Emit the report as machine-readable data that accompanies the export, and emit it even when nothing was lost, with a lossless flag set and an empty entry list.
  - Observable: unit tests confirm an unmapped field is reported as omitted with an unmapped reason, an approximate representation is reported as transformed, a fully covering table yields a lossless report with no entries, and every non-preserved entry carries a non-empty reason.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  - _Boundary: FidelityReporter_
  - _Depends: 1.2_

- [ ] 3.2 Implement the interoperable provenance export
  - Emit a standard provenance-vocabulary representation as linked-data JSON in which sources, evidence, and claims are entities and derivation steps and the run are activities, driven entirely by the mapping table so the exporter contains no per-field literal of its own.
  - Preserve the relationship from each claim to each evidence record it cites and from each evidence record to its source document, and make the payload self-contained so it can be read without the runtime, its configuration, or the source documents.
  - Carry the export schema identity and version, the sharing marking, and the reported status into the export, and produce the export for a run whose provenance is incomplete rather than refusing, carrying the incomplete status with it.
  - Emit only the provenance vocabulary's context, never the existing annotation vocabulary's context, so the repository's existing sole producer of that annotation format is preserved.
  - Observable: an integration test over a fabricated three-claim graph recovers every claim-to-evidence-to-source path from the emitted payload alone, and asserts the export carries its schema information, the marking, the status, and a fidelity report produced from the same table.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - _Boundary: ProvExporter_
  - _Depends: 2.1, 2.2, 3.1_

- [ ] 3.3 (P) Implement the append-only decision log export
  - Serialize the ordered event-log projection the upstream package already produces, defining no ordering, assigning no sequence numbers, and holding no state, so no separate decision store exists.
  - Carry the pipeline stage, the record concerned, and the position in the run order onto each exported entry, taken from the projected event rather than re-derived.
  - Deliver this as the project's single append-only decision ledger so no second ledger implementation is produced.
  - Observable: unit tests confirm the exported entry count and ordering equal the upstream projection's, that exporting twice from the same graph yields identical output, and a source-level assertion confirms the module holds no module-level state and writes no file of its own.
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_
  - _Boundary: DecisionLogExporter_
  - _Depends: 1.2_

- [ ] 4. Per-document audit package container

- [ ] 4.1 Implement the declared audit-package slot registry
  - Declare, as data, a slot for each of the input manifest, parser outputs, canonical document, cleaned document, parser quality-control report, route map, route quality control, counterfactual route output, final route map, extraction outputs, agreement report, verification output, repair output, final extraction, cost report, and logs.
  - Declare three additional slots this spec requires directly: the retained raw parser output, the recorded removed blocks with their removal reasons and evidence-bearing flags, and the review records covering accepted outputs, human edits, and manual-review reasons.
  - Give every slot a description, the name of the spec that fills it, and an availability flag, so a slot whose owning capability has not shipped can later be reported as not applicable rather than as missing.
  - Observable: unit tests confirm the registry contains a slot for each of the sixteen named stage outputs plus the three added here, that every slot names an owning spec, and that no two slots share a name or a storage path.
  - _Requirements: 8.2, 8.6, 9.1, 9.2, 10.1_
  - _Boundary: SlotRegistry_
  - _Depends: 1.2_

- [ ] 4.2 Implement audit-package slot admission with verbatim retention
  - Create one builder per document, accepting content for a declared slot together with the name of the supplying stage, recording that supplier and retaining the content unmodified, and rejecting any undeclared slot name with a dedicated error.
  - Accept raw parser output per extraction backend under distinct entries so the selected backend's output is not the only one retained, and accept a reference plus digest instead of a copy when retention is disabled or the payload already exists in the content-addressed extraction cache.
  - Accept the recorded removed blocks with their removal reasons and any evidence-bearing flag exactly as supplied, parsing, computing, and reinterpreting nothing.
  - Accept an explicit absence marking with a reason, generate no slot content, and make every method a no-op when the container is disabled so no call site needs a conditional; guard all mutation so a stage emitting from a worker thread cannot interleave a partial slot.
  - Observable: unit tests confirm admitted bytes are retained byte-for-byte with the supplying stage recorded, that per-backend raw outputs are retained distinguishably, that an undeclared slot name raises, and that a disabled builder accepts every call without writing anything.
  - _Requirements: 8.1, 8.5, 8.7, 9.3, 9.7_
  - _Boundary: PackageBuilder_
  - _Depends: 4.1_

- [ ] 4.3 Implement audit-package finalization and the slot inventory
  - Produce an inventory covering every registry slot exactly once, classifying each as filled, absent with a reason, or not applicable naming the owning spec, so a capability that has not shipped is never conflated with an output that should have been present.
  - Report an absent slot whenever a declared and available slot was never supplied, including when quality control did not run or produced no report, and record the reason.
  - Mark the package incomplete whenever any slot is absent so an incomplete package can never present itself as complete, and attach the document's reported status and sharing marking to the finalized package.
  - Make finalization repeatable, returning equal values on repeated calls and never removing or rewriting a slot already filled.
  - Observable: unit tests fill a subset of slots and confirm the finalized inventory classifies the remainder as absent with reasons or not applicable with owning specs, reports the package incomplete, and returns equal values when called twice.
  - _Requirements: 8.3, 8.4, 8.6, 9.6_
  - _Boundary: PackageBuilder_
  - _Depends: 2.1, 2.2, 4.2_

- [ ] 4.4 Implement accepted-output, human-edit, and manual-review records
  - Record, for each accepted output, the route and the supporting evidence records backing it, retaining an entry whose route and evidence are both absent with an explicit unsupported marking rather than omitting it.
  - Record a human edit preserving both the original model value and the edited value, and mark the resulting value's origin as human rather than model.
  - Record the reason a field was sent to manual review, together with the stage that requested it.
  - Decide nothing, edit nothing, and select no route; accept only what a producing stage supplies.
  - Observable: unit tests confirm a finalized package contains the accepted-output, human-edit, and manual-review records supplied to it, that an accepted output lacking both route and evidence appears with its unsupported marking rather than being dropped, and that a human-edited entry retains both values with human origin.
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_
  - _Boundary: PackageBuilder_
  - _Depends: 4.2_

- [ ] 5. Run artifact, comparison, and package bundling

- [ ] 5.1 Implement run-level provenance artifact assembly and persistence
  - Assemble exactly one artifact per run containing the source identities, evidence references, claim references, transformation summaries, validation summary, run metadata, the reproducibility reference, sharing marking, reported status, and the relative references to each document's audit package.
  - Project every element from the upstream graph, holding no store of its own and copying no evidence text — identifiers and digests only.
  - Make assembly total: a graph from a failed run yields an artifact describing the completed portion with its missing segments carried through, and a run with no records at all yields an artifact that states no provenance was recorded and reports the run as undetermined rather than producing no artifact.
  - Persist and read the artifact through the canonical writer so version guarding and byte-identical rewriting are inherited, and carry the same status report object into the artifact that is handed to the exporter and to every package.
  - Observable: unit tests confirm a written artifact reads back equal to the assembled one, that a graph with missing segments and a graph with no records each still produce an artifact with the documented status, and that assembly is a pure function of its arguments with no clock or filesystem read.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 7.8_
  - _Boundary: RunArtifactAssembler_
  - _Depends: 1.3, 2.1, 2.2, 2.3, 4.3_

- [ ] 5.2 (P) Implement cross-run artifact comparison
  - Compare two run artifacts, reporting which source identities, claim entries, transformation summaries, validation statuses, and reported statuses differ, keyed by document fingerprint, claim identifier, derivation identifier, and subject identifier respectively.
  - Report a schema-version difference explicitly and list every section whose shape changed as not comparable, while still comparing the sections that remain comparable.
  - Perform no file access; callers read artifacts through the canonical reader first.
  - Observable: unit tests confirm comparing an artifact with itself yields no changes and no incomparable sections, that a changed claim value surfaces as a claim change, and that two artifacts with differing major versions report the version difference together with the incomparable sections.
  - _Requirements: 3.2, 3.3_
  - _Boundary: ArtifactComparator_
  - _Depends: 5.1_

- [ ] 5.3 Implement the self-contained audit-package bundle export
  - Produce a bundle for a selected document containing a bundle manifest with the schema identity and version, the slot inventory, the reported provenance status, and the sharing marking, plus every filled slot's content, the interoperable export, and its fidelity report, with no reference to any path outside the bundle.
  - Write entries in sorted order with pinned entry timestamps so re-bundling an unchanged package produces identical bytes, and write through the temp-then-replace pattern.
  - Bundle an incomplete package with its incompleteness declared in the manifest rather than refusing, and carry the package's sharing marking unchanged without removing anything on its basis.
  - Reject a package that has not been finalized before any file is written.
  - Observable: integration tests confirm a bundle for a package with unfilled slots is produced with its incompleteness declared, that the bundle can be opened and every slot resolved without reference to the run directory, and that bundling the same package twice yields byte-identical output.
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_
  - _Boundary: PackageBundler_
  - _Depends: 3.2, 4.3, 5.1_

- [ ] 6. Pipeline integration

- [ ] 6.1 Wire the run-completion lifecycle into the orchestrator
  - Own the per-document builder lifecycle: construct one builder per document as the run begins processing it, pass it explicitly down the call chain with no module-level state, and hold it until finalization.
  - After the upstream package has built, validated, and attached its reports to the graph, drive status reporting, sharing marking, reproducibility reference collection, per-document package finalization, run-artifact assembly and writing, interoperable export and fidelity-report writing, decision-log writing, and optional bundling, in that order so the artifact can reference the packages and the reproducibility hashes cover the files written before it; ensure the run-reporting subsystem's per-run reproducibility manifest is written before reference collection runs, so the reference resolves and the manifest itself is covered by a file hash.
  - Exclude the run artifact's own file from its hash list, and skip each step independently according to its configuration flag, recording a skipped artifact as absent rather than omitting it silently.
  - Ensure a run that ended abnormally still reaches this lifecycle and still produces the full artifact set.
  - Observable: an end-to-end test over a fabricated graph and bundle confirms the run artifact, the interoperable export, the fidelity report, the decision log, and one package bundle are all written under the run output directory, and that the reported run status is identical across all of them.
  - _Requirements: 1.1, 1.4, 7.8, 11.1, 12.1_
  - _Depends: 5.1, 5.3, 3.3_

- [ ] 6.2 Feed parser quality control, raw parser output, and cleaning records into each document's package
  - Accept the per-document builder the orchestrator constructed as an explicit parameter on the extraction path; construct no builder of its own and introduce no module-level state.
  - Hand the parser quality-control report, each extraction backend's raw payload, the canonical document, the cleaned document, and the recorded removed blocks with their removal reasons and evidence-bearing flags to the builder from the pipeline side, reading them from the bundle the quality-control package already returns and modifying that package not at all.
  - Record an absent slot with its reason when parser quality control did not run or produced no report.
  - Observable: an integration test over a fabricated bundle confirms the parser quality-control report, each backend's raw payload, the canonical and cleaned documents, and the removed-block records all arrive in their declared slots with the supplying stage recorded and content byte-identical to what was handed in.
  - _Requirements: 8.1, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - _Depends: 4.2, 4.3, 6.1_

- [ ] 6.3 Feed extraction outputs and accepted-output justification into each document's package
  - Hand the per-chunk extraction outputs and the final extraction to the document's builder after response parsing, so no artifact value can reach the shared prompt prefix.
  - Build accepted-output justification records from the claim records the upstream package already produced, carrying the route where one exists and the cited evidence identifiers, and marking an accepted output with neither as unsupported rather than omitting it.
  - Observable: an integration test confirms the finalized package for a processed document contains the extraction-output and final-extraction slots filled with the supplying stage recorded, and one accepted-output record per extracted field with its supporting evidence identifiers or its unsupported marking.
  - _Requirements: 8.5, 10.1, 10.5_
  - _Depends: 4.4, 6.1_

- [ ] 7. Boundary, regression, and property validation

- [ ] 7.1 Add boundary and regression tests for the export subpackage
  - Assert by syntax-tree scan that no module in the subpackage imports the pipeline, agent, quality-control, or PDF-extraction packages, and that the subpackage imports cleanly with those packages absent from the module table and without network access.
  - Assert by syntax-tree scan that the status module imports no upstream computation or validation entry point, that the sharing module never references the privacy label field, that no module other than the canonical writer and the bundle writer opens a file for writing, and that the existing annotation vocabulary's context appears nowhere in the subpackage.
  - Assert that the slot registry covers each of the sixteen named stage outputs plus the three added here, and that the shared prompt prefix is unchanged by this integration.
  - Observable: the fast test suite passes with these boundary tests collected and green, and each violates-on-purpose fixture used to develop them fails as expected.
  - _Requirements: 1.7, 4.4, 5.6, 7.6, 8.2, 8.7_
  - _Depends: 6.1, 6.2, 6.3_

- [ ] 7.2 (P) Add property tests for determinism, comparison symmetry, and inventory coverage
  - Assert that serializing any assembled run artifact twice yields byte-identical output across independent constructions of the same content, and that bundling the same package twice does likewise.
  - Assert that comparing an arbitrary artifact with itself yields no changes, and that swapping the comparison arguments swaps each recorded change's before and after.
  - Assert that finalization over an arbitrary sequence of admission, reference, and absence calls always covers every registry slot exactly once and never reports the package complete while any slot is absent.
  - Assert that the mapping table covers every field of every upstream record type, with the inventory computed at test time rather than enumerated, so adding a field upstream fails this test with a named gap.
  - Observable: the property suite passes and, when a field is temporarily added to an upstream record type in a fixture, the coverage property fails naming that field.
  - _Requirements: 3.2, 3.6, 6.4, 8.3, 8.4, 11.6_
  - _Boundary: FidelityReporter, ArtifactComparator, PackageBuilder, CanonicalSerialization_
  - _Depends: 5.2, 5.3_

- [ ] 7.3 Add end-to-end validation across the failure and degradation paths
  - Assert that a failed run with missing segments still yields the full artifact set with the failed stage named and a status of undetermined or unvalidated, and that a run with no provenance records yields an artifact stating that nothing was recorded.
  - Assert that the interoperable export of a multi-claim graph allows every claim-to-evidence-to-source path to be recovered from the payload alone, and that an export and a bundle are both produced for a run whose provenance is incomplete, carrying that incompleteness with them.
  - Assert that an audit package with unfilled slots owned by unshipped specs reports them as not applicable while an expected-but-missing slot reports as absent, and that the package is thereby reported incomplete.
  - Observable: the end-to-end suite passes with each degradation path exercised, and the reported status is consistent across the run artifact, the interoperable export, and every audit package in the same run.
  - _Requirements: 1.4, 1.5, 5.2, 5.3, 5.7, 7.1, 7.4, 7.8, 8.4, 8.6, 11.5_
  - _Depends: 7.1_
