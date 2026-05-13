# `quality_control/` — Branch Adjudication and Reconciliation

Generic quality-control pipeline for adjudicating between multiple
agent or extractor outputs. Ships with a PDF-specific implementation
for the [`pdf_extractor`](../pdf_extractor/README.md) extraction
pipeline, but the core orchestrator is **fully domain-agnostic** and
can be reused for LLM attribute extraction, multi-agent workflows, or
any other branched-output use case.

---

## Where it fits

```text
pdf_extractor.extraction.GROBID    ──► tei_xml
pdf_extractor.extraction.pdfplumber ──► blocks
                                   │
                                   ▼ Candidate[]
quality_control.run_quality_control(branches, document_id, config)
        │
        ├── stage 1: Rater          (per-branch ExtractionCoverageReport via heuristics)
        ├── stage 2: IAA Calculator (inter-rater agreement)
        ├── stage 3: Adjudicator    (pick the primary branch via concern strategies)
        └── stage 4: Reconciler     (build UnifiedRecord with semantic/structural/alignment layers)
                                   │
                                   ▼
                          QCBundle (with .unified)
                                   │
                                   ▼
pipeline.extraction_pipeline.build_qc_bundle  (stores exact_text + W3C annotations)
```

---

## Purpose

The `quality_control` package sits between extractor branches and
downstream reconciliation. For each document it:

1. Evaluates every branch against a shared quality contract (extraction
   coverage heuristics, source-text presence verification, semantic
   source verification scaffold).
2. Computes inter-rater agreement metrics.
3. Selects a preferred result via injectable concern strategies.
4. Builds the `UnifiedRecord` consumed by the rest of the system.

It supports two modes:

1. **Generic branch-adjudication pipeline** — pass any callables that
   satisfy the four stage signatures (`rater_fn`, `iaa_fn`,
   `adjudicator_fn`, `reconciler_fn`) to `run_pipeline`.
2. **PDF-specific wrapper** — `run_quality_control` wires the current
   PDF-extraction workflow into `run_pipeline`.

---

## Public API

```python
from quality_control import run_pipeline, run_quality_control
from quality_control import QCBundle, Candidate
from quality_control import Validator, ValidationResult
from quality_control import StructureSchemaValidator, StructureSchemaLoadError
from quality_control import validate_qc_context_input, ValidationError
from quality_control import ExtractionCoverageReport, ExtractionCoverageMetricRecord
from quality_control import VerificationResult
from quality_control.checks import (
    SourceTextPresenceCheck,
    SemanticSourceVerificationCheck,
    ExtractorAgreementCheck,
    build_task_quality_scaffold,
)
from quality_control.builtin_impls import QualityReport, InterRaterReport, AdjudicationDecision
```

| Entry point | Use when |
| ----------- | -------- |
| `run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config) -> QCBundle` | You want to inject custom stage implementations (any domain). |
| `run_quality_control(branches, document_id, config, *, exact_match_fn=None, semantic_search_fn=None) -> QCBundle` | You want the current PDF-extraction workflow with the built-in stages. |

---

## Files

### `quality_control.py`

Pipeline orchestrator.

- `run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config) -> QCBundle`
  — generic four-stage flow with injected stage callables. Domain-agnostic.
  Raises `TypeError` if `branches` is not a list.
- `run_quality_control(branches, document_id, config, *, exact_match_fn=None, semantic_search_fn=None) -> QCBundle`
  — wires the PDF-specific branch flow into `run_pipeline`. Tracks the
  metrics hierarchy on `ctx.metrics_hierarchy`:
  - **`"extraction_coverage"`** — `ExtractionCoverageReport` heuristics (always run).
  - **`"source_text_verification"`** — `SourceTextPresenceCheck` results; requires `exact_match_fn` to be provided; bypassed (passing sentinel) when `source_text_verification.enabled` is `false`.
  - **`"semantic_verification"`** — `SemanticSourceVerificationCheck` results and optional `"extractor_agreement"` sub-dict; requires `semantic_search_fn` to be provided; bypassed when `semantic_verification.enabled` is `false`.

### `models.py`

Shared dataclasses. **Always import from here** — never from individual QC
submodules.

| Class | Role |
| ----- | ---- |
| `Candidate` | One branch's payload, source name, branch index, status. `.extractor` and `.agent` are read-only aliases for `source`. |
| `QualityMetrics` | ABC — override `passes_check()`. |
| `InterRaterMetrics` | ABC — override `compute(reports)`. |
| `AdjudicationRules` | ABC — override `adjudicate(reports, metrics)`. `primary_agent` aliases `primary_extractor`. |
| `SemanticLayer` | `metadata`, `sections`, `paragraphs`, `sentences`, `references`. |
| `StructuralLayer` | `pages`, `blocks`, `tables`, `figures`. |
| `AlignmentRecord` | `source`, `ocr_derived`, `agreement`, `edit_distance`, `preferred_reading`, `confidence`. |
| `DocumentAlignment` | `paragraph_to_blocks`, `sentence_to_char_range`, `section_header_to_block`, `reconciliation_flags`. |
| `UnifiedRecord` | Final output: `document_id`, `content`, `semantic`, `structural`, `alignment`. |
| `ExtractionCoverageMetricRecord` | `metric_name`, `computed_value`, `threshold`, `triggered`. Holds a single heuristic metric result produced by `ExtractionCoverageReport`. |
| `VerificationResult` | `check_name`, `status`, `score`, `evidence`, `details`. Stable result type produced by all QC check classes. `score` is constrained to `[0.0, 1.0]`; valid `status` values are `"verified"`, `"candidate_match"`, `"no_match"`, `"skipped"`, `"unavailable"`. |
| `QCBundle` | Full run state: `branches`, `reports`, `iaa_metrics`, `decision`, `unified`, `metrics_hierarchy`. |

### `local_metrics.py` — `ExtractionCoverageReport`

Concrete heuristic quality checks. Reads thresholds from
`config["quality_control"]["local_metrics"]` and produces one
`ExtractionCoverageMetricRecord` per metric when `passes_check()` is called.

| # | Metric name | Threshold key |
| - | ----------- | ------------- |
| 1 | `min_chars_per_page` | `min_chars_per_page` |
| 2 | `extraction_coverage_ratio` | `extraction_coverage_ratio_threshold` |
| 3 | `long_sentence_fraction` | `long_sentence_word_threshold`, `long_sentence_max_fraction` |
| 4 | `section_coverage` | `expected_sections` |
| 5 | `caption_table_figure_coverage` | `caption_table_figure_check_enabled` |
| 6 | `coordinate_availability` | `coordinate_coverage_threshold` |
| 7 | `references_in_body` | `references_in_body_threshold` |
| 8 | `weird_char_ratio` | `weird_char_ratio_threshold` |

### `checks/`

QC check classes that consume injected matcher dependencies. All modules in
this sub-package are subject to hard constraints: no import of `TextProcessor`
or from `utils.text_processor`; no import from the `text_processing` package;
no top-level import of `faiss`, `torch`, `sentence_transformers`, `spacy`,
`scispacy`, `stanza`, or `wtpsplit`; no inline matching or embedding logic.

Exported from `quality_control.checks`:

| Class / function | Role |
| ---------------- | ---- |
| `SourceTextPresenceCheck` | Verifies source-text presence via an injected lexical matcher. `check_name = "source_text_presence"`. Constructor accepts `matcher: Callable`. `run(needle, full_text, page_texts, blocks) -> VerificationResult`. Returns `status="verified"` when the matcher finds a match, `status="no_match"` otherwise. |
| `SemanticSourceVerificationCheck` | Verifies source text semantically via an injected semantic-search dependency. `check_name = "semantic_source_verification"`. Constructor accepts `matcher: Callable` and `on_index_unavailable: str` (`"skip"` \| `"fail"` \| `"degrade"`). `run(query, sentence_store, embed_fn, threshold, page_texts) -> VerificationResult`. Returns `status="candidate_match"` when a candidate meets the threshold, `status="no_match"` otherwise, or `status="unavailable"` when the sentence store is absent and mode is `"skip"`. |
| `ExtractorAgreementCheck` | **Optional.** Compares two extractor branch payloads and emits an agreement report. Only runs when `quality_control.semantic_verification.extractor_agreement.enabled` is `true`. Constructor accepts `exact_matcher: Callable` and optional `semantic_matcher: Callable \| None`. `run(primary_blocks, candidate_blocks, config) -> dict`. Result is stored in `ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]` and does not influence adjudication or reconciliation. |
| `build_task_quality_scaffold` | Returns a JSON-serializable scaffold dict with placeholder entries for task-quality metrics (`field_recall`, `critical_field_recall`, `evidence_validity`, `evidence_compactness`, `cost_reduction`, `manual_qc_rate`, `interobserver_agreement`, `pipeline_agreement`). Makes no HTTP requests and calls no LLM API. When included in per-PDF output, stored under key `"task_quality_scaffold"`. |

### `rater.py`

`observe(candidate, config) -> QualityReport` — generates one
`QualityReport` per candidate (source, index, status=None). Does not
produce canonical artifacts.

### `iaa_calculator.py`

`investigate(primary_observation, secondary_observation, primary_artifact, secondary_artifact, config) -> dict`
— evaluates observation objects against configured thresholds and returns
an `Investigator_Object` representing branch agreement and artifact
references. Does **not** make final accept/reject/reconcile decisions.

### `adjudicator.py`

`adjudicate(alignment_map, config, *, text_fidelity_strategy=None, section_strategy=None, table_figure_strategy=None) -> dict`
— delegates adjudication for each concern type to the corresponding
injectable strategy. Returns a `decisions` dict with zero or more of:
`"text_fidelity"`, `"section_verification"`, `"table_figure"`.

### `reconciler.py`

`reconcile(primary_artifact, secondary_artifact, ..., *, text_fidelity_strategy=None, section_strategy=None, table_figure_strategy=None, text_processor=None) -> UnifiedRecord`
— sole producer of `UnifiedRecord`. Receives adjudication decisions and
reconciles outputs from both extractor branches using injectable concern
strategies to produce a fully-populated `UnifiedRecord` with semantic,
structural, and alignment layers. Falls back to a structural placeholder
when no decision is available.

### `validator.py`

Generic, injectable validation engine.

- `ValidationResult` — frozen dataclass: `is_valid`, `errors`, `validated_object`.
- `Validator(serializer, schema)` — accepts a serializer callable and a
  JSON-Schema dict (Draft 7). `validate(obj) -> ValidationResult` runs
  `Draft7Validator` and collects all violations.

### `structure_validator.py`

Sole reader of `configs/structure_schema.json`.

- `StructureSchemaLoadError` — raised when the schema file is missing or
  contains invalid JSON.
- `StructureSchemaValidator(schema_path=None)` — loads the schema once at
  construction time. Five typed validation methods:
  - `validate_candidate(candidate, serializer) -> ValidationResult`
  - `validate_qc_bundle(bundle, serializer) -> ValidationResult`
  - `validate_pdf_processor_output(fields, serializer) -> ValidationResult`
  - `validate_extraction_map(extraction_map, serializer) -> ValidationResult`
  - `validate_chunk_output(chunk_output, serializer) -> ValidationResult`

### `validate_context.py`

QC-to-LLM handoff guard.

- `ValidationError` — raised when `validate_qc_context_input` detects a
  problem. Has an `errors: list[str]` attribute.
- `validate_qc_context_input(ctx)` — performs six sequential checks on a
  `QCBundle` before field extraction begins:
  1. `ctx` is a `QCBundle` instance.
  2. `ctx.unified` is not `None`.
  3. `ctx.unified.document_id` is a non-empty `str`.
  4. `ctx.unified.content` is a `dict`.
  5. `ctx.unified.content['exact_text']` is a non-empty `str`.
  6. Structural schema check via `_structure_validator.validate_qc_bundle`.

### `builtin_impls/`

Concrete default implementations of the three ABCs, exported from
`quality_control.builtin_impls`:

- `QualityReport` — default per-branch quality report (unconditional pass).
- `InterRaterReport` — default inter-rater agreement report (pairwise pass/fail).
- `AdjudicationDecision` — default adjudication decision (majority-vote election).

### `concerns/`

Injectable strategy objects, exported from `quality_control.concerns`:

- `TextFidelityConcern` + `DEFAULT_TEXT_FIDELITY` — paragraph-level text
  fidelity via normalized Levenshtein distance. `reconcile(primary, reference, text_processor)`.
  `DEFAULT_TEXT_FIDELITY` uses `source_label="pdfplumber"` (reference is preferred reading).
- `SectionVerificationConcern` + `DEFAULT_SECTION_VERIFICATION` — section
  heading verification via font-size tolerance. `reconcile(primary_section, reference_block, text_processor)`.
- `TableFigureMergeConcern` + `DEFAULT_TABLE_FIGURE_MERGE` — table/figure
  merge decisions. `merge(primary_block, secondary_block)`. Raises
  `MissingContributionError` when one engine has no contribution.

---

## Inputs and outputs

- **Input:**
  - `branches: list[Candidate]` — typically a GROBID `tei_xml` branch and
    a pdfplumber `blocks` branch (native path), or PaddleOCR + PyMuPDF
    branches (scanned path).
  - `document_id: str` — used to namespace the `UnifiedRecord`.
  - `config: dict` — the full project config; the package reads only the
    `quality_control` and `text_processor` sections.
- **Output:** `QCBundle` with `ctx.unified` populated. Downstream code reads
  `ctx.unified.document_id` and `ctx.unified.content["exact_text"]`.

---

## Metrics hierarchy

After `run_quality_control` completes, `ctx.metrics_hierarchy` contains
exactly three top-level keys:

| Key | Value type | Populated by |
| --- | ---------- | ------------ |
| `"extraction_coverage"` | `list[ExtractionCoverageMetricRecord]` | `ExtractionCoverageReport` — always runs |
| `"source_text_verification"` | `list[VerificationResult]` | `SourceTextPresenceCheck` — bypassed (passing sentinel) when `source_text_verification.enabled` is `false` |
| `"semantic_verification"` | `dict` containing `VerificationResult` instances and optionally `"extractor_agreement"` | `SemanticSourceVerificationCheck` + optional `ExtractorAgreementCheck` — bypassed when `semantic_verification.enabled` is `false` |

---

## Configuration surface

Defaults live in
[`utils/config_utils._QC_DEFAULTS`](../utils/README.md) and are
deep-merged with user values from `configs/config.yaml`.

| Sub-section | Purpose |
| ----------- | ------- |
| `discard_failed_branches` | Drop branches whose status is `"failed"` instead of carrying them through. |
| `status_field_location` | Where to surface QC status (`"both"` / `"unified"` / `"branch"`). |
| `grobid` | GROBID server URL + extraction options. |
| `grobid_integration` | `failure_behavior` (`"manifest_fail"` \| `"fallback"`), `crop_figures`, `crop_tables`. |
| `scan_detection` | `text_density_threshold`, `alpha_ratio_threshold`, `image_dominance_threshold`. |
| `ocr` | `rasterization_dpi` for PaddleOCR page rasterization. |
| `local_metrics` | Heuristic thresholds (see table above). Key `extraction_coverage_ratio_threshold` controls the extraction coverage ratio check. |
| `source_text_verification` | `enabled` (default `true`). When `false`, the source-text check is bypassed and a passing sentinel is recorded. |
| `semantic_verification` | `enabled` (default `false`), `similarity_threshold` (default `0.85`), `max_sentences` (default `10000`), `model_name` (default `"BAAI/bge-base-en-v1.5"`), `on_index_unavailable` (`"skip"` \| `"fail"` \| `"degrade"`, default `"skip"`). When `enabled` is `false`, no heavy dependencies (`sentence_transformers`, `faiss`, `torch`) are imported through the QC code path. |
| `semantic_verification.extractor_agreement` | `enabled` (default `false`), `len_filter` (default `40`), `max_examples` (default `10`). `ExtractorAgreementCheck` only runs when `enabled` is `true`. |
| `task_quality_scaffold` | `enabled` (default `true`). Controls whether `build_task_quality_scaffold()` output is included in per-PDF JSON under `"task_quality_scaffold"`. |
| `text_fidelity` | `edit_distance_threshold` for `TextFidelityConcern`. |
| `section_verification` | `font_size_tolerance` for `SectionVerificationConcern`. |
| `artifact_generator` | `export_to_disk` toggle and `output_dir`. |
| `rater` | Future per-branch attribute config (currently `attributes: []`). |
| `iaa_calculator` | Future agreement thresholds and metrics list. |
| `adjudicator` | `strategy: "placeholder"` until real strategies are added. |
| `reconciler` | Optional TEI / W3C annotation exports. |
| `addons` | `grobid_quantities`, `datastet`, `entity_fishing` (all disabled by default). |

For the full YAML schema see [../configs/README.md](../configs/README.md).

---

## Dependency direction rule

`quality_control` must **not** import from `agents`, `pipeline`, or
`pdf_extractor`. This boundary is enforced by
`tests/test_dependency_directions.py`. The `validate_qc_context_input`
function was placed in `quality_control/validate_context.py` specifically
to respect this boundary.

The `quality_control/checks/` sub-package inherits this rule. Check modules
must not import `TextProcessor` by name, must not import from
`utils.text_processor`, and must not import from the `text_processing`
package. This is enforced by `tests/steering/test_qc_textprocessor_separation.py`.

---

## Caveats

- Semantic verification is **report-only**. The adjudicator does not
  consume `semantic_verification` output; it does not influence branch
  selection, `UnifiedRecord` construction, or any field written to
  `outputs/<paper>.extracted.json`.
- `ExtractorAgreementCheck` is **optional** and disabled by default. It
  compares extractor branches for observability purposes only and has no
  effect on adjudication or reconciliation.
- The adjudicator currently uses placeholder logic — configurable
  per-block / per-page strategies are planned.
- `run_quality_control` produces a structural-placeholder `UnifiedRecord`
  when no adjudication decision is available, so downstream interfaces
  remain stable even when the QC pipeline is effectively a passthrough.

---

## Related

- Producers of QC branches: [../pdf_extractor/extraction/README.md](../pdf_extractor/extraction/README.md)
- Single source of truth for extraction flow: [../pipeline/README.md](../pipeline/README.md)
- Downstream consumer of `UnifiedRecord`: [../pipeline/README.md](../pipeline/README.md)
- Configuration loader and defaults: [../utils/README.md](../utils/README.md)
- Config schema reference: [../configs/README.md](../configs/README.md)
- Root overview: [../README.md](../README.md)
