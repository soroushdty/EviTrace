# Implementation Plan — extraction-contract-hardening

## Implementation Notes

- **Every task is committable green on its own.** The full fast suite passes after every sub-task. To make that true: the expected-failure convention is introduced first (1.1); a test-side oracle and helpers come next (1.2); every existing fixture that encodes the old behaviour is re-pointed, before any rule flips, to a fixture that is valid under both the old and the new behaviour (group 2); only then do the validation rules, the shown set, the prefill labels, the mitigation and the prompt text change (groups 3-9). A flip commit changes no pre-existing test other than the ones its own task names.
- **Order.** Foundation; re-pointing of fixtures; shown set and validator (pure, testable alone); independent leaf changes in parallel; prompt contract; mitigation; repair loop; `process_pdf` wiring and sanitisation; cross-module verification; documentation; final validation. Task N implicitly depends on the tasks before it; `_Depends:_` is stated only where it is not obvious. `design.md` is the authority for names, signatures and texts.
- **Tests first.** A "write failing tests" sub-task adds tests marked `pytest.mark.xfail(strict=True, reason="task N.M")` and references new names lazily (inside test bodies) so the suite still collects; the implementing sub-task removes the markers; a strict XPASS makes a half-done implementation fail loudly. Small independent changes (group 5) write their tests first inside the same sub-task.
- **Test isolation.** `test_pdf_processor_helpers.py` removes `pipeline*` modules from `sys.modules` at import, so a class can exist twice. New tests resolve modules through the helper of 1.2 at test time and compare values (codes, messages, fields), never class identity.
- **Leaf modules.** `validator.py`, `deterministic_merge.py`, `evidence_index.py`, `extraction_report.py`, `extraction_map.py` are loaded by file path in test modules; they must not gain sibling or `pipeline.` imports (guard in 1.3). New code in `src/` must not name a function parameter `stage` or `stages` (the stage-contract drift guard classifies those); use `call_kind`.
- **Declared changes (each needs the CHANGELOG entry in 11.3).** (1) model-facing text: system instruction, one prefix sentence, repair-prompt format, violation templates; (2) stricter validator with a per-call validation set and the absence-phrase exemption; (3) item-granular mitigation of evidence packages; (4) persisted `nr_origin` (four values) and `absence_like`; (5) fallback prefill labels (author fallback becomes `nr`) and the evidence-cache `author_provenance` record; (6) manifest `extraction_contract` stamp; (7) three appended columns in `flagged_fields.csv` and the orchestrator's `below_coverage` key; (8) sanitisation guard; (9) `num_chunks < 2` is a configuration error; (10) agent schema file reduced, two accessors removed, `get_version` added.
- **Commits.** One tick per commit, subject `<type>(extraction-contract-hardening): <task-id> <description>` (`.kiro/steering/testing.md`, "Spec-Task Traceability").
- **Do not do here.** Anything under `src/provenance/`, `claim_provenance.py`, `document_provenance.py`, `run_provenance.py`; removal of the exhaustion-retain path; selection or serialisation changes; tolerance settings; edits of the normalisers.
- **Run notes (added during implementation).** 1.1: fast-suite baseline at the start of implementation (HEAD 634995a) is 4047 passed, 2 skipped, 23 deselected (+1 xfailed from the permanent self-check `tests/src/pipeline/test_xfail_convention_selfcheck.py`); the 4043 → 4047 difference is `tests/steering/test_spec_task_traceability.py` being parametrised over active specs (the two new specs add 4 tests). `deterministic_merge.py` is imported as `pipeline.deterministic_merge` by its tests (no test loads it by file path) but follows the same leaf rule; `validator.py`, `evidence_index.py`, `extraction_report.py`, `extraction_map.py` are the ones loaded by `spec_from_file_location`.

- [ ] 1. Foundation: convention, test harness and guards

- [x] 1.1 Introduce the expected-failure convention before its first use
  - Add a section to the testing steering document describing `xfail(strict=True, reason="task N.M")` for test-first sub-tasks (reference new names lazily, remove the marker in the implementing task, a strict pass fails the suite), the class-identity isolation rule, and the leaf-module rule; no `xfail` exists in the repository today.
  - Add one self-check test that is marked as a strict expected failure and asserts something false, demonstrating that the suite stays green with it.
  - Observable: the testing steering document contains the section, the self-check test is reported as an expected failure, and the fast suite passes.
  - _Requirements: 10.9, 10.10_
  - _Boundary: TestHarness_

- [ ] 1.2 Add the shared contract test helpers and the verdict oracle
  - Provide a builder for evidence package text from a list of ids with the pipeline's envelope, a bundle builder with a matching evidence map, a builder for the model's `{extractions: [...]}` text, and a scripted replacement for the model call that records its arguments and returns scripted responses in order.
  - Provide a function that extracts the evidence ids from a user message built by the real prompt builder by reading between the package markers, a resolver that imports the modules under test at call time through one path, and an independent test-side statement of the verdict table of Requirement 2.10 (value kind, confidence, citation state, absence phrases) returning accept or the rejection codes.
  - Provide a compliance check that asserts a scripted response is accepted by the oracle against a given package and phrase mapping.
  - Add a self-test: the builder's text is byte-equal to the pipeline's serialisation of the same selected items, the extractor returns exactly the placed ids, and the oracle gives the expected verdict for each row of the table.
  - Observable: the helper self-tests pass and importing the helpers pulls in no network, GROBID or OCR code.
  - _Requirements: 10.2, 10.3, 10.6, 10.9_
  - _Depends: 1.1_
  - _Boundary: TestHarness_

- [ ] 1.3 Add the leaf-module standalone-import guard
  - Write an AST test asserting that the five leaf modules named in the notes contain no relative import and no import of `pipeline.*`; the list is extended with the shown-set module in 3.2.
  - Add a self-test that an offending synthetic module is reported.
  - Observable: the guard passes on the current tree, fails on the synthetic offender and names the file and the import in its failure message.
  - _Requirements: 10_
  - _Boundary: TestHarness_

- [ ] 2. Re-point existing fixtures so they are valid under both the old and the new behaviour

- [ ] 2.1 (P) Re-point the processor helper tests
  - Give every scripted model output ids that exist in a real package passed as the call's evidence (use the helpers), make outputs obey the confidence coupling and cite at least one id per asserted value, give stub bundles whose prefilled author and year are expected to be `h` a TEI header and year provenance that make them header-sourced, and relax exact-equality assertions on the complete manifest entry to assertions on its status and coverage record.
  - Run every scripted response through the oracle compliance check.
  - Observable: the module passes on the unchanged source, alone and in full-suite order, and every scripted response satisfies the oracle.
  - _Requirements: 1.6, 2.3, 5.3, 8.3, 10.10_
  - _Depends: 1.2_
  - _Boundary: ExistingTestsProcessor_

- [ ] 2.2 (P) Re-point the repair-loop tests
  - Apply the same fixture changes to the repair retry, repair retry properties and repair retry provenance modules: real packages as the evidence text, cited ids drawn from them, coupling-compliant outputs; keep assertions about the format reference agnostic to its wording (keys present), and keep the size-guarantee and telemetry assertions.
  - Observable: the three modules pass on the unchanged source and every scripted response satisfies the oracle.
  - _Requirements: 1.6, 2.3, 10.10_
  - _Depends: 1.2_
  - _Boundary: ExistingTestsRepairLoop_

- [ ] 2.3 (P) Re-point the remaining affected tests
  - Apply the fixture changes to the token-efficiency regression module, the document-level provenance test module (including making it independent of the module purge performed by the processor helper tests) and the two migration regression modules; relax manifest-entry equality; confirm the loader-based modules (validator loc, evidence index stability, final output validator, extraction report, extraction map grouping) still load.
  - Observable: the listed modules pass on the unchanged source alone and in full-suite order, and the fast suite is green.
  - _Requirements: 1.6, 2.3, 5.3, 10.10_
  - _Depends: 1.2_
  - _Boundary: ExistingTestsMisc_

- [ ] 3. Shown evidence (pure module)

- [ ] 3.1 Write failing tests for the shown-set computation
  - Cases: exact ids of a package; a mitigated text gives a subset and the withheld ids; non-package, empty, truncated and non-object JSON text give an empty set with the readable flag false; items with missing, empty or non-string ids are ignored; the digest equals the SHA-256 of the sent text; the record is immutable and hashable.
  - Property tests: ids equal the item ids of the sent text; pruning a package never adds an id; the result does not depend on item order; the function never raises.
  - Cross-check test: for a package placed in the real user message, the helper-extracted ids equal the computed shown ids.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.1, 1.2, 1.5, 1.10, 10.3, 10.7_
  - _Depends: 1.2_
  - _Boundary: ShownEvidence_

- [ ] 3.2 Implement the shown-set module
  - Create the leaf module with the frozen record, the package-ids function and the builder exactly as in the design; standard library only, never raises.
  - Remove the markers of 3.1 and add the module to the guard of 1.3.
  - Observable: the tests of 3.1 pass without markers, the guard covers the new module and the dependency-direction suite passes unchanged.
  - _Requirements: 1.1, 1.2, 1.5, 1.10_
  - _Boundary: ShownEvidence_

- [ ] 4. Output validator: the missing-value contract and output fields

- [ ] 4.1 Write failing tests for the verdict table, templates and aggregated reporting
  - One parametrised case per row of Requirement 2.10 against the oracle of 1.2; confidence labels; spelling variants of the token; empty versus whitespace-only values; absence phrases with and without ids and with `nr` confidence; yes, no and none values and judged-field values still rejected without ids; items with several violations; the 20-violation cap and its trailer; structural errors keep their messages and precede contract errors.
  - Template tests: each of the six violation message templates, the trailer and the joined form as literal strings; violation codes exposed on the error.
  - Sink-mode tests: reference problems go to the sink judged against the shown set with the not-shown reason, contract rows still raise (so only reference-only outputs can be retained); legacy callers passing only the known ids keep single-set behaviour.
  - Property tests: classification is total and deterministic, accepts exactly where the oracle accepts, an accepted item never cites an id outside the shown set, an absence phrase or the token never needs ids.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.6, 1.7, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 6.4, 7.2, 9.2, 9.6, 10.2, 10.7_
  - _Depends: 1.2_
  - _Boundary: OutputValidator_

- [ ] 4.2 Implement the contract rules in the validator
  - Add the constants, origin vocabulary (four values), contract version, violation record, item classification with the absence-phrase parameter, the templates, the error codes and the shown-set and phrase parameters of the chunk validation; collect contract violations over the whole output (up to 20) and raise them together after the structural checks; canonicalise the token.
  - Implement sink mode as designed; keep the structural checks, messages and order.
  - Remove the markers of 4.1. Pre-existing tests are not edited in this task; they already hold fixtures valid under the new rules (group 2).
  - Observable: the tests of 4.1 pass without markers, the whole fast suite is green with no pre-existing test modified, and the module still loads by file path with no sibling import.
  - _Requirements: 1.6, 1.7, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 6.4, 7.2, 9.2, 9.6_
  - _Depends: 2.1, 2.2, 2.3, 4.1_
  - _Boundary: OutputValidator_

- [ ] 4.3 Write failing tests for output-field markers and empty-value normalisation
  - Cases: a model `nr` keeps its cited ids in the location list and has origin "model"; an index named as merge-synthesised has origin "pipeline_empty_value"; a blank value with confidence `nr` is normalised to `nr` with empty location, evidence and metadata and the same origin on direct input; a value equal to the field's absence phrase carries the absence-like marker and keeps its text; other fields carry no marker; the human origin is never produced; marker keys are last in the field dict.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 3.1, 3.3, 3.4, 3.7, 3.8_
  - _Depends: 1.2_
  - _Boundary: OutputValidator_

- [ ] 4.4 Implement markers and normalisation in field reconstruction
  - Add the synthesised-index and phrase parameters and the marker rules to the field reconstruction exactly as designed; leave every other key of the field dict and the sink behaviour unchanged.
  - Remove the markers of 4.3.
  - Observable: the tests of 4.3 pass without markers, the existing reconstruction tests pass unchanged and the fast suite is green.
  - _Requirements: 3.1, 3.3, 3.4, 3.7, 3.8_
  - _Depends: 4.2, 4.3_
  - _Boundary: OutputValidator_

- [ ] 5. Independent leaf changes

- [ ] 5.1 (P) Name the fields the merge turned into `nr`
  - Write tests first: all-empty, `None`, whitespace-only and absent contributions list the field index in ascending order; a model `nr` does not; `merged_fields` entries are unchanged.
  - Add the new result attribute with an empty default and fill it in the existing all-empty branch; keep the merge's own constants.
  - Observable: the new tests and the existing merge unit and property tests pass.
  - _Requirements: 3.2_
  - _Boundary: MergeMarker_

- [ ] 5.2 (P) Decide whether a prefilled value is header-sourced, robustly across the cache
  - Write tests first: a new cache entry records whether the author was read from the header; a hit with no TEI content keeps a recorded header author as header-sourced; a legacy entry (no record) with an author different from the paper identifier is header-sourced; a legacy entry equal to the identifier is header-sourced only when the current header confirms it; the year is header-sourced only with header year provenance (and a confirming current header when TEI is available); paper-id author, empty header surname and non-header years are fallback; the three real fixtures give the expected sources.
  - Add the equivalence test: every field the provenance header-citation rule cites is header-sourced here, over the fixtures, stale-cache bundles, legacy entries and no-TEI hits.
  - Implement the bundle field, the cache record (written on a miss, read on a hit, empty when absent), the source constants and the source function next to the prefill logic; do not change the item-building return value, the cache key, the year resolver or the bundle's prefilled values.
  - Observable: the new tests pass, the evidence package bytes built from a pre-change cache entry are identical to before, and the existing evidence-index, cache and year-resolver tests pass unchanged.
  - _Requirements: 5.3, 5.4, 5.5, 5.6, 5.11, 7.4_
  - _Boundary: PrefillPolicy_

- [ ] 5.3 (P) Reject `num_chunks` below 2 at configuration load
  - Write tests first: values 0, 1 and negative, from the configuration file and from the environment override, raise an error naming the setting, the environment variable, the supplied value and the minimum; 2, 3 and 5 load and the derived tables are unchanged; the two derived-table helpers raise the same error when called directly.
  - Add the check to the loader and to both helpers.
  - Observable: the new tests pass, the existing configuration tests pass and loading the shipped configuration is unchanged.
  - _Requirements: 5.7_
  - _Boundary: ConfigGuard_

- [ ] 5.4 (P) Declare the optional markers in the final-output schema
  - Write tests first: a field with each of the four origins passes final-output validation; a field with the absence-like marker passes; a field without either key passes; an unknown origin value fails; any other extra key still fails; a legacy output file without the keys passes validation, the wide CSV export, the flagged report and the provenance claim conversion.
  - Add the two optional properties to the schema file and nothing else.
  - Observable: the new tests pass and the existing final-output validator unit and property tests pass unchanged.
  - _Requirements: 3.1, 3.8, 8.1, 8.2_
  - _Boundary: OutputSchema_

- [ ] 5.5 (P) Classify absences and show coverage in the flagged-fields report
  - Write tests first: the absence class is empty for fields that are neither `nr` nor absence-like, `absence_like` for the marker, `pipeline` for the two pipeline origins, `human` for the human origin, `ids_cited` or `no_ids` for a model or legacy `nr` by whether the location list is non-empty; the report appends `nr_origin`, `absence` and `below_coverage` after the seven existing columns in the existing row order; `below_coverage` is `true` on every flagged row of a document whose result entry says so; the orchestrator result entries carry the key read from the manifest coverage record and omit it when unknown; the stdout summary and `nr`-rate ranking are unchanged.
  - Implement the class vocabulary and function, the appended columns and the orchestrator key.
  - Observable: the new tests and the existing report and orchestrator tests pass; a report over a mixed list shows one row per `l` or `nr` field with the new columns filled as specified.
  - _Requirements: 3.4, 3.6, 3.7, 3.9, 8.6_
  - _Boundary: ReportColumns_

- [ ] 5.6 (P) Derive each field's absence phrases from the extraction map
  - Write tests first: the rule on constructed category texts (parenthetical stems, the slash form, the examples prefix, non-absence categories, yes/no/none/partial never marked); a pinned table of the derived phrases for all 62 fields of the shipped map (fields 3, 5, 10, 24, 25, 27, 34, 44, 49, 50, 51, 53, 56 and 61 have phrases, all others none); property test that the function is total and never marks a yes/no/none/partial item.
  - Implement the derivation next to the extraction-map helpers.
  - Observable: the new tests pass, the table equals the measured one, and the existing extraction-map tests pass unchanged.
  - _Requirements: 2.12, 10.7, 10.8_
  - _Boundary: AbsencePhrases_

- [ ] 6. Prompt contract

- [ ] 6.1 Write failing tests for the instruction and the schema file guard
  - Agent schema file guard: the shipped file loads; an extra top-level key is rejected with its name; a missing system instruction is rejected; the policy accessors no longer exist; the version accessor exists.
  - Instruction obligations: it names the token, says ids come from the provided package and are copied exactly, says every reported value cites an id except the token and a field's listed absence categories, states the value/confidence coupling, says cited ids on absence-valued answers record where the model looked and never where the absence is stated, treats other categories (including yes, no, none) as ordinary values, and mentions none of the inputs that are not sent; a placeholder digest pin is added.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.10, 7.3_
  - _Boundary: PromptContract_

- [ ] 6.2 (P) Rewrite the agent schema file and its validator
  - Replace the system instruction with the text in the design, set the file version to 2.0.0, delete the policy and rule sections; make the validator require only the system instruction, accept the two metadata keys, reject any other key by name, add the version accessor and remove the two policy accessors.
  - Pin the real digest of the instruction in the test of 6.1 and remove the markers.
  - Observable: the schema-guard and instruction tests pass without markers, the prompt-builder tests pass unchanged, nothing in `src/` still calls the removed accessors and the fast suite is green.
  - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.10, 7.3_
  - _Depends: 6.1_
  - _Boundary: AgentSchemaGuard_

- [ ] 6.3 (P) Add the prefix sentence and recompute the pinned prefix values once
  - In one change: add the sentence to the shared paper prefix, recompute the twelve digests and byte lengths with the golden module's own digest function, update the wrapper literal and its explanatory comments to describe a declared model-facing text change, and pin the digest of the package part alone (computed before the sentence is added) for the three fixtures.
  - Assert that every byte length grew by exactly the sentence length plus one and that warm-up, chunk and synthesis messages still share the identical prefix.
  - Observable: the golden module passes with the new values, the negative provenance-vocabulary guard still passes, a deliberate one-character change to the sentence makes the module fail, and the fast suite is green.
  - _Requirements: 4.4, 4.6, 7.1, 7.2, 7.3, 7.4_
  - _Boundary: PromptContract_

- [ ] 7. Budget mitigation keeps the package readable

- [ ] 7.1 Write failing tests for whole-item mitigation
  - Cases: an over-budget package at the extraction-chunk stage yields a parseable package whose ids are a subset removed from the tail of the id order; protected ids always survive; the synthesis result for a non-empty protected set is byte-identical to the pre-change function on fixed inputs; non-package text (the repair prompt) takes the unchanged flat path; a package that cannot fit even with only protected items raises the budget error; an in-budget package is returned unchanged.
  - Property test: for any package and budget the result parses, keeps protected ids and has ids within the input ids.
  - Observable: the new tests are collected and recorded as expected failures (the extraction-chunk case fails today with unparseable output).
  - _Requirements: 1.4, 1.9, 9.4, 10.5, 10.7_
  - _Depends: 1.2_
  - _Boundary: BudgetMitigation_

- [ ] 7.2 Prune whole items for any evidence package
  - Route every evidence text that parses as an evidence package through the structured pruning with the protected set possibly empty; keep the flat path for other text; keep the rejection when the protected items alone exceed the budget.
  - Remove the markers of 7.1.
  - Observable: the tests of 7.1 pass without markers, the existing budget and mitigation tests pass unchanged and the fast suite is green.
  - _Requirements: 1.4, 1.9, 9.4_
  - _Boundary: BudgetMitigation_

- [ ] 8. Repair loop: per-call shown set

- [ ] 8.1 Write failing tests for the shown set in the repair loop
  - Regression: a scripted chunk output cites an id that exists in the paper's evidence map but not in the call's package; exactly one repair call follows, its prompt names the id as not provided, the repair call receives the identical package text, and a conforming second response is accepted.
  - Exhaustion and triage: contract-row violations through every attempt raise the existing exhaustion error with the aggregated message naming items and ids, with and without a provenance sink; reference-only violations fail without a sink and are retained with one, judged against the shown set with the not-shown reason; the attempt limit is the existing one.
  - Synthesis and mitigation: its shown set is its own; an id present only in the prior-result context is rejected; a mitigated-away id is rejected; a reduced call logs the warning and an unreduced sibling keeps the full package; shown ids outside the evidence map log a warning and stay shown.
  - Exposure and parity: the exposed mapping is read-only, keyed by call kind and chunk, and its values equal the ids of the user message built from the captured package text; the repair log line carries the violation codes; an unreadable package logs a warning and accepts only the token with no ids or an absence phrase; with and without a sink the model-call arguments and prompts are identical.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.1, 1.3, 1.5, 1.6, 1.7, 1.8, 1.10, 1.11, 6.1, 6.2, 6.4, 7.1, 9.1, 9.3, 9.5, 9.6, 9.7, 10.1, 10.3_
  - _Depends: 1.2, 3.2, 4.2, 7.2_
  - _Boundary: RepairLoop_

- [ ] 8.2 Build, expose and validate against the shown set in the repair loop
  - After the optional mitigation and before the first model call, build the shown record from the package text before and after mitigation, store it under its call kind and chunk, and expose a read-only view; validate the initial answer and every repair answer against it with the known ids and the loop's absence phrases passed separately; pass the shown ids to the model-call function in place of the full set; judge retained references against the shown set.
  - Add the per-call INFO line, the mitigation warning, the subset warning and the violation codes in the repair log line; add the phrase parameter to the loop's constructor.
  - Remove the markers of 8.1. Pre-existing tests are not edited in this task.
  - Observable: the tests of 8.1 pass without markers, the loop's public call signature is unchanged, the stage-contract drift guard passes and the fast suite is green with no pre-existing test modified.
  - _Requirements: 1.1, 1.3, 1.5, 1.6, 1.7, 1.8, 1.10, 1.11, 6.2, 6.4, 7.1, 9.1, 9.3, 9.5, 9.6, 9.7_
  - _Depends: 2.1, 2.2, 2.3, 8.1_
  - _Boundary: RepairLoop_

- [ ] 8.3 State the contract in the repair-prompt format reference
  - Write tests first: the verbose and terse format references equal the literal texts of the design (value, `nr` coupling, citation rule, absence-category exemption, "where you looked"); the repair prompt remains strictly smaller than the original prompt in all size tiers; the parse-error and schema-error prompts keep their other content.
  - Replace the two format texts.
  - Observable: the new tests and the existing repair-prompt unit and property tests (size guarantee included) pass.
  - _Requirements: 4.5, 7.2_
  - _Depends: 8.2_
  - _Boundary: RepairLoop_

- [ ] 9. `process_pdf` wiring and sanitisation

- [ ] 9.1 Write failing tests for prefill, markers, the manifest stamp and the summary
  - Prefill: header author and year are `h` and unmarked; a fallback year keeps its value with `l`; a paper-id author and a missing year are `nr` with the prefill origin; a cached header author with no TEI content stays `h`; prefilled fields still reach the output with empty evidence and location and are still excluded from the model request.
  - Markers: a merge-synthesised `nr` and a synthesis-path empty value carry the empty-value origin; a model `nr` carries the model origin and its ids; an absence-phrase answer carries the absence-like marker on both paths and is not converted; origin is present on exactly the `nr` fields of the persisted output.
  - Manifest and logs: the complete entry carries the contract version and keeps the coverage record; a skipped complete document whose entry lacks the version logs the earlier-contract note at INFO and is not reprocessed; one summary line per finished document gives the five counts.
  - Exposure: after a document finishes, the repair loop that served it holds a shown record for every extraction chunk and for the synthesis call, each equal to the ids of the package text passed to the model call.
  - Observable: the new tests are collected and recorded as expected failures.
  - _Requirements: 1.10, 3.2, 3.3, 3.4, 3.5, 3.8, 5.3, 5.4, 5.5, 5.11, 8.3, 8.4, 8.5, 8.7, 9.8_
  - _Depends: 4.4, 5.1, 5.2, 5.4, 5.5, 5.6, 8.2_
  - _Boundary: ProcessPdfWiring_

- [ ] 9.2 Build prefilled fields by the new policy and plumb the markers
  - Replace the inline prefill with a builder that follows the label table of the design; derive the absence phrases once from the field definitions and give them to the loop and to both reconstruction calls; pass the merge's synthesised indices (restricted to the extraction fields) to the reconstruction of merged fields.
  - Update the docstring of the per-document function to list the declared behaviour changes.
  - Remove the markers of 9.1 that cover prefill and markers. Pre-existing tests are not edited in this task.
  - Observable: the prefill and marker tests of 9.1 pass; the prompt-context entries for prefilled fields show the new values and confidences; the fast suite is green with no pre-existing test modified.
  - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.8, 5.3, 5.4, 5.5, 5.6, 5.11_
  - _Depends: 2.1, 2.3, 9.1_
  - _Boundary: ProcessPdfWiring_

- [ ] 9.3 Stamp complete entries, note earlier-contract resumes and log the document summary
  - Add the contract version to the complete manifest entry; add the INFO note on the skip path; add the summary line using the report's class function; leave failure entries, statuses and `is_resumed` unchanged.
  - Remove the remaining markers of 9.1.
  - Observable: the manifest, resume, exposure and summary tests of 9.1 pass without markers; failure-entry shapes asserted by the existing manifest tests are unchanged.
  - _Requirements: 1.10, 8.3, 8.4, 8.5, 8.7, 9.8_
  - _Boundary: ProcessPdfWiring_

- [ ] 9.4 Keep the not-reported token and the markers well defined under sanitisation
  - Write tests first, for each of the six selectable sanitisers: inputs including the token, ` NR `, `N/R`, `n.r.`, a real value, and an absence phrase; after the output write, every `nr` field has exactly the value `nr` and an origin, no other field has value `nr` or an origin, the absence-like marker survives, and a warning is logged when a sanitiser would have produced the token from another value; the human origin never appears in pipeline output.
  - Implement the guard around the sanitiser call in the output writer exactly as designed.
  - Observable: the new tests pass for all six sanitisers, the existing output-write tests pass unchanged and the fast suite is green.
  - _Requirements: 3.1, 3.8, 3.10, 10.8_
  - _Depends: 5.4, 9.3_
  - _Boundary: SanitisationGuard_

- [ ] 10. Cross-module verification

- [ ] 10.1 Add the consistency and published-interface tests
  - Assert the token and confidence constants agree across the validator, the merge and the instruction text; the origin vocabulary agrees across the validator, the schema enum and the report; the absence classes agree between the report vocabulary and the README table; the file version accessor equals the contract version equals the manifest stamp; the published names (shown record, loop mapping, origin and absence-like keys, absence classes, version, error codes) exist with the documented shapes so downstream specs can rely on them.
  - Observable: the consistency module passes, and renaming any one of the constants makes it fail with a message naming the disagreeing places.
  - _Requirements: 2.1, 3.1, 3.7, 4.1, 5.10, 8.3_
  - _Depends: 6.2, 6.3, 9.3_
  - _Boundary: ContractConsistency_

- [ ] 10.2 Add the end-to-end scripted-document tests
  - Run the per-document function over a small real-shaped bundle with scripted responses covering: asserted values, `ids_cited` and `no_ids` absences, an absence-phrase answer, a blank-value absence, an unshown-id repair, exhaustion, and the synthesis path; check the persisted output, the manifest, both iff properties over all produced outputs, and that legacy outputs flow through the export, the report and the claim conversion.
  - Observable: the module passes and touches no real service; the persisted output of the success case validates against the final-output schema.
  - _Requirements: 3.1, 3.3, 3.4, 3.8, 8.1, 8.2, 9.1, 9.2, 9.6, 10.1_
  - _Depends: 9.4_
  - _Boundary: ContractConsistency_

- [ ] 10.3 Add the provenance independence differential test
  - Run the same scripted responses with provenance disabled and with a real recorder enabled; compare every attempt's verdict, repair prompts, model-call arguments, outputs and manifest entries; add separate tests of the exception: reference-only exhaustion is retained with a recorder (references judged against the shown set) and fails without; a contract-row violation fails with both.
  - Assert that no prompt argument contains a provenance identifier, a shown-set count or an origin value.
  - Observable: the module passes; mutating the validator to consult the recorder makes it fail.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 10.4_
  - _Depends: 9.3_
  - _Boundary: ContractConsistency_

- [ ] 11. Documentation, change record and steering

- [ ] 11.1 (P) Document the contract in the pipeline README and correct its stale prose
  - Add a contract section: the token, the verdict table, the origin and absence-class vocabularies with their definitions (`ids_cited` means ids shown and cited, not examined; cited ids on absence-valued answers record where the model looked), the absence phrases and the 14 fields that have them, the shown set and how to read it, the fallback prefill labels, the manifest stamp, the report columns including `below_coverage`, the retention caveat, the known limitation for uncited yes/no/none and judged values, and the operational pilot gate; name `generate_flagged_fields_report` and `flagged_fields.csv` in the flow diagram, the outputs list and the extraction-report entry.
  - Observable: a grep of the README finds no `generate_qc_report` or `qc_report.csv`, and the contract section matches the design's tables, the report vocabulary and the consistency test.
  - _Requirements: 3.11, 5.8, 5.9, 6.5, 9.9_
  - _Boundary: DocsPipelineReadme_

- [ ] 11.2 (P) Correct the other READMEs
  - Root README (report names in the feature list, flow, file tree and outputs table), utilities README (the constant that no longer exists is replaced by the flagged-fields constant), agents README (accessors, accepted keys, version accessor), model-client README (the validation-set parameter is unused and what the loop passes), configs README (agent schema file content and the `num_chunks` minimum).
  - Observable: no remaining mention of `generate_qc_report`, `qc_report.csv`, `QC_REPORT_FILE`, `get_policies` or `get_extraction_rules` outside archived specs and the changelog.
  - _Requirements: 5.8_
  - _Boundary: DocsReadmes_

- [ ] 11.3 (P) Add the CHANGELOG entry
  - One entry following the changelog rules and listing the ten declared changes of the notes, the one-time provider-side cache miss, the unchanged evidence-cache items with one additive record, the retention caveat ("never grounded in unseen text" holds up to repair exhaustion until `provenance-schema-v2` removes the retention), the known limitation for uncited yes/no/none and judged values, the operational pilot gate, and the interfaces published for downstream specs.
  - Observable: the entry exists under the current month heading, earlier entries are untouched, and every declared change in the notes, the caveat, the limitation and the pilot gate are named.
  - _Requirements: 6.5, 7.5, 9.9_
  - _Boundary: DocsChangelog_

- [ ] 11.4 (P) Sync steering and the project instructions
  - Product steering (stale report names, schema-file description, accessor list), config steering (`num_chunks` minimum), testing steering beyond 1.1 (the new test modules, the oracle and helpers, where each new test lives), roadmap (this spec added in dependency order; the README stale-prose item closed), and the two stale lines in the project instructions file.
  - Observable: the steering files and the project instructions agree with the code and with the README text of 11.1.
  - _Requirements: 5.8, 5.9, 10.9_
  - _Boundary: DocsSteering_

- [ ] 11.5 (P) Verify and update the downstream specs' view of the published interfaces
  - Verify the "Interfaces published" section already present in the `provenance-schema-v2` brief against the final names and update it: class names `ids_cited`/`no_ids` replace verified/unverified absence; `absence_like` marker and class; the fourth origin `human`; the `below_coverage` report flag; the retention triage; the renamed or added identifiers listed in the design; and that accepted outputs may contain absence-like claims without evidence.
  - Add or verify equivalent short upstream notes in the briefs or designs of `multiagent-extraction`, `evidence-routing` and `cost-and-run-reporting`; do not change their decisions, requirements or tasks.
  - Observable: each of the four specs links to the published interface names, and none of their requirements or tasks are edited.
  - _Requirements: 1.10, 3.7, 6.4_
  - _Boundary: DocsDownstream_

- [ ] 12. Final validation

- [ ] 12.1 Run the full validation and reconcile the declared changes
  - Run the fast suite, the slow suite, the dependency-direction tests and the spec and steering tests from the repository root; confirm no expected-failure marker remains except the demonstration test of 1.1; confirm every item of the declared-changes list appears in the CHANGELOG and in the pipeline README; confirm no configuration key was added (`_ALL_KNOWN_TOP_LEVEL_KEYS` and the loader defaults are unchanged); confirm that the measured impact lists of the design (46 tests for rules plus shown set, 18 for the prefix sentence) match the modules changed in group 2 and task 6.3, and that no flip commit modified a pre-existing test it does not own.
  - Record the run as a short validation note appended to this file; leave the real-run measurements (repair rate, `nr` rate, absence-like rate, output-token adequacy) as open items for the pilot.
  - Observable: all suites pass, `git status` shows changes only in the files named by the design, and the validation note lists the commands and results.
  - _Requirements: 6.1, 7.3, 8.1, 8.7, 9.1, 10_
  - _Depends: 11.1, 11.2, 11.3, 11.4, 11.5_
  - _Boundary: ContractConsistency_
