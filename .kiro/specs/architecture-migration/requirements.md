# Requirements: architecture-migration

## Project Description

EviTrace developers are migrating the codebase from its current state to the architecture specified in §2.1–§2.7 of the design document. The migration spans three axes:

**Axis A — Data model.** `UnifiedRecord` currently has a flat `content: dict` with no typed semantic or structural layers. It must grow two typed layers (`SemanticLayer`, `StructuralLayer`) and a linking structure (`AlignmentMap`), plus first-class W3C JSON-LD annotation output.

**Axis B — Extraction routing.** The current four-tier waterfall cascade treats backends as competitors. It must be replaced by a per-page scan detector that routes each page to the correct complementary backend. Standalone Tesseract must be removed entirely.

**Axis C — QC pipeline.** The reconciler and adjudicator currently hardcode extractor names and use symmetric winner-takes-all logic. They must become fully concern-strategy-injectable and domain-agnostic. Sentence segmentation, word segmentation, and text normalization must be powered by independently configurable, pluggable backends.

Three hard constraints apply throughout:

1. All sentence segmentation goes through `TextProcessor.tokenize_sentences()` — no regex sentence splitters in production code paths.
2. All extractor asymmetry is encoded exclusively in injectable concern strategies — neither the reconciler nor the adjudicator may hardcode extractor names.
3. The core pipeline runner is domain-agnostic — any PDF-specific logic lives exclusively in `run_quality_control` or in closures it creates.

---

## Scope Boundaries

**Included in this migration:**
- Typed data layers (`SemanticLayer`, `StructuralLayer`, `AlignmentMap`) on `UnifiedRecord`
- Five-stage per-page scan detection with provenance
- Complementary backend routing (GROBID semantic, pdfplumber structural, PyMuPDF scan detection, PaddleOCR OCR)
- Removal of standalone Tesseract integration
- Pluggable `TextProcessor` and `SentenceSegment` hierarchy
- Concern-strategy-injectable reconciler and adjudicator
- W3C JSON-LD annotation output as a first-class pipeline artifact
- Configuration extensions with backward-compatible defaults

**Excluded from this migration:**
- Active adjudication fallback patching (recording divergence is in scope; modifying page content at runtime is not)
- GROBID invocation on scanned/OCR pages (architecture is wired but endpoint not called)
- NLP model downloads in CI; all model backends are mocked in tests
- Character-level bounding box extraction from pdfplumber
- LLM-based adjudication; all concern strategies are deterministic
- W3C JSON-LD round-trip validation against the official JSON-LD schema
- Additional text-processing backends beyond the specified built-in set

---

## Requirements

### 1. Typed Data Layers in UnifiedRecord

The `UnifiedRecord` model must expose typed semantic, structural, and alignment layers so that downstream consumers can inspect extraction provenance and confidence at the paragraph and sentence level.

#### 1.1 Semantic Layer

- The EviTrace pipeline shall produce a `SemanticLayer` containing sections, paragraphs, sentences, references, and document metadata for each document processed.
- When a document is classified as native (born-digital), the EviTrace pipeline shall populate `UnifiedRecord.semantic` with structured content derived from the semantic extraction branch.
- When a document page is classified as scanned, the EviTrace pipeline shall populate the semantic layer with OCR-derived text and shall flag each affected entry as `ocr_derived: true`.

#### 1.2 Structural Layer

- The EviTrace pipeline shall produce a `StructuralLayer` containing pages (with dimensions), text blocks (with bounding boxes), tables, and figures for each document processed.
- When a document is classified as native, the EviTrace pipeline shall populate `UnifiedRecord.structural` from the structural extraction branch.
- When a document page is classified as scanned, the EviTrace pipeline shall populate `UnifiedRecord.structural.blocks` with entries whose bounding boxes are expressed in PDF user-space points.

#### 1.3 Alignment Map

- The EviTrace pipeline shall produce an `AlignmentMap` that links semantic paragraphs to structural blocks, sentences to character ranges, and section headers to blocks.
- The EviTrace pipeline shall record for each alignment entry: agreement level (full, partial, divergent, or one_engine_only), normalized edit distance in the range [0.0, 1.0], confidence score in the range [0.0, 1.0], and the preferred reading selected by the concern strategy.
- The EviTrace pipeline shall not constrain the `source` identifier in alignment entries to any fixed set of extractor names.

#### 1.4 Backward Compatibility

- While a caller reads `UnifiedRecord.content`, the EviTrace pipeline shall continue to populate that field alongside the new typed layers.

---

### 2. Per-Page Scan Detection

The extraction pipeline must classify each page individually as native or scanned before routing it to the appropriate backend.

#### 2.1 Five-Stage Classification

- When processing a PDF, the EviTrace extraction pipeline shall classify each page as native or scanned using a five-stage detection sequence evaluated in order.
- When stage 1 fires (page text is empty after stripping whitespace), the EviTrace extraction pipeline shall classify the page as scanned immediately and shall not evaluate stages 2 through 5 for that page.
- The EviTrace extraction pipeline shall classify a page as native only when none of the five stages fire.

#### 2.2 Stage Triggers

- If a page produces empty text after stripping whitespace, the EviTrace extraction pipeline shall classify that page as scanned (stage 1).
- If a page's word count falls below the configured text-density threshold, the EviTrace extraction pipeline shall record stage 2 as triggered.
- If a page's alpha-character ratio (alpha characters divided by non-whitespace characters) falls below the configured alpha-ratio threshold, the EviTrace extraction pipeline shall record stage 3 as triggered.
- If a page has zero embedded fonts, the EviTrace extraction pipeline shall record stage 4 as triggered.
- If a page's image area exceeds the configured image-dominance threshold fraction of total page area, the EviTrace extraction pipeline shall record stage 5 as triggered.

#### 2.3 Classification Provenance

- The EviTrace extraction pipeline shall record, for each classified page, the ordered list of stage numbers that triggered and the numeric signal values computed for each triggered stage (word count, alpha ratio, font count, image coverage ratio).

---

### 3. Complementary Backend Routing

Extraction backends must operate in defined, non-competing roles. No two backends are alternatives for the same output slot.

#### 3.1 Native Page Handling

- While processing a native (born-digital) page, the EviTrace extraction pipeline shall obtain structural text blocks from the pdfplumber backend.
- While processing a native page, the EviTrace extraction pipeline shall obtain font metadata from the PyMuPDF backend.
- The EviTrace extraction pipeline shall not use pdfplumber and PyMuPDF as alternatives for the same output; each shall contribute its designated output independently.

#### 3.2 Scanned Page Handling

- While processing a confirmed scanned page, the EviTrace extraction pipeline shall obtain text blocks and bounding boxes from the PaddleOCR backend.
- When PaddleOCR returns pixel-space bounding boxes, the EviTrace extraction pipeline shall convert them to PDF user-space points using the rasterization DPI configured for that run.
- The EviTrace extraction pipeline shall record the rasterization DPI and OCR confidence score inside each block returned by PaddleOCR.

#### 3.3 Semantic and OCR Bypass Branches

- The EviTrace pipeline shall obtain semantic structure (sections, paragraphs, references, IMRaD labels) from the GROBID backend independently of per-page scan detection routing; GROBID is not part of the per-page routing cascade.
- When OCR mode is disabled in configuration, the EviTrace extraction pipeline shall skip scan detection entirely and obtain structural text from pdfplumber only.

---

### 4. Tesseract Removal

The standalone Tesseract integration must be absent from all production code paths.

- The EviTrace extraction pipeline shall not invoke `pytesseract` or any standalone Tesseract extractor in any production code path.
- When a scanned page is detected, the EviTrace extraction pipeline shall route it to PaddleOCR, not to Tesseract.
- When PyMuPDF's built-in OCR cross-validation is used, the EviTrace extraction pipeline shall invoke it through PyMuPDF's internal mechanism without requiring an external Tesseract installation.

---

### 5. Pluggable Text Processing

All text transformation tasks must be delegated to a configurable TextProcessor with independently swappable backends.

#### 5.1 Sentence Segmentation

- The EviTrace pipeline shall perform all sentence segmentation through the configured sentence-tokenizer backend.
- When no sentence-tokenizer backend is specified in configuration, the EviTrace pipeline shall use scispaCy as the default backend.
- When the configured sentence-tokenizer backend package is not installed, the EviTrace pipeline shall raise an error that includes the exact install command needed to resolve the missing dependency.
- When an unrecognized sentence-tokenizer backend value is specified in configuration, the EviTrace pipeline shall raise an error at startup that lists the valid built-in backend options.
- The EviTrace pipeline shall load the sentence-tokenizer model at most once per pipeline run, not once per document or per page.

#### 5.2 Word Segmentation and Normalization

- The EviTrace pipeline shall normalize text before word segmentation using the configured normalizer backend.
- The EviTrace pipeline shall produce identical output when normalization is applied to already-normalized text (the normalize operation is idempotent).
- Where the normalizer is configured to use NFKC mode, the EviTrace pipeline shall apply Unicode NFKC normalization including ligature expansion (e.g., ﬁ → fi, ﬂ → fl).

#### 5.3 Text Comparison

- The EviTrace pipeline shall compute text similarity as a normalized value in [0.0, 1.0] where 1.0 means the inputs are identical after normalization.
- The EviTrace pipeline shall apply normalization to both inputs before computing similarity.

#### 5.4 OCR Text Cleaning

- The EviTrace pipeline shall remove Unicode replacement characters (U+FFFD) and C0 control characters from OCR-extracted text before further processing.

#### 5.5 Custom Backend Support

- Where a fully qualified Python class path is supplied in configuration for the text processor, the EviTrace pipeline shall load and use that class without requiring changes to pipeline code.
- Where a fully qualified class path is supplied for the sentence-tokenizer backend, the EviTrace pipeline shall accept it as a valid backend value.

---

### 6. Domain-Agnostic Core Pipeline

The core pipeline runner must remain free of PDF-specific and extractor-specific logic so that it can be reused across non-PDF domains.

#### 6.1 Pipeline Runner Isolation

- The EviTrace core pipeline runner shall accept only domain-independent inputs: injectable callable functions and a list of branch payloads.
- The EviTrace core pipeline runner shall not reference any PDF-specific library, extractor name, or document-structure concept in its function body.
- When non-PDF branch payloads are passed to the core pipeline runner, the EviTrace core pipeline runner shall process them without raising an error.

#### 6.2 QC Closure Encapsulation

- The EviTrace QC pipeline shall encapsulate all PDF-specific logic inside closures created within the `run_quality_control` function; no PDF-specific symbols may appear in the core pipeline runner's body.
- The EviTrace QC pipeline shall create exactly one TextProcessor instance per pipeline run and share that instance across all stage closures that require it.

---

### 7. Concern-Strategy-Injectable QC

The reconciler and adjudicator must be fully agnostic about extractor identity, delegating all asymmetric decisions to injectable strategy objects.

#### 7.1 Reconciler Agnosticism

- The EviTrace reconciler shall route inputs to the appropriate concern strategy (text fidelity, section verification, or table/figure merge) without hardcoding any extractor name in its control flow or output-shaping logic.
- The EviTrace reconciler shall accept injectable strategy objects for each concern type and shall use those objects exclusively to determine preferred readings, agreement levels, and confidence scores.
- When custom strategy objects are injected at the call site, the EviTrace reconciler shall use them in place of the defaults without requiring code changes to the reconciler.
- The EviTrace reconciler shall return a `UnifiedRecord` with populated semantic, structural, and alignment layers.

#### 7.2 Adjudicator Agnosticism

- The EviTrace adjudicator shall delegate all decisions about preferred sources to injected concern strategies and shall not assign a preferred source directly.
- When a strategy returns a preferred source identifier, the EviTrace adjudicator shall record it exactly as provided by the strategy, without modification.
- When custom strategy objects are injected, the EviTrace adjudicator shall use them without requiring code changes to the adjudicator.

#### 7.3 Concern Strategy Behavior

- When two text artifacts are compared by the text-fidelity concern strategy, the EviTrace pipeline shall produce different preferred readings depending on argument order (the strategy is asymmetric by design).
- When either the primary or reference artifact is absent, the table/figure merge concern strategy shall raise an error that identifies which side is missing.
- When two text artifacts are identical, the text-fidelity concern strategy shall report full agreement with edit distance 0.0 and confidence 1.0.

---

### 8. W3C Annotation Output

The pipeline must produce W3C JSON-LD annotation output as a first-class artifact for both born-digital and scanned pages.

#### 8.1 Annotation Data Model Projection

- The EviTrace pipeline shall produce annotation records from the typed data layers without reading raw extractor output during the projection step.
- When a sentence alignment entry is born-digital (not OCR-derived), the EviTrace pipeline shall produce an annotation record with a TextPositionSelector carrying character start and end positions.
- When a sentence alignment entry is OCR-derived, the EviTrace pipeline shall produce an annotation record with a FragmentSelector carrying the page number and bounding box in media-fragment format.
- The EviTrace pipeline shall populate a TextQuoteSelector (exact, prefix, suffix) for every annotation record regardless of page type.
- A single document may produce annotation records from both born-digital and scanned pages in the same projection output.

#### 8.2 JSON-LD Serialization

- When serializing annotation records, the EviTrace pipeline shall produce JSON-LD documents with `@context`, `id`, `type`, `body`, and `target` fields conforming to the W3C Web Annotation structure.
- The EviTrace pipeline shall assign each annotation record a unique URN-format identifier.
- When serializing an empty list of annotation records, the EviTrace pipeline shall return an empty list without raising an error.
- The EviTrace pipeline shall produce all W3C JSON-LD serialization through the designated artifact generator; no other module shall produce W3C JSON-LD dicts.

---

### 9. Configuration

All new pipeline behaviors must be configurable and must have defaults that preserve backward compatibility with existing config files.

- The EviTrace pipeline shall accept a `text_processor` configuration block specifying the class path, sentence-tokenizer backend, word-tokenizer backend, and normalizer backend.
- The EviTrace pipeline shall accept scan-detection threshold configuration (text-density threshold, alpha-ratio threshold, image-dominance threshold) with documented defaults.
- The EviTrace pipeline shall accept text-fidelity edit-distance threshold, section-verification font-size tolerance, and OCR rasterization DPI configuration with documented defaults.
- While a configuration key introduced by this migration is absent from the config file, the EviTrace pipeline shall behave as if the documented default value for that key were present, without raising an error.

---

### 10. Test Coverage

All behaviors introduced by this migration must be verifiable without external NLP model downloads.

- The EviTrace test suite shall be executable without downloading any NLP model (scispaCy, stanza, or equivalent) by using mocked backends.
- The EviTrace test suite shall verify that each of the five scan-detection stages can be triggered independently using a mocked PDF page.
- The EviTrace test suite shall verify at runtime that the core pipeline runner's source code contains no import or reference to any PDF-specific library or extractor name.
- The EviTrace test suite shall verify at runtime that the reconciler's source code contains no hardcoded extractor name in control flow or output-shaping context.
- The EviTrace test suite shall verify at runtime that the adjudicator's source code contains no hardcoded assignment of a preferred source to a specific extractor name.
