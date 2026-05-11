# `pdf_extractor/utils/` — Parser Utilities

> **Migration notice (2026-05):** `text_utils.py` and `embedding_utils.py`
> have been deleted and their functionality migrated to the standalone
> `text_processing/` package at the repository root. Import paths:
>
> - `text_processing.matchers.LexicalMatcher` (replaces `exact_match_search`)
> - `text_processing.matchers.SemanticMatcher` (replaces `semantic_search`)
> - `text_processing.embedding.EmbeddingProcessor` (replaces all `embedding_utils` functions)
> - `text_processing.normalizers.WhitespaceNormalizer` (replaces `normalise_ws`)
> - `text_processing.normalizers.FullNormalizer` (replaces `normalise_full`)

This directory now contains only `__init__.py` (empty). Any new parser-specific
utilities should be evaluated for placement in either `text_processing/` (if
reusable across the project) or here (if truly pdf_extractor-scoped only).
