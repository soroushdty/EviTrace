# Bugfix Requirements Document

## Introduction

A full-repository audit of EviTrace identified 11 bugs spanning two critical
runtime crashes (direct instantiation of the `TextProcessor` ABC), one
security issue (live API key committed to `configs/config.yaml`), four
high-severity defects (wrong config path, dead branch-lookup code, eager
`openai` import, incomplete `TextProcessor` contract), and four medium/low
issues (inconsistent `failure_behavior` defaults, eager `requests` import,
unused `asdict` import, and silent error swallowing in `_load_text_processor`).

Together these bugs prevent the pipeline from starting in clean environments,
expose credentials in version control, silently mis-route secondary branches
during reconciliation, and make the configured text-processor backend
unreachable without any operator-visible error.

---

## Bug Analysis

### Current Behavior (Defect)

**Bug 1 — Critical: `TextProcessor` ABC instantiated directly in `extraction_pipeline.py`**

1.1 WHEN `build_qc_bundle()` is called THEN the system raises `TypeError: Can't instantiate abstract class TextProcessor with abstract methods …` at `pipeline/extraction_pipeline.py:80` because `TextProcessor(config=tp_cfg)` attempts to instantiate the ABC directly instead of loading the concrete class named in `tp_cfg["class"]`.

**Bug 2 — Critical: `TextProcessor` ABC instantiated directly in `reconciler.py` fallback**

1.2 WHEN `reconcile()` is called and `text_processor` is `None` THEN the system raises `TypeError: Can't instantiate abstract class TextProcessor with abstract methods …` at `quality_control/reconciler.py:391` because the fallback `text_processor = TextProcessor()` attempts to instantiate the ABC directly.

**Bug 3 — High: Importing the `pipeline` package eagerly pulls in `agents.openai.api_client`**

1.3 WHEN any code executes `import pipeline` or `from pipeline.extraction_pipeline import build_qc_bundle` THEN the system raises `ImportError` (or `ModuleNotFoundError`) if the `openai` package is not installed, because `pipeline/__init__.py` → `orchestrator.py` → `pdf_processor.py` contains `from agents.openai.api_client import …` at module level, even though the PDF/QC extraction path is documented as OpenAI-independent.

**Bug 4 — High: Wrong `extraction_map_path` in `configs/config.yaml`**

1.4 WHEN the pipeline starts and reads `configs/config.yaml` THEN the system resolves `EXTRACTION_MAP` to a non-existent path (`configs/config/extraction_map.json`) because `extraction_map_path` is set to `"config/extraction_map.json"` (missing the `s`), and the two-step fallback in `path_utils.py` prepends `configs/` to the already-wrong value rather than correcting it.

**Bug 5 — High: Secondary branch lookup compares integer index to extractor name strings**

1.5 WHEN `_pdf_reconciler_fn` in `quality_control/quality_control.py` searches for the secondary branch THEN the system always falls through the first predicate (`str(b.index).lower() in {"pdfplumber", "pymupdf"}`) without a match, because `b.index` is an integer (e.g. `1`) and `str(1).lower()` is `"1"`, which is never equal to `"pdfplumber"` or `"pymupdf"`.

**Bug 6 — High: `_load_text_processor` silently swallows all load errors**

1.6 WHEN `_load_text_processor` in `quality_control/quality_control.py` encounters any error (wrong class path, missing dependency, import error) THEN the system silently sets `text_processor = None` with no log message, no exception propagation, and no operator-visible indication that the configured backend failed to load.

**Bug 7 — Medium: Inconsistent `grobid_integration.failure_behavior` defaults across three locations**

1.7 WHEN `failure_behavior` is not explicitly set in `configs/config.yaml` THEN the system uses three different default values depending on which code path is active: `"manifest_fail"` in `configs/config.yaml`, `"fallback"` in `_QC_DEFAULTS` inside `config_utils.py`, and `"fallback"` hardcoded in `load_openai_config` — making the effective default unpredictable and environment-dependent.

**Bug 8 — Medium: `import requests` at module level in `grobid_manager.py`**

1.8 WHEN any module that imports `grobid_manager` (including `main.py` and `pdf_extractor.py`) is loaded THEN the system raises `ImportError: No module named 'requests'` if the `requests` package is not installed, even when GROBID is not used, because `import requests` appears unconditionally at the top of `utils/grobid_manager.py` rather than inside the methods that actually use it.

**Bug 9 — Low: Unused `asdict` import in `validate_context.py`**

1.9 WHEN `quality_control/validate_context.py` is imported THEN the system imports `from dataclasses import asdict` even though `asdict` is never called anywhere in the file — `_qc_bundle_serializer` manually constructs the dict instead.

**Bug 10 — Medium: No concrete `TextProcessor` subclass covers all 6 abstract methods**

1.10 WHEN `scan_detector.classify_page` calls `text_processor.clean_ocr()`, or `TextFidelityConcern.reconcile` calls `text_processor.compare()`, or `SectionVerificationConcern.reconcile` calls `text_processor.compare()` THEN the system raises `NotImplementedError` because every shipped concrete subclass (`LexicalMatcher`, `SemanticMatcher`, `SentenceSegment` subclasses) implements only a subset of the six abstract methods and raises `NotImplementedError` for the rest.

**Bug 11 — Security: Live API key hardcoded in `configs/config.yaml`**

1.11 WHEN `configs/config.yaml` is read from version control THEN the system exposes a live-looking OpenAI API key (`sk-proj-T0khx…`) committed in plain text under `openai.api_key`, instead of an empty string with a comment directing operators to use the `OPENAI_API_KEY` environment variable.

---

### Expected Behavior (Correct)

**Bug 1 — Concrete class loaded via `importlib` in `extraction_pipeline.py`**

2.1 WHEN `build_qc_bundle()` is called THEN the system SHALL load the concrete `TextProcessor` subclass by resolving `tp_cfg["class"]` via `importlib` (exactly as `_load_text_processor()` in `quality_control/quality_control.py` already does), instantiate it with `cls(config=tp_cfg)`, and proceed without raising `TypeError`.

**Bug 2 — Concrete class loaded via `importlib` in `reconciler.py` fallback**

2.2 WHEN `reconcile()` is called and `text_processor` is `None` THEN the system SHALL NOT fall back to `TextProcessor()` directly; instead the fallback SHALL either be removed (relying on callers to always supply a concrete instance) or SHALL load a concrete default class via `importlib`, so that no `TypeError` is raised.

**Bug 3 — `pipeline` package importable without `openai` installed**

2.3 WHEN any code executes `import pipeline` or `from pipeline.extraction_pipeline import build_qc_bundle` without the `openai` package installed THEN the system SHALL import successfully, because the `from agents.openai.api_client import …` statement in `pipeline/pdf_processor.py` SHALL be moved inside the function bodies that actually use it (lazy import), matching the lazy-import convention used for all other heavy optional dependencies in the codebase.

**Bug 4 — Correct `extraction_map_path` in `configs/config.yaml`**

2.4 WHEN the pipeline starts and reads `configs/config.yaml` THEN the system SHALL resolve `EXTRACTION_MAP` to the existing file `configs/extraction_map.json` because `extraction_map_path` SHALL be set to `"configs/extraction_map.json"` (with the `s`).

**Bug 5 — Secondary branch lookup uses `b.extractor` (or `b.source`) not `str(b.index)`**

2.5 WHEN `_pdf_reconciler_fn` searches for the secondary branch THEN the system SHALL match against `b.extractor` (which aliases `b.source`) rather than `str(b.index)`, so that a branch with `source="pdfplumber"` or `source="pymupdf"` is correctly identified as the secondary branch.

**Bug 6 — `_load_text_processor` logs errors and propagates them**

2.6 WHEN `_load_text_processor` encounters any error THEN the system SHALL at minimum log the error at `ERROR` level (including the class path and the exception message) so that operators can diagnose misconfiguration; the error SHOULD propagate so that the pipeline fails fast rather than silently continuing with `text_processor = None`.

**Bug 7 — Single canonical default for `grobid_integration.failure_behavior`**

2.7 WHEN `failure_behavior` is not explicitly set in `configs/config.yaml` THEN the system SHALL use a single consistent default value (`"manifest_fail"`) across all three locations: `configs/config.yaml`, `_QC_DEFAULTS` in `config_utils.py`, and `load_openai_config` in `config_utils.py`.

**Bug 8 — `requests` imported lazily inside methods in `grobid_manager.py`**

2.8 WHEN `utils/grobid_manager.py` is imported THEN the system SHALL NOT import `requests` at module level; instead `import requests` SHALL appear only inside the method bodies that use it (`_is_server_alive`), matching the lazy-import convention used for all other heavy optional dependencies in the codebase.

**Bug 9 — Unused `asdict` import removed from `validate_context.py`**

2.9 WHEN `quality_control/validate_context.py` is imported THEN the system SHALL NOT import `asdict` from `dataclasses`, because it is unused and its presence is misleading.

**Bug 10 — A concrete `TextProcessor` implementation covers all 6 abstract methods**

2.10 WHEN `scan_detector.classify_page` calls `text_processor.clean_ocr()`, or any concern strategy calls `text_processor.compare()` THEN the system SHALL NOT raise `NotImplementedError`; the concrete class used at those call sites SHALL implement all six abstract methods (`normalize`, `tokenize_words`, `tokenize_sentences`, `clean_ocr`, `compare`, `extract_keywords`) without raising `NotImplementedError`.

**Bug 11 — API key field empty with env-var comment in `configs/config.yaml`**

2.11 WHEN `configs/config.yaml` is read from version control THEN the system SHALL expose an empty string (`""`) for `openai.api_key` with a comment directing operators to set the `OPENAI_API_KEY` environment variable, and no live or real-looking key SHALL appear in the committed file.

---

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `build_qc_bundle()` is called with a valid config that specifies a concrete `TextProcessor` class THEN the system SHALL CONTINUE TO instantiate that class and pass it to `scan_detector.classify_page` and the QC pipeline exactly as before.

3.2 WHEN `reconcile()` is called with an explicit non-`None` `text_processor` argument THEN the system SHALL CONTINUE TO use that argument unchanged, with no alteration to the reconciliation logic.

3.3 WHEN `from pipeline.extraction_pipeline import build_qc_bundle` is called in an environment where `openai` IS installed THEN the system SHALL CONTINUE TO import and execute `build_qc_bundle` without error, and the full pipeline (including LLM extraction) SHALL CONTINUE TO work as before.

3.4 WHEN `EXTRACTION_MAP` is read at startup after the path is corrected THEN the system SHALL CONTINUE TO resolve the path to the same `configs/extraction_map.json` file that was always intended, and all downstream consumers of `EXTRACTION_MAP` SHALL CONTINUE TO work without modification.

3.5 WHEN `_pdf_reconciler_fn` processes a branch list that contains a `pdfplumber` or `pymupdf` branch THEN the system SHALL CONTINUE TO select that branch as the secondary branch and pass it to `reconciler.reconcile()` as before; the fix SHALL NOT change which branch is selected, only how the selection predicate is evaluated.

3.6 WHEN `_load_text_processor` successfully loads the configured class THEN the system SHALL CONTINUE TO return the instantiated object and the QC pipeline SHALL CONTINUE TO use it exactly as before.

3.7 WHEN `failure_behavior` IS explicitly set in `configs/config.yaml` THEN the system SHALL CONTINUE TO use the explicitly configured value, overriding any default.

3.8 WHEN `GrobidServerManager` is used in an environment where `requests` IS installed THEN the system SHALL CONTINUE TO call `requests.get` in `_is_server_alive` and all GROBID lifecycle management SHALL CONTINUE TO work as before.

3.9 WHEN `validate_qc_context_input` is called THEN the system SHALL CONTINUE TO perform all six pre-flight checks and raise `ValidationError` on failure, with no change in behavior from removing the unused `asdict` import.

3.10 WHEN `scan_detector.classify_page` is called with a concrete `TextProcessor` that correctly implements `clean_ocr` THEN the system SHALL CONTINUE TO classify pages using the five-stage algorithm with no change to thresholds, stage order, or return type.

3.11 WHEN `load_openai_config` reads `openai.api_key` from `configs/config.yaml` THEN the system SHALL CONTINUE TO prefer the `OPENAI_API_KEY` environment variable over the config-file value, so that operators who already use the env var are unaffected by the key field being cleared.

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

FOR ALL env WHERE isBugCondition_EagerImport(env) DO
  result ← import("pipeline")
  ASSERT no ImportError raised
END FOR
```

### Bug 4 — Wrong Config Path

```pascal
FUNCTION isBugCondition_WrongPath(config_yaml)
  INPUT: config_yaml — the loaded config dict
  OUTPUT: boolean

  RETURN config_yaml["extraction_map_path"] = "config/extraction_map.json"
END FUNCTION

FOR ALL config_yaml WHERE isBugCondition_WrongPath(config_yaml) DO
  ASSERT EXTRACTION_MAP.exists() = True
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

FOR ALL branch WHERE isBugCondition_DeadPredicate(branch) DO
  secondary ← find_secondary_branch([branch])
  ASSERT secondary IS branch
END FOR
```

### Bug 11 — Hardcoded API Key

```pascal
FUNCTION isBugCondition_HardcodedKey(config_yaml_text)
  INPUT: config_yaml_text — raw text of configs/config.yaml
  OUTPUT: boolean

  RETURN config_yaml_text CONTAINS pattern "sk-proj-[A-Za-z0-9_-]{20,}"
END FUNCTION

FOR ALL config_yaml_text WHERE isBugCondition_HardcodedKey(config_yaml_text) DO
  ASSERT no real-looking API key present in committed file
END FOR

// Preservation
FOR ALL env WHERE NOT isBugCondition_HardcodedKey(env.config_yaml_text) DO
  ASSERT load_openai_config()["api_key"] = env.OPENAI_API_KEY_env_var
         OR load_openai_config()["api_key"] = ""
END FOR
```
