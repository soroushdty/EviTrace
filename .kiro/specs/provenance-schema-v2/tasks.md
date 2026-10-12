# Implementation Plan — provenance-schema-v2

## Implementation Notes

- **Prerequisite spec.** `extraction-contract-hardening` is implemented first, in full (its task 12.1 complete), and its suite is green at every one of its tasks. This plan is written against its amended `design.md`. Where a task first needs a hardening interface, the task carries a "Prerequisite (other spec)" bullet naming the hardening task that delivers it. Those bullets are prose on purpose: `tests/steering/test_spec_task_traceability.py` accepts only task ids of this file inside `_Depends:_`. Hardening tasks used: 3.2 (shown-set module), 4.2 and 4.4 (validator constants, `nr_origin`, `absence_like`, codes), 5.2 (`prefill_source`, `author_provenance`), 5.5 (report classes, `below_coverage`), 6.2 (`get_version`), 8.1 and 8.2 (loop shown set, retention triage and its tests), 9.2 (prefill and marker plumbing), 9.3 (manifest stamp), 9.4 (sanitisation), 10.3 (differential test with the retention exception).
- **Order.** Foundation (baseline fixture, helpers); records, identity and carrier; the recorder; graph assembly, claim classification and chain checks; completeness; the 2.0.0 shape and the streaming writer; shards, run end and recovery; pipeline wiring (observer purity, lineage together with the new stage, run lifecycle); verification and guards; documentation; final validation. Task N implicitly depends on the tasks before it; `_Depends:_` is stated only where it is not obvious. `design.md` is the authority for names and signatures.
- **Every task is committable green.** The full fast suite passes after every sub-task. A task that changes a constructor, a vocabulary, a rule, a path or a patched name owns and re-points, in the same commit, the existing tests it breaks; `design.md` ("Existing tests that change, by owning task") lists them task by task with the measured counts (8 retain-path tests, 41 never-marked tests, 3 findings tests, 20 `RunIdentity`, 11 `ClaimNode`, 5 `ProvenanceGraph` construction sites, the artifact-path and empty-run tests, the ~14 patched run-end names). No task states that an existing test "passes unchanged" unless the design lists it as not changed.
- **Test-first.** A test-first sub-task adds tests marked `pytest.mark.xfail(strict=True, reason="task N.M")` that reference new names lazily (inside test bodies); the implementing sub-task removes the markers (a strict pass fails the suite otherwise).
- **The new stage is declared with its emitter.** `evidence_selection` is added to the stage tables, the drift guard and the contract tests in task 8.4, the task that makes the pipeline mark it. Fixtures that mark every stage iterate `STAGE_ORDER` (helper of task 1.2), so earlier tasks need no stage list.
- **Guards are updated, never weakened.** The stage-contract drift guard, the import-isolation and hashing guard, the 9.4 label scan, the property tests and the end-to-end test are updated in the task that makes them obsolete, with the reason recorded in the commit body.
- **Test isolation.** New tests resolve modules through one import path and compare values, never class identity across re-imports.
- **Parameter naming.** New functions that take a stage name are registered in the drift guard (`mark_stage_skipped`); other new code names such parameters `call_kind`.
- **Commits.** One tick per commit, subject `<type>(provenance-schema-v2): <task-id> <description>`.
- **Do not do here.** Prompt text, evidence selection or serialisation, validation rules of model output, the manifest shape, privacy logic, export formats, any new configuration key.

- [ ] 1. Foundation: baseline fixture and shared test helpers

- [ ] 1.1 Capture the 1.0.0 artifact fixture from the unchanged writer
  - Before any source change, build a small realistic graph (two documents, a failed stage, extensions, a validation record, completeness) with the current code and write it with the current `write_graph` to a checked-in fixture; add a generator note in the test explaining how it was produced.
  - Add a test that the fixture file declares schema version `1.0.0`, reading it as raw JSON (no reader), so later tasks that change the reader cannot break it.
  - Add the refusal test (the reader raises the unrecognized-version error naming `1.0.0` and the supported versions) marked as a strict expected failure for task 6.2; it keeps failing, as expected, while the reader still accepts or rejects the file for other reasons.
  - Observable: the fixture exists, the raw-JSON test passes, the refusal test is recorded as an expected failure, and the fast suite passes.
  - _Requirements: 1.8_
  - _Boundary: TestsAndGuards_

- [ ] 1.2 Add the shared v2 test helpers
  - Provide builders for run identities with a run-instance identity, small multi-document recorders and graphs, a helper that marks every declared stage for a document as complete, skipped or failed by iterating the stage order (never a literal list), a shard-directory builder and a fake run folder layout; keep every construction of a changing record in this one place for new tests.
  - Observable: a helper self-test passes and importing the helpers pulls in no network, GROBID or model-client code.
  - _Requirements: 16.5_
  - _Boundary: TestsAndGuards_

- [ ] 2. Records, vocabularies, identity and the privacy-carrier field

- [ ] 2.1 Write failing tests for the new records and identities
  - Cases: every new vocabulary member and record (stage status including skipped, claim answer and absence states, claim support `not_applicable`, the `pipeline` origin, stage entry and stages record, artifact reference, resumed document, claim counts with `pipeline_produced`, the run completeness `scope`, document records); run-instance identity derivation (stable, order-independent, sensitive to run identity, fingerprints and creation time) and the instance directory name; the shape rule for the document scope of identifiers; the stream digest with `update` and `finish`; the new finding codes and their default severities; the carrier decision identifier rules (string, requires a decision, stored verbatim, rejected otherwise).
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.7, 2.1, 4.1, 4.3, 4.13, 7.1, 8.7, 12.6_
  - _Depends: 1.2_
  - _Boundary: TestsAndGuards_

- [ ] 2.2 Add the decision identifier to the privacy carrier
  - Add the optional `decision_id` after `decision`, extend the structural conformance check (a string; allowed only together with a decision; no other undeclared keys), store it verbatim, and keep the module free of any interpretation of label or decision values.
  - Remove the expected-failure markers of 2.1 that cover the carrier; re-point the serialization and end-to-end assertions that spell out a privacy object and the label-scan expectations for the new key.
  - Observable: the carrier tests pass without markers, the inertness scans pass, a carrier without a decision identifier serialises with `decision_id` null, and the full fast suite is green.
  - _Requirements: 12.6_
  - _Depends: 2.1_
  - _Boundary: SchemaVocabulary_

- [ ] 2.3 Implement the new records, vocabularies and identities
  - Add the vocabularies and records of the design to the models module (required `run_instance_id` on the run identity, defaulted `answer` and `absence` on claims, `not_applicable` support, `pipeline_produced` counts, the run completeness `scope` field, records for stages, resumed documents and shards, the new finding codes and severities, the new snapshot fields for skip reasons, extensions and resumed documents); add the run-instance derivation, the instance directory name, the shape rule for document scope and the stream digest to the identity module; make the identity adapter build the run-instance identity and accept corpus identities so a run with no processed document still has run-wide fingerprints.
  - Update every existing construction site of the changed records (the 20 run-identity, 11 claim and 5 graph sites and the recorder snapshot assertions); in the serialization tests give `_small_graph()` and the realistic recorder the same literal run-instance identity; remove the markers of 2.1 that cover them.
  - Observable: the tests of 2.1 pass without markers, the full fast suite passes, and no test constructs a run identity without a run-instance identity.
  - _Requirements: 1.7, 2.1, 4.1, 4.3, 4.13, 7.1, 7.2, 8.7_
  - _Depends: 2.1_
  - _Boundary: SchemaVocabulary, IdentityV2_

- [ ] 3. Recorder: skipped stages, accessors and the per-document index

- [ ] 3.1 Write failing tests for the recorder changes
  - Cases: skipped marking and precedence (skip never replaces complete or failed; skip then complete becomes complete); every distinct failure and skip reason kept in order at run and document level; constructor context (extensions, resumed documents) in the snapshot; the accessors (`run`, `get_node`, `has_source`, `document_stage_status`); `document_records` content for several interleaved documents including a source shared by two labels; the per-document index agrees with the shared attribution rule (`document_of`, `step_documents`) over generated graphs; accessor results are empty when the recorder is disabled; a graph-builder segment and a completeness reason are produced from a tuple of run-wide failure reasons.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 2.2, 2.4, 9.3, 11.6_
  - _Depends: 2.3_
  - _Boundary: TestsAndGuards_

- [ ] 3.2 Implement skipped status, all-reason failures and run context
  - Add the skipped marking with the precedence of the design, keep every distinct run-wide failure reason (a tuple), store skip reasons at both levels, accept the extensions and resumed documents at construction and put them in the snapshot; edit the two readers of the old string field - the graph builder's failed-stage segment text and the completeness status normalisation and failure reasons - so a tuple is neither stripped as text nor dropped; register the new marking function in the drift guard inventories and its probe.
  - Re-point the recorder, graph-builder and completeness tests that read the old first-reason-only field.
  - Observable: the stage-related tests of 3.1 pass without markers, the drift guard passes, and the full fast suite is green.
  - _Requirements: 2.1, 2.2, 2.4, 15.4_
  - _Depends: 3.1_
  - _Boundary: RecorderV2_

- [ ] 3.3 Implement the accessors and the per-document index
  - Add the public accessors and the record-time index (documents by the shared shape rule; findings by node, by the document named in their details, or by the edge's origin), with `document_records` building sorted tuples under the lock; make the graph module's shape parse delegate to the identity helper so there is one parse, and update the separator allow-list of the import-isolation guard (the stale graph entry removed, a justified identity entry added).
  - Remove the remaining markers of 3.1.
  - Observable: the tests of 3.1 pass without markers, the agreement property test passes, and the attribution guard and the identifier-construction guard still pass.
  - _Requirements: 9.3, 11.6_
  - _Depends: 3.2_
  - _Boundary: RecorderV2, GraphAssembly_

- [ ] 3.4 Use the accessors in the pipeline
  - Replace the three per-document snapshot calls (evidence-index run identity, shared-source check, extraction-failure stage lookup) by the accessors.
  - Observable: no pipeline module calls the recorder snapshot except the run-end code; the evidence-index, document-provenance and run-provenance tests pass (they assert behaviour, not the snapshot calls); a timing test shows the per-document cost does not grow with the number of recorded documents.
  - _Requirements: 11.6_
  - _Depends: 3.3_
  - _Boundary: RunLifecycle_

- [ ] 4. Graph assembly, claim classification and chain checks

- [ ] 4.1 Write failing tests for graph assembly, carriers and the absence rule
  - Cases: the graph carries extensions and resumed documents from the snapshot and validates the keys of its run-level extensions; `attach_stages` rebuilds the stages record from a later snapshot with one entry per declared stage for every document of the run identity (`not_run` explicit, also for a document that produced no records) and is idempotent; `attach_carriers` (new graph, original unchanged, idempotent for equal carriers, atomic conflict naming only node ids, unknown ids ignored with one warning, all five node kinds including the validation record, rejected carriers refused when disallowed, equality decided on whole carriers without interpreting label or decision); the shared absence-state rule (`ids_cited` iff a cited id is an evidence node of the graph).
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.10, 2.1, 2.6, 2.7, 8.1, 12.1, 12.2, 12.3, 12.4, 12.5, 12.9, 12.10_
  - _Depends: 3.3_
  - _Boundary: TestsAndGuards_

- [ ] 4.2 Implement graph assembly, stage attachment and carrier attachment
  - Add the defaulted graph fields (graph assembly copies extensions and resumed documents but leaves `stages` unset until the schema task switches the serializer on, so a graph and its serialisation never disagree between tasks), add the stages attachment, the carrier attachment with its conflict error, the run-level extension key check and the shared absence-state rule; keep the schema version constant unchanged for now.
  - Remove the markers of 4.1.
  - Observable: the tests of 4.1 pass without markers, existing graph tests pass, round trips still hold for every graph the builder produces, and the label-interpretation scan finds nothing in the graph module.
  - _Requirements: 1.10, 2.1, 2.6, 2.7, 8.1, 12.1, 12.2, 12.3, 12.4, 12.5, 12.9, 12.10_
  - _Boundary: GraphAssembly_

- [ ] 4.3 Write failing tests for claim classification and the new findings
  - Claim adapter module: answer from the persisted marks only (never from value text), the four-value origin mapping, unknown mark and both-marks fallbacks with a warning, `ids_cited`/`no_ids` against known evidence ids (including a not-reported answer citing only a dangling id), support of absence-valued claims (`not_applicable` without cited nodes, the anchor-rule value with them, `unsupported` never), value digest unchanged, property test that arbitrary mark combinations never raise.
  - Chain-check module: no `unsupported_claim` or `prefill_unsupported` for absence-valued claims, `absence_without_ids` only for model-origin `no_ids` claims, never for `absence_like`, prefill or pipeline claims; `cited_evidence_not_shown` positive, negative and vacuous cases; an in-place filter step does not create a cycle or change attribution.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.13, 6.5_
  - _Depends: 4.2_
  - _Boundary: TestsAndGuards_

- [ ] 4.4 (P) Implement claim classification in the claim adapter
  - Read the persisted marks, assign answer, absence state, support and origin per field (per-field origin overrides the call-level origin only through the mapping), mirror the origin-mark vocabulary in the adapter, and leave reference-problem findings as they are.
  - Remove the adapter markers of 4.3.
  - Observable: the claim-adapter tests of 4.3 pass without markers and the existing adapter tests pass.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.10, 4.13_
  - _Depends: 4.3_
  - _Boundary: ClaimClassification_

- [ ] 4.5 (P) Implement the new chain checks
  - Restrict the unsupported-claim findings to asserted claims, add the absence finding and the cited-outside-inputs check using the shared absence-state rule.
  - Remove the chain-check markers of 4.3.
  - Observable: the chain-check tests of 4.3 pass without markers and the existing validation tests pass; the finding-code default-severity table test covers the two new codes.
  - _Requirements: 4.5, 4.6, 6.5_
  - _Depends: 4.3_
  - _Boundary: ChainChecks_

- [ ] 5. Completeness policy

- [ ] 5.1 Write failing tests for the completeness policy
  - Never-marked rule for each upstream combination (complete, skipped, failed, not run, unmarked), including the suppression when any stage of the document failed (a later failure does not make an earlier unmarked stage reported), for a document and for the run-scoped stage; skipped stage requires nothing; the attribution table row by row with minimal graphs; warning and info findings change nothing; an override lowering a code removes its effect; the set of degrading findings equals the report's critical and error set; fail-run severity independence; same-content documents read incomplete with the duplicate-identity reason and share their stage status; absence-valued claims need no support but a critical finding on one still degrades it; the document reads complete at any absence rate when its asserted claims are supported; counts and their invariants (nothing counted twice) including revised claims and the coverage indicator; the empty-run reason; the run `scope` (`partial` with resumed or unrecorded documents, `full` otherwise).
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 2.8, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 4.7, 4.8, 4.9, 4.11, 4.12, 8.5, 8.7_
  - _Depends: 4.4, 4.5_
  - _Boundary: TestsAndGuards_

- [ ] 5.2 Implement skipped handling and the never-marked rule
  - Extend the status ranking with skipped, make skipped and unmarked stages require nothing in the contract diff, add the never-marked rule with the upstream definition of the design (ordering by the shared stage ordering function, not by position in the stage tuple; suppressed for a document with any failed stage) and the run-scoped variant, and include skipped stages in the recorded-stage set for strict undeclared-stage checks.
  - Re-point the 41 existing tests that build snapshots or run ends without marking every stage (29 in the completeness module, 8 in the provenance end-to-end module, 2 in the extraction-pipeline provenance module, 1 in the attribution module, 1 in the evidence-index provenance module) by marking their stages in the fixtures through the helper of 1.2; change no assertion about behaviour this task does not change.
  - Remove the markers of 5.1 that cover these rules.
  - Observable: the stage-related tests of 5.1 pass without markers and the full fast suite is green.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 2.4_
  - _Depends: 5.1_
  - _Boundary: CompletenessPolicy_

- [ ] 5.3 Implement findings-driven completeness
  - Contribute `incomplete` for each critical or error finding of the effective report at the level given by the attribution rules, keep graph-derived support gaps independent of findings, state in the module documentation that findings now count, and re-point the 3 end-to-end tests whose expected states change.
  - Remove the markers of 5.1 that cover findings, attribution, overrides, consistency and same-content documents.
  - Observable: the tests of 5.1 for these rules pass without markers; a generated-graph property test finds a completeness entry for every critical or error finding and none for warning or info findings.
  - _Requirements: 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12_
  - _Depends: 5.2_
  - _Boundary: CompletenessPolicy_

- [ ] 5.4 Implement absence-aware claims, counts, coverage, scope and the empty-run reason
  - Skip support gaps for absence-valued claims, compute the counts over final claims with the origin split and no double counting, carry the coverage indicator per document and for the run from the supplied mapping, set the run entry's scope, and state in the empty-run reason the number of resumed and unrecorded documents.
  - Re-point the tests that compare run or document completeness entries or whole reports by equality (new `scope`, counts).
  - Remove the remaining markers of 5.1.
  - Observable: the remaining tests of 5.1 pass without markers; the count invariants hold in a property test; the full fast suite is green.
  - _Requirements: 2.8, 4.7, 4.8, 4.9, 4.11, 4.12, 8.5, 8.7_
  - _Depends: 5.3_
  - _Boundary: CompletenessPolicy_

- [ ] 6. Schema 2.0.0 and the streaming writer

- [ ] 6.1 Write failing tests for the 2.0.0 shape
  - Cases: a literal 2.0.0 golden for a small graph that includes a not-reported claim with support `not_applicable`, a run entry with its scope and the counts (byte compare), top-level key order, version constants and supported majors, the 1.0.0 refusal test of 1.1 (still pending), an unknown top-level key preserved under the reserved compatibility key with one warning, unknown keys in the run record, stage-status object, counts and completeness entries named and dropped with a warning that states a major version is needed, a non-conforming run-level extension key relocated, the writer stamping its own version when a graph read with a newer minor is rewritten, explicit `not_run` entries per declared stage for every processed document, round trips of every new record and of the stages, resumed documents and counts, required keys of a 2.x artifact.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 1.9, 1.10, 2.1_
  - _Depends: 5.4_
  - _Boundary: TestsAndGuards_

- [ ] 6.2 Implement the 2.0.0 artifact shape and reader
  - Set the schema version to 2.0.0, write and read the new top-level keys and records, switch graph assembly on to fill the stages record, preserve unknown top-level keys, stamp the writer's version on every write, keep supported majors derived from the writer (now 2), state the compatibility rule and the list of 2.0.0 additions in the module documentation and the decoder warning, and remove the strict expected-failure marker of the 1.1 refusal test.
  - Re-point existing serialization, builder and end-to-end tests for the version literal, the key order and the new required keys.
  - Observable: the tests of 6.1 pass without markers, the golden file is checked in, the 1.0.0 fixture is refused, and the full fast suite is green.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.9, 1.10, 2.1, 2.3, 2.6_
  - _Depends: 6.1_
  - _Boundary: ArtifactSerialization_

- [ ] 6.3 Write failing tests for the streaming writer
  - Cases: a property test that the piecewise text equals the complete encoding for generated graphs (unicode, empty containers, nested free-form values, extensions); content errors raise before any directory or file exists and name the path; a failing replace or write removes the temporary file and keeps the previous artifact; a digest mismatch between the passes discards the file; the guard refuses to replace an artifact of another run instance, a corrupt file and a foreign file but replaces its own instance; the encoding transient does not grow with the document count; the artifact bytes do not depend on the order in which concurrently recorded documents finished (the same records recorded in shuffled orders and from several threads give identical bytes).
  - Observable: the new tests are collected and recorded as expected failures; the existing write-contract tests still pass.
  - _Requirements: 7.5, 11.1, 11.2, 11.3, 11.4, 11.5, 13.1, 13.4_
  - _Depends: 6.2_
  - _Boundary: TestsAndGuards_

- [ ] 6.4 Implement the streaming two-pass writer and the no-overwrite guard
  - Add the piecewise encoder, the validation and digest pass, the file pass with digest comparison, the bounded prefix check of an existing artifact and the conflict error; confine hashing to the identity module through the stream digest (finisher named `finish`) and update the hashing guard's allow-list for the new function names.
  - Re-point the tests that write two graphs of different run instances into one directory (for example the failing-replace test, which would otherwise raise the conflict error instead of the operating-system error).
  - Remove the markers of 6.3.
  - Observable: the tests of 6.3 pass without markers, the five write-contract tests named in the design pass without modification, and the hashing guard passes.
  - _Requirements: 7.5, 11.1, 11.2, 11.3, 11.4, 11.5, 13.1_
  - _Depends: 6.3_
  - _Boundary: ArtifactSerialization_

- [ ] 7. Shards, run end and recovery

- [ ] 7.1 Write failing tests for the shard store
  - Cases: run header and shard round trips, content (no clock, no run-level data, sorted, structural edges absent), byte-identical output for equal records, atomic write (temporary file in the same directory, failure keeps the previous file, interrupted write leaves a readable earlier shard), file naming by document fingerprint with rejection of any name that is not a bounded run of letters and digits (path separators, dots, over-long names), restore through the public recorder methods gives an equal snapshot, a stray edge is skipped with a warning, malformed shards are reported, file references carry a relative POSIX path, a digest and the run-instance identity.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.9, 9.10, 13.2_
  - _Depends: 6.4, 3.3_
  - _Boundary: TestsAndGuards_

- [ ] 7.2 Implement the shard store
  - Add the run header and shard formats, atomic writers, readers, listing, file references, the safe file-name rule and the recorder restore function exactly as designed.
  - Remove the markers of 7.1.
  - Observable: the tests of 7.1 pass without markers and the import-isolation guards pass for the new module.
  - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.9, 9.10, 13.2_
  - _Depends: 7.1_
  - _Boundary: ShardStore_

- [ ] 7.3 Write failing tests for the run-end sequence
  - Cases: the sequence order (carriers, validation, stage mark, second carrier phase, stages attachment, completeness, write); a carrier source called twice with the two phases and absent giving byte-identical output; a failing or conflicting carrier source degrades with a warning and changes nothing else; rejected carriers attach before validation and are refused after; a conflicting existing artifact degrades without an artifact; validation failure still writes an unvalidated artifact; the final chain-validation status is the serialised one; the coverage mapping reaches the counts.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 2.6, 12.7, 12.8, 12.9, 14.1, 4.11_
  - _Depends: 7.2_
  - _Boundary: TestsAndGuards_

- [ ] 7.4 Implement the run-end function
  - Move the run-end sequence into the provenance package as the single implementation, add the carrier phases and the stages attachment, return a result object with the degradations, and keep every step individually guarded.
  - Remove the markers of 7.3.
  - Observable: the tests of 7.3 pass without markers; the import-isolation guards pass.
  - _Requirements: 2.6, 12.7, 12.8, 12.9, 14.1, 4.11_
  - _Depends: 7.3_
  - _Boundary: RunEnd_

- [ ] 7.5 Delegate the pipeline run end to the shared function
  - Make the run-provenance finish routine build its summary from the run-end result, pass the optional carrier source and the manifest-derived coverage mapping, keep settings validation, the fail-run severity and the summary line, and log the recovery command when the run end cannot finish.
  - Re-point the tests that patch run-end functions on the pipeline module (about 14 sites in the run-provenance tests and the injection table of the orchestrator provenance tests: graph assembly, validation, completeness, attachment and write) so they patch the names now used by the provenance run-end module.
  - Observable: the run-provenance and orchestrator provenance tests pass with the re-pointed patch targets, and a test shows a run produced through the pipeline equals one produced by calling the run-end function directly.
  - _Requirements: 3.10, 14.1, 14.2, 14.3_
  - _Depends: 7.4_
  - _Boundary: RunLifecycle_

- [ ] 7.6 Write failing tests for recovery
  - Cases: recovery of a run in which every document finished equals the normal artifact apart from the recovery record; documents without a shard are present, undetermined and listed; the policy comes from the header; refusal when an artifact exists unless replacement is requested; refusal of an instance whose files are newer than the liveness threshold unless forced (using an injected clock); orphan detection (header present, artifact absent) with the logged command; the command-line entry returns 0 and 1 with the documented messages and flags; recovery imports no pipeline module, works with no credential set, and the provenance package does not import the recovery module (running it with `-m` raises no double-import warning); a carrier source can be supplied.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - _Depends: 7.4_
  - _Boundary: TestsAndGuards_

- [ ] 7.7 Implement recovery, orphan detection and the command-line entry point
  - Add recovery with the liveness guard and the replace and force options, orphan detection, and the command-line entry exactly as designed; do not import the recovery module from the package initialiser.
  - Remove the markers of 7.6.
  - Observable: the tests of 7.6 pass without markers; running the entry point against a constructed instance directory writes the artifact and prints its path.
  - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6, 10.7, 10.8_
  - _Depends: 7.6_
  - _Boundary: Recovery_

- [ ] 8. Pipeline: observer purity and evidence lineage

- [ ] 8.1 Write failing tests for observer purity and failure reasons
  - Prerequisite (other spec): extraction-contract-hardening 8.1 and 8.2 (loop shown set, exposed mapping, retention triage) and 4.2 (violation codes).
  - Differential: the real orchestrator path with scripted responses, provenance on and off, over scripts including repair exhaustion on unknown ids, on a not-shown id, on a parse error and in synthesis; compare extracted outputs, manifest, evidence cache bytes and all model-call arguments. Failed documents: stage failed with a reason carrying the codes and ids, undetermined document, no claims, two failed chunks both named, a very long message gets the omitted-characters marker, accepted outputs with duplicate references are recorded once per claim.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 16.1_
  - _Depends: 7.5_
  - _Boundary: TestsAndGuards_

- [ ] 8.2 Remove the exhaustion-retain path and record failure reasons
  - Prerequisite (other spec): extraction-contract-hardening 8.2.
  - Delete the retention block of the repair loop (keeping the accepted-output reference reporting), add the read-only exhaustion mapping and the codes in the exhaustion metadata, add the reason helpers with the 4,000-character bound and marker, use them at the extraction-failure and synthesis-failure sites, and stop truncating failure reasons at 200 characters.
  - Rewrite every docstring and comment that describes retention (the document processor's docstring, the repair loop's constructor comment and exhaustion comment, the claim-provenance module documentation) and the sink-mode docstring of the validator, which now says it is reached only by direct callers and accepted outputs; the `not_shown` reason itself stays.
  - Re-point the 8 retain-path tests as listed in the design and the hardening tests that pin retention with the not-shown reason.
  - Remove the markers of 8.1.
  - Observable: the tests of 8.1 pass without markers, the differential test shows identical results with provenance on and off in every exhaustion scenario, no source comment or docstring mentions retained output, and the full fast suite is green.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - _Depends: 8.1_
  - _Boundary: ObserverBoundary_

- [ ] 8.3 Write failing tests for the evidence lineage
  - Prerequisite (other spec): extraction-contract-hardening 3.2 (shown-set record) and 8.2 (exposed mapping).
  - Cases: the selection step (inputs the document's index nodes without header nodes, outputs the package nodes, removed ids and the cap reason, nothing recorded as removed when everything was selected); a reduced call adds a token-budget step; extraction and synthesis steps list the call's shown nodes as inputs and carry the package digest; claims cite a subset; every exit after the first model call records the selection steps and marks the stage - an exhausted extraction chunk, a failing synthesis call, an output that fails validation, a raised output-write error, an unexpected exception in a later step, and success; an unreadable package marks the stage failed; a document with no evidence items but a model call marks it failed without recording an empty step; a document that needs no model call marks it skipped with a reason, and so do the prefill, chunk and synthesis stages exactly where `process_pdf` positively knows they do not apply; an emitter that silently returns leaves its stage unmarked and the document undetermined; an exception escaping the document processor marks the evidence-selection stage failed together with the claim stages; the lineage changes no prompt argument and no selection output.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 2.5, 6.1, 6.2, 6.3, 6.4, 6.6, 6.7, 6.8, 6.9_
  - _Depends: 8.2_
  - _Boundary: TestsAndGuards_

- [ ] 8.4 Declare the evidence-selection stage and implement the lineage emission, skip marks and call-step inputs
  - Prerequisite (other spec): extraction-contract-hardening 3.2, 8.2 and 9.2.
  - Declare the stage in both stage tables in this task, together with the drift guard's core-stage list and the contract and projection tests that count or list stages, so that no earlier state has a declared stage nothing marks.
  - Add the lineage emission and the positive-knowledge skip marking to the claim-provenance module; call the emission from a `finally` that covers everything from the first model call to the end of the document processor; mark skipped only at the four conditions the processor knows; make extraction and synthesis steps take the shown nodes as inputs (falling back to the cited nodes when no shown set exists) with the package digest extension, building input tuples from the recorded node identifier objects; extend the orchestrator-side failure marking to the evidence-selection stage.
  - Remove the markers of 8.3.
  - Observable: the tests of 8.3 pass without markers; the prefix goldens and the prompt-cache stability tests pass unchanged; a before/after comparison of evidence selection output shows no change; the full fast suite is green with the stage declared.
  - _Requirements: 2.5, 6.1, 6.2, 6.3, 6.4, 6.6, 6.7, 6.8, 6.9, 15.4_
  - _Depends: 8.3_
  - _Boundary: EvidenceLineage, StageTables_

- [ ] 8.5 Use the single header-sourcing rule for header citations
  - Prerequisite (other spec): extraction-contract-hardening 5.2 (prefill source and author provenance).
  - Make header citation require that the prefill-source rule calls the value header-sourced in addition to the current-header equality check.
  - Observable: the claim-provenance tests and hardening's equivalence test pass; citations on the three fixtures and on stale caches are unchanged.
  - _Requirements: 4.12_
  - _Depends: 8.4_
  - _Boundary: EvidenceLineage_

- [ ] 8.6 Add the end-to-end claim classification test through the document processor
  - Prerequisite (other spec): extraction-contract-hardening 4.4 and 9.2 (markers on persisted fields).
  - Run the document processor with scripted answers covering an asserted value, an `ids_cited` and a `no_ids` not-reported answer, an absence phrase with and without ids, an empty-value normalisation, a merge-synthesised not-reported field and header and fallback prefill; assert each claim's answer, absence state, support and origin and the document's counts and completeness, with no `unsupported` for absence-valued claims.
  - Observable: the test passes and the document reads complete with every asserted claim supported.
  - _Requirements: 4.1, 4.3, 4.5, 4.6, 4.7, 4.8, 4.9, 4.13_
  - _Depends: 8.4_
  - _Boundary: TestsAndGuards_

- [ ] 9. Run lifecycle: identities, resumed documents, header, shards, instance directory

- [ ] 9.1 Write failing tests for the run lifecycle
  - Prerequisite (other spec): extraction-contract-hardening 6.2 and 9.3 (contract version single source, manifest stamp).
  - Cases: the configuration fingerprint changes with the sanitisation setting, the normaliser and the contract version and not with excluded keys; two executions share the run identity and differ in the run-instance identity; the artifact lands in the instance directory; a foreign artifact is refused; the header is written before the first document; one shard per document including a failing one and one that raised; the shard carries the coverage flag; two identical-content documents finishing concurrently never leave an older shard and share their stage status (one failure marks both, reported through the duplicate-identity finding); resumed documents with and without earlier shards and artifacts (pointers, digests, relative paths, contract version, an orphan instance contributing a shard reference only); unrecorded documents with reasons; a run with no processed document writes an artifact with no nodes whose reason states the numbers and logs no warning; the resume set equals the skip test; orphan instances are detected and the recovery command is logged, nothing is recovered in-process; a header or shard write failure continues the run and still produces the artifact; shards are never deleted.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 2.8, 3.10, 3.11, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.2, 9.6, 9.8, 9.10, 10.4_
  - _Depends: 8.5, 7.7_
  - _Boundary: TestsAndGuards_

- [ ] 9.2 Implement the run plan and the fingerprint scope
  - Prerequisite (other spec): extraction-contract-hardening 4.2 and 6.2.
  - Add the planning function that returns processed identities, resumed candidates and unrecorded documents (keeping the old function as a wrapper), widen the hashed extraction section with the sanitisation settings and the contract version, and build the run identity from the corpus identities when nothing is processed.
  - Re-point the tests that patch the planning function on the pipeline module (in the run-provenance tests and the orchestrator provenance tests, including its injection table) so they patch the function `start_run_provenance` now calls, and update the tests that pin the old fingerprint.
  - Remove the markers of 9.1 that cover planning and fingerprints.
  - Observable: the planning and fingerprint tests of 9.1 pass without markers and the full fast suite is green.
  - _Requirements: 7.1, 7.2, 7.3, 8.3, 8.4_
  - _Depends: 9.1_
  - _Boundary: RunLifecycle_

- [ ] 9.3 Implement the instance directory, run header, resumed references and orphan detection at run start
  - Compute the instance directory and make the run end use it, discover earlier artifacts and shards of resumed documents from the sibling directories as they exist, build the extensions and the resumed-document records, detect orphan sibling instances and log the explicit recovery command for each, construct the recorder with its context, write the run header (a failure disables shards for the run with a warning), and let a run with no processed document proceed to an artifact.
  - Re-point the tests that this changes, as listed in the design: the artifact path helper and every use of it in the orchestrator provenance tests, the summary-path assertions in the run-provenance tests, the end-to-end test's path monkeypatch and summary assertions, and the empty-run tests that asserted no artifact or no run plan (all-resumed and unreadable-only runs now write an artifact with no nodes and log no warning; start failures still return no plan).
  - Remove the markers of 9.1 that cover the header, pointers, unrecorded documents, empty runs and the guard.
  - Observable: the corresponding tests of 9.1 pass without markers, the start-up INFO line names the instance directory and the counts, and the full fast suite is green.
  - _Requirements: 7.4, 7.5, 7.6, 8.1, 8.2, 8.3, 8.5, 8.6, 9.1, 9.6, 10.4_
  - _Depends: 9.2_
  - _Boundary: RunLifecycle_

- [ ] 9.4 Write a shard when each document finishes and pass local configuration and manifest through
  - Add the per-document finish hook (takes a per-fingerprint lock around reading the coverage flag from the manifest, copying the document's records and writing the shard; degrades on any error), call it from a `finally` in the orchestrator's per-document function through a worker thread, and pass the local configuration to planning and the manifest to the run end.
  - Remove the remaining markers of 9.1.
  - Observable: the remaining tests of 9.1 pass without markers, the orchestrator provenance tests (as re-pointed in 9.3) pass, and with provenance disabled the orchestrator makes exactly the pre-provenance calls.
  - _Requirements: 2.8, 4.11, 9.2, 9.3, 9.6, 9.7, 9.8, 9.10, 14.1_
  - _Depends: 9.3_
  - _Boundary: RunLifecycle_

- [ ] 9.5 Add the crash-simulation and end-to-end tests
  - Run a scripted multi-document run, stop it after some documents finished (an exception in the next document's processing and a run end that fails), recover the artifact from the shards, and compare with the full normal run for the finished documents and with the documented gaps; check that the next run's start only logs the recovery command, byte-identity of the recovered artifact when all documents finished, and that shards written before a simulated out-of-memory failure are intact.
  - Observable: the tests pass without real services and the recovered artifact equals the normal one apart from the recovery record.
  - _Requirements: 10.2, 10.4, 10.5, 13.3, 14.2, 14.3, 16.3_
  - _Depends: 9.4_
  - _Boundary: TestsAndGuards_

- [ ] 10. Verification, guards and measurements

- [ ] 10.1 Update the property tests and add the new properties
  - Add strategies for the new records to the shared hostile-snapshot strategy; add properties for claim-count invariants, finding attribution, shard round trips, streamed text equality and carrier attachment, keeping the existing properties and their derandomised settings.
  - Observable: the property modules pass at their configured example counts in the fast suite.
  - _Requirements: 16.2, 16.4_
  - _Depends: 9.5_
  - _Boundary: TestsAndGuards_

- [ ] 10.2 Update the end-to-end test and sweep the guards
  - Extend the provenance end-to-end test to the new keys, the stage object and the counts; run the stage-contract drift guard, the import-isolation and hashing guard, the label scan, the prefix goldens and the dependency-direction tests and record in the commit body each guard change and why.
  - Observable: all guards pass; the prefix goldens are byte-for-byte unchanged by this feature; a diff review shows no weakened assertion.
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 16.4_
  - _Depends: 10.1_
  - _Boundary: TestsAndGuards_

- [ ] 10.3 Add the scale measurements and refresh the documented estimates
  - Add slow-marked tests that record artifact bytes and peak encoding memory per document for several corpus sizes, assert the encoding transient is independent of size, and print the figures used in the documentation.
  - Observable: the slow tests pass, and the measured per-document size is compared with the estimate in the design; a difference of more than 25 percent is written into the research log before the documents are finalised.
  - _Requirements: 6.10, 11.2, 11.7_
  - _Depends: 10.2_
  - _Boundary: TestsAndGuards_

- [ ] 11. Documentation, change record and notification

- [ ] 11.1 (P) Document the 2.0.0 artifact, the instance directory, shards, recovery and the policy in the package READMEs
  - Provenance README: modules, the artifact shape and compatibility rule, the 2.0.0 additions, the identities and their purposes, stage statuses, the completeness policy, scope and the findings table, absence terms with the `ids_cited` wording, the recovery command with `PYTHONPATH=src`, its liveness and replace options, what a recovered artifact cannot contain, that recovery needs the same memory as a normal assembly and does not solve memory exhaustion in the analysis at very large corpora, the shard and cleanup policy, sizes and memory. Pipeline README: lifecycle, no retained output at exhaustion, failure reasons. Configuration README and utilities README: the instance-directory path.
  - Observable: a grep of the touched READMEs finds no statement that provenance retains an output, that completeness ignores findings, or the old single-file path.
  - _Requirements: 1.6, 1.7, 3.12, 5.7, 6.10, 7.6, 10.7, 11.7, 17.2_
  - _Depends: 10.3_
  - _Boundary: DocsReadmes_

- [ ] 11.2 (P) Add the CHANGELOG entry
  - One entry following the changelog rules: the schema release, behaviour changes (completeness semantics, failed rather than retained documents, instance directories, shards, recovery, fingerprint scope, the explicit scope and the support value), the estimated growth in size and memory, the measured test impacts, and the published interfaces.
  - Observable: the entry exists under the current month heading, earlier entries are untouched, and every item above is named.
  - _Requirements: 17.1_
  - _Depends: 10.3_
  - _Boundary: DocsChangelog_

- [ ] 11.3 (P) Sync steering and the project instructions
  - Configuration, testing, product and roadmap steering and the project instructions file: the artifact path, the instance directory, the recovery command, the new modules, the dependency rules for the new files, the test modules and conventions, the roadmap order, and the corrected statement about module invocation needing `PYTHONPATH`.
  - Observable: the steering files and the project instructions agree with the code and the READMEs.
  - _Requirements: 17.2_
  - _Depends: 10.3_
  - _Boundary: DocsSteering_

- [ ] 11.4 (P) Add the dated revision notes to the baseline spec
  - Add a "Revision 2026-10 (provenance-schema-v2)" block to the `provenance-core` requirements, design (Out of Boundary retention statement, Compatibility rule, schema example, completeness description) and tasks (task 6.4 observable, Implementation Notes) pointing to the Supersedes table; rewrite no history.
  - Observable: each of the three files carries the block and no earlier text is altered.
  - _Requirements: 17.3_
  - _Depends: 10.3_
  - _Boundary: DocsBaselineSpec_

- [ ] 11.5 (P) Update the downstream specs' upstream notes
  - Replace or extend the upstream notes of `provenance-audit-export` (stage status serialised; artifact path; empty runs), `privacy-core` (attach path, decision identifier), `public-private-provenance` (new events, carrier on the validation record), `cost-and-run-reporting` (resumed documents, resume gate), `evidence-routing` and `multiagent-extraction` (stage order with the evidence-selection stage, skipped marks, shard behaviour), `reviewer-ui` (instance path, `human` origin) and `evaluation-harness` (asserted-only denominators) with the facts listed in the design; change none of their requirements, decisions or tasks.
  - Observable: every listed spec carries the updated note and `git diff` shows only note blocks changed.
  - _Requirements: 17.4_
  - _Depends: 10.3_
  - _Boundary: DocsDownstream_

- [ ] 11.6 (P) Update the repository README
  - Add the new provenance modules (shards, run end, recovery) and the instance directory to the file tree and the module table of the root README, and state the recovery command.
  - Observable: the root README names every new provenance module and the instance-directory layout.
  - _Requirements: 17.2_
  - _Depends: 10.3_
  - _Boundary: DocsRootReadme_

- [ ] 12. Final validation

- [ ] 12.1 Run the full validation and reconcile
  - Run the fast suite, the slow suite, the dependency-direction tests and the spec and steering tests from the repository root; confirm no expected-failure marker remains; confirm every requirement id of this spec is cited by a task; confirm the Supersedes rows are reflected in the baseline revision notes; confirm no configuration key and no third-party dependency was added; confirm the differential test shows identical extraction with provenance on and off; record the run as a validation note appended to this file with the commands and results; leave any real-run measurements (absence rates, memory) as open items.
  - Observable: all suites pass, `git status` shows changes only in the files named by the design, and the validation note lists the commands and results.
  - _Requirements: 15.3, 16.1, 16.4, 17.1_
  - _Depends: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_
  - _Boundary: TestsAndGuards_
