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
        ├── stage 1: Rater          (per-branch LocalQCReport via Tier 1 heuristics)
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

1. Evaluates every branch against a shared quality contract (Tier 1 local
   heuristics, Tier 2 exact-match search, Tier 3 semantic search scaffold).
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
  three-tier metrics hierarchy on `ctx.metrics_hierarchy`:
  - **`"local_metrics"`** — `LocalQCReport` heuristics (always run).
  - **`"exact_match"`** — exact-match search for borderline branches (1–2
    triggered Tier 1 metrics); requires `exact_match_fn` to be provided.
  - **`"semantic_match"`** — semantic search scaffold; not currently part of
    final adjudication; requires `semantic_search_fn` to be provided.

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
| `LocalQCMetricRecord` | `metric_name`, `computed_value`, `threshold`, `triggered`. |
| `QCBundle` | Full run state: `branches`, `reports`, `iaa_metrics`, `decision`, `unified`, `metrics_hierarchy`. |

### `local_metrics.py` — `LocalQCReport`

Concrete Tier 1 quality checks. Reads thresholds from
`config["quality_control"]["local_metrics"]` and produces one
`LocalQCMetricRecord` per metric when `passes_check()` is called.

| # | Metric name | Threshold key |
| - | ----------- | ------------- |
| 1 | `min_chars_per_page` | `min_chars_per_page` |
| 2 | `grobid_vs_native_ratio` | `grobid_vs_native_ratio_threshold` |
| 3 | `long_sentence_fraction` | `long_sentence_word_threshold`, `long_sentence_max_fraction` |
| 4 | `section_coverage` | `expected_sections` |
| 5 | `caption_table_figure_coverage` | `caption_table_figure_check_enabled` |
| 6 | `coordinate_availability` | `coordinate_coverage_threshold` |
| 7 | `references_in_body` | `references_in_body_threshold` |
| 8 | `weird_char_ratio` | `weird_char_ratio_threshold` |

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

### `defaults/`

Concrete default implementations of the three ABCs, exported from
`quality_control.defaults`:

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
| `local_metrics` | Tier 1 thresholds (see table above). |
| `semantic_qc` | Tier 3 scaffold. `enabled: false` keeps all heavy deps unimported. |
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

---

## Caveats

- Tier 3 semantic QC is **scaffolded only**. The adjudicator does not
  consume Tier 3 output today.
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
