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
pythonpath = "."
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

- `conftest.py` (repo root) — required; ensures `pdf_extractor.*`, `utils.*`, `quality_control.*`, `pipeline.*`, and `agents.*` all resolve when pytest collects from the root.
- `pdf_extractor/conftest.py` — inserts the project root at the front of `sys.path` as a fallback for collection starting inside `pdf_extractor/`.

Both must exist. The root-level one takes precedence when running `python -m pytest` from the repo root.

---

## Test Layout

```
tests/
├── agents/
│   └── openai/                  # api_client, prompts (async + PBT)
├── pdf_extractor/               mirrors pdf_extractor/
├── pipeline/                    mirrors pipeline/
├── quality_control/             mirrors quality_control/
├── utils/                       mirrors utils/
├── test_dependency_directions.py   # cross-package import enforcement (AST-based)
├── test_migration_artifact_scrub_bug_condition.py
└── test_migration_artifact_scrub_preservation.py
```

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

PBT tests live alongside unit tests in their respective subdirectory under `tests/`. Examples: `test_validator_properties.py`, `test_prompts_pbt.py`.

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

`tests/test_dependency_directions.py` enforces cross-package import rules via AST analysis. It checks every `.py` file in each package and fails if a forbidden import is found.

Enforced rules:

| Source package | Must NOT import |
|---|---|
| `pdf_extractor` | `quality_control` |
| `quality_control` | `agents`, `pipeline`, `pdf_extractor` |
| `agents` | `quality_control`, `pipeline`, `pdf_extractor` |

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
| `pdf_extractor/extraction/` | Backends (GROBID, PyMuPDF, pdfplumber, PaddleOCR), scan detector, schemas |
| `pdf_extractor/utils/` | `text_utils`, `embedding_utils`, `layout_utils` |
| `pdf_extractor/annotation/` | W3C annotation projection and artifact generation |
| `pdf_extractor/processing/` | Sentence processor (via `tests/utils/test_sentence_processor.py`) |
| `quality_control/` | Pipeline, models, local metrics, rater, IAA calculator, adjudicator, reconciler, concern strategies, `Validator`, `StructureSchemaValidator`, `validate_context` |
| `pipeline/` | Evidence index, manifest I/O, validator loc checks, extraction map grouping, extraction report QC, orchestrator concurrency, pdf_processor helpers |
| `agents/openai/` | `api_client` (async), cache key, prompts builders, prompts PBT |
| `utils/` | `config_utils`, `logging_utils`, source resolution, `text_processor`, sentence processor |
| `tests/` root | Dependency direction enforcement, migration regression tests |
| `pipeline/orchestrator.py` (full integration) | No dedicated integration test — exercised via end-to-end runs |

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
    from utils.text_processor import TextProcessor
    tp = TextProcessor(config={})
```

For backends that raise `ImportError` when the package is absent, patch the module to `None`:

```python
with patch.dict(sys.modules, {"wtpsplit": None}):
    seg = WtpSplitSentenceSegment()
    with pytest.raises(ImportError, match="pip install wtpsplit"):
        seg.tokenize_sentences("Hello.")
```
