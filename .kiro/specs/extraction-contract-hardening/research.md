# Research & Design Decisions — extraction-contract-hardening

---
**Purpose**: Discovery findings, measured facts, rejected alternatives and unverified assumptions behind `design.md`.

**Governing instruction (from the owner, 2026-10-12)**: cost and time of work are not a concern; accuracy and scientific credibility supersede them. Every decision below prefers explicit, enforced and auditable over cheap or tolerant.

**Amended 2026-10-12 after an independent review** (items D1-D7 and the smaller items; the review-response section at the end records each point, what was verified and where the review was wrong). Decisions D4, D6, D7, D11, D13, D22, D23 were amended; D25-D35 are new.

**Provenance of facts**: "Measured" means observed by running code at HEAD `c21c929` in this session; scratch scripts were not persisted into the repository. "Read" means read from the cited source. "Assumption" means not verified and listed in the last section.
---

## Summary

- **Feature**: `extraction-contract-hardening`
- **Discovery Scope**: Extension of the existing extraction pipeline (light discovery, escalated to code-level verification of every claim in the brief). No external service, library or standard is involved; no web research was needed.
- **Key Findings**:
  - The shown set can be derived exactly from the bytes sent (`json.loads` of the package text handed to `extract_chunk`), which is robust to any later change of selection (including `evidence-routing`). It cannot be derived from the selection output, because token-budget mitigation changes the package after selection, and for the extraction-chunk stage mitigation can cut the serialised JSON mid-item (measured, M2). The fix is item-granular mitigation for every stage that carries a package.
  - Eight test modules load `validator.py`, `evidence_index.py` or `extraction_report.py` by file path (and four more load `manifest.py`), so those modules cannot gain intra-package imports (measured, M3). The contract therefore lives as facets in the existing owners plus one new stdlib-only leaf, not in one new shared module.
  - The extraction map's categorical vocabularies contain domain answers such as "not stated", "unclear", "not reported (silent on interpretability)" and "not applicable" (14 of the 62 fields list a category phrase of the "not stated" / "not reported" / "not applicable" / "unclear" kind in `categories_or_examples`; measured with a regular expression over `configs/extraction_map.json`). These must stay ordinary asserted values; the pipeline `nr` token must not be confused with them.
  - A prototype of the verdict rules plus the shown-set check (scratch copy, M4) fails 46 existing tests in 8 files, all of them stub outputs that cite no id or cite ids absent from a dummy package. No test fails for a reason other than the deliberately changed behaviour.

## Research Log

### M1. How much of the evidence index is never shown (reproduces the investigation)
- **Context**: The brief and `provenance-schema-v2/investigation-2026-10-12.md` give 79% / 41% / 18%.
- **Method**: `_build_items_from_tei` on the three checked-in TEI fixtures, `select_paper_evidence` with the real 62-field `configs/extraction_map.json`, `max_items=150`, `max_chars=30000`, `serialise_evidence_package`.
- **Measured**: bioRxiv 150 of 184 ids shown (18% never shown); arXiv 102 of 496 (79%); PLOS ONE 141 of 241 (41%). Package size 52,827 / 46,572 / 60,572 characters, i.e. about 13.2k / 11.6k / 15.1k estimated tokens (`len // 4`) against an `extraction_chunk` budget of 100,000 tokens and a `synthesis` budget of 120,000.
- **Implications**: The numbers reproduce. Token-budget mitigation is unreachable on these fixtures at default caps and budgets, so the mitigation fix (D2) is a correctness guard for configured or future conditions, not a change visible in the default path. Stated as an observation about three fixtures, not as a corpus statistic.
- **Prefill facts seen in the same run**: bioRxiv resolves to author `Blanco-Melo`, year `nr` with empty year provenance; arXiv and PLOS ONE resolve a year from the TEI header (`year_provenance: tei_header`).

### M2. Token-budget mitigation can break the evidence package
- **Context**: `_check_and_mitigate_budget` (`pdf_processor.py:256`) has two paths: a structured path (`_prune_evidence_json_preserving_protected`) taken only when `protected_evidence_ids` is non-empty (synthesis), and `token_budget.apply_mitigation` otherwise.
- **Read**: `apply_mitigation._prune_evidence` splits the evidence string on blank lines (`"\n\n"`), but `serialise_evidence_package` emits compact `json.dumps` with no literal newline, so the "items" list has one element; the item-count cap and the trailing-item drop are no-ops, and the character cap and the last-resort loop slice the string.
- **Measured**: a 200-item, 82,654-character package with a 4,000-token budget: stage `extraction_chunk` returns a 14,350-character string that does not parse as JSON (ends mid-text); stage `synthesis` with one protected id returns a parseable package of 38 items.
- **Implications**: For the extraction-chunk stage the model could receive a truncated JSON package, and the "ids in the prompt" would be undefined. The design makes mitigation item-granular for any evidence-package text (D2) and keeps the flat path only for non-package text (the repair prompt, which carries no evidence).

### M3. Standalone-load convention of leaf modules
- **Read**: `validator.py`, `evidence_index.py`, `extraction_report.py`, `deterministic_merge.py`, `manifest.py`, `token_budget.py` import only top-level packages (`utils`, `provenance`, `pdf_extractor`), never siblings. The tests `test_pipeline_validator_loc.py`, `test_final_output_validator.py`, `test_evidence_index_stability.py` (validator and evidence index), `test_pipeline_evidence_index.py`, `test_annotation_schema_properties.py`, `test_evidence_cache.py`, `test_evidence_cache_properties.py` and `test_extraction_report_qc.py` load them with `importlib.util.spec_from_file_location`; `test_manifest_*.py` do the same for `manifest.py`.
- **Measured**: adding `from .extraction_contract import ...` to `validator.py` in a scratch copy made `test_evidence_index_stability.py` fail at collection with `ImportError: attempted relative import with no known parent package`.
- **Implications**: No new sibling import may be added to those modules (D20). An AST guard test pins the convention (design, Testing Strategy).

### M4. Prototype impact on the existing suite
- **Method**: scratch copy of the repository (not persisted). Baseline: 4,000 passed, 2 skipped, 23 deselected in 104 s (`tests/steering` excluded). Prototype: the verdict rules (including "asserted value must cite an id") and the shown set derived in `extract_with_repair` from the sent `source` text, no other change.
- **Measured**: 46 failures: `test_pdf_processor_helpers.py` 18, `test_repair_retry.py` 16, `test_repair_retry_provenance.py` 4, `test_repair_retry_properties.py` 3, `test_token_efficiency_regression.py` 2, `test_process_pdf_provenance.py` 1, `tests/test_migration_risk_remediation_bug_condition.py` 1, `tests/test_migration_risk_remediation_preservation.py` 1. All fail because the stubbed model outputs cite no id, or cite ids (`ev1`, `ev-1`) that are not in the dummy package text used as the call's evidence.
- **Addendum (review response)**: the validator rules alone (without the shown-set check) fail 26 existing tests; rules plus shown set fail 46; the reviewer's prototype counted about 34 for a variant of the rules. The counts depend on which rules are included, not on a disagreement about the behaviour; the plan therefore re-points all fixtures first (D27).
- **Implications**: The change list in `design.md` (Revalidation Triggers) is measured, not guessed. The prefill, `nr_origin`, prompt, golden and configuration changes were not prototyped; their affected tests are named from reading and grep, and will add to this count.

### M4b. Prefix sentence impact
- **Measured** (same scratch copy): adding the one-sentence prefix line to `_shared_paper_prefix` fails exactly 18 tests, all in `tests/src/provenance/test_provenance_prompt_prefix_regression.py` (six golden digests, golden repeatability, wrapper literal, three cross-call stability cases that compare to the wrapper, the bundle-path golden and six live-recorder cases). `tests/src/agents` (prompt builders, PBT, stability properties, telemetry) and `test_evidence_index_*` pass unchanged.

### M12. Is an evidence-cache invalidation needed?
- **Read**: the cache file `{paper_id}_{pdf_sha256}_{extraction_map_hash}.evidence.json` stores `evidence_items`, `prefilled_fields`, `year_provenance` and `year_confidence` (`evidence_index.py:1318-1331`). It stores no prompt text and no package. The cache key covers the PDF bytes and the extraction map only.
- **Findings**: this feature changes none of the stored values or their derivation: selection and serialisation are untouched; `bundle.prefilled_fields` and `year_provenance` keep their values (the new prefill labels are computed at run time from them and from the current TEI, `PrefillPolicy`); the prompt text is built at call time. So an old cache yields the same evidence package bytes as a fresh build, and no invalidation, key change or version marker is needed. The persisted `year_confidence` is not read by the new policy.
- **What does change**: the bytes of the user message and system message sent to the provider (declared change), so provider-side prompt caches keyed on the old bytes miss once per paper; `prompt_cache_key_prefix` is not part of the cached bytes and need not change (`paper_cache_key` hashes the package only).
- **Implications**: Requirement 7.4 and a golden for the package-only bytes (task 5.3).

### M13. The extraction map's absence-like categories (review D2)
- **Measured** (regular expression and the D25 rule over `configs/extraction_map.json`): 14 fields have an absence-phrase category; 8 fields have a whole-item `yes`/`no`/`none`/`partial` category (9, 43, 47, 54 outside the first group, plus the first-group fields 34, 51, 53, 56); explicit "reviewer-judged" formats are fields 56 and 61, "Structured notes" field 13, free-text notes field 62. The review quoted "16 more" yes/no/none fields and judged fields 56, 58, 60, 61, 62; I could not reproduce 16 (fields 58 and 60 are coded multi-selects with a controlled vocabulary, not judged). The decision does not depend on the exact count.

### M14. How the report can learn coverage
- **Read**: `generate_flagged_fields_report(results)` consumes `{"pdf": name, "fields": [...]}` built at `orchestrator.py:193`; `evidence_coverage` (with `below_threshold`) is written to the manifest entry of `pdf_path.stem` (`pdf_processor.py:1921-1924`). The orchestrator has the manifest in scope.

### M5. Normalisers and the token
- **Measured** (re-run during the review response, on `"nr"`, `" NR "`, `"N/R"`, `"n.r."`, `"Not reported"`): `text_processing.normalizers` holds six concrete normalisers, all `TextProcessor` subclasses: `WhitespaceNormalizer`, `AggressiveNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`, `OcrCleaner`, `OULNormalizer` (the review said five; `OcrCleaner` is a sixth, selectable by name through `_get_normalizer`). All return `nr` for `"nr"`. `AggressiveNormalizer` maps `" NR "`, `"N/R"` and `"n.r."` to `nr` (it lower-cases and strips non-word characters); `WhitespaceNormalizer` maps `" NR "` to `nr` and `"N/R"` to `n/r`; `LineHealing`, `Unicode` and `OUL` upper-case `" NR "` to `NR` and leave `N/R`; `OcrCleaner` leaves `" NR "` untouched. `exported_value_normalizer` is user-selectable (shipped config: `OULNormalizer`).
- **Implications**: the sanitiser can create the token from a non-`nr` value (`Aggressive`: `"N/R"`) and can alter the token's spelling. "Origin present iff value is `nr`" is therefore only well-defined if sanitisation is constrained (D30).

### M6. `num_chunks` boundary behaviour
- **Measured**: `_get_domain_to_chunk(1)` raises `ZeroDivisionError`; `(0)` returns `{13: 0}` silently; `(2)` maps domains 1-12 to chunk 1 and domain 13 to chunk 2; `(14)` leaves chunks 13 without a domain. `_get_chunk_max_tokens(1)` would give chunk 1 the generic 3,500 output tokens.
- **Read**: `process_pdf` itself tolerates `num_chunks=1` (tests in `test_process_pdf_provenance.py` pass it directly in the config dict), so the limit belongs at config load, not in `process_pdf`.
- **Implications**: D18.

### M7. Consumers tolerate an extra field key
- **Read**: `claim_adapter.py` reads fields by named `.get` (`field_index`, `field_name`, `extracted_value`, `location`, `confidence`, optional `revision`, `contributing_claim_ids`; lines 125-135, 421-507); `csv_exporter.extract_from_json` reads `field_name`, `extracted_value`, `field_index`, `location_metadata`; `extraction_report._collect_qc_data` reads named keys. None iterates keys. `configs/final_output_schema.json` has `additionalProperties: false`, so the key must be declared there.

### M8. The model client ignores the validation set
- **Read**: `api_client.extract_chunk` accepts `valid_location_ids` (line 441) and never uses it. The repair prompt is appended as a further user message to the same `input` list as the system message and the original user message (lines 507-509), so a repair call carries the same evidence package and the model's invalid output only as a text fragment inside the repair prompt.

### M9. Where the system instruction sits relative to the prefix
- **Read**: `_base_request_kwargs` sends the system instruction as a separate `system` message; `_shared_paper_prefix` is the start of the `user` message. Provider-side prompt caching matches the whole input prefix, system message first. The stable-prefix fingerprint in telemetry hashes `system prompt + package`, so changing either changes the fingerprint consistently within a run.

### M10. Absence vocabulary downstream
- **Read**: `multiagent-extraction` R2.1-2.5 and design (lines 740, 995) already require "a non-not-reported value cites at least one id", "not-reported with ids = verified, without = unverified", and "pre-filled fields are not requested"; they call the token "the configured not-reported value". `agreement-statistics` 1.3 treats `nr` as an explicit rating; `evaluation-harness` 3.3 computes the unsupported-answer rate over asserted values; `reviewer-ui` 4.4 offers "mark not reported". This feature fixes the token as the constant `nr`; no configuration key is introduced.

### M11. Pre-existing repair-loop fact the new rules interact with
- **Read**: `validate_chunk_output` raises at the first invalid item (`_validate_extraction_item`), so with at most `max_repair_attempts` (default 2) repairs, a chunk with several violating items could exhaust attempts one item at a time. The stricter rules make several violations in one output more likely, hence D9.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|---|---|---|---|---|
| A. Facets in existing owners + one stdlib leaf (`shown_evidence.py`) | Verdict rules and token in `validator.py`; synthesis marker in `deterministic_merge.py`; prefill rule in `evidence_index.py`; shown set in a new leaf; prompt text in `agent_schema.json`/`prompts.py` | Respects the standalone-load convention (M3); no new import edges; smallest diff | The contract is spread over facets; mitigated by a consistency test and by one README section | **Chosen** |
| B. New `pipeline/extraction_contract.py` imported by leaves | One home for token, verdicts, origins | Single file to read | Breaks standalone loads of `validator.py`/`evidence_index.py` (M3); would require editing eight test loaders | Rejected |
| C. New top-level leaf package (`src/extraction_contract/`) importable by `pipeline` and `agents` | Lets the prompt builder import the token constant | One source for token in prompt and validator | New package needs new dependency-direction rules, README, CLAUDE.md and steering edits; the prompt text is static, so a drift test costs less | Rejected |
| D. Derive the shown set by re-running selection | Recompute `select_paper_evidence` per call | No parsing | Wrong after mitigation; couples validation to selection internals; breaks when `evidence-routing` changes selection | Rejected |
| E. Parse the sent package text | `json.loads` of the exact text handed to the model | Exact by construction; independent of selection | Needs readable packages (D2) | **Chosen** |

## Design Decisions

### Decision D1: The shown set is the id list of the package text actually sent
- **Context**: Brief: "the exact ids serialised into the prompt of that call (post-mitigation)".
- **Alternatives**: (a) recompute selection; (b) record ids when serialising; (c) parse the final text. (b) is wrong after mitigation.
- **Selected**: (c), in one function, `build_shown_evidence`, called by `RepairRetryLoop.extract_with_repair` after mitigation, immediately before the first model call. The result is a frozen dataclass; repairs reuse it.
- **Rationale**: Exactness and independence from selection and routing. The user-visible bytes are the only ground truth; a test builds the real user message and extracts the ids independently (Requirement 10.3).
- **Trade-offs**: one `json.loads` of a package of at most tens of kilobytes per call.
- **Follow-up**: test that `package_sha256` equals the digest of the substring between the prefix markers of `build_user_message`.

### Decision D2: Mitigation prunes whole items for any evidence package
- **Context**: M2.
- **Alternatives**: (a) leave the flat truncation and compute the shown set tolerantly from complete objects in a truncated string; (b) item-granular pruning for every stage; (c) forbid mitigation for the chunk stage and fail.
- **Selected**: (b): `_check_and_mitigate_budget` uses the structured pruning for any `evidence_text` that parses as an evidence package, with the protected set possibly empty; non-package text (the repair prompt) keeps the flat path.
- **Rationale**: (a) gives the model garbage JSON and an ambiguous "partially shown" id. (c) turns a recoverable condition into a failure. (b) reuses code already proven on the synthesis path and keeps rejection (`TokenBudgetExceededError`) when even the protected items alone do not fit.
- **Trade-offs**: declared behaviour change for over-budget extraction-chunk prompts; unreachable at defaults (M1).
- **Follow-up**: differential test: for the synthesis path with non-empty protected ids the output is unchanged.

### Decision D3: An unreadable package yields an empty shown set (fail closed)
- **Alternatives**: fall back to the full evidence map (fail open, restores today's weakness silently); raise.
- **Selected**: empty set plus a WARNING. Every cited id is then rejected and the only conforming answer is `nr` with no ids; exhaustion fails the document like any repair exhaustion.
- **Rationale**: A guarantee that silently weakens on malformed input is not a guarantee. Production never reaches this branch (M1, M2 fix).
- **Consequence**: unit tests that pass dummy strings as the evidence text must pass a real package (46-test finding, M4).

### Decision D4: Absence token set
- **Brief default**: exactly `nr` after strip and casefold.
- **Selected**: confirmed. The canonical form `nr` is written back into the validated item.
- **Rejected**: (a) a synonym list (`n/a`, `not reported`, `none`, `unknown`): several are legitimate codebook values (`not stated`, `not reported`, `not applicable` and `unclear` appear in the category lists of 14 fields, `none` in others), so reinterpreting them would corrupt asserted answers; (b) a configurable token: adds a knob that weakens a guarantee and breaks the shared vocabulary downstream specs rely on.
- **Known limitation**: a model that writes the phrase "Not reported" as a value with `c=m` and a cited id is accepted as an asserted value unless the phrase is one of the field's own absence phrases (D25). The prompt forbids it; first-run measurement is the check (Assumption A1).
- **Amendment (review D2)**: the token stays the only canonical not-reported form, but values equal to the field's own listed absence phrases are no longer "ordinary values that must cite evidence": they are absence-like assertions (D25).

### Decision D5: Confidence is coupled to the token (`nr` if and only if `nr`)
- **Selected**: the brief default. A value with `c=nr` and an `nr` value with `c ∈ {h,m,l}` are rejected.
- **Rejected**: decoupling so that `nr` could carry `h` for "confident it is absent": the model's self-reported confidence is uncalibrated and `flagged_fields.csv` and the merge rank (`nr` lowest) treat `c=nr` as the not-reported marker; the auditable form of "how sure" is whether ids were checked.

### Decision D6 (amended): Names for the two `nr` classes are `ids_cited` and `no_ids`
- **Selected**: `nr` with at least one cited id, all shown, is `ids_cited`; `nr` with none is `no_ids`; both are accepted. The ids are validated against the shown set like any other. The classes, the CSV `absence` column values, the README and the tests use exactly these names.
- **Why renamed (review D4)**: "verified absence" overclaims. The pipeline verifies only that the cited ids were shown to the model; it cannot know that they were examined or that they are where the absence would have been stated, and a model can pad ids. `ids_cited` states what is true.
- **Added context**: the report marks rows of a document whose recorded evidence coverage is below `min_evidence_coverage_ratio` (`below_coverage`, D26), because an absence claim made on a package that covered less than the pipeline's own threshold of the paper's text is weaker still. The README states plainly: `ids_cited` means ids shown and cited, not examined.
- **Rejected**: (a) require ids on every `nr` (turns an honest "I examined nothing specific" into a repair loop and invites padding with arbitrary ids); (b) a separate field (changes the output schema the model must follow and the strict JSON schema in `api_client`); (c) keep "verified/unverified" with a footnote (a name that misleads in a column header is not fixed by a footnote).
- **Downstream**: `provenance-schema-v2` classes must follow these names (its brief currently says verified/unverified absence).

### Decision D7: An asserted value must cite at least one shown id
- **Brief**: not listed among the defaults; the task requires the whole `v`/`c`/`loc` matrix to be decided. `multiagent-extraction` R2.1-2.2 already require citation of every non-`nr` value.
- **Alternatives**: (a) accept and let provenance classify `unsupported` (status quo); (b) accept and downgrade confidence; (c) reject.
- **Selected**: (c), with the explicit legal alternative `nr`: every situation has a conforming answer, so rejection cannot trap the model.
- **Rationale**: (a) leaves ungrounded assertions in persisted outputs, invisible in the CSV and the report; (b) alters a model answer on a guess.
- **Risk**: the first real run may show a repair or failure rate the owner finds too high (A1). There is deliberately no tolerance knob (Requirement 9.3); a tolerance policy is explicitly out of scope and would be a separate, recorded decision.
- **Flagged for the owner**: this is the one verdict the brief did not pre-decide.
- **Amendment (review D2, owner-delegated)**: the rule has an enumerated exemption and an enumerated limitation.
  - *Exemption*: an absence-like assertion (a value equal to one of the field's own listed absence phrases, D25) needs no ids; if it cites ids they must be shown and mean "where I looked".
  - *Limitation (recorded, not fixed)*: yes/no/none/partial values and the values of judged or notes fields (56 and 61 are explicitly "reviewer-judged", 13 "structured notes", 62 free-text synthesis notes) stay under the citation rule. For them an uncited answer cannot be distinguished from an omission: "no external validation" and "none" are findings about the paper that need a place where the paper says so, or `nr`. The consequence is more repairs and possibly failed documents where a model has no citation for a judged value.
  - *Mitigation, not a knob*: the operational pilot gate (Requirement 9.9): before bulk runs process at least three papers, read the `codes=` repair log lines, and decide `max_repair_attempts`; no tolerance setting is added.
  - *Rejected*: exempting yes/no/none and judged fields as well (gives up the guarantee for 8 further fields on a guess about model behaviour that no real output supports; reversible later by extending the phrase rule once a pilot shows the need).

### Decision D8: Empty value
- **Selected**: accepted only as the degenerate answer (`c=nr`, no ids), normalised to `nr` and marked `pipeline_empty_value`; rejected otherwise.
- **Rationale**: it preserves the merge's existing behaviour (empty becomes `nr`) and gives the synthesised marker a real, reachable source, while a contradictory empty value (high confidence, or with ids) is a malformed answer that repair can fix.
- **Note**: the deterministic merge treats `None`/empty/whitespace identically (read, `deterministic_merge.py:108-228`); the synthesis path had no normalisation at all, so a synthesis answer `""` previously reached the output as an empty `extracted_value`. D10 closes that.

### Decision D9: Report all violating items in one repair message
- **Context**: M11.
- **Selected**: contract violations are collected over the whole output (up to 20 items) and raised together after the structural checks; structural errors keep first-error behaviour and precede contract errors.
- **Rationale**: repair attempts are few and bounded; reporting one item per attempt turns a recoverable output into an exhausted one.

### Decision D10: Where and how the synthesised marker is carried
- **Selected**: a persisted, optional field key `nr_origin` ∈ {`model`, `pipeline_empty_value`, `pipeline_prefill`} present on every field whose `extracted_value` is `nr` and on no other. Set by `reconstruct_fields` (single constructor of output fields from compact items) from (a) the merge's `MergeResult.synthesised_not_reported` list, (b) empty-value normalisation, (c) default `model`; set by the prefill builder for prefill. The key is declared optional in `configs/final_output_schema.json`.
- **Alternatives**: (a) an in-band key in the compact merge output (breaks exact-dict equality in merge tests and spreads through chunk-result handling); (b) a transient, non-persisted marker (not auditable from the output; contradicts "explicit over derived"); (c) one marker only on synthesised fields, with absence meaning "model" (infers the common case).
- **Rationale**: the persisted output is the artefact reviewers and auditors read; a three-valued closed vocabulary lets consumers test membership, and "origin present iff `nr`" is checkable.
- **Open point for `provenance-schema-v2`**: `ClaimOrigin` is `model|prefill|human|import`; the mapping of `pipeline_empty_value` is theirs to decide.

### Decision D11: Fallback prefill labels
- **Selected**: `h` only for a header-sourced value; year from PDF metadata, first-page text or file name keeps its value with `l`; author fallback (paper id) becomes `nr`/`nr` with origin `pipeline_prefill`; a year that resolves to `nr` stays `nr`. The header test is the same predicate `claim_provenance.header_citations` uses (TEI re-read for the author, persisted `year_provenance` for the year); an equivalence test pins them together.
- **Alternatives**: (a) map the year resolver's own confidences (`h` for PDF metadata, `m` for first-page text and file name): the resolver rates a PDF creation date `h`, but a creation date is often not the publication year, and none cites evidence; (b) keep the paper id as the author at `l`: still places a non-author string in an author column, and counts as an unsupported asserted value in downstream rates; (c) let fallback fields fall through to model extraction: more accurate in header-less cases but changes which fields the model is asked and the "prefilled fields are never sent to the model" rule (`product.md`, `multiagent-extraction` R2.5); recorded as a follow-up, not decided here.
- **Flagged for the owner**: the author change alters a visible output value (paper id to `nr`).
- **Amendment (review, smaller items)**: `prefill_source` must not depend on the current run having TEI content; see D29.

### Decision D12: Error wording
- Unknown id keeps today's text (`Item {i}: loc contains unknown evidence IDs: [...]`) so existing log searches keep working. Not-shown id: `Item {i}: loc cites evidence IDs that exist in this paper but were not included in the evidence package provided with this request: [...]. Cite only IDs that appear as an "id" in the evidence package you were given.` Each message names the item position and the ids; messages for several items are joined by newlines.

### Decision D13: Where the contract text lives
- **Selected**: format, citation and not-reported semantics in the system instruction (`agent_schema.json`, sent as the `system` message); one sentence in `_shared_paper_prefix` tying citability to the package ("Cite evidence only by the "id" of an item in this package; no other id is valid."); the same rules in the repair-prompt format reference.
- **Brief question "does the system prompt text live in the shared prefix?"**: no. The system instruction is a separate message and is never part of `_shared_paper_prefix`; the prefix keeps carrying only the wrapper and the package.
- **Alternatives**: (a) everything in the system instruction (the prefix goldens would not move; the "ids in this package" rule would sit far from the package it describes and would apply to the warm-up call too); (b) everything in the prefix (makes the one cache-critical string carry rules that belong to the role of the call, and grows the bytes pinned per paper).
- **Amendment**: the system instruction also carries the absence-phrase exemption and the "where I looked" meaning of cited ids on absence-valued answers (D25); the prefix sentence stays one static line.
- **Consequence**: prefix goldens move once (a constant length delta equal to the added line plus a newline for all twelve digests) and a new digest pins the system instruction.

### Decision D14: Synthesis shown set
- Its own, from the synthesis call's mitigated package. Ids that appear only in `PRIOR EXTRACTION RESULTS` (conflict candidate `evidence_ids`) are not citable: the model saw them without their text. Conflicts are structurally unreachable in the current disjoint-chunk configuration (read, `process_pdf` comments), so this affects only a future reconfiguration or an over-budget synthesis.

### Decision D15: `protected_evidence_ids`
- Unchanged. The set protects evidence items cited by `h` earlier results during synthesis mitigation; under the new rule those items are provably in the synthesis package before mitigation (same paper package) and stay shown afterwards. A lower-confidence candidate's ids may be pruned under budget pressure and are then not citable in synthesis; this is accepted and tested rather than widened, because widening reverses the Requirement 9.4 scope of the completed `token-efficient-extraction` spec.

### Decision D16: Ids removed by mitigation
- They are not shown (shown = post-mitigation). A model citing one gets the "not provided" message. `ShownEvidence.package_ids` minus `ids` is exposed so provenance can record the removals.

### Decision D17: `agent_schema.json` reconciliation
- **Selected**: keep `version`, `type` (file metadata) and `system_prompt`; delete `policies` and `extraction_rules`; `AgentSchemaValidator` requires only `system_prompt`, rejects any other top-level key except the two metadata keys, and drops `get_policies`/`get_extraction_rules` (no callers outside the class, grep).
- **Rejected**: rewriting the policies to the real contract and sending them (more tokens per call with no enforcement behind them; the contract is enforced in code and stated in the system instruction); keeping them as dormant documentation (what drifted in the first place).
- The system instruction also stops referring to `agent_schema`, `structure_schema` and `study_json`, none of which is sent.

### Decision D18: `num_chunks` below 2
- **Selected**: configuration error at load.
- **Rejected**: supporting 1. A single call would have to return about 60 fields with a generic 3,500 output-token cap (derived default, M6) in the synthesis stage with no extraction chunk; no output-size evidence exists to size it (A2), and the pipeline's chunk and synthesis structure presumes at least one of each. A clear error costs one line; an unvalidated mode costs trust.

### Decision D19: Manifest stamp
- `complete` entries gain `"extraction_contract": "2.0.0"` (additive, like `evidence_coverage`). Resumed entries without it log an INFO line. This makes mixed-contract corpora detectable and gives `cost-and-run-reporting`'s resume gate and `provenance-schema-v2`'s fingerprint something to compare, without making the pipeline reprocess anything. Note (read): `run_id` is derived from configuration fingerprints that exclude `agent_schema.json` (`provenance-core` validation report W7), so this contract change does not change `run_id`; `provenance-schema-v2` decision 8 may add `EXTRACTION_CONTRACT_VERSION` to its fingerprint. The manifest identity check (`is_stale`) is not wired into the orchestrator (`orchestrator.py` calls `load_manifest()` only), and identity fields do not cover prompt text, so a contract change would otherwise be invisible to resume.

### Decision D20: No shared contract module
- See the pattern table (B, C) and M3.

### Decision D21: No tolerance or strictness switches
- Requirement 9.3. Rejected a `contract.strict_citation` flag: a switch whose off position reintroduces the defect this feature removes would be used.

### Decision D22: Exhaustion behaviour while provenance is enabled
- Unchanged, owned by `provenance-schema-v2` decision 4. This feature only makes the retained references be judged against the shown set (the validator's sink path receives the shown set as its known set, and marks a reference present in the index but not shown with `reason: "not_shown"`). Per-attempt verdicts are provenance-independent.
- **Amendment (review D3)**: the earlier draft promised full on/off identity (Requirement 6.1) while also keeping the retention (6.4), which contradicted itself, because provenance is enabled by default. Resolution: 6.1 now states the exception explicitly and 6.5 states the consequence for the guarantee.
- **Triage, justified**: *reference-only exhaustion* (every remaining violation is `unknown_evidence_id` or `evidence_not_shown`) is retained while provenance is on, because it is exactly the class `provenance-core` 5.4 turned into recorded findings and the retained output still carries a recorded, named reference problem. *Any contract-row violation* (`uncited_value`, `confidence_nr_with_value`, `nr_value_with_confidence`, `empty_value`) fails the document with or without provenance, because nothing records those and a retained output would silently violate the contract. This is what the existing sink path already does: it tolerates only reference problems and raises on every other row, so no new mechanism is needed. Per-attempt verdicts and repair prompts stay identical on/off.
- **Rejected**: (a) failing reference-only exhaustion too (removes the divergence now, but it is `provenance-schema-v2` decision 4 and would re-point the 8 pinned tests from two specs at once); (b) retaining contract-row violations with provenance on (the provenance findings vocabulary has no row for them).

### Decision D23: Reports
- `flagged_fields.csv` gains three trailing columns, in this order: `nr_origin`, `absence`, `below_coverage`; the wide CSV exporter is unchanged (it exports values only by design, and `nr` is still `nr` in it). `absence` takes exactly: empty, `ids_cited`, `no_ids`, `pipeline`, `human`, `absence_like`. `below_coverage` is `true` or empty. See D26.

### Decision D25 (new, review D2): Absence-like assertions
- **Context**: 14 of the 62 fields (3, 5, 10, 24, 25, 27, 34, 44, 49, 50, 51, 53, 56, 61; measured with the rule below over `configs/extraction_map.json`) list a category whose meaning is an absence. Under "every non-`nr` value must cite" the model would pad arbitrary ids onto "not stated", or answer `nr`, and `nr` would then mean two things to evaluation and provenance. Gold data uses the category text.
- **Selected (owner-delegated)**: (a) ids attached to any absence-valued answer (`nr` or an absence phrase) mean "where I looked", never "where it is stated", and the prompt says so; (b) a value that equals, after strip/casefold, one of the field's own absence phrases is an *absence-like assertion*: exempt from the mandatory-citation rule (ids optional; if present they must be shown), kept as the model wrote it, never converted to `nr`, marked `absence_like` and classified `absence_like` in reports; `nr` stays the canonical token and `nr`-valued answers keep the `nr` rules.
- **Phrase derivation (fixed rule)**: split the field's `categories_or_examples` on `;`, strip a leading `Examples:`, casefold each item; an item is an absence category iff its stem (text before the first ` (`) is one of `not stated`, `unclear/not stated`, `not reported`, `not applicable`, `not addressed`, `unclear`; the field's phrase set holds each such item and its stem. Measured result: `unclear/not stated` (3, 27), `not stated` (5, 24, 34, 49, 50), `unclear` (10, 61), `not applicable` (25, 50, 51, 56), `not addressed` and `not applicable` (44), and for 53 both the full texts and the stems of `not applicable (...)` and `not reported (...)`. The derivation lives next to the extraction-map helpers (a leaf module), is called once per document from the field definitions already in `process_pdf`, and is pinned for all 62 fields by a table test; user-defined maps get phrases derived by the same rule, and a map that lists none gets the strict behaviour.
- **Persistence (minimal, enumerated)**: two optional keys in `final_output_schema.json`: `nr_origin` (enum of four) and `absence_like` (boolean `true`), each present iff its condition holds. The report class (`absence`) stays derived by one function. Rejected: a single combined enum key (couples two independent facts: an `nr` has an origin, an absence phrase does not), and a derived-only marker (not readable by consumers without the extraction map, which `provenance-schema-v2` must not be required to load).
- **Limit (honest)**: "unclear" in a judged field means ambiguous evidence, not absence; treating it as absence-like follows the owner's instruction and is a judgement the pilot should revisit. The rule is exact match only: combinations in a multi-select answer are asserted values.
- **Interface consequence**: `provenance-schema-v2` must classify these as absence-like claims (not `unsupported`) by reading the marker; its brief's statement "after both specs an accepted output contains no asserted claim without evidence" is no longer true for them.

### Decision D26 (new, review D4): Coverage context
- **Source**: the manifest `complete` entry already records `evidence_coverage.below_threshold` against `min_evidence_coverage_ratio` (`pdf_processor._build_evidence_coverage_record`). The report receives only `{"pdf", "fields"}` from the orchestrator, whose manifest key is `pdf_path.stem` (`orchestrator.py:148`) while `pdf` is `pdf_path.name`.
- **Selected**: the orchestrator adds an optional key `below_coverage` (true/false/absent) to each result entry from the manifest entry of that document; the report appends a `below_coverage` column (`true` or empty) on every flagged row of such a document. Enumerated and boolean; no change to persisted outputs, because coverage is a document property that already lives in the manifest.
- **Rejected**: a suffix on `absence` values (makes the vocabulary combinatorial), and persisting coverage into every field (duplicates a manifest fact 62 times).

### Decision D27 (new, review D1): Order of work keeps every step green
- **Finding**: with only the validator rules flipped, 26 existing tests fail; with the shown set 46; with prefill, schema and prompt changes more. A plan that flips rules before re-pointing fixtures is red for many commits, and `test_process_pdf_provenance` is order-dependent through the `sys.modules` purge in `test_pdf_processor_helpers` (read: it removes `pipeline*` modules at import).
- **Selected**: (1) a task that introduces the `xfail(strict=True)` convention (none exists in the repository today, grep) and documents it in `testing.md` before first use; (2) a shared test-side oracle of the verdict table; (3) re-point every fixture and stub that encodes the old behaviour so that it is valid under both the old and the new rules (cited ids that exist in a real package, `nr`/`c` coupling, no uncited asserted values, header TEI for stubs that expect `h` prefill, subset assertions on the manifest entry), verified alone and in full-suite order; (4) only then flip the rules, the shown set, the prefill and the prompt, each in a commit that changes no pre-existing test other than the one it owns.
- **Rejected**: flipping first and fixing tests afterwards (red commits break one-tick-per-commit); marking old tests `skip`/`xfail` (hides regressions).

### Decision D28 (new, review D6): Prefix identity under mitigation is scoped
- **Selected**: the byte-identity requirement applies while no token-budget reduction applies to any call of the paper (all three fixtures at default caps: 11.6k-15.1k estimated tokens against 100k, M1). When a reduction applies, the affected call's package is a pruning of the paper package, a WARNING names the paper, the chunk and the number of items removed, and unaffected calls keep the full package.
- **Rejected**: computing the pruning once per paper. The calls' budgets differ by stage (100k, 120k), their other prompt parts differ by chunk, and the synthesis prior context does not exist until the chunks finish, so no single pruning is exact; pruning all calls to the tightest case would remove evidence from calls that fit, which costs accuracy for cache hits, against the governing instruction.
- **Tested**: the unscoped case (no mitigation) by the goldens and cross-call equality; the scoped case by a test that a reduced call's shown set is exact, the warning is logged and an unreduced sibling keeps the full package.

### Decision D29 (new, review smaller items): `prefill_source` and the cache
- **Problem**: a cache hit with no TEI in the content (for example a later run where GROBID failed and the failure behaviour continues) must not turn a legitimate cached author into `nr`.
- **Selected**: when the evidence cache entry is written, record `author_provenance` (`tei_header` or `paper_id_fallback`) in the entry, computed from the same TEI the author was read from, not by changing `_build_items_from_tei`'s return value (a test pins `meta == {...}` at `test_evidence_index_provenance.py:241`). `EvidenceBundle` gains `author_provenance` (default empty, excluded from comparison like `year_provenance`). `prefill_source(bundle, 1)`: persisted marker if non-empty; else for an entry written before this change: a value different from the paper identifier is header-sourced (the code only ever writes `surname or paper_id`, `evidence_index.py:623`), and a value equal to the paper identifier is header-sourced only if the current TEI header says so, otherwise fallback (conservative). The year already uses the persisted `year_provenance`.
- **Cache compatibility**: no invalidation, no key change; old entries lack the key and are handled by the fallback; the package bytes are unchanged. Consequence for equivalence with `header_citations`: every cited value is header-sourced, but a header-sourced value without TEI content is not cited (no node can be recorded without TEI); Requirement 5.6 is worded accordingly.
- **Rejected**: persisting the label rather than the provenance (labels are policy and would freeze it in the cache); a cache version marker (invalidates everything for a flag that only matters in the no-TEI case).

### Decision D30 (new, review smaller items): Sanitisation invariant
- **Problem**: M5. Sanitisers can alter the token and `Aggressive` can create it.
- **Selected**: the invariant "`nr_origin` present iff `extracted_value == "nr"`" is checked on the persisted output (after sanitisation) and is made true by constraining sanitisation in `_save_pdf_output`: a field that carries `nr_origin` keeps the exact token whatever the sanitiser returns; for any other field, if the sanitiser's result is the token (strip/casefold) while the input was not, the original value is kept and a WARNING is logged. `absence_like` is computed before sanitisation and survives it; the sanitiser may rewrite the phrase's characters (for example `Aggressive` turns `unclear/not stated` into `unclearnot stated`), which is the user's configured behaviour for all values and is documented, not prevented. The writer-side check runs the invariant for all six sanitisers.
- **Rejected**: exempting `nr` fields only (leaves `N/R`-style creation); skipping sanitisation for all absence-valued fields (changes configured behaviour more than needed); letting sanitisers create `nr` and giving it a marker (a model assertion of a different value would become an absence).

### Decision D31 (new): The `human` origin
- **Selected**: `nr_origin` has a fourth value, `human`, so the enum does not need a later schema break for `reviewer-ui` 4.4 ("mark not reported"). Only the review/merge layer may write it; the pipeline never does and a test asserts no pipeline output contains it; the model-output validator has no path to it. `absence_class` returns `human`.
- **Rejected**: adding the value later (a schema change for an additive vocabulary item), or letting the pipeline accept it from a model (a model must not claim to be a human).

### Decision D32 (new): One source of truth for the contract version
- **Selected**: `EXTRACTION_CONTRACT_VERSION` in the validator module is the code constant; `AgentSchemaValidator` gains `get_version()` (the file's `version`, which is file metadata and is not sent to the model); a consistency test asserts both are equal and both equal the manifest stamp.
- **Rejected**: deriving the constant from the JSON at import (would make the leaf validator import `agents`, which tests stub), and a separate version constant in prompts.

### Decision D33 (new): Subset warning
- When a call's shown ids are not all in the evidence map (a package built from ids not in the index), the loop logs a WARNING naming the paper, chunk and ids (Requirement 1.11) and still treats them as shown. Rejected: rejecting them (they were in the prompt; the right place to surface a stale or corrupt cache is a warning, and the claim conversion already records dangling references).

### Decision D34 (new, review D7): Test isolation
- `test_pdf_processor_helpers.py` removes `pipeline*` modules from `sys.modules` at import, so the same class can exist twice (`ValidationError`, `TokenBudgetExceededError`; the existing note in `test_repair_retry.py` records the hazard). New tests resolve modules through one helper at test time and compare values (`codes`, messages, dataclass fields) rather than classes; the foundation helper provides the resolver and the plan adds the rule to `testing.md`.

### Decision D35 (new): Reserved human review of absence-like fields
- Not decided here: whether reviewers may convert an absence-like assertion to `nr`. The persisted markers allow either; `reviewer-ui` decides.

### Synthesis outcomes (design-synthesis lenses)
- **Generalisation**: the shown set is a per-call record `ShownEvidence(call_kind, chunk, ids, package_ids, digest)`; the same record serves validation, provenance and a future routing layer, with no routing-specific code here. The verdict function takes `shown` and `known` sets, so it works for any agent that cites ids.
- **Build vs adopt**: no library fits; `json`, `hashlib` and `dataclasses` suffice. The structured pruning already exists and is reused.
- **Simplification**: no new config, no tolerance layer, no new module for the contract, no changes to the provenance package or to `claim_provenance.py`.

## Risks & Mitigations

- **Repair or failure rate rises on real runs** (D7, shown-set rule) — Mitigation: single aggregated repair message (D9), explicit legal alternative `nr`, per-attempt violation-code logging and a per-document absence summary line (Requirement 9.7-9.8) so the first run can be read; no tolerance knob by decision; owner reviews the first measured run.
- **Longer outputs from `ids_cited` id lists exceed `chunk_max_tokens`** — Mitigation: the prompt asks for "the few items you examined"; truncated JSON is already a handled parse error with repair; sizing needs real output (A2).
- **Prompt change shifts model behaviour in ways not visible in tests** — Mitigation: declared change, pinned digests, CHANGELOG entry; no frequencies are asserted anywhere.
- **Hidden coupling with `provenance-schema-v2`** — Mitigation: interfaces published in `design.md` (`ShownEvidence`, `RepairRetryLoop.shown_evidence`, `nr_origin`, `EXTRACTION_CONTRACT_VERSION`); that spec's brief already says it develops against them.
- **Standalone-load regression** — Mitigation: AST guard test for the leaf modules (design, Testing Strategy).
- **Cross-spec schema edits** — `final_output_schema.json` gains an optional property; `risk-remediation` forbade changing it only for its own scope; `cost-and-run-reporting` plans an envelope key on the same file (its design line 817). Listed under Revalidation Triggers.

## Assumptions not verified (carry into implementation and the first real run)

- **A1** The effect of the new system instruction on real model behaviour (rate of repairs, of `nr`, of phrase-style absences such as "Not reported", of padded `ids_cited` id lists). No real model output exists in the repository or on this machine.
- **A2** Whether `chunk_max_tokens` (5-chunk default `{1: 3500, 2: 3000, 3: 5000, 4: 3500, 5: 2500}`; 3-chunk `{1: 4096, 2: 5000, 3: 8192}`) is large enough once `ids_cited` absences list ids. Not measured.
- **A3** That item-granular mitigation is never reached on the corpora the owner will run (true on three fixtures, M1; larger configured caps or smaller budgets would change this).
- **A4** The OCR/scanned path: documents without TEI fall back to a sentence index and prefill `{1: paper_id, 2: "nr"}`; under the new prefill rule both become `nr` with origin `pipeline_prefill`. Behaviour was read, not run.
- **A5** That GROBID header parsing yields a surname for the large majority of native papers; the fixtures yield one for all three, which is not a rate.
- **A6** That no out-of-repository consumer reads `*.extracted.json` with a strict key whitelist (the schema file has `additionalProperties: false` for writes; readers in the repository do not).

## Review response (independent review of 2026-10-12)

| Point | Outcome |
|---|---|
| D1 task order / green suite | Accepted. D27; tasks restructured: convention task first, oracle and fixtures re-pointed before any flip. The review counted about 34 failures; I measured 26 (rules only) and 46 (rules plus shown set) in M4. |
| D2 absence-type and judged values | Accepted as an owner-delegated decision. D25, amendments to D4 and D7, Requirements 2.11-2.12, 3.8, 4.1-4.2, 9.9. Verified: 14 absence-phrase fields. Not reproduced: "16 more" yes/no/none fields (found 8 whole-item yes/no/none/partial fields); fields 58 and 60 are not judged. |
| D3 contradiction 6.1 vs 6.4 | Accepted. D22 amendment; Requirements 6.1, 6.4, 6.5. |
| D4 overclaim | Accepted. D6 amendment (`ids_cited`/`no_ids`), D26 (`below_coverage`). |
| D5 7.2 templates | Accepted. Requirement 7.2; each template pinned as a literal string. |
| D6 prefix identity under mitigation | Accepted with the scoping option. D28. |
| D7 test isolation | Accepted. D34, Requirement 10.9. |
| Smaller: 2.9 vs design cap | Fixed: "up to 20 violations" in both. |
| Smaller: version single source | D32. |
| Smaller: `human` origin | D31. |
| Smaller: cache hit without TEI | D29. |
| Smaller: subset warning | D33, Requirement 1.11. |
| Smaller: "5 normaliser classes" | **Not confirmed.** There are six concrete normalisers (M5); the reviewer's other observations about `Aggressive` (`N/R`, `n.r.` to `nr`) and upper-case ` NR ` are correct and measured. D30. |
| Smaller: task 11.5 | Accepted. The `provenance-schema-v2` brief already holds an "Interfaces published" section; the task is now verify-and-update (it needs the renamed classes, the absence-like marker and `human`). |

## References

- `.kiro/specs/extraction-contract-hardening/brief.md` — problem, desired outcome, recommended defaults.
- `.kiro/specs/provenance-schema-v2/investigation-2026-10-12.md` — W1 (`nr` claims), W3 (shown set), cross-cutting findings.
- `.kiro/specs/multiagent-extraction/requirements.md` R2.1-2.5, `design.md` lines 82, 740, 995 — absence vocabulary and the validator keys it extends.
- `.kiro/specs/evidence-routing/design.md` line 79 — the insertion point that will change selection.
- `.kiro/specs/risk-remediation/design.md` — failure-record shape and amendment style; `tasks.md` — task format.
- `.kiro/specs/provenance-core/requirements.md` 5.4 — the retention behaviour left untouched.
- Code read: `src/pipeline/{pdf_processor,validator,evidence_index,deterministic_merge,extraction_report,token_budget,manifest,claim_provenance}.py`, `src/agents/openai/{api_client,prompts}.py`, `src/agents/validator.py`, `src/utils/config_utils.py`, `configs/{agent_schema,final_output_schema,extraction_map}.json`, `src/artifact_generation/csv_exporter.py`, `src/provenance/adapters/claim_adapter.py`.
