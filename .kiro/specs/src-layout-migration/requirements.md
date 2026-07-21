# Requirements Document

## Introduction

This spec defines the migration of EviTrace's source packages from the repository root into a `src/` directory, following the Python "src layout" convention. The migration encompasses moving all application packages, updating all import paths and configuration, restructuring the test directory to mirror the new layout, fixing two pre-existing test failures, and scrubbing all references to the old root-level locations. No backward-compatibility shims are permitted.

## Glossary

- **Migration_Tool**: The set of file-move operations, import rewrites, and configuration changes that accomplish the src layout migration
- **Source_Packages**: The seven top-level Python packages being moved: `agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, `utils/`
- **Test_Suite**: The complete pytest test collection under `tests/`
- **Build_Config**: The `pyproject.toml` file and root `conftest.py` that configure Python path resolution and test discovery
- **Steering_Docs**: The `.kiro/steering/` markdown files that document project architecture and conventions
- **Import_Statement**: Any Python `import` or `from ... import` statement referencing a Source_Package

## Requirements

### Requirement 1: Relocate Source Packages to src/ Directory

**User Story:** As a developer, I want all application source packages under a single `src/` directory, so that the project follows the standard Python src layout and cleanly separates source from tests and configuration.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Migration_Tool SHALL have moved each of the seven Source_Packages (`agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, `utils/`) into `src/` such that each package directory exists at the path `src/<package_name>/` and contains an `__init__.py` file
2. WHEN the migration is complete, THE Migration_Tool SHALL have preserved all files and subdirectories within each Source_Package such that the file count per package is identical before and after migration, all relative paths within each package are unchanged, and file contents are byte-for-byte identical
3. WHEN the migration is complete, THE Migration_Tool SHALL have removed the original root-level directories for all seven Source_Packages so that none of the seven directory names exist as immediate children of the project root
4. WHEN the migration is complete, THE Migration_Tool SHALL NOT create an `__init__.py` inside `src/` — the `src/` directory is a namespace container, not a Python package

### Requirement 2: Update All Import Statements

**User Story:** As a developer, I want all import statements across the codebase to resolve correctly after the move, so that the application runs without import errors.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Migration_Tool SHALL have preserved the text of every standard Import_Statement (i.e., `import <package>` or `from <package> import ...`) in the Source_Packages, `main.py`, and the Test_Suite unchanged, relying on the Build_Config `pythonpath = "src"` setting to resolve them to the new `src/` location
2. WHEN the migration is complete, THE Migration_Tool SHALL have updated every direct file-path-based module import (using `importlib.util.spec_from_file_location`) in the Test_Suite so that the computed `Path` includes the `src/` directory segment between the repository root and the Source_Package name (e.g., `<repo_root> / "src" / "pipeline" / "module.py"`)
3. WHEN `python -c "import agents; import artifact_generation; import pdf_extractor; import pipeline; import quality_control; import text_processing; import utils"` is run with `PYTHONPATH=src` from the repository root after migration, THE command SHALL complete with exit code 0 and no `ModuleNotFoundError`
4. IF any Import_Statement in the Test_Suite uses a relative path computation (e.g., `Path(__file__).resolve().parents[N]`) to locate a Source_Package file, THEN THE Migration_Tool SHALL have adjusted the parent traversal depth or inserted the `src/` path segment so that the resolved path points to the file's new location under `src/`

### Requirement 3: Update Build and Test Configuration

**User Story:** As a developer, I want `pyproject.toml` and `conftest.py` to correctly configure Python path resolution for the new layout, so that pytest discovers and runs all tests without manual path manipulation.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Build_Config SHALL set `pythonpath` in `[tool.pytest.ini_options]` to `"src"` so that pytest resolves Source_Package imports from the `src/` directory
2. WHEN the migration is complete, THE Build_Config SHALL update the root `conftest.py` to compute the absolute path of the `src/` subdirectory relative to the conftest file location (i.e., `Path(__file__).resolve().parent / "src"`) and insert it at position 0 of `sys.path` if not already present
3. IF `pdf_extractor/conftest.py` exists at `src/pdf_extractor/conftest.py` after the move, THEN THE Build_Config SHALL update it to compute the absolute path of the `src/` directory (i.e., `Path(__file__).resolve().parent.parent`) and insert it at position 0 of `sys.path` if not already present
4. WHEN the migration is complete, THE Build_Config SHALL preserve `testpaths = ["tests"]`, `addopts = "--import-mode=importlib -m \"not slow\""`, and the `markers` list unchanged in `[tool.pytest.ini_options]`
5. WHEN the migration is complete, THE Build_Config SHALL ensure that running `python -m pytest --collect-only` from the repository root exits with code 0 and discovers at least 1 test item, confirming that all Source_Package imports resolve without `ModuleNotFoundError`

### Requirement 4: Restructure Test Directory to Mirror src/ Layout

**User Story:** As a developer, I want the test directory structure to mirror the new `src/` layout, so that test file locations are predictable and consistent with the source they test.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Test_Suite SHALL have its package-mirroring subdirectories (`agents/`, `artifact_generation/`, `pdf_extractor/`, `pipeline/`, `quality_control/`, `text_processing/`, `utils/`) nested under `tests/src/` instead of directly under `tests/`
2. WHEN the migration is complete, THE Test_Suite SHALL retain `tests/steering/` at its current location (not moved under `tests/src/`)
3. WHEN the migration is complete, THE Test_Suite SHALL retain all root-level test files (`test_dependency_directions.py`, `test_csv_exporter.py`, `test_migration_artifact_scrub_bug_condition.py`, `test_migration_artifact_scrub_preservation.py`, and any future `test_*.py` files directly under `tests/`) at `tests/` (not moved under `tests/src/`)
4. WHEN the migration is complete, THE Test_Suite SHALL pass `python -m pytest -q` from the repo root with zero collection errors, confirming that pytest discovers tests in both `tests/src/` subdirectories and the `tests/` root-level files

### Requirement 5: Scrub All Legacy Path References

**User Story:** As a developer, I want zero references to the old root-level package locations remaining anywhere in the repository, so that there is no confusion about the canonical source location.

#### Acceptance Criteria

1. WHEN the migration is complete, THE Migration_Tool SHALL have updated all Steering_Docs under `.kiro/steering/` (product.md, testing.md, config.md, changelog.md) so that every filesystem path reference to a top-level package directory uses the `src/<package_name>/` prefix instead of the root-level `<package_name>/` prefix
2. WHEN the migration is complete, THE Migration_Tool SHALL have updated `tests/test_dependency_directions.py` so that `PROJECT_ROOT` resolution and all file-scanning calls scan `src/<package_name>/` directories instead of root-level `<package_name>/` directories
3. WHEN the migration is complete, THE Migration_Tool SHALL have left no backward-compatibility shim, re-export module, `sys.path` manipulation in `conftest.py` files, or `__init__.py` stub at the old root-level package locations that causes `import <package_name>` to resolve without the `src/` prefix
4. WHEN the migration is complete, THE Migration_Tool SHALL have updated `src/utils/path_utils.py` so that `PROJECT_ROOT` still resolves to the repository root and all package-relative path computations account for the additional `src/` directory level
5. WHEN the migration is complete, THE Migration_Tool SHALL ensure that a recursive text search for the patterns `quality_control/`, `pdf_extractor/`, `agents/`, `pipeline/`, `text_processing/`, and `utils/` in all tracked non-test `.py` files, `.md` files, and `.yaml`/`.toml` config files returns zero matches that reference the old root-level location — excluding references inside `src/` paths, git history, `.hypothesis/`, and Python import statements (which remain unchanged per Requirement 2)

### Requirement 6: Fix GROBID Test Failures

**User Story:** As a developer, I want the four GROBID extractor tests to pass, so that the test suite is green after migration.

#### Acceptance Criteria

1. WHEN any of the four GROBID extractor tests executes, THE Test_Suite SHALL patch `_get_session` on the `GROBID` module to return a `MagicMock` session whose `post` method returns a fake HTTP response containing valid minimal TEI XML, AND SHALL also patch the `requests` module in `sys.modules` so that the lazy `import requests` inside `_call_grobid_api` does not raise `ModuleNotFoundError`
2. WHEN `test_extract_with_grobid_parse_blocks_false_skips_parse` executes with `parse_blocks=False`, THE Test_Suite SHALL assert that `extract_with_grobid` returns the raw TEI XML string as the first element, an empty list as the second element, and that `_parse_tei_to_blocks` is not called
3. WHEN `test_extract_with_grobid_parse_blocks_true_parses` executes with `parse_blocks=True`, THE Test_Suite SHALL assert that `extract_with_grobid` returns a non-empty list of block dicts as the second element
4. WHEN `test_grobid_call_passes_no_proxy` executes, THE Test_Suite SHALL assert that the mocked session's `post` method was called with `proxies={"http": None, "https": None}` in its keyword arguments
5. WHEN `test_grobid_extract_default_generate_ids_is_false` executes, THE Test_Suite SHALL assert that the mocked session's `post` method was called with form data containing `generateIDs` equal to the string `"0"`
6. WHEN `python -m pytest tests/pdf_extractor/test_grobid_extractor.py` is run, THE Test_Suite SHALL report all four tests as passed with exit code 0

### Requirement 7: Fix Flagged Fields CSV Test Failure

**User Story:** As a developer, I want `test_write_flagged_fields_csv_header` to pass, so that the test suite is green after migration.

#### Acceptance Criteria

1. THE `test_write_flagged_fields_csv_header` function in the extraction report QC test file SHALL use the variable `flagged_csv` (not `qc_csv`) in the `open()` call that reads the CSV file for header verification
2. WHEN `python -m pytest` runs the extraction report QC test file, THE Test_Suite SHALL report `test_write_flagged_fields_csv_header` as PASSED with exit code 0
3. WHEN `python -m pytest` runs the extraction report QC test file, THE Test_Suite SHALL report all tests in the file as PASSED with no regressions introduced by the variable rename

### Requirement 8: All Tests Pass Post-Migration

**User Story:** As a developer, I want the entire test suite to pass after migration, so that I have confidence the migration introduced no regressions.

#### Acceptance Criteria

1. WHEN `python -m pytest -q` is run from the repository root after all other migration requirements are implemented, THE Test_Suite SHALL exit with return code 0 and report zero failures and zero errors for all non-slow tests
2. WHEN `python -m pytest -q -m ""` is run from the repository root after all other migration requirements are implemented, THE Test_Suite SHALL exit with return code 0 and report zero failures and zero errors for all tests including slow-marked tests, given that heavy optional dependencies (paddleocr, faiss, torch, sentence-transformers) are mocked as specified by existing test fixtures
3. IF any test performs AST-based file scanning or file-system traversal using a string literal or Path reference to a Source_Package directory, THEN THE Test_Suite SHALL use the updated `src/<package_name>` path so that the resolved path points to the post-migration package location
4. WHEN pytest collection begins, THE Test_Suite SHALL collect all test modules under `tests/` without raising ImportError or ModuleNotFoundError for any Source_Package import

### Requirement 9: Preserve Dependency Direction Enforcement

**User Story:** As a developer, I want the dependency direction tests to continue enforcing cross-package import rules after migration, so that architectural boundaries remain guarded.

#### Acceptance Criteria

1. WHEN `test_dependency_directions.py` executes after migration, THE Test_Suite SHALL use AST-based static analysis to recursively scan all `.py` files (including `__init__.py` and sub-packages) under `src/pdf_extractor/`, `src/quality_control/`, `src/agents/`, and `src/text_processing/` for forbidden absolute imports
2. WHEN `test_dependency_directions.py` executes after migration, THE Test_Suite SHALL enforce the following forbidden-import rules by detecting both `import X` and `from X import Y` statement forms: pdf_extractor must not import quality_control; quality_control must not import agents, pipeline, or pdf_extractor; agents must not import quality_control, pipeline, or pdf_extractor; text_processing must not import quality_control
3. IF a forbidden cross-package import is detected, THEN THE Test_Suite SHALL fail the test with a message identifying the violating file path relative to the project root, the imported module name, and the forbidden pair that was violated

### Requirement 10: Preserve Steering Test Enforcement

**User Story:** As a developer, I want the steering separation tests to continue scanning the correct directories after migration, so that architectural separation rules remain enforced.

#### Acceptance Criteria

1. WHEN `tests/steering/test_qc_textprocessor_separation.py` executes after migration, THE Test_Suite SHALL use AST analysis to scan all `.py` files under `src/quality_control/checks/` and fail if any file contains an import from the `text_processing` package, an import of `TextProcessor` by name or from `utils.text_processor`, or a top-level import of `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`
2. WHEN `tests/steering/test_text_processing_separation.py` executes after migration, THE Test_Suite SHALL use AST analysis to scan all `.py` files under `src/text_processing/` and fail if any file contains an absolute import from `quality_control`, `pipeline`, or `agents`
3. IF `src/quality_control/checks/` or `src/text_processing/` does not exist or contains no `.py` files, THEN THE Test_Suite SHALL fail with an error indicating the expected directory is missing or empty
