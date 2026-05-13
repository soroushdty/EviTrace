# Implementation Plan: src-layout-migration

## Overview

Migrate EviTrace's seven source packages from the repository root into a `src/` directory, update all build configuration and path references, restructure the test directory to mirror the new layout, fix two pre-existing test failures, and scrub all steering documentation of legacy path references. The migration is purely structural — no application logic changes.

## Tasks

- [x] 1. Relocate source packages into src/
  - [x] 1.1 Create `src/` directory and move all seven source packages (`agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, `utils/`) into `src/`
    - Move each package directory preserving all files, subdirectories, and `__init__.py` files byte-for-byte
    - Do NOT create `src/__init__.py` — `src/` is a namespace container, not a package
    - Remove the now-empty root-level package directories after the move
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Update build configuration for src layout
  - [x] 2.1 Update `pyproject.toml` to set `pythonpath = "src"`
    - Change `pythonpath = "."` to `pythonpath = "src"` in `[tool.pytest.ini_options]`
    - Preserve `testpaths = ["tests"]`, `addopts`, and `markers` unchanged
    - _Requirements: 3.1, 3.4_

  - [x] 2.2 Rewrite root `conftest.py` to insert `src/` into `sys.path`
    - Compute `Path(__file__).resolve().parent / "src"` and insert at `sys.path[0]` if not present
    - Remove old project-root insertion logic
    - _Requirements: 3.2_

  - [x] 2.3 Update `src/pdf_extractor/conftest.py` to resolve `src/` via `parent.parent`
    - Compute `Path(__file__).resolve().parent.parent` (which is `src/`) and insert at `sys.path[0]` if not present
    - _Requirements: 3.3_

- [x] 3. Update PROJECT_ROOT and path computations
  - [x] 3.1 Update `src/utils/path_utils.py` to add one more `.parent` level
    - Change `PROJECT_ROOT = Path(__file__).resolve().parent.parent` to `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent`
    - This accounts for the new `src/` directory level: `src/utils/path_utils.py` → `src/utils/` → `src/` → repo root
    - Verify all downstream path constants (`BASE_DIR`, `EXTRACTION_MAP`, `PDF_DIR`, etc.) still resolve correctly
    - _Requirements: 5.4_

- [x] 4. Restructure test directories under tests/src/
  - [x] 4.1 Create `tests/src/` and move package-mirroring test subdirectories into it
    - Move `tests/agents/` → `tests/src/agents/`
    - Move `tests/pdf_extractor/` → `tests/src/pdf_extractor/`
    - Move `tests/pipeline/` → `tests/src/pipeline/`
    - Move `tests/quality_control/` → `tests/src/quality_control/`
    - Move `tests/text_processing/` → `tests/src/text_processing/`
    - Move `tests/utils/` → `tests/src/utils/`
    - Do NOT move `tests/steering/` — it stays at `tests/steering/`
    - Do NOT move root-level `tests/test_*.py` files — they stay directly under `tests/`
    - Do NOT create `__init__.py` in `tests/` or `tests/src/` (importlib mode does not require them)
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 5. Checkpoint - Verify collection after structural moves
  - Ensure `python -m pytest --collect-only` exits with code 0 and discovers tests
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Update dependency direction and steering tests
  - [x] 6.1 Update `tests/test_dependency_directions.py` to scan `src/` paths
    - Change `package_dir = PROJECT_ROOT / source_package` to `package_dir = PROJECT_ROOT / "src" / source_package`
    - _Requirements: 5.2, 9.1, 9.2_

  - [x] 6.2 Update `tests/steering/test_qc_textprocessor_separation.py` to scan `src/` paths
    - Change `CHECKS_DIR = PROJECT_ROOT / "quality_control" / "checks"` to `CHECKS_DIR = PROJECT_ROOT / "src" / "quality_control" / "checks"`
    - _Requirements: 10.1_

  - [x] 6.3 Update `tests/steering/test_text_processing_separation.py` to scan `src/` paths
    - Change `package_dir = PROJECT_ROOT / "text_processing"` to `package_dir = PROJECT_ROOT / "src" / "text_processing"` in all three test functions
    - _Requirements: 10.2_

- [x] 7. Fix file-path-based imports in tests
  - [x] 7.1 Update any test files that use `importlib.util.spec_from_file_location` or direct `Path` computations to locate source modules
    - Insert the `src/` segment between `PROJECT_ROOT` and the package name in all file-path-based module imports
    - Search for patterns like `PROJECT_ROOT / "pipeline"`, `PROJECT_ROOT / "pdf_extractor"`, etc. in test files and add `"src"` segment
    - _Requirements: 2.2, 2.4_

- [x] 8. Fix GROBID test failures
  - [x] 8.1 Fix the four GROBID extractor tests in `tests/src/pdf_extractor/test_grobid_extractor.py`
    - Patch `sys.modules["requests"]` with a `MagicMock` in test fixtures so lazy `import requests` inside `_call_grobid_api` does not raise `ModuleNotFoundError`
    - Patch `_get_session` on the GROBID module to return a mock session whose `post` returns a response with `.text` containing valid minimal TEI XML and `.status_code = 200`
    - Ensure `test_extract_with_grobid_parse_blocks_false_skips_parse` asserts raw TEI XML returned, empty list, and `_parse_tei_to_blocks` not called
    - Ensure `test_extract_with_grobid_parse_blocks_true_parses` asserts non-empty list of block dicts
    - Ensure `test_grobid_call_passes_no_proxy` asserts `post` called with `proxies={"http": None, "https": None}`
    - Ensure `test_grobid_extract_default_generate_ids_is_false` asserts form data contains `generateIDs` equal to `"0"`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 9. Fix flagged fields CSV test failure
  - [x] 9.1 Fix `test_write_flagged_fields_csv_header` in `tests/src/pipeline/test_extraction_report_qc.py`
    - Replace the incorrect variable `qc_csv` with `flagged_csv` in the `open()` call that reads the CSV file for header verification
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 10. Checkpoint - Verify test fixes
  - Run `python -m pytest tests/src/pdf_extractor/test_grobid_extractor.py` and confirm all 4 tests pass
  - Run `python -m pytest tests/src/pipeline/test_extraction_report_qc.py` and confirm all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Scrub steering documentation of legacy path references
  - [x] 11.1 Update `.kiro/steering/product.md`
    - Update the architecture tree to show packages under `src/`
    - Update the module responsibilities table to use `src/<package>/` paths
    - Replace all root-level package path references with `src/<package>/` equivalents
    - _Requirements: 5.1_

  - [x] 11.2 Update `.kiro/steering/testing.md`
    - Update the test layout tree to show `tests/src/` nesting for package-mirroring directories
    - Update conftest documentation to reference `src/` path insertion
    - Update coverage table and any path references to use `src/<package>/` prefix
    - _Requirements: 5.1_

  - [x] 11.3 Update `.kiro/steering/config.md`
    - Update `pythonpath` value from `"."` to `"src"` in the pytest config example
    - Update any config loading function references that mention root-level package paths
    - _Requirements: 5.1_

  - [x] 11.4 Verify `.kiro/steering/changelog.md` has no stale path references
    - Confirm no filesystem path references to root-level packages exist; if found, update them
    - _Requirements: 5.1_

- [x] 12. Final verification - Full test suite green
  - [x] 12.1 Run `python -m pytest --collect-only` and confirm exit code 0 with no collection errors
    - _Requirements: 3.5, 8.4_

  - [x] 12.2 Run `python -m pytest -q` and confirm zero failures for the non-slow suite
    - _Requirements: 8.1_

  - [x] 12.3 Verify no root-level package directories remain
    - Confirm none of `agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, `utils/` exist as immediate children of the project root
    - _Requirements: 1.3, 5.3_

  - [x] 12.4 Verify `src/__init__.py` does NOT exist
    - _Requirements: 1.4_

## Notes

- This is a structural migration — no property-based tests apply
- No backward-compatibility shims are permitted (no `__init__.py` stubs or `sys.path` hacks at old locations)
- Standard Python import text (`from pipeline import ...`) remains unchanged; resolution is handled by `pythonpath = "src"`
- The migration should be a single atomic commit for easy rollback via `git checkout -- .`
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation of the migration

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "3.1", "4.1"] },
    { "id": 2, "tasks": ["6.1", "6.2", "6.3", "7.1"] },
    { "id": 3, "tasks": ["8.1", "9.1"] },
    { "id": 4, "tasks": ["11.1", "11.2", "11.3", "11.4"] },
    { "id": 5, "tasks": ["12.1", "12.2", "12.3", "12.4"] }
  ]
}
```
