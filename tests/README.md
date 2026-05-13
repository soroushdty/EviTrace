# `tests/` — Test Suite

Pytest test suite for EviTrace. All tests are runnable from the repo
root with:

```bash
python -m pytest -q
```

pytest is configured in `pyproject.toml` at the repo root:

```toml
[tool.pytest.ini_options]
pythonpath = "."
testpaths = ["tests"]
addopts = "--import-mode=importlib -m \"not slow\""
markers = [
    "slow: marks tests as slow (deselected by default; run with -m slow or -m \"\")",
]
```

Always run from the **repo root**. `testpaths = ["tests"]` means pytest
only collects from `tests/`.

Slow tests are deselected by default. To include them:

```bash
python -m pytest -q -m slow      # only slow tests
python -m pytest -q -m ""        # everything
```

---

## Layout

```text
tests/
├── agents/
│   └── openai/                  # api_client (async + cache key), prompts (builders + PBT)
├── pdf_extractor/               # extraction backends, scan detector, annotation, text/embedding utils
├── pipeline/                    # evidence index, manifest I/O, validator loc checks,
│                                # extraction map grouping, extraction report QC,
│                                # orchestrator concurrency, pdf_processor helpers
├── quality_control/             # QC pipeline, models, local metrics, rater, IAA, adjudicator,
│                                # reconciler, concern strategies, Validator, StructureSchemaValidator,
│                                # validate_context, domain agnosticism
├── utils/                       # config_utils, logging_utils, source resolution,
│                                # text_processor, sentence_processor
├── test_dependency_directions.py          # cross-package import enforcement (AST-based)
├── test_migration_artifact_scrub_bug_condition.py
└── test_migration_artifact_scrub_preservation.py
```

All test files follow the naming convention:

```
test_<module-or-feature>_<aspect>.py
```

---

## Coverage at a glance

| Area | Test directory | Key files |
| ---- | -------------- | --------- |
| `agents/openai/api_client` | `tests/agents/openai/` | `test_api_client_async.py`, `test_api_client_cache_key.py` |
| `agents/openai/prompts` | `tests/agents/openai/` | `test_prompts_builders.py`, `test_prompts_pbt.py` |
| `pdf_extractor/extraction/` | `tests/pdf_extractor/` | `test_text_extractor_orchestrator.py`, `test_text_extractor_schemas.py`, `test_pdfplumber_backend.py`, `test_pymupdf_backend.py`, `test_pymupdf_schema.py`, `test_paddleocr_backend.py` |
| `pdf_extractor/extraction/scan_detector` | `tests/pdf_extractor/` | `test_scan_detector.py`, `test_scan_detector_routing.py` |
| `artifact_generation/w3c_annotation` | `tests/pdf_extractor/` | `test_w3c_annotation.py` |
| `pdf_extractor/utils/` | `tests/pdf_extractor/` | `test_text_utils.py`, `test_embedding_utils.py` |
| `pdf_extractor/layout_utils` | `tests/pdf_extractor/` | `test_layout_utils.py` |
| `pdf_extractor/` (pipeline) | `tests/pdf_extractor/` | `test_parser_pipeline.py` |
| `quality_control/` | `tests/quality_control/` | `test_quality_control_pipeline.py`, `test_qc_models.py`, `test_quality_control_local_metrics.py`, `test_quality_control_rater.py`, `test_quality_control_iaa_calculator.py`, `test_quality_control_adjudicator.py`, `test_quality_control_reconciler.py` |
| `quality_control/concerns/` | `tests/quality_control/` | `test_concern_strategies.py` |
| `quality_control/validator` | `tests/quality_control/` | `test_validator_base.py`, `test_validator_properties.py` |
| `quality_control/structure_validator` | `tests/quality_control/` | `test_structure_validator.py` |
| `quality_control/validate_context` | `tests/quality_control/` | (covered in `test_quality_control_pipeline.py`) |
| `quality_control/` (domain agnosticism) | `tests/quality_control/` | `test_domain_agnosticism.py` |
| `quality_control/` (unified record layers) | `tests/quality_control/` | `test_unified_record_layers.py` |
| `pipeline/evidence_index` | `tests/pipeline/` | `test_pipeline_evidence_index.py` |
| `pipeline/manifest` | `tests/pipeline/` | `test_manifest_io.py` |
| `pipeline/validator` (loc checks) | `tests/pipeline/` | `test_pipeline_validator_loc.py` |
| `pipeline/extraction_map` | `tests/pipeline/` | `test_extraction_map_grouping.py` |
| `pipeline/extraction_report` | `tests/pipeline/` | `test_extraction_report_qc.py` |
| `pipeline/orchestrator` | `tests/pipeline/` | `test_orchestrator_concurrency.py` |
| `pipeline/pdf_processor` | `tests/pipeline/` | `test_pdf_processor_helpers.py` |
| `utils/config_utils` | `tests/utils/` | `test_quality_control_config.py` |
| `utils/logging_utils` | `tests/utils/` | `test_logging_utils.py` |
| `utils/path_utils` | `tests/utils/` | `test_source_resolution.py` |
| `utils/text_processor` | `tests/utils/` | `test_text_processor.py` |
| `utils/` + `pdf_extractor/processing/` | `tests/utils/` | `test_sentence_processor.py` |
| Cross-package imports | `tests/` root | `test_dependency_directions.py` |
| Migration regression | `tests/` root | `test_migration_artifact_scrub_bug_condition.py`, `test_migration_artifact_scrub_preservation.py` |

---

## `conftest.py` files

Two `conftest.py` files ensure `sys.path` is correct:

- `conftest.py` (repo root) — required; ensures `pdf_extractor.*`,
  `utils.*`, `quality_control.*`, `pipeline.*`, and `agents.*` all
  resolve when pytest collects from the root.
- `pdf_extractor/conftest.py` — inserts the project root at the front
  of `sys.path` as a fallback for collection starting inside
  `pdf_extractor/`.

Both must exist. The root-level one takes precedence when running
`python -m pytest` from the repo root.

---

## Dependency direction tests

`tests/test_dependency_directions.py` enforces cross-package import rules
via AST analysis. It checks every `.py` file in each package and fails if
a forbidden import is found.

Enforced rules:

| Source package | Must NOT import |
| -------------- | --------------- |
| `pdf_extractor` | `quality_control` |
| `quality_control` | `agents`, `pipeline`, `pdf_extractor` |
| `agents` | `quality_control`, `pipeline`, `pdf_extractor` |

---

## Migration / Steering-Drift Regression Tests

Files at the root of `tests/` (not in a subdirectory) encode migration
correctness:

- `test_migration_artifact_scrub_bug_condition.py` — encodes the expected
  post-fix state; each sub-check fails on unfixed code and passes after
  the fix.
- `test_migration_artifact_scrub_preservation.py` — encodes existing
  correct behaviour that must not regress.

---

## Conventions

- Test names follow `test_<module-or-feature>_<aspect>.py`.
- Property-based tests use [Hypothesis](https://hypothesis.readthedocs.io/);
  `@given(...)` strategies and `@settings(...)` are imported per file.
  Examples: `test_validator_properties.py`, `test_prompts_pbt.py`.
- Slow tests carry `pytestmark = pytest.mark.slow` at module level so
  they are skipped by default.
- Tests are import-safe: heavy optional dependencies
  (`sentence-transformers`, `faiss`, `torch`, `pdf2image`, `paddleocr`)
  are mocked rather than required.
- Never call real GROBID, OpenAI, or PaddleOCR in unit tests.

---

## Related

- Root overview: [../README.md](../README.md)
- Module under test (parser): [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Module under test (QC): [../quality_control/README.md](../quality_control/README.md)
- Module under test (pipeline): [../pipeline/README.md](../pipeline/README.md)
- Module under test (agents): [../agents/README.md](../agents/README.md)
- Testing conventions: [../.kiro/steering/testing.md](../.kiro/steering/testing.md)
