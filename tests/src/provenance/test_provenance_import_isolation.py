"""Boundary and isolation tests for the ``provenance`` package (task 7.1).

Four invariants, each enforced mechanically and each backed by a pure scanner
that is itself exercised against synthetic violating source (so "fails loudly
when a violation is added" is proven inside this suite, without touching
``src/``):

1. **Import isolation (1.5).** Every module under ``src/provenance`` imports in a
   fresh interpreter in which ``pipeline``, ``quality_control``, ``agents``,
   ``pdf_extractor`` (and the other sibling packages) cannot be imported at all,
   in which any attempt to open a network connection raises, and in which no
   model-provider credential is set. Afterwards none of those packages, nor a
   network client library, is in ``sys.modules``.
   ``import provenance`` legitimately pulls in ``utils.logging_utils`` and, through
   it, ``utils.path_utils`` (which reads ``configs/config.yaml`` and imports
   ``yaml``); ``utils`` and the standard library are the package's only allowed
   dependencies.
2. **Fingerprint isolation (3.6).** No provenance module other than
   ``identity.py`` imports a hashing/digest primitive or calls one, nor refers to
   the pipeline's fingerprint computations; ``identity.py`` itself hashes only in
   the three functions that digest identifier material, claim values and
   evidence text, and is pure (no file, clock, randomness or network access), so
   it cannot compute a document, configuration or extraction-map fingerprint.
3. **Inert privacy carrier (9.4).** ``privacy_carrier.py`` contains no comparison
   against, branch on, lookup by, or string method applied to a privacy label or
   decision value, and contains no privacy vocabulary as a string literal. The
   same "never compare a carrier's label/decision" rule is asserted over every
   other provenance module.
4. **Single evidence-identifier construction (4.8).** Across all of ``src/`` the
   only place that builds (or splits) the ``<source_id>#<local_id>`` evidence
   identifier is ``provenance.identity``. Every other occurrence of the ``#``
   scope separator -- as a literal or through ``EVIDENCE_SCOPE_SEPARATOR`` -- must be
   on an explicit, justified allow-list keyed by (file, enclosing function,
   expression); a stale allow-list entry fails the test too.

The scanners are pure functions over source text. Nothing here imports
``pipeline``, ``quality_control`` or any heavy dependency.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple

import pytest

# tests/src/provenance/<this file> -> repo root is three levels up from the directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
PROVENANCE_DIR = SRC_ROOT / "provenance"
IDENTITY_PATH = PROVENANCE_DIR / "identity.py"
PRIVACY_CARRIER_PATH = PROVENANCE_DIR / "privacy_carrier.py"


def _py_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    """Path relative to ``src/`` in POSIX form, e.g. ``provenance/identity.py``."""
    return path.relative_to(SRC_ROOT).as_posix()


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class Finding(NamedTuple):
    """One scanner hit. ``key`` is what an allow-list names; ``lineno`` is for messages."""

    lineno: int
    scope: str
    kind: str
    snippet: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.scope, self.snippet)

    def describe(self) -> str:
        return f"line {self.lineno} in {self.scope}: [{self.kind}] {self.snippet}"


# ---------------------------------------------------------------------------
# Shared AST plumbing
# ---------------------------------------------------------------------------

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class _Tree:
    """A parsed module with parent links and a ``qualname`` for every node."""

    def __init__(self, source: str) -> None:
        self.module = ast.parse(source)
        self.parent: dict[ast.AST, ast.AST] = {}
        self.scope: dict[ast.AST, str] = {}
        self.docstrings: set[ast.AST] = set()
        self._index(self.module, ())

    def _index(self, node: ast.AST, stack: tuple[str, ...]) -> None:
        self.scope[node] = ".".join(stack) if stack else "<module>"
        if isinstance(node, (ast.Module, *_SCOPE_NODES)):
            body = node.body
            if body and isinstance(body[0], ast.Expr):
                first = body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    self.docstrings.add(first)
        for child in ast.iter_child_nodes(node):
            self.parent[child] = node
            child_stack = stack + (child.name,) if isinstance(child, _SCOPE_NODES) else stack
            # A def's own node is attributed to its *enclosing* scope; its body to itself.
            self._index(child, child_stack)

    def walk(self):
        yield from ast.walk(self.module)

    def enclosing(self, node: ast.AST, kinds: tuple[type, ...]) -> ast.AST | None:
        current: ast.AST | None = node
        while current is not None:
            if isinstance(current, kinds):
                return current
            current = self.parent.get(current)
        return None


def _text(node: ast.AST) -> str:
    return ast.unparse(node)


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imported_modules(tree: _Tree) -> list[tuple[ast.AST, str]]:
    """``(node, dotted module)`` for every absolute import statement."""
    found: list[tuple[ast.AST, str]] = []
    for node in tree.walk():
        if isinstance(node, ast.Import):
            found.extend((node, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node, node.module))
    return found


# ---------------------------------------------------------------------------
# Invariant 2 -- fingerprint isolation (3.6)
# ---------------------------------------------------------------------------

#: Modules whose only purpose here would be to digest bytes. ``secrets`` and ``random``
#: are not digests and are deliberately absent; ``json`` is a serializer, not a hash.
HASH_MODULES = frozenset(
    {
        "hashlib", "hmac", "zlib", "binascii", "_hashlib", "_md5", "_sha1", "_sha2", "_sha3",
        "_blake2", "xxhash", "mmh3", "blake3", "crc32c", "crcmod", "cityhash", "farmhash",
    }
)

#: Hashing/digest primitives, recognised wherever they are called (``hashlib.sha256(..)``,
#: ``from hashlib import sha256``; ``sha256(..)``, ``h.new(..).hexdigest()``).
HASH_CALL_NAMES = frozenset(
    {
        "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_224", "sha3_256", "sha3_384",
        "sha3_512", "md5", "blake2b", "blake2s", "shake_128", "shake_256", "file_digest",
        "pbkdf2_hmac", "scrypt", "digest", "hexdigest", "crc32", "adler32", "crc_hqx",
        "uuid3", "uuid5", "xxh32", "xxh64", "xxh3_64", "xxh3_128",
    }
)

#: The pipeline's fingerprint computations (``pipeline.manifest`` / ``evidence_index`` /
#: ``extraction_pipeline``). Provenance *consumes* their results from the caller (3.6); it
#: must not reference them even without importing ``pipeline`` (e.g. receive one as a callable).
PIPELINE_FINGERPRINT_NAMES = frozenset(
    {
        "compute_identity", "_compute_file_sha256", "_compute_config_hash",
        "_compute_extraction_map_hash", "_pdf_sha256", "_scan_detection_config_hash",
        "_stat_fingerprint", "hash_file_digest", "compute_prompt_fingerprint",
    }
)


def find_hashing_calls(source: str) -> list[Finding]:
    """Every import of a hashing module, hashing call, or fingerprint-computation reference in *source*.

    Detected, whatever the aliasing: ``import hashlib`` / ``import hashlib as h`` /
    ``from hashlib import sha256``; ``hmac``/``zlib.crc32``/``uuid5``-style calls; a call named
    after a digest primitive (``.sha256(``, ``.hexdigest()``); ``__import__("hashlib")`` and
    ``importlib.import_module("hashlib")``; any reference to a name in
    :data:`PIPELINE_FINGERPRINT_NAMES`; and the builtin ``hash()`` outside a ``__hash__``
    method (it is salted per process, so it can never be a stable fingerprint, but it is
    flagged so every use is a conscious, allow-listed one).
    """
    tree = _Tree(source)
    findings: list[Finding] = []

    def add(node: ast.AST, kind: str, snippet: str) -> None:
        findings.append(Finding(getattr(node, "lineno", 0), tree.scope[node], kind, snippet))

    for node, module in _imported_modules(tree):
        if module.split(".")[0] in HASH_MODULES:
            add(node, "hash-import", _text(node))

    for node in tree.walk():
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in PIPELINE_FINGERPRINT_NAMES:
                add(node, "fingerprint-reference", _text(node))
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name in HASH_CALL_NAMES:
                add(node, "hash-call", _text(node))
            elif name in {"__import__", "import_module"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value.split(".")[0] in HASH_MODULES:
                        add(node, "hash-dynamic-import", _text(node))
            elif isinstance(node.func, ast.Name) and node.func.id == "hash":
                function = tree.enclosing(node, _SCOPE_NODES)
                if not (isinstance(function, ast.FunctionDef) and function.name == "__hash__"):
                    add(node, "builtin-hash", _text(node))
    return findings


#: Hash-like constructs tolerated outside ``identity.py``: ``(file, scope, expression)`` -> why.
ALLOWED_HASHING_OUTSIDE_IDENTITY: dict[tuple[str, str, str], str] = {
    (
        "provenance/recorder.py",
        "_finding_key",
        "hash(key)",
    ): (
        "a hashability probe (``TypeError`` means unhashable); the value is discarded, "
        "so it is not a fingerprint"
    ),
}

#: The only functions of ``identity.py`` that may hash, and what each digests (2.3).
IDENTITY_HASHING_SCOPES: dict[str, str] = {
    "_digest": "run identity fields and derivation-step content address (identifier material)",
    "derive_value_digest": "an extracted claim value (so the claim node never holds the value)",
    "derive_text_digest": "evidence text (so the evidence node never holds the text)",
}

#: ``identity.py`` may import only these: pure computation, no I/O, clock, randomness or network.
IDENTITY_ALLOWED_IMPORTS = frozenset({"__future__", "hashlib", "json", "collections.abc"})

#: Calls that read a file or the environment -- what computing a document, configuration or
#: extraction-map fingerprint would need, and what the pure identity module must never do.
IO_CALL_NAMES = frozenset(
    {"open", "read_bytes", "read_text", "stat", "listdir", "iterdir", "glob", "rglob", "getenv",
     "urlopen", "create_connection", "connect", "time", "now", "monotonic"}
)


def find_io_calls(source: str) -> list[Finding]:
    """Calls in *source* that touch the filesystem, environment, clock or network."""
    tree = _Tree(source)
    return [
        Finding(node.lineno, tree.scope[node], "io-call", _text(node))
        for node in tree.walk()
        if isinstance(node, ast.Call) and _called_name(node) in IO_CALL_NAMES
    ]


def _format(findings: list[Finding]) -> str:
    return "\n".join(f"    {finding.describe()}" for finding in findings)


@pytest.mark.parametrize(
    ("snippet", "kind"),
    [
        ("import hashlib\n", "hash-import"),
        ("import hashlib as h\nh.sha256(b'x')\n", "hash-import"),
        ("from hashlib import sha256\n", "hash-import"),
        ("import hmac\n", "hash-import"),
        ("import zlib\nzlib.crc32(b'x')\n", "hash-import"),
        ("from binascii import crc32\n", "hash-import"),
        ("import hashlib.sha3_256\n", "hash-import"),
        ("def f(b):\n    return hashlib.sha256(b).hexdigest()\n", "hash-call"),
        ("def f(b):\n    return lib.md5(b)\n", "hash-call"),
        ("def f(b):\n    return h.new('sha1').digest()\n", "hash-call"),
        ("import uuid\nuuid.uuid5(ns, name)\n", "hash-call"),
        ("m = __import__('hashlib')\n", "hash-dynamic-import"),
        ("m = importlib.import_module('hashlib')\n", "hash-dynamic-import"),
        ("def f(p):\n    return _compute_file_sha256(p)\n", "fingerprint-reference"),
        ("x = manifest.compute_identity\n", "fingerprint-reference"),
        ("def f(text):\n    return hash(text)\n", "builtin-hash"),
    ],
)
def test_hashing_scanner_detects_violations(snippet: str, kind: str) -> None:
    """The fingerprint scanner flags every hashing construct, whatever the aliasing."""
    assert kind in {finding.kind for finding in find_hashing_calls(snippet)}


@pytest.mark.parametrize(
    "snippet",
    [
        "import json\nfrom collections.abc import Mapping\n",
        "def f(x):\n    return json.dumps(x, sort_keys=True)\n",
        "class A:\n    def __hash__(self):\n        return hash(self.x)\n",
        "import secrets\n",
        "def f(d):\n    return sorted(d.items())\n",
        "digest_value = 3\nprint(digest_value)\n",
    ],
)
def test_hashing_scanner_allows_non_hashing_code(snippet: str) -> None:
    assert find_hashing_calls(snippet) == []


def test_hashing_scanner_reports_file_scope_and_line() -> None:
    findings = find_hashing_calls("import os\n\n\ndef outer():\n    def inner(b):\n        return sha256(b)\n")
    assert [(f.lineno, f.scope, f.kind) for f in findings] == [(6, "outer.inner", "hash-call")]


def test_io_scanner_detects_file_clock_and_network_access() -> None:
    probe = textwrap.dedent(
        """
        def f(path):
            data = open(path, 'rb').read()
            other = path.read_bytes()
            return time.time(), os.getenv('X'), socket.create_connection(('h', 1))
        """
    )
    assert {finding.snippet.split("(")[0] for finding in find_io_calls(probe)} >= {
        "open", "path.read_bytes", "time.time", "os.getenv", "socket.create_connection",
    }


def test_no_provenance_module_outside_identity_computes_a_fingerprint() -> None:
    """3.6: only ``identity.py`` imports a hashing module or calls a hashing primitive."""
    unexpected: list[str] = []
    used: set[tuple[str, str, str]] = set()
    for path in _py_files(PROVENANCE_DIR):
        if path == IDENTITY_PATH:
            continue
        for finding in find_hashing_calls(path.read_text(encoding="utf-8")):
            key = (_rel(path), *finding.key)
            if key in ALLOWED_HASHING_OUTSIDE_IDENTITY:
                used.add(key)
            else:
                unexpected.append(f"{_rel(path)}: {finding.describe()}")
    assert not unexpected, (
        "hashing/fingerprint code found outside provenance/identity.py (3.6: document, configuration "
        "and extraction-map fingerprints are consumed from the caller; digests of identifier "
        "material belong to the identity module):\n" + "\n".join(f"    {line}" for line in unexpected)
    )
    stale = sorted(set(ALLOWED_HASHING_OUTSIDE_IDENTITY) - used)
    assert not stale, f"stale ALLOWED_HASHING_OUTSIDE_IDENTITY entries (no longer match anything): {stale}"


def test_hashing_scan_is_not_vacuous_it_finds_identitys_own_hashing() -> None:
    """Dropping the ``identity.py`` exemption must make the scan fail: it sees real hashing there."""
    findings = find_hashing_calls(IDENTITY_PATH.read_text(encoding="utf-8"))
    kinds = {finding.kind for finding in findings}
    assert "hash-import" in kinds
    assert {f.scope for f in findings if f.kind == "hash-call"}, "no hashing call found in identity.py"


def test_identity_hashes_only_in_its_three_digest_functions() -> None:
    """2.3 / 3.6: identity digests identifier material, claim values and evidence text -- nothing else."""
    findings = find_hashing_calls(IDENTITY_PATH.read_text(encoding="utf-8"))
    outside = [
        f for f in findings if f.kind != "hash-import" and f.scope not in IDENTITY_HASHING_SCOPES
    ]
    assert not outside, (
        "identity.py hashes outside the digest functions "
        f"{sorted(IDENTITY_HASHING_SCOPES)} -- a document/config/extraction-map fingerprint "
        "must be computed by the caller (3.6):\n" + _format(outside)
    )
    hashing_scopes = {f.scope for f in findings if f.kind == "hash-call"}
    assert hashing_scopes == set(IDENTITY_HASHING_SCOPES), (
        f"stale IDENTITY_HASHING_SCOPES: allowed {sorted(IDENTITY_HASHING_SCOPES)}, found {sorted(hashing_scopes)}"
    )
    assert all(f.kind in {"hash-import", "hash-call"} for f in findings)


def test_identity_is_pure_so_it_cannot_fingerprint_a_file_or_a_configuration() -> None:
    """Identity imports only pure modules and makes no file, environment, clock or network call."""
    source = IDENTITY_PATH.read_text(encoding="utf-8")
    imported = {module for _, module in _imported_modules(_Tree(source))}
    assert imported <= IDENTITY_ALLOWED_IMPORTS, (
        f"identity.py imports {sorted(imported - IDENTITY_ALLOWED_IMPORTS)}; it must stay pure "
        f"(allowed: {sorted(IDENTITY_ALLOWED_IMPORTS)})"
    )
    io = find_io_calls(source)
    assert not io, "identity.py touches the filesystem, environment, clock or network:\n" + _format(io)


# ---------------------------------------------------------------------------
# Invariant 3 -- the privacy carrier performs no interpretation (9.4)
# ---------------------------------------------------------------------------

#: Names under which a label/decision *value* reaches code: the dataclass fields / parameters,
#: and the two mapping-key constants the carrier module reads them with.
_VALUE_NAMES = frozenset({"label", "decision"})
_VALUE_KEY_NAMES = frozenset({"_LABEL_KEY", "_DECISION_KEY"})

#: Privacy vocabulary that must never appear as a string literal: it would mean the module
#: knows about specific label or decision values (privacy-core owns that vocabulary).
PRIVACY_WORDS = (
    "public", "private", "restricted", "confidential", "secret", "sensitive", "classified",
    "internal", "phi", "pii", "redact", "redacted", "redaction", "allow", "allowed", "deny",
    "denied", "release", "withhold", "withheld", "consent", "anonymous", "deidentified",
    "de-identified", "masked",
)
_PRIVACY_WORD_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in PRIVACY_WORDS) + r")\b", re.IGNORECASE)
_VALUE_CONSUMERS = frozenset({"isinstance", "type", "PrivacyCarrier"})


def _is_value_ref(node: ast.AST) -> bool:
    """True when *node* reads a label/decision value (name, attribute, key lookup or ``.get``)."""

    def is_key(key: ast.AST) -> bool:
        if isinstance(key, ast.Constant):
            return key.value in _VALUE_NAMES
        return isinstance(key, ast.Name) and key.id in _VALUE_KEY_NAMES

    if isinstance(node, ast.Name):
        return node.id in _VALUE_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _VALUE_NAMES
    if isinstance(node, ast.Subscript):
        return is_key(node.slice)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "get" and bool(node.args) and is_key(node.args[0])
    return False


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _contains_value_ref(node: ast.AST) -> bool:
    return any(_is_value_ref(sub) for sub in ast.walk(node))


def find_label_interpretation(source: str) -> list[Finding]:
    """Every place *source* compares, branches on, looks up by, or string-processes a label/decision value.

    Forbidden: ``match``; a comparison with a value operand other than ``is None`` /
    ``is not None``; a comparison with any string literal operand; a comparison that is not
    ``is None`` or a key-membership test on a declared key constant; a method call on a value;
    a value used as a subscript key, or passed to any callable other than ``isinstance`` /
    ``type`` / ``PrivacyCarrier``; truthiness of a bare value (``if label``, ``not label``,
    ``label or x``).
    """
    tree = _Tree(source)
    findings: list[Finding] = []

    def add(node: ast.AST, kind: str) -> None:
        findings.append(Finding(node.lineno, tree.scope[node], kind, _text(node)))

    for node in tree.walk():
        if isinstance(node, ast.Match):
            add(node, "match-statement")
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            has_value = any(_contains_value_ref(op) for op in operands)
            is_none_check = all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) and any(
                _is_none(op) for op in operands
            )
            is_key_membership = (
                all(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                and isinstance(node.left, ast.Name)
                and node.left.id in _VALUE_KEY_NAMES
                and not any(_contains_value_ref(c) for c in node.comparators)
            )
            if has_value and not is_none_check:
                add(node, "comparison-on-value")
            elif not (is_none_check or is_key_membership):
                add(node, "unsanctioned-comparison")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and _is_value_ref(node.func.value):
                add(node, "method-on-value")
            callee = _called_name(node)
            if callee not in _VALUE_CONSUMERS and not isinstance(node.func, ast.Attribute):
                args = [*node.args, *(kw.value for kw in node.keywords)]
                if any(_is_value_ref(arg) for arg in args):
                    add(node, "value-passed-to-callable")
            elif isinstance(node.func, ast.Attribute) and callee != "get":
                args = [*node.args, *(kw.value for kw in node.keywords)]
                if any(_is_value_ref(arg) for arg in args):
                    add(node, "value-passed-to-callable")
        elif isinstance(node, ast.Subscript):
            if _contains_value_ref(node.slice) and not _is_value_ref(node):
                add(node, "lookup-by-value")
        elif isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            if _is_value_ref(node.test):
                add(node, "truthiness-of-value")
        elif isinstance(node, ast.BoolOp):
            if any(_is_value_ref(value) for value in node.values):
                add(node, "truthiness-of-value")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            if _is_value_ref(node.operand):
                add(node, "truthiness-of-value")
    return findings


def find_privacy_vocabulary(source: str) -> list[Finding]:
    """Every non-docstring string literal in *source* containing a privacy word."""
    tree = _Tree(source)
    findings: list[Finding] = []
    for node in tree.walk():
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in tree.docstrings
            and _PRIVACY_WORD_RE.search(node.value)
        ):
            findings.append(Finding(node.lineno, tree.scope[node], "privacy-word", repr(node.value)))
    return findings


@pytest.mark.parametrize(
    ("snippet", "kind"),
    [
        ('if label == "PHI":\n    pass\n', "comparison-on-value"),
        ('if carrier.decision != "release":\n    pass\n', "comparison-on-value"),
        ('ok = carrier_input["label"] in ("a", "b")\n', "comparison-on-value"),
        ('ok = carrier_input.get("decision") == other\n', "comparison-on-value"),
        ("ok = label < other\n", "comparison-on-value"),
        ("ok = decision in ALLOWED\n", "comparison-on-value"),
        ("ok = other in (label, decision)\n", "comparison-on-value"),
        ('ok = state == "labeled"\n', "unsanctioned-comparison"),
        ("ok = n > 3\n", "unsanctioned-comparison"),
        ("ok = x is label\n", "comparison-on-value"),
        ("x = label.lower()\n", "method-on-value"),
        ("x = carrier.decision.startswith('r')\n", "method-on-value"),
        ('match label:\n    case "PHI":\n        pass\n', "match-statement"),
        ("x = TABLE[label]\n", "lookup-by-value"),
        ("x = TABLE[carrier_input[_LABEL_KEY]]\n", "lookup-by-value"),
        ("x = normalize(label)\n", "value-passed-to-callable"),
        ("x = policy.check(decision)\n", "value-passed-to-callable"),
        ("if label:\n    pass\n", "truthiness-of-value"),
        ("x = label or 'default'\n", "truthiness-of-value"),
        ("x = 1 if not decision else 2\n", "truthiness-of-value"),
    ],
)
def test_label_interpretation_scanner_detects_violations(snippet: str, kind: str) -> None:
    assert kind in {finding.kind for finding in find_label_interpretation(snippet)}


@pytest.mark.parametrize(
    "snippet",
    [
        "ok = label is None\n",
        "ok = carrier.decision is not None\n",
        "ok = isinstance(label, str)\n",
        "ok = type(decision).__name__\n",
        "extra = set(carrier_input) - KEYS\n",
        "ok = _LABEL_KEY not in carrier_input\n",
        "c = PrivacyCarrier(label=carrier_input[_LABEL_KEY], decision=carrier_input.get(_DECISION_KEY))\n",
        "d = carrier_input.get(_DECISION_KEY)\n",
    ],
)
def test_label_interpretation_scanner_allows_structural_handling(snippet: str) -> None:
    assert find_label_interpretation(snippet) == []


@pytest.mark.parametrize(
    "snippet",
    [
        'x = "public"\n',
        'if v == "Restricted": pass\n',
        'x = f"label is {v} PHI"\n',
        'LEVELS = ("pii", "other")\n',
        'X = "de-identified"\n',
        'X = "REDACT"\n',
    ],
)
def test_privacy_vocabulary_scanner_detects_violations(snippet: str) -> None:
    assert find_privacy_vocabulary(snippet)


def test_privacy_vocabulary_scanner_ignores_docstrings_and_ordinary_words() -> None:
    source = '"""Holds a private label (PHI)."""\n\ndef f():\n    """Allow nothing."""\n    return "labeled"\n'
    assert find_privacy_vocabulary(source) == []


def test_privacy_carrier_module_does_not_interpret_label_or_decision_values() -> None:
    """9.4: no comparison against, branch on, or normalization of a label/decision value."""
    source = PRIVACY_CARRIER_PATH.read_text(encoding="utf-8")
    violations = find_label_interpretation(source)
    assert not violations, (
        "privacy_carrier.py interprets a privacy label/decision value (9.4); the carrier "
        "must hold them verbatim and inert:\n" + _format(violations)
    )


def test_privacy_carrier_module_names_no_privacy_label_value() -> None:
    """9.4: no privacy vocabulary hard-coded as a string literal."""
    violations = find_privacy_vocabulary(PRIVACY_CARRIER_PATH.read_text(encoding="utf-8"))
    assert not violations, (
        "privacy_carrier.py hard-codes privacy vocabulary (privacy-core owns label values):\n"
        + _format(violations)
    )


def test_privacy_carrier_scan_is_not_vacuous() -> None:
    """The real module has the very constructs the scanner has to judge (key lookups, ``is None``)."""
    source = PRIVACY_CARRIER_PATH.read_text(encoding="utf-8")
    tree = _Tree(source)
    assert sum(isinstance(n, ast.Compare) for n in tree.walk()) >= 2
    assert any(_is_value_ref(n) for n in tree.walk())


def test_no_provenance_module_compares_a_carriers_label_or_decision() -> None:
    """9.4 beyond the carrier: validators and adapters check a carrier's structure, never its values."""
    violations: list[str] = []
    for path in _py_files(PROVENANCE_DIR):
        if path == PRIVACY_CARRIER_PATH:
            continue
        for finding in find_label_interpretation(path.read_text(encoding="utf-8")):
            # Outside the carrier only the *value-reading* rules apply; the carrier's own
            # key-constant discipline (``unsanctioned-comparison``) is not meaningful elsewhere.
            if finding.kind != "unsanctioned-comparison":
                violations.append(f"{_rel(path)}: {finding.describe()}")
    assert not violations, "a provenance module interprets a privacy label/decision (9.4):\n" + "\n".join(
        f"    {line}" for line in violations
    )


# ---------------------------------------------------------------------------
# Invariant 4 -- one evidence-identifier construction (4.8)
# ---------------------------------------------------------------------------

SEPARATOR_NAME = "EVIDENCE_SCOPE_SEPARATOR"

_CONSTRUCTOR_NAME_RE = re.compile(
    r"^_*(?:format|make|build|compose|derive|create|join|scope|scoped)\w*evidence\w*id\w*$"
    r"|^_*\w*evidence\w*id\w*_(?:format|make|build|compose)\w*$",
    re.IGNORECASE,
)
_SOURCE_ID_RE = re.compile(r"source_?id|src_?id|source_?node|\bsrc\b", re.IGNORECASE)
_LOCAL_ID_RE = re.compile(r"local_?id|evidence_?id|ev_?id|loc_?id|\bloc\b", re.IGNORECASE)
_SNIPPET_NODES = (ast.JoinedStr, ast.Call, ast.Compare, ast.BinOp, ast.Assign, ast.AnnAssign)


def find_evidence_id_constructions(source: str) -> list[Finding]:
    """Every place *source* handles the ``#`` evidence scope separator or builds a scoped identifier.

    Flagged: a string literal containing ``#`` and no whitespace (an identifier cannot contain
    whitespace, so prose and shell comments are not separators) -- this covers f-strings,
    ``%``/``.format``/``+`` concatenation, ``"#".join``, ``.split("#")`` / ``.partition("#")`` and
    membership tests; any reference to ``EVIDENCE_SCOPE_SEPARATOR`` (also under an import alias);
    an f-string interpolating both a source-id-like and a local-id-like expression (a scoped
    identifier built with some other separator); and any function named like an evidence-id
    constructor. Docstrings are exempt.
    """
    tree = _Tree(source)
    findings: list[Finding] = []
    aliases = {SEPARATOR_NAME}
    for node in tree.walk():
        if isinstance(node, ast.ImportFrom):
            aliases.update(a.asname for a in node.names if a.name == SEPARATOR_NAME and a.asname)

    def add(node: ast.AST, kind: str) -> None:
        anchor = tree.enclosing(node, _SNIPPET_NODES) or node
        findings.append(Finding(node.lineno, tree.scope[node], kind, _text(anchor)))

    for node in tree.walk():
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in tree.docstrings:
            if "#" in node.value and not any(ch.isspace() for ch in node.value):
                add(node, "separator-literal")
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in aliases:
                add(node, "separator-name")
        elif isinstance(node, ast.JoinedStr):
            values = [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
            if any(_SOURCE_ID_RE.search(_text(v)) for v in values) and any(
                _LOCAL_ID_RE.search(_text(v)) for v in values
            ):
                add(node, "scoped-id-interpolation")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _CONSTRUCTOR_NAME_RE.match(node.name):
                findings.append(Finding(node.lineno, tree.scope[node], "constructor-function", f"def {node.name}"))
    return findings


_IDENTITY_REL = "provenance/identity.py"

#: Every handling of the ``#`` separator outside the sanctioned constructor:
#: ``(file relative to src/, enclosing scope, expression)`` -> why it is legitimate.
#: An entry that matches nothing is an error, so this list cannot rot.
ALLOWED_SEPARATOR_USES: dict[tuple[str, str, str], str] = {
    # -- provenance/identity.py: the owner. It defines the separator, constructs/parses/validates
    #    evidence ids and builds *claim* ids (which are not evidence ids) with the same separator.
    (_IDENTITY_REL, "<module>", "EVIDENCE_SCOPE_SEPARATOR = '#'"): "the one definition of the separator",
    (_IDENTITY_REL, "_check_fingerprint", "EVIDENCE_SCOPE_SEPARATOR in fingerprint"): (
        "rejects a fingerprint that would make an identifier ambiguous"
    ),
    (_IDENTITY_REL, "_check_fingerprint", "f'{what} must not contain the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}'"): (
        "error message naming the separator"
    ),
    (_IDENTITY_REL, "format_evidence_id", "def format_evidence_id"): "the sanctioned constructor (4.8)",
    (_IDENTITY_REL, "format_evidence_id", "EVIDENCE_SCOPE_SEPARATOR in local_id"): (
        "rejects a local id that would make the identifier ambiguous"
    ),
    (_IDENTITY_REL, "format_evidence_id", "f'local_id must not contain the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}, got {local_id!r}'"): (
        "error message naming the separator"
    ),
    (_IDENTITY_REL, "format_evidence_id", "f'{source_id}{EVIDENCE_SCOPE_SEPARATOR}{local_id}'"): (
        "THE evidence identifier construction (4.8)"
    ),
    (_IDENTITY_REL, "parse_evidence_id", "node_id.partition(EVIDENCE_SCOPE_SEPARATOR)"): (
        "the sanctioned parser, the total inverse of format_evidence_id (4.8)"
    ),
    (_IDENTITY_REL, "parse_evidence_id", "f'evidence node_id {node_id!r} lacks the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}'"): (
        "error message naming the separator"
    ),
    (_IDENTITY_REL, "derive_claim_id", "f'{CLAIM_ID_PREFIX}{source_id}{EVIDENCE_SCOPE_SEPARATOR}f{field_index}'"): (
        "builds a *claim* identifier (claim:<source_id>#f<n>), a different record kind that shares the scope separator"
    ),
    # -- provenance/graph.py: owner of the shared attribution rules (4.9, pinned by test_provenance_attribution.py).
    ("provenance/graph.py", "_scope_of", "text.partition(EVIDENCE_SCOPE_SEPARATOR)"): (
        "attribution helper: the src: scope of a source, evidence or claim id; reads only the scope part"
    ),
    ("provenance/graph.py", "stage_segment_document", "EVIDENCE_SCOPE_SEPARATOR not in ids[0]"): (
        "attribution helper: tells a bare source id from a scoped id; builds nothing"
    ),
    # -- provenance/adapters/claim_adapter.py: resolves caller-supplied references through identity.
    ("provenance/adapters/claim_adapter.py", "_EvidenceIndex.resolve", "EVIDENCE_SCOPE_SEPARATOR in reference"): (
        "chooses parse_evidence_id (already scoped) or format_evidence_id (bare local id); constructs nothing itself"
    ),
    ("provenance/adapters/claim_adapter.py", "_EvidenceIndex.candidates", "EVIDENCE_SCOPE_SEPARATOR in reference"): (
        "chooses parse_evidence_id for a scoped reference; constructs nothing itself"
    ),
    # -- consumers in pipeline/.
    ("pipeline/claim_provenance.py", "claim_issue_sink.wrapped", "EVIDENCE_SCOPE_SEPARATOR in reference"): (
        "membership test: a scoped reference is judged by the claim adapter, so this sink drops its issue; builds nothing"
    ),
    ("pipeline/validator.py", "_near_matches", "'#' in reference"): (
        "membership test: a scoped (full) evidence id is judged by the claim adapter; builds nothing. "
        "Hard-codes the separator literal rather than importing it (pipeline.validator does not import provenance)"
    ),
    ("pipeline/evidence_index.py", "_figure_section_map", "target.startswith('#')"): (
        "TEI/XML internal-reference syntax (<ref target=\"#id\">), unrelated to evidence identifiers"
    ),
    # -- quality_control/structure_validator.py: JSON-Schema reference syntax.
    ("quality_control/structure_validator.py", "StructureSchemaValidator._validate", "prefix = '#/$defs/'"): "JSON Schema $ref pointer prefix",
    ("quality_control/structure_validator.py", "StructureSchemaValidator._validate", "f'#/$defs/{type_name}'"): "JSON Schema $ref pointer",
}


def _all_separator_findings() -> dict[tuple[str, str, str], list[str]]:
    hits: dict[tuple[str, str, str], list[str]] = {}
    for path in _py_files(SRC_ROOT):
        for finding in find_evidence_id_constructions(path.read_text(encoding="utf-8")):
            hits.setdefault((_rel(path), *finding.key), []).append(f"{_rel(path)}: {finding.describe()}")
    return hits


@pytest.mark.parametrize(
    ("snippet", "kind"),
    [
        ('def f(s, l):\n    return f"{s}#{l}"\n', "separator-literal"),
        ('def f(s, l):\n    return s + "#" + l\n', "separator-literal"),
        ('def f(s, l):\n    return "%s#%s" % (s, l)\n', "separator-literal"),
        ('def f(s, l):\n    return "{}#{}".format(s, l)\n', "separator-literal"),
        ('def f(s, l):\n    return "#".join([s, l])\n', "separator-literal"),
        ('def f(i):\n    return i.split("#")\n', "separator-literal"),
        ('def f(i):\n    return i.rpartition("#")[2]\n', "separator-literal"),
        ('def f(i):\n    return "#" in i\n', "separator-literal"),
        ("def f(i):\n    return i.partition(EVIDENCE_SCOPE_SEPARATOR)\n", "separator-name"),
        ("def f(s, l):\n    return s + identity.EVIDENCE_SCOPE_SEPARATOR + l\n", "separator-name"),
        (
            "from provenance.identity import EVIDENCE_SCOPE_SEPARATOR as SEP\n\ndef f(s, l):\n    return s + SEP + l\n",
            "separator-name",
        ),
        ('def f(source_id, local_id):\n    return f"{source_id}::{local_id}"\n', "scoped-id-interpolation"),
        ('def f(src, evidence_id):\n    return f"{src}/{evidence_id}"\n', "scoped-id-interpolation"),
        ("def make_evidence_id(s, l):\n    return s\n", "constructor-function"),
        ("def _build_scoped_evidence_id(s, l):\n    return s\n", "constructor-function"),
        ("class A:\n    def format_evidence_id(self, s, l):\n        return s\n", "constructor-function"),
    ],
)
def test_evidence_id_scanner_detects_second_constructions(snippet: str, kind: str) -> None:
    assert kind in {finding.kind for finding in find_evidence_id_constructions(snippet)}


@pytest.mark.parametrize(
    "snippet",
    [
        '"""Evidence ids look like src:abc#e1."""\n\ndef f():\n    """src:abc#e1"""\n    return 1\n',
        "def f(i):\n    return i.strip()\n",
        'def f():\n    return "see section # 3 of the plan"\n',
        'def f(s, l):\n    return f"{s}: {l}"\n',
        "def parse_evidence_id(x):\n    return x\n",
        "def collect_protected_evidence_ids(x):\n    return x\n",
        "x = 3  # a trailing comment mentioning src:abc#e1\n",
    ],
)
def test_evidence_id_scanner_allows_unrelated_code(snippet: str) -> None:
    assert find_evidence_id_constructions(snippet) == []


def test_evidence_id_scanner_reports_scope_and_expression() -> None:
    findings = find_evidence_id_constructions('class A:\n    def m(self, s, l):\n        return f"{s}#{l}"\n')
    assert [(f.lineno, f.scope, f.snippet) for f in findings] == [(3, "A.m", "f'{s}#{l}'")]


def test_only_the_identity_module_constructs_evidence_ids() -> None:
    """4.8: across all of ``src/``, every handling of the ``#`` scope separator is allow-listed and justified."""
    hits = _all_separator_findings()
    unexpected = sorted(line for key, lines in hits.items() if key not in ALLOWED_SEPARATOR_USES for line in lines)
    assert not unexpected, (
        "a second evidence-identifier construction (or separator handling) was added; use "
        "provenance.identity.format_evidence_id / parse_evidence_id (4.8), or, if it is a "
        "legitimate non-identifier use, add a justified entry to ALLOWED_SEPARATOR_USES:\n"
        + "\n".join(f"    {line}" for line in unexpected)
    )
    stale = sorted(set(ALLOWED_SEPARATOR_USES) - set(hits))
    assert not stale, (
        "stale ALLOWED_SEPARATOR_USES entries (they no longer match anything; remove or update them):\n"
        + "\n".join(f"    {entry}" for entry in stale)
    )


def test_evidence_id_scan_is_not_vacuous_it_finds_the_real_constructor() -> None:
    """If the scanner could not see ``format_evidence_id``'s own f-string it would prove nothing."""
    hits = _all_separator_findings()
    construction = (
        _IDENTITY_REL,
        "format_evidence_id",
        "f'{source_id}{EVIDENCE_SCOPE_SEPARATOR}{local_id}'",
    )
    assert construction in hits
    assert ("provenance/identity.py", "format_evidence_id", "def format_evidence_id") in hits


def test_evidence_id_constructor_exists_exactly_once_in_src() -> None:
    constructors = [
        f"{_rel(path)}: {finding.describe()}"
        for path in _py_files(SRC_ROOT)
        for finding in find_evidence_id_constructions(path.read_text(encoding="utf-8"))
        if finding.kind == "constructor-function"
    ]
    assert len(constructors) == 1 and "format_evidence_id" in constructors[0], constructors


# ---------------------------------------------------------------------------
# Invariant 1 -- import isolation (1.5)
# ---------------------------------------------------------------------------

#: Packages ``provenance`` must be importable without. ``pipeline`` is the integration point
#: that imports provenance, never the reverse; the rest are sibling packages and heavy dependencies.
BLOCKED_PACKAGES = (
    "pipeline", "quality_control", "agents", "pdf_extractor", "text_processing", "artifact_generation",
)
#: Network clients that must not even be imported (no connection can be needed, so no client is).
NETWORK_MODULES = (
    "openai", "requests", "httpx", "urllib3", "aiohttp", "http.client", "urllib.request", "ssl",
    "anthropic", "httpcore",
)
#: Heavy optional dependencies that must stay lazy (CLAUDE.md) and are never needed by provenance.
HEAVY_MODULES = ("torch", "faiss", "sentence_transformers", "paddleocr", "fitz", "pdfplumber")

# Runs inside the fresh interpreter, *before* anything of the project is imported.
_ISOLATION_PRELUDE = r"""
import importlib.abc, json, os, socket, sys

SRC = {src!r}
BLOCKED = {blocked!r}
sys.path.insert(0, SRC)

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("import of " + name + " is blocked by the isolation test")
        return None

sys.meta_path.insert(0, _Blocker())
for _name in BLOCKED:
    sys.modules.pop(_name, None)
    sys.modules[_name] = None  # a None entry also makes a cached-style import fail

class _NoNetwork(socket.socket):
    def __init__(self, *args, **kwargs):
        raise OSError("network access is disabled by the isolation test")

def _no_network(*args, **kwargs):
    raise OSError("network access is disabled by the isolation test")

socket.socket = _NoNetwork
socket.create_connection = _no_network
socket.getaddrinfo = _no_network
socket.gethostbyname = _no_network
socket.gethostbyname_ex = _no_network
"""

_IMPORT_ALL_BODY = r"""
import importlib

imported = []
for _module in {modules!r}:
    importlib.import_module(_module)
    imported.append(_module)

loaded = sorted(sys.modules[m].__name__ for m in sys.modules if sys.modules[m] is not None)
def _present(prefixes):
    return sorted({{m for m in loaded if any(m == p or m.startswith(p + ".") for p in prefixes)}})
print(json.dumps({{
    "imported": imported,
    "blocked_loaded": _present({blocked!r}),
    "network_loaded": _present({network!r}),
    "heavy_loaded": _present({heavy!r}),
    "utils_loaded": _present(["utils"]),
    "api_key_visible": any(k.startswith(("OPENAI_", "ANTHROPIC_")) for k in os.environ),
}}))
"""


def _run_isolated(body: str, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run *body* in a fresh ``-I`` interpreter from the repo root, behind the isolation prelude."""
    script = _ISOLATION_PRELUDE.format(src=str(SRC_ROOT), blocked=BLOCKED_PACKAGES) + body
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "ANTHROPIC_", "PYTHONPATH"))
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _all_provenance_modules() -> list[str]:
    return sorted(_module_name(path) for path in _py_files(PROVENANCE_DIR))


def test_every_provenance_module_imports_without_pipeline_quality_control_network_or_credentials() -> None:
    """1.5: the whole package imports with sibling packages unimportable, networking dead, no API key."""
    modules = _all_provenance_modules()
    assert "provenance" in modules and "provenance.identity" in modules
    assert any(m.startswith("provenance.adapters.") for m in modules)

    body = _IMPORT_ALL_BODY.format(
        modules=modules, blocked=BLOCKED_PACKAGES, network=NETWORK_MODULES, heavy=HEAVY_MODULES
    )
    result = _run_isolated(body)
    assert result.returncode == 0, (
        "provenance failed to import in isolation (a forbidden package was imported, or networking / "
        f"a credential was needed):\n{result.stderr[-1200:]}"
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["imported"] == modules
    assert report["blocked_loaded"] == [], "provenance pulled in a sibling package"
    assert report["network_loaded"] == [], "provenance pulled in a network client"
    assert report["heavy_loaded"] == [], "provenance pulled in a heavy optional dependency"
    assert report["api_key_visible"] is False
    # The only project dependency is ``utils`` (``import provenance`` -> utils.logging_utils -> utils.path_utils).
    assert "utils" in report["utils_loaded"]


def test_importing_the_package_leaves_blocked_packages_out_of_sys_modules() -> None:
    """The headline form of 1.5, on the package root alone (what ``pipeline`` actually imports)."""
    result = _run_isolated(
        "import provenance\n"
        "bad = sorted(m for m in sys.modules if sys.modules[m] is not None and m.split('.')[0] in BLOCKED)\n"
        "print(json.dumps(bad))\n"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_isolation_harness_is_not_vacuous() -> None:
    """The harness really blocks sibling packages and the network, and really strips credentials."""
    for package in ("pipeline", "quality_control", "agents", "pdf_extractor"):
        blocked = _run_isolated(f"import {package}\n")
        assert blocked.returncode != 0, f"importing {package} was not blocked"
        assert "ModuleNotFoundError" in blocked.stderr, blocked.stderr
        submodule = _run_isolated(f"import importlib\nimportlib.import_module({package + '.x'!r})\n")
        assert submodule.returncode != 0, f"importing {package}.x was not blocked"
        # The meta-path finder blocks on its own, without the ``sys.modules`` sentinel.
        finder_only = _run_isolated(f"del sys.modules[{package!r}]\nimport {package}\n")
        assert finder_only.returncode != 0 and "blocked by the isolation test" in finder_only.stderr, (
            finder_only.stderr
        )

    network = _run_isolated(
        "try:\n"
        "    socket.create_connection(('example.com', 80), timeout=1)\n"
        "except OSError as exc:\n"
        "    print('refused:', exc)\n"
        "else:\n"
        "    print('connected')\n"
        "try:\n"
        "    socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "except OSError as exc:\n"
        "    print('refused:', exc)\n"
        "else:\n"
        "    print('connected')\n"
    )
    assert network.returncode == 0, network.stderr
    assert network.stdout.count("refused: network access is disabled") == 2

    # A credential in the parent environment is stripped before the child starts.
    previous = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"
    try:
        stripped = _run_isolated("print(json.dumps('OPENAI_API_KEY' in os.environ))\n")
    finally:
        if previous is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous
    assert stripped.returncode == 0, stripped.stderr
    assert json.loads(stripped.stdout.strip().splitlines()[-1]) is False


def test_provenance_source_does_not_name_a_blocked_package_or_network_client_in_an_import() -> None:
    """Static companion to the runtime check: no import statement under ``src/provenance`` names one."""
    forbidden = set(BLOCKED_PACKAGES) | {m.split(".")[0] for m in NETWORK_MODULES} | set(HEAVY_MODULES)
    offenders = [
        f"{_rel(path)}:{node.lineno}: {module}"
        for path in _py_files(PROVENANCE_DIR)
        for node, module in _imported_modules(_Tree(path.read_text(encoding="utf-8")))
        if module.split(".")[0] in forbidden
    ]
    assert not offenders, "provenance imports a forbidden package:\n" + "\n".join(f"    {o}" for o in offenders)
