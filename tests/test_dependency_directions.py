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
    # provenance-core Requirement 1.5 / design "Allowed Dependencies":
    # provenance is a leaf package (imports only utils + stdlib) ...
    ("provenance", "agents"),
    ("provenance", "pipeline"),
    ("provenance", "pdf_extractor"),
    ("provenance", "quality_control"),
    # ... and pipeline is its sole integration point, so these must not import it.
    ("quality_control", "provenance"),
    ("agents", "provenance"),
    ("pdf_extractor", "provenance"),
    ("text_processing", "provenance"),
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


def _assert_source_package_exists(source_package: str) -> None:
    """
    Guard against a vacuous pass: ``_check_forbidden_imports`` returns no
    violations for a package directory that does not exist, so a renamed or
    missing package would otherwise satisfy its rule silently.
    """
    package_dir = PROJECT_ROOT / "src" / source_package
    assert package_dir.is_dir(), (
        f"src/{source_package}/ does not exist; its dependency-direction rule "
        "would pass vacuously"
    )


def test_provenance_does_not_import_agents():
    """
    provenance-core Requirement 1.5: provenance SHALL NOT import agents.
    """
    _assert_source_package_exists("provenance")
    violations = _check_forbidden_imports("provenance", "agents")
    assert not violations, (
        "provenance imports agents (forbidden by provenance-core 1.5):\n"
        + "\n".join(violations)
    )


def test_provenance_does_not_import_pipeline():
    """
    provenance-core Requirement 1.5: provenance SHALL NOT import pipeline.
    """
    _assert_source_package_exists("provenance")
    violations = _check_forbidden_imports("provenance", "pipeline")
    assert not violations, (
        "provenance imports pipeline (forbidden by provenance-core 1.5):\n"
        + "\n".join(violations)
    )


def test_provenance_does_not_import_pdf_extractor():
    """
    provenance-core Requirement 1.5: provenance SHALL NOT import pdf_extractor.
    """
    _assert_source_package_exists("provenance")
    violations = _check_forbidden_imports("provenance", "pdf_extractor")
    assert not violations, (
        "provenance imports pdf_extractor (forbidden by provenance-core 1.5):\n"
        + "\n".join(violations)
    )


def test_provenance_does_not_import_quality_control():
    """
    provenance-core Requirement 1.5: provenance SHALL NOT import quality_control.
    """
    _assert_source_package_exists("provenance")
    violations = _check_forbidden_imports("provenance", "quality_control")
    assert not violations, (
        "provenance imports quality_control (forbidden by provenance-core 1.5):\n"
        + "\n".join(violations)
    )


def test_quality_control_does_not_import_provenance():
    """
    provenance-core design (Allowed Dependencies): quality_control SHALL NOT
    import provenance.
    """
    _assert_source_package_exists("quality_control")
    violations = _check_forbidden_imports("quality_control", "provenance")
    assert not violations, (
        "quality_control imports provenance (forbidden; pipeline is the sole "
        "integration point):\n" + "\n".join(violations)
    )


def test_agents_does_not_import_provenance():
    """
    provenance-core design (Allowed Dependencies): agents SHALL NOT import
    provenance.
    """
    _assert_source_package_exists("agents")
    violations = _check_forbidden_imports("agents", "provenance")
    assert not violations, (
        "agents imports provenance (forbidden; pipeline is the sole "
        "integration point):\n" + "\n".join(violations)
    )


def test_pdf_extractor_does_not_import_provenance():
    """
    provenance-core design (Allowed Dependencies): pdf_extractor SHALL NOT
    import provenance.
    """
    _assert_source_package_exists("pdf_extractor")
    violations = _check_forbidden_imports("pdf_extractor", "provenance")
    assert not violations, (
        "pdf_extractor imports provenance (forbidden; pipeline is the sole "
        "integration point):\n" + "\n".join(violations)
    )


def test_text_processing_does_not_import_provenance():
    """
    provenance-core design (Allowed Dependencies): text_processing SHALL NOT
    import provenance.
    """
    _assert_source_package_exists("text_processing")
    violations = _check_forbidden_imports("text_processing", "provenance")
    assert not violations, (
        "text_processing imports provenance (forbidden; pipeline is the sole "
        "integration point):\n" + "\n".join(violations)
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
