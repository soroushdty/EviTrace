"""Regression guard: provenance integration never changes the shared paper prompt prefix (task 7.3).

``agents.openai.prompts._shared_paper_prefix`` is what the OpenAI prompt cache keys on: it must be
byte-identical across the cache warm-up, every extraction chunk and the synthesis chunk of one
paper, and -- the point of this module -- it must be byte-identical *whether or not provenance is
being recorded*. Provenance-core (Requirement 7.6, design "PipelineIntegration": "All emission
occurs after prompt assembly ... No provenance value may enter ``_shared_paper_prefix``") adds
emission points to the evidence index and the per-PDF processor; none of them may leak a source
id, claim id, run id, derivation id, timestamp or any other provenance value into the bytes the
model is billed and cached on.

What is pinned, and why each piece exists:

1. **Literal goldens** (``_GOLDEN``): ``sha256`` and byte length of the shared prefix, per real
   GROBID fixture, for the real pipeline functions ``_build_items_from_tei`` ->
   ``select_paper_evidence`` -> ``serialise_evidence_package`` -> ``_shared_paper_prefix`` with
   fixed ``paper_id`` / ``source_pdf``, a fixed field list, no add-ons, no cache directory and no
   clock.  Two selections per fixture: ``full`` (nothing pruned -- every item's serialised bytes
   are covered) and ``pruned`` (production-style caps, so the ranking/selection order is covered
   too).  A second golden set (``_GOLDEN_BUNDLE``) pins the same prefix built from the bundle the
   production entry point ``build_or_load_evidence_bundle`` returns -- that path also runs the
   add-on enrichment stage (heuristic quantity/dataset/entity annotations, no network), so its
   bytes differ from the direct ``_build_items_from_tei`` path and need their own pin.
2. **Wrapper literal**: ``_shared_paper_prefix(x)`` is exactly the documented header, ``x`` and
   the documented footer, so nothing can be added around the package either, and its signature
   still takes only the package string.
3. **Cross-call stability**: warm-up, chunk and synthesis messages for one paper all start with
   the identical prefix whatever the chunk field list or prior context (the existing technique,
   re-asserted here against the goldens' bytes).
4. **Live recorder**: ``build_or_load_evidence_bundle(..., recorder=, source_id=)`` with a real
   ``ProvenanceRecorder`` (enabled, disabled, miss path, hit path) yields a bundle that
   serialises to the golden prefix bytes -- the integration point that touches evidence cannot
   leak into the prompt.
5. **Negative guard**: the evidence package keeps exactly the pre-provenance envelope and item
   keys, and neither it nor the prefix contains provenance vocabulary beyond what the paper text
   itself contains.

The goldens are *post-6.6 by design*. Provenance-core task 6.6 made one declared prompt-content
change -- caption sentences gained the enclosing figure's ``page`` / ``coords`` (see
``tasks.md`` Implementation Notes, 6.6, and ``test_evidence_index_caption_location.py``).  The
digests below were computed with ``compute_prefix_digests`` at the 6.6 commit (``f0ffdeb``), at
the current tree, and at the commit immediately before 6.6 (``c13a8d3``): the first two are
identical -- no provenance integration after 6.6 (6.2 evidence nodes, 6.3 source/QC, 6.4 claims,
6.5 recorder lifecycle) changed a prefix byte -- and ``c13a8d3`` differs *only* by the 6.6
caption-location change.

**Changing a golden is a declared prompt-content change**, not a test fix: it invalidates every
cached prompt prefix in the field, so it needs a note in ``tasks.md`` Implementation Notes and a
``CHANGELOG.md`` entry (``.kiro/steering/changelog-rules.md``).  If a golden fails and you did not
mean to change the prompt, your change leaked into the prefix -- fix the change, not the digest.

Everything here imports ``pipeline`` / ``agents`` inside functions: sibling test modules delete
and re-stub ``pipeline*`` modules in ``sys.modules`` and the bundle/prompts objects must be taken
from whatever is current when a test runs.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "grobid_tei"
_FIXTURE_FILES = {
    "biorxiv": "biorxiv_2020.03.24.004655.tei.xml",
    "arxiv": "arxiv_2003.10218.tei.xml",
    "plosone": "plosone_journal.pone.0230405.tei.xml",
}
_FIXTURES = list(_FIXTURE_FILES)

_PAPER_ID = "paper"
_SOURCE_PDF = ""

#: Fixed extraction fields driving the ranking. Not read from ``configs/extraction_map.json`` so
#: that editing the field map cannot move the goldens.
_FIELDS = [
    {
        "field_index": 8,
        "field_name": "Dataset",
        "definition": "sample population cohort participants patients",
        "reviewer_question": "Which dataset or cohort was used?",
    },
    {
        "field_index": 20,
        "field_name": "Model",
        "definition": "network architecture training objective",
        "reviewer_question": "What model was trained?",
    },
    {
        "field_index": 33,
        "field_name": "Outcome",
        "definition": "primary endpoint measured result accuracy",
        "reviewer_question": "",
    },
]
#: ``(max_items, max_chars)`` of the two selections: nothing pruned / production-style caps.
_SELECTIONS = {"full": (10_000, 10_000_000), "pruned": (60, 6_000)}

#: ``{fixture: {selection: (sha256 of the UTF-8 prefix, byte length)}}``.  Post-6.6 (see module
#: docstring).  Identical at f0ffdeb (6.6) and at the tree that introduced this module.
_GOLDEN: dict[str, dict[str, tuple[str, int]]] = {
    "biorxiv": {
        "full": ("bfc9c5d0b3665e5bb07d699ac0968717b9f14c0f1fa759ce2b3a25c32a202fd7", 60152),
        "pruned": ("d1e84700c5eea0de325af8f1d075a91e50344ba355cb2e8097d193e9e9d25b1e", 8835),
    },
    "arxiv": {
        "full": ("70ce9a36c0e737ac4caf72e5dc2ebc4bfd19fe2f08ce4919d573c3594ad2a9c7", 177671),
        "pruned": ("b7baa57e076eeea3fce09d89f4618d851e047987e33a9d560aafb91391a5f443", 8978),
    },
    "plosone": {
        "full": ("df0b06b263e97932ab61ba0d3b2f4e85de3bd4d99ccb5a9fd06ff528f10c5850", 91429),
        "pruned": ("e859f36b6fca54ab9dbf491913357c3c13bc1fc0b60b1c4a46079b664373d5cd", 8910),
    },
}

#: Same shape as ``_GOLDEN`` for bundles from ``build_or_load_evidence_bundle`` (``addons: {}``,
#: so only the offline heuristic annotations are added), source_pdf_path ``""``.
_GOLDEN_BUNDLE: dict[str, dict[str, tuple[str, int]]] = {
    "biorxiv": {
        "full": ("3e3c8a72c2fc383611aca9b7b4149e0fc8ad8408da750758bb1f74f9d15b6400", 108656),
        "pruned": ("e1eaa7eaf51a548cab9eff74bcfcf6baaa6c094667731fb4241097be1017dfab", 15657),
    },
    "arxiv": {
        "full": ("5462b130a2a3901462d34b540c04929b2346c1eddc72d65c7924e40ae44b3005", 296826),
        "pruned": ("0e55fe597d373750a1fae4df5145d0cbd6315daa268009cb89cd17750b2cdfa2", 14562),
    },
    "plosone": {
        "full": ("7babafa09d54132d06bde2847b8fb708ba793923ba2038f513e6f35da20d9bbd", 152872),
        "pruned": ("1e60a08f56807028cdae9111392c6610eccafb0f079d9ecd043a5318af8c30f2", 13650),
    },
}

#: Provenance vocabulary that must not appear in the prompt unless the paper text has it already.
_PROVENANCE_VOCABULARY = (
    "src:",
    "claim:",
    "run:",
    "drv:",
    "val:",
    "finding:",
    "M-first-author",
    "M-publication-date",
    "provenance",
)

#: The evidence package envelope and item shape as of the pre-provenance prompt.
_ENVELOPE_KEYS = ["paper_id", "evidence_count", "evidence"]
_ITEM_KEYS = ["id", "type", "section", "page", "coords", "text", "annotations"]

_PREFIX_HEADER = (
    "SHARED EVIDENCE PACKAGE\n"
    "The following compact evidence package is the only source of evidence for this extraction.\n"
    "Do not use prior knowledge or outside sources.\n"
    "\n"
    "EVIDENCE PACKAGE JSON:\n"
)
_PREFIX_FOOTER = "\n\nEND SHARED EVIDENCE PACKAGE\n"


# ---------------------------------------------------------------------------
# Computation (also run, via importlib, against older source trees to capture the goldens)
# ---------------------------------------------------------------------------


def _modules():
    """``(pipeline.evidence_index, agents.openai.prompts)`` as currently importable."""
    return importlib.import_module("pipeline.evidence_index"), importlib.import_module(
        "agents.openai.prompts"
    )


def _tei_text(name: str) -> str:
    return (_FIXTURE_DIR / _FIXTURE_FILES[name]).read_text(encoding="utf-8")


def _bundle_from_items(ei, items: list[dict]):
    return ei.EvidenceBundle(
        paper_id=_PAPER_ID,
        tei_xml="",
        evidence_items=items,
        evidence_map={i["id"]: i for i in items},
        prefilled_fields={},
        index_path=Path("unused.evidence.json"),
    )


def _package_for_bundle(ei, bundle, selection: str) -> str:
    max_items, max_chars = _SELECTIONS[selection]
    selected, _stats = ei.select_paper_evidence(
        bundle, _FIELDS, max_items=max_items, max_chars=max_chars
    )
    return ei.serialise_evidence_package(bundle, selected)


def _package(name: str, selection: str) -> str:
    ei, _ = _modules()
    items, _prefilled, _meta = ei._build_items_from_tei(_tei_text(name), _PAPER_ID, _SOURCE_PDF)
    return _package_for_bundle(ei, _bundle_from_items(ei, items), selection)


def _prefix(package: str) -> str:
    _, prompts = _modules()
    return prompts._shared_paper_prefix(package)


def _digest(text: str) -> tuple[str, int]:
    raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def compute_prefix_digests() -> dict[str, dict[str, tuple[str, int]]]:
    """``{fixture: {selection: (sha256, byte length)}}`` for the current source tree's functions."""
    return {
        name: {sel: _digest(_prefix(_package(name, sel))) for sel in _SELECTIONS}
        for name in _FIXTURES
    }


def _qc_context(name: str):
    from quality_control.models import QCBundle, UnifiedRecord

    unified = UnifiedRecord(
        document_id=_PAPER_ID,
        content={
            "exact_text": "unused when TEI is present.",
            "source_pdf_path": _SOURCE_PDF,
            "grobid_tei_xml": _tei_text(name),
        },
    )
    return QCBundle(branches=[], unified=unified)


def _config(cache_root: Path) -> dict:
    return {"evidence_cache_dir": str(cache_root), "addons": {}}


def _bundle(name: str, cache_root: Path, recorder=None, source_id=None):
    """The bundle ``build_or_load_evidence_bundle`` returns (the recorder kwargs only when given)."""
    ei, _ = _modules()
    kwargs = {} if recorder is None else {"recorder": recorder, "source_id": source_id}
    return ei.build_or_load_evidence_bundle(_qc_context(name), _config(cache_root), **kwargs)


def compute_bundle_prefix_digests(cache_root: Path) -> dict[str, dict[str, tuple[str, int]]]:
    """Like :func:`compute_prefix_digests`, through ``build_or_load_evidence_bundle`` (no recorder)."""
    ei, _ = _modules()
    out: dict[str, dict[str, tuple[str, int]]] = {}
    for name in _FIXTURES:
        bundle = _bundle(name, Path(cache_root) / name)
        out[name] = {
            sel: _digest(_prefix(_package_for_bundle(ei, bundle, sel))) for sel in _SELECTIONS
        }
    return out


def _regeneration_hint(
    name: str, selection: str, actual: tuple[str, int], table: str = "_GOLDEN"
) -> str:
    expected = (_GOLDEN_BUNDLE if table == "_GOLDEN_BUNDLE" else _GOLDEN)[name][selection]
    return (
        f"\nThe shared prompt prefix for fixture {name!r} / selection {selection!r} ({table}) changed.\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}\n"
        "The shared prefix is what OpenAI's prompt cache keys on. Changing it is a DECLARED "
        "PROMPT-CONTENT CHANGE: it invalidates cached prefixes in the field and needs a note in "
        ".kiro/specs/provenance-core/tasks.md (Implementation Notes) and a CHANGELOG.md entry "
        "(.kiro/steering/changelog-rules.md).\n"
        "If you did not intend to change the prompt, a provenance value (source/claim/run/"
        "derivation id, timestamp, ...) or other new material leaked into the evidence package "
        "or _shared_paper_prefix -- fix that, do not update the digest.\n"
        "If the change IS intended, regenerate with:\n"
        "  .venv/bin/python -c \"import sys, tempfile; sys.path[:0]=['src','tests/src/provenance']; "
        "import test_provenance_prompt_prefix_regression as m; "
        "print(m.compute_prefix_digests()); "
        "print(m.compute_bundle_prefix_digests(tempfile.mkdtemp()))\"\n"
        "and paste the new (sha256, length) pairs into _GOLDEN / _GOLDEN_BUNDLE together with the "
        "note above."
    )


# ---------------------------------------------------------------------------
# 1. Literal goldens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selection", list(_SELECTIONS))
@pytest.mark.parametrize("name", _FIXTURES)
def test_shared_prefix_bytes_equal_the_golden(name, selection):
    actual = _digest(_prefix(_package(name, selection)))
    assert actual == _GOLDEN[name][selection], _regeneration_hint(name, selection, actual)


def test_the_goldens_are_distinct_and_non_trivial():
    """A copy-paste of one digest over another fixture, or a placeholder, must not pass silently."""
    flat = [
        d for table in (_GOLDEN, _GOLDEN_BUNDLE) for per_sel in table.values() for d in per_sel.values()
    ]
    assert len({sha for sha, _ in flat}) == len(flat)
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) and sha != "0" * 64 for sha, _ in flat)
    assert all(length > 5_000 for _, length in flat)
    for table in (_GOLDEN, _GOLDEN_BUNDLE):
        for per_sel in table.values():
            assert per_sel["pruned"][1] < per_sel["full"][1]


def test_pruned_selection_actually_prunes_and_full_keeps_every_item():
    ei, _ = _modules()
    items, _, _ = ei._build_items_from_tei(_tei_text("arxiv"), _PAPER_ID, _SOURCE_PDF)
    full = json.loads(_package("arxiv", "full"))
    pruned = json.loads(_package("arxiv", "pruned"))
    assert full["evidence_count"] == len(items)
    assert 0 < pruned["evidence_count"] <= _SELECTIONS["pruned"][0] < len(items)


def test_golden_computation_is_independent_of_cwd_and_repeatable(tmp_path, monkeypatch):
    first = compute_prefix_digests()
    monkeypatch.chdir(tmp_path)
    assert compute_prefix_digests() == first
    assert first == _GOLDEN
    assert compute_bundle_prefix_digests(tmp_path) == _GOLDEN_BUNDLE


# ---------------------------------------------------------------------------
# 2. The wrapper is exactly the documented text around the package
# ---------------------------------------------------------------------------


def test_shared_prefix_is_exactly_header_package_footer():
    _, prompts = _modules()
    for payload in ("", "PKG", '{"paper_id":"p","evidence":[]}', "line1\nline2"):
        assert prompts._shared_paper_prefix(payload) == _PREFIX_HEADER + payload + _PREFIX_FOOTER


def test_shared_prefix_takes_the_package_and_nothing_else():
    """No parameter through which a run/source/claim id could be threaded in."""
    _, prompts = _modules()
    params = inspect.signature(prompts._shared_paper_prefix).parameters
    assert list(params) == ["source_package"]


def test_prompts_module_does_not_import_provenance():
    _, prompts = _modules()
    tree = ast.parse(Path(prompts.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    leaked = sorted(m for m in imported if m.split(".")[0] in {"provenance", "pipeline"})
    assert not leaked, f"agents.openai.prompts imports {leaked}; provenance may not reach the prompt"


# ---------------------------------------------------------------------------
# 3. Stability across warm-up / chunks / synthesis
# ---------------------------------------------------------------------------

_CHUNK_A = [{"field_index": 8, "field_name": "Dataset", "definition": "", "reviewer_question": ""}]
_CHUNK_B = [
    {"field_index": 21, "field_name": "Other", "definition": "x", "reviewer_question": "y"},
    {"field_index": 20, "field_name": "Model", "definition": "", "reviewer_question": ""},
]


@pytest.mark.parametrize("name", _FIXTURES)
def test_prefix_is_identical_across_warmup_chunks_and_synthesis(name):
    _, prompts = _modules()
    package = _package(name, "pruned")
    prefix = prompts._shared_paper_prefix(package)
    prefix_bytes = prefix.encode("utf-8")
    messages = [
        prompts.build_cache_warmup_message(package),
        prompts.build_cache_warmup_message(package, _CHUNK_B),
        prompts.build_user_message(package, _CHUNK_A),
        prompts.build_user_message(package, _CHUNK_B),
        prompts.build_user_message(package, _CHUNK_B, prior_context=[{"field_index": 8}]),
        prompts.build_user_message(package, _CHUNK_A, prior_context=[]),
        prompts.build_user_message(package, _CHUNK_A, prior_context=[{"run": "run:0123456789abcdef"}]),
    ]
    for message in messages:
        assert message.encode("utf-8").startswith(prefix_bytes)
    # The prefix is a pure function of the evidence package: nothing else reaches it.
    assert _digest(prefix) == _GOLDEN[name]["pruned"]


def test_prefix_is_a_pure_function_of_the_package_and_repeatable():
    _, prompts = _modules()
    package = _package("plosone", "pruned")
    assert [prompts._shared_paper_prefix(package) for _ in range(3)] == [
        prompts._shared_paper_prefix(package)
    ] * 3


# ---------------------------------------------------------------------------
# 4. A live recorder through the public evidence integration point
# ---------------------------------------------------------------------------

_FINGERPRINT = "a" * 64
_RUN_ID = "run:0123456789abcdef"


def _recorder(*, enabled: bool):
    from provenance.identity import derive_source_id
    from provenance.models import RunIdentity
    from provenance.recorder import ProvenanceRecorder

    source_id = derive_source_id(_FINGERPRINT)
    run = RunIdentity(
        run_id=_RUN_ID,
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=("m",),
        document_fingerprints={source_id: _FINGERPRINT},
        created_at="2026-10-11T00:00:00+00:00",
    )
    return ProvenanceRecorder(run, enabled=enabled), source_id


def test_bundle_path_goldens_equal_the_current_bundle_digests(tmp_path):
    actual = compute_bundle_prefix_digests(tmp_path)
    for name in _FIXTURES:
        for selection in _SELECTIONS:
            assert actual[name][selection] == _GOLDEN_BUNDLE[name][selection], _regeneration_hint(
                name, selection, actual[name][selection], "_GOLDEN_BUNDLE"
            )


@pytest.mark.parametrize("enabled", [True, False], ids=["recorder-enabled", "recorder-disabled"])
@pytest.mark.parametrize("name", _FIXTURES)
def test_bundle_built_with_a_live_recorder_serialises_to_the_golden_prefix(name, enabled, tmp_path):
    ei, _ = _modules()
    plain = _bundle(name, tmp_path / "plain")
    recorder, source_id = _recorder(enabled=enabled)
    recorded_miss = _bundle(name, tmp_path / "recorded", recorder, source_id)  # cache miss
    recorded_hit = _bundle(name, tmp_path / "recorded", recorder, source_id)  # cache hit

    if enabled:  # the integration point really ran: provenance was recorded, not a no-op
        nodes = recorder.snapshot().nodes
        assert sum(1 for n in nodes.values() if n.kind == "evidence") >= len(plain.evidence_items)
    for selection in _SELECTIONS:
        digests = {
            label: _digest(_prefix(_package_for_bundle(ei, bundle, selection)))
            for label, bundle in (("plain", plain), ("miss", recorded_miss), ("hit", recorded_hit))
        }
        assert digests["plain"] == digests["miss"] == digests["hit"], digests
        assert digests["plain"] == _GOLDEN_BUNDLE[name][selection], _regeneration_hint(
            name, selection, digests["plain"], "_GOLDEN_BUNDLE"
        )


@pytest.mark.parametrize("name", _FIXTURES)
def test_recorder_does_not_touch_the_bundle_the_prompt_is_built_from(name, tmp_path):
    plain = _bundle(name, tmp_path / "plain")
    recorder, source_id = _recorder(enabled=True)
    recorded = _bundle(name, tmp_path / "recorded", recorder, source_id)
    assert recorded.evidence_items == plain.evidence_items
    assert recorded.prefilled_fields == plain.prefilled_fields
    assert recorded.index_path.read_bytes() == plain.index_path.read_bytes()


# ---------------------------------------------------------------------------
# 5. Negative guard: no provenance vocabulary, no new envelope or item keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selection", list(_SELECTIONS))
@pytest.mark.parametrize("name", _FIXTURES)
def test_package_keeps_the_pre_provenance_envelope_and_item_keys(name, selection):
    package = json.loads(_package(name, selection))
    assert list(package) == _ENVELOPE_KEYS
    assert package["paper_id"] == _PAPER_ID
    assert package["evidence_count"] == len(package["evidence"]) > 0
    for item in package["evidence"]:
        assert list(item) == _ITEM_KEYS, item["id"]


@pytest.mark.parametrize("name", _FIXTURES)
def test_prefix_adds_no_provenance_vocabulary_beyond_the_paper_text(name):
    """Each vocabulary token occurs in the prefix no more often than in the paper's own TEI text."""
    prefix = _prefix(_package(name, "full"))
    tei = _tei_text(name)
    for token in _PROVENANCE_VOCABULARY:
        assert prefix.count(token) <= tei.count(token), (
            f"{token!r} appears {prefix.count(token)}x in the prompt prefix but only "
            f"{tei.count(token)}x in the paper's TEI: provenance vocabulary leaked into the prompt"
        )


def test_vocabulary_check_flags_an_injected_provenance_value():
    """Self-test of the negative guard's predicate on a synthetic leak."""
    clean = _prefix(_package("plosone", "pruned"))
    leaked = clean.replace(
        "END SHARED EVIDENCE PACKAGE", "END SHARED EVIDENCE PACKAGE src:" + "a" * 64
    )
    assert leaked.count("src:") > clean.count("src:")
    assert _digest(leaked) != _GOLDEN["plosone"]["pruned"]
