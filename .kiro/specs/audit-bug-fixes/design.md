# Bugfix Design Document

## Overview

This design describes the architectural decisions and component-level fixes for
all 12 bugs identified in the audit. The central theme is **removing dead
abstractions, surfacing errors, and providing a single composite `TextProcessor`
that fulfils the full pipeline contract** so that an end-to-end run from
`main.py` succeeds without placeholder outputs or silent degradation.

### Goals

- Every fix is minimal and localised — no sweeping refactors.
- The composite `TextProcessor` uses only stdlib + `difflib` for all methods
  except `tokenize_sentences`, which delegates to a configurable backend.
- No new heavy dependencies are introduced (all existing backends remain lazy).
- Pipeline behavior is preserved for any caller that already passes explicit
  injectables; only the defaults change.

### Non-Goals

- Full reimplementation of task-quality metrics (Bug 12 gates them behind
  `enabled: False` rather than implementing real computation).
- Changing the `QualityReport` base class behavior (the PDF pipeline already
  uses `ExtractionCoverageReport` which has real logic).
- Modifying the generic `run_pipeline()` API or its stage signatures.

---

## Design Decisions

### D1: Composite `TextProcessor` class (Bugs 1, 2, 10, 12)

**Decision:** Create `text_processing/composite.py` containing a
`DefaultTextProcessor(TextProcessor)` class that implements all six abstract
methods using only stdlib primitives (plus `difflib.SequenceMatcher`).

**Rationale:** The pipeline requires `compare()`, `clean_ocr()`, `normalize()`,
`tokenize_words()`, `tokenize_sentences()`, and `extract_keywords()` to all
work on the same instance. No existing class provides this. Using stdlib avoids
adding new dependencies. The sentence tokenization backend is delegated to a
configurable `SentenceSegment` subclass internally.

**Interface:**

```python
# text_processing/composite.py

from text_processing.base import TextProcessor

class DefaultTextProcessor(TextProcessor):
    """Full-featured TextProcessor for the EviTrace pipeline.

    Implements all six abstract methods with real, functional logic.
    Uses only stdlib + difflib for all methods except tokenize_sentences,
    which delegates to a configurable SentenceSegment backend.
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._sentence_backend = None  # lazy-loaded

    def normalize(self, text: str) -> str:
        """NFKC unicode normalization + whitespace collapsing."""
        ...

    def tokenize_words(self, text: str) -> list[str]:
        """Split on whitespace after normalization."""
        ...

    def tokenize_sentences(self, text: str) -> list[str]:
        """Delegate to configured SentenceSegment backend (lazy-loaded)."""
        ...

    def clean_ocr(self, text: str) -> str:
        """Remove U+FFFD, C0 controls, collapse whitespace."""
        ...

    def compare(self, a: str, b: str) -> float:
        """Normalized Levenshtein similarity via difflib.SequenceMatcher."""
        ...

    def extract_keywords(self, text: str) -> list[str]:
        """Return non-stopword tokens after normalization."""
        ...
```

**Method implementations:**

| Method | Algorithm | Edge cases |
|--------|-----------|------------|
| `normalize` | `unicodedata.normalize("NFKC", text)` then `re.sub(r"\s+", " ", ...).strip()` | Empty string → `""` |
| `tokenize_words` | `self.normalize(text).split()` | Empty → `[]` |
| `tokenize_sentences` | Lazy-load backend from `config["sentence_tokenizer"]["backend"]`; default `"nltk_punkt"` (lightest dep) | Empty → `[]` |
| `clean_ocr` | Strip `\ufffd`, C0 controls `[\x00-\x08\x0b\x0c\x0e-\x1f]`, then collapse whitespace | Empty → `""` |
| `compare` | `difflib.SequenceMatcher(None, a_norm, b_norm).ratio()`; both empty → `1.0` | One empty, one not → `0.0` |
| `extract_keywords` | `tokenize_words(text)` minus a hardcoded English stopword set (~180 words) | Empty → `[]` |

**Sentence backend delegation:** The `tokenize_sentences` method reads
`config["sentence_tokenizer"]["backend"]` and instantiates the corresponding
`SentenceSegment` subclass on first call. Supported values:
`"scispacy"`, `"wtpsplit"`, `"nltk_punkt"`, `"spacy_sentencizer"`, `"stanza"`.
Default: `"nltk_punkt"` (NLTK Punkt is lightweight and already a requirement).

---

### D2: Fix ABC instantiation sites (Bugs 1, 2)

**Bug 1 — `pipeline/extraction_pipeline.py` line ~80:**

Current:
```python
tp = TextProcessor(config=tp_cfg)
```

Fix:
```python
import importlib

class_path = tp_cfg.get("class", "text_processing.composite.DefaultTextProcessor")
module_name, class_name = class_path.rsplit(".", 1)
module = importlib.import_module(module_name)
cls = getattr(module, class_name)
tp = cls(config=tp_cfg)
```

This mirrors the pattern already used in `_load_text_processor()`.

**Bug 2 — `quality_control/reconciler.py` strategy-defaults block:**

Current:
```python
from text_processing.base import TextProcessor
if text_processor is None:
    text_processor = TextProcessor()
```

Fix:
```python
if text_processor is None:
    import importlib
    _mod = importlib.import_module("text_processing.composite")
    _cls = getattr(_mod, "DefaultTextProcessor")
    text_processor = _cls()
```

---

### D3: Lazy import for `agents.openai.api_client` (Bug 3)

**File:** `pipeline/pdf_processor.py`

Current (line 6):
```python
from agents.openai.api_client import extract_chunk, warm_pdf_cache
```

Fix: Move import inside the two function bodies that use it:
```python
async def process_pdf(...):
    from agents.openai.api_client import extract_chunk, warm_pdf_cache
    ...
```

**File:** `pipeline/orchestrator.py` (line 10):

Current:
```python
from . import pdf_processor
```

This is fine to keep — once `pdf_processor.py` no longer has the top-level
import, importing the module won't trigger the `openai` dependency. No change
needed in `orchestrator.py`.

---

### D4: Fix `extraction_map_path` (Bug 4)

**File:** `configs/config.yaml` line 57

Current:
```yaml
extraction_map_path: "config/extraction_map.json"
```

Fix:
```yaml
extraction_map_path: "configs/extraction_map.json"
```

One character change (`config` → `configs`).

---

### D5: Remove dead predicate (Bug 5)

**File:** `quality_control/quality_control.py`, inside `_pdf_reconciler_fn`

Current:
```python
secondary_branch = next(
    (b for b in all_branches if str(b.index).lower() in {"pdfplumber", "pymupdf"}),
    None,
)
if secondary_branch is None:
    secondary_branch = next(
        (b for b in all_branches if b.extractor in {"pdfplumber", "pymupdf"}),
        None,
    )
```

Fix:
```python
secondary_branch = next(
    (b for b in all_branches if b.extractor in {"pdfplumber", "pymupdf"}),
    None,
)
```

---

### D6: Remove silent error swallowing (Bug 6)

**File:** `quality_control/quality_control.py`, in `run_quality_control()`

Current:
```python
try:
    text_processor = _load_text_processor(config)
except Exception:
    # Fall back to None to preserve behavior in test environments
    text_processor = None
```

Fix:
```python
text_processor = _load_text_processor(config)
```

The `_load_text_processor` function already raises `ImportError` with a clear
message. Removing the bare `except` lets it propagate. Tests that need to
operate without a text processor must explicitly configure a lightweight class
(e.g. `text_processing.composite.DefaultTextProcessor` with NLTK backend).

---

### D7: Align `failure_behavior` defaults (Bug 7)

**File 1:** `utils/config_utils.py`, `_QC_DEFAULTS` dict

Change:
```python
"grobid_integration": {
    "enabled": True,
    "failure_behavior": "fallback",  # ← change to "manifest_fail"
    ...
}
```

**File 2:** `utils/config_utils.py`, `load_openai_config()` return dict

Change:
```python
"grobid_failure_behavior": grobid_integration_cfg.get("failure_behavior", "fallback"),
# ← change fallback default to "manifest_fail"
```

---

### D8: Lazy `requests` import (Bug 8)

**File:** `utils/grobid_manager.py`

Current (line 3):
```python
import requests
```

Fix: Remove from top level. Add inside each method that uses it:
```python
def _is_server_alive(self, url: str) -> bool:
    import requests
    try:
        resp = requests.get(url, timeout=5)
        ...
```

---

### D9: Remove unused import (Bug 9)

**File:** `quality_control/validate_context.py` line 31

Remove:
```python
from dataclasses import asdict
```

---

### D10: Update `_QC_DEFAULTS` text_processor class path (Bugs 10, 12)

**File:** `utils/config_utils.py`

Change:
```python
"text_processor": {
    "class": "text_processing.base.ScispaCySentenceSegment",
    ...
}
```

To:
```python
"text_processor": {
    "class": "text_processing.composite.DefaultTextProcessor",
    ...
}
```

---

### D11: Remove hardcoded API key (Bug 11)

**File:** `configs/config.yaml` line 4

Current:
```yaml
api_key: "sk-proj-T0khxCMqsjPzbDusTuHYsacczxUf0qHV0qn0WrTAM-..."
```

Fix:
```yaml
api_key: ""  # Set OPENAI_API_KEY environment variable
```

---

### D12: Gate `task_quality_scaffold` and fix adjudicator label (Bug 12)

**File 1:** `utils/config_utils.py`, `_QC_DEFAULTS`

Change:
```python
"adjudicator": {"strategy": "placeholder"},
```
To:
```python
"adjudicator": {"strategy": "majority_vote"},
```

**File 2:** `utils/config_utils.py`, `_QC_DEFAULTS`

Change:
```python
"task_quality_scaffold": {
    "enabled": True,
},
```
To:
```python
"task_quality_scaffold": {
    "enabled": False,
},
```

**File 3:** `configs/config.yaml`

Change `task_quality_scaffold.enabled` from `true` to `false`.

**Rationale:** Since `build_task_quality_scaffold()` only returns placeholder
values (`"scaffolded"`, `None`), gating it behind `enabled: False` means the
code path is unreachable by default. When real task-quality metrics are
implemented in the future, the feature can be re-enabled.

---

## Component Interaction Diagram

```
main.py
  │
  ├─► GrobidServerManager (lazy `requests` — Bug 8 fixed)
  │
  └─► pipeline.run_pipeline()
        │
        ├─► pipeline/orchestrator.py
        │     └─► from . import pdf_processor  (no eager openai — Bug 3 fixed)
        │
        └─► build_qc_bundle()  [pipeline/extraction_pipeline.py]
              │
              ├─► importlib → DefaultTextProcessor (Bug 1 fixed)
              │     ├── normalize()      ← NFKC + whitespace
              │     ├── tokenize_words() ← split after normalize
              │     ├── tokenize_sentences() ← delegate to backend
              │     ├── clean_ocr()      ← strip control/replacement chars
              │     ├── compare()        ← SequenceMatcher.ratio()
              │     └── extract_keywords() ← tokens minus stopwords
              │
              ├─► scan_detector.classify_page(page, tp, ...)
              │     └── tp.clean_ocr()  ← now works (Bug 10 fixed)
              │
              └─► run_quality_control(branches, ...)
                    │
                    ├─► _load_text_processor(config)  ← propagates errors (Bug 6 fixed)
                    │     └── loads DefaultTextProcessor
                    │
                    ├─► _pdf_rater_fn → ExtractionCoverageReport (real metrics)
                    ├─► _pdf_iaa_fn → InterRaterReport (real pairwise agreement)
                    ├─► _pdf_adjudicator_fn → AdjudicationDecision (real majority vote)
                    │
                    └─► _pdf_reconciler_fn
                          ├── secondary_branch via b.extractor (Bug 5 fixed)
                          └── reconciler.reconcile(...)
                                ├── text_fidelity_strategy.reconcile(p, r, tp)
                                │     └── tp.compare()  ← now works (Bug 10 fixed)
                                ├── section_strategy.reconcile(section, block, tp)
                                │     └── tp.compare()  ← now works (Bug 10 fixed)
                                └── fallback: loads DefaultTextProcessor (Bug 2 fixed)
```

---

## Risk Assessment

| Fix | Risk | Mitigation |
|-----|------|------------|
| Bug 1 (importlib in extraction_pipeline) | Low — identical pattern exists in `_load_text_processor` | Test with real and missing configs |
| Bug 2 (importlib in reconciler fallback) | Low — fallback rarely triggered; pipeline always passes `text_processor` | Unit test the fallback path explicitly |
| Bug 3 (lazy import in pdf_processor) | Medium — could break if import order matters in async context | Test `import pipeline` without `openai` installed |
| Bug 4 (config path typo) | Negligible — one character change | Verify file resolves at startup |
| Bug 5 (dead predicate removal) | Negligible — second predicate already does the work | Existing reconciler tests validate |
| Bug 6 (remove bare except) | Medium — may surface errors in CI/test environments that relied on silent fallback | Update affected tests to configure valid TP class |
| Bug 7 (align defaults) | Low — config.yaml already sets `"manifest_fail"` explicitly | Only affects users who remove the config key entirely |
| Bug 8 (lazy requests) | Low — same pattern used throughout codebase | Verify GROBID lifecycle in integration tests |
| Bug 9 (unused import) | Negligible | No behavioral change |
| Bug 10 (composite TP) | Medium — new code, could have edge-case bugs in `compare()` or `clean_ocr()` | Comprehensive unit tests with edge cases |
| Bug 11 (remove API key) | Low — env var override already works | Verify `load_openai_config` still reads from env |
| Bug 12 (gate scaffold, fix labels) | Low — disabling scaffold has no downstream effect | Verify pipeline output JSON omits scaffolded keys when disabled |

---

## File Change Summary

| File | Action | Bugs |
|------|--------|------|
| `text_processing/composite.py` | **CREATE** | 1, 2, 10, 12 |
| `pipeline/extraction_pipeline.py` | MODIFY (line ~80) | 1 |
| `quality_control/reconciler.py` | MODIFY (strategy-defaults block) | 2 |
| `pipeline/pdf_processor.py` | MODIFY (move import to function body) | 3 |
| `configs/config.yaml` | MODIFY (3 changes: path, key, scaffold) | 4, 11, 12 |
| `quality_control/quality_control.py` | MODIFY (2 changes: predicate, except block) | 5, 6 |
| `utils/config_utils.py` | MODIFY (3 changes: failure_behavior ×2, adjudicator strategy, TP class) | 7, 10, 12 |
| `utils/grobid_manager.py` | MODIFY (move import) | 8 |
| `quality_control/validate_context.py` | MODIFY (remove import) | 9 |
| `text_processing/__init__.py` | MODIFY (add DefaultTextProcessor export) | 10, 12 |
