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
pdf_extractor.extraction.PyMuPDF   ──► blocks
                                  │
                                  ▼ BranchOutput[]
quality_control.run_quality_control(branches, document_id, config)
        │
        ├── stage 1: Artifact Generator      (canonicalise per-branch payload + SHA-256 ID)
        ├── stage 2: Rater                   (per-branch QualityReport via LocalQCReport)
        ├── stage 3: IAA Calculator          (inter-rater agreement)
        ├── stage 4: Adjudicator             (pick the primary branch)
        └── stage 5: Reconciler              (build UnifiedRecord)
                                  │
                                  ▼
                         QCContext (with .unified)
                                  │
                                  ▼
pipeline.pdf_processor.process_pdf  (uses unified.content["exact_text"])
```

---

## Purpose

The `quality_control` package sits between extractor branches and
downstream reconciliation. For each document it:

1. Evaluates every branch against a shared quality contract.
2. Compares branches and computes inter-rater agreement metrics.
3. Selects a preferred result when the branches disagree.
4. Builds the unified record consumed by the rest of the system.

It supports two modes:

1. **Generic branch-adjudication pipeline** that can be reused outside
   PDF extraction. Pass any callables that satisfy the four stage
   signatures (`rater_fn`, `iaa_fn`, `adjudicator_fn`,
   `reconciler_fn`) to adjudicate between agents, LLM outputs, or any
   set of branch outputs.
2. **PDF-specific wrapper** that plugs into the current
   `pdf_extractor` workflow.

---

## Public API

```python
from quality_control import run_pipeline, run_quality_control
from quality_control import QCContext, BranchOutput
```

| Entry point | Use when |
| ----------- | -------- |
| `run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config) -> QCContext` | You want to inject custom stage implementations (any domain). |
| `run_quality_control(branches, document_id, config) -> QCContext` | You want the current PDF-extraction workflow with the built-in stages. |

---

## Files

### `quality_control.py`

Pipeline orchestrator.

- `run_pipeline(...)` — generic four-stage flow with injected rater,
  IAA, adjudicator, and reconciler callables. Domain-agnostic.
- `run_quality_control(...)` — wires the PDF-specific branch flow into
  `run_pipeline`. Tracks the three-tier metrics hierarchy on
  `ctx.metrics_hierarchy`:
  - **Tier 1** — `LocalQCReport` heuristics (always run).
  - **Tier 2** — exact-match search for borderline branches.
  - **Tier 3** — semantic search scaffold; not currently part of
    final adjudication.

### `models.py`

Shared dataclasses; the QC pipeline communicates through a single
`QCContext` instance that is mutated in place rather than passed by
value, so the full pipeline state is inspectable at any point.

| Class | Role |
| ----- | ---- |
| `BranchOutput` | One branch's payload, extractor name, branch index, status. |
| `QualityMetrics` / `QualityReport` | Per-branch quality-check contract. Concrete `LocalQCReport` lives in `local_metrics.py`. |
| `LocalQCMetricRecord` | One row per Tier 1 metric. |
| `InterRaterMetrics` / `InterRaterReport` | Inter-rater agreement contract. |
| `AdjudicationRules` / `AdjudicationDecision` | Branch-selection logic. |
| `UnifiedRecord` | Final reconciled output (carries `document_id` and `content["exact_text"]`). |
| `QCContext` | Full run state (branches, artifacts, observations, decision, unified, metrics_hierarchy). |

### `local_metrics.py` — `LocalQCReport`

Concrete Tier 1 quality checks. Reads thresholds from
`config["quality_control"]["local_metrics"]` and produces one
`LocalQCMetricRecord` per metric.

| # | Metric | Threshold key |
| - | ------ | ------------- |
| 1 | `min_chars_per_page` | `min_chars_per_page` |
| 2 | `grobid_vs_native_length_ratio` | `grobid_vs_native_ratio_threshold` |
| 3 | `long_sentence_fraction` | `long_sentence_word_threshold`, `long_sentence_max_fraction` |
| 4 | `section_coverage` | `expected_sections` |
| 5 | `caption_table_figure_coverage` | `caption_table_figure_check_enabled` |
| 6 | `coordinate_availability` | `coordinate_coverage_threshold` |
| 7 | `references_in_body` | `references_in_body_threshold` |
| 8 | `weird_char_ratio` | `weird_char_ratio_threshold` |

### `artifact_generator.py`

Sole producer of canonical artifacts for the two extractor branches.
Always runs in memory; on-disk export is opt-in via
`quality_control.artifact_generator.export_to_disk`.

- `canonicalize_grobid_xml(tei_xml_str)` — deterministic UTF-8 string
  via `xml.etree.ElementTree`. Attributes are sorted by key; no
  timestamps or random IDs are emitted.
- `canonicalize_pymupdf_json(payload)` — deterministic UTF-8 JSON
  string from a PyMuPDF dict/list payload (sorted keys, fixed
  separators).
- `build_canonical_artifacts(branches)` — returns `{extractor_name:
  {id, format, content}}` with `id = sha256(content)`.
- `export_canonical_artifacts(artifacts, output_dir)` — opt-in disk
  writer.

### `rater.py`

Generates one `Observation_Object` per extractor (extractor name,
document ID, placeholder attributes, status, provenance references).
Does not produce canonical artifacts and does not call any
`artifact_generator` functions.

### `iaa_calculator.py`

Evaluates observation objects against configured thresholds and
returns an `Investigator_Object` representing branch agreement and
artifact references. Does **not** make final accept/reject/reconcile
decisions.

### `adjudicator.py`

Evaluates branch quality and chooses the preferred extractor. Current
implementation uses placeholder logic; future versions will support
configurable per-block / per-page criteria. Passes the
`AdjudicationDecision` to the reconciler.

### `reconciler.py`

Sole producer of `UnifiedRecord`, the source of truth for all
downstream consumers (PDF reader highlighting, LLM
retrieval/QA, TEI XML export, W3C Web Annotation JSON-LD export).
Receives the adjudication decision and reconciles outputs from both
extractors. Falls back to a structural placeholder when no decision
is available, so downstream interfaces remain stable.

---

## Inputs and outputs

- **Input:**
  - `branches: list[BranchOutput]` — typically a GROBID `tei_xml`
    branch and a PyMuPDF `blocks` branch, both for the same PDF.
  - `document_id: str` — used to namespace artifacts and the
    `UnifiedRecord`.
  - `config: dict` — the full project config; the package reads only
    the `quality_control` section.
- **Output:** `QCContext` with `ctx.unified` populated. Downstream
  code reads `ctx.unified.document_id` and
  `ctx.unified.content["exact_text"]`.

---

## Configuration surface

Defaults live in
[`utils/config_utils._QC_DEFAULTS`](../utils/README.md) and are
deep-merged with user values from `config.yaml`. The key sub-sections:

| Sub-section | Purpose |
| ----------- | ------- |
| `discard_failed_branches` | Drop branches whose status is `"failed"` instead of carrying them through. |
| `status_field_location` | Where to surface QC status (`"both"` / `"unified"` / `"branch"`). |
| `grobid` | GROBID server URL + extraction options used by `pdf_extractor.extraction.GROBID`. |
| `local_metrics` | Tier 1 thresholds (see table above). |
| `semantic_qc` | Tier 3 scaffold. `enabled: false` keeps all heavy deps unimported. |
| `artifact_generator` | `export_to_disk` toggle and `output_dir`. |
| `rater` | Future per-branch attribute config (currently `attributes: []`). |
| `iaa_calculator` | Future agreement thresholds and metrics list. |
| `adjudicator` | `strategy: "placeholder"` until real strategies are added. |
| `reconciler` | Optional TEI / W3C annotation exports. |

For the full YAML schema see [../config/README.md](../config/README.md).

---

## Caveats

- Tier 3 semantic QC is **scaffolded only**. The Adjudicator does not
  consume Tier 3 output today.
- The Adjudicator currently uses placeholder logic — configurable
  per-block / per-page strategies are planned.
- Multi-agentic adjudication beyond the GROBID/PyMuPDF branch pair is
  planned (see `pdf_extractor/next steps.txt`).
- `run_quality_control` produces a structural-placeholder
  `UnifiedRecord` when no adjudication decision is available, so
  downstream interfaces remain stable even when the QC pipeline is
  effectively a passthrough.

---

## Related

- Producers of QC branches: [../pdf_extractor/extraction/README.md](../pdf_extractor/extraction/README.md)
- Downstream consumer of `UnifiedRecord`: [../pipeline/README.md](../pipeline/README.md)
- Configuration loader and defaults: [../utils/README.md](../utils/README.md)
- Config schema reference: [../config/README.md](../config/README.md)
- Test coverage: [../tests/pdf_extractor/README.md](../tests/pdf_extractor/README.md)
- Root overview: [../README.md](../README.md)
