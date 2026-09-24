# Changelog

All significant changes to the repository are recorded here. This file is permanent
and should never be deleted. Add a brief entry whenever a spec is implemented,
steering docs change, README files change, or any other significant code change
occurs.

## [2026-09] — Resolve the `risk-remediation` 10.1 validation finding by rewording (`risk-remediation`)

Requirement 10.1 ("≥60% evidence coverage for a 30,000-char paper") was unsatisfiable as worded
while any finite item cap exists. The spec owner chose to reword it rather than drop the
150-item cap: the 60% floor is now guaranteed only on the reference bioRxiv fixture and is
monitored (not guaranteed) elsewhere. No code or config value changed.

- `requirements.md` 10.1 reworded (original wording preserved in its note); `design.md`,
  `validation-report.md` §1, `tasks.md` banner and `spec.json` (`phase: implemented`) updated.
- Coverage-rationale wording in `configs/config.yaml`, `configs/README.md` and
  `.kiro/steering/config.md` scoped to the reference paper; enforced by the doc-site test.
- New `test_mean_item_length_does_not_guarantee_the_floor` pins why a sentence-length premise
  was rejected (mean 120 chars, coverage 0.40).

## [2026-09] — Implement the `risk-remediation` spec (Requirements 1–11)

Eleven defects in shipped behaviour, specced, designed and implemented task-by-task with an
independent review per task. Full suite after the work: 1920 passed / 3 skipped (was 1470 / 2).
Requirement 8 shipped separately, see the entry below.

**Behaviour changes operators should know about**

- **Annotation identifiers change.** `urn:evitrace:anno:<uuid4>` → `<uuid5>` derived from
  (document source, page, occurrence, sentence text), scoped per paper via
  `base_uri=urn:evitrace:document:<pdf_name>`. Ids are now stable across runs and distinct
  across papers; every annotation in existing `outputs/*.json` gets a new id on re-run.
- **Cached parses are no longer all-native.** A new sidecar `{sha256}.pages.json` beside the
  cached TEI stores per-page scan classification; a cache hit now routes scanned pages to OCR
  exactly like a first run, so re-running a mixed PDF no longer yields different QC branches.
  Re-runs of mixed/scanned PDFs therefore cost OCR time again. The file is safe to delete.
- **QC per-page metrics see real pages.** GROBID TEI blocks previously all landed on page 0
  because the coords parser expected a format GROBID does not emit. Consequently
  `extraction_coverage_ratio` for non-GROBID branches stops falsely triggering (~0.07 → ~1.0)
  and `min_chars_per_page` now compares real pages — rater pass/fail, and hence adjudication,
  can differ from before on the same document.
- **Evidence indices cached on disk keep their old ids, sections and pages** until the
  evidence cache directory is cleared (the cache key carries no parser version).
- **`max_evidence_chars_per_chunk` default 10000 → 30000** (the yaml value now matches its own
  comment and the loader default). New keys `retry.max_repair_attempts` (2) and
  `extraction.min_evidence_coverage_ratio` (0.6), overridable via `OPENAI_MAX_REPAIR_ATTEMPTS`
  and `OPENAI_MIN_EVIDENCE_COVERAGE_RATIO`.
- **Manifest entries gain `failures` and `evidence_coverage`.** Chunk exhaustion, synthesis
  exhaustion, schema validation and output-write errors now share one record shape
  `{stage, chunk, error_type, last_error, attempts}`; the `failed_chunks` int list is kept for
  compatibility and a new `failed_output_write` status exists. Completed papers record
  `{ratio, selected_chars, substantive_chars, below_threshold}`.
- **GROBID requests now actually ask for coordinates** (`teiCoordinates` was sent comma-joined,
  which GROBID ignores), so production TEI carries `coords` and evidence items carry pages.

**Per requirement**

- **R1** — final-output validation already worked; added the missing negative-path coverage and
  made a write error record `failed_output_write` instead of leaving no status.
- **R2** — reconciliation follows the adjudication decision: primary is the adjudicated branch,
  secondary the first remaining one, so the OCR-named branch is no longer dropped (its bboxes
  used to vanish). Provenance records `primary_branch_source`, `secondary_branch_source`,
  `branch_selection`.
- **R3** — deterministic annotation ids (above).
- **R4** — OCR provenance end to end: blocks carry `source`/`ocr_derived`; the reconciler is the
  sole sentence producer and derives sentences from OCR blocks on scanned pages; each sentence
  gets an aligned location entry (page, offsets, occurrence, bbox); annotations take their region
  from the sentence's own block and warn when one is missing. `project()` no longer reads
  `unified.structural`.
- **R5** — the synthesis stage runs through the shared repair loop with its prior context,
  stage budget and protected evidence ids; the attempt limit is configurable and shared with
  the chunk stage.
- **R6** — page classification persisted and reused (above).
- **R7** — figures and tables are attributed to the first body section that cites them
  (`<ref target="#id">`) instead of inheriting the last heading, with a neutral `body` fallback;
  each figure/table yields exactly one evidence item (tables used to produce two).
- **R9** — quality reports carry their branch's source, so agreement keys are named
  (`grobid_vs_paddleocr`, previously a single `_vs_`) and adjudication names a real extractor.
- **R10** — evidence selection is measured (`select_paper_evidence` → `EvidenceSelectionStats`)
  and the per-paper coverage ratio is recorded with a warning below threshold; budgets and their
  documentation reconciled across yaml, loader, README and steering.
- **R11** (new during the spec) — TEI coordinates: request format fixed, one canonical parser
  (`parse_tei_coords`) with mirrors in the evidence index and QC, sentence pages inherited from
  the enclosing paragraph.

**Tests and fixtures**

- `tests/fixtures/grobid_tei/` — three real GROBID 0.8.2 outputs from open-access papers, with a
  provenance README; every hand-built fixture mis-modelled GROBID's structure and coordinate format.
- `tests/helpers/grobid_tei.py` — shared fixture loaders and a coordinate-string table.
- `tests/test_migration_risk_remediation_{bug_condition,preservation}.py` — the migration pair:
  16 sub-checks that fail on the pre-spec tree and pass now, and 14 that pass on both.
- `tests/src/pipeline/test_provenance_end_to_end.py` — mixed-PDF run twice (miss then cache hit)
  through the real QC, reconciliation and annotation chain.

**Spec documents**: `.kiro/specs/risk-remediation/{requirements,design,tasks,research}.md`.

## [2026-09] — Enforce the QC extension-contract ABCs (`risk-remediation` Requirement 8)

`QualityMetrics`, `InterRaterMetrics` and `AdjudicationRules` in
`src/quality_control/models.py` were documented as abstract and used `@abstractmethod`,
but did not inherit from `abc.ABC`, so Python never enforced the contract: the bases and
any incomplete subclass could be instantiated. **Behaviour change:** all three now inherit
`ABC`; instantiating one directly, or a subclass that omits `passes_check` / `compute` /
`adjudicate`, raises `TypeError` naming the missing method. Fields, defaults, properties
and method signatures are unchanged, and the shipped implementations (`QualityReport`,
`InterRaterReport`, `AdjudicationDecision`, `ExtractionCoverageReport`) are unaffected.

- `tests/src/quality_control/test_domain_agnosticism.py`: the all-mock pipeline tests
  instantiated the bases directly; they now use minimal test-local stub subclasses (not the
  `builtin_impls` defaults, to keep the domain-agnosticism test independent of them).
- `tests/src/quality_control/test_qc_models.py`: added regression tests for ABCMeta
  enforcement, direct-instantiation `TypeError`, incomplete-subclass `TypeError`,
  complete-subclass success, and built-in implementation preservation.
- Implemented directly rather than via a new bugfix spec: it is covered by the existing
  `risk-remediation` Requirement 8 (still at `requirements-generated`), and the code change
  is three class headers. `TextProcessor`/`SentenceSegment` deliberately untouched.

## [2026-07] — Add roadmap steering doc; decompose idea docs into specs

Turned the three loose idea documents in `.kiro/specs/feature/` into a dependency-ordered
spec decomposition and removed that non-standard directory. No production code changed.

- Added `.kiro/steering/roadmap.md` (new steering doc, `inclusion: always`): the plan of
  record — 12 new specs in dependency order, 2 existing-spec updates, direct-implementation
  items, cross-cutting NFRs, standing product boundaries, and 10 unresolved open questions
  carried over from the multiagent doc.
- Added `brief.md` for 12 new specs: `provenance-core`, `provenance-audit-export`,
  `public-private-provenance`, `privacy-core`, `privacy-transformations`,
  `agreement-statistics`, `evidence-routing`, `multiagent-extraction`, `evaluation-harness`,
  `corpus-and-schema-builder`, `cost-and-run-reporting`, `reviewer-ui`.
- Moved `.kiro/specs/feature/{evitrace_multiagent,privacy_requirements,provenance_requirements}.md`
  to `.kiro/specs/archive/original-idea-documents/`. Archived rather than deleted: ~300
  requirement citations across the briefs resolve against them. All three number their
  requirements `R1`–`R14`+, so citations must carry a `multiagent`/`priv`/`prov` prefix.
- Moved the parked, deliberately-unread `merckle tree.md` to
  `.kiro/specs/public-private-provenance/parked-merkle-tree-idea.md`, beside the spec that
  will own it (prov R10). `.kiro/specs/feature/` is now removed.
- Resolved a product contradiction: the multiagent doc required a PDF annotation GUI while
  `xtrace-toolkit` ruled one out permanently. Decision: headless core first, reviewer UI
  sequenced last as a consumer. `xtrace-toolkit/requirements.md` §2 and §7 amended
  accordingly (NFR-2 itself unchanged — it constrains extraction/QC, which a separate UI
  consumer does not violate); `xtrace-toolkit/research.md` decision D3 annotated as
  superseded rather than deleted.
- Added `.kiro/specs/risk-remediation/` (requirements only) from the loose
  `.kiro/specs/risk-mediation.md`, which moved to that spec's `legacy-design-reference.md`.
  **Its finding C1 was found invalid**: `final_output_schema.json` declaring `domain_group`
  as `integer` is correct, because `extraction_map.py::_build_field_lookup` parses the
  descriptive string to its integer prefix before emission. Recorded in the spec so the
  schema is not "fixed" into breaking. The audit predates commit `8daf0a0`.
- `CLAUDE.md`: repointed the steering/specs paragraph at the new layout and `roadmap.md`.

## [2026-07] — Case-insensitive matching now uses `casefold()`; 5 property tests repaired

Five Hypothesis property tests failed on a clean checkout. One had found a real
defect in the lexical matching path; the other four were imprecise strategies.

**Behaviour change (`text_processing`):** `WhitespaceNormalizer.normalize()` and
`AggressiveNormalizer.normalize()` now call `str.casefold()` instead of
`str.lower()`. `str.lower()` implements the Unicode Final_Sigma rule, so `"Σ"`
lowercases to `"ς"` at the end of a word but `"σ"` elsewhere. A needle ending in
`Σ` therefore normalised differently from the identical substring inside a
longer text, and `LexicalMatcher.search()` returned `None` for a substring that
was demonstrably present. Scientific prose is full of Greek letters, so this was
a genuine matching gap rather than a theoretical one. `casefold()` maps both
sigma forms to `"σ"`.

Callers should be aware this is a real semantic change, not a refactor: both
normalisers are used by `LexicalMatcher` (Pass 1 and Pass 2), which backs
Tier-2 `exact_match_search` in QC. Matching is now slightly more permissive —
`casefold()` also folds e.g. `"ß"` to `"ss"` and `"İ"` to `"i̇"`. Neither
normaliser preserved length or character offsets before this change (both
collapse whitespace; `AggressiveNormalizer` also strips punctuation), so the
length changes casefold introduces add no new constraint on callers. No
offset-dependent code consumes their output.

- `src/text_processing/normalizers.py`: `.lower()` → `.casefold()` in both
  `normalize()` implementations, each with a comment recording why.

**Test-only fixes** (no production change; distinct from the similarly-named
entry below, which concerned config/parsing boundaries):

- `tests/src/pipeline/test_manifest_resume_properties.py`: `_corrupt_content_st`
  applied its `_is_not_valid_json` filter to the free-text branch only, while the
  binary branch could emit `b"0"`, which decodes to `"0"` and parses as the JSON
  number zero. Three tests then asserted that `_is_output_valid` and
  `_load_completed_result` reject *valid* JSON as corrupt — the inverse of their
  documented contract. Moved the filter onto the whole strategy.
- `tests/src/utils/test_safe_logging_properties.py`: the test asserted the full
  response never appears verbatim in the log. That is false when the withheld
  remainder is itself a prefix of the `"..."` marker (response `"0"*14 + "."`
  with `max_chars=14`), because the marker supplies the trailing dot. No content
  beyond `max_chars` is disclosed, so a verbatim-substring check was the wrong
  formalisation for that input; excluded via `assume()`. The precise invariant —
  the log carries exactly `response[:max_chars]` plus the marker — was already
  asserted separately and is unchanged.

Full suite after the change: 1448 passed, 2 skipped (fast); 22 passed (slow).

## [2026-07] — Update stale tests to match intentional config/parsing fixes

Five tests had been left behind by two earlier `fix:` commits and were failing
on a clean checkout. In both cases the source was correct and the tests were
stale; no production code changed.

- `tests/src/pipeline/test_extraction_map_grouping.py`: commit `8daf0a0` made
  `_build_field_lookup` parse `domain_group` to its integer prefix. This is a
  deliberate type boundary — `structure_schema.json` requires the raw
  `extraction_map.json` value to be the descriptive string
  (`"1. Study identification"`), while `final_output_schema.json` requires the
  emitted field dicts to carry an `integer`. Updated the value assertion, and
  fixed the Hypothesis strategy which generated arbitrary `st.text()` for
  `domain_group` instead of the mandated `"<int>. <label>"` shape. Added an
  `isinstance(..., int)` assertion to pin the output side of the boundary.
- `tests/src/utils/test_quality_control_config.py`: commit `c7ea055` switched the
  default sentence tokenizer from `scispacy` to `nltk_punkt`. Updated three
  assertions to match `_QC_DEFAULTS` and `configs/config.yaml`.

## [2026-07] — Add Kiro agentic-SDLC skills; fix stale `specs/` paths

Committed the `.claude/skills/kiro-*` spec-driven-development skills and
`.kiro/settings/templates/` scaffolding, and updated `CLAUDE.md` to point at the
new `.kiro/` layout.

- Added `.claude/skills/kiro-*/` (discovery, spec-init/requirements/design/tasks,
  batch/quick, impl, review, debug, validate-*, steering, verify-completion).
- Added `.kiro/settings/templates/` (specs, steering, steering-custom).
- `CLAUDE.md`: repointed six stale `specs/steering/*` and `specs/feature|archive/`
  references to `.kiro/steering/*` and `.kiro/specs/*`.

## [2026-07] — Lift steering to `.kiro/steering/`

Moved `.kiro/specs/steering/` up to `.kiro/steering/` to match the stock Kiro
layout (steering is project-wide, not a spec). Git renames; history preserved.
No code/config referenced the path.

## [2026-07] — Standardize specs under `.kiro/specs/`

Moved the entire top-level `specs/` tree to `.kiro/specs/` to standardize on the
Kiro convention (all moves are git renames; history preserved). No code, config,
or docs referenced the old `specs/` paths, so nothing else changed.

- `specs/steering/`, `specs/feature/`, `specs/archive/`, `specs/src-layout-migration/`,
  `specs/token-efficient-extraction/`, and `specs/risk-mediation.md` → `.kiro/specs/`.
- Added `.kiro/specs/xtrace-toolkit/` (Phase-1 spec: requirements, design, tech,
  research, spec.json) for the evidence-traceability toolkit.

## [2026-07] — Make PyMuPDF an optional (AGPL) dependency

PyMuPDF (`fitz`) is AGPL-licensed. To keep the default install on
permissively-licensed libraries, it was moved out of the required dependencies
into the `ocr` extra, and its runtime uses now degrade gracefully when it is
absent. Native (text-layer) PDFs are handled by the GROBID + pdfplumber path
with no PyMuPDF present.

- Removed `PyMuPDF>=1.24.0` from required deps in `requirements.txt` and
  `pyproject.toml`; added it to the `ocr` optional-dependencies extra.
- `pipeline/extraction_pipeline.py`: `_run_scan_detector` now falls back to
  treating all pages as native (page count via pdfplumber) with a warning when
  `fitz` is not installed, instead of raising `ImportError`. Scan detection and
  the OCR path it gates require the `ocr` extra.
- `pipeline/evidence_index.py`: figure/table crop generation
  (`attach_table_figure_crops`) now guards its `import fitz` and skips crops
  with a warning when PyMuPDF is absent. (Year-heuristic fitz uses were already
  guarded.)
- Updated README install docs and the dependency list to mark PyMuPDF optional.

## [2026-05] — Remove `pdf_extractor/annotation/` wrapper

Deleted the `pdf_extractor/annotation/` subpackage which was a thin proxy to
`artifact_generation/w3c_annotation.py`. Moved `AnnotationRecord` dataclass and
`project()` function directly into `artifact_generation/w3c_annotation.py` so all
W3C annotation logic lives in one place. Updated all imports, READMEs, and
steering docs.

- Deleted `pdf_extractor/annotation/__init__.py` and `pdf_extractor/annotation/w3c_annotation.py`
- Consolidated `AnnotationRecord`, `project()`, and `generate_w3c_jsonld()` in `artifact_generation/w3c_annotation.py`
- Updated `artifact_generation/__init__.py` to export `AnnotationRecord` and `project`
- Updated `pipeline/extraction_pipeline.py` import to use `artifact_generation.w3c_annotation.project`
- Updated `tests/pdf_extractor/test_w3c_annotation.py` imports
- Scrubbed references from all READMEs and steering docs

## [2026-05] — Steering docs drift fix (`fix_steering_drift`)

Updated all four steering documents to match the current codebase state after the QC migration and text-processing migration.

- Removed `pdf_extractor/utils/` directory entirely (was empty after migration; only contained empty `__init__.py` and a migration notice README).
- `product.md` — Fixed architecture tree: removed deleted `pdf_extractor/utils/` entries (text_utils, embedding_utils, layout_utils migrated); renamed `quality_control/defaults/` to `builtin_impls/`; added `quality_control/checks/` and `local_metrics.py`; added `VerificationResult` to data models table; updated module responsibilities table; added `text_processing` to dependency direction rule; removed deleted `utils/text_processor.py` entry; fixed `LocalQCReport` → `ExtractionCoverageReport`; fixed `semantic_qc` → `semantic_verification` in config quick-ref.
- `config.md` — Fixed `text_processor.class` default from `utils.text_processor.TextProcessor` to `text_processing.base.ScispaCySentenceSegment`; clarified addons section (disabled by default, enable with URL).
- `testing.md` — Expanded test layout tree to list all actual test files; added `text_processing → quality_control` to enforced dependency rules; updated TextProcessor mocking to reference `text_processing.base`; added `text_processing.*` to conftest resolution note; updated "What Is and Isn't Tested" table.

## [2026-05] — Text Processing Migration (`text-processing-migration`)

Extracted all text processing utilities into a standalone `text_processing/` package at the repository root. This completes Phase 2 of the QC/TextProcessor split.

**New package: `text_processing/`**
- `base.py` — `TextProcessor` ABC (6 abstract methods), `SentenceSegment` ABC, and 5 concrete sentence-segmentation backends (ScispaCy, WtpSplit, NLTKPunkt, SpacySentencizer, Stanza)
- `normalizers.py` — `WhitespaceNormalizer`, `AggressiveNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`, `OcrCleaner`
- `tokenizers.py` — `SimpleWordTokenizer`
- `matchers.py` — `LexicalMatcher` (two-pass exact string match), `SemanticMatcher` (FAISS-based)
- `embedding.py` — `EmbeddingProcessor` (lazy-loaded sentence-transformers + FAISS)

**Deleted legacy files:**
- `utils/text_processor.py` (migrated to `text_processing/base.py`)
- `pdf_extractor/utils/text_utils.py` (migrated to `text_processing/matchers.py` + `text_processing/normalizers.py`)
- `pdf_extractor/utils/embedding_utils.py` (migrated to `text_processing/embedding.py`)

**Updated callers:**
- `pdf_extractor/processing/sentence_processor.py` — replaced `normalise_text()` with `LineHealingNormalizer` instance
- `pipeline/extraction_pipeline.py` — replaced `exact_match_search`/`semantic_search` imports with `LexicalMatcher`/`SemanticMatcher`
- `quality_control/quality_control.py` — updated `_load_text_processor` default to `text_processing.base.ScispaCySentenceSegment`
- `utils/config_utils.py` — updated `_QC_DEFAULTS` class path
- `configs/config.yaml` — updated `text_processor.class`

**New tests:**
- `tests/text_processing/` — full test suite: ABC enforcement, normalizer examples + PBT, tokenizer, matcher examples + PBT, embedding (mark slow), import isolation, deleted path verification
- `tests/steering/test_text_processing_separation.py` — AST-walker enforcing `text_processing/` does not import from `quality_control/`
- Added `("text_processing", "quality_control")` forbidden pair to `tests/test_dependency_directions.py`

**Updated steering:**
- `.kiro/steering/product.md` — architecture diagram and module responsibilities table updated
- `.kiro/steering/testing.md` — test layout table updated with `tests/text_processing/`

## [2025-07] — QC migration (`qc-migration`)

Reorganised the `quality_control/` package with descriptive naming throughout, a new
`checks/` sub-package for injectable QC check classes, and updated config keys. This
is Phase 1 of the QC/TextProcessor split; Phase 2 (TextProcessor migration) must not
begin until all tasks here are complete.

**Renamed symbols:**
- `LocalQCReport` → `ExtractionCoverageReport` (in `quality_control/local_metrics.py`)
- `LocalQCMetricRecord` → `ExtractionCoverageMetricRecord` (in `quality_control/models.py`)
- `_check_grobid_vs_native_ratio` → `_check_extraction_coverage_ratio` (in `local_metrics.py`)
- metric name `"grobid_vs_native_ratio"` → `"extraction_coverage_ratio"` (all `ExtractionCoverageMetricRecord` instances)

**Renamed config keys:**
- `quality_control.local_metrics.grobid_vs_native_ratio_threshold` → `extraction_coverage_ratio_threshold`
- `quality_control.semantic_qc` → `quality_control.semantic_verification`

**Updated `metrics_hierarchy` keys** (in `QCBundle` and all write sites):
- `"local_metrics"` → `"extraction_coverage"`
- `"exact_match"` → `"source_text_verification"`
- `"semantic_match"` → `"semantic_verification"`

**Deleted:**
- `quality_control/defaults/` directory (all contents removed)

**Added:**
- `quality_control/builtin_impls/` — replacement for `defaults/`; exports `QualityReport`, `InterRaterReport`, `AdjudicationDecision`
- `quality_control/checks/` package — exports `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck`, `build_task_quality_scaffold`
- `quality_control/checks/source_text.py` — `SourceTextPresenceCheck` (injected lexical matcher)
- `quality_control/checks/semantic_source.py` — `SemanticSourceVerificationCheck` (injected semantic-search dependency; three `on_index_unavailable` modes)
- `quality_control/checks/extractor_agreement.py` — `ExtractorAgreementCheck` (optional; disabled by default)
- `quality_control/checks/task_quality.py` — `build_task_quality_scaffold` (JSON-serializable scaffold for 8 task-quality metrics)
- `VerificationResult` dataclass to `quality_control/models.py` (fields: `check_name`, `status`, `score`, `evidence`, `details`; score constrained to `[0.0, 1.0]`)

**Added config keys** (in `_QC_DEFAULTS` and `configs/config.yaml`):
- `quality_control.source_text_verification.enabled` (default `true`)
- `quality_control.semantic_verification.enabled` (default `false`)
- `quality_control.semantic_verification.similarity_threshold` (default `0.85`)
- `quality_control.semantic_verification.max_sentences` (default `10000`)
- `quality_control.semantic_verification.model_name` (default `"BAAI/bge-base-en-v1.5"`)
- `quality_control.semantic_verification.on_index_unavailable` (default `"skip"`)
- `quality_control.semantic_verification.extractor_agreement.enabled` (default `false`)
- `quality_control.semantic_verification.extractor_agreement.len_filter` (default `40`)
- `quality_control.semantic_verification.extractor_agreement.max_examples` (default `10`)
- `quality_control.task_quality_scaffold.enabled` (default `true`)

## [2026-05] — Full README rewrite to reflect current codebase

All 13 README files rewritten to match the current architecture. The previous
READMEs contained stale references to the old waterfall-cascade architecture,
`config/` (now `configs/`), Tesseract, `extract_pdf()`, `QCContext`, `BranchOutput`,
`layout_utils`, and the old test layout.

Key corrections across all READMEs:
- Root README: updated workflow (evidence index, pre-filled fields 1–2, W3C annotations),
  repository structure (added `pipeline/extraction_pipeline.py`, `pdf_extractor/annotation/`,
  `quality_control/validate_context.py`, `quality_control/defaults/`, `quality_control/concerns/`),
  outputs table (added `evidence_cache/` files), technologies (added `jsonschema`).
- `utils/README.md`: added `text_processor.py` and `grobid_manager.py` documentation;
  updated `config_utils` return values to include evidence cache and GROBID failure behavior keys;
  corrected config path from `config/` to `configs/`.
- `quality_control/README.md`: updated pipeline diagram (4 stages, not 5); documented
  `validator.py`, `structure_validator.py`, `validate_context.py`, `defaults/`, `concerns/`;
  corrected `metrics_hierarchy` key names (`local_metrics`, `exact_match`, `semantic_match`);
  added dependency direction rule note.
- `pipeline/README.md`: added `extraction_pipeline.py` as single source of truth;
  documented `evidence_index.py` (EvidenceBundle, build_or_load_evidence_bundle,
  build_chunk_evidence_package, attach_table_figure_crops); updated `pdf_processor.py`
  steps to include evidence index and pre-filled fields; corrected config path.
- `pdf_extractor/README.md`: removed cascade/tier/Tesseract references; documented
  `pdf_validator.py`, `annotation/` sub-package; updated output artifact schema.
- `pdf_extractor/extraction/README.md`: corrected backend roles (pdfplumber is structural
  authority, not PyMuPDF); documented `PaddleOCRBlockDict`; updated `classify_page` signature.
- `pdf_extractor/processing/README.md`: added `text_processor` parameter to
  `process_sentences`; documented `is_noise` patterns.
- `pdf_extractor/utils/README.md`: removed `layout_utils.py` (not present); documented
  `semantic_search` return dict; corrected `embedding_utils` function signatures.
- `agents/README.md`: added `validator.py` / `AgentSchemaValidator` documentation;
  updated `get_system_prompt()` callable (replacing removed `SYSTEM_PROMPT` constant).
- `agents/openai/README.md`: updated `extract_chunk` to return raw text (not validated list);
  documented `source_package` parameter (evidence package, not `pdf_text`); updated
  `paper_cache_key` signature; documented `_response_text`, `_base_request_kwargs`.
- `configs/README.md`: corrected directory name from `config/` to `configs/`; added
  `extraction_map.json`, `agent_schema.json`, `structure_schema.json` documentation;
  added full `quality_control` YAML including `grobid_integration`, `scan_detection`,
  `ocr`, `text_fidelity`, `section_verification`, `addons`.
- `tests/README.md`: completely rewritten to reflect current test layout
  (`tests/agents/`, `tests/pipeline/`, `tests/quality_control/`, `tests/utils/`);
  documented all test files; added dependency direction and migration regression sections.
- `tests/pdf_extractor/README.md`: updated file list to match current test files
  (renamed backends, added `test_scan_detector.py`, `test_scan_detector_routing.py`,
  `test_w3c_annotation.py`; removed stale entries).
Format: `## [date] — title` followed by a short description of what changed
and why. No need to be exhaustive — just enough for a future reader to
understand what happened and where to look.

## [2026-05] — schema-validator-split spec complete

All tasks for the `schema-validator-split` spec are now implemented and all tests pass (787 passed, 2 pre-existing failures unrelated to this spec). The full validation architecture refactor is complete:

- Generic `Validator` base class + `ValidationResult` frozen dataclass in `quality_control/validator.py`
- `StructureSchemaValidator` in `quality_control/structure_validator.py` (sole reader of `structure_schema.json`)
- `validate_qc_context_input` migrated to `quality_control/validate_context.py`
- `PDFValidationError` + `validate_pdf` standalone function in `pdf_extractor/pdf_validator.py`
- `SYSTEM_PROMPT` constant removed from `agents/openai/prompts.py`; replaced with `get_system_prompt()` callable
- `validate_qc_context_input` hard-removed from `pipeline/validator.py`
- Static AST dependency-direction test in `tests/test_dependency_directions.py` (8 tests, all pass)
- All forbidden cross-package import violations resolved

## [2026-05] — schema-validator-split task 11.1: static AST dependency-direction test + architectural fixes

Implemented `tests/test_dependency_directions.py` (already existed) and fixed all 7 forbidden cross-package import violations it detected. The test enforces Requirements 9.1–9.6 by recursively inspecting AST of all `.py` files in each package.

Architectural changes to eliminate violations:
- Moved `pdf_extractor/extraction_pipeline.py` → `pipeline/extraction_pipeline.py` (resolves `pdf_extractor` → `quality_control` violation)
- Removed `from pdf_extractor.utils.text_utils import exact_match_search, semantic_search` from `quality_control/quality_control.py`; made these injectable via `exact_match_fn`/`semantic_search_fn` keyword args on `run_quality_control`
- Moved annotation chain (`w3c_annotation.project`, `generate_w3c_jsonld`) from `quality_control/quality_control.py` into `pipeline/extraction_pipeline.py`
- Removed `from pipeline.validator import ValidationError, validate_chunk_output` from `agents/openai/api_client.py`; `extract_chunk` now returns raw text; validation moved to `pipeline/pdf_processor.py`
- Removed `from quality_control.models import UnifiedRecord` from `pdf_extractor/annotation/w3c_annotation.py`; replaced with `Any` type hint
- Removed `_structure_validator.validate_chunk_output` call from `pipeline/validator.py` (schema mismatch with compact LLM format)
- Updated `pipeline/orchestrator.py` to use relative import for `extraction_pipeline`
- Updated tests to match new API: `extract_chunk` returns `str`, annotation chain no longer in `run_quality_control`

## [2026-05] — schema-validator-split task 9.1: refactor `pipeline/validator.py` — delegate to `StructureSchemaValidator`, remove `validate_qc_context_input`

`pipeline/validator.py` now imports `StructureSchemaValidator` from `quality_control.structure_validator` and creates a module-level `_structure_validator` singleton. `validate_chunk_output` delegates structural field validation to `_structure_validator.validate_chunk_output({"extractions": data}, lambda x: x)` after existing JSON parsing and raises `ValidationError` when `result.is_valid` is `False`. `validate_qc_context_input` has been hard-removed (no shim, no re-export). Satisfies Requirements 8.1, 8.2, 8.3, 8.6, 10.2, 10.4.

- Added: `from quality_control.structure_validator import StructureSchemaValidator`
- Added: `_structure_validator = StructureSchemaValidator()` module-level singleton
- Modified: `validate_chunk_output` — structural delegation after field-level checks
- Removed: `validate_qc_context_input` (hard removal)
- Removed: `from quality_control import QCBundle` (no longer needed)

## [2026-05] — schema-validator-split task 8.1: replace `SYSTEM_PROMPT` with `get_system_prompt()` in `agents/openai/prompts.py`

The module-level constant `SYSTEM_PROMPT` has been removed from `agents/openai/prompts.py` and replaced with a `get_system_prompt() -> str` callable that delegates to the `agent_schema_validator` singleton from `agents`. This satisfies Requirements 5.3, 5.4, 10.1, and 10.3 (hard removal — no re-export, no alias).

- Removed: `SYSTEM_PROMPT: str = agent_schema_validator.get_system_prompt()`
- Added: `def get_system_prompt() -> str` (delegates to `agent_schema_validator.get_system_prompt()`)

## [2026-05] — Rename `BranchOutput` → `Candidate`; generalize fields

`BranchOutput` was renamed to `Candidate` to reflect that the QC pipeline is domain-agnostic — the contributor can be an extractor, an LLM agent, a human annotator, or anything else.

Field changes on `Candidate`:
- `extractor: str` → `source: str` (canonical field name)
- `branch: int` → `index: int`
- `.extractor` and `.agent` are now both read-only properties aliasing `source`

`QualityReport` in `quality_control/defaults/` updated to match: `extractor` → `source`, `branch` → `index`, with `.extractor` and `.agent` as aliases.

Updated across all call sites: `extraction_pipeline.py`, `pdf_extractor.py`, `quality_control/`, `pipeline/`, all tests, steering docs.

## [2026-05] — Rename `QCContext` → `QCBundle`

`QCContext` was renamed to `QCBundle` across the entire codebase. The new name better reflects what the object is — the bundled outputs of one QC pipeline run (branches, reports, IAA metrics, adjudication decision, unified record, metrics hierarchy) — rather than the vague "context" suffix.

- Renamed in: `quality_control/models.py`, `quality_control/quality_control.py`, `quality_control/__init__.py`
- Updated all call sites: `pipeline/orchestrator.py`, `pipeline/pdf_processor.py`, `pipeline/validator.py`
- Updated all test files under `tests/`
- Updated steering docs and READMEs

## [2026-05] — Extract concrete QC defaults to `quality_control/defaults/`; self-sufficient `pdf_extractor.py` CLI

**QC model split:** Moved the three concrete default implementations out of `quality_control/models.py` into a new `quality_control/defaults/` subpackage. `models.py` now contains only ABCs and pure data containers.

- `quality_control/defaults/quality_report.py` — `QualityReport` (default always-pass rater)
- `quality_control/defaults/inter_rater_report.py` — `InterRaterReport` (pairwise pass/fail IAA)
- `quality_control/defaults/adjudication_decision.py` — `AdjudicationDecision` (majority-vote election)
- `quality_control/__init__.py` re-exports all three for backwards compatibility
- Updated `quality_control/rater.py`, `quality_control/local_metrics.py`, `quality_control/quality_control.py` to import from `defaults/`

**Shared extraction pipeline:** Extracted `_build_qc_context` logic from `pipeline/orchestrator.py` into `pdf_extractor/extraction_pipeline.py` (`build_qc_bundle`). Both the orchestrator and the standalone CLI now share the same scan-detection → backend-routing → QC pipeline code.

**Self-sufficient `pdf_extractor.py` CLI:** Rewrote `pdf_extractor/pdf_extractor.py` to run the full multi-backend extraction pipeline (GROBID + pdfplumber for native pages; PaddleOCR + PyMuPDF for scanned pages) and write `UnifiedRecord`-based JSON artifacts. No OpenAI API key required.

## [2026-05] — Rename `AlignmentMapEntry` → `AlignmentRecord`, `AlignmentMap` → `DocumentAlignment`

The old names implied a subclass relationship that didn't exist. Renamed to clarify
that `AlignmentRecord` is a single provenance record and `DocumentAlignment` is the
document-level container that holds lists of them.

- `quality_control/models.py`: class renames
- `quality_control/__init__.py`: updated imports and `__all__`
- `quality_control/reconciler.py`, `quality_control/quality_control.py`, `quality_control/concerns/text_fidelity.py`: updated all references
- All affected test files updated accordingly
- `.kiro/steering/product.md`: data model table updated

---

## [2026-05] — Migration Artifact Scrub (`bugfix/migration-artifact-scrub`)

Removed all artifacts from the old waterfall-cascade architecture. The
codebase now fully reflects the current scan-detector routing architecture
described in `product.md`.

**Dead code removed:**
- `_run_legacy_pipeline`, `_run_legacy_annotation_path`, `_derive_document_id`
  from `quality_control/quality_control.py`
- `PLACEHOLDER_NOTICE` constant and placeholder reconciliation path from
  `quality_control/reconciler.py`
- `load_config` backward-compat alias from `utils/config_utils.py`
- `ocr_text_quality_threshold` from `_LOCAL_DEFAULTS` and `load_local_config`
- Unused `extract_with_pymupdf` import and `font_metadata = []` assignment
  from `pdf_extractor/pdf_extractor.py`

**Renamed to extractor-agnostic names:**
- `iaa_calculator.investigate()` parameters: `grobid_observation` →
  `primary_observation`, `pymupdf_observation` → `secondary_observation`,
  `grobid_artifact` → `primary_artifact`, `pymupdf_artifact` →
  `secondary_artifact`; return keys updated to match
- `reconciler._build_provenance_dict` keys: `grobid_artifact_id` →
  `primary_artifact_id`, `pymupdf_artifact_id` → `secondary_artifact_id`,
  `grobid_observation` → `primary_observation`, `pymupdf_observation` →
  `secondary_observation`
- `rater.observe()` refactored from `observe(extractor_name, canonical_artifact,
  document_id, config)` to `observe(branch: Candidate, config: dict) ->
  QualityReport`
- `metrics_hierarchy` keys: `"tier1"` → `"local_metrics"`, `"tier2"` →
  `"exact_match"`, `"tier3"` → `"semantic_match"`
- `_build_tier1_report` → `_build_local_metrics_report`

**Content dict cleaned up (removed keys):** `observer_summary`,
`investigator_summary`, `geometry`, `adjudication_status`, `placeholder_notice`

**Test files renamed:** `test_text_extractor_tier1.py` →
`test_pdfplumber_backend.py`, `test_text_extractor_tier2.py` →
`test_pymupdf_backend.py`, `test_text_extractor_tier3.py` →
`test_paddleocr_backend.py`, `test_text_extractor_branch2.py` →
`test_pymupdf_schema.py`, `test_sentence_processor_task61.py` →
`test_sentence_processor.py`

**Stale files deleted:** All 6 files in `tests/steering/` (migration-era
steering drift tests); spec directories `bugfix/extraction-routing-alignment`,
`bugfix/test-suite-bugfix`, `bugfix/test-coverage`

**Docstrings cleaned:** Removed all `Design reference:`, `Requirements:`,
`Boundary:`, and `Task N.N` cross-references from production module docstrings
in `utils/text_processor.py`, `pdf_extractor/extraction/scan_detector.py`,
`quality_control/concerns/`, `quality_control/reconciler.py`,
`quality_control/quality_control.py`

**Documentation rewritten:** `pdf_extractor/extraction/README.md` replaced
entirely to describe current scan-detector routing architecture; root
`README.md` updated to remove cascade/tier/Tesseract references

---

## [2026-04] — Extraction Routing Alignment (`bugfix/extraction-routing-alignment`)

Fixed `pipeline/orchestrator._build_qc_context` which bypassed the per-page
routing architecture. It was calling GROBID and PyMuPDF directly without
running `scan_detector.classify_page()`, using PyMuPDF as the structural QC
branch (a role belonging to pdfplumber), and never invoking PaddleOCR for
scanned pages.

**What changed:**
- `_build_qc_context` now runs `scan_detector.classify_page()` on every page
  before invoking any extraction backend
- Native pages → `Candidate(extractor="grobid")` + `Candidate(extractor="pdfplumber")`
- Scanned pages with `ocr: true` → `Candidate(extractor="paddleocr")` +
  `Candidate(extractor="pymupdf")` (built-in OCR cross-validator)
- PyMuPDF font metadata stored outside `branches` as comparison signals
- `extract_pdf()` removed from `pdf_extractor/extraction/__init__.py` (dead
  legacy cascade function)
- `extract_with_pymupdf` re-exported from `pdf_extractor.extraction` so patch
  targets resolve correctly in tests

---

## [2026-04] — Test Suite Bug Fix (`bugfix/test-suite-bugfix`)

Fixed 4 collection errors and 51 test failures caused by stale import paths
and outdated test logic left over from the architecture migration.

**What changed:**
- Fixed stale imports in `test_logging_utils.py`, `test_parser_pipeline.py`,
  `test_quality_control_artifact_generator.py`; deleted `test_metrics_hierarchy.py`
  (tested a function that no longer exists)
- Rewrote `test_text_extractor_orchestrator.py` to test scan-detector routing
  instead of the old waterfall cascade
- Added `autouse` scispaCy/spaCy mock fixtures to all test files that
  construct `TextProcessor` directly or indirectly
- Fixed `pdf2image` mock setup in PaddleOCR coordinate tests
- Added missing `MockFaiss` class to `test_embedding_utils.py`
- Fixed `test_reconciler_call_is_strategy_driven_and_extractor_agnostic` to
  use a real `SemanticLayer` instead of a `MagicMock` for the reconciler
  return value
- Fixed `test_no_tesseract_references` grep exclusion to skip test files

---

## [2026-03] — Test Coverage for Pipeline Modules (`bugfix/test-coverage`)

Added seven new test files covering modules that previously had no dedicated
unit tests: `agents/openai/api_client.py`, `agents/openai/prompts.py`,
`pipeline/manifest.py`, `pipeline/extraction_map.py`,
`pipeline/extraction_report.py`, `pipeline/pdf_processor.py`,
`pipeline/orchestrator.py`.

**What was added:**
- Async retry logic tests for `_call_api_with_retries` (RateLimitError,
  APIStatusError, APIConnectionError, APITimeoutError, unexpected exceptions)
- `paper_cache_key` determinism and format PBT
- Prompt cache-stability tests: shared prefix invariant across warmup and
  extraction messages
- Manifest round-trip idempotency PBT
- ExtractionMap field grouping and domain-to-chunk assignment tests
- QC report flagged-row aggregation and CSV output tests
- PdfProcessor checkpointing, output persistence, and chunk orchestration tests
- Orchestrator PDF-level concurrency and GROBID failure-handling tests
- All tests mock external dependencies (OpenAI API, GROBID, file I/O) — no
  real credentials or services required

## [2026-05] — schema-validator-split: task 3.1 — `StructureSchemaValidator` (`schema-validator-split`)

Added `quality_control/structure_validator.py` as the sole reader of `configs/structure_schema.json`. Provides `StructureSchemaLoadError` and `StructureSchemaValidator` with five typed validation methods (`validate_candidate`, `validate_qc_bundle`, `validate_pdf_processor_output`, `validate_extraction_map`, `validate_chunk_output`). Each method resolves its target via `validator_targets` in the schema, builds a wrapper schema that carries the full `$defs` block for correct `$ref` resolution, and delegates to the generic `Validator`. Both names are re-exported from `quality_control/__init__.py`.

## [2026-05] — schema-validator-split: task 4.1 — migrate `validate_qc_context_input` to `quality_control/`

Created `quality_control/validate_context.py` as part of the `schema-validator-split` spec. The `validate_qc_context_input` function is now defined in `quality_control/` (migrated from `pipeline/validator.py`), respecting the dependency-direction rule that `quality_control` must not import from `pipeline`.

- Added `quality_control/validate_context.py` with `ValidationError` (defined locally, not imported from `pipeline`) and `validate_qc_context_input`.
- The function performs five pre-flight checks (isinstance QCBundle, unified not None, document_id non-empty str, content is dict, exact_text non-empty str) then delegates to `_structure_validator.validate_qc_bundle`.
- Module-level `_structure_validator = StructureSchemaValidator()` singleton instantiated at import time.

