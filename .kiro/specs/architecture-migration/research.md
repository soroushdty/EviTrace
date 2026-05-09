# Gap Analysis: architecture-migration

**Date:** 2026-05-08  
**Requirements:** `.kiro/specs/architecture-migration/requirements.md`  
**Spec:** `.kiro/specs/refactor.md`

---

## Analysis Summary

- **Scope**: XL — 9 new files, 8 files modified, 1 deleted, 10 new test files, updates to 6 existing test files. Changes span every layer of the pipeline.
- **Highest-risk area**: Concern-strategy refactor of `reconciler.py` and `adjudicator.py` — both currently hardcode extractor names and have existing tests that will need updating; design must not break the `QCContext`/`metrics_hierarchy` contract.
- **New infrastructure**: `utils/text_processor.py` (TextProcessor + SentenceSegment hierarchy), `pdf_extractor/extraction/scan_detector.py`, `quality_control/concerns/` package, `pdf_extractor/annotation/` package — all net-new with no existing code to migrate from.
- **Low-risk areas**: `quality_control/models.py` (purely additive), `utils/config_utils.py` (additive defaults only), `config/config.yaml` (additive YAML keys), `quality_control/__init__.py` (export list only).
- **Recommended approach**: Option C (Hybrid) — create the new foundational modules first (Tier 0), then modify dependent files in tier order per the spec's prerequisite ordering. The spec's 7-tier ordering is the correct implementation sequence.

---

## 1. Current State Investigation

### 1.1 Codebase Asset Map

| File | Current State | Migration Class |
|---|---|---|
| `quality_control/models.py` | `UnifiedRecord(document_id, content)` only; no typed layers | Extend (additive) |
| `quality_control/reconciler.py` | Hardcoded `grobid_artifact`/`pymupdf_artifact` params; winner-takes-all `_reconcile_blocks` | Modify (sig change + concern routing) |
| `quality_control/adjudicator.py` | Hardcoded `"pymupdf"`/`"grobid"` in `_evaluate_extractor_quality`; symmetric comparison | Modify (remove symmetric logic + strategy delegation) |
| `quality_control/quality_control.py` | `_split_sentences` regex at line 138–144; called at lines 220, 237, ~450, ~487 | Modify (remove + inject TextProcessor) |
| `quality_control/__init__.py` | Exports `run_pipeline`, `run_quality_control`, model classes, `LocalQCReport` | Extend (add 4 new dataclasses to `__all__`) |
| `pdf_extractor/extraction/__init__.py` | Four-tier waterfall; imports `extract_with_tesseract`; `_compute_quality_score` alpha heuristic | Modify (replace waterfall with scan-detector routing) |
| `pdf_extractor/extraction/Tesseract.py` | Wraps `pytesseract`; `extract_with_tesseract(pdf_path) → list[BlockDict]` | **Delete** |
| `pdf_extractor/extraction/PaddleOCR.py` | `extract_with_paddleocr(pdf_path) → list[BlockDict]`; one block/page; `block_bbox=None` | Modify (add DPI param; pixel→PDF coord mapping) |
| `pdf_extractor/extraction/PyMuPDF.py` | Already extracts font metadata per-span; `extract_with_pymupdf` traverses full doc | Modify (add `get_page_font_metadata(page)` for single-page use) |
| `pdf_extractor/processing/sentence_processor.py` | `_RE_SENTENCE_SPLIT` regex; `process_sentences(blocks, len_filter) → list[dict]`; one caller at `pdf_extractor.py:83` | Modify (add `text_processor` param; replace regex) |
| `utils/config_utils.py` | `_QC_DEFAULTS` has 40-key dict; `_deep_merge` is non-mutating; `load_qc_config` applies defaults | Extend (add `text_processor` and scan_detection/ocr/text_fidelity/section_verification keys) |
| `config/config.yaml` | Has all current QC keys; OCR, grobid, concurrency sections | Extend (add `text_processor` and new `quality_control` sub-keys) |
| `utils/text_processor.py` | **Does not exist** | **Create** |
| `pdf_extractor/extraction/scan_detector.py` | **Does not exist** | **Create** |
| `quality_control/concerns/` (package) | **Does not exist** | **Create** (4 files) |
| `pdf_extractor/annotation/` (package) | **Does not exist** | **Create** (2 files + `__init__.py`) |

### 1.2 Preserved Architecture Patterns

The following patterns are active across the codebase and must be respected in the migration:

1. **Abstract base classes** — `QualityMetrics`, `InterRaterMetrics`, `AdjudicationRules` in `models.py` are subclassed by callers; the migration adds concern strategies that follow the same pattern.
2. **Config-driven runtime** — All thresholds and backend choices come from the `config` dict passed through `run_quality_control`; new keys must follow the same `load_qc_config` / `_QC_DEFAULTS` path.
3. **`_deep_merge` for config** — Non-mutating recursive merge; new defaults must be added to `_QC_DEFAULTS`, not hardcoded at call sites.
4. **`schemas.make_block()`** — All extraction backends use this factory; `PaddleOCR.py` migration must continue using it (adding `rasterization_dpi` and `ocr_confidence` as fields or in block metadata).
5. **Stage closures in `run_quality_control`** — PDF-specific logic is already encapsulated in closures; this pattern must be extended, not abandoned.
6. **`QCContext.metrics_hierarchy`** — The `tier1/tier2/tier3` dict structure is a non-goal for change; the migration must not alter it.
7. **Hypothesis property tests** — Existing test files use `@given` + `@settings(max_examples=100)`; new tests should follow the same pattern.
8. **`_make_*()` builders in tests** — Each test file defines local artifact builders; new test files should follow this convention.

### 1.3 Integration Surfaces

| Surface | Consumer | Notes |
|---|---|---|
| `UnifiedRecord` | `run_quality_control`, downstream artifact consumers | Must stay backward compatible; `content` field retained |
| `BlockDict` schema | All extraction backends, `process_sentences`, `build_full_text` | `schemas.make_block()` is the only valid constructor |
| `QCContext` | All QC pipeline stages | `metrics_hierarchy` shape frozen per non-goals |
| `reconcile()` signature | `_pdf_reconciler_fn` closure in `quality_control.py` | Closure is the only call site; signature can change |
| `adjudicate()` signature | `_pdf_adjudicator_fn` closure in `quality_control.py` | Same — single call site |
| `process_sentences()` signature | `pdf_extractor/pdf_extractor.py:83` | One call site; must be updated together |
| `extract_pdf()` signature | `pdf_extractor/pdf_extractor.py:72` | Must remain backward compatible or caller updated together |

---

## 2. Requirements Feasibility Analysis

### 2.1 Requirement-to-Asset Map

| Requirement | Assets Needed | Gap Status |
|---|---|---|
| Req 1.1–1.3: Typed data layers | `SemanticLayer`, `StructuralLayer`, `AlignmentMap`, `AlignmentMapEntry` in `models.py` | **Missing** — add 4 dataclasses |
| Req 1.4: Backward compat `content` | `UnifiedRecord.content` field | **Present** — no change needed |
| Req 2.1–2.3: Five-stage scan detection | `scan_detector.py` + `PageScanClassification` dataclass | **Missing** — new module |
| Req 3.1: Native page routing (pdfplumber + PyMuPDF) | `extract_pdf` orchestration logic | **Missing** — replace waterfall |
| Req 3.2: Scanned page routing (PaddleOCR + coord mapping) | `PaddleOCR.py` DPI param + coord transform | **Partial** — PaddleOCR exists but lacks DPI and coord mapping |
| Req 3.3: GROBID semantic branch | Already runs separately in `run_quality_control` | **Present** — no change needed |
| Req 4: Tesseract removal | Delete `Tesseract.py`; remove import; update tests | **Present** (to remove) |
| Req 5.1: Pluggable sentence segmentation | `utils/text_processor.py` + `SentenceSegment` hierarchy (5 built-ins) | **Missing** — new module |
| Req 5.2–5.4: Word seg, normalization, OCR cleaning | Same `TextProcessor` class | **Missing** |
| Req 5.5: Custom class path support | `importlib`-based loader in `run_quality_control` | **Missing** |
| Req 6.1–6.2: Domain-agnostic `run_pipeline` | `run_pipeline` body in `quality_control.py` | **Partial** — currently mixed with PDF logic in closures (closures are correct; body itself may already be clean, needs verification) |
| Req 7.1: Reconciler agnosticism | Replace `grobid_artifact`/`pymupdf_artifact` params; concern routing | **Missing** — hardcoded today |
| Req 7.2: Adjudicator agnosticism | Remove `_compute_text_quality_score`, `_evaluate_extractor_quality`; add `_adjudicate_concern` | **Missing** |
| Req 7.3: Concern strategy behavior | `TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern` | **Missing** — new package |
| Req 8.1: Annotation data model | `pdf_extractor/annotation/w3c_annotation.py` | **Missing** — new file |
| Req 8.2: JSON-LD serialization | `pdf_extractor/annotation/artifact_generator.py` | **Missing** — new file |
| Req 9: Config extensions | `_QC_DEFAULTS` + `config.yaml` additions | **Partial** — structure exists, new keys absent |
| Req 10: Test coverage (no NLP downloads) | 10 new test files + 6 updated test files | **Partially present** — existing test infrastructure is compatible |

### 2.2 Constraints from Existing Architecture

1. **`schemas.BlockDict` is a TypedDict** — `PaddleOCR.py` uses `make_block()` which only accepts the current four fields (`text`, `page_index`, `block_bbox`, `spans`). Adding `rasterization_dpi` and `ocr_confidence` requires either (a) extending `BlockDict` and `make_block()`, or (b) storing these in a separate dict/attribute per block. This is a **Research Needed** item for design.

2. **`_split_sentences` has 4 call sites** — The subagent found calls at lines 220, 237, and approximately 450 and 487. The spec only documents lines 220 and 237. The design must audit all four call sites.

3. **`reconciler.py` returns a dict**, not a `UnifiedRecord` — The current `reconcile()` returns a dict; the spec requires it to return `UnifiedRecord`. The caller (`_pdf_reconciler_fn`) assigns this to `QCContext.unified`. This is compatible but the type change must be propagated.

4. **`adjudicator.adjudicate()` calls `reconciler.reconcile()` directly** — The adjudicator currently orchestrates both adjudication and reconciliation. The spec redesigns them as independent steps. The call chain must be restructured without breaking the `QCContext` update pattern.

5. **No existing `pdf_extractor/annotation/` package** — `__init__.py` must be created alongside `w3c_annotation.py` and `artifact_generator.py`.

6. **`test_text_extractor_tier2.py` imports Tesseract** — This test file will fail after deletion. It must be updated as part of the Tier 0 deletion work.

### 2.3 Research Needed (Defer to Design)

| # | Item | Why Deferred |
|---|---|---|
| R1 | How to store `rasterization_dpi` and `ocr_confidence` per PaddleOCR block given `BlockDict` TypedDict structure | Requires schema extension decision |
| R2 | Whether `adjudicator.adjudicate()` should still call `reconciler.reconcile()` or whether the two must be fully decoupled and called separately from the closure | Affects closure structure in `quality_control.py` |
| R3 | Whether `process_sentences` output dict needs a `source_extractor` field for the W3C annotation chain | Determines whether `sentence_to_char_range` in `AlignmentMap` can be populated from sentence records |
| R4 | Exact field names on `SemanticLayer.sections` list dicts — the spec says "IMRaD label, heading text, depth, paragraph refs" but the GROBID extraction artifact structure determines the actual keys | Implementation depends on GROBID artifact shape |
| R5 | How `_pdf_reconciler_fn` identifies which branch is GROBID vs pdfplumber (currently explicit extraction by name) while keeping the reconciler itself name-agnostic | Core of Invariant 2 implementation |

---

## 3. Implementation Approach Options

### Option A: In-Place Refactor (Extend Existing)

Modify existing files directly, adding concern-strategy logic inside current module bodies.

- **Reconciler**: Add injectable params to existing `reconcile()` signature; add concern routing inline.
- **Adjudicator**: Add `_adjudicate_concern()` helper; keep existing structure for backward compat.
- **Quality control**: Add `text_processor` instantiation at top of `run_quality_control`; replace `_split_sentences` call sites.
- **TextProcessor**: Add to `utils/text_processor.py` as standalone.
- **Concerns**: New package `quality_control/concerns/`.
- **Annotation**: New package `pdf_extractor/annotation/`.

**Trade-offs:**
- ✅ Minimal structural change; closures stay in the same files
- ✅ Leverages existing config and context passing patterns
- ❌ `reconciler.py` and `adjudicator.py` grow larger; harder to test in isolation
- ❌ Risk of partial migration (old and new logic coexisting in same functions)

### Option B: New Modules for QC Stages

Create new versions of reconciler and adjudicator as separate modules, keeping the old ones for backward compat.

- `quality_control/reconciler_v2.py` and `quality_control/adjudicator_v2.py`
- Swap out in `_pdf_reconciler_fn` / `_pdf_adjudicator_fn`

**Trade-offs:**
- ✅ Zero risk to existing pipeline during development
- ✅ Old and new can coexist and be tested independently
- ❌ Two versions of reconciler/adjudicator create navigation confusion
- ❌ Spec does not call for this; creates dead code immediately after migration

### Option C: Hybrid — Tier-Ordered Migration (Recommended)

Follow the spec's 7-tier prerequisite ordering exactly. Create new foundational modules first, then modify existing files in dependency order. Existing files are modified (not duplicated); old logic is removed at the same step new logic is introduced.

- **Tier 0 (parallel)**: Create `utils/text_processor.py`; delete `Tesseract.py` + remove import + update tier2 test
- **Tier 1–2 (parallel within tier)**: `scan_detector.py`; update `sentence_processor.py`; remove `_split_sentences`; extend `models.py`
- **Tier 3**: Create `quality_control/concerns/` package (3 strategy files + `__init__.py`)
- **Tier 4**: Modify `reconciler.py` and `adjudicator.py`
- **Tier 5**: Wire `quality_control.py` and `extraction/__init__.py`
- **Tier 6**: Update `PaddleOCR.py` and `PyMuPDF.py`; create `annotation/` package
- **Tier 7 (parallel to all)**: Config extension; `__init__.py` exports

**Trade-offs:**
- ✅ Each tier is independently testable before proceeding
- ✅ No dead code; old logic is cleanly replaced at each step
- ✅ Matches the spec's own ordering — lowest risk of missed dependencies
- ✅ Parallelizable within tiers
- ❌ Requires upfront discipline to not skip tiers; cascade failures if tier ordering violated
- ❌ More complex planning (7 distinct phases vs. a single pass)

---

## 4. Implementation Complexity & Risk

| Area | Effort | Risk | Justification |
|---|---|---|---|
| `utils/text_processor.py` (TextProcessor + SentenceSegment) | M | Low | Well-specified interface; 5 backends follow identical lazy-load pattern; no integration with existing code at creation time |
| `pdf_extractor/extraction/scan_detector.py` | S | Low | Pure function; 5 stages clearly specified; mocked fitz.Page in tests |
| `quality_control/models.py` extensions | S | Low | Purely additive dataclasses; existing `UnifiedRecord` retains `content` |
| `quality_control/concerns/` package | S | Low | 3 strategy classes with small, well-specified interfaces; no external dependencies |
| `pdf_extractor/annotation/` package | M | Low | `w3c_annotation.py` is a pure data projection; `artifact_generator.py` is pure serialization; both are net-new with no legacy entanglement |
| `quality_control/reconciler.py` refactor | M | **Medium** | Signature change + concern routing is well-specified; risk is in correctly identifying which branch is primary vs. secondary (Research Item R5) without leaking extractor names into the reconciler |
| `quality_control/adjudicator.py` refactor | M | **Medium** | Removing symmetric alpha-ratio logic and replacing with strategy delegation; existing tests must be updated; call chain restructuring (Research Item R2) |
| `quality_control/quality_control.py` wiring | M | **Medium** | 4 `_split_sentences` call sites; TextProcessor instantiation via `importlib`; wiring concern strategies into `_pdf_reconciler_fn` while keeping `run_pipeline` body clean (Invariant 3) |
| `pdf_extractor/extraction/__init__.py` | M | **Medium** | Replacing waterfall with scan-detector routing changes observable extraction behavior; PaddleOCR coord mapping is a new code path with no existing tests |
| `pdf_extractor/extraction/PaddleOCR.py` | S | Low | DPI param + coordinate transform formula is fully specified; one call site |
| `pdf_extractor/extraction/PyMuPDF.py` | S | Low | Add `get_page_font_metadata(page)` for single-page use; existing doc-level logic untouched |
| `pdf_extractor/processing/sentence_processor.py` | S | Low | One call site (`pdf_extractor.py:83`) to update; `text_processor` becomes third param |
| Config extensions | S | Low | Additive only; `_deep_merge` handles missing keys gracefully |
| Test suite (10 new + 6 updated files) | L | Low | Infrastructure patterns are established (Hypothesis, `_make_*` builders, `unittest.mock`); no NLP downloads required |
| **Overall** | **XL** | **Medium** | Broad multi-file change; well-specified; medium risk concentrated in 3 files |

---

## 5. Recommendations for Design Phase

### Preferred Approach

**Option C (Hybrid, tier-ordered)**. The spec's 7-tier prerequisite ordering is the correct implementation sequence and should be adopted as the task decomposition structure in `tasks.md`.

### Key Decisions for Design to Resolve

1. **BlockDict schema extension (R1)** — Decide whether `rasterization_dpi` and `ocr_confidence` are added as new fields to `BlockDict` TypedDict (extending `schemas.py` and `make_block()`), or stored in a separate per-block metadata dict. The simpler option is to add optional fields to `BlockDict`; extending `make_block()` would break existing callers that use positional args.

2. **Adjudicator–Reconciler call chain (R2)** — Decide whether `adjudicator.adjudicate()` continues to call `reconciler.reconcile()` as its final step (current design), or whether the two are fully decoupled and each closure in `quality_control.py` calls them independently. Decoupling is cleaner for the agnosticism invariant.

3. **Branch role identification in `_pdf_reconciler_fn` (R5)** — This is the most nuanced design question. The reconciler must not know which extractor is GROBID vs. pdfplumber, but the closure that calls it knows. The design must specify exactly how the closure identifies the branches generically (e.g., by extractor name in `BranchOutput.extractor`) and passes them as `primary_artifact` / `secondary_artifact` without leaking that knowledge into the reconciler.

4. **`_split_sentences` call sites at lines ~450 and ~487** — The spec documents only lines 220 and 237. The additional call sites (in Tier 2/3 metric paths) must be audited and the `text_processor` dependency propagated through them as well.

5. **`AlignmentMap` population from existing sentence records (R3)** — `sentence_to_char_range` requires character offsets per sentence. The current `process_sentences` output does not include character offsets. The design must specify how these are computed (e.g., from `full_pdf_text` + sentence text lookup, or from span-level bboxes in PyMuPDF blocks).

---

## Design Synthesis Outcomes

### Generalizations

- The concern-strategy pattern (`TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern`) is a generalization of the reconciliation problem: each concern type has the same interface pattern (`reconcile`/`merge` + `adjudicate`). New concern types can be added without changing the reconciler or adjudicator.
- `SentenceSegment` extending `TextProcessor` is a generalization: any sentence backend can serve as a drop-in `TextProcessor`, keeping the configuration interface uniform regardless of whether a single-purpose segmenter or a full processor is injected.

### Build vs. Adopt Decisions

| Component | Decision | Rationale |
|---|---|---|
| `TextProcessor` | Build | No library provides the exact interface (normalize + tokenize_sentences + tokenize_words + compare + clean_ocr + extract_keywords) as a single configurable class |
| Sentence backends | Adopt | scispaCy, NLTK Punkt, stanza, wtpsplit, spaCy Sentencizer are all existing libraries; the design wraps them behind a stable interface |
| Text similarity | Adopt `difflib.SequenceMatcher` | stdlib; no new dependency; sufficient for normalized Levenshtein in [0.0, 1.0] |
| W3C annotation format | Adopt W3C Web Annotation standard | Specified by requirements; well-documented standard |
| JSON-LD serialization | Build thin layer | `artifact_generator` is a thin dict-construction layer over `AnnotationRecord`; no JSON-LD library needed |
| Coordinate transform | Build | Simple arithmetic formula (72.0/dpi scaling); no library needed |

### Simplifications Applied

- `adjudicator.py` no longer calls `reconciler.py`; the existing `run_pipeline()` call chain already calls them independently. Removing line 218 is the only change needed — no QCContext restructuring required.
- `PaddleOCRBlockDict` uses TypedDict inheritance with `total=False` rather than a parallel block schema — simpler than creating a wholly new block type, and `validate_blocks()` requires no changes.
- The `sentence_to_char_range` computation uses stdlib `str.find()` on the existing `full_pdf_text` string — no new text indexing infrastructure needed.

### Research Items Resolved

| Item | Resolution |
|---|---|
| R1: BlockDict extension | `PaddleOCRBlockDict(BlockDict, total=False)` + `make_ocr_block()` factory. All 6 `make_block()` callers use named args; no breakage. |
| R2: Adjudicator-reconciler decoupling | Remove `reconciler.reconcile()` call at adjudicator line 218. `adjudicator.adjudicate()` returns decisions dict only. QCContext unchanged. |
| R3: sentence_to_char_range | Use `full_pdf_text.find(sentence_text)` after normalization. `build_full_text()` already provides the haystack. Missing sentences recorded as `"one_engine_only"` in `reconciliation_flags`. |
| R4: GROBID artifact field names | GROBID returns `tuple[str, list[BlockDict]]` — flat blocks only. `SemanticLayer` hierarchy (sections, paragraphs) is reconstructed in `_pdf_reconciler_fn` by text-pattern classification (headings vs. body blocks). |
| R5: Branch role identification | `_pdf_reconciler_fn` closure identifies branches by `branch.extractor == "grobid"` and `branch.extractor in ("pymupdf", "pdfplumber")`. The reconciler receives only `primary_artifact`/`secondary_artifact` — no extractor names. |

### Research Items to Carry Forward

- R1: `BlockDict` extension strategy for PaddleOCR metadata fields
- R2: Adjudicator–reconciler decoupling decision
- R3: `sentence_to_char_range` computation strategy from existing sentence records
- R4: GROBID artifact field names for `SemanticLayer` population
- R5: Branch role identification pattern in `_pdf_reconciler_fn`
