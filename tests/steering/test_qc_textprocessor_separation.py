"""
AST-based separation test: quality_control/checks/ must not import TextProcessor
or any text_processing package, and must not have top-level imports of heavy
optional dependencies.

Validates: Requirements 12.10, 13.9, 13.10
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Project root is three levels up from this file:
# tests/steering/test_qc_textprocessor_separation.py -> tests/steering/ -> tests/ -> repo root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CHECKS_DIR = PROJECT_ROOT / "quality_control" / "checks"

# ---------------------------------------------------------------------------
# Forbidden import patterns
# ---------------------------------------------------------------------------

# Any import whose module starts with "text_processing"
FORBIDDEN_PACKAGE_PREFIXES = ["text_processing"]

# Any import that names TextProcessor directly or comes from utils.text_processor
FORBIDDEN_MODULE_NAMES = ["utils.text_processor"]
FORBIDDEN_NAMES = ["TextProcessor"]

# Top-level imports of heavy optional dependencies are forbidden
FORBIDDEN_TOPLEVEL_MODULES = [
    "faiss",
    "torch",
    "sentence_transformers",
    "spacy",
    "scispacy",
    "stanza",
    "wtpsplit",
]


# ---------------------------------------------------------------------------
# AST helpers (same pattern as tests/test_dependency_directions.py)
# ---------------------------------------------------------------------------


def _collect_py_files(directory: Path) -> list[Path]:
    """Return all .py files under directory, recursively, sorted."""
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.py"))


def _parse_tree(source_path: Path) -> ast.AST | None:
    """Parse a Python source file and return its AST, or None on failure."""
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return ast.parse(source, filename=str(source_path))
    except SyntaxError:
        return None


def _is_toplevel(node: ast.AST, tree: ast.AST) -> bool:
    """
    Return True if the given import node is at module top level
    (i.e., a direct child of the Module body, not inside a function or class).
    """
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for child in ast.walk(parent):
                if child is node:
                    return False
    return True


# ---------------------------------------------------------------------------
# Violation collectors
# ---------------------------------------------------------------------------


def _check_text_processing_imports(py_file: Path) -> list[str]:
    """
    Return violation strings for any import of the text_processing package
    or of utils.text_processor / TextProcessor by name.

    Checks ALL import nodes (top-level and nested) because the requirement
    forbids these imports anywhere in the file.
    """
    tree = _parse_tree(py_file)
    if tree is None:
        return []

    rel = py_file.relative_to(PROJECT_ROOT)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                # Forbidden package prefix (e.g. "text_processing" or "text_processing.foo")
                for prefix in FORBIDDEN_PACKAGE_PREFIXES:
                    if name == prefix or name.startswith(prefix + "."):
                        violations.append(
                            f"  {rel}: `import {name}` "
                            f"(forbidden: imports from text_processing package)"
                        )
                # Forbidden module names (e.g. "utils.text_processor")
                for forbidden in FORBIDDEN_MODULE_NAMES:
                    if name == forbidden or name.startswith(forbidden + "."):
                        violations.append(
                            f"  {rel}: `import {name}` "
                            f"(forbidden: imports utils.text_processor)"
                        )

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level  # relative imports have level > 0

            # Absolute imports only for package-prefix checks
            if level == 0:
                for prefix in FORBIDDEN_PACKAGE_PREFIXES:
                    if module == prefix or module.startswith(prefix + "."):
                        imported_names = [a.name for a in node.names]
                        violations.append(
                            f"  {rel}: `from {module} import {', '.join(imported_names)}` "
                            f"(forbidden: imports from text_processing package)"
                        )

                for forbidden in FORBIDDEN_MODULE_NAMES:
                    if module == forbidden or module.startswith(forbidden + "."):
                        imported_names = [a.name for a in node.names]
                        violations.append(
                            f"  {rel}: `from {module} import {', '.join(imported_names)}` "
                            f"(forbidden: imports from utils.text_processor)"
                        )

            # Check for TextProcessor imported by name from any module
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"  {rel}: `from {module} import {alias.name}` "
                        f"(forbidden: imports TextProcessor by name)"
                    )

    return violations


def _check_toplevel_heavy_imports(py_file: Path) -> list[str]:
    """
    Return violation strings for any top-level import of a heavy optional
    dependency (faiss, torch, sentence_transformers, spacy, scispacy, stanza,
    wtpsplit).

    Top-level means the import node is a direct child of the module body —
    imports inside function or method bodies are permitted (lazy loading).
    """
    tree = _parse_tree(py_file)
    if tree is None:
        return []

    rel = py_file.relative_to(PROJECT_ROOT)
    violations: list[str] = []

    # Only look at direct children of the module body
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                root = name.split(".")[0]
                if root in FORBIDDEN_TOPLEVEL_MODULES:
                    violations.append(
                        f"  {rel}: top-level `import {name}` "
                        f"(forbidden heavy dependency: {root})"
                    )

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in FORBIDDEN_TOPLEVEL_MODULES:
                imported_names = [a.name for a in node.names]
                violations.append(
                    f"  {rel}: top-level `from {module} import {', '.join(imported_names)}` "
                    f"(forbidden heavy dependency: {root})"
                )

    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_checks_dir_exists():
    """
    Sanity check: quality_control/checks/ must exist and contain .py files.
    """
    assert CHECKS_DIR.is_dir(), (
        f"quality_control/checks/ directory not found at {CHECKS_DIR}. "
        "The checks package must exist before this test can run."
    )
    py_files = _collect_py_files(CHECKS_DIR)
    assert py_files, (
        f"No .py files found under {CHECKS_DIR}. "
        "Expected at least __init__.py and the four check modules."
    )


def test_no_text_processing_imports_in_checks():
    """
    Requirements 12.10, 13.9: ALL .py files under quality_control/checks/
    (including __init__.py) SHALL NOT import from the text_processing package
    or import TextProcessor by name or from utils.text_processor.

    Validates: Requirements 12.10, 13.9
    """
    py_files = _collect_py_files(CHECKS_DIR)
    all_violations: list[str] = []

    for py_file in py_files:
        violations = _check_text_processing_imports(py_file)
        all_violations.extend(violations)

    if all_violations:
        pytest.fail(
            f"Found {len(all_violations)} forbidden TextProcessor/text_processing "
            f"import(s) in quality_control/checks/:\n"
            + "\n".join(all_violations)
        )


def test_no_toplevel_heavy_imports_in_checks():
    """
    Requirement 13.10: ALL .py files under quality_control/checks/
    (including __init__.py) SHALL NOT contain any top-level import of
    faiss, torch, sentence_transformers, spacy, scispacy, stanza, or wtpsplit.

    Imports inside function or method bodies are permitted (lazy loading).

    Validates: Requirement 13.10
    """
    py_files = _collect_py_files(CHECKS_DIR)
    all_violations: list[str] = []

    for py_file in py_files:
        violations = _check_toplevel_heavy_imports(py_file)
        all_violations.extend(violations)

    if all_violations:
        pytest.fail(
            f"Found {len(all_violations)} forbidden top-level heavy dependency "
            f"import(s) in quality_control/checks/:\n"
            + "\n".join(all_violations)
        )


def test_all_separation_rules_exhaustive():
    """
    Exhaustive combined check: collects all separation violations across both
    rule categories and reports them together for a complete picture.

    Validates: Requirements 12.10, 13.9, 13.10
    """
    py_files = _collect_py_files(CHECKS_DIR)
    all_violations: list[str] = []

    for py_file in py_files:
        all_violations.extend(_check_text_processing_imports(py_file))
        all_violations.extend(_check_toplevel_heavy_imports(py_file))

    if all_violations:
        pytest.fail(
            f"Found {len(all_violations)} QC/TextProcessor separation violation(s) "
            f"in quality_control/checks/:\n"
            + "\n".join(all_violations)
        )
