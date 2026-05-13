"""
Static import analysis test for cross-package dependency direction enforcement.

Recursively inspects the AST of all .py files in each package (including
sub-packages and __init__.py files) and asserts no forbidden cross-package
import statements exist.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""

import ast
import os
from pathlib import Path

# Project root is two levels up from this file (tests/ -> repo root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Forbidden pairs: (source_package, forbidden_import_prefix)
# Each tuple means: files inside source_package must not import from forbidden_import_prefix.
FORBIDDEN_PAIRS = [
    # Requirement 9.1: pdf_extractor must not import quality_control
    ("pdf_extractor", "quality_control"),
    # Requirement 9.4: quality_control must not import agents
    ("quality_control", "agents"),
    # Requirement 9.4: quality_control must not import pipeline
    ("quality_control", "pipeline"),
    # Requirement 9.4: quality_control must not import pdf_extractor
    ("quality_control", "pdf_extractor"),
    # Requirement 9.5: agents must not import quality_control
    ("agents", "quality_control"),
    # Requirement 9.2 / 9.5: agents must not import pipeline
    ("agents", "pipeline"),
    # Requirement 9.5: agents must not import pdf_extractor
    ("agents", "pdf_extractor"),
    # text_processing must not import quality_control
    ("text_processing", "quality_control"),
]


def _collect_py_files(package_dir: Path) -> list[Path]:
    """Return all .py files under package_dir, recursively."""
    if not package_dir.is_dir():
        return []
    return sorted(package_dir.rglob("*.py"))


def _extract_imports(source_path: Path) -> list[str]:
    """
    Parse a Python source file and return all top-level module names
    referenced by import statements (both `import X` and `from X import Y`).

    Returns a list of dotted module name strings (e.g. "quality_control.models").
    """
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError:
        return []

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                # Absolute imports only; relative imports (level > 0) are
                # intra-package and cannot violate cross-package rules.
                if node.level == 0:
                    imported_modules.append(node.module)

    return imported_modules


def _check_forbidden_imports(
    source_package: str,
    forbidden_prefix: str,
) -> list[str]:
    """
    Walk all .py files in source_package and collect violations where a file
    imports from forbidden_prefix.

    Returns a list of human-readable violation strings.
    """
    package_dir = PROJECT_ROOT / "src" / source_package
    violations: list[str] = []

    py_files = _collect_py_files(package_dir)
    for py_file in py_files:
        imported_modules = _extract_imports(py_file)
        for module in imported_modules:
            # A violation occurs when the imported module is exactly the
            # forbidden package or starts with "<forbidden_package>."
            if module == forbidden_prefix or module.startswith(forbidden_prefix + "."):
                rel_path = py_file.relative_to(PROJECT_ROOT)
                violations.append(
                    f"  {rel_path}: imports '{module}' "
                    f"(forbidden: {source_package} → {forbidden_prefix})"
                )

    return violations


# ---------------------------------------------------------------------------
# Individual test functions — one per forbidden pair for clear failure output
# ---------------------------------------------------------------------------


def test_pdf_extractor_does_not_import_quality_control():
    """
    Requirement 9.1: pdf_extractor SHALL NOT import quality_control.
    """
    violations = _check_forbidden_imports("pdf_extractor", "quality_control")
    assert not violations, (
        "pdf_extractor imports quality_control (forbidden by Requirement 9.1):\n"
        + "\n".join(violations)
    )


def test_quality_control_does_not_import_agents():
    """
    Requirement 9.4: quality_control SHALL NOT import agents.
    """
    violations = _check_forbidden_imports("quality_control", "agents")
    assert not violations, (
        "quality_control imports agents (forbidden by Requirement 9.4):\n"
        + "\n".join(violations)
    )


def test_quality_control_does_not_import_pipeline():
    """
    Requirement 9.4: quality_control SHALL NOT import pipeline.
    """
    violations = _check_forbidden_imports("quality_control", "pipeline")
    assert not violations, (
        "quality_control imports pipeline (forbidden by Requirement 9.4):\n"
        + "\n".join(violations)
    )


def test_quality_control_does_not_import_pdf_extractor():
    """
    Requirement 9.4: quality_control SHALL NOT import pdf_extractor.
    """
    violations = _check_forbidden_imports("quality_control", "pdf_extractor")
    assert not violations, (
        "quality_control imports pdf_extractor (forbidden by Requirement 9.4):\n"
        + "\n".join(violations)
    )


def test_agents_does_not_import_quality_control():
    """
    Requirement 9.3 / 9.5: agents SHALL NOT import quality_control.
    """
    violations = _check_forbidden_imports("agents", "quality_control")
    assert not violations, (
        "agents imports quality_control (forbidden by Requirements 9.3, 9.5):\n"
        + "\n".join(violations)
    )


def test_agents_does_not_import_pipeline():
    """
    Requirement 9.2 / 9.5: agents SHALL NOT import pipeline.
    """
    violations = _check_forbidden_imports("agents", "pipeline")
    assert not violations, (
        "agents imports pipeline (forbidden by Requirements 9.2, 9.5):\n"
        + "\n".join(violations)
    )


def test_agents_does_not_import_pdf_extractor():
    """
    Requirement 9.5: agents SHALL NOT import pdf_extractor.
    """
    violations = _check_forbidden_imports("agents", "pdf_extractor")
    assert not violations, (
        "agents imports pdf_extractor (forbidden by Requirement 9.5):\n"
        + "\n".join(violations)
    )


def test_text_processing_does_not_import_quality_control():
    """
    text_processing SHALL NOT import quality_control.
    """
    violations = _check_forbidden_imports("text_processing", "quality_control")
    assert not violations, (
        "text_processing imports quality_control (forbidden):\n"
        + "\n".join(violations)
    )


def test_all_forbidden_pairs_exhaustive():
    """
    Requirement 9.6: Exhaustive check of all forbidden pairs in a single pass.

    This test collects all violations across every forbidden pair and reports
    them together, making it easy to see the full picture in one failure.
    """
    all_violations: list[str] = []

    for source_package, forbidden_prefix in FORBIDDEN_PAIRS:
        violations = _check_forbidden_imports(source_package, forbidden_prefix)
        all_violations.extend(violations)

    assert not all_violations, (
        f"Found {len(all_violations)} forbidden cross-package import(s):\n"
        + "\n".join(all_violations)
    )
