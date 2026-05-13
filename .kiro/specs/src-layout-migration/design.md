# Design Document: src-layout-migration

## Overview

This design describes the technical approach for migrating EviTrace's seven source packages from the repository root into a `src/` directory, following the standard Python src layout convention. The migration is a structural refactoring that touches file locations, build configuration, test infrastructure, and documentation — but does not alter any application logic.

The src layout provides a clean separation between installable source code and project-level files (tests, configs, scripts). It prevents accidental imports from the working directory during development and aligns with modern Python packaging best practices (PEP 517/518, setuptools discovery).

### Design Decisions

1. **No backward-compatibility shims** — Old root-level package directories are deleted entirely. No `__init__.py` stubs or `sys.path` hacks remain at the old locations.
2. **Import statements unchanged** — Python import text (`from pipeline import ...`) stays the same. Resolution is handled by `pythonpath = "src"` in pytest config and the root `conftest.py` inserting `src/` into `sys.path`.
3. **Test directory gains `tests/src/` nesting** — Package-mirroring test subdirectories move under `tests/src/` to reflect the new source layout. Cross-cutting tests (`tests/steering/`, root-level `test_*.py`) stay in place.
4. **Two pre-existing test failures fixed in-flight** — The GROBID mock tests and the flagged fields CSV test are corrected as part of this migration to achieve a green suite.

## Architecture

### Pre-Migration Layout

```
EviTrace/
├── agents/
├── artifact_generation/
├── pdf_extractor/
├── pipeline/
├── quality_control/
├── text_processing/
├── utils/
├── tests/
│   ├── agents/
│   ├── pdf_extractor/
│   ├── pipeline/
│   ├── quality_control/
│   ├── steering/
│   ├── text_processing/
│   ├── utils/
│   └── test_dependency_directions.py
├── configs/
├── main.py
├── conftest.py
└── pyproject.toml
```

### Post-Migration Layout

```
EviTrace/
├── src/
│   ├── agents/
│   ├── artifact_generation/
│   ├── pdf_extractor/
│   ├── pipeline/
│   ├── quality_control/
│   ├── text_processing/
│   └── utils/
├── tests/
│   ├── src/
│   │   ├── agents/
│   │   ├── pdf_extractor/
│   │   ├── pipeline/
│   │   ├── quality_control/
│   │   ├── text_processing/
│   │   └── utils/
│   ├── steering/
│   ├── test_dependency_directions.py
│   ├── test_migration_artifact_scrub_bug_condition.py
│   └── test_migration_artifact_scrub_preservation.py
├── configs/
├── main.py
├── conftest.py
└── pyproject.toml
```

Key structural rules:
- `src/` has **no** `__init__.py` — it is a namespace container, not a package
- Each package under `src/` retains its existing `__init__.py` and internal structure byte-for-byte
- `tests/steering/` remains at `tests/steering/` (not under `tests/src/`)
- Root-level test files remain directly under `tests/`

## Components and Interfaces

### Component 1: File Relocation Engine

**Responsibility:** Move the seven source packages into `src/`.

| Operation | Detail |
|---|---|
| Move | `<root>/<pkg>/` → `src/<pkg>/` for each of the 7 packages |
| Verify | File count per package identical before/after |
| Clean | Remove empty root-level directories after move |
| Constraint | No `src/__init__.py` created |

### Component 2: Build Configuration Update

**Responsibility:** Update `pyproject.toml` and `conftest.py` files for the new layout.

#### pyproject.toml changes

```toml
[tool.pytest.ini_options]
pythonpath = "src"          # was "."
testpaths = ["tests"]       # unchanged
addopts = "--import-mode=importlib -m \"not slow\""  # unchanged
markers = [...]             # unchanged
```

#### Root conftest.py (new content)

```python
import sys
from pathlib import Path

_src_dir = str(Path(__file__).resolve().parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
```

#### src/pdf_extractor/conftest.py (updated)

```python
import sys
from pathlib import Path

# parent.parent of src/pdf_extractor/conftest.py → src/
_src_dir = str(Path(__file__).resolve().parent.parent)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
```

### Component 3: Test Directory Restructuring

**Responsibility:** Nest package-mirroring test directories under `tests/src/`.

| Current | New |
|---|---|
| `tests/agents/` | `tests/src/agents/` |
| `tests/pdf_extractor/` | `tests/src/pdf_extractor/` |
| `tests/pipeline/` | `tests/src/pipeline/` |
| `tests/quality_control/` | `tests/src/quality_control/` |
| `tests/text_processing/` | `tests/src/text_processing/` |
| `tests/utils/` | `tests/src/utils/` |
| `tests/steering/` | `tests/steering/` (unchanged) |
| `tests/test_*.py` | `tests/test_*.py` (unchanged) |

No `__init__.py` files are added to `tests/` or `tests/src/` — pytest's `importlib` import mode does not require them.

### Component 4: Import Path Fixup

**Responsibility:** Update file-path-based imports and `PROJECT_ROOT` computations.

#### Standard imports (no change)

All `import pipeline`, `from quality_control.models import ...`, etc. remain textually unchanged. They resolve via `pythonpath = "src"`.

#### File-path-based imports in tests

Tests that use `importlib.util.spec_from_file_location` or `Path(__file__).resolve().parents[N]` to locate source files must insert the `src/` segment:

```python
# Before
module_path = PROJECT_ROOT / "pipeline" / "validator.py"

# After
module_path = PROJECT_ROOT / "src" / "pipeline" / "validator.py"
```

#### src/utils/path_utils.py

```python
# Before
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# After (one more .parent to escape src/)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
```

All downstream path computations (`BASE_DIR`, `EXTRACTION_MAP`, `PDF_DIR`, etc.) continue to work because they derive from `PROJECT_ROOT`.

### Component 5: Dependency Direction & Steering Test Updates

**Responsibility:** Update AST-scanning tests to look under `src/`.

#### tests/test_dependency_directions.py

```python
# Before
package_dir = PROJECT_ROOT / source_package

# After
package_dir = PROJECT_ROOT / "src" / source_package
```

#### tests/steering/test_qc_textprocessor_separation.py

```python
# Before
CHECKS_DIR = PROJECT_ROOT / "quality_control" / "checks"

# After
CHECKS_DIR = PROJECT_ROOT / "src" / "quality_control" / "checks"
```

#### tests/steering/test_text_processing_separation.py

```python
# Before
TEXT_PROCESSING_DIR = PROJECT_ROOT / "text_processing"

# After
TEXT_PROCESSING_DIR = PROJECT_ROOT / "src" / "text_processing"
```

### Component 6: Steering Documentation Scrub

**Responsibility:** Update all `.kiro/steering/*.md` path references from `<package>/` to `src/<package>/`.

Files affected:
- `.kiro/steering/product.md` — architecture tree, module responsibilities table
- `.kiro/steering/testing.md` — test layout tree, conftest documentation, coverage table
- `.kiro/steering/config.md` — config loading function references
- `.kiro/steering/changelog.md` — no path references expected, verify only

### Component 7: GROBID Test Fix

**Responsibility:** Fix four failing GROBID extractor tests.

**Root cause:** The tests mock `_get_session` but do not patch `requests` in `sys.modules`. The GROBID module does `import requests` lazily inside `_call_grobid_api`, which fails when `requests` is not installed in the test environment.

**Fix approach:**
1. Patch `sys.modules["requests"]` with a `MagicMock` in the test fixtures
2. Patch `_get_session` on the correct module path (`src.pdf_extractor.extraction.GROBID` post-migration, resolved via the import `pdf_extractor.extraction.GROBID`)
3. Ensure the mock session's `post` returns a `MagicMock` response with `.text` containing valid minimal TEI XML and `.status_code = 200`

**Test file location:** `tests/src/pdf_extractor/test_grobid_extractor.py`

### Component 8: Flagged Fields CSV Test Fix

**Responsibility:** Fix `test_write_flagged_fields_csv_header` variable name bug.

**Root cause:** The test uses `qc_csv` (an undefined or wrong variable) instead of `flagged_csv` when opening the CSV file for header verification.

**Fix:** Replace `qc_csv` with `flagged_csv` in the `open()` call.

**Test file location:** `tests/src/pipeline/test_extraction_report_qc.py`

## Data Models

No new data models are introduced. All existing dataclasses in `quality_control/models.py` and other modules remain unchanged. The migration is purely structural — file locations change, but module contents and their public APIs are byte-for-byte identical.

### Configuration Changes

| File | Key | Before | After |
|---|---|---|---|
| `pyproject.toml` | `pythonpath` | `"."` | `"src"` |
| `conftest.py` (root) | `sys.path` insert | project root | `<root>/src` |
| `src/pdf_extractor/conftest.py` | `sys.path` insert | project root | `<root>/src` (via `parent.parent`) |

## Error Handling

### Migration Failure Modes

| Failure | Detection | Mitigation |
|---|---|---|
| Incomplete file move (files left at old location) | Post-migration check: assert old directories don't exist | Script verifies absence of root-level package dirs |
| Broken imports after move | `python -m pytest --collect-only` exits non-zero | Root conftest.py + `pythonpath = "src"` ensures resolution |
| `PROJECT_ROOT` resolves wrong directory | `path_utils.PROJECT_ROOT` no longer points to repo root | Unit test: `PROJECT_ROOT / "configs" / "config.yaml"` exists |
| Steering tests scan empty directories | Tests fail with "directory not found" | Updated paths point to `src/<pkg>` |
| GROBID tests still fail | pytest reports failures in `test_grobid_extractor.py` | Mock both `_get_session` and `requests` module |

### Rollback Strategy

The migration is a single atomic commit. If any post-migration verification fails:
1. `git checkout -- .` restores all files
2. No database, external service, or deployment state is affected
3. The migration can be re-attempted after fixing the identified issue

## Testing Strategy

### Why Property-Based Testing Does Not Apply

This feature is a **structural migration** — it moves files, updates paths in configuration, and fixes test mocking. There are no pure functions with varying inputs, no data transformations, no parsers or serializers being introduced. The acceptance criteria are binary (file exists or doesn't, test passes or fails, path resolves or doesn't). Property-based testing provides no value here.

### Testing Approach

**Smoke tests** verify the migration is complete:
- `python -m pytest --collect-only` exits 0 (all imports resolve)
- `python -c "import agents; import pipeline; ..."` with `PYTHONPATH=src` exits 0
- Root-level package directories do not exist after migration

**Example-based unit tests** verify specific behaviors:
- `PROJECT_ROOT` in `path_utils.py` resolves to the actual repo root
- `conftest.py` inserts the correct `src/` path into `sys.path`
- Dependency direction tests scan `src/<pkg>/` and find `.py` files
- Steering separation tests scan `src/quality_control/checks/` and `src/text_processing/`
- GROBID tests pass with proper mocking
- Flagged fields CSV test passes with corrected variable name

**Integration tests** verify end-to-end correctness:
- `python -m pytest -q` exits 0 with zero failures (full non-slow suite)
- `python -m pytest -q -m ""` exits 0 (full suite including slow, with mocked heavy deps)

### Test Execution Order

1. Run `python -m pytest --collect-only` to verify no collection errors
2. Run `python -m pytest tests/test_dependency_directions.py` to verify architectural guards
3. Run `python -m pytest tests/steering/` to verify separation enforcement
4. Run `python -m pytest tests/src/pdf_extractor/test_grobid_extractor.py` to verify GROBID fix
5. Run `python -m pytest -q` for full non-slow suite
6. Run `python -m pytest -q -m ""` for complete suite (if heavy deps available or mocked)

### Verification Checklist

- [ ] No directory named `agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, or `utils/` exists at repo root
- [ ] Each package exists at `src/<name>/` with `__init__.py`
- [ ] `src/__init__.py` does NOT exist
- [ ] `pyproject.toml` has `pythonpath = "src"`
- [ ] Root `conftest.py` inserts `<root>/src` into `sys.path`
- [ ] `src/utils/path_utils.py` `PROJECT_ROOT` resolves to repo root (3 `.parent` levels)
- [ ] All steering docs reference `src/<pkg>/` paths
- [ ] `test_dependency_directions.py` scans `src/<pkg>/` directories
- [ ] Steering tests scan `src/quality_control/checks/` and `src/text_processing/`
- [ ] GROBID tests pass (4 tests)
- [ ] Flagged fields CSV test passes
- [ ] `python -m pytest -q` exits 0
