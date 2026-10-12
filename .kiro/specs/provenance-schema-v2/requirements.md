# Requirements Document

## Introduction

`provenance-core` shipped the provenance artifact (schema `evitrace/provenance-graph` 1.0.0, validated GO on 2026-10-11). A read-only post-validation review (`investigation-2026-10-12.md`, items W1-W9) showed that the artifact and its completeness semantics are not yet credible enough for audit use:

- A model's explicit "not reported" answer is classified like an unsupported assertion, so document and run completeness read `incomplete` for any realistic rate of such answers (at 1% of fields, 18 of 40 measured documents read complete; at 5% or more, none).
- Completeness ignores findings (a critical cycle or dangling reference leaves a document `complete`) and ignores stages that never ran; stage status is not in the artifact, so the artifact cannot be interpreted without the pipeline.
- With provenance enabled, an output whose repairs were exhausted on reference-only problems is retained, so provenance changes what is extracted.
- A resumed or empty run leaves no trace of the documents it did not process, an artifact can silently overwrite an earlier one with the same run identity, and the run identity names a configuration, not an execution.
- The end-of-run serialisation can exhaust memory at about a thousand documents after all extraction has finished, losing the run's provenance, and per-document snapshots are quadratic.
- Privacy-core has no designed way to put a carrier on a recorded node, and the evidence the model was actually shown is not recorded.

The owner's governing instruction is that cost and time of work are irrelevant and that accuracy and scientific credibility supersede them. This feature therefore delivers **one deliberate schema release, 2.0.0**, batching every schema change, and the behaviour changes that make the artifact trustworthy: typed absence answers and honest completeness, serialised stage status, a provenance observer that never alters extraction, model-visible evidence lineage, separate run and run-instance identities, durable per-document shards with crash recovery, a streaming artifact write, and a privacy-carrier attach path. Version 1.0.0 is treated as never released (nothing consumed it); readers support major 2 only.

### Terms used below

- **Artifact**: the single provenance file a run writes (graph of sources, evidence, claims, derivation steps and validation records, plus run state and reports).
- **Stage**: a declared pipeline step whose records are contractually expected. **Stage status** is one of `not_run` (never marked), `complete`, `failed`, `skipped` (intentionally not run, with a reason). A stage is **required** for a document when its contract has document or claim scope. The stages **upstream** of a stage are the declared stages that precede it in the declared stage order and have document or claim scope.
- **Asserted claim / not-reported claim / absence-like claim**: a claim whose field carries a value the extraction contract requires evidence for / a claim whose field is a not-reported answer / a claim whose field is a category value that the field's own category text lists as an absence phrase (for example "not stated") and that the contract exempts from mandatory citation. A not-reported or absence-like claim is `ids_cited` when it cites at least one evidence node of the run and `no_ids` otherwise. `ids_cited` means that evidence was shown to the author of the answer and cited; it never means that the author examined it or that the pipeline verified the absence, and cited ids on an absence-valued answer record where the author looked, not where an absence is stated.
- **Origin mark / absence-like marker**: the persisted markers the extraction contract attaches to a field: `nr_origin` (`model`, `pipeline_empty_value`, `pipeline_prefill` or `human`), present exactly on the not-reported fields, and the boolean `absence_like`, present exactly on the absence-phrase values. Provenance reads the marks and never interprets a field's value text.
- **Shown set**: the evidence ids contained in the evidence package actually sent with one model call, published per call by the extraction contract.
- **Run identity / run-instance identity**: the identity of a configuration (configuration, schema and model identifiers) / the identity of one execution of it over one set of processed documents.
- **Processed / resumed document**: a document this run recorded / a corpus document this run skipped because it was already complete.
- **Shard**: the durable per-document file holding one finished document's provenance records. **Instance directory**: the directory holding one run instance's run header, shards and artifact. **Recovery**: assembling an artifact from a run header and shards after a run did not finish normally.

## Boundary Context

- **In scope**: schema 2.0.0 and its reader/writer contract; stage status and the stage-contract table in the artifact; the completeness policy (skipped stages, never-marked stages, findings, claim counts); typed absence answers, claim origins and absence findings; removal of the exhaustion-retain behaviour and the reinterpretation of `provenance-core` Requirement 5.4; model-visible evidence lineage; run and run-instance identity and the configuration-fingerprint scope; artifact location and the no-silent-overwrite guard; resumed, unrecorded and empty runs; durable per-document shards and recovery; the streaming artifact write; the privacy-carrier attach path and the decision identifier; the tests, goldens, guards and documentation that go with them.
- **Out of scope**: privacy classification, labelling and disclosure logic (`privacy-core`); audit-package and export formats (`provenance-audit-export`); the reviewer interface; the extraction contract itself - shown-set computation, the not-reported token, prompt text (`extraction-contract-hardening`); fixing cross-invocation resume (`cost-and-run-reporting`) - this feature only makes provenance honest about it; any tolerance policy for bad evidence ids in extraction; reading a very large artifact incrementally (a consumer concern).
- **Adjacent expectations**: `extraction-contract-hardening` is implemented first and publishes the per-call shown set, the origin mark on not-reported fields, the absence-like marker, the `ids_cited`/`no_ids` classes, the contract version and the fixed not-reported semantics; this feature consumes them and never re-derives or re-interprets them. `provenance-core` stays the baseline; where this feature supersedes one of its criteria it says so (table at the end). Downstream specs that read the artifact (`provenance-audit-export`, `reviewer-ui`, `public-private-provenance`, `privacy-core`, `cost-and-run-reporting`, `evidence-routing`, `multiagent-extraction`, `evaluation-harness`) are notified of what changes for them; they are not redesigned here.

## Requirements

### Requirement 1: One honest schema release

**Objective:** As a consumer of provenance artifacts, I want the schema change declared once, versioned honestly and refused by readers that cannot interpret it, so that no consumer silently misreads or loses data.

#### Acceptance Criteria

1. The provenance subsystem shall write every artifact with the schema identity `evitrace/provenance-graph` and the schema version `2.0.0`.
2. If a reader is given an artifact whose schema major version is not 2, or whose schema version is absent or not a semantic version, then the provenance subsystem shall refuse it with an error naming the version found and the versions supported, and shall materialise no part of the artifact.
3. The provenance subsystem shall provide no converter, compatibility mode or lenient reading for a version 1 artifact, because version 1.0.0 was never released or consumed.
4. The artifact shall carry these top-level keys, in this order: schema identity, schema version, run identity, run-level extensions, stage status, resumed documents, nodes, edges, missing segments, validation report, completeness report.
5. When a reader reads an artifact of a supported major version that contains a top-level key it does not know, the reader shall preserve that key and its value in the run-level extensions under the reserved compatibility key and shall log one warning naming the key.
6. The compatibility rule shall be stated once, in the schema documentation and in the reader's warning text, as: adding a key where the artifact has a free-form slot (the run-level extensions, node extensions, edge attributes, finding details) or adding a top-level key is a minor change; adding a key to any other record (the run identity, the stage-status object, the claim counts, a completeness entry, a missing segment, a report container), adding or removing a member of a closed vocabulary, removing or renaming a key, or changing the meaning of a key is a major change.
7. The schema documentation shall list every vocabulary member and key added by 2.0.0: the claim answer and absence states, the `not_applicable` claim support, the `pipeline` claim origin, the run completeness scope, the `skipped` stage status, the evidence-selection stage, the new finding codes, the privacy decision identifier, the run-instance identity, the stage-status object, the run-level extensions and the resumed-documents list.
8. The test suite shall pin a literal 2.0.0 artifact for a small graph, and shall pin that a 1.0.0 artifact produced by the previous writer is refused.
9. When the provenance subsystem writes a graph, it shall stamp its own schema version, never the version the graph was read with, so that an artifact whose unknown keys were relocated into the extensions is not rewritten under a newer minor version it does not implement.
10. The provenance subsystem shall reject, when reading or constructing a graph, a run-level extension key that does not follow the namespaced extension-key convention, in the same way it does for node-level extensions.

### Requirement 2: Stage status is part of the artifact

**Objective:** As an auditor reading an artifact without the pipeline, I want to see which stages ran, were skipped or failed, and why, so that the artifact is interpretable on its own.

#### Acceptance Criteria

1. The artifact shall state, for the run and for each processed document, the status of every declared stage as `not_run`, `complete`, `failed` or `skipped`, writing `not_run` explicitly for a stage that was never marked.
2. The artifact shall state every distinct failure reason of a failed stage and every skip reason of a skipped stage, in the order recorded.
3. The artifact shall state the stage order and the stage-contract table (stage, required record kinds, scope) that completeness was computed against.
4. When a stage is marked skipped and later marked complete or failed for the same scope, the status shall become complete or failed; a skip mark shall never replace a complete or failed status.
5. While a document is processed, if the pipeline positively knows that a stage does not apply to it (for example no field needs a model call, or synthesis is not required), then the pipeline shall mark that stage skipped with a reason naming why; the pipeline shall not mark a stage skipped merely because it found the stage unmarked, so that an emitter that silently returns stays detectable.
6. The stage status in the artifact shall be the final status at the end of the run, so that the chain-validation stage reads `complete` when validation ran to the end and `failed` with its reason when it did not.
7. When the artifact contains a processed document that produced no records, the artifact shall still contain that document's stage status.
8. The artifact's per-document stage status shall be shared by every document with the same content fingerprint, and the pipeline shall document that a failure recorded for one of two identical-content documents marks both.

### Requirement 3: Completeness reflects what did not happen

**Objective:** As a workflow operator, I want completeness to degrade when a stage was forgotten or a serious finding exists, so that a complete reading can be trusted.

#### Acceptance Criteria

1. If a required stage was never marked for a document and every stage upstream of it is complete or skipped for that document, then the provenance subsystem shall report that document undetermined, with a reason naming the unmarked stage.
2. While any stage of a document is failed, or a stage upstream of an unmarked stage is not run or itself unmarked, the provenance subsystem shall not add a gap for the unmarked stage, because the failure or the upstream condition already explains it and a failure can legitimately cut later emitters short.
3. While a stage is skipped for a document, the provenance subsystem shall require no records of it and shall not degrade the document because of the skip.
4. If the run-scoped chain-validation stage was never marked while the run contains processed documents, then the provenance subsystem shall report the run undetermined with a reason naming the stage.
5. If a finding whose effective severity is critical or error is attributed to a claim, a document or the run, then the provenance subsystem shall report that artifact incomplete with a reason naming the finding code; a document shall be at least as severe as its claims and the run at least as severe as its documents.
6. The provenance subsystem shall attribute a finding to the claim it names; else to each document of the node it names (a derivation step to every document it touches); else to the document named in its details; else to the document of the edge it names; else to the run.
7. A finding of warning or info severity shall not change any completeness state by itself; completeness gaps from missing support, missing anchors or missing records are reported as before and independently of findings.
8. The set of findings that degrade completeness shall be exactly the findings the validation report classifies critical or error, so that the validation report and the completeness report cannot disagree about what is serious.
9. Where a severity override lowers a finding code below error, the finding shall not degrade completeness, and the artifact shall state the severity overrides and the strictness setting in effect.
10. The configured fail-run severity shall not change any completeness state, and completeness shall not change whether the fail-run severity is reached.
11. When two processed documents have identical content and a later record for the shared document conflicts with the first, the provenance subsystem shall report the shared document incomplete with a reason naming the duplicate identity, shall log a warning naming both document names, and shall not alter either document's extracted output.
12. The documentation of the provenance subsystem shall no longer state that completeness is independent of findings.

### Requirement 4: Absence-valued answers are typed and quantified

**Objective:** As an evaluator, I want an explicit "not reported" answer and a category-valued absence to be told apart from an unsupported assertion and from a pipeline fallback, and I want absences counted rather than hidden, so that completeness stays meaningful and a genuinely uncited assertion stays visible.

#### Acceptance Criteria

1. The provenance subsystem shall record on every claim whether it is an asserted value, a not-reported answer or an absence-like value and, for the latter two, whether it is `ids_cited` or `no_ids`.
2. The provenance subsystem shall decide that a claim is a not-reported answer only from the origin mark and that it is an absence-like value only from the absence-like marker, and shall never inspect the value text of a field.
3. The provenance subsystem shall record the origin of a claim as one of `model`, `prefill`, `pipeline`, `human` or `import`, mapping the origin mark `model` to `model`, `pipeline_prefill` to `prefill`, `pipeline_empty_value` to `pipeline` and `human` to `human`.
4. If a field carries an origin mark outside the known vocabulary, or carries both an origin mark and the absence-like marker, then the provenance subsystem shall treat the claim as an asserted value, shall use the origin supplied by the caller, and shall log a warning naming the field and the mark.
5. When a model-origin not-reported answer cites no evidence node that exists in the run, the provenance subsystem shall record it as `no_ids` and raise an informational finding `absence_without_ids` whose severity is overridable; when it cites at least one such node it shall record it as `ids_cited` and raise no finding for it.
6. The provenance subsystem shall not report a not-reported or absence-like claim as unsupported and shall not raise `unsupported_claim` or `prefill_unsupported` for it, and shall not raise `absence_without_ids` for an absence-like claim, a prefill-origin claim or a pipeline-origin claim.
7. The provenance subsystem shall not report a gap for missing support, a missing anchor or missing ids on a not-reported claim or an absence-like claim, and shall continue to report an asserted claim without usable evidence incomplete with its existing reason; a critical or error finding attributed to an absence-valued claim (for example a dangling reference) degrades it like any other claim (Requirement 3.5).
8. While every asserted claim of a document is supported and no critical or error finding applies to it, the document shall read complete however many of its fields are answered not-reported or with an absence phrase.
9. The completeness report shall carry, for the run and for each document, the number of final claims and, among them, the asserted claims split into supported, missing-anchor and unsupported, the absence-like claims, the model-origin `ids_cited` and `no_ids` not-reported claims, the not-reported claims produced by the pipeline itself (prefill or empty-value normalisation) and the human-origin not-reported claims, such that the parts add up to the total with no claim counted twice; a claim superseded by a later revision shall not be counted.
10. A model-origin not-reported answer whose cited ids resolve to no evidence node of the run shall be counted as `no_ids`.
11. Where the pipeline supplies, for a processed document, whether its recorded evidence coverage was below the configured threshold, the completeness report shall carry that indicator with the document's counts and shall mark the run's counts when any processed document was below coverage; where it supplies nothing, the indicator shall be explicitly unknown.
12. A claim whose origin is `prefill` and is only unsupported or missing an anchor shall remain reported at claim level only, as before; any critical or error finding on it shall aggregate to its document like any other.
13. The provenance subsystem shall give the claim support value `not_applicable` to a not-reported or absence-like claim that cites no evidence node of the run, shall give a not-reported or absence-like claim that cites evidence nodes the support computed by the usual anchor rule, and shall reserve `unsupported` for asserted claims, so that a consumer that ignores the answer type cannot read an absence as an unsupported assertion.

### Requirement 5: Provenance observes and never decides

**Objective:** As a maintainer and an auditor, I want extraction to be identical whether or not provenance is recorded, so that the observer never changes what it observes.

#### Acceptance Criteria

1. When the same model responses are supplied, the pipeline shall produce the same extracted outputs, manifest entries and model calls (arguments and prompts) whether provenance is enabled or disabled, including when repair attempts are exhausted.
2. If repair attempts are exhausted for any reason, including when only evidence-reference problems remain, then the pipeline shall fail the document exactly as it does with provenance disabled, shall write no output for it, and shall record the existing manifest failure.
3. When a document fails in the extraction chunks or the synthesis call, the provenance subsystem shall record that stage as failed for that document with a reason that carries the validator's violation codes and message naming the offending items and evidence ids, shall read the document undetermined, and shall record no claims for it.
4. When more than one chunk of a document fails, the recorded reason shall identify each failed chunk.
5. The recorded failure reason shall hold at least 4000 characters and, when it must shorten a longer text, shall end with an explicit marker stating how many characters were omitted.
6. When an accepted output cites one evidence reference twice, or cites a reference the claim adapter finds ambiguous, the provenance subsystem shall retain the claim, cite each node once, and record an issue naming the claim, the reference and the kind of issue.
7. The operator documentation and the code documentation (module and function docstrings and comments) shall no longer describe any behaviour in which provenance retains an output that extraction rejected.

### Requirement 6: The evidence the model could see is recorded

**Objective:** As a reviewer, I want to know which evidence reached the model and which was withheld, so that a claim's grounding and the absence of a citation can be judged against what was available.

#### Acceptance Criteria

1. When the pipeline makes at least one model call for a document, the artifact shall record, in a declared evidence-selection stage, one derivation step whose inputs are the document's evidence nodes, whose outputs are the evidence nodes in the paper-level evidence package, and whose removed identifiers and removal reason name the nodes left out and the selection limits in effect.
2. When a call's evidence package was reduced to meet a token budget, the artifact shall record a further derivation step in the same stage whose inputs are the package nodes, whose outputs are the nodes actually sent, and whose removed identifiers and removal reason name the nodes withheld by the reduction.
3. The artifact shall record each extraction-chunk step and each synthesis step with, as inputs, the evidence nodes in the shown set of its own call (not only the cited ones) and, as outputs, the claims it produced; the provenance subsystem shall take the shown set from the pipeline's per-call record and shall not recompute it.
4. The artifact shall record on each model step the digest of the evidence package text sent with its call.
5. If a claim cites an evidence node that is not among the evidence inputs of the model step that produced it, then the provenance subsystem shall record an error-severity finding naming the claim and the node.
6. If the pipeline positively knows that no field of a document needs a model call, then it shall mark the evidence-selection stage skipped for it with a reason; if a model call was made but the document has no evidence items to select from, it shall mark the stage failed with a reason naming that.
7. If the evidence package of a call could not be read, so that its shown set is empty because of that, then the pipeline shall mark the evidence-selection stage failed for the document with a reason naming the call, and the document shall read undetermined.
8. When a document leaves the model stages after at least one call by any route (success, an exhausted extraction chunk or synthesis call, a failed output validation or write, or an unexpected exception), the pipeline shall still record the evidence-selection and token-budget steps of the calls that were made.
9. Recording the lineage shall not change evidence selection, evidence serialisation, the evidence cache or any prompt.
10. The documentation shall state the expected growth of the artifact caused by the lineage per document at the default selection limits, derived from the measured artifact composition.

### Requirement 7: Run identity and run-instance identity

**Objective:** As an auditor, I want to cite one execution unambiguously and to know that two artifacts of the same configuration are not mistaken for each other, so that an artifact is never silently replaced.

#### Acceptance Criteria

1. The provenance subsystem shall record, in addition to the run identity, a run-instance identity derived from the run identity, the fingerprints of the processed documents and the creation time, in the artifact and in every shard.
2. Two executions of the same configuration shall have equal run identities, and shall have different run-instance identities unless they process the same documents and start within the same second.
3. The configuration fingerprint shall additionally cover the value-sanitisation setting, the exported-value normaliser and the extraction contract version, and shall change when any of them changes; its other exclusions shall be unchanged.
4. The pipeline shall write the artifact into the instance directory named by the run-instance identity.
5. If an artifact already exists at the location the pipeline would write and it belongs to a different run instance, then the provenance subsystem shall refuse to replace it, shall keep it unchanged, and shall log a warning naming the existing file and both run-instance identities.
6. The documentation shall state which identity serves which purpose: the run identity groups runs by configuration, the run-instance identity names one execution, its artifact and its directory, a document fingerprint names a document, and the run folder name is none of these.

### Requirement 8: Resumed, unrecorded and empty runs

**Objective:** As an operator and an auditor, I want a run that did not process every document to say which it left out and why, so that no document silently has no trace.

#### Acceptance Criteria

1. The artifact shall list every resumed document with its document name, document fingerprint, output path, the extraction contract version recorded for it (or none), the reason it was resumed, and a reference to the earlier artifact and to the earlier shard that hold its provenance, each with a relative path, a digest and the run-instance identity of the earlier run.
2. If no earlier artifact or shard of a resumed document can be found, then the artifact shall state that explicitly for that document and shall not infer a reference.
3. The artifact shall list, in the run-level extensions, every corpus document that was left out of provenance for a reason other than resuming (no usable fingerprint, a second document with the same name) together with that reason.
4. The set of resumed documents shall be decided by the same test the pipeline uses to skip a document, so that the two cannot drift.
5. If no document is processed in a run, then the pipeline shall still write an artifact with no nodes, a run completeness of undetermined, and a reason stating that no document was processed together with the number of resumed and of unrecorded documents.
6. While a run processes some documents and resumes others, the run's completeness shall describe the processed documents only, and the artifact shall not state or imply that the resumed documents are complete.
7. The run completeness entry shall state a scope, `full` when every corpus document was processed and recorded in this run and `partial` when any document was resumed or left unrecorded, so that a run that reads complete cannot be mistaken for a complete corpus.

### Requirement 9: Durable per-document shards

**Objective:** As an operator running large corpora, I want each finished document's provenance made durable at once, so that a crash or an out-of-memory kill after extraction cannot lose the run's provenance.

#### Acceptance Criteria

1. Before the first document of a run starts, the pipeline shall write a run header into the instance directory holding the run identity, the run-instance identity, the run-level extensions and the resumed documents.
2. When a document finishes in the pipeline, whether it succeeded, failed or raised an unhandled error, the pipeline shall write that document's shard before the run ends.
3. A shard shall contain the document's identity, its nodes, recorded edges and recorded findings, and its stage status, and shall contain no run-level data and no clock value.
4. The pipeline shall write every shard and the run header atomically, so that a reader finds the previous file or the new one and never a partial file, and a failed write shall leave the previous file in place.
5. Equal records shall produce byte-identical shard bytes.
6. If a shard or the run header cannot be written, then the pipeline shall log a warning, shall continue the run, and shall still produce the end-of-run artifact from the records it holds.
7. While a shard is being written, the pipeline shall not prevent other documents from recording their provenance for longer than it takes to copy the finished document's records.
8. The pipeline shall never delete a shard, a run header or an artifact; the documentation shall state that an instance directory may be removed as a unit and what that means for references from later runs.
9. If the process is killed after a shard was written, then that shard shall be unchanged and readable.
10. When two documents with identical content share one shard, the pipeline shall serialise taking the document's records and writing its shard, so that an older state never replaces a newer one, and shall accept a shard file name only when it is a safe file name (a bounded length of letters and digits).

### Requirement 10: Recovery from shards

**Objective:** As an operator, I want to regenerate the run's artifact after a crash, so that already-extracted documents keep their provenance.

#### Acceptance Criteria

1. The provenance subsystem shall provide a command-line entry point that assembles the artifact of an instance directory from its run header and shards, using the same validation, completeness and write sequence as a normal run and the severity overrides and strictness recorded in the run header.
2. When a document planned for the run has no shard, the recovered artifact shall contain that document with no records and read it undetermined, and shall record the recovery in the run-level extensions with the number of shards used and the documents without a shard.
3. If the instance directory already contains an artifact, then the recovery shall refuse to replace it and shall report the existing file, unless the operator explicitly asks to replace an artifact of the same run instance.
4. When a run starts and finds, in the same provenance directory, an instance directory that has a run header and shards but no artifact, the pipeline shall not recover it automatically; it shall log one message per such directory naming the explicit recovery command, and shall not let the finding affect the new run.
5. When every planned document of a crashed run has a shard, the recovered artifact shall equal the artifact the normal path would have written, apart from the recovery record.
6. The recovery shall need no model-provider credential, no GROBID service and no network access, and shall not start the extraction pipeline.
7. The documentation shall state what a recovered artifact cannot contain (run-wide stage marks, findings that name no document, privacy carriers attached at run end unless a carrier source is supplied to the recovery), and that recovery needs the same memory as a normal assembly and therefore does not help when the analysis itself runs out of memory at very large corpus sizes.
8. If the files of an instance directory were modified more recently than a documented threshold, so that the run may still be in progress, then the recovery shall refuse to run unless the operator explicitly overrides it.

### Requirement 11: Scalable, safe artifact write

**Objective:** As an operator, I want the artifact write to be memory-bounded and safe, so that a large run neither runs out of memory at the end nor leaves a broken file.

#### Acceptance Criteria

1. The artifact bytes shall be identical to the bytes of the complete, two-space-indented JSON encoding of the same graph, with a trailing newline.
2. While the artifact is being written, the additional memory held for encoding shall not grow with the number of documents.
3. If a graph holds a value that cannot be written as strict JSON, then the provenance subsystem shall fail the write before creating any directory or temporary file, shall leave an existing artifact unchanged, and shall name the offending path in the error.
4. If the operating system fails during the write, then the provenance subsystem shall remove its temporary file, shall leave an existing artifact unchanged, and shall fail with an error naming the path.
5. If the bytes written differ from the bytes the validation pass encoded, then the provenance subsystem shall discard the temporary file and fail the write.
6. When the pipeline reads run-wide information while documents are being recorded (the run identity, whether a source exists, one document's status), it shall do so without copying the records of other documents.
7. The documentation shall state the measured per-document artifact size and memory growth of the previous release and the estimated growth of this release.

### Requirement 12: Privacy carriers can be attached

**Objective:** As the owner of the privacy component, I want a defined way to put privacy labels and decisions on recorded nodes, so that provenance carries them without ever deciding anything.

#### Acceptance Criteria

1. The provenance subsystem shall provide an operation that, given a graph and a mapping from node identifiers to privacy carriers, returns a new graph in which those nodes carry those carriers, and shall not modify the graph it was given.
2. If a supplied carrier equals the one a node already carries, then the operation shall leave the graph unchanged, so that applying the same mapping twice gives an equal graph.
3. If a supplied carrier differs from a carrier a node already carries other than the unknown carrier, then the operation shall apply none of the mapping and shall fail with an error naming the node identifier.
4. If the mapping names an identifier that is no node of the graph, then the operation shall ignore it and log one warning naming the number of such identifiers and the first few.
5. The operation shall not interpret a carrier's label or decision value in any way, and shall decide equality and conflict only by comparing carriers as opaque whole records.
6. The privacy carrier shall be able to hold a decision identifier that ties the node to the privacy audit record, stored verbatim; a carrier whose decision identifier is not a string, or is given without a decision, shall be rejected as non-conforming.
7. Where a carrier source is supplied at run end, the pipeline shall ask it for carriers and apply them before validation, and shall ask again and apply the result after the validation report is attached, so that the validation record can carry one.
8. If a carrier source is absent, then the artifact shall be byte-identical to an artifact written without this capability; if it fails or raises, the pipeline shall log a warning, apply no carriers from that call, and change nothing else.
9. If a carrier is structurally rejected, then the operation shall attach it before validation so that validation reports it, and shall refuse it after validation with a warning naming the node.
10. The operation shall accept every node kind, including the validation record; which nodes receive which carrier is decided by the privacy component.

### Requirement 13: Determinism

**Objective:** As an auditor, I want equal inputs to give equal bytes, so that artifacts and shards can be compared and their digests trusted.

#### Acceptance Criteria

1. The same graph shall always produce the same artifact bytes, in any process and in any thread order.
2. The same document records shall always produce the same shard bytes.
3. The artifact assembled from memory and the artifact assembled from the shards of the same run shall be byte-identical apart from the recovery record.
4. The order of keys, nodes, edges, stages, documents and findings in the artifact shall not depend on the order in which concurrent documents finished.

### Requirement 14: Provenance never aborts extraction

**Objective:** As an operator, I want every provenance failure to degrade with a warning, so that extraction results are never lost to the observer.

#### Acceptance Criteria

1. If any provenance operation raises (recording, marking, shard writing, run-end assembly, recovery, carrier source), then the pipeline shall log a warning, continue, and leave extraction outputs and the manifest unchanged.
2. If a memory error occurs while the run-end artifact is assembled, then the pipeline shall log it and shall not lose any shard already written.
3. If the run-end assembly fails entirely, then the shards written so far shall remain on disk and the log shall name the recovery entry point.

### Requirement 15: Prompt-cache stability and boundaries

**Objective:** As a maintainer, I want this feature to leave prompts, selection and package dependencies untouched, so that cost, caching and architecture rules keep holding.

#### Acceptance Criteria

1. The shared paper prefix shall remain byte-identical across warm-up, extraction chunks and synthesis of one paper, and shall contain no value introduced by this feature (no identifier, digest, count or stage name).
2. The provenance package shall import no pipeline, quality-control, agent or PDF-extraction code, and no other package shall import provenance except the pipeline.
3. The feature shall add no top-level configuration key and no new third-party dependency.
4. Every new function that takes a stage name shall be registered in the stage-contract drift guard, and the new evidence-selection stage shall be declared in both stage tables and in the guard's inventories.

### Requirement 16: Verification without real services

**Objective:** As a maintainer, I want the behaviour pinned by tests that need no model, no parser service and no network, so that it can neither regress nor be overclaimed.

#### Acceptance Criteria

1. The test suite shall include a differential test that runs the pipeline with provenance enabled and disabled on the same scripted responses - including a response sequence that exhausts repair - and compares extracted outputs, manifest entries and model calls.
2. The test suite shall include property-based tests of the claim counts, the finding-to-completeness attribution, the shard round trip, the streamed-versus-complete encoding and the carrier attach operation.
3. The test suite shall include a crash-simulation test that stops a scripted run after some documents finished and recovers the artifact from the shards.
4. Every existing guard, golden and property test that this feature makes obsolete shall be updated deliberately in the change that makes it obsolete, and none shall be weakened or deleted without a recorded reason.
5. Fixtures shall use synthetic identifiers and text only, and no real GROBID, model-provider or OCR service shall be called.

### Requirement 17: Change record and notification of consumers

**Objective:** As a maintainer of downstream specs and as an operator, I want the changes recorded and each affected consumer told what changed, so that nothing depends on a behaviour that no longer exists.

#### Acceptance Criteria

1. The change record shall state the schema release, the behaviour changes visible to operators (document completeness semantics, failed-rather-than-retained documents, the instance-directory layout, shards, the recovery command, the configuration-fingerprint scope) and the expected growth of artifact size and memory.
2. The documentation of the provenance and pipeline packages, the configuration reference, the steering documents and the project instructions shall describe the 2.0.0 artifact, the instance directory, the shards, the recovery entry point, the identities and the completeness policy.
3. The `provenance-core` design and requirements shall carry a dated revision note that points to the criteria this feature supersedes, without rewriting history.
4. Each downstream spec whose recorded expectation this feature obsoletes shall carry an updated upstream note stating the new fact.

## Supersedes

How this feature relates to the `provenance-core` criteria it changes. Criteria not listed are unchanged.

| provenance-core | What changes | v2 |
|---|---|---|
| 2.1 stage emits declared records | A never-marked required stage is now a gap; a skipped stage requires nothing | 2.4, 2.5, 3.1, 3.3 |
| 2.2 completeness state at run level | The run entry also states whether the corpus was fully processed (`scope`) | 8.7 |
| 2.5 undetermined rather than complete | Extended to never-marked stages, failed documents with named reasons, empty runs | 3.1, 3.4, 5.3, 8.5 |
| 2.6 completeness as queryable data | Gains claim and absence counts and a coverage indicator; states the stage status and policy it used | 2.3, 3.9, 4.9, 4.11 |
| 3.5 / 3.6 run identity, one fingerprint set | Adds the run-instance identity; widens the fingerprint scope | 7.1, 7.3 |
| 5.2 claim without evidence is unsupported | Narrowed to asserted claims; not-reported and absence-like answers are typed, get support `not_applicable` when they cite nothing, and are quantified | 4.1, 4.5, 4.6, 4.7, 4.13 |
| 5.4 invalid reference recorded, claim retained | Reinterpreted: applies to references of accepted outputs; a rejected output fails the document and provenance records the failure | 5.2, 5.3, 5.6 |
| 5.7 non-model origin | Adds the `pipeline` origin and the mapping of the persisted origin mark (four values, `human` included) | 4.3 |
| 6.1 / 6.7 derivation tracking incl. removals | Completed for the evidence the model could see | 6.1, 6.2, 6.3 |
| 7.3 interpretable without pipeline internals | Completed by serialising stage status and the contract table | 2.1, 2.3 |
| 7.4 partial graph preserved | Extended across process death by shards and recovery | 9.2, 10.1, 10.2 |
| 7.5 schema identity and version | Version becomes 2.0.0 | 1.1 |
| 8.5 severity classification | Severity now also decides whether a finding degrades completeness | 3.5, 3.8, 3.9 |
| 9.2 carrier stored unmodified (partial in v1: no attach path) | An attach path and a decision identifier are defined | 12.1, 12.6, 12.7 |
| 10.5 earlier artifacts remain interpretable | Reversed for major changes: a reader supports its own major only; 1.0.0 was never released | 1.2, 1.3 |
| tasks 6.4 observable (unknown id retained after exhaustion) | Reworded: accepted-output reference issues are recorded; exhaustion fails the document | 5.2, 5.3, 5.6 |
