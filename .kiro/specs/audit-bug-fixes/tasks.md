# Tasks: audit-bug-fixes

## Overview

Implementation tasks for the 12-bug audit fix. Tasks are ordered by the
Recommended Fix Order from the bugfix spec. Each task is self-contained and
can be committed independently. Tasks 1–4 are trivial single-file edits;
Task 5 is the largest (new file + config updates); Tasks 6–10 are small
targeted fixes.

---

## Task 1: Remove hardcoded API key (Bug 11 — Priority 0)

**Requirements:** 2.11

### Subtasks

- [ ] 1.1 In `configs/config.yaml` line 4, replace the `sk-proj-T0khx...` value with `""` and update the comment to read `# Set OPENAI_API_KEY environment variable`
- [ ] 1.2 Verify `load_openai_config()` returns empty string for `api_key` when env var is unset
- [ ] 1.3 Verify `load_openai_config()` returns env var value when `OPENAI_API_KEY` is set

---

## Task 2: Fix `extraction_map_path` typo (Bug 4 — Priority 1)

**Requirements:** 2.4

### Subtasks

- [ ] 2.1 In `configs/config.yaml` line 57, change `extraction_map_path: "config/extraction_map.json"` to `extraction_map_path: "configs/extraction_map.json"`
- [ ] 2.2 Verify `utils/path_utils.py` resolves `EXTRACTION_MAP` to `configs/extraction_map.json` on first attempt (no fallback needed)
- [ ] 2.3 Verify `pipeline/extraction_map.py` `load_chunk_fields()` can read the file at the resolved path

---

## Task 3: Lazy `requests` import in `grobid_manager.py` (Bug 8 — Priority 2)

**Requirements:** 2.8

### Subtasks

- [ ] 3.1 Remove `import requests` from top of `utils/grobid_manager.py` (line 3)
- [ ] 3.2 Add `import requests` inside each method body that uses it (at minimum `_is_server_alive`)
- [ ] 3.3 Verify `from utils.grobid_manager import GrobidServerManager` succeeds without `requests` installed
- [ ] 3.4 Verify `GrobidServerManager._is_server_alive()` still calls `requests.get` correctly when `requests` IS installed

---

## Task 4: Lazy `openai` import in `pdf_processor.py` (Bug 3 — Priority 3)

**Requirements:** 2.3

### Subtasks

- [ ] 4.1 Remove `from agents.openai.api_client import extract_chunk, warm_pdf_cache` from top of `pipeline/pdf_processor.py` (line 6)
- [ ] 4.2 Add the import inside `process_pdf()` function body (first line of function or before first use)
- [ ] 4.3 Verify `import pipeline` succeeds without the `openai` package installed
- [ ] 4.4 Verify `from pipeline.extraction_pipeline import build_qc_bundle` succeeds without `openai`
- [ ] 4.5 Verify full pipeline still works when `openai` IS installed (functional regression check)

---

## Task 5: Create `DefaultTextProcessor` and update defaults (Bugs 1, 10, 12 — Priority 4–5)

**Requirements:** 2.1, 2.10, 2.12

### Subtasks

- [ ] 5.1 Create `text_processing/composite.py` with `DefaultTextProcessor(TextProcessor)` class
- [ ] 5.2 Implement `normalize(text)` — NFKC normalization + whitespace collapse via `unicodedata` and `re`
- [ ] 5.3 Implement `tokenize_words(text)` — normalize then split on whitespace
- [ ] 5.4 Implement `tokenize_sentences(text)` — lazy-load configurable `SentenceSegment` backend; default to `NLTKPunktSentenceSegment`
- [ ] 5.5 Implement `clean_ocr(text)` — strip U+FFFD, C0 controls `[\x00-\x08\x0b\x0c\x0e-\x1f]`, collapse whitespace
- [ ] 5.6 Implement `compare(a, b)` — `difflib.SequenceMatcher(None, norm_a, norm_b).ratio()`; both empty → `1.0`
- [ ] 5.7 Implement `extract_keywords(text)` — tokenize_words minus hardcoded English stopword set
- [ ] 5.8 Add `DefaultTextProcessor` to `text_processing/__init__.py` exports
- [ ] 5.9 In `utils/config_utils.py`, change `_QC_DEFAULTS["text_processor"]["class"]` from `"text_processing.base.ScispaCySentenceSegment"` to `"text_processing.composite.DefaultTextProcessor"`
- [ ] 5.10 In `pipeline/extraction_pipeline.py` (line ~80), replace `tp = TextProcessor(config=tp_cfg)` with importlib-based dynamic loading (same pattern as `_load_text_processor`)
- [ ] 5.11 Verify `DefaultTextProcessor().compare("hello", "hello")` returns `1.0`
- [ ] 5.12 Verify `DefaultTextProcessor().compare("hello", "world")` returns a float < `1.0`
- [ ] 5.13 Verify `DefaultTextProcessor().clean_ocr("text\ufffdhere")` does not contain `\ufffd`
- [ ] 5.14 Verify `DefaultTextProcessor().tokenize_sentences("First. Second.")` returns at least 2 items
- [ ] 5.15 Verify `DefaultTextProcessor().extract_keywords("the quick brown fox")` excludes "the" but includes "quick", "brown", "fox"
- [ ] 5.16 Verify `DefaultTextProcessor().normalize("")` returns `""`
- [ ] 5.17 Verify `DefaultTextProcessor().compare("", "")` returns `1.0`

---

## Task 6: Fix reconciler fallback (Bug 2 — Priority 6)

**Requirements:** 2.2

### Subtasks

- [ ] 6.1 In `quality_control/reconciler.py`, locate the strategy-defaults block where `text_processor = TextProcessor()` is set
- [ ] 6.2 Replace with importlib-based loading of `DefaultTextProcessor`:
      ```python
      if text_processor is None:
          import importlib
          _mod = importlib.import_module("text_processing.composite")
          text_processor = getattr(_mod, "DefaultTextProcessor")()
      ```
- [ ] 6.3 Remove or update the `from text_processing.base import TextProcessor` import that was only used for the fallback (keep it if used elsewhere for type checking)
- [ ] 6.4 Verify `reconcile(primary_artifact={...}, secondary_artifact={...}, text_processor=None)` succeeds without `TypeError`
- [ ] 6.5 Verify `reconcile(..., text_processor=some_explicit_instance)` still uses the explicit instance

---

## Task 7: Remove silent error swallowing (Bug 6 — Priority 7)

**Requirements:** 2.6

### Subtasks

- [ ] 7.1 In `quality_control/quality_control.py`, locate the `try/except Exception` block around `_load_text_processor(config)` (lines ~345–347)
- [ ] 7.2 Remove the `try/except` and call `_load_text_processor(config)` directly:
      ```python
      text_processor = _load_text_processor(config)
      ```
- [ ] 7.3 Search test suite for tests that rely on `text_processor = None` behavior (e.g. tests that don't configure a valid TP class)
- [ ] 7.4 Update any affected tests to either: (a) set `config["text_processor"]["class"]` to a valid lightweight class, or (b) mock `_load_text_processor` explicitly
- [ ] 7.5 Verify that when `_load_text_processor` raises `ImportError`, it propagates to the caller of `run_quality_control()`
- [ ] 7.6 Verify that when a valid class is configured, `run_quality_control()` proceeds normally

---

## Task 8: Remove dead predicate in branch lookup (Bug 5 — Priority 8)

**Requirements:** 2.5

### Subtasks

- [ ] 8.1 In `quality_control/quality_control.py`, locate `_pdf_reconciler_fn` (line ~436)
- [ ] 8.2 Replace the two-step secondary branch lookup:
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
      With the single working predicate:
      ```python
      secondary_branch = next(
          (b for b in all_branches if b.extractor in {"pdfplumber", "pymupdf"}),
          None,
      )
      ```
- [ ] 8.3 Verify reconciler still selects the correct secondary branch when branches include a `pdfplumber` or `pymupdf` source
- [ ] 8.4 Verify reconciler handles the case where no secondary branch exists (returns `None`)

---

## Task 9: Align `failure_behavior` defaults (Bug 7 — Priority 9)

**Requirements:** 2.7

### Subtasks

- [ ] 9.1 In `utils/config_utils.py`, `_QC_DEFAULTS["quality_control"]["grobid_integration"]["failure_behavior"]`: change `"fallback"` to `"manifest_fail"`
- [ ] 9.2 In `utils/config_utils.py`, `load_openai_config()` return dict: change `grobid_integration_cfg.get("failure_behavior", "fallback")` to `grobid_integration_cfg.get("failure_behavior", "manifest_fail")`
- [ ] 9.3 Verify that when `configs/config.yaml` explicitly sets `failure_behavior: "manifest_fail"`, the pipeline uses that value (no change in behavior)
- [ ] 9.4 Verify that when `failure_behavior` key is removed from config.yaml, both `_QC_DEFAULTS` and `load_openai_config()` agree on `"manifest_fail"`

---

## Task 10: Remove unused import + gate scaffold + fix adjudicator label (Bugs 9, 12 — Priority 10)

**Requirements:** 2.9, 2.12

### Subtasks

- [ ] 10.1 In `quality_control/validate_context.py` line 31, remove `from dataclasses import asdict`
- [ ] 10.2 Verify `validate_qc_context_input()` still works (no `NameError`)
- [ ] 10.3 In `utils/config_utils.py`, change `"adjudicator": {"strategy": "placeholder"}` to `"adjudicator": {"strategy": "majority_vote"}`
- [ ] 10.4 In `utils/config_utils.py`, change `"task_quality_scaffold": {"enabled": True}` to `"task_quality_scaffold": {"enabled": False}`
- [ ] 10.5 In `configs/config.yaml`, change `task_quality_scaffold: enabled: true` to `enabled: false`
- [ ] 10.6 Verify that the task_quality_scaffold code path in `quality_control/quality_control.py` is not reached when `enabled: false`
- [ ] 10.7 Verify existing tests in `tests/utils/test_quality_control_config.py` still pass (update assertions if they check for `"placeholder"` or `enabled: True`)

---

## Task 11: Final verification

**Requirements:** All (acceptance)

### Subtasks

- [ ] 11.1 Run `python -c "import pipeline"` without `openai` installed — no ImportError
- [ ] 11.2 Run `python -c "from utils.grobid_manager import GrobidServerManager"` without `requests` — no ImportError
- [ ] 11.3 Run `python -c "from quality_control.quality_control import run_quality_control"` — no ImportError
- [ ] 11.4 Verify `configs/config.yaml` contains no string matching `sk-proj-` or `sk-[A-Za-z0-9]{40,}`
- [ ] 11.5 Verify `configs/extraction_map.json` resolves correctly via `utils/path_utils.EXTRACTION_MAP`
- [ ] 11.6 Run full test suite: `python -m pytest -q` — all tests pass
- [ ] 11.7 Verify no `NotImplementedError` is raised when `DefaultTextProcessor` is used as the pipeline text processor
- [ ] 11.8 Verify no `"scaffolded"` or `"not_computed"` values appear in pipeline output when `task_quality_scaffold.enabled` is `false`
- [ ] 11.9 Verify `reconciler.reconcile()` with `text_processor=None` loads `DefaultTextProcessor` and produces a valid `UnifiedRecord`
- [ ] 11.10 Verify `_QC_DEFAULTS["adjudicator"]["strategy"]` equals `"majority_vote"`
- [ ] 11.11 Verify `_QC_DEFAULTS["quality_control"]["grobid_integration"]["failure_behavior"]` equals `"manifest_fail"`
