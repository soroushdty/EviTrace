---
description: Directory organization, naming conventions, and import patterns
---

# EviTrace — Structure Steering

## Top-Level Organization

Modules are organized by pipeline stage and responsibility:

```
agents/          # LLM extraction — OpenAI API client, prompt assembly
pdf_extractor/   # PDF ingestion — multi-backend extraction, processing, annotation
pipeline/        # Orchestration — workflow control, manifest, reporting
quality_control/ # QC pipeline — adjudication, reconciliation, metrics
utils/           # Cross-cutting — config loading, path helpers, logging
config/          # Runtime configuration — YAML files
tests/           # Test suite — mirrors production structure
```

Each top-level module is independently consumable; imports flow inward (pipeline depends on pdf_extractor and quality_control, not the reverse).

## Naming Conventions

**Files**: `snake_case.py` for all modules except extractor backends.

**Extractor backends** use PascalCase matching the tool name (`GROBID.py`, `PyMuPDF.py`, `PaddleOCR.py`) — this signals they are wrappers around named external tools.

**Classes**: PascalCase (`QCContext`, `BranchOutput`, `UnifiedRecord`, `TextProcessor`).

**Functions/methods**: `snake_case`. Private helpers prefixed with `_` (`_build_tier1_report`, `_adjudicate_concern`).

**Tests**: `test_<module>.py` per component, placed in `tests/<component>/`.

**Config keys**: `snake_case` with nested YAML structure mirroring the module hierarchy.

## Import Patterns

**Within a package**: relative imports (`from .models import UnifiedRecord`, `from .extraction_map import X`).

**Across packages**: absolute imports (`from pdf_extractor.extraction.GROBID import extract_with_grobid`, `from quality_control import QCContext`).

**Utils**: always imported absolutely regardless of calling location.

**Optional/lazy dependencies**: imported inside the function or method that uses them, wrapped in `try/except ImportError` with a `pip install` hint. Never at module level.

## Key Invariants

These constraints are enforced at the architecture level and must not be violated:

1. `run_pipeline` is domain-agnostic — no PDF-specific symbols may appear in its function body.
2. `reconciler.py` and `adjudicator.py` contain no hardcoded extractor names in control flow.
3. All sentence segmentation goes through `TextProcessor.tokenize_sentences()` — no regex sentence splitters in production code paths.

## Spec & Steering Paths

Active feature specifications live in `.kiro/specs/<feature-name>/`. Steering files are in `.kiro/steering/`. Use `/kiro-spec-status <feature>` to check progress.
