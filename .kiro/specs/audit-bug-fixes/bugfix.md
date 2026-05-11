# Bugfix Requirements Document

## Introduction

A full-repository audit of EviTrace identified **12 bugs** spanning two critical
runtime crashes (direct instantiation of the `TextProcessor` ABC), one
security issue (live API key committed to `configs/config.yaml`), five
high-severity defects (wrong config path, dead branch-lookup code, eager
`openai` import, silent error swallowing at `_load_text_processor` call site,
incomplete `TextProcessor` contract), and four medium/low issues (inconsistent
`failure_behavior` defaults, eager `requests` import, unused `asdict` import,
and incomplete injectable defaults preventing end-to-end execution).

Together these bugs prevent the pipeline from starting in clean environments,
expose credentials in version control, silently degrade reconciliation quality,
and make the configured text-processor backend unreachable without any
operator-visible error.

> **Reviewer note — corrections from prior version:**
>
> - Bug 5 severity reduced: the fallback predicate (`b.extractor`) works correctly;
>   only the first predicate is dead code. Functional impact is wasted computation
>   and misleading code, not a runtime failure.
> - Bug 6 rewritten: `_load_text_processor()` itself correctly raises `ImportError`;
>   the silent swallowing occurs at the **call site** in `run_quality_control()` where
>   a bare `except Exception` catches the error and sets `text_processor = None`.
> - Bug 4: reference to non-existent `path_utils.py` corrected — the two-step
>   fallback actually lives in `utils/path_utils.py` (confirmed to exist).
> - Bug 10 expanded: the problem is not merely "no subclass covers all 6 methods"
>   but that the **default class** (`ScispaCySentenceSegment`) loaded by
>   `_load_text_processor` raises `NotImplementedError` for `compare()` which is
>   called by the concern strategies during reconciliation.
> - **New Bug 12** added: injectable completeness gaps that prevent an end-to-end
>   pipeline run from `main.py`.
> - Pseudocode section expanded to cover all bugs.
> - Recommended fix order added.

---

## Recommended Fix Order

Fixes SHOULD be applied in the following order to maximize incremental
testability and avoid cascading failures:

| Priority | Bug(s) | Rationale |
|----------|--------|-----------|
| 0 (emergency) | 11 | Security: revoke & remove leaked key immediately |
| 1 | 4 | One-character fix, zero risk, unblocks path resolution |
| 2 | 8 | One-line move, unblocks import in environments without `requests` |
| 3 | 3 | Lazy import, unblocks `import pipeline` without `openai` |
| 4 | 1 | Unblocks `build_qc_bundle` — prerequisite for all QC work |
| 5 | 10, 12 | Provide a full-featured `TextProcessor` — prerequisite for reconciler |
| 6 | 2 | Fix reconciler fallback (depends on Bug 10/12 fix existing) |
| 7 | 6 | Remove silent swallowing so failures surface |
| 8 | 5 | Remove dead predicate (cosmetic once fallback works) |
| 9 | 7 | Align defaults (low risk, no behavioral change if config is explicit) |
| 10 | 9 | Remove unused import (trivial) |

---

## Bug Analysis

### Current Behavior (Defect)

**Bug 1 — Critical: `TextProcessor` ABC instantiated directly in `extraction_pipeline.py`**

1.1 WHEN `build_qc_bundle()` is called THEN the system raises `TypeError: Can't instantiate abstract class TextProcessor with abstract methods …` at `pipeline/extraction_pipeline.py:80` because `TextProcessor(config=tp_cfg)` attempts to instantiate the ABC directly instead of loading the concrete class named in `tp_cfg["class"]`.

**Bug 2 — Critical: `TextProcessor` ABC instantiated directly in `reconciler.py` fallback**

1.2 WHEN `reconcile()` is called and `text_processor` is `None` THEN the system raises `TypeError: Can't instantiate abstract class TextProcessor with abstract methods …` at `quality_control/reconciler.py` (inside the strategy-defaults block) because the fallback `text_processor = TextProcessor()` attempts to instantiate the ABC directly.

**Bug 3 — High: Importing the `pipeline` package eagerly pulls in `agents.openai.api_client`**

1.3 WHEN any code executes `import pipeline` or `from pipeline.extraction_pipeline import build_qc_bundle` THEN the system raises `ImportError` (or `ModuleNotFoundError`) if the `openai` package is not installed, because the import chain is: `pipeline/__init__.py` → `.orchestrator` → `from . import pdf_processor` → `pipeline/pdf_processor.py` line 6: `from agents.openai.api_client import extract_chunk, warm_pdf_cache` at module level, even though the PDF/QC extraction path is documented as OpenAI-independent.

**Bug 4 — High: Wrong `extraction_map_path` in `configs/config.yaml`**

1.4 WHEN the pipeline starts and reads `configs/config.yaml` THEN the system resolves `EXTRACTION_MAP` to a non-existent path because `extraction_map_path` is set to `"config/extraction_map.json"` (missing the `s`). The two-step fallback in `utils/path_utils.py` first tries `BASE_DIR / "config/extraction_map.json"` (does not exist), then tries `BASE_DIR / "configs" / "config/extraction_map.json"` (also does not exist). The actual file is at `configs/extraction_map.json`.

**Bug 5 — Medium: Dead first predicate in secondary branch lookup**

1.5 WHEN `_pdf_reconciler_fn` in `quality_control/quality_control.py` searches for the secondary branch THEN the first predicate (`str(b.index).lower() in {"pdfplumber", "pymupdf"}`) is dead code that always evaluates to `False` because `b.index` is an integer positional index (e.g. `0`, `1`). The branch IS ultimately found by the second predicate (`b.extractor in {"pdfplumber", "pymupdf"}`) which correctly uses the `extractor` property (alias for `source`). The dead predicate wastes a full list scan and misleads future maintainers.

**Bug 6 — High: Call site of `_load_text_processor` silently swallows errors**

1.6 WHEN `_load_text_processor` in `quality_control/quality_control.py` raises any exception (wrong class path, missing dependency, import error) THEN the call site in `run_quality_control()` (lines ~345–347) catches the error with a bare `except Exception` and silently sets `text_processor = None` with only a code comment ("Fall back to None to preserve behavior in test environments") — no log message, no warning, no operator-visible indication that the configured backend failed to load. Downstream, this causes the reconciler to skip sentence tokenization entirely and concern strategies to skip text comparison, silently degrading output quality.

> **Clarification:** `_load_text_processor()` itself correctly wraps errors in `ImportError` and re-raises. The defect is exclusively at the call site.

**Bug 7 — Medium: Inconsistent `grobid_integration.failure_behavior` defaults across three locations**

1.7 WHEN `failure_behavior` is not explicitly set in `configs/config.yaml` THEN the system uses three different default values depending on which code path is active:

| Location | Default Value |
|----------|--------------|
| `configs/config.yaml` (explicit) | `"manifest_fail"` |
| `_QC_DEFAULTS["quality_control"]["grobid_integration"]` in `utils/config_utils.py` | `"fallback"` |
| `load_openai_config()` return dict key `grobid_failure_behavior` in `utils/config_utils.py` | `"fallback"` |

The `configs/config.yaml` value is authoritative because: (a) it is the operator-facing configuration file, (b) the comment next to it documents the valid choices (`fallback | manifest_fail`), and (c) `manifest_fail` is the safer default (fail loudly rather than silently degrade). The other two locations SHALL be aligned to `"manifest_fail"`.

**Bug 8 — Medium: `import requests` at module level in `grobid_manager.py`**

1.8 WHEN any module that imports `grobid_manager` (including `main.py` via `from utils.grobid_manager import GrobidServerManager`) is loaded THEN the system raises `ImportError: No module named 'requests'` if the `requests` package is not installed, even when GROBID is not used, because `import requests` appears unconditionally at the top of `utils/grobid_manager.py` rather than inside the methods that actually use it.

**Bug 9 — Low: Unused `asdict` import in `validate_context.py`**

1.9 WHEN `quality_control/validate_context.py` is imported THEN the system imports `from dataclasses import asdict` even though `asdict` is never called anywhere in the file — `_qc_bundle_serializer` manually constructs the dict instead.

**Bug 10 — High: Default `TextProcessor` subclass cannot fulfill pipeline contract**

1.10 WHEN the QC pipeline loads the default text processor (`ScispaCySentenceSegment` as configured in `_QC_DEFAULTS`), the loaded instance:
- Implements ONLY `tokenize_sentences()`
- Raises `NotImplementedError` for `normalize()`, `tokenize_words()`, `clean_ocr()`, `compare()`, and `extract_keywords()`

This means:
- `TextFidelityConcern.reconcile()` calls `text_processor.compare(primary, reference)` → **crashes**
- `SectionVerificationConcern.reconcile()` calls `text_processor.compare(heading, ref_text)` → **crashes**
- `scan_detector.classify_page()` calls `text_processor.clean_ocr(text)` → **crashes**

The design choice of `SentenceSegment` raising `NotImplementedError` for non-segmentation methods is intentional for single-purpose backends, but the **pipeline requires a composite implementation** that covers all six methods. No such implementation ships with the codebase.

**Bug 11 — Security: Live API key hardcoded in `configs/config.yaml`**

1.11 WHEN `configs/config.yaml` is read from version control THEN the system exposes a live-looking OpenAI API key (`sk-proj-T0khx…`) committed in plain text under `openai.api_key`, instead of an empty string with a comment directing operators to use the `OPENAI_API_KEY` environment variable.

**Bug 12 — High: Injectable completeness gaps prevent end-to-end pipeline execution**

1.12 WHEN `main.py` is executed for a complete pipeline run, the following injectable/default gaps prevent successful end-to-end execution:

| Gap | Location | Impact |
|-----|----------|--------|
| No composite `TextProcessor` subclass | `text_processing/` | Reconciler crashes on `compare()`, scan detector crashes on `clean_ocr()` |
| `task_quality_scaffold` returns placeholder data | `quality_control/checks/task_quality.py` | All 8 task-quality metrics report `{"status": "scaffolded", "value": None}` — no real computation |
| `QualityReport.passes_check()` unconditionally returns `True` | `quality_control/builtin_impls/quality_report.py` | The base `passes_check()` is a no-op; only `ExtractionCoverageReport` actually computes metrics. Any code path that uses `QualityReport` directly will pass all branches regardless of quality |
| `_QC_DEFAULTS["adjudicator"]["strategy"]` set to `"placeholder"` | `utils/config_utils.py` | Config value is never consumed by code (the pipeline hardcodes `AdjudicationDecision()`), but signals incomplete design intent to operators reading the config |

The most critical of these is the missing composite `TextProcessor`. Without it, the reconciler and scan detector cannot function.

---


### Expected Behavior (Correct)

**Bug 1 — Concrete class loaded via `importlib` in `extraction_pipeline.py`**

2.1 WHEN `build_qc_bundle()` is called THEN the system SHALL load the concrete `TextProcessor` subclass by resolving `tp_cfg["class"]` via `importlib` (exactly as `_load_text_processor()` in `quality_control/quality_control.py` already does), instantiate it with `cls(config=tp_cfg)`, and proceed without raising `TypeError`.

**Bug 2 — Concrete class loaded via `importlib` in `reconciler.py` fallback**

2.2 WHEN `reconcile()` is called and `text_processor` is `None` THEN the system SHALL load the default concrete class (`text_processing.base.ScispaCySentenceSegment` or the new composite class from Bug 12) via `importlib` using the same pattern as `_load_text_processor()`, so that no `TypeError` is raised. The fallback `text_processor = TextProcessor()` line SHALL be removed entirely.

**Bug 3 — `pipeline` package importable without `openai` installed**

2.3 WHEN any code executes `import pipeline` or `from pipeline.extraction_pipeline import build_qc_bundle` without the `openai` package installed THEN the system SHALL import successfully, because the `from agents.openai.api_client import …` statement in `pipeline/pdf_processor.py` SHALL be moved inside the function bodies that actually use it (lazy import), matching the lazy-import convention used for all other heavy optional dependencies in the codebase. Additionally, `pipeline/orchestrator.py` line `from . import pdf_processor` SHALL be deferred or guarded so that `pipeline/__init__.py` does not transitively trigger the eager import.

**Bug 4 — Correct `extraction_map_path` in `configs/config.yaml`**

2.4 WHEN the pipeline starts and reads `configs/config.yaml` THEN the system SHALL resolve `EXTRACTION_MAP` to the existing file `configs/extraction_map.json` because `extraction_map_path` SHALL be set to `"configs/extraction_map.json"` (with the `s`). The two-step fallback in `utils/path_utils.py` will then resolve correctly on the first attempt.

**Bug 5 — Dead first predicate removed from `_pdf_reconciler_fn`**

2.5 WHEN `_pdf_reconciler_fn` searches for the secondary branch THEN the system SHALL use a single predicate matching against `b.extractor` (which aliases `b.source`), removing the dead `str(b.index).lower()` predicate entirely. The result SHALL be functionally identical to the current behavior (the secondary branch IS found via the fallback predicate), but with clearer code and no wasted iteration.

**Bug 6 — Call site of `_load_text_processor` logs and propagates errors**

2.6 WHEN `_load_text_processor` raises any exception THEN the call site in `run_quality_control()` SHALL:
1. Log the exception at `ERROR` level with the class path and exception message
2. Re-raise the exception (fail fast) rather than silently degrading to `text_processor = None`

The bare `except Exception: text_processor = None` pattern SHALL be removed. If test environments need to run without a text processor, they SHALL explicitly configure a test-safe concrete class (e.g. `NLTKPunktSentenceSegment`) rather than relying on silent failure.

**Bug 7 — Single canonical default for `grobid_integration.failure_behavior`**

2.7 WHEN `failure_behavior` is not explicitly set in `configs/config.yaml` THEN the system SHALL use a single consistent default value (`"manifest_fail"`) across all three locations:
- `configs/config.yaml` (already correct)
- `_QC_DEFAULTS["quality_control"]["grobid_integration"]["failure_behavior"]` in `utils/config_utils.py` → change from `"fallback"` to `"manifest_fail"`
- `load_openai_config()` fallback in `grobid_integration_cfg.get("failure_behavior", ...)` → change from `"fallback"` to `"manifest_fail"`

**Bug 8 — `requests` imported lazily inside methods in `grobid_manager.py`**

2.8 WHEN `utils/grobid_manager.py` is imported THEN the system SHALL NOT import `requests` at module level; instead `import requests` SHALL appear only inside the method bodies that use it (primarily `_is_server_alive` and any other methods calling `requests.get`), matching the lazy-import convention used for all other heavy optional dependencies in the codebase.

**Bug 9 — Unused `asdict` import removed from `validate_context.py`**

2.9 WHEN `quality_control/validate_context.py` is imported THEN the system SHALL NOT import `asdict` from `dataclasses`, because it is unused and its presence is misleading.

**Bug 10 — A composite `TextProcessor` implementation covers all 6 abstract methods**

2.10 The system SHALL ship a concrete `TextProcessor` subclass (suggested name: `CompositeTextProcessor` or `DefaultTextProcessor`) that implements all six abstract methods with real, functional logic:

| Method | Required Behavior |
|--------|-------------------|
| `normalize(text)` | Apply NFKC unicode normalization and whitespace collapsing |
| `tokenize_words(text)` | Split on whitespace boundaries (or use a real tokenizer) |
| `tokenize_sentences(text)` | Delegate to a sentence segmentation backend (configurable) |
| `clean_ocr(text)` | Remove replacement chars (U+FFFD), control chars, collapse whitespace |
| `compare(a, b)` | Return normalized Levenshtein similarity ratio in [0.0, 1.0] |
| `extract_keywords(text)` | Return non-stopword tokens |

This class SHALL be:
- The default value of `_QC_DEFAULTS["text_processor"]["class"]`
- The default loaded by `_load_text_processor()` when no class is configured
- The fallback in `reconciler.reconcile()` when `text_processor` is `None`

Each method SHALL produce **logically correct output** — no `NotImplementedError`, no placeholder returns, no trivial/nonsensical implementations. The `compare()` method in particular SHALL use a real string similarity algorithm (Levenshtein ratio via `difflib.SequenceMatcher` or equivalent) because it directly determines reconciliation confidence scores.

**Bug 11 — API key field empty with env-var comment in `configs/config.yaml`**

2.11 WHEN `configs/config.yaml` is read from version control THEN the system SHALL expose an empty string (`""`) for `openai.api_key` with a comment directing operators to set the `OPENAI_API_KEY` environment variable, and no live or real-looking key SHALL appear in the committed file. The leaked key SHALL be revoked immediately.

**Bug 12 — All pipeline-required injectables have complete, functional defaults**

2.12 The system SHALL ensure that every injectable dependency in the pipeline has a **complete, functional, non-placeholder default** so that `main.py` can execute a full end-to-end run (given valid PDFs and an OpenAI API key) without hitting `NotImplementedError`, scaffold placeholders, or silent degradation.

Specific requirements:

| Injectable | Required State |
|------------|---------------|
| `TextProcessor` (via `_load_text_processor`) | Composite class with all 6 methods functional (see Bug 10) |
| `TextFidelityConcern` (DEFAULT_TEXT_FIDELITY) | Already functional — no change needed |
| `SectionVerificationConcern` (DEFAULT_SECTION_VERIFICATION) | Already functional — no change needed |
| `TableFigureMergeConcern` (DEFAULT_TABLE_FIGURE_MERGE) | Already functional — no change needed |
| `AdjudicationDecision` | Already functional (majority-vote) — no change needed |
| `InterRaterReport` | Already functional (pairwise agreement) — no change needed |
| `ExtractionCoverageReport` | Already functional (8 metrics) — no change needed |
| `LexicalMatcher` | Already functional for `search()` — no change needed (other ABC methods unused) |
| `SemanticMatcher` | Already functional for `search()` — no change needed (other ABC methods unused) |
| `_QC_DEFAULTS["adjudicator"]["strategy"]` | Rename from `"placeholder"` to a value that reflects reality (e.g. `"majority_vote"`) |
| `build_task_quality_scaffold()` | Replace scaffold with real metric computation OR clearly document that this is a future feature and remove `enabled: True` default |

**Absolute prohibition:** No injectable used in a runtime code path may return hardcoded placeholder values (e.g. `{"status": "scaffolded", "value": None}`), raise `NotImplementedError`, unconditionally pass/fail, or produce logically meaningless output. If a feature is not yet implemented, it SHALL be gated behind `enabled: False` in config and the code path SHALL be unreachable by default.

---


### Unchanged Behavior (Regression Prevention)

3.1 WHEN `build_qc_bundle()` is called with a valid config that specifies a concrete `TextProcessor` class THEN the system SHALL CONTINUE TO instantiate that class and pass it to `scan_detector.classify_page` and the QC pipeline exactly as before.

3.2 WHEN `reconcile()` is called with an explicit non-`None` `text_processor` argument THEN the system SHALL CONTINUE TO use that argument unchanged, with no alteration to the reconciliation logic.

3.3 WHEN `from pipeline.extraction_pipeline import build_qc_bundle` is called in an environment where `openai` IS installed THEN the system SHALL CONTINUE TO import and execute `build_qc_bundle` without error, and the full pipeline (including LLM extraction) SHALL CONTINUE TO work as before.

3.4 WHEN `EXTRACTION_MAP` is read at startup after the path is corrected THEN the system SHALL CONTINUE TO resolve the path to the same `configs/extraction_map.json` file that was always intended, and all downstream consumers of `EXTRACTION_MAP` SHALL CONTINUE TO work without modification.

3.5 WHEN `_pdf_reconciler_fn` processes a branch list that contains a `pdfplumber` or `pymupdf` branch THEN the system SHALL CONTINUE TO select that branch as the secondary branch and pass it to `reconciler.reconcile()` as before; the fix SHALL NOT change which branch is selected, only how the selection predicate is expressed (removing dead code).

3.6 WHEN `_load_text_processor` successfully loads the configured class THEN the system SHALL CONTINUE TO return the instantiated object and the QC pipeline SHALL CONTINUE TO use it exactly as before.

3.7 WHEN `failure_behavior` IS explicitly set in `configs/config.yaml` THEN the system SHALL CONTINUE TO use the explicitly configured value, overriding any default.

3.8 WHEN `GrobidServerManager` is used in an environment where `requests` IS installed THEN the system SHALL CONTINUE TO call `requests.get` in `_is_server_alive` and all GROBID lifecycle management SHALL CONTINUE TO work as before.

3.9 WHEN `validate_qc_context_input` is called THEN the system SHALL CONTINUE TO perform all six pre-flight checks and raise `ValidationError` on failure, with no change in behavior from removing the unused `asdict` import.

3.10 WHEN `scan_detector.classify_page` is called with a concrete `TextProcessor` that correctly implements `clean_ocr` THEN the system SHALL CONTINUE TO classify pages using the five-stage algorithm with no change to thresholds, stage order, or return type.

3.11 WHEN `load_openai_config` reads `openai.api_key` from `configs/config.yaml` THEN the system SHALL CONTINUE TO prefer the `OPENAI_API_KEY` environment variable over the config-file value, so that operators who already use the env var are unaffected by the key field being cleared.

3.12 WHEN the new composite `TextProcessor` is loaded as the default THEN its `tokenize_sentences()` method SHALL produce results equivalent to the current `ScispaCySentenceSegment.tokenize_sentences()` (i.e., it MAY delegate to the same scispaCy backend or to a configurable backend). Existing sentence segmentation behavior SHALL NOT regress.

3.13 WHEN `LexicalMatcher.search()` or `SemanticMatcher.search()` is called THEN these SHALL CONTINUE TO function exactly as before. The new composite `TextProcessor` does NOT replace these matcher classes — they serve different purposes (search vs. text processing).

3.14 WHEN `run_pipeline()` (the generic pipeline) is called with custom stage callables THEN the generic pipeline SHALL CONTINUE TO work with any user-provided callables, with no assumption that the new composite `TextProcessor` is available. The composite class is a default for the PDF-specific pipeline only.

---


## Bug Condition Pseudocode

### Bugs 1 & 2 — ABC Instantiation

```pascal
FUNCTION isBugCondition_ABC(call_site)
  INPUT: call_site — the expression used to create a TextProcessor instance
  OUTPUT: boolean

  RETURN call_site = "TextProcessor(config=...)" OR call_site = "TextProcessor()"
         // i.e., the ABC is called directly rather than a concrete subclass
END FUNCTION

// Fix Checking
FOR ALL call_site WHERE isBugCondition_ABC(call_site) DO
  result ← execute(call_site_after_fix)
  ASSERT no TypeError raised
  ASSERT isinstance(result, TextProcessor)
  ASSERT NOT type(result) IS TextProcessor  // must be a concrete subclass
END FOR

// Preservation Checking
FOR ALL call_site WHERE NOT isBugCondition_ABC(call_site) DO
  ASSERT execute(call_site_before_fix) = execute(call_site_after_fix)
END FOR
```

### Bug 3 — Eager OpenAI Import

```pascal
FUNCTION isBugCondition_EagerImport(env)
  INPUT: env — the Python environment
  OUTPUT: boolean

  RETURN "openai" NOT IN env.installed_packages
         AND "pipeline" IN env.import_targets
END FUNCTION

// Fix Checking
FOR ALL env WHERE isBugCondition_EagerImport(env) DO
  result ← import("pipeline")
  ASSERT no ImportError raised
  result ← import("pipeline.extraction_pipeline")
  ASSERT no ImportError raised
END FOR

// Preservation Checking
FOR ALL env WHERE "openai" IN env.installed_packages DO
  ASSERT import("pipeline") succeeds
  ASSERT pipeline.run_pipeline is callable
  ASSERT pipeline.pdf_processor.process_pdf is callable
END FOR
```

### Bug 4 — Wrong Config Path

```pascal
FUNCTION isBugCondition_WrongPath(config_yaml)
  INPUT: config_yaml — the loaded config dict
  OUTPUT: boolean

  RETURN config_yaml["extraction_map_path"] = "config/extraction_map.json"
         // missing 's' in "configs"
END FUNCTION

// Fix Checking
FOR ALL config_yaml WHERE isBugCondition_WrongPath(config_yaml) DO
  fix: set extraction_map_path = "configs/extraction_map.json"
  ASSERT Path(PROJECT_ROOT / "configs/extraction_map.json").exists() = True
  ASSERT EXTRACTION_MAP resolves to that path
END FOR
```

### Bug 5 — Dead Branch Predicate

```pascal
FUNCTION isBugCondition_DeadPredicate(branch)
  INPUT: branch — a Candidate object
  OUTPUT: boolean

  RETURN branch.source IN {"pdfplumber", "pymupdf"}
         AND str(branch.index).lower() NOT IN {"pdfplumber", "pymupdf"}
         // always true for integer indices
END FUNCTION

// Fix Checking: remove dead first predicate, use only b.extractor
FOR ALL branch WHERE isBugCondition_DeadPredicate(branch) DO
  secondary ← find_secondary_branch_FIXED([branch])
  ASSERT secondary IS branch  // same result as before, just no dead code
END FOR

// Preservation: functional result unchanged
FOR ALL branch_list DO
  ASSERT find_secondary_branch_FIXED(branch_list) = find_secondary_branch_OLD(branch_list)
END FOR
```

### Bug 6 — Silent Error Swallowing at Call Site

```pascal
FUNCTION isBugCondition_SilentSwallow(run_quality_control_code)
  INPUT: run_quality_control_code — the source code of run_quality_control()
  OUTPUT: boolean

  RETURN run_quality_control_code CONTAINS pattern:
         "try:.*_load_text_processor.*except Exception:.*text_processor = None"
END FUNCTION

// Fix Checking
FOR ALL env WHERE _load_text_processor raises ImportError DO
  ASSERT run_quality_control() raises ImportError (fail fast)
  ASSERT logger.error was called with class path and exception details
END FOR

// Preservation
FOR ALL env WHERE _load_text_processor succeeds DO
  ASSERT text_processor IS NOT None
  ASSERT run_quality_control() proceeds normally
END FOR
```

### Bug 7 — Inconsistent Failure Behavior Defaults

```pascal
FUNCTION isBugCondition_InconsistentDefault()
  OUTPUT: boolean

  val_config ← configs/config.yaml["quality_control"]["grobid_integration"]["failure_behavior"]
  val_defaults ← _QC_DEFAULTS["quality_control"]["grobid_integration"]["failure_behavior"]
  val_openai ← load_openai_config() fallback for "failure_behavior"

  RETURN NOT (val_config = val_defaults AND val_defaults = val_openai)
END FUNCTION

// Fix Checking
ASSERT _QC_DEFAULTS[...]["failure_behavior"] = "manifest_fail"
ASSERT load_openai_config() uses default "manifest_fail"
ASSERT configs/config.yaml uses "manifest_fail"
```

### Bug 8 — Eager Requests Import

```pascal
FUNCTION isBugCondition_EagerRequests(env)
  INPUT: env — the Python environment
  OUTPUT: boolean

  RETURN "requests" NOT IN env.installed_packages
         AND "utils.grobid_manager" IN env.import_targets
END FUNCTION

// Fix Checking
FOR ALL env WHERE isBugCondition_EagerRequests(env) DO
  result ← import("utils.grobid_manager")
  ASSERT no ImportError raised
END FOR

// Preservation
FOR ALL env WHERE "requests" IN env.installed_packages DO
  mgr ← GrobidServerManager(config)
  ASSERT mgr._is_server_alive() calls requests.get successfully
END FOR
```

### Bug 9 — Unused Import

```pascal
FUNCTION isBugCondition_UnusedImport(source_file)
  INPUT: source_file — text of validate_context.py
  OUTPUT: boolean

  RETURN "from dataclasses import asdict" IN source_file
         AND "asdict(" NOT IN source_file
         AND "asdict)" NOT IN source_file
END FUNCTION

// Fix: remove the import line
// Preservation: all 6 validate_qc_context_input checks still pass/fail identically
```

### Bug 10 — Incomplete TextProcessor Contract

```pascal
FUNCTION isBugCondition_IncompleteTP(text_processor_instance)
  INPUT: text_processor_instance — the default loaded TP
  OUTPUT: boolean

  methods ← ["normalize", "tokenize_words", "tokenize_sentences",
             "clean_ocr", "compare", "extract_keywords"]
  FOR method IN methods DO
    TRY:
      call text_processor_instance.method(sample_input)
    CATCH NotImplementedError:
      RETURN True
  RETURN False
END FUNCTION

// Fix Checking
tp ← _load_text_processor(default_config)
FOR method IN ["normalize", "tokenize_words", "tokenize_sentences",
               "clean_ocr", "compare", "extract_keywords"] DO
  result ← tp.method(sample_input)
  ASSERT no NotImplementedError raised
  ASSERT result is logically meaningful (not empty, not placeholder)
END FOR

// Specific contract checks
ASSERT tp.compare("hello world", "hello world") = 1.0
ASSERT tp.compare("hello", "goodbye") < 1.0
ASSERT tp.compare("", "") = 1.0  // edge case
ASSERT len(tp.tokenize_sentences("First. Second.")) >= 2
ASSERT tp.clean_ocr("hello\ufffdworld") does not contain "\ufffd"
ASSERT len(tp.extract_keywords("the quick brown fox")) >= 2  // excludes stopwords
```

### Bug 11 — Hardcoded API Key

```pascal
FUNCTION isBugCondition_HardcodedKey(config_yaml_text)
  INPUT: config_yaml_text — raw text of configs/config.yaml
  OUTPUT: boolean

  RETURN config_yaml_text CONTAINS pattern "sk-proj-[A-Za-z0-9_-]{20,}"
         OR config_yaml_text CONTAINS pattern "sk-[A-Za-z0-9_-]{40,}"
END FUNCTION

// Fix Checking
FOR ALL config_yaml_text WHERE isBugCondition_HardcodedKey(config_yaml_text) DO
  ASSERT no real-looking API key present in committed file
  ASSERT api_key field = "" OR api_key field = null
END FOR

// Preservation
FOR ALL env DO
  ASSERT load_openai_config()["api_key"] = env.OPENAI_API_KEY_env_var
         OR load_openai_config()["api_key"] = ""
END FOR
```

### Bug 12 — Injectable Completeness

```pascal
FUNCTION isBugCondition_IncompleteInjectables()
  OUTPUT: boolean

  // Check 1: composite TextProcessor exists and is default
  tp ← _load_text_processor(default_config)
  IF tp raises OR tp.compare raises NotImplementedError:
    RETURN True

  // Check 2: task_quality_scaffold returns non-placeholder data when enabled
  IF _QC_DEFAULTS["task_quality_scaffold"]["enabled"] = True:
    scaffold ← build_task_quality_scaffold()
    IF scaffold["status"] = "not_computed" OR scaffold["status"] = "scaffolded":
      RETURN True

  // Check 3: adjudicator config value is not "placeholder"
  IF _QC_DEFAULTS["adjudicator"]["strategy"] = "placeholder":
    RETURN True

  RETURN False
END FUNCTION

// Fix Checking
ASSERT _load_text_processor(default_config) returns functional composite TP
ASSERT _QC_DEFAULTS["adjudicator"]["strategy"] != "placeholder"
ASSERT _QC_DEFAULTS["task_quality_scaffold"]["enabled"] = False
       OR build_task_quality_scaffold() returns real metrics

// End-to-end validation (integration)
// Given: valid PDFs in input/, OPENAI_API_KEY set, GROBID running
result ← asyncio.run(main())
ASSERT result exits with code 0
ASSERT no NotImplementedError in any stack trace
ASSERT no "scaffolded" values in output JSON
ASSERT reconciler produces non-empty UnifiedRecord for each PDF
```

---

## Appendix: File Reference Map

| Bug | Files to Modify |
|-----|-----------------|
| 1 | `pipeline/extraction_pipeline.py` (line ~80) |
| 2 | `quality_control/reconciler.py` (strategy-defaults block) |
| 3 | `pipeline/pdf_processor.py` (line 6), optionally `pipeline/orchestrator.py` (line 10) |
| 4 | `configs/config.yaml` (line 57: `extraction_map_path`) |
| 5 | `quality_control/quality_control.py` (`_pdf_reconciler_fn`, lines ~436–442) |
| 6 | `quality_control/quality_control.py` (`run_quality_control`, lines ~345–347) |
| 7 | `utils/config_utils.py` (`_QC_DEFAULTS` line ~87, `load_openai_config` line ~295) |
| 8 | `utils/grobid_manager.py` (line 3) |
| 9 | `quality_control/validate_context.py` (line 31) |
| 10 | New file: `text_processing/composite.py` (or extend `text_processing/base.py`) |
| 11 | `configs/config.yaml` (line 4), `.gitignore` (add config key pattern) |
| 12 | `text_processing/composite.py`, `utils/config_utils.py`, `quality_control/checks/task_quality.py` |

---

## Appendix: Existing Test Coverage

The following test modules already exercise related functionality and SHOULD
continue to pass after all fixes are applied:

- `tests/utils/test_quality_control_config.py` — validates `_QC_DEFAULTS` structure and `load_qc_config` deep-merge
- `tests/quality_control/` — exercises rater, IAA, adjudicator, reconciler, concerns, checks
- `tests/agents/openai/` — exercises API client (should be unaffected by lazy import fix)
- `tests/test_migration_artifact_scrub_preservation.py` — validates `load_qc_config` merge behavior

If any of these tests relied on the bare `except Exception` in Bug 6 to
silently suppress `_load_text_processor` failures, those tests SHALL be updated
to either: (a) configure a valid text processor class, or (b) mock
`_load_text_processor` explicitly.
