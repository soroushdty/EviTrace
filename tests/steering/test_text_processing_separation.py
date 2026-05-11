"""
tests/steering/test_text_processing_separation.py
=================================================
AST-walker verifying no text_processing/ file imports from quality_control/.

This test enforces the architectural boundary: text_processing is a
standalone package that must not depend on quality_control.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _collect_py_files(package_dir: Path) -> list[Path]:
    """Return all .py files under package_dir, recursively."""
    if not package_dir.is_dir():
        return []
    return sorted(package_dir.rglob("*.py"))


def _extract_imports(source_path: Path) -> list[str]:
    """Parse a Python source file and return all module names from import statements."""
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
            if node.module is not None and node.level == 0:
                imported_modules.append(node.module)

    return imported_modules


def test_text_processing_does_not_import_quality_control():
    """No file in text_processing/ shall import from quality_control/."""
    package_dir = PROJECT_ROOT / "text_processing"
    violations: list[str] = []

    for py_file in _collect_py_files(package_dir):
        imported_modules = _extract_imports(py_file)
        for module in imported_modules:
            if module == "quality_control" or module.startswith("quality_control."):
                rel_path = py_file.relative_to(PROJECT_ROOT)
                violations.append(
                    f"  {rel_path}: imports '{module}' "
                    f"(forbidden: text_processing → quality_control)"
                )

    assert not violations, (
        "text_processing imports quality_control (forbidden):\n"
        + "\n".join(violations)
    )


def test_text_processing_does_not_import_pipeline():
    """No file in text_processing/ shall import from pipeline/."""
    package_dir = PROJECT_ROOT / "text_processing"
    violations: list[str] = []

    for py_file in _collect_py_files(package_dir):
        imported_modules = _extract_imports(py_file)
        for module in imported_modules:
            if module == "pipeline" or module.startswith("pipeline."):
                rel_path = py_file.relative_to(PROJECT_ROOT)
                violations.append(
                    f"  {rel_path}: imports '{module}' "
                    f"(forbidden: text_processing → pipeline)"
                )

    assert not violations, (
        "text_processing imports pipeline (forbidden):\n"
        + "\n".join(violations)
    )


def test_text_processing_does_not_import_agents():
    """No file in text_processing/ shall import from agents/."""
    package_dir = PROJECT_ROOT / "text_processing"
    violations: list[str] = []

    for py_file in _collect_py_files(package_dir):
        imported_modules = _extract_imports(py_file)
        for module in imported_modules:
            if module == "agents" or module.startswith("agents."):
                rel_path = py_file.relative_to(PROJECT_ROOT)
                violations.append(
                    f"  {rel_path}: imports '{module}' "
                    f"(forbidden: text_processing → agents)"
                )

    assert not violations, (
        "text_processing imports agents (forbidden):\n"
        + "\n".join(violations)
    )
