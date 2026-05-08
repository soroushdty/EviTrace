# `tests/pdf_extractor/` — PDF Extractor and QC Tests

Pytest tests for the [`pdf_extractor`](../../pdf_extractor/README.md)
parser and the [`quality_control`](../../quality_control/README.md)
package.

Run from the repo root:

```bash
python -m pytest -q                     # default: skip slow tests
python -m pytest -q -m slow             # only slow tests
python -m pytest -q -m ""               # everything
python -m pytest -q tests/pdf_extractor/test_text_utils.py    # single file
```

The `slow` marker is registered in
[`pdf_extractor/pyproject.toml`](../../pdf_extractor/README.md) and
applied to extraction-tier and embedding tests via
`pytestmark = pytest.mark.slow`.

---

## What's covered

### PDF extractor

| File | Module under test |
| ---- | ----------------- |
| `test_text_extractor_orchestrator.py` | `pdf_extractor.extraction.extract_pdf` cascade |
| `test_text_extractor_schemas.py` | `pdf_extractor.extraction.schemas` |
| `test_text_extractor_tier1.py` | `pdf_extractor.extraction.pdfplumber` (slow) |
| `test_text_extractor_tier2.py` | `pdf_extractor.extraction.Tesseract` (slow) |
| `test_text_extractor_tier3.py` | `pdf_extractor.extraction.PaddleOCR` (slow) |
| `test_text_extractor_branch2.py` | PyMuPDF-branch behaviour |
| `test_parser_pipeline.py` | End-to-end parser pipeline |
| `test_text_utils.py` | `pdf_extractor.utils.text_utils` |
| `test_embedding_utils.py` | `pdf_extractor.utils.embedding_utils` |
| `test_layout_utils.py` | `pdf_extractor.utils.layout_utils` |
| `test_metrics_hierarchy.py` | `sentence_processor.build_metrics_hierarchy` |
| `test_logging_utils.py` | `utils.logging_utils` |
| `test_source_resolution.py` | `utils.path_utils` source resolution |

### Quality control

| File | Module under test |
| ---- | ----------------- |
| `test_quality_control_pipeline.py` | `quality_control.run_quality_control` orchestration |
| `test_quality_control_config.py` | QC config loading + defaults merge |
| `test_quality_control_local_metrics.py` | Tier 1 `LocalQCReport` heuristics |
| `test_quality_control_artifact_generator.py` | Canonicalisation + artifact IDs |
| `test_quality_control_rater.py` | Per-branch observation builder |
| `test_quality_control_iaa_calculator.py` | Inter-rater agreement scaffold |
| `test_quality_control_adjudicator.py` | Branch adjudication logic |
| `test_quality_control_reconciler.py` | Reconciler / `UnifiedRecord` output |
| `test_qc_models.py` | Shared dataclass models |

### Regressions

| File | Purpose |
| ---- | ------- |
| `test_steering_drift_bug_condition.py` | Reproduces a steering-drift bug condition |
| `test_steering_drift_preservation.py` | Verifies that the regression fix is preserved |

---

## Conventions

- Test names follow `test_<module-or-feature>_<aspect>.py`.
- Property-based tests use [Hypothesis](https://hypothesis.readthedocs.io/);
  `@given(...)` strategies and `@settings(...)` are imported per file.
- Slow tests carry `pytestmark = pytest.mark.slow` so they are skipped
  by default. They run any time a config / extraction-tier change is
  made.
- Tests are import-safe: heavy optional dependencies
  (`sentence-transformers`, `faiss`, `torch`, `pdf2image`, `pytesseract`)
  are mocked rather than required.

---

## Related

- Test suite root: [../README.md](../README.md)
- Module under test (parser): [../../pdf_extractor/README.md](../../pdf_extractor/README.md)
- Module under test (QC): [../../quality_control/README.md](../../quality_control/README.md)
- Root overview: [../../README.md](../../README.md)
