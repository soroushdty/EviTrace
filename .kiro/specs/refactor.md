# Implementation Specification
## EviTrace — Architecture Migration to §2.1–§2.7 Target Design

**Status:** Approved for implementation  
**Delivery constraint:** `run_pipeline` must remain domain-agnostic throughout.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Current Problems](#2-current-problems)
3. [Target Architecture Summary](#3-target-architecture-summary)
4. [Files to Delete](#4-files-to-delete)
5. [Files to Create](#5-files-to-create)
6. [Files to Modify](#6-files-to-modify)
7. [Config Additions](#7-config-additions)
8. [Implementation Requirements](#8-implementation-requirements)
9. [Prerequisite Ordering](#9-prerequisite-ordering)
10. [Testing Plan](#10-testing-plan)
11. [Acceptance Criteria](#11-acceptance-criteria)
12. [Non-Goals](#12-non-goals)

---

## 1. Overview

This spec describes the migration of the EviTrace codebase from its current
state to the architecture specified in §2.1–§2.7 of the design document.

The migration has three axes:

**Axis A — Data model.** `UnifiedRecord` grows two typed layers
(`SemanticLayer`, `StructuralLayer`) and a linking structure (`AlignmentMap`),
replacing the current flat `content: dict`. W3C JSON-LD annotation output is
added as a first-class projection from this model, with serialization to JSON
delegated entirely to `artifact_generator.py`.

**Axis B — Extraction routing.** The four-tier waterfall cascade in
`extraction/__init__.py` is replaced by a per-page scan detector that routes
each page to the correct backend. Backends become complementary, not
competitors. Standalone Tesseract is removed entirely.

**Axis C — QC pipeline.** The reconciler and adjudicator are fully
concern-strategy-injectable: all extraction asymmetry, extractor identity, and
resolution logic lives in the injectable strategies, making both modules
domain-agnostic at the call-site level. Sentence segmentation, word
segmentation, and text normalization throughout the pipeline are each powered by
independently configurable, pluggable backends via a `SentenceSegment` class
hierarchy and `TextProcessor` composition.

Three invariants are enforced as hard constraints on the implementation:

- **Invariant 1:** All sentence segmentation goes through
  `TextProcessor.tokenize_sentences()`, which delegates to a configured
  `SentenceSegment` backend. No regex sentence splitter may appear in any
  production code path.
- **Invariant 2:** All extractor asymmetry (role assignments, preferred-reading
  logic, ground-truth designation) is encoded exclusively in injectable concern
  strategies. Neither `reconciler.py` nor `adjudicator.py` may hardcode extractor
  names or treat any two extractors as having fixed relative authority.
- **Invariant 3:** `run_pipeline` is domain-agnostic. Any PDF-specific logic
  lives exclusively in `run_quality_control` or in closures it creates.

---

## 2. Current Problems

### 2.1 Flat UnifiedRecord

`quality_control/models.py:UnifiedRecord` has two fields: `document_id: str`
and `content: dict`. There is no typed semantic layer, no typed structural
layer, and no alignment structure linking the two. Downstream consumers cannot
inspect extraction provenance or confidence at the paragraph or sentence level.

### 2.2 Standalone Tesseract (Invariant #10)

`pdf_extractor/extraction/Tesseract.py` wraps `pytesseract` directly. This
violates §2.7 ("The standalone Tesseract integration currently present in the
repository must be removed entirely"). PaddleOCR is the designated scanned-page
backend; PyMuPDF's `page.get_textpage_ocr()` call is the cross-validation path.

### 2.3 Symmetric Waterfall Cascade (§2.2)

`extraction/__init__.py` runs PyMuPDF → pdfplumber → Tesseract → PaddleOCR and
selects the first tier that meets `ocr_text_quality_threshold`. This treats all
four backends as competitors for the same output slot. Per §2.2, each backend
has a specific role: GROBID produces semantic structure, PyMuPDF detects scan
state and provides font metadata, pdfplumber provides structural ground-truth
text, PaddleOCR handles confirmed scanned pages. These roles are not
interchangeable.

### 2.4 Regex Sentence Splitters

Two regex-based splitters are used in production paths:

- `quality_control/quality_control.py:_split_sentences` (lines 137–143) — used
  in `_build_tier1_report` (line 219) and `_build_placeholder_sentence_store`
  (lines 236–245).
- `pdf_extractor/processing/sentence_processor.py:_RE_SENTENCE_SPLIT` (line 167)
  — used in `process_sentences` to split blocks into sentence candidates.

Neither is powered by a configurable NLP-backed segmenter. §2.6 requires all
sentence tokenisation to go through `TextProcessor.tokenize_sentences()`.

### 2.5 Symmetric Adjudicator

`adjudicator.py:_make_adjudication_decisions` computes an alpha-ratio quality
score for GROBID and pdfplumber separately and elects the winner. This is a
symmetric comparison. Per §2.2 and §2.5.3, GROBID and pdfplumber are not
symmetric: GROBID is the semantic authority, pdfplumber is the structural
ground-truth. However, this asymmetry must be expressed through injected
strategies, not hardcoded in the adjudicator module itself, so that the
adjudicator remains reusable across domains.

### 2.6 No Concern-Type Routing in Reconciler

`reconciler.py:_reconcile_blocks` uses a single primary/fallback strategy:
whichever extractor scores higher takes all blocks. §2.5.3 specifies three
concern types with distinct resolution strategies. None of these are
implemented. Additionally, the reconciler currently encodes extractor-specific
assumptions in its call sites, preventing reuse outside the PDF domain.

### 2.7 Single-Pass Scan Detection

`extraction/__init__.py:_compute_quality_score` computes an alpha-ratio and
uses it as a document-wide quality signal. §2.7 specifies five-stage per-page
scan detection, with each stage recording provenance. The current code produces
no per-page classification and no triggered-stage provenance.

### 2.8 No W3C Annotation Output

`reconciler.py` mentions W3C JSON-LD as a downstream export goal in its module
docstring but produces nothing. §2.4 requires W3C JSON-LD to be a first-class
output for both born-digital and scanned pages. The JSON generation itself must
be delegated to `artifact_generator.py`; `w3c_annotation.py` produces only an
in-memory data model.

---

## 3. Target Architecture Summary

### 3.1 Backend Roles (Complementary, Not Competing)

| Backend | Role |
|---------|------|
| GROBID | Semantic structure: sections, paragraphs, references, IMRaD labels, TEI coordinates |
| PyMuPDF | Scan detection + font metadata. On native pages provides font evidence for section verification. On scanned pages, runs `page.get_textpage_ocr()` for cross-validation. |
| pdfplumber | Structural ground truth: character-level bounding boxes, table spatial layout, per-page text for text-fidelity comparison |
| PaddleOCR | Primary OCR backend for confirmed scanned pages. Returns pixel-space bounding boxes that must be mapped to PDF coordinate space using rasterisation DPI. |

GROBID and pdfplumber always both run on every native page. They are never
alternatives. PaddleOCR runs only on confirmed scanned pages. PyMuPDF always
runs for scan detection; on native pages it also provides font metadata.

### 3.2 Two Output Types

**Semantic Output** — populated from GROBID: sections, paragraphs, references,
IMRaD label per section, TEI-derived coordinates, citation linkage.

**Structural Output** — populated from pdfplumber: pages with dimensions,
text blocks with character-level bounding boxes, tables with cell grids,
figures with spatial extents.

Both outputs are produced for every born-digital document. For confirmed
scanned pages, the Semantic Output uses PaddleOCR text (flagged `ocr_derived:
true`) and the Structural Output uses PaddleOCR bounding boxes mapped to PDF
coordinate space.

### 3.3 UnifiedRecord Layer Structure

```
UnifiedRecord
├── document_id: str
├── content: dict          (retained for backward compatibility during migration)
├── semantic: SemanticLayer | None
│   ├── metadata: dict     (title, authors, DOI, journal, publication date)
│   ├── sections: list     (IMRaD label, heading text, depth, paragraph refs)
│   ├── paragraphs: list   (text, section ref, sentence refs)
│   ├── sentences: list    (text, paragraph ref — produced by configured SentenceSegment backend)
│   └── references: list   (parsed bibliography + inline citation linkage)
├── structural: StructuralLayer | None
│   ├── pages: list        (page_index, width, height in PDF user-space pts)
│   ├── blocks: list       (bbox, page_index, text, font, size, bold/italic)
│   ├── tables: list       (bbox, page_index, cell grid, caption ref)
│   └── figures: list      (bbox, page_index, caption ref)
└── alignment: AlignmentMap | None
    ├── paragraph_to_blocks: list
    ├── sentence_to_char_range: list
    ├── section_header_to_block: list
    └── reconciliation_flags: list[AlignmentMapEntry]
        ├── source: str                (extractor name; not constrained to a fixed set)
        ├── ocr_derived: bool
        ├── ocr_engines: list[str]
        ├── agreement: "full" | "partial" | "divergent" | "one_engine_only"
        ├── edit_distance: float       (normalized Levenshtein [0.0, 1.0])
        ├── preferred_reading: str     (set by the injected concern strategy)
        └── confidence: float          ([0.0, 1.0])
```

### 3.4 QC Pipeline Stage Flow

```
run_quality_control(branches, document_id, config)
  │
  ├─ [Closure] _pdf_rater_fn  ──► Tier 1: LocalQCReport (8 metrics)
  │                                ↳ TextProcessor.tokenize_sentences() for sentence records
  ├─ [Closure] _pdf_iaa_fn   ──► InterRaterReport.compute()
  ├─ [Closure] _pdf_adjudicator_fn ──► Injectable concern-specific adjudication strategies
  └─ [Closure] _pdf_reconciler_fn
         │
         ├─ text_fidelity_strategy.reconcile(primary_artifact, reference_artifact, tp)
         ├─ section_strategy.reconcile(primary_section, reference_block, tp)
         └─ table_figure_strategy.merge(primary_caption_ref, reference_spatial)
              │
              └─► AlignmentMap  ──► UnifiedRecord(semantic, structural, alignment)
                                         │
                                         └─► W3CAnnotationProjector.project()
                                                   │
                                                   └─► artifact_generator.generate_w3c_jsonld()
```

`run_pipeline` is called by `run_quality_control` and sees none of this.
It receives only the four injectable callables and the branches list.

### 3.5 Five-Stage Per-Page Scan Detection

```
Stage 1: page.get_text().strip() == ""            → scanned (fast exit)
Stage 2: word_count < text_density_threshold      → scanned candidate
Stage 3: alpha_ratio < alpha_ratio_threshold       → scanned candidate
Stage 4: page.get_fonts() returns zero embeddings  → scanned candidate
Stage 5: image_coverage > image_dominance_threshold → scanned
Decision: native only if Stage 1–5 all pass; else OCR path
Provenance: triggered_stages: list[int], computed values recorded per page
```

### 3.6 Pluggable Text Processing

All three text processing tasks — sentence segmentation, word segmentation, and
text normalization — are handled by independently configurable backends. The
`TextProcessor` class composes these backends. The `SentenceSegment` class
hierarchy provides the sentence segmentation abstraction. Users may supply
custom implementations by subclassing the relevant base or by pointing
`text_processor.class` to any class that conforms to the `TextProcessor`
interface.

| Task | Default backend | Built-in alternatives |
|------|-----------------|-----------------------|
| Sentence segmentation | scispaCy (`en_core_sci_lg`) | wtpsplit, NLTK Punkt, spaCy Sentencizer, stanza |
| Word segmentation | spaCy | NLTK |
| Text normalization | spaCy | python-ftfy, unicodedata NFKC, NLTK, textacy, symspellpy |

---

## 4. Files to Delete

### `pdf_extractor/extraction/Tesseract.py`

**Reason:** §2.7 explicitly requires removal of the standalone Tesseract
integration. PaddleOCR is the designated scanned-page OCR backend.
`page.get_textpage_ocr()` (PyMuPDF) is the cross-validation path and does
not require `pytesseract`.

**Cascade:** Remove the import `from .Tesseract import extract_with_tesseract`
from `pdf_extractor/extraction/__init__.py`. Any test that imports or mocks
`extract_with_tesseract` must be updated.

---

## 5. Files to Create

### `utils/text_processor.py`

Public text transformation interface. §2.6 specifies six methods. All
backend-specific logic for sentence segmentation, word segmentation, and text
normalization is isolated in configurable sub-components; `TextProcessor` itself
is backend-agnostic.

```python
class TextProcessor:
    def __init__(self, config: dict | None = None): ...
    def normalize(self, text: str) -> str: ...
    def tokenize_sentences(self, text: str) -> list[str]: ...
    def tokenize_words(self, text: str) -> list[str]: ...
    def compare(self, a: str, b: str) -> float: ...        # normalized Levenshtein [0.0, 1.0]
    def clean_ocr(self, text: str) -> str: ...
    def extract_keywords(self, text: str) -> list[str]: ...
```

`TextProcessor` composes three pluggable components, each resolved at
construction time from config:

- `_segmenter: SentenceSegment` — delegates `tokenize_sentences()`
- `_word_tokenizer` — delegates `tokenize_words()`
- `_normalizer` — delegates `normalize()`

**`SentenceSegment` class hierarchy:**

`SentenceSegment` is the abstract base for all sentence boundary detectors. It
is also a subclass of `TextProcessor` (inheriting the full interface), so any
`SentenceSegment` concrete implementation is a valid drop-in for
`TextProcessor` itself when plugged in via `text_processor.class`.

```python
class SentenceSegment(TextProcessor):
    """Abstract base for sentence boundary detection backends."""
    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError

class ScispaCySentenceSegment(SentenceSegment):
    """Default. Uses en_core_sci_lg (or configured model). Lazy-loaded."""
    ...

class WtpSplitSentenceSegment(SentenceSegment):
    """wtpsplit backend. Requires: pip install wtpsplit"""
    ...

class NLTKPunktSentenceSegment(SentenceSegment):
    """NLTK Punkt tokenizer. Requires: nltk.download('punkt')"""
    ...

class SpacySentencizerSegment(SentenceSegment):
    """spaCy rule-based Sentencizer component (no model download required)."""
    ...

class StanzaSentenceSegment(SentenceSegment):
    """Stanford NLP / stanza backend. Requires: pip install stanza"""
    ...
```

The config key `text_processor.sentence_tokenizer.backend` selects the
implementation. Recognized string values:

| Config value | Class |
|---|---|
| `"scispacy"` (default) | `ScispaCySentenceSegment` |
| `"wtpsplit"` | `WtpSplitSentenceSegment` |
| `"nltk_punkt"` | `NLTKPunktSentenceSegment` |
| `"spacy_sentencizer"` | `SpacySentencizerSegment` |
| `"stanza"` | `StanzaSentenceSegment` |

A fully qualified class path may also be supplied to use a custom implementation.

**Word segmentation backends:**

| Config value | Backend |
|---|---|
| `"spacy"` (default) | spaCy tokenizer via the same loaded model |
| `"nltk"` | `nltk.tokenize.word_tokenize` |

**Text normalization backends:**

| Config value | Backend |
|---|---|
| `"spacy"` (default) | spaCy unicode normalization utilities |
| `"ftfy"` | `python-ftfy` (fixes text encoding issues) |
| `"nfkc"` | `unicodedata.normalize("NFKC", ...)` |
| `"nltk"` | NLTK-based normalization |
| `"textacy"` | textacy preprocessing pipeline |
| `"symspellpy"` | symspellpy spelling correction + normalization |

**Method implementation requirements:**

- `normalize`: NFC unicode normalization by default (NFKC when configured),
  whitespace collapse (multiple whitespace → single space), ligature expansion
  (ﬁ → fi, ﬂ → fl, etc.), strip leading/trailing whitespace.
  Idempotent: `normalize(normalize(x)) == normalize(x)`.

- `tokenize_sentences`: delegates to the configured `_segmenter`. The default
  `ScispaCySentenceSegment` lazy-loads `en_core_sci_lg` on first call and caches
  the loaded model as an instance attribute. If the required package is not
  installed, raises `ImportError` with a `pip install` hint specific to the
  configured backend. Returns `list[str]`.

- `tokenize_words`: apply `normalize()` first, then delegate to the configured
  word tokenizer. Returns `list[str]`.

- `compare`: normalized Levenshtein ratio using `difflib.SequenceMatcher`.
  Apply `normalize()` to both inputs before comparing.
  Returns `float` in `[0.0, 1.0]` where `1.0` means identical.

- `clean_ocr`: remove U+FFFD replacement characters, C0 control characters
  (`\x00`–`\x08`, `\x0b`, `\x0c`, `\x0e`–`\x1f`), and repair common
  ligature mis-encodings. This absorbs the regex logic currently inside
  `local_metrics.py:_check_weird_char_ratio` for production use (the metric
  check remains in `local_metrics.py` for reporting; `clean_ocr` is the
  correction path).

- `extract_keywords`: lowercase, split, filter against a built-in English
  stopword list. Returns `list[str]`.

**Instantiation:** Config-driven. The config key `text_processor.class`
(default: `"utils.text_processor.TextProcessor"`) is loaded via `importlib`
by `run_quality_control`. One instance is created per pipeline run and passed
as a dependency to all stage closures that need it.

---

### `pdf_extractor/extraction/scan_detector.py`

Per-page scan classification with full provenance.

```python
@dataclass
class PageScanClassification:
    page_index: int
    is_native: bool
    triggered_stages: list[int]
    stage_values: dict[str, float]   # e.g. {"alpha_ratio": 0.42, "word_count": 12}

def classify_page(
    page,                            # fitz.Page
    text_processor: "TextProcessor",
    config: dict,
) -> PageScanClassification:
    ...
```

**Stage logic:**

| Stage | Condition | Trigger value |
|-------|-----------|--------------|
| 1 | `page.get_text().strip() == ""` | empty string → immediate scanned verdict |
| 2 | `word_count < config["scan_detection"]["text_density_threshold"]` | word count |
| 3 | `alpha_ratio < config["scan_detection"]["alpha_ratio_threshold"]` | alpha_ratio: alpha chars / non-ws chars |
| 4 | `len(page.get_fonts()) == 0` | zero embedded fonts |
| 5 | `image_area / page_area > config["scan_detection"]["image_dominance_threshold"]` | coverage ratio |

Stage 1 is an early exit: if it fires, stages 2–5 are skipped and only `[1]`
appears in `triggered_stages`.

**Decision rule:** `is_native = True` only when no stage fires.

**TextProcessor dependency:** Stage 3 alpha-ratio computation uses
`text_processor.clean_ocr()` on the raw page text before counting characters.

**No global state.** The function is stateless; it does not cache results.

---

### `quality_control/concerns/__init__.py`

Empty init that exports:
```python
from .text_fidelity import TextFidelityConcern, DEFAULT_TEXT_FIDELITY
from .section_verification import SectionVerificationConcern, DEFAULT_SECTION_VERIFICATION
from .table_figure_merge import TableFigureMergeConcern, DEFAULT_TABLE_FIGURE_MERGE, MissingContributionError
```

---

### `quality_control/concerns/text_fidelity.py`

```python
class TextFidelityConcern:
    def reconcile(
        self,
        primary_artifact: str,       # subject under test (e.g. GROBID paragraph)
        reference_artifact: str,     # ground truth (e.g. pdfplumber block)
        text_processor: TextProcessor,
    ) -> AlignmentMapEntry:
        ...
```

**Behavior:**
- Compute `edit_distance = 1.0 - text_processor.compare(primary_artifact, reference_artifact)`.
- If `edit_distance > config["text_fidelity"]["edit_distance_threshold"]`:
  set `agreement = "divergent"`.
- Else if `edit_distance > 0`:
  set `agreement = "partial"`.
- Else:
  set `agreement = "full"`.
- `preferred_reading` is always `reference_artifact`. The strategy itself
  encodes which argument is the ground truth — the reconciler does not.
- `source` is set to a string identifying the reference extractor, supplied
  via constructor injection (default: `"reference"`).
- `ocr_derived = False`, `ocr_engines = []`.
- `confidence = 1.0 - edit_distance`.

The asymmetry (which argument is the subject, which is the ground truth) is a
property of this strategy class. Callers such as the reconciler treat both
arguments as generic artifacts and let this class determine `preferred_reading`
and `source`.

`DEFAULT_TEXT_FIDELITY = TextFidelityConcern(source_label="pdfplumber")`.

---

### `quality_control/concerns/section_verification.py`

```python
class SectionVerificationConcern:
    def reconcile(
        self,
        primary_section: dict,       # carries: heading, label, depth (extractor-agnostic keys)
        reference_block: dict,       # carries: bbox, font_size, bold, text
        text_processor: TextProcessor,
    ) -> float:                      # confidence only; never modifies primary_section
        ...
```

**Behavior:**
- Compare `primary_section["heading"]` against `reference_block["text"]`
  using `text_processor.compare()`.
- If the reference block's font size is below the median body font size
  (passed via config threshold `section_verification.font_size_tolerance`),
  lower `confidence` proportionally.
- Never modify `primary_section` or any of its fields. The label assigned by
  the primary extractor is read-only from the perspective of this concern.
- Return `confidence: float` in `[0.0, 1.0]`.

`DEFAULT_SECTION_VERIFICATION = SectionVerificationConcern()`.

---

### `quality_control/concerns/table_figure_merge.py`

```python
class MissingContributionError(ValueError):
    pass

class TableFigureMergeConcern:
    def merge(
        self,
        primary_caption_ref: dict | None,       # logical record (e.g. GROBID)
        reference_table_spatial: dict | None,   # spatial record (e.g. pdfplumber)
    ) -> dict:
        ...
```

**Behavior:**
- If either argument is `None`, raise `MissingContributionError` naming which
  side is absent.
- Return a merged record with two top-level sub-fields whose keys are
  determined by the strategy's constructor (default: `"primary"` and
  `"reference"`):
  ```python
  {
      "primary": primary_caption_ref,         # the logical record, unmodified
      "reference": reference_table_spatial,   # the spatial record, unmodified
      "agreement": "full" | "partial" | "divergent",
      "merged_text": ...,                     # caption text for display
  }
  ```

`DEFAULT_TABLE_FIGURE_MERGE = TableFigureMergeConcern(
    primary_label="grobid", reference_label="pdfplumber"
)`.

---

### `pdf_extractor/annotation/w3c_annotation.py`

W3C Web Annotation in-memory data model. Reads from `SemanticLayer` +
`AlignmentMap` only. Does not re-read raw extractor output and does not
produce JSON. JSON serialization is the responsibility of `artifact_generator.py`.

```python
@dataclass
class AnnotationRecord:
    id: str
    body_value: str
    body_format: str
    body_language: str
    ocr_derived: bool
    ocr_engines: list[str]
    source: str
    selector_type: str           # "TextPositionSelector" | "FragmentSelector"
    selector_payload: dict       # populated fields depend on selector_type
    quote_selector: dict         # {"exact": str, "prefix": str, "suffix": str}

def project(
    unified: UnifiedRecord,
    base_uri: str = "",
) -> list[AnnotationRecord]:
    """Return a list of AnnotationRecord objects (no JSON, no serialization)."""
    ...
```

**Born-digital pages** (entries where `AlignmentMapEntry.ocr_derived == False`):
- `selector_type = "TextPositionSelector"`.
- `selector_payload` contains `{"start": int, "end": int}` from
  `alignment.sentence_to_char_range`.
- `quote_selector` populated with `exact`, `prefix`, `suffix`.

**Scanned pages** (entries where `AlignmentMapEntry.ocr_derived == True`):
- `selector_type = "FragmentSelector"`.
- `selector_payload` contains `{"page": int, "xywh": str}` derived from the
  PaddleOCR block bbox in `StructuralLayer.blocks`.
- `ocr_derived = True`, `ocr_engines` populated from `AlignmentMapEntry`.

Both paths are first-class. A projection may contain entries from both paths if
the document has mixed native and scanned pages.

---

### `pdf_extractor/annotation/artifact_generator.py`

JSON serialization layer for annotation output. Consumes `AnnotationRecord`
objects produced by `w3c_annotation.project()` and renders them to W3C
JSON-LD dicts. No business logic about selectors or extractors lives here.

```python
def generate_w3c_jsonld(
    records: list[AnnotationRecord],
    base_uri: str = "",
) -> list[dict]:
    """Serialize AnnotationRecord objects to W3C Web Annotation JSON-LD dicts."""
    ...
```

**Output format per record:**

```json
{
  "@context": "http://www.w3.org/ns/anno.jsonld",
  "id": "urn:evitrace:anno:<uuid>",
  "type": "Annotation",
  "body": {
    "type": "TextualBody",
    "value": "<sentence text>",
    "format": "text/plain",
    "language": "en",
    "ocr_derived": false,
    "ocr_engines": []
  },
  "target": {
    "source": "<base_uri or document_id>",
    "selector": [
      {
        "type": "TextPositionSelector",
        "start": 1024,
        "end": 1089
      },
      {
        "type": "TextQuoteSelector",
        "exact": "...",
        "prefix": "...",
        "suffix": "..."
      }
    ]
  }
}
```

For scanned pages, `TextPositionSelector` is replaced with `FragmentSelector`:
```json
{
  "type": "FragmentSelector",
  "conformsTo": "http://www.w3.org/TR/media-frags/",
  "value": "page=<N>&xywh=<x0>,<y0>,<w>,<h>"
}
```
and `"ocr_derived": true` with `"ocr_engines": ["paddleocr"]` in the body.

---

## 6. Files to Modify

### `quality_control/models.py`

Add three new dataclasses **before** `UnifiedRecord`. Extend `UnifiedRecord`
with three optional fields. Do not change any existing fields or classes.

```python
@dataclass
class SemanticLayer:
    metadata: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    paragraphs: list = field(default_factory=list)
    sentences: list = field(default_factory=list)
    references: list = field(default_factory=list)

@dataclass
class StructuralLayer:
    pages: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    figures: list = field(default_factory=list)

@dataclass
class AlignmentMapEntry:
    source: str = "native"          # extractor name; not constrained to a fixed set
    ocr_derived: bool = False
    ocr_engines: list = field(default_factory=list)
    agreement: str = "full"
    edit_distance: float = 0.0
    preferred_reading: str = ""     # set by the injected concern strategy
    confidence: float = 1.0

@dataclass
class AlignmentMap:
    paragraph_to_blocks: list = field(default_factory=list)
    sentence_to_char_range: list = field(default_factory=list)
    section_header_to_block: list = field(default_factory=list)
    reconciliation_flags: list = field(default_factory=list)  # list[AlignmentMapEntry]
```

Extend `UnifiedRecord`:
```python
@dataclass
class UnifiedRecord:
    document_id: str = ""
    content: dict = field(default_factory=dict)     # kept for backward compat
    semantic: SemanticLayer | None = None            # NEW
    structural: StructuralLayer | None = None        # NEW
    alignment: AlignmentMap | None = None            # NEW
```

Update `__all__` in `quality_control/__init__.py` to export the four new
dataclasses.

---

### `quality_control/reconciler.py`

Replace the existing `_reconcile_blocks` winner-takes-all strategy with
concern-type routing. The reconciler is fully agnostic about which extractors
are present — all extractor-specific logic is encoded in the injected
strategies. The reconciler's role is to route inputs to strategies, collect
results, and assemble the output model.

```python
def reconcile(
    primary_artifact: dict,
    secondary_artifact: dict,
    primary_observation: dict,
    secondary_observation: dict,
    investigator_object: dict,
    adjudication_decisions: dict | None = None,
    config: dict | None = None,
    *,
    text_fidelity_strategy=None,
    section_strategy=None,
    table_figure_strategy=None,
    text_processor=None,
) -> dict:
```

The argument names `primary_artifact` / `secondary_artifact` and
`primary_observation` / `secondary_observation` are intentionally
extractor-agnostic. Callers (i.e. the PDF-domain closure in
`run_quality_control`) determine which extractor fills each role. The reconciler
does not know or care which extractor produced which input.

The three concern strategy arguments default to the module-level instances from
`quality_control/concerns/__init__.py` if not provided. `text_processor`
defaults to a freshly instantiated `TextProcessor()` if not provided (for
backward compatibility).

**Concern routing logic:**

1. For each primary paragraph that has a matching secondary text block (matched
   by page_index and approximate bbox overlap), call
   `text_fidelity_strategy.reconcile(primary_text, secondary_text, text_processor)`.
   Collect `AlignmentMapEntry` results.

2. For each primary section heading that has a candidate secondary block by
   font size, call
   `section_strategy.reconcile(primary_section, secondary_block, text_processor)`.
   Record the returned confidence score alongside the section's provenance.

3. For each primary-detected table/figure caption reference that has a
   secondary spatial record, call
   `table_figure_strategy.merge(primary_caption_ref, secondary_spatial)`.
   Store merged records in `StructuralLayer.tables` / `.figures`.

4. Assemble `AlignmentMap` from all `AlignmentMapEntry` results.

5. Build `SemanticLayer` from primary-artifact metadata fields (title, authors,
   abstract, sections, references). These fields are populated from the existing
   artifact structure via a helper that returns empty values if not yet
   implemented.

6. Build `StructuralLayer` from secondary-artifact blocks.

7. Return a `UnifiedRecord` with `semantic`, `structural`, and `alignment`
   populated in addition to `content` (backward compat).

The existing `PLACEHOLDER_NOTICE` path (when `adjudication_decisions is None`)
is retained for backward compatibility.

---

### `quality_control/adjudicator.py`

Replace the symmetric `_make_adjudication_decisions` with fully
strategy-delegated adjudication. The adjudicator module is agnostic about
extractor identity; all asymmetry and ground-truth designation is expressed
through the injectable concern strategies.

**Remove:**
- `_compute_text_quality_score` — symmetric alpha-ratio on two named extractors.
- `_evaluate_extractor_quality` — symmetric quality evaluation.
- All hardcoded references to `"pdfplumber"` or `"grobid"` as `primary_extractor`
  values. These determinations belong in the strategy, not the adjudicator.
- The winner-takes-all logic in `_make_adjudication_decisions`.

**Add:**

```python
def _adjudicate_concern(
    alignment_entries: list,
    strategy,
    config: dict,
) -> dict:
    """
    Delegate concern-level adjudication entirely to the provided strategy.
    Returns a decisions dict with keys: preferred_source, confidence, rationale.
    The strategy determines which source is preferred; the adjudicator does not.
    """
    return strategy.adjudicate(alignment_entries, config)
```

The public `adjudicate` function signature is unchanged. Internally it now:
1. Iterates over concern types present in the `AlignmentMap`.
2. For each concern, calls `_adjudicate_concern(entries, strategy, config)`,
   where the strategy is passed in by the caller (defaulting to the module-level
   defaults from `quality_control/concerns/__init__.py`).
3. Assembles the adjudication decisions dict from strategy return values.
   The `preferred_source` key (formerly `primary_extractor`) is populated
   entirely by the strategy — the adjudicator never assigns it directly.
4. Records concern-level summaries (confidence, rationale) without assuming
   which extractor those values favour.

Each concern strategy must implement an `adjudicate(alignment_entries, config)`
method in addition to its `reconcile` / `merge` method. The default PDF-domain
strategies (e.g. `DEFAULT_TEXT_FIDELITY`) encode the GROBID/pdfplumber
asymmetry in their `adjudicate` implementations.

---

### `quality_control/quality_control.py`

**Remove `_split_sentences`** (lines 137–143). Replace every call site:

- Line 219: `sentence_records = [{"sentence": s} for s in _split_sentences(full_text)]`
  → `sentence_records = [{"sentence": s} for s in text_processor.tokenize_sentences(full_text)]`

- Lines 236–245 in `_build_placeholder_sentence_store`:
  `first_sentence = _split_sentences(full_text)[:1]`
  → `first_sentence = text_processor.tokenize_sentences(full_text)[:1]`

**Wire `text_processor` into the PDF rater closure.** `run_quality_control`
instantiates a `TextProcessor` (using the class path from config) before the
closures are defined, then captures it by closure:

```python
tp_class_path = config.get("text_processor", {}).get("class", "utils.text_processor.TextProcessor")
# importlib.import_module + getattr to load the class
text_processor = <loaded class>(config=config.get("text_processor", {}))
```

The `text_processor` instance is passed into `_build_tier1_report` so that
`_split_sentences` is replaced (Invariant 1).

**Invariant 3 enforcement:** No new import of any PDF-specific symbol may be
added to the body of `run_pipeline`. All concern module imports and W3C
annotation imports live in `run_quality_control`'s local scope or in
`_pdf_reconciler_fn`.

**Wire concern strategies into `_pdf_reconciler_fn`:**

```python
def _pdf_reconciler_fn(decision, all_branches, cfg):
    unified_output = _run_legacy_pipeline(...)      # existing legacy path
    # NEW: concern-aware reconciliation
    updated_unified = reconciler.reconcile(
        primary_artifact=grobid_branch,
        secondary_artifact=pdfplumber_branch,
        primary_observation=grobid_observation,
        secondary_observation=pdfplumber_observation,
        investigator_object=investigator,
        text_fidelity_strategy=DEFAULT_TEXT_FIDELITY,
        section_strategy=DEFAULT_SECTION_VERIFICATION,
        table_figure_strategy=DEFAULT_TABLE_FIGURE_MERGE,
        text_processor=text_processor,
    )
    annotation_records = w3c_annotation.project(updated_unified)
    updated_unified.w3c_annotations = artifact_generator.generate_w3c_jsonld(annotation_records)
    return updated_unified
```

---

### `pdf_extractor/processing/sentence_processor.py`

**Remove** `_RE_SENTENCE_SPLIT` (line 167) and the line:
```python
candidates = _RE_SENTENCE_SPLIT.split(normalised)
```

**Change `process_sentences` signature:**
```python
def process_sentences(
    text_blocks_with_pages: list,
    len_filter: int,
    text_processor,          # NEW: TextProcessor instance
) -> list:
```

Replace `candidates = _RE_SENTENCE_SPLIT.split(normalised)` with:
```python
candidates = text_processor.tokenize_sentences(normalised)
```

The `is_noise` filter and `len_filter` check are unchanged. The noise filter
operates on the tokenized sentences, not on the raw split.

**Callers of `process_sentences`** must be updated to pass a `TextProcessor`
instance. The primary caller is `pdf_extractor/pdf_extractor.py`. Trace all
callers and add the argument.

---

### `pdf_extractor/extraction/__init__.py`

Replace the four-tier waterfall with scan-detector-driven routing.

**Remove:**
- `from .Tesseract import extract_with_tesseract`
- The `_compute_quality_score` function.
- The waterfall cascade inside `extract_pdf`.

**New `extract_pdf` behavior:**

```python
def extract_pdf(
    pdf_path: str,
    ocr: bool,
    ocr_text_quality_threshold: float,   # kept for API compat; used as scan threshold fallback
    embed_model=None,
    text_processor=None,
    config: dict | None = None,
) -> tuple:
```

1. Open the document with `fitz.open(pdf_path)`.
2. For each page, call `scan_detector.classify_page(page, text_processor, config)`.
3. Collect results into `native_pages` and `scanned_pages` lists.
4. For native pages: run both pdfplumber (structural) and accumulate font
   metadata from PyMuPDF. Return pdfplumber blocks as the primary block list
   and font metadata from PyMuPDF separately.
5. For scanned pages: run PaddleOCR. Each block must carry the DPI used for
   rasterization and a pixel-to-PDF coordinate map.
6. Return a combined block list and the font metadata dict.
7. If `ocr=False`, skip scan detection entirely and run pdfplumber only.

**GROBID is not called here.** GROBID runs separately as a distinct branch in
`run_quality_control`, not inside `extract_pdf`.

---

### `pdf_extractor/extraction/PyMuPDF.py`

Add a `get_scan_classification` function that accepts a pre-opened `fitz.Page`
and delegates to `scan_detector.classify_page`.

```python
def get_page_font_metadata(page) -> list[schemas.FontMetaDict]:
    """Extract font metadata from a single fitz.Page."""
    ...
```

This separates font-metadata extraction from full document traversal. No other
changes to the block extraction logic.

---

### `pdf_extractor/extraction/PaddleOCR.py`

Add DPI parameter and per-block pixel-to-PDF coordinate mapping.

**New signature:**
```python
def extract_with_paddleocr(
    pdf_path: str,
    dpi: int = 150,
) -> list[schemas.BlockDict]:
```

**Coordinate mapping:** For each bounding box returned by PaddleOCR
(`line_info[0]`, a list of four `[x, y]` pixel-space corners), compute the
PDF coordinate space rectangle:

```
pdf_x = pixel_x * (72.0 / dpi)
pdf_y = pixel_y * (72.0 / dpi)
```

Store the PDF-space bbox in `block_bbox` (a 4-tuple `(x0, y0, x1, y1)` in
PDF user-space points). Also store `"rasterization_dpi": dpi` and
`"ocr_confidence": float` inside each block dict.

---

### `utils/config_utils.py`

Extend `_QC_DEFAULTS` with the new configuration keys. All new keys must have
defaults so that existing config files continue to work unchanged.

```python
_QC_DEFAULTS = {
    # ... existing keys ...
    "text_processor": {
        "class": "utils.text_processor.TextProcessor",
        "sentence_tokenizer": {
            "backend": "scispacy",          # scispacy | wtpsplit | nltk_punkt | spacy_sentencizer | stanza
            "model": "en_core_sci_lg",      # used only when backend == "scispacy"
        },
        "word_tokenizer": {
            "backend": "spacy",             # spacy | nltk
        },
        "normalizer": {
            "backend": "spacy",             # spacy | ftfy | nfkc | nltk | textacy | symspellpy
        },
        "comparison": {
            "metric": "levenshtein",
            "threshold": 0.85,
        },
        "ocr_cleaning": {
            "weird_char_threshold": 0.05,
        },
    },
    "quality_control": {
        # ... existing quality_control keys ...
        "scan_detection": {
            "text_density_threshold": 50,
            "alpha_ratio_threshold": 0.60,
            "image_dominance_threshold": 0.85,
        },
        "ocr": {
            "rasterization_dpi": 150,
        },
        "text_fidelity": {
            "edit_distance_threshold": 0.10,
        },
        "section_verification": {
            "font_size_tolerance": 1.0,
        },
    },
}
```

---

## 7. Config Additions

Add the following blocks to `config/config.yaml`:

```yaml
text_processor:
  class: "utils.text_processor.TextProcessor"
  sentence_tokenizer:
    backend: "scispacy"        # scispacy | wtpsplit | nltk_punkt | spacy_sentencizer | stanza
    model: "en_core_sci_lg"    # relevant only when backend is "scispacy"
  word_tokenizer:
    backend: "spacy"           # spacy | nltk
  normalizer:
    backend: "spacy"           # spacy | ftfy | nfkc | nltk | textacy | symspellpy
  comparison:
    metric: "levenshtein"
    threshold: 0.85
  ocr_cleaning:
    weird_char_threshold: 0.05

quality_control:
  # ... existing entries unchanged ...
  scan_detection:
    text_density_threshold: 50      # words per page below which scan is suspected
    alpha_ratio_threshold: 0.60     # alpha-char ratio below which scan is suspected
    image_dominance_threshold: 0.85 # image area fraction above which scan is confirmed
  ocr:
    rasterization_dpi: 150
  text_fidelity:
    edit_distance_threshold: 0.10   # normalized Levenshtein above which divergent
  section_verification:
    font_size_tolerance: 1.0        # pts below median body font to downgrade confidence
```

---

## 8. Implementation Requirements

### 8.1 TextProcessor Singleton Pattern

A single `TextProcessor` instance is created per pipeline invocation inside
`run_quality_control`. It is never re-instantiated per document or per page.

```python
import importlib

def _load_text_processor(config: dict):
    class_path = (
        config.get("text_processor", {}).get("class", "utils.text_processor.TextProcessor")
    )
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(config=config.get("text_processor", {}))
```

### 8.2 SentenceSegment Backend Loading

`ScispaCySentenceSegment.tokenize_sentences` uses lazy loading:

```python
def tokenize_sentences(self, text: str) -> list[str]:
    if self._nlp is None:
        try:
            import spacy
            self._nlp = spacy.load(self._model_name)
        except ImportError:
            raise ImportError(
                "scispaCy is required for the 'scispacy' sentence backend. "
                "Install with: pip install scispacy && "
                "pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/"
                "releases/v0.5.3/en_core_sci_lg-0.5.3.tar.gz"
            )
    doc = self._nlp(text)
    return [sent.text for sent in doc.sents]
```

All other built-in backends follow the same lazy-load + `ImportError`-with-hint
pattern for their respective packages. Tests mock `self._nlp` directly or mock
the underlying `spacy.load` / library entry point to avoid requiring model
downloads.

If `text_processor.sentence_tokenizer.backend` is set to an unrecognised
string, `TextProcessor.__init__` raises `ValueError` with a message listing the
valid built-in options.

### 8.3 Reconciler and Adjudicator Agnosticism

Neither `reconciler.py` nor `adjudicator.py` may contain:

- Any import of, or string reference to, `"grobid"`, `"pdfplumber"`, `"paddleocr"`,
  or any other extractor name as a hardcoded literal that influences control flow
  or output shape.
- Any logic that assigns `preferred_reading`, `preferred_source`, or
  `primary_extractor` without delegating to an injected strategy.

The default PDF-domain strategies (`DEFAULT_TEXT_FIDELITY`,
`DEFAULT_SECTION_VERIFICATION`, `DEFAULT_TABLE_FIGURE_MERGE`) are defined in
`quality_control/concerns/` and are injected at the call site in
`run_quality_control`. The reconciler and adjudicator receive them as arguments
and must treat them as opaque callable objects.

### 8.4 Artifact Generation Separation

`w3c_annotation.project()` must return `list[AnnotationRecord]` with no JSON
serialization, no `json.dumps`, and no dict construction. It is a pure
data-model projection.

`artifact_generator.generate_w3c_jsonld()` is the only function permitted to
construct the final `list[dict]` output. No other module may produce W3C
JSON-LD dicts. This separation ensures the annotation data model can be
consumed by other serializers (e.g., RDF, XML) without changes to
`w3c_annotation.py`.

### 8.5 Pixel-to-PDF Coordinate Mapping in PaddleOCR

```python
corners = line_info[0]     # [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
xs = [c[0] for c in corners]
ys = [c[1] for c in corners]
pdf_x0 = min(xs) * (72.0 / dpi)
pdf_y0 = min(ys) * (72.0 / dpi)
pdf_x1 = max(xs) * (72.0 / dpi)
pdf_y1 = max(ys) * (72.0 / dpi)
block_bbox = (pdf_x0, pdf_y0, pdf_x1, pdf_y1)
```

No additional y-flip is needed; `fitz`, pdfplumber, and PyMuPDF all report
bboxes in the same top-left-origin pixel convention when working from
rasterised images.

---

## 9. Prerequisite Ordering

The following ordering must be respected. Steps may be parallelised within
each tier but not across tiers.

**Tier 0 (no dependencies):**
- Delete `pdf_extractor/extraction/Tesseract.py`
- Create `utils/text_processor.py` (including `SentenceSegment` hierarchy)

**Tier 1 (depends on Tier 0):**
- Create `pdf_extractor/extraction/scan_detector.py` — depends on `TextProcessor`
- Modify `pdf_extractor/processing/sentence_processor.py` — depends on `TextProcessor`
- Replace `_split_sentences` in `quality_control/quality_control.py` — depends on `TextProcessor`

**Tier 2 (depends on Tier 0):**
- Add `SemanticLayer`, `StructuralLayer`, `AlignmentMap`, `AlignmentMapEntry`
  to `quality_control/models.py`

**Tier 3 (depends on Tier 2):**
- Create `quality_control/concerns/text_fidelity.py` — depends on `AlignmentMapEntry`
- Create `quality_control/concerns/section_verification.py` — depends on `AlignmentMapEntry`
- Create `quality_control/concerns/table_figure_merge.py` — depends on `AlignmentMapEntry`

**Tier 4 (depends on Tier 3):**
- Modify `quality_control/reconciler.py` — depends on all three concern modules
- Modify `quality_control/adjudicator.py` — depends on concern-strategy interface

**Tier 5 (depends on Tier 1, 3, 4):**
- Modify `quality_control/quality_control.py` — depends on `TextProcessor`, concern modules, and reconciler
- Modify `pdf_extractor/extraction/__init__.py` — depends on `scan_detector`

**Tier 6 (depends on Tier 5):**
- Modify `pdf_extractor/extraction/PaddleOCR.py` — depends on updated routing
- Modify `pdf_extractor/extraction/PyMuPDF.py` — depends on updated routing
- Create `pdf_extractor/annotation/w3c_annotation.py` — depends on `UnifiedRecord` layers (data model only)
- Create `pdf_extractor/annotation/artifact_generator.py` — depends on `w3c_annotation.AnnotationRecord`

**Tier 7 (parallel to all; applies globally):**
- Extend `utils/config_utils.py:_QC_DEFAULTS`
- Add new keys to `config/config.yaml`
- Update `quality_control/__init__.py` exports

---

## 10. Testing Plan

All tests that exercise sentence segmentation mock the segmenter backend. No
test may require any NLP model (scispaCy `en_core_sci_lg`, stanza, etc.) to be
downloaded or installed.

### New test files

**`tests/pdf_extractor/test_text_processor.py`**
- `normalize` is idempotent
- `normalize` collapses whitespace and expands ligatures
- `tokenize_sentences` with default backend (scispaCy) raises `ImportError` with pip hint when scispaCy absent
- `tokenize_sentences` uses cached model on second call (model loaded once per instance)
- `tokenize_sentences` with `backend="nltk_punkt"` raises `ImportError` with NLTK hint when absent
- `tokenize_sentences` with `backend="wtpsplit"` raises `ImportError` with wtpsplit hint when absent
- `tokenize_sentences` with `backend="spacy_sentencizer"` works without model download
- `tokenize_sentences` with `backend="stanza"` raises `ImportError` with stanza hint when absent
- Unrecognised `backend` value raises `ValueError` at `TextProcessor.__init__` time
- `tokenize_words` with `backend="spacy"` vs `backend="nltk"` both return `list[str]`
- `normalize` with `backend="nfkc"` vs `backend="ftfy"` both return normalized strings
- `compare` returns `1.0` for identical strings, `0.0` for completely different
- `clean_ocr` removes U+FFFD and C0 control characters
- `extract_keywords` excludes stopwords, lowercases output

**`tests/pdf_extractor/test_sentence_segment.py`**
- `ScispaCySentenceSegment` is a subclass of `SentenceSegment`
- `SentenceSegment` is a subclass of `TextProcessor`
- All five built-in `SentenceSegment` subclasses satisfy the `TextProcessor` interface
- Passing a `SentenceSegment` subclass as `text_processor.class` works end-to-end
  (verified via `_load_text_processor` with a mock backend)

**`tests/pdf_extractor/test_scan_detector.py`**
- Stage 1 triggers on empty page text
- Stage 2 triggers on low word count
- Stage 3 triggers on low alpha-ratio
- Stage 4 triggers when `get_fonts()` returns empty
- Stage 5 triggers on high image coverage
- All five stages can be triggered independently (mocked fitz.Page)
- Mixed document: some pages native, some scanned, correct per-page classification
- `stage_values` dict populated with computed values for each stage

**`tests/pdf_extractor/test_unified_record_layers.py`**
- `SemanticLayer` constructs with defaults, all list fields empty
- `StructuralLayer` constructs with defaults, all list fields empty
- `AlignmentMapEntry` with explicit values round-trips to dict
- `AlignmentMap` with multiple entries accumulates correctly
- `UnifiedRecord` with all three layers set (non-None)
- `UnifiedRecord.content` remains accessible alongside new layer fields
- `AlignmentMapEntry.source` is a free string; no test constrains it to a specific extractor name

**`tests/pdf_extractor/test_concern_text_fidelity.py`**
- Identical primary/reference text → `agreement = "full"`, `edit_distance = 0.0`
- Divergent text → `agreement = "divergent"`, `preferred_reading` is `reference_artifact`
- Swapping argument order produces a different result (asymmetry enforced by strategy)
- `AlignmentMapEntry.ocr_derived` is always `False`
- `DEFAULT_TEXT_FIDELITY.source_label` is `"pdfplumber"`

**`tests/pdf_extractor/test_concern_section_verification.py`**
- Matching heading and font size → high confidence
- Font size below median → reduced confidence
- `primary_section["imrad_label"]` (or equivalent key) unchanged after `reconcile()` call
- Return value is `float`, not a dict or `AlignmentMapEntry`

**`tests/pdf_extractor/test_concern_table_figure_merge.py`**
- Both arguments present → merged record has both `"primary"` and `"reference"` sub-fields
- `primary_caption_ref=None` → `MissingContributionError`
- `reference_table_spatial=None` → `MissingContributionError`
- Primary logical record is unmodified in merged output
- `DEFAULT_TABLE_FIGURE_MERGE` uses `"grobid"` / `"pdfplumber"` as field labels

**`tests/pdf_extractor/test_reconciler_concern_routing.py`**
- `reconcile()` calls `text_fidelity_strategy.reconcile()` for paragraph-type content
- `reconcile()` calls `section_strategy.reconcile()` for section-type content
- `reconcile()` calls `table_figure_strategy.merge()` for table-type content
- Custom strategy objects passed as arguments are used instead of defaults
- Returned `UnifiedRecord` has non-None `semantic`, `structural`, `alignment`
- No literal string `"grobid"` or `"pdfplumber"` appears in `reconciler.py` source
  (verified via `inspect.getsource`)

**`tests/pdf_extractor/test_adjudicator_strategy_delegation.py`**
- `adjudicate()` calls `strategy.adjudicate(alignment_entries, config)` for each concern type
- `preferred_source` in the output is set by the strategy, not by the adjudicator
- A custom strategy that always returns `preferred_source="custom_extractor"` is
  respected without modification
- No literal string `"grobid"` or `"pdfplumber"` appears in `adjudicator.py` source
  (verified via `inspect.getsource`)

**`tests/pdf_extractor/test_w3c_annotation.py`**
- `project()` returns `list[AnnotationRecord]`, not `list[dict]`
- Born-digital annotation record has `selector_type == "TextPositionSelector"`
- Scanned annotation record has `selector_type == "FragmentSelector"` and `ocr_derived == True`
- Both annotation types have a populated `quote_selector`
- `project()` produces no JSON, no `json.dumps`, no dict construction

**`tests/pdf_extractor/test_artifact_generator.py`**
- `generate_w3c_jsonld([])` returns `[]`
- Born-digital record serializes to dict with `TextPositionSelector`
- Scanned record serializes to dict with `FragmentSelector` and `"ocr_derived": true`
- Each output dict contains `"@context"`, `"id"`, `"type"`, `"body"`, `"target"` keys
- `"id"` field is a valid URN (non-empty string matching `urn:evitrace:anno:...`)

**`tests/pdf_extractor/test_run_pipeline_domain_agnostic.py`**
- `run_pipeline` is callable with mock callables that return dummy
  `QualityMetrics`, `InterRaterMetrics`, `AdjudicationRules`, `UnifiedRecord`
- No import of `fitz`, `grobid`, `pdfplumber`, or any PDF module appears in the
  `run_pipeline` function body (verified by reading the source with `inspect`)
- Passing a non-PDF branch payload does not raise

### Modifications to existing tests

- `test_quality_control_pipeline.py`: mock `TextProcessor.tokenize_sentences`
  to return `["sentence one", "sentence two"]`; verify `sentence_records` in
  `LocalQCReport` uses these values.
- `test_quality_control_local_metrics.py`: no change needed; `LocalQCReport`
  receives `sentence_records` as input, it does not call `_split_sentences`.
- `test_quality_control_reconciler.py`: update to pass mock concern strategies;
  verify `AlignmentMap` is populated; verify no hardcoded extractor names in call.
- `test_quality_control_adjudicator.py`: update to pass mock concern strategies
  with `adjudicate()` methods; verify `preferred_source` comes from the strategy.
- `test_text_extractor_tier1.py`, `test_text_extractor_tier2.py`,
  `test_text_extractor_tier3.py`: verify no import or reference to
  `extract_with_tesseract` anywhere.
- Any test that imports `sentence_processor.process_sentences` must now pass a
  mock `TextProcessor` as the third argument.

---

## 11. Acceptance Criteria

The implementation is complete when all of the following are true:

1. `grep -r "extract_with_tesseract" .` returns no results.
2. `grep -r "pytesseract" .` returns no results.
3. `grep -r "_split_sentences\|_RE_SENTENCE_SPLIT\|re\.split.*[.!?]" quality_control/ pdf_extractor/` returns no results.
4. `run_pipeline`'s source contains no reference to `grobid`, `pdfplumber`,
   `fitz`, `GROBID`, `PyMuPDF`, `PaddleOCR`, `TEI`, `scan`, or `AlignmentMap`.
   (Verified by `inspect.getsource(run_pipeline)` in a test.)
5. `reconciler.py`'s source contains no hardcoded literal strings `"grobid"` or
   `"pdfplumber"` in any control flow or output-shaping context.
   (Verified by `inspect.getsource`.)
6. `adjudicator.py`'s source contains no hardcoded assignment of `preferred_source`
   or `primary_extractor` to any specific extractor name.
   (Verified by `inspect.getsource`.)
7. A custom `SentenceSegment` subclass can be plugged in via config and used
   end-to-end without modifying any pipeline code.
8. `TextFidelityConcern.reconcile(a, b, tp)` and `TextFidelityConcern.reconcile(b, a, tp)`
   produce different `preferred_reading` values when `a != b`. (Asymmetry test.)
9. `TableFigureMergeConcern.merge(None, x)` raises `MissingContributionError`.
10. `TableFigureMergeConcern.merge(x, None)` raises `MissingContributionError`.
11. `w3c_annotation.project(unified)` returns `list[AnnotationRecord]` (not dicts)
    when `alignment.sentence_to_char_range` is non-empty.
12. `artifact_generator.generate_w3c_jsonld(records)` returns a non-empty
    `list[dict]` when `records` is non-empty.
13. A `UnifiedRecord` returned by `run_quality_control` has non-None `semantic`,
    `structural`, and `alignment` fields when at least one branch is present.
14. `PaddleOCR.extract_with_paddleocr(path, dpi=150)` returns blocks where
    `block_bbox` is a 4-tuple of floats (not `None`).
15. `scan_detector.classify_page(page, tp, config)` returns a
    `PageScanClassification` with `triggered_stages` recorded for every stage
    that fires.
16. All existing tests pass without modification except the targeted updates
    listed in §10.

---

## 12. Non-Goals

The following are explicitly out of scope for this implementation. They are
scaffolded where noted but not activated.

- **Active adjudication fallback patching.** The adjudicator may record that a
  page needs review, but it does not modify page content or swap block sources
  at runtime. Scaffolded with placeholder decision fields.

- **GROBID on scanned pages (degraded mode).** §2.7 describes running GROBID
  on low-confidence OCR output. The architecture is wired (OCR output goes into
  a GROBID branch) but the GROBID endpoint is not called with OCR text in this
  implementation.

- **NLP model download in CI.** Tests mock all segmenter backends. No NLP model
  (`en_core_sci_lg`, stanza, etc.) is required to be present in the CI environment.

- **Backward-incompatible changes to `QCContext.metrics_hierarchy` dict shape.**
  The `tier1`, `tier2`, `tier3` keys and their list structure are unchanged.

- **pdfplumber character-level bbox beyond current page-level blocks.** pdfplumber
  currently returns page-level blocks. Character-level extraction
  (`page.extract_words(keep_blank_chars=True)`) is out of scope.

- **LLM-generated task-specific QC criteria.** The adjudicator uses deterministic
  concern strategies only. No LLM call is added to the QC pipeline.

- **Full W3C annotation round-trip validation against the official JSON-LD context.**
  The output format is structurally correct but is not validated against the W3C
  JSON-LD schema in tests.

- **Additional normalization or segmentation backend implementations beyond the
  listed built-ins.** The interface and dispatch mechanism are complete; backend
  implementations beyond the five sentence backends, two word backends, and six
  normalization backends listed in §3.6 are deferred.
