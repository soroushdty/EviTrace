# `tests/pdf_extractor/` — PDF Extractor Tests

Pytest tests for the [`pdf_extractor`](../../pdf_extractor/README.md)
package — extraction backends, scan detector, annotation layer, and
text/embedding utilities.

Run from the repo root:

```bash
python -m pytest -q                                                    # default: skip slow tests
python -m pytest -q -m slow                                            # only slow tests
python -m pytest -q -m ""                                              # everything
python -m pytest -q tests/pdf_extractor/test_text_utils.py            # single file
```

The `slow` marker is registered in `pyproject.toml` and applied to
extraction-tier and embedding tests via
`pytestmark = pytest.mark.slow`.

---

## What is covered

| File | Module under test |
| ---- | ----------------- |
| `test_text_extractor_orchestrator.py` | Scan-detector routing in `pipeline/extraction_pipeline.build_qc_bundle` |
| `test_text_extractor_schemas.py` | `pdf_extractor.extraction.schemas` — `BlockDict`, `validate_blocks`, factory helpers |
| `test_pdfplumber_backend.py` | `pdf_extractor.extraction.pdfplumber` (slow) |
| `test_pymupdf_backend.py` | `pdf_extractor.extraction.PyMuPDF` built-in OCR (slow) |
| `test_pymupdf_schema.py` | PyMuPDF-branch `BlockDict` shape and schema conformance |
| `test_paddleocr_backend.py` | `pdf_extractor.extraction.PaddleOCR` (slow) |
| `test_scan_detector.py` | `pdf_extractor.extraction.scan_detector.classify_page` — five-stage logic |
| `test_scan_detector_routing.py` | End-to-end routing decisions from `build_qc_bundle` |
| `test_w3c_annotation.py` | `artifact_generation.w3c_annotation.project` and `generate_w3c_jsonld` |
| `test_text_utils.py` | `pdf_extractor.utils.text_utils` — `normalise_ws`, `normalise_full`, `exact_match_search`, `semantic_search` |
| `test_embedding_utils.py` | `pdf_extractor.utils.embedding_utils` — `load_embedding_model`, `embed_query`, `l2_normalise`, `build_faiss_index`, `build_sentence_store` |
| `test_layout_utils.py` | `pdf_extractor.layout_utils` — `detect_section_heading`, `location_cross_check` (top-level module, not in `pdf_extractor/utils/`) |
| `test_parser_pipeline.py` | End-to-end parser pipeline |
| `test_quality_control_artifact_generator.py` | Canonicalisation + artifact IDs (legacy location; QC tests now also in `tests/quality_control/`) |

---

## Conventions

- Test names follow `test_<module-or-feature>_<aspect>.py`.
- Property-based tests use [Hypothesis](https://hypothesis.readthedocs.io/).
- Slow tests carry `pytestmark = pytest.mark.slow` so they are skipped
  by default.
- Tests are import-safe: heavy optional dependencies
  (`sentence-transformers`, `faiss`, `torch`, `pdf2image`, `paddleocr`)
  are mocked rather than required.
- `fitz` (PyMuPDF) document/page objects are mocked with `MagicMock`.
- Never call real GROBID, OpenAI, or PaddleOCR in unit tests.

---

## Related

- Test suite root: [../README.md](../README.md)
- Module under test (parser): [../../pdf_extractor/README.md](../../pdf_extractor/README.md)
- Root overview: [../../README.md](../../README.md)
