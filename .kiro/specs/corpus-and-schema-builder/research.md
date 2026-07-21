# Research & Design Decisions — corpus-and-schema-builder

## Summary

- **Feature**: `corpus-and-schema-builder`
- **Discovery Scope**: Extension (light discovery) with one greenfield package. The extraction pipeline, PDF backends, text matchers, and config loader already exist; the project/corpus/schema layer above them does not.
- **Key Findings**:
  1. Every path the pipeline uses is a **module-level constant computed at import time** in `src/utils/path_utils.py` (`PDF_DIR`, `OUTPUT_DIR`, `MANIFEST_FILE`, `EXTRACTION_MAP`, `RUN_FOLDER_NAME`). A project-scoped store cannot be delivered by mutating these; it must resolve its own paths and be wired in at the CLI boundary.
  2. `configs/extraction_map.json` is a bare JSON array of 62 seven-key objects with **no version field**, and whole field dicts are serialized verbatim into the cache-stable prompt prefix (`src/agents/openai/prompts.py`). Any new schema model must therefore be able to project back to the exact legacy 7-key shape byte-identically, or the prompt cache breaks.
  3. `src/text_processing/matchers.py::LexicalMatcher.search()` already solves "locate a snippet in canonical text and recover its page span". There is **no `rapidfuzz` and no real Levenshtein** in the repo; `DefaultTextProcessor.compare()` is `difflib.SequenceMatcher.ratio()` over NFKC-normalised text. Reusing these avoids a new dependency.
  4. `openpyxl` and `pandas` are absent from `requirements.txt`, `pyproject.toml`, and the active venv. Excel support must be an optional, lazily imported extra.
  5. `pdfplumber` is a **core** dependency; PyMuPDF (`fitz`) is an optional AGPL extra. Admission validation must therefore not require `fitz`.

## Research Log

### Path and configuration resolution

- **Context**: Requirement 1.3 demands per-project isolation of corpus, schema, evidence, and outputs; Requirement 1.4 demands fallback to installation-level configuration.
- **Sources Consulted**: `src/utils/path_utils.py`, `src/utils/config_utils.py`, `configs/config.yaml`.
- **Findings**:
  - `path_utils` computes `PROJECT_ROOT`, `PDF_DIR`, `OUTPUT_DIR`, `MANIFEST_FILE`, `EXTRACTION_MAP`, and `RUN_FOLDER_NAME` at import time from `_load_local_settings()`, which swallows all exceptions and returns `{}` on failure. `PDF_DIR` uses a naive `BASE_DIR / pdfs_path` join and ignores absolute paths, unlike `load_local_config`, which routes through `resolve_project_path`.
  - `MANIFEST_FILE` is a single repo-root `manifest.json` shared by every run.
  - `_ALL_KNOWN_TOP_LEVEL_KEYS` (`config_utils.py:236-246`) rejects any unregistered top-level YAML key with `ValueError`.
  - Only `load_openai_config` applies an env layer; `load_local_config` and `load_qc_config` have none.
- **Implications**: the new package resolves its own project directory tree and never mutates or relies on `path_utils` globals. A new `projects` top-level key must be registered. The per-project configuration profile is a plain overlay dictionary deep-merged over the installation config, resolved at call time and passed explicitly — consistent with the "no global mutation" steering rule.

### Existing document identity and manifest status

- **Context**: Requirements 2.1, 2.2, 3.5 (content-derived, idempotent identity) and 4.1-4.3 (status vocabulary).
- **Sources Consulted**: `src/pipeline/manifest.py`, `src/pipeline/orchestrator.py`, `src/pipeline/pdf_processor.py`, `.kiro/steering/roadmap.md`.
- **Findings**:
  - `compute_identity()` already produces a SHA-256 of the PDF's **bytes** (`pdf_content_hash`), alongside `config_hash`, `extraction_map_hash`, `model_id`, `schema_version`, `output_path`.
  - `path_utils.list_pdf_files_from_source()` also mints an "id", but it hashes the **absolute path**, not content — it is not a stable document identity.
  - The live manifest vocabulary is `pending`, `complete`, `failed_qc_pipeline`, `failed_validation`, `failed_schema_validation`, `failed_chunks`, `failed_chunk_<n>`. The roadmap explicitly retracts an earlier claim that this vocabulary was wrong: both `failed_chunks` and `failed_chunk_<n>` are genuine and distinct.
  - The manifest is a per-run checkpoint keyed by PDF stem, not a corpus record.
- **Implications**: document identity reuses the same SHA-256-of-bytes rule so a document ID equals the existing `pdf_content_hash` — no second hashing convention enters the repo. The corpus status vocabulary is a **separate, coarser** reviewer-facing vocabulary; the pipeline's manifest vocabulary is left untouched and is mapped into it at the integration boundary. Nothing in this spec renames or removes a manifest status.

### Schema shape and prompt-cache stability

- **Context**: Requirements 5.1, 5.6, 6.6, 7.4.
- **Sources Consulted**: `configs/extraction_map.json`, `src/pipeline/extraction_map.py`, `src/agents/openai/prompts.py`, `src/quality_control/structure_validator.py`.
- **Findings**:
  - The extraction map is a JSON array of 62 objects with exactly `field_index`, `domain_group`, `field_name`, `definition`, `reviewer_question`, `format`, `categories_or_examples`. `domain_group` is a string whose numeric prefix (1-13) drives chunk assignment in `extraction_map.py::_infer_chunk_field_ranges()`.
  - `prompts.py::build_user_message()` and `build_cache_warmup_message()` serialize the whole field dicts with `json.dumps(ordered_fields, indent=2, ensure_ascii=False)` into the cache-stable prefix. `compute_stable_prefix()` guards byte-identity.
  - `manifest.py` and `evidence_index.py` each independently hash the extraction map file; the evidence cache key includes that hash.
  - `StructureSchemaValidator.validate_extraction_map()` already validates the legacy shape against `#/$defs/ExtractionMap`.
- **Implications**: the new field model is a **superset** with a lossless projection back to the legacy 7 keys. The seed schema version must project to a byte-identical rendering of today's `configs/extraction_map.json`, verified by test. Consumption of the new model by `extraction_map.py` and `prompts.py` stays out of boundary; a project's schema version is instead *materialized* to the legacy shape at a project-scoped path, so no prompt-path code changes and no cache invalidation risk is introduced by this spec.

### Fuzzy matching capability already in the repo

- **Context**: Requirements 9.1-9.5.
- **Sources Consulted**: `src/text_processing/matchers.py`, `composite.py`, `normalizers.py`, installed-package probe of the active venv.
- **Findings**:
  - `LexicalMatcher.search(needle, full_text, page_texts, blocks)` is a two-pass normalised-containment locator (whitespace pass scores 1.0, aggressive pass scores 0.9), skips needles under 10 normalised characters, and recovers the original page span with `difflib.SequenceMatcher`. It returns `{found_sentence, page_index, prefix, suffix, block_bbox, span_bboxes, score}` and reports **top-1 only**.
  - `SemanticMatcher` exists but needs FAISS/torch and is disabled by default.
  - `DefaultTextProcessor.compare(a, b)` is `difflib.SequenceMatcher(...).ratio()` on NFKC + whitespace-collapsed text; the config metric name `"levenshtein"` is a misnomer.
  - `rapidfuzz`, `python-Levenshtein`, `pandas`, and `openpyxl` are installed nowhere.
- **Implications**: adopt `LexicalMatcher` for the exact/near-exact path and `DefaultTextProcessor.compare` for the graded fallback score. Because `LexicalMatcher` returns only the best hit, the competing-candidate requirement (9.4) needs a thin occurrence enumeration over `page_texts` in this spec's own matcher, not a change to `text_processing`.

### Table and spreadsheet reading

- **Context**: Requirements 7.1, 7.5, 8.1, 11.4.
- **Sources Consulted**: `src/artifact_generation/csv_exporter.py`, `requirements.txt`, `pyproject.toml`.
- **Findings**: the repo has a CSV *writer* (`csv.DictWriter`) and no CSV *reader* anywhere; there is no generic table abstraction to extend. `PyYAML` and `jsonschema` are declared core dependencies; `openpyxl` is not declared anywhere.
- **Implications**: a small row-reader module is new code. CSV/JSON/YAML use the standard library plus PyYAML (core); Excel uses `openpyxl` imported inside the function body and declared as a new optional extra, matching the existing PyMuPDF/faiss pattern.

### PDF admission validation without PyMuPDF

- **Context**: Requirements 2.3-2.6, 11.4.
- **Sources Consulted**: `src/pdf_extractor/pdf_validator.py`, `src/pdf_extractor/extraction/pdfplumber.py`, `pyproject.toml`.
- **Findings**: `pdf_validator.validate_pdf()` checks the `%PDF-` magic bytes, non-zero size, `fitz.open()` success, and `doc.needs_pass` — but has **no page-count check**, and `fitz` is an optional AGPL extra. `pdfplumber` is a core dependency and exposes `len(pdf.pages)`; it raises on password-protected files.
- **Implications**: admission validation is implemented in this package on top of magic bytes, file size, and `pdfplumber`, so it works in the default permissive install. It duplicates neither `pdf_validator` nor any extraction backend, and adds the page count that Requirement 2.3 needs.

### Dependency-direction enforcement

- **Context**: Constraint from the brief and steering; Requirement 1.3 isolation.
- **Sources Consulted**: `tests/test_dependency_directions.py`, `.kiro/steering/testing.md`.
- **Findings**: rules are a module-level `FORBIDDEN_PAIRS: list[tuple[str, str]]` of `(source_package, forbidden_import_prefix)`, checked by AST over every `.py` file, with an exhaustive test that picks up new tuples automatically.
- **Implications**: adding `("corpus", "quality_control")`, `("corpus", "agents")`, `("corpus", "pipeline")`, `("quality_control", "corpus")`, `("text_processing", "corpus")`, `("pdf_extractor", "corpus")`, and `("agents", "corpus")` is a one-line-per-rule change plus named tests for readable failures.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| File-backed project directory (selected) | One directory per project holding JSON records, an append-only event log, immutable schema-version files, and admitted PDF copies | Matches the repo's config-driven, no-database design and `xtrace-toolkit` R-X-1; trivially inspectable and diffable; atomic writes are easy | Needs explicit locking for concurrent writers; no query language | Immutability of schema versions falls out of the file layout |
| Embedded relational database (SQLite) | Projects, documents, schema versions, evidence in tables | Transactions and queries for free; concurrency handled | Introduces a storage engine the repo does not have; schema migrations become a new concern; contradicts the brief's "file-backed rather than database-backed" | Rejected |
| Extend `manifest.json` in place | Grow the existing per-run checkpoint into a corpus record | No new store | Conflates run checkpointing with corpus identity; the manifest is keyed by PDF stem and reset on staleness; would break resumability semantics | Rejected |
| Two packages (`src/corpus/` + `src/schema/`) | Split corpus and schema into peer packages | Sharper seam between corpus and schema | `schema` is an overloaded name in this repo (`agent_schema.json`, `structure_schema.json`, `StructureSchemaValidator`); the two share the project aggregate, locking, and store primitives | Rejected in favour of one package with sub-packages |

## Design Decisions

### Decision: One `src/corpus/` package with sub-packages, not two peer packages

- **Context**: The spec owns three visibly distinct concerns — corpus, schema, evidence import — but they share a project aggregate root, a directory layout, a locking primitive, and a JSON store.
- **Alternatives Considered**: 1) `src/corpus/` + `src/schema/` peers; 2) one package with `schema/`, `tables/`, `evidence/` sub-packages.
- **Selected Approach**: option 2. `src/corpus/` owns the project aggregate; `corpus.schema`, `corpus.tables`, and `corpus.evidence` are sub-packages with a declared inward dependency direction (`models` → `store`/`locking`/`paths` → services → importers → `cli`).
- **Rationale**: avoids duplicating the store and lock primitives across two packages, avoids the overloaded name `schema` at top level, and keeps the dependency-direction rule set small.
- **Trade-offs**: the package is larger; enforced by the internal direction rule and one-responsibility-per-file structure.
- **Follow-up**: the internal direction is asserted by an AST test alongside the cross-package rules.

### Decision: Document identity is the existing `pdf_content_hash`

- **Context**: Requirements 2.1, 2.2, 3.5; the brief's constraint that document IDs stay content-derived and consistent with the existing manifest hash.
- **Alternatives Considered**: 1) new hash scheme with a prefix and truncation; 2) reuse SHA-256 of file bytes exactly as `manifest._compute_file_sha256` computes it.
- **Selected Approach**: option 2 — the document identifier is the full lowercase hex SHA-256 of the PDF bytes, computed by this package with the same rule, so `document_id == manifest.compute_identity(...).pdf_content_hash`.
- **Rationale**: guarantees idempotent re-admission, keeps one hashing convention in the repo, and lets a later spec join corpus records to manifest entries without a translation table.
- **Trade-offs**: this package re-implements a four-line hashing helper rather than importing `pipeline` (which the dependency direction forbids). A test asserts the two agree on the same bytes.
- **Follow-up**: never truncate the ID in stored records; truncate only in log lines.

### Decision: Corpus status vocabulary is separate from, and mapped onto, the manifest vocabulary

- **Context**: Requirement 4.1 wants uploaded/parsed/extracted/reviewed/failed/flagged; the pipeline writes seven finer-grained manifest statuses.
- **Alternatives Considered**: 1) replace the manifest vocabulary; 2) make corpus status a projection over the manifest; 3) an independent corpus status with an explicit mapping applied at the integration boundary.
- **Selected Approach**: option 3. The corpus record is the authority for reviewer-facing status; the manifest remains the authority for run resumability. A single mapping table converts manifest outcomes into corpus statuses when the pipeline reports.
- **Rationale**: the roadmap explicitly protects the manifest vocabulary, and a projection (option 2) would make corpus status unavailable for states the pipeline never writes, such as `reviewed` and `flagged`.
- **Trade-offs**: two vocabularies must be kept in sync at exactly one place — the mapping table, which is unit-tested for totality against the manifest's status set.
- **Follow-up**: `flagged` is a status but must not erase progress, so the record retains `prior_status` for unflagging.

### Decision: Schema versions are immutable content-addressed files; the seed version is a lossless migration

- **Context**: Requirements 6.1, 6.3, 6.4, 6.6, plus the prompt-cache constraint.
- **Alternatives Considered**: 1) mutable schema file plus a changelog; 2) immutable numbered version files with a content hash in the identifier.
- **Selected Approach**: option 2. `version_id = f"{ordinal:04d}-{sha256(canonical_json)[:12]}"`; files are written once and never rewritten; in-place modification attempts are rejected.
- **Rationale**: immutability makes output pinning (6.3) a lookup rather than a reconstruction, and the content hash makes accidental divergence detectable.
- **Trade-offs**: every edit rewrites the full field list; at 62 fields this is negligible.
- **Follow-up**: the migration must not invent enumerations — `categories_or_examples` is prose containing examples, so it maps to an `examples` attribute and leaves `allowed_values` unset.

### Decision: Adopt `text_processing` for matching; add no fuzzy-matching dependency

- **Context**: Requirements 9.1-9.5.
- **Alternatives Considered**: 1) add `rapidfuzz`; 2) reuse `LexicalMatcher` plus `DefaultTextProcessor.compare`.
- **Selected Approach**: option 2, with a thin occurrence enumerator in this package to surface competing candidates that `LexicalMatcher`'s top-1 contract hides.
- **Rationale**: no new runtime dependency, and the normalisation rules stay identical to those the rest of the pipeline uses, so a snippet that the pipeline can find is a snippet the importer can find.
- **Trade-offs**: `SequenceMatcher` is slower than `rapidfuzz` on long texts; mitigated by restricting the graded pass to hint-selected pages first and by a configurable candidate cap.
- **Follow-up**: if import volumes make this too slow, `rapidfuzz` can be introduced behind the same matcher interface without changing callers.

### Decision: Canonical text is consumed through a provider interface, never re-derived

- **Context**: Requirement 9.1 and 9.7; the boundary rule that parser and canonical-document work is out of scope; the dependency rule that `corpus` must not import `pipeline` or `quality_control`.
- **Alternatives Considered**: 1) import the QC bundle directly; 2) define a `CanonicalTextProvider` protocol with a file-backed default that reads a per-document canonical-text artifact from the project directory.
- **Selected Approach**: option 2. Absence of the artifact is the observable "canonical text unavailable" condition of 9.7.
- **Rationale**: keeps the dependency direction clean and lets a later spec supply a richer provider without touching the importer.
- **Trade-offs**: matching cannot run until the pipeline has persisted canonical text for a document; this is reported, not silently skipped.

### Decision: Held imports are persisted state, not an interactive prompt

- **Context**: Requirements 7.2, 7.3 and the headless constraint.
- **Selected Approach**: an ambiguous import is written as a held-import record listing each unresolved column with candidate attributes; a second command supplies the mapping and completes it.
- **Rationale**: satisfies "present them for human mapping" without any interactive surface, and keeps the operation resumable across processes.
- **Trade-offs**: two commands instead of one for ambiguous sources.

### Decision: Locking by atomic directory creation plus atomic file replacement

- **Context**: Requirement 11.5.
- **Alternatives Considered**: 1) `fcntl` advisory locks; 2) a lock directory created with `os.mkdir` (atomic on POSIX and Windows) guarding writes, with every file write performed as temp-file + `os.replace`.
- **Selected Approach**: option 2, mirroring the atomic write already used by `manifest.save_manifest`.
- **Rationale**: no platform-specific API, matches an existing repo pattern, and stale locks are detectable through a recorded owner PID and timestamp.
- **Trade-offs**: coarse-grained, one writer per project at a time. Acceptable for a single-reviewer headless tool; multi-user access control is explicitly out of scope.

## Risks & Mitigations

- **Prompt-cache invalidation through schema materialization** — the seed schema version must render byte-identically to today's `configs/extraction_map.json`; enforced by a byte-comparison test, and the prompt path is not modified by this spec.
- **Two hashing conventions for the same PDF** — mitigated by reusing the SHA-256-of-bytes rule and testing agreement with `manifest.compute_identity`.
- **Status vocabulary drift between corpus and manifest** — mitigated by one mapping table plus a totality test over the manifest's known status values, including the `failed_chunk_<n>` prefixed form.
- **Optional Excel reader silently unavailable** — mitigated by an explicit rejection message naming the missing component and a test that runs the suite with `openpyxl` mocked absent.
- **Copying admitted PDFs duplicates storage** — accepted deliberately so a project is self-contained and re-admission is idempotent; the copy is keyed by content hash so identical files are stored once per project.
- **Matching cost on large corpora** — mitigated by hint-first search ordering and a configurable candidate cap; the fallback graded pass is bounded to the hinted pages before widening.

## References

- `.kiro/steering/roadmap.md` — dependency order, carve-outs for multiagent R20.6 and R2.3-R2.4, standing product boundaries.
- `.kiro/steering/product.md` — module responsibilities, no-global-mutation rule, lazy heavy-dependency rule.
- `.kiro/steering/testing.md` — test layout, mocking conventions, dependency-direction test structure.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — multiagent R1, R2, R20 source text.
- `.kiro/specs/xtrace-toolkit/` — R-X-1 declarative configuration, R-X-3 per-PDF resumability.
