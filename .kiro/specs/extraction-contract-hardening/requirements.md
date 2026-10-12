# Requirements Document

## Introduction

EviTrace asks an extraction model for one record per field, `{i, v, loc, c}`: a field index, a value, the ids of the evidence items that support it, and a confidence. Three weaknesses make that contract weaker than it looks, and every downstream auditor (provenance, agreement statistics, evaluation, review) can be no more credible than the contract it audits:

- Cited evidence ids are checked against the paper's *entire* evidence index, while the model is shown only a selected, budget-reduced subset of it. On the three checked-in GROBID fixtures at the default caps, 79% (arXiv), 41% (PLOS ONE) and 18% (bioRxiv) of the ids are never shown to the model; a claim "grounded" in such an id passes validation, and nothing records what the model could see.
- "Not reported" has no defined meaning. The model is never told what an absent value looks like; the validator accepts any mix of the value `nr`, the confidence `nr`, and cited ids; the deterministic merge and the author/year prefill manufacture `nr` values that cannot be told apart from a model's own answer. Separately, 14 of the 62 extraction-map fields list a category such as "not stated", "not applicable" or "unclear" whose meaning is itself an absence, so a rule "every non-`nr` value must cite evidence" would force the model to pad arbitrary ids onto those answers.
- Contract artefacts disagree. The agent schema file carries policies and rules that are never sent and describe a different contract; fallback author/year prefills cite no evidence yet are labelled confidence `h`; `num_chunks: 1` crashes at configuration load with a division error; the pipeline README documents a report generator that no longer exists.

This feature defines the contract once, states it to the model, enforces it in the pipeline, and makes the pipeline's own placeholders and the category-valued absences distinguishable from asserted values. It changes extraction acceptance, so it is a pipeline concern that does not depend on provenance recording, with one stated, temporary exception (Requirement 6); provenance is a downstream observer of what is defined here.

### Terms used below

- **Evidence package**: the serialised list of evidence items placed in the shared paper prefix of a model call.
- **Shown set**: the set of evidence ids of the items actually contained in the evidence package of one model call, after any token-budget reduction.
- **Not-reported token**: the string `nr` (Requirement 2).
- **Absence phrase of a field**: a category listed in that field's own category text in the extraction map whose meaning is an absence ("not stated", "unclear/not stated", "not reported", "not applicable", "not addressed", "unclear"; Requirement 2.12).
- **Absence-like assertion**: a value that equals an absence phrase of its own field. It is an asserted category value, not the not-reported token.
- **Asserted value**: any value other than the not-reported token and the empty value, including an absence-like assertion.
- **`ids_cited` / `no_ids`**: a model's not-reported answer that does / does not cite evidence ids. `ids_cited` means "ids that were shown to the model and cited"; it does not mean the pipeline verified that the model examined them or that they show the absence.
- **Synthesised not-reported**: a not-reported value produced by the pipeline (deterministic merge, empty-value normalisation, prefill) rather than asserted by the model.
- **Below coverage**: the document's recorded evidence coverage is below `min_evidence_coverage_ratio`, the pipeline's own measure of how much of the paper's substantive text the package covered.

## Boundary Context

- **In scope**: computing and validating against the per-call shown set; exposing each call's shown set; the missing-value contract (accepted combinations, token, coupling, citation rule and its absence-phrase exemption, `ids_cited`/`no_ids`, empty-value handling); marking synthesised not-reported values, absence-like assertions and the origin of every not-reported field; stating the contract in the model prompts; reconciling the agent schema file, fallback prefill confidence, the `num_chunks` lower bound and the stale README prose; compatibility of existing outputs, manifests and evidence caches; failure semantics; tests, including keeping the suite green at every step.
- **Out of scope**: any provenance, graph, schema or completeness change (owned by `provenance-schema-v2`, which consumes what is defined here); tolerance policies for a single bad evidence id among the 62 fields (retain-and-downgrade, case or whitespace canonicalisation of ids) and any tolerance for uncited yes/no/none or judged values, explicitly not decided here; changing the 62-field extraction map; multi-agent routing, verification and repair; the OpenAI client and transport; letting fallback-prefilled fields fall through to model extraction (a rejected alternative in `research.md`).
- **Adjacent expectations**: `provenance-core` (implemented) keeps recording claims and findings and must keep working unchanged; `provenance-schema-v2` will read the shown set, the not-reported origin, the absence classes and the absence-like marker from this feature and must not re-derive or re-interpret them. The repair-exhaustion behaviour that retains an output whose only remaining problems are evidence-reference problems while provenance is enabled belongs to `provenance-core` Requirement 5.4 and is removed by `provenance-schema-v2`; this feature does not change it, and states what it means for the guarantee in Requirement 6. The shared-prefix prompt-cache rule keeps binding every change here.

## Requirements

### Requirement 1: Evidence ids are validated against the evidence the model was shown

**Objective:** As a reviewer relying on cited evidence, I want every id a model cites to be one it actually received in that request, so that a claim is never "grounded" in text the model never saw, and so that the evidence a call could see is known exactly.

#### Acceptance Criteria

1. When the pipeline prepares a model call that carries a paper evidence package (an extraction-chunk call or the synthesis call), the pipeline shall determine that call's shown set as exactly the evidence ids of the items contained in the evidence package sent with that call, after any token-budget reduction of that package.
2. The pipeline shall derive each shown set from the evidence package text that is sent to the model, and shall not derive it from the inputs of evidence selection or from the paper's full evidence index.
3. The pipeline shall fix a call's shown set before the call is made and shall not change it for the lifetime of that call; every repair attempt for the call shall be validated against, and shall be sent, the same shown set and the same evidence package.
4. When a call's evidence package is reduced to meet a token budget, the pipeline shall remove whole evidence items only, so that the package the model receives remains a complete, readable evidence package and its shown set is exact.
5. If a call's evidence package cannot be read as an evidence package, then the pipeline shall treat that call's shown set as empty, shall log a warning naming the paper and the chunk, and shall accept no cited evidence id for that call.
6. When a model output cites an evidence id that is not in the shown set of its call, the pipeline shall treat the output as invalid and shall handle it through the same repair attempts, repair prompt and exhaustion handling as an output that cites an unknown id.
7. When an invalid cited id is present in the paper's evidence index but not in the call's shown set, the repair prompt shall name the id and state that it exists but was not provided in this request; when the id is not in the paper's evidence index, the repair prompt shall name it and state that it is unknown.
8. When the synthesis call is prepared, the pipeline shall compute its shown set from the evidence package sent with the synthesis call; an id that appears only in the prior-result context of the synthesis call shall not be in its shown set.
9. While a synthesis call's token budget requires pruning its evidence package, the pipeline shall continue to keep every evidence item cited by a high-confidence earlier result, as it does today.
10. When a call's shown set has been determined, the pipeline shall make it available, read-only, to the per-document processing code, together with the call kind, the chunk number, the ids of the package before token-budget reduction and a digest of the package text sent, whether or not provenance recording is enabled.
11. If a call's shown set contains an evidence id that is not in the paper's evidence index, then the pipeline shall log a warning naming the paper, the chunk and the ids, and shall still treat those ids as shown, because they were in the prompt.

### Requirement 2: The missing-value contract is defined and enforced

**Objective:** As a reviewer interpreting extracted values, I want "not reported" and the category-valued absences to have defined meanings that are checked on every model output, so that an absence claim is never confused with an asserted finding, with a contradictory answer, or with an uncited guess, and so that the model is never forced to invent citations.

#### Acceptance Criteria

1. The pipeline shall recognise exactly one not-reported token: the text `nr`, compared after removing surrounding whitespace and ignoring letter case, and shall store its canonical form `nr`. Any other text, including a category phrase listed in the extraction map such as "not stated", shall not be interpreted as the not-reported token and shall not be converted to it.
2. When a model output item has an asserted value that is not an absence-like assertion, a confidence of `h`, `m` or `l`, and at least one cited evidence id, all of which are in the call's shown set, the output validator shall accept the item as an asserted value.
3. If an item has an asserted value that is not an absence-like assertion and a confidence of `h`, `m` or `l` and cites no evidence id, then the output validator shall reject it and the repair prompt shall tell the model to cite evidence or to answer not-reported. Values such as yes, no and none, and the values of reviewer-judged and notes fields, are subject to this rule.
4. If an item has an asserted value and the confidence `nr`, then the output validator shall reject it.
5. If an item has the not-reported token as its value and a confidence of `h`, `m` or `l`, then the output validator shall reject it.
6. When an item has the not-reported token as its value, the confidence `nr`, and cites no evidence id, the output validator shall accept it and the pipeline shall treat it as a `no_ids` absence.
7. When an item has the not-reported token as its value, the confidence `nr`, and cites evidence ids that are all in the call's shown set, the output validator shall accept it and the pipeline shall treat it as an `ids_cited` absence; the cited ids record where the model says it looked and shall not be read as evidence that states the absence. An id cited by a not-reported item that is not in the shown set shall be rejected under Requirement 1.6.
8. When an item has an empty or whitespace-only value, the confidence `nr` and cites no evidence id, the output validator shall accept it and the pipeline shall normalise it to a synthesised not-reported value (Requirement 3.3); if an item has an empty or whitespace-only value with any other confidence, or with cited evidence ids, then the output validator shall reject it.
9. When a model output contains several invalid items, the output validator shall report up to 20 violations, each naming its item, in one validation error and shall state how many further violations were omitted, so that a single repair attempt can address them; the checks on the shape of the output (missing or extra keys, non-integer indexes, invalid confidence labels, wrong value types, wrong item count, index mismatch) shall keep their current meaning.
10. The output validator shall give every combination of value kind (asserted, absence phrase of the field, not-reported token, empty), confidence (`h`/`m`/`l`, `nr`) and citation state (none, all shown, some not shown) exactly one verdict as set out in the table below, and shall not accept a combination the table rejects.
11. When an item's value equals, after removing surrounding whitespace and ignoring letter case, an absence phrase of the item's own field and its confidence is `h`, `m` or `l`, the output validator shall accept the item as an absence-like assertion whether or not it cites evidence ids; cited ids shall all be in the call's shown set and, like the ids of an `ids_cited` absence, record where the model says it looked. The pipeline shall keep the value as the model wrote it and shall not convert it to the not-reported token.
12. The pipeline shall derive the absence phrases of each field by one fixed rule from that field's own category text in the extraction map; a field whose category text lists no absence phrase has none; the rule and its result for all 62 fields of the shipped extraction map shall be fixed, versioned with the extraction-contract version and covered by a test; yes, no, none and partial values and judged or notes fields are not absence phrases.

| Value | Confidence | Cited ids | Verdict |
|---|---|---|---|
| asserted, not an absence phrase | `h`/`m`/`l` | at least one, all shown | accept: asserted value |
| asserted, not an absence phrase | `h`/`m`/`l` | none | reject: uncited value |
| asserted, not an absence phrase | `h`/`m`/`l` | any not shown | reject: not provided or unknown id |
| asserted (any) | `nr` | any | reject: value with `nr` confidence |
| absence phrase of the field | `h`/`m`/`l` | none | accept: absence-like assertion |
| absence phrase of the field | `h`/`m`/`l` | at least one, all shown | accept: absence-like assertion |
| absence phrase of the field | `h`/`m`/`l` | any not shown | reject: not provided or unknown id |
| `nr` token | `nr` | none | accept: `no_ids` absence |
| `nr` token | `nr` | at least one, all shown | accept: `ids_cited` absence |
| `nr` token | `nr` | any not shown | reject: not provided or unknown id |
| `nr` token | `h`/`m`/`l` | any | reject: `nr` value with a confidence |
| empty | `nr` | none | accept: normalised, synthesised not-reported |
| empty | `nr` | at least one | reject: empty value |
| empty | `h`/`m`/`l` | any | reject: empty value |

### Requirement 3: Every absence-valued field says what kind of absence it is

**Objective:** As a downstream consumer (audit, evaluation, review), I want a not-reported answer asserted by the model, one manufactured by the pipeline, one entered by a human, and a category-valued absence to be distinguishable from the field itself, so that absence counts are not inflated by pipeline fallbacks, no consumer has to infer the origin, and no one reads cited ids as stronger than they are.

#### Acceptance Criteria

1. The pipeline shall record, in every field of a document's extracted output whose value is `nr`, the origin of that value as exactly one of: asserted by the model, normalised by the pipeline from an empty value, produced by the pipeline's metadata prefill, or entered by a human reviewer; and shall record no origin in a field whose value is not `nr`. The pipeline shall never write the human origin; it is reserved for the review and merge layer, which is the only component that may set it.
2. When the deterministic merge resolves a field to `nr` because every contributing chunk entry for the field is empty or absent, the pipeline shall record the origin "normalised from an empty value" for that field.
3. When an accepted model answer for a field is an empty value with the confidence `nr` and no cited ids, the pipeline shall store the field with the value `nr`, the confidence `nr`, an empty location list and the origin "normalised from an empty value", on the merged path and on the synthesis path alike.
4. When an accepted model answer for a field is the not-reported token, the pipeline shall record the origin "asserted by the model", shall keep the confidence `nr`, and shall keep the cited ids in the field's location list, so that the absence is `ids_cited` exactly when that list is not empty and `no_ids` otherwise.
5. When the metadata prefill finds no value for a prefilled field (no usable first author or no publication year), the pipeline shall store the field as `nr` with the confidence `nr` and the origin "produced by the prefill".
6. When the flagged-fields report lists a field, it shall show for that field the origin and an absence class, and the class shall be exactly one of: empty (not an absence), `ids_cited`, `no_ids`, `pipeline` (either pipeline origin), `human`, or `absence_like`; the report's existing columns shall keep their names and order.
7. The set of origins and the set of absence classes shall be closed and shall each be defined once, so that a consumer can test a field against a fixed vocabulary.
8. The pipeline shall record, in every field of a document's extracted output whose value is an absence-like assertion, a marker saying so, and in no other field.
9. When a document's recorded evidence coverage is below the configured threshold, the flagged-fields report shall mark every row of that document as below coverage, so that a reader can see that an absence claim was made on a package that covered less of the paper than the pipeline itself requires.
10. When sanitisation of extracted values is enabled, the pipeline shall not let sanitisation change the not-reported token, shall not let sanitisation turn any other value into the token, and shall leave origin and absence-like markers in place, so that for every selectable sanitiser the origin is present in a persisted field if and only if its value is `nr`.
11. The pipeline README and the report documentation shall state that `ids_cited` means that ids were shown and cited, not that the model examined them or that the pipeline verified them, and that cited ids on an absence-valued answer record where the model looked, never where the absence is stated.

### Requirement 4: The contract is stated to the model

**Objective:** As an operator, I want the model told exactly what the pipeline will enforce, so that a conforming model is not rejected for a rule it was never told.

#### Acceptance Criteria

1. The system instruction sent with every model call shall state the keys of an answer record and their meaning, that every asserted value must cite at least one evidence id, that only ids of items present in the provided evidence package may be cited and must be copied exactly, that a value which cannot be supported by a cited item is to be answered as not-reported, and that category values listed in the extraction map are ordinary values, except that a category the map lists for that field as an absence phrase may be reported as written without citing ids.
2. The system instruction shall state the not-reported token, that the value `nr` and the confidence `nr` are used together and only together, and that ids cited on a not-reported answer or on an absence-phrase answer record where the model looked and never where the absence is stated; an answer that cites no id means no specific item was examined.
3. The system instruction shall not refer to any input that the pipeline does not send to the model.
4. The shared paper prefix shall state that evidence may be cited only by the id of an item in the package it contains.
5. The format reference included in every repair prompt shall state the same value, confidence and citation rules as the system instruction, including the absence-phrase exemption.
6. While no token-budget reduction applies to a call, the shared paper prefix shall remain byte-identical across the cache warm-up, every extraction chunk and the synthesis call of one paper, and shall contain no value that varies between calls, such as a chunk number, a file name, a run identifier or a shown-set count.

### Requirement 5: The contract artefacts agree with each other and with the code

**Objective:** As a maintainer, I want the agent schema file, the prefill labels, the configuration limits and the documentation to describe the contract that is actually in force, so that no artefact misleads a reader or the model.

#### Acceptance Criteria

1. The agent schema file shall contain only content that is sent to the model or enforced by the pipeline; the policy and rule sections that are never sent and describe a different contract (status words, null values, integer confidence) shall be removed.
2. If the agent schema file contains a top-level key that is neither sent to the model nor file metadata, then loading it shall fail with an error that names the key.
3. Where a prefilled value was read from the TEI header, the pipeline shall keep the confidence `h`.
4. Where a prefilled publication year comes from a fallback source (PDF metadata, first-page text or the file name), the pipeline shall give the prefilled field the confidence `l`.
5. Where no first author can be read from the TEI header, the pipeline shall store the first-author field as `nr` with the confidence `nr` and shall not store the paper identifier as an author.
6. The prefill shall decide whether a value came from the TEI header by the same header-sourcing rule that decides whether the value is cited to a header metadata item, so that a prefilled field is labelled `h` exactly when it is header-sourced; every value cited to a header metadata item shall be header-sourced.
7. If the configured `num_chunks` (from the configuration file or from `OPENAI_NUM_CHUNKS`) is less than 2, then loading the configuration shall fail with an error that names the setting, the environment variable, the value supplied and the minimum of 2; a configured `num_chunks` of 2 or more shall load as it does today.
8. The pipeline README, the utilities README and the repository README shall name the report generator that exists (`generate_flagged_fields_report`) and the file it writes (`flagged_fields.csv`), and shall no longer name `generate_qc_report`, `qc_report.csv` or `QC_REPORT_FILE`.
9. The pipeline README shall document the extraction contract: the not-reported token, the verdict table, the origin and absence-class vocabularies, the absence phrases, the shown set and the fallback prefill labels.
10. The version of the extraction contract recorded in the manifest and the version in the agent schema file shall be the same value, read from one source or asserted equal by a test.
11. When the evidence cache is written, the pipeline shall record in the entry whether the first-author value was read from the TEI header; for an entry written before this change, which lacks that record, the pipeline shall treat an author value that differs from the paper identifier as header-sourced, because the fallback value is the paper identifier, and shall treat a value equal to the paper identifier as header-sourced only when the current TEI header confirms it. A cached author shall not become `nr` merely because the current run has no TEI content.

### Requirement 6: Pipeline behaviour does not depend on provenance recording, up to a stated exception

**Objective:** As a maintainer, I want extraction acceptance to be identical whether or not provenance is recorded, so that the observer never changes what it observes, and I want the one place where today it does to be named, bounded and temporary.

#### Acceptance Criteria

1. When the same model responses are supplied, the pipeline shall produce the same validation verdict for every attempt, the same repair prompt text, the same number and content of model calls, and the same extracted output files and manifest entries whether provenance recording is enabled or disabled, except in the case described in criterion 4, which exists only until `provenance-schema-v2` removes it.
2. The pipeline shall determine and expose every call's shown set whether or not a provenance recorder exists, and shall not import the provenance package to do so.
3. The pipeline shall not place any provenance identifier, shown-set count or origin value in any prompt.
4. While provenance recording is enabled and repair attempts are exhausted and every remaining violation is an evidence-reference problem (an unknown id or an id not in the shown set), the pipeline shall retain the output as `provenance-core` Requirement 5.4 defines, shall judge the retained references against the call's shown set rather than the full evidence index, and shall report an id that is in the evidence index but not in the shown set with the reason "not shown". If any remaining violation is of any other kind (an uncited value, a confidence and value contradiction, an empty value), the pipeline shall fail the document whether or not provenance is enabled.
5. The pipeline README and the change record shall state that "no claim is grounded in text the model did not receive" holds up to repair exhaustion, and that until `provenance-schema-v2` removes the retention an output retained at exhaustion with provenance enabled can cite an id the model did not receive; the retained id is recorded as a finding.

### Requirement 7: Prompt-cache stability and the one declared prompt-content change

**Objective:** As an operator paying for model calls, I want the shared prompt prefix to stay cache-stable and every change to prompt text to be a declared, pinned change, so that a cache regression or an unnoticed prompt edit cannot slip in.

#### Acceptance Criteria

1. While no token-budget reduction applies to any call of a paper, the pipeline shall build the shared paper prefix for that paper byte-identically for the warm-up call, every extraction-chunk call and the synthesis call, and byte-identically across processes; when a reduction applies to a call, that call's package is a pruning of the paper package, the pipeline shall log a warning naming the paper, the chunk and the number of items removed, and the calls that were not reduced shall keep the full paper package.
2. The prefix sentence added by Requirement 4.4, the system instruction rewritten by Requirements 4.1 to 4.3, the format reference in repair prompts (Requirement 4.5) and the violation message templates carried in repair prompts (Requirements 1.7, 2.3 and 2.9) shall be the only changes to model-facing text delivered by this feature, and each violation message template shall be pinned by a test as a literal string.
3. The pinned digests of the shared prefix for the three TEI fixtures, the pinned wording of the prefix wrapper, and a new pinned digest of the system instruction shall be updated in the same change as the prompt text, and shall fail when prompt text changes without that update.
4. The pipeline shall continue to read evidence caches written before this change without regenerating them, and the evidence package built from such a cache shall be byte-identical to the one built before this change; the cache key shall not change and the only change to an entry written after this change is one additive record (Requirement 5.11).
5. The change record shall state that the prompt change causes a one-time miss of provider-side prompt caches for papers processed before the change.

### Requirement 8: Existing manifests, outputs and caches stay usable

**Objective:** As an operator with earlier runs on disk, I want nothing written before this change to become unreadable or to be silently rewritten, and I want to be able to tell which contract produced an output.

#### Acceptance Criteria

1. The pipeline shall read extracted output files written before this change, whose not-reported fields carry no origin and whose absence-phrase values carry no marker, without error in every in-repository consumer (the wide CSV export, the flagged-fields report and the provenance claim conversion), and shall not rewrite them.
2. The final-output validation shall accept a field with and without an origin and with and without the absence-like marker.
3. When the pipeline marks a document complete in the manifest, it shall add the version of the extraction contract that produced the output to the entry; manifest entries without it shall remain readable and resumable.
4. When the pipeline skips a document as already complete and the entry lacks the current extraction-contract version, the pipeline shall log at INFO that the output was produced under an earlier contract, and shall not reprocess it.
5. The set of manifest statuses and the shape of failure records shall not change.
6. The flagged-fields report shall keep its existing columns in their existing order and shall append any new column after them; the wide CSV export shall keep exporting values only.
7. The feature shall add no configuration key; the only configuration behaviour that changes is the `num_chunks` lower bound.

### Requirement 9: Contract violations fail like any other repair exhaustion, and are observable

**Objective:** As an operator, I want a contract violation that cannot be repaired to fail the document in the way I already know, with enough information to see which rule and which ids were involved, so that the first real runs can be understood without guessing.

#### Acceptance Criteria

1. If repair attempts are exhausted for a call whose output violates the contract (including a cited id outside the shown set), then the pipeline shall fail the document exactly as for any other exhausted repair, subject to Requirement 6.4: the manifest status `failed_chunks` (or `failed_chunk_<n>` for the synthesis call) with failure records of the existing shape, and no extracted output file.
2. The failure record's last error shall carry the validator's message, naming the offending items and the offending evidence ids.
3. Contract violations shall use the existing repair-attempt limit and shall not have a separate limit, a tolerance setting or a retain-and-downgrade path.
4. If a prompt cannot be brought within its token budget without removing a protected evidence item, then the pipeline shall reject it with the existing token-budget error and shall not weaken the shown-set rule to avoid it.
5. If the shown set of a call cannot be read from its evidence package, then the pipeline shall fail closed under Requirement 1.5 and shall not raise an unhandled error.
6. When an output contains any contract violation, the pipeline shall reject the whole output; it shall not accept the conforming items of a rejected output.
7. When a repair attempt is started because of contract violations, the pipeline shall log the violation codes with the attempt.
8. When a document finishes, the pipeline shall log one INFO line with the counts of asserted values (excluding absence-like assertions), absence-like assertions, `ids_cited` absences, `no_ids` absences and pipeline-produced not-reported fields.
9. The pipeline README and the change record shall state an operational pilot gate: before bulk runs, process at least three papers, read the repair log lines that carry violation codes, and decide the repair-attempt limit; and shall state as a known limitation that uncited yes, no and none values and uncited values of judged or notes fields are rejected, because silence cannot be distinguished from omission for them, and that this feature adds no tolerance setting.

### Requirement 10: The contract is verified without real model output, and every step stays green

**Objective:** As a maintainer, I want the contract pinned by tests that need no model and assert no invented frequencies, and I want every implementation step to leave the suite passing, so that it can neither regress nor be overclaimed and every step is committable.

#### Acceptance Criteria

1. The test suite shall include a regression test in which a model output cites an id that exists in the paper's evidence index but was not in the call's package, and shall show that the output is rejected, that exactly one repair call follows, that the repair prompt names the id as not provided, and that a conforming repair response is accepted.
2. The test suite shall cover every row of the verdict table in Requirement 2.10 and every combination of the not-reported token and `nr` confidence, using an independent test-side statement of the table as an oracle.
3. The test suite shall verify the shown set of a call against the evidence ids found in the user message built for that call, independently of the code that computes the shown set.
4. The test suite shall include a differential test that runs the per-document processing with provenance recording disabled and enabled on the same scripted responses and compares verdicts, repair prompts, calls, outputs and manifest entries, and a separate test of the exception in Requirement 6.4.
5. The test suite shall include a test that a token-budget reduction leaves a readable package whose shown set equals the ids that remain, and that a call cannot cite an id removed by the reduction.
6. The test suite shall use scripted model responses only, shall call no real GROBID, OpenAI or OCR service, and shall assert no rate of model behaviour.
7. Where a rule is a total function over its inputs (the verdict classification, the shown-set computation, the absence-phrase derivation), the test suite shall include property-based tests of its invariants.
8. The test suite shall run the sanitisation invariant of Requirement 3.10 for every selectable sanitiser, and shall derive the absence phrases of all 62 fields of the shipped extraction map and compare them with a pinned table.
9. New tests shall not depend on the identity of classes across re-imports of the pipeline modules; they shall resolve the modules under test through one import path or compare values rather than class objects.
10. Before the validation rules, the prefill labels or the prompt text change, the existing tests whose fixtures encode the old behaviour shall be re-pointed to fixtures that are valid under both the old and the new behaviour, and every implementation step shall leave the full fast suite passing.
