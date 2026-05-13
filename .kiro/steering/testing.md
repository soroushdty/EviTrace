---
inclusion: always
---

# EviTrace — Testing Conventions

## Running Tests

```bash
# Fast suite (default — slow tests excluded)
python -m pytest -q

# Slow tests only
python -m pytest -q -m slow

# Everything (slow + fast)
python -m pytest -q -m ""
```

pytest is configured in `pyproject.toml` at the repo root:

```toml
[tool.pytest.ini_options]
pythonpath = "src"
testpaths = ["tests"]
addopts = "--import-mode=importlib -m \"not slow\""
markers = [
    "slow: marks tests as slow (deselected by default; run with -m slow or -m \"\")",
]
```

Always run from the **repo root**. `testpaths = ["tests"]` means pytest only collects from `tests/`.

---

## conftest.py Files

Two `conftest.py` files ensure `sys.path` is correct:

- `conftest.py` (repo root) — required; computes `Path(__file__).resolve().parent / "src"` and inserts it at `sys.path[0]` if not already present. This ensures `pdf_extractor.*`, `utils.*`, `quality_control.*`, `pipeline.*`, `agents.*`, and `text_processing.*` all resolve when pytest collects from the root.
- `src/pdf_extractor/conftest.py` — computes `Path(__file__).resolve().parent.parent` (which resolves to `src/`) and inserts it at `sys.path[0]` as a fallback for collection starting inside `src/pdf_extractor/`.

Both must exist. The root-level one takes precedence when running `python -m pytest` from the repo root.

---

## Test Layout

```
tests/
├── src/
│   ├── agents/
│   │   └── openai/              # api_client (async + cache key), prompts builders, prompts PBT
│   ├── pdf_extractor/           # mirrors src/pdf_extractor/
│   │   ├── test_layout_utils.py
│   │   ├── test_paddleocr_backend.py
│   │   ├── test_parser_pipeline.py
│   │   ├── test_pdfplumber_backend.py
│   │   ├── test_pymupdf_backend.py
│   │   ├── test_pymupdf_schema.py
│   │   ├── test_quality_control_artifact_generator.py
│   │   ├── test_scan_detector.py
│   │   ├── test_scan_detector_routing.py
│   │   ├── test_text_extractor_orchestrator.py
│   │   ├── test_text_extractor_schemas.py
│   │   └── test_w3c_annotation.py
│   ├── pipeline/                # mirrors src/pipeline/
│   │   ├── test_extraction_map_grouping.py
│   │   ├── test_extraction_report_qc.py
│   │   ├── test_manifest_io.py
│   │   ├── test_orchestrator_concurrency.py
│   │   ├── test_pdf_processor_helpers.py
│   │   ├── test_pipeline_evidence_index.py
│   │   └── test_pipeline_validator_loc.py
│   ├── quality_control/         # mirrors src/quality_control/
│   │   ├── test_concern_strategies.py
│   │   ├── test_domain_agnosticism.py
│   │   ├── test_qc_checks_extractor_agreement.py
│   │   ├── test_qc_checks_semantic_source.py
│   │   ├── test_qc_checks_source_text.py
│   │   ├── test_qc_checks_task_quality.py
│   │   ├── test_qc_models.py
│   │   ├── test_qc_pipeline_integration.py
│   │   ├── test_qc_verification_result.py
│   │   ├── test_quality_control_adjudicator.py
│   │   ├── test_quality_control_iaa_calculator.py
│   │   ├── test_quality_control_local_metrics.py
│   │   ├── test_quality_control_pipeline.py
│   │   ├── test_quality_control_rater.py
│   │   ├── test_quality_control_reconciler.py
│   │   ├── test_structure_validator.py
│   │   ├── test_unified_record_layers.py
│   │   ├── test_validator_base.py
│   │   └── test_validator_properties.py
│   ├── text_processing/         # mirrors src/text_processing/
│   │   ├── test_base_abc.py     # ABC enforcement + lazy model loading
│   │   ├── test_normalizers.py  # example-based normalizer tests
│   │   ├── test_normalizers_properties.py  # PBT idempotence (Hypothesis)
│   │   ├── test_tokenizers.py   # SimpleWordTokenizer tests
│   │   ├── test_matchers.py     # LexicalMatcher + SemanticMatcher example-based
│   │   ├── test_matchers_properties.py    # PBT for matcher properties
│   │   ├── test_embedding.py    # EmbeddingProcessor tests (mark slow)
│   │   ├── test_embedding_properties.py   # PBT for embedding (mark slow)
│   │   ├── test_import_isolation.py # verify import without heavy deps
│   │   └── test_deleted_paths.py   # verify ModuleNotFoundError for legacy paths
│   └── utils/                   # mirrors src/utils/
│       ├── test_logging_utils.py
│       ├── test_quality_control_config.py
│       ├── test_sentence_processor.py
│       └── test_source_resolution.py
├── steering/                    # cross-cutting structural / separation tests
│   ├── test_qc_textprocessor_separation.py
│   └── test_text_processing_separation.py
├── test_dependency_directions.py   # cross-package import enforcement (AST-based)
├── test_migration_artifact_scrub_bug_condition.py
└── test_migration_artifact_scrub_preservation.py
```

`tests/steering/` holds tests that enforce architectural rules which span multiple packages and don't belong to any single mirrored subdirectory. Files here are collected automatically by pytest because `testpaths = ["tests"]` recurses into all subdirectories. Package-mirroring test directories live under `tests/src/` to reflect the `src/` layout of the source packages.

All test files follow the naming convention:

```
test_<module-or-feature>_<aspect>.py
```

Examples:
- `test_quality_control_rater.py` — rater module
- `test_scan_detector_routing.py` — routing aspect of scan detector
- `test_validator_base.py` — generic Validator engine
- `test_validator_properties.py` — PBT for Validator
- `test_structure_validator.py` — StructureSchemaValidator
- `test_qc_checks_source_text.py` — source text check class
- `test_qc_pipeline_integration.py` — end-to-end QC integration
- `test_dependency_directions.py` — cross-package dependency enforcement

---

## Slow Test Marking

Apply `pytestmark` at module level for extraction-tier and embedding tests:

```python
import pytest
pytestmark = pytest.mark.slow
```

Heavy optional dependencies (`paddleocr`, `faiss`, `torch`, `sentence-transformers`) must be **mocked** in tests — never require them to be installed for the suite to pass.

---

## Property-Based Testing

Use Hypothesis for correctness properties. Apply `@given` and `@settings` directly on test functions:

```python
from hypothesis import given, settings, assume
from hypothesis import strategies as st

@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
@settings(max_examples=100)
def test_some_property(score: float):
    assume(score >= 0.7)
    ...
```

PBT tests live alongside unit tests in their respective subdirectory under `tests/src/`. Examples: `test_validator_properties.py`, `test_prompts_pbt.py`.

---

## Mocking Conventions

Mock heavy dependencies at the module level using `patch.dict("sys.modules", ...)` or `patch.object`:

```python
import sys
from unittest.mock import MagicMock, patch

# Patch a missing optional dependency
with patch.dict(sys.modules, {"paddleocr": None, "faiss": None, "torch": None}):
    import my_module
```

Remove cached modules before re-importing with patched dependencies:

```python
for mod_name in list(sys.modules.keys()):
    if "target_module" in mod_name:
        del sys.modules[mod_name]
```

Use `MagicMock` for fitz (PyMuPDF) document/page objects. Never call real GROBID, OpenAI, or PaddleOCR in unit tests.

---

## Dependency Direction Tests

`tests/test_dependency_directions.py` enforces cross-package import rules via AST analysis. It checks every `.py` file in each package under `src/` and fails if a forbidden import is found.

Enforced rules:

| Source package | Must NOT import |
|---|---|
| `src/pdf_extractor` | `quality_control` |
| `src/quality_control` | `agents`, `pipeline`, `pdf_extractor` |
| `src/agents` | `quality_control`, `pipeline`, `pdf_extractor` |
| `src/text_processing` | `quality_control` |

These rules are tested individually (one test per pair) and exhaustively (one combined test). Always run this suite after adding new cross-package imports.

---

## Migration / Steering-Drift Regression Tests

Files at the root of `tests/` (not in a subdirectory) encode migration correctness:

- `test_migration_artifact_scrub_bug_condition.py` — encodes the **expected post-fix state**; each sub-check fails on unfixed code and passes after the fix.
- `test_migration_artifact_scrub_preservation.py` — encodes **existing correct behaviour** that must not regress; these must pass on both unfixed and fixed code.

Rules:
- Sub-checks use `pytest.fail()` with descriptive messages referencing the deviation number (e.g. `"Deviation 1.4"`).
- When writing a new bugfix spec, add a corresponding `test_migration_<feature>_bug_condition.py` and `test_migration_<feature>_preservation.py` pair at the `tests/` root.

---

## What Is and Isn't Tested

| Area | Coverage |
|---|---|
| `src/pdf_extractor/extraction/` | Backends (GROBID, PyMuPDF, pdfplumber, PaddleOCR), scan detector, schemas |
| `src/pdf_extractor/layout_utils.py` | Section heading detection, location cross-check |
| `src/artifact_generation/w3c_annotation.py` | W3C annotation projection and serialization |
| `src/pdf_extractor/processing/` | Sentence processor (via `tests/src/utils/test_sentence_processor.py`) |
| `src/quality_control/` | Pipeline, models, local metrics, rater, IAA calculator, adjudicator, reconciler, concern strategies, `Validator`, `StructureSchemaValidator`, `validate_context`, checks package (separation + integration), verification result, unified record layers |
| `src/quality_control/checks/` | Source text, semantic source, extractor agreement, task quality scaffold |
| `src/pipeline/` | Evidence index, manifest I/O, validator loc checks, extraction map grouping, extraction report QC, orchestrator concurrency, pdf_processor helpers |
| `src/agents/openai/` | `api_client` (async + cache key), prompts builders, prompts PBT |
| `src/utils/` | `config_utils`, `logging_utils`, source resolution, sentence processor |
| `src/text_processing/` | ABC enforcement, normalizers (example + PBT), tokenizers, matchers (example + PBT), embedding (mark slow), import isolation, deleted legacy paths |
| `tests/steering/` | QC/TextProcessor separation enforcement (AST-based) |
| `tests/` root | Dependency direction enforcement, migration regression tests |
| `src/pipeline/orchestrator.py` (full integration) | No dedicated integration test — exercised via end-to-end runs |

---

## QC Migration Test Contracts

### `tests/steering/test_qc_textprocessor_separation.py`

**What it checks:** Uses AST analysis (same pattern as `test_dependency_directions.py`) to walk every `.py` file under `src/quality_control/checks/` and inspect all import statements.

**Passes when:** No file under `src/quality_control/checks/` contains any of the following:
- An import from `text_processing` (any submodule)
- An import of `TextProcessor` by name or from `utils.text_processor`
- A top-level import (outside function/method bodies) of `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`

**Fails when:** Any `.py` file under `src/quality_control/checks/` — including `__init__.py` — contains one of the forbidden imports listed above. The failure message names the offending file, the import node, and the forbidden symbol.

This test must pass at all times. It is the automated guard for Requirements 1.4, 1.5, and 1.6 (QC/TextProcessor separation boundary).

---

### `tests/src/quality_control/test_qc_pipeline_integration.py`

**What it checks:** End-to-end integration of `run_quality_control` with the migrated QC package, plus output-preservation assertions.

**Passes when all of the following hold:**

1. `run_quality_control` completes without error when `semantic_verification.enabled=false`.
2. `ctx.metrics_hierarchy` contains **exactly** the keys `"extraction_coverage"`, `"source_text_verification"`, and `"semantic_verification"` — no legacy keys (`"local_metrics"`, `"exact_match"`, `"semantic_match"`, `"semantic_qc"`) are present.
3. `metrics_hierarchy["semantic_verification"]["extractor_agreement"]` is absent or has `status="skipped"` when `extractor_agreement.enabled=false`.
4. Manifest status values (`complete`, `failed_qc_pipeline`, `failed_chunks`, `failed_chunk_<n>`) are unchanged after migration.
5. `ExtractionCoverageReport` and the legacy `LocalQCReport` alias produce identical pass/fail boolean outcomes for the same inputs (preservation parametrized case).
6. Importing `quality_control`, `quality_control.checks`, and `quality_control.builtin_impls` does not cause `sentence_transformers`, `faiss`, or `torch` to appear in `sys.modules`.
7. `build_task_quality_scaffold()` return value serializes with `json.dumps()` without error.

**Fails when:** Any of the above assertions is violated. Specific failure cases:
- `metrics_hierarchy` contains a legacy key → fails with a message naming the unexpected key.
- `ExtractionCoverageReport` and the alias produce different pass/fail outcomes for the same input → fails with the differing input and both outcomes.
- A heavy dependency (`sentence_transformers`, `faiss`, `torch`) appears in `sys.modules` after a plain import of the QC package → fails naming the leaked module.
- `json.dumps(build_task_quality_scaffold())` raises → fails with the serialization error.

---

## Config Fixtures

For tests that need a config, write a minimal valid YAML to `tmp_path`:

```python
import textwrap

def test_something(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )
    from utils.config_utils import load_local_config
    config = load_local_config(str(cfg_file))
```

Never mutate module-level globals or `_QC_DEFAULTS` in tests.

---

## QCBundle Construction in Tests

Build minimal `QCBundle` instances using `Candidate` directly:

```python
from quality_control.models import Candidate, QCBundle

branches = [
    Candidate(source="grobid",  index=0, payload="<TEI/>", status=None),
    Candidate(source="pymupdf", index=1, payload=[],        status=None),
]
ctx = QCBundle(branches=branches)
```

Import all dataclasses from `quality_control.models` — never from individual QC submodules.

---

## Validator Testing

`quality_control/validator.py` provides a generic `Validator` + `ValidationResult`. Test it by injecting a serializer and a schema dict — no domain objects required:

```python
from quality_control.validator import Validator, ValidationResult

schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
v = Validator(serializer=lambda obj: obj, schema=schema)

result = v.validate({"name": "test"})
assert result.is_valid

result = v.validate({"name": 42})
assert not result.is_valid
assert any("name" in e for e in result.errors)
```

`StructureSchemaValidator` wraps `Validator` with `configs/structure_schema.json`. Pass a `schema_path` in tests to avoid relying on the project root:

```python
from quality_control.structure_validator import StructureSchemaValidator
validator = StructureSchemaValidator(schema_path="configs/structure_schema.json")
```

---

## TextProcessor Mocking

`TextProcessor` and `SentenceSegment` subclasses lazy-load NLP models. Mock `spacy` and `scispacy` when constructing them in tests:

```python
import sys
from unittest.mock import MagicMock, patch

mock_spacy = MagicMock()
mock_sent = MagicMock()
mock_sent.text = "Sentence one."
mock_doc = MagicMock()
mock_doc.sents = [mock_sent]
mock_spacy.load.return_value = MagicMock(return_value=mock_doc)

with patch.dict(sys.modules, {"scispacy": MagicMock(), "spacy": mock_spacy}):
    from text_processing.base import ScispaCySentenceSegment
    seg = ScispaCySentenceSegment()
```

For backends that raise `ImportError` when the package is absent, patch the module to `None`:

```python
with patch.dict(sys.modules, {"wtpsplit": None}):
    from text_processing.base import WtpSplitSentenceSegment
    seg = WtpSplitSentenceSegment()
    with pytest.raises(ImportError, match="pip install wtpsplit"):
        seg.tokenize_sentences("Hello.")
```
