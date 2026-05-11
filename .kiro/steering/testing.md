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
addopts = "--import-mode=importlib -m \"not slow\""
markers = [
    "slow: marks tests as slow (deselected by default; run with -m slow or -m \"\")",
]
```

Always run from the **repo root**.

---

## conftest.py Files

Two `conftest.py` files ensure `sys.path` is correct:

- `conftest.py` (repo root) — required; ensures `pdf_extractor.*`, `utils.*`, `quality_control.*`, and `pipeline.*` all resolve when pytest collects from the root.
- `pdf_extractor/conftest.py` — inserts the project root at the front of `sys.path` as a fallback for collection starting inside `pdf_extractor/`.

Both must exist. The root-level one takes precedence when running `python -m pytest` from the repo root.

---

## Test Layout

```
tests/
├── pdf_extractor/         mirrors pdf_extractor/
├── quality_control/       mirrors quality_control/
├── pipeline/              mirrors pipeline/
└── utils/                 mirrors utils/
```

All test files follow the naming convention:

```
test_<module-or-feature>_<aspect>.py
```

Examples:
- `test_quality_control_rater.py` — rater module
- `test_scan_detector_routing.py` — routing aspect of scan detector
- `test_text_extractor_branch2.py` — PyMuPDF backend
- `test_steering_drift_bug_condition.py` — bug condition regression

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

PBT tests live alongside unit tests in their respective subdirectory under `tests/`.

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

## Steering-Drift Regression Tests

`test_steering_drift_*.py` files verify that specific bug conditions are preserved and fixed:

- `test_steering_drift_bug_condition.py` — encodes the **expected post-fix state**; each sub-check fails on unfixed code and passes after the fix.
- `test_steering_drift_preservation.py` — encodes **existing correct behaviour** that must not regress; these must pass on both unfixed and fixed code.

Rules:
- Both files live in `tests/steering`.
- Sub-checks use `pytest.fail()` with descriptive messages referencing the deviation number (e.g. `"Deviation 1.4"`).
- When writing a new bugfix spec, add a corresponding `test_steering_drift_<feature>_bug_condition.py`.

---

## What Is and Isn't Tested

| Area | Coverage |
|---|---|
| `pdf_extractor/extraction/` | Tier 1/2/3 orchestration, schemas, backends (GROBID, PyMuPDF, pdfplumber, PaddleOCR), scan detector |
| `quality_control/` | Generic pipeline, models, local metrics, artifact generator, rater, IAA calculator, adjudicator, reconciler |
| `pdf_extractor/utils/` | text_utils, embedding_utils, layout_utils, text_processor, sentence segmenters |
| `pdf_extractor/annotation/` | W3C annotation projection and artifact generation |
| `pipeline/evidence_index.py` | Evidence bundle building and caching |
| `pipeline/validator.py` | Chunk output validation and field reconstruction |
| `utils/` | config_utils, logging_utils, path_utils, source resolution |
| `agents/openai/` | **No dedicated tests** — exercised via integration |
| `pipeline/orchestrator.py` | **No dedicated tests** — exercised via integration |

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
