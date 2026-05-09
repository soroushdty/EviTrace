---
description: Technology stack, architectural decisions, and coding conventions
---

# EviTrace — Tech Steering

## Language & Runtime

Python 3.10+ — union-type hints (`X | Y`), dataclasses, and `asyncio` throughout.

## Key Dependencies

| Layer | Library | Role |
|---|---|---|
| PDF (native) | PyMuPDF (fitz) | Scan detection, font metadata, cross-validation OCR |
| PDF (structural) | pdfplumber | Ground-truth character-level text blocks, tables |
| PDF (semantic) | GROBID | TEI XML semantic structure (sections, paragraphs, references, IMRaD labels) |
| PDF (OCR) | PaddleOCR | Primary scanned-page backend |
| LLM | OpenAI SDK 1.0+ | Structured extraction with JSON-Schema outputs and prompt caching |
| Config | PyYAML | Single-source YAML config |
| Testing | pytest | Unit and integration tests |

Optional deps (lazy-imported): wtpsplit, stanza, scispaCy, NLTK — never required at import time.

## Architectural Patterns

### Multi-Backend Extraction (Complementary, Not Competing)

Extractors have fixed, non-interchangeable roles. They are never a waterfall where one replaces another:
- GROBID → semantic structure
- pdfplumber → structural ground truth
- PyMuPDF → scan detection + font metadata
- PaddleOCR → confirmed scanned pages only

### Async/Await Orchestration

All pipeline coordination is `asyncio`-based. Concurrency is controlled via semaphores — one for PDF-level parallelism, one for global OpenAI API rate limiting.

### Config-Driven Runtime

All behavior flows from `config/config.yaml`. No hardcoded thresholds or model names in business logic. Config is loaded once per entry-point and passed as a `dict` through dependency injection.

### Dataclass Contracts Between Stages

`TypedDict` and `@dataclass` define the schemas passed between pipeline stages (`BranchOutput`, `QCContext`, `UnifiedRecord`). Stages communicate through typed contracts, not shared globals.

### Pluggable Text Processing

Sentence segmentation, word segmentation, and text normalization are independently configurable backends behind `TextProcessor`. Backend selection is via `text_processor.class` in config; custom implementations subclass `SentenceSegment` or `TextProcessor`.

### Concern-Strategy Injection

The QC reconciler and adjudicator are domain-agnostic. All extractor-specific asymmetry (which source is ground truth, which is authoritative) is expressed exclusively in injectable concern strategies. Neither module may hardcode extractor names.

## Testing Conventions

- All NLP model backends are mocked in tests — no model downloads required in CI
- Test files mirror production structure: `tests/<component>/test_<module>.py`
- Integration tests use real data flows; unit tests mock at the boundary
