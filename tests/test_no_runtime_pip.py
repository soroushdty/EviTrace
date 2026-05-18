"""
CI guard: no runtime pip install calls in source modules.

AST-scans all .py files under src/ for subprocess.run or subprocess.call
invocations that include 'pip' or 'install' in their arguments. Test files
are excluded.

Validates: Requirements 10.4
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def _collect_py_files(directory: Path) -> list[Path]:
    """Return all .py files under directory, recursively."""
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.py"))


def _is_subprocess_call(node: ast.Call) -> bool:
    """Check if a Call node is subprocess.run or subprocess.call."""
    func = node.func

    # subprocess.run(...) or subprocess.call(...)
    if isinstance(func, ast.Attribute):
        if func.attr in ("run", "call"):
            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                return True

    return False


def _args_contain_pip_or_install(node: ast.Call) -> bool:
    """Check if any argument to the call contains 'pip' or 'install'."""
    # Check positional arguments
    for arg in node.args:
        if _node_contains_pip_or_install(arg):
            return True

    # Check keyword arguments
    for kw in node.keywords:
        if _node_contains_pip_or_install(kw.value):
            return True

    return False


def _node_contains_pip_or_install(node: ast.expr) -> bool:
    """Recursively check if an AST expression contains 'pip' or 'install' strings."""
    # String constant: "pip", "install", or contains them as substrings
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        lower = node.value.lower()
        if "pip" in lower or "install" in lower:
            return True

    # List or tuple of arguments: [sys.executable, "-m", "pip", "install", ...]
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            if _node_contains_pip_or_install(elt):
                return True

    # f-string (JoinedStr): check string parts
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                lower = value.value.lower()
                if "pip" in lower or "install" in lower:
                    return True

    return False


def _find_runtime_pip_calls(source_path: Path) -> list[str]:
    """
    Parse a Python source file and return violation descriptions for any
    subprocess.run/call invocations with 'pip' or 'install' in arguments.
    """
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_subprocess_call(node):
            if _args_contain_pip_or_install(node):
                rel_path = source_path.relative_to(PROJECT_ROOT)
                lineno = node.lineno
                violations.append(
                    f"  {rel_path}:{lineno}: subprocess call with pip/install arguments"
                )

    return violations


def test_no_runtime_pip_in_src():
    """
    Requirement 10.4: No .py file under src/ shall contain a subprocess.run
    or subprocess.call invocation with 'pip' or 'install' in its arguments.

    This guards against runtime package installation, which violates the
    deterministic dependency management requirement.
    """
    all_violations: list[str] = []

    py_files = _collect_py_files(SRC_DIR)
    for py_file in py_files:
        violations = _find_runtime_pip_calls(py_file)
        all_violations.extend(violations)

    assert not all_violations, (
        f"Found {len(all_violations)} runtime pip install call(s) in src/:\n"
        + "\n".join(all_violations)
        + "\n\nRuntime pip calls are forbidden (Requirement 10.4). "
        "Dependencies must be installed before running the pipeline."
    )
