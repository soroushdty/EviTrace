"""Tests for ``provenance.adapters.evidence_adapter`` — evidence-index items -> EvidenceNode.

Item mappings are literal dicts in the exact shape ``_build_items_from_tei`` in
``src/pipeline/evidence_index.py`` emits (``id``, ``type``, ``section_path``,
``page``, ``coords``, ``xpath``, ``text``, ``source_pdf``, ``score``,
``annotations``), including the ``page=None, coords=None`` case and the weak
``.//{tag}`` structural-path case.

Covers the evidence kind and producing backend on every node (4.1, 4.6), every
available anchor preserved (4.3), anchor precision ``exact`` / ``approximate``
/ ``absent`` with the absent anchor kinds enumerated (4.4, 4.5), the item
identifier adopted verbatim as the local identifier (4.7), unrecognized item
keys preserved in ``parser_metadata`` (10.2), and totality: a malformed item
converts without raising, while an item that has no usable identifier is the
only one that cannot become a node — and neither a skipped item nor a ``type``
fallback is silent: each is logged at DEBUG, with one WARNING per batch.
"""

from __future__ import annotations

import ast
import copy
import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from provenance import adapters
from provenance.adapters import evidence_adapter
from provenance.adapters.evidence_adapter import evidence_node_from_item, evidence_nodes_from_items
from provenance.identity import InvalidIdentifierError, format_evidence_id, parse_evidence_id
from provenance.models import EvidenceNode, SourceAnchor
from provenance.privacy_carrier import UNKNOWN_PRIVACY

_SOURCE_ID = "src:" + "a" * 64
_RUN_ID = "run:0123456789abcdef"
_BACKEND = "grobid"
_PDF = "data/papers/paper_a.pdf"

_ALL_ANCHOR_KINDS = ("page", "coords", "section_path", "char_span", "structural_path")


def _sentence_item(**overrides: Any) -> dict[str, Any]:
    """A body sentence with its own TEI ``coords`` and an ``xml:id`` path."""
    item: dict[str, Any] = {
        "id": "S000001",
        "type": "sentence",
        "section_path": "Methods",
        "page": 3,
        "coords": [72.0, 100.5, 523.25, 112.0],
        "xpath": "//*[@xml:id='_s1']",
        "text": "Patients were enrolled between 2010 and 2015.",
        "source_pdf": _PDF,
        "score": 40,
        "annotations": {},
    }
    item.update(overrides)
    return item


def _convert(items: list[Any], *, producing_backend: str | None = _BACKEND) -> list[EvidenceNode]:
    return evidence_nodes_from_items(
        items, source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=producing_backend
    )


def _one(item: Any, *, producing_backend: str | None = _BACKEND) -> EvidenceNode:
    nodes = _convert([item], producing_backend=producing_backend)
    assert len(nodes) == 1
    return nodes[0]


# ---------------------------------------------------------------------------
# Node fields: kind, source, rank, backend, adopted identifier (4.1, 4.6, 4.7)
# ---------------------------------------------------------------------------


def test_sentence_item_becomes_evidence_node_with_every_core_field() -> None:
    node = _one(_sentence_item())

    assert isinstance(node, EvidenceNode)
    assert node.kind == "evidence"
    assert node.node_id == format_evidence_id(_SOURCE_ID, "S000001")
    assert node.local_id == "S000001"
    assert node.evidence_kind == "sentence"
    assert node.source_id == _SOURCE_ID
    assert node.run_id == _RUN_ID
    assert node.producing_backend == _BACKEND
    assert node.rank_score == 40
    assert node.privacy is UNKNOWN_PRIVACY
    assert node.extensions == {}


def test_node_id_parses_back_to_source_and_verbatim_local_id() -> None:
    for local_id in ("S000001", "T000002", "F000003", "item 7/é:x"):
        node = _one(_sentence_item(id=local_id))
        assert node.local_id == local_id
        assert parse_evidence_id(node.node_id) == (_SOURCE_ID, local_id)


@pytest.mark.parametrize("item_type", ["sentence", "table", "figure_caption", "metadata"])
def test_evidence_kind_is_taken_from_item_type(item_type: str) -> None:
    assert _one(_sentence_item(type=item_type)).evidence_kind == item_type


def test_table_and_figure_items_in_real_shape() -> None:
    table = _sentence_item(
        id="T000001",
        type="table",
        section_path="Results",
        xpath="//*[@xml:id='tab_0']",
        text="Table 1. Baseline characteristics\nAge | 64",
        score=60,
    )
    figure = _sentence_item(
        id="F000001",
        type="figure_caption",
        section_path="body",
        page=None,
        coords=None,
        xpath=".//figure",
        text="Figure 1. Study flow.",
        score=5,
    )
    table_node, figure_node = _convert([table, figure])

    assert (table_node.local_id, table_node.evidence_kind, table_node.rank_score) == ("T000001", "table", 60)
    assert (figure_node.local_id, figure_node.evidence_kind, figure_node.rank_score) == (
        "F000001",
        "figure_caption",
        5,
    )


def test_unknown_item_type_falls_back_to_sentence_and_keeps_raw_type() -> None:
    node = _one(_sentence_item(type="equation"))

    assert node.evidence_kind == "sentence"
    assert node.parser_metadata["type"] == "equation"


def test_missing_item_type_falls_back_to_sentence() -> None:
    item = _sentence_item()
    del item["type"]
    node = _one(item)

    assert node.evidence_kind == "sentence"
    assert "type" not in node.parser_metadata


def test_producing_backend_may_be_absent() -> None:
    assert _one(_sentence_item(), producing_backend=None).producing_backend is None


def test_negative_score_is_a_valid_rank() -> None:
    # The title item carries score -30 in the real index.
    assert _one(_sentence_item(score=-30)).rank_score == -30


# ---------------------------------------------------------------------------
# Anchor precision and missing anchor kinds (4.3, 4.4, 4.5)
# ---------------------------------------------------------------------------


def test_exact_when_page_and_coords_present_and_every_anchor_preserved() -> None:
    anchor = _one(_sentence_item()).anchor

    assert anchor == SourceAnchor(
        precision="exact",
        page=3,
        coords=(72.0, 100.5, 523.25, 112.0),
        section_path="Methods",
        char_span=None,
        structural_path="//*[@xml:id='_s1']",
        missing=(),
    )


def test_exact_even_when_structural_path_is_the_weak_fallback() -> None:
    anchor = _one(_sentence_item(xpath=".//s")).anchor

    assert anchor.precision == "exact"
    assert anchor.structural_path == ".//s"
    assert anchor.missing == ()


def test_integer_coords_are_preserved_as_floats() -> None:
    anchor = _one(_sentence_item(coords=[1, 2, 3, 4])).anchor

    assert anchor.precision == "exact"
    assert anchor.coords == (1.0, 2.0, 3.0, 4.0)
    assert all(type(value) is float for value in anchor.coords)


def test_approximate_when_page_present_but_coords_absent() -> None:
    # The real title item: page=1, coords=None, "Metadata" section.
    item = _sentence_item(
        id="S000042",
        section_path="Metadata",
        page=1,
        coords=None,
        xpath=".//title",
        text="A cohort study of something",
        score=-30,
    )
    anchor = _one(item).anchor

    assert anchor.precision == "approximate"
    assert anchor.page == 1
    assert anchor.coords is None
    assert anchor.section_path == "Metadata"
    assert anchor.structural_path == ".//title"
    assert anchor.missing == ("coords", "char_span")


def test_approximate_when_page_and_coords_none_but_section_path_present() -> None:
    # A GROBID run without coordinates: _parse_coords yields page=None, coords=None.
    anchor = _one(_sentence_item(page=None, coords=None, xpath=".//p")).anchor

    assert anchor.precision == "approximate"
    assert anchor.section_path == "Methods"
    assert anchor.missing == ("page", "coords", "char_span")


def test_approximate_when_only_the_weak_structural_path_remains() -> None:
    anchor = _one(_sentence_item(page=None, coords=None, section_path=None, xpath=".//p")).anchor

    assert anchor.precision == "approximate"
    assert anchor.structural_path == ".//p"
    assert anchor.missing == ("page", "coords", "section_path", "char_span")


def test_approximate_when_only_an_xml_id_structural_path_remains() -> None:
    anchor = _one(_sentence_item(page=None, coords=None, section_path="", xpath="//*[@xml:id='_p9']")).anchor

    assert anchor.precision == "approximate"
    assert anchor.structural_path == "//*[@xml:id='_p9']"
    assert anchor.missing == ("page", "coords", "section_path", "char_span")


def test_approximate_when_coords_present_without_page() -> None:
    anchor = _one(_sentence_item(page=None, section_path=None, xpath=None)).anchor

    assert anchor.precision == "approximate"
    assert anchor.coords == (72.0, 100.5, 523.25, 112.0)
    assert anchor.missing == ("page", "section_path", "char_span", "structural_path")


def test_absent_when_no_anchor_is_present_and_all_kinds_enumerated() -> None:
    item = _sentence_item(page=None, coords=None, section_path=None, xpath=None)
    anchor = _one(item).anchor

    assert anchor == SourceAnchor(precision="absent", missing=_ALL_ANCHOR_KINDS)


def test_absent_when_anchor_keys_are_missing_or_empty() -> None:
    node = _one({"id": "S000009", "type": "sentence", "section_path": "", "xpath": ""})

    assert node.anchor.precision == "absent"
    assert node.anchor.missing == _ALL_ANCHOR_KINDS


def test_char_span_is_preserved_when_supplied() -> None:
    anchor = _one(_sentence_item(char_span=[10, 42])).anchor

    assert anchor.char_span == (10, 42)
    assert anchor.precision == "exact"


def test_char_span_alone_is_an_approximate_anchor() -> None:
    item = {"id": "S000010", "type": "sentence", "char_span": (0, 5)}
    anchor = _one(item).anchor

    assert anchor.precision == "approximate"
    assert anchor.char_span == (0, 5)
    assert anchor.missing == ("page", "coords", "section_path", "structural_path")


# ---------------------------------------------------------------------------
# Parser metadata: unrecognized keys preserved (10.2)
# ---------------------------------------------------------------------------


def test_unrecognized_keys_are_copied_into_parser_metadata() -> None:
    annotations = {"quantities": [{"text": "5 mg", "type": "quantity"}]}
    node = _one(_sentence_item(annotations=annotations, ocr_confidence=0.93))

    assert node.parser_metadata == {
        "text": "Patients were enrolled between 2010 and 2015.",
        "source_pdf": _PDF,
        "annotations": annotations,
        "ocr_confidence": 0.93,
    }


def test_recognized_keys_are_not_duplicated_into_parser_metadata() -> None:
    metadata = _one(_sentence_item()).parser_metadata

    for key in ("id", "type", "section_path", "page", "coords", "xpath", "score"):
        assert key not in metadata


def test_parser_metadata_does_not_alias_the_item() -> None:
    item = _sentence_item(annotations={"entities": ["Aspirin"]})
    node = _one(item)
    item["annotations"]["entities"].append("Placebo")
    item["text"] = "changed"

    assert node.parser_metadata["annotations"] == {"entities": ["Aspirin"]}
    assert node.parser_metadata["text"] == "Patients were enrolled between 2010 and 2015."


def test_conversion_does_not_mutate_the_items() -> None:
    items = [_sentence_item(), _sentence_item(id="S000002", page=None, coords=None)]
    before = copy.deepcopy(items)
    _convert(items)

    assert items == before


def test_node_is_hashable_and_equal_by_value() -> None:
    first = _one(_sentence_item(annotations={"entities": ["Aspirin"]}))
    second = _one(_sentence_item(annotations={"entities": ["Aspirin"]}))

    assert first == second
    assert hash(first) == hash(second)


# ---------------------------------------------------------------------------
# Totality: malformed items convert without raising
# ---------------------------------------------------------------------------


def test_item_with_unknown_and_missing_keys_converts_without_raising() -> None:
    node = _one({"id": "S000077", "vendor_blob": {"k": [1, 2]}, "lang": "en"})

    assert node.local_id == "S000077"
    assert node.evidence_kind == "sentence"
    assert node.rank_score is None
    assert node.producing_backend == _BACKEND
    assert node.anchor == SourceAnchor(precision="absent", missing=_ALL_ANCHOR_KINDS)
    assert node.parser_metadata == {"vendor_blob": {"k": [1, 2]}, "lang": "en"}


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("page", "3"),
        ("page", True),
        ("page", -1),
        ("page", 2.0),
        ("coords", [1.0, 2.0, 3.0]),
        ("coords", "72,100,523,112"),
        ("coords", [1.0, 2.0, "x", 4.0]),
        ("coords", [1.0, 2.0, math.nan, 4.0]),
        ("coords", [True, 2.0, 3.0, 4.0]),
        ("section_path", ["Methods"]),
        ("xpath", 7),
        ("char_span", [5]),
        ("char_span", [9, 3]),
        ("char_span", [-1, 3]),
    ],
)
def test_malformed_anchor_value_is_unusable_and_preserved_raw(key: str, bad_value: Any) -> None:
    item = {"id": "S000005", "type": "sentence", key: bad_value}
    node = _one(item)

    assert node.anchor.precision == "absent"
    assert node.anchor.missing == _ALL_ANCHOR_KINDS
    assert node.parser_metadata == {key: bad_value}


@pytest.mark.parametrize("bad_score", [1.5, "40", True, None])
def test_non_integer_score_yields_no_rank(bad_score: Any) -> None:
    node = _one(_sentence_item(score=bad_score))

    assert node.rank_score is None
    if bad_score is None:
        assert "score" not in node.parser_metadata
    else:
        assert node.parser_metadata["score"] == bad_score


def test_malformed_page_alongside_usable_coords_is_not_exact() -> None:
    anchor = _one(_sentence_item(page="3")).anchor

    assert anchor.precision == "approximate"
    assert anchor.page is None
    assert "page" in anchor.missing


@pytest.mark.parametrize("bad_type", [None, 3, ["sentence"]])
def test_non_string_type_falls_back_to_sentence(bad_type: Any) -> None:
    node = _one(_sentence_item(type=bad_type))

    assert node.evidence_kind == "sentence"
    if bad_type is None:
        assert "type" not in node.parser_metadata
    else:
        assert node.parser_metadata["type"] == bad_type


# ---------------------------------------------------------------------------
# Items with no usable identifier (Error Handling: invalid identifier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_item",
    [
        {"type": "sentence", "page": 1},  # no id
        {"id": None},
        {"id": ""},
        {"id": 12},
        {"id": "S#000001"},
        "S000001",
        None,
        ["id", "S000001"],
    ],
)
def test_per_item_conversion_raises_invalid_identifier_for_unidentifiable_item(bad_item: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        evidence_node_from_item(bad_item, source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=_BACKEND)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def adapter_log() -> Iterator[_ListHandler]:
    """Capture the adapter's records directly on its logger.

    ``utils.logging_utils.setup_logging`` sets ``propagate = False`` on the
    ``evi_trace`` root, so pytest's ``caplog`` (a root-logger handler) would miss
    these records whenever another test has configured logging.
    """
    handler = _ListHandler()
    adapter_logger = evidence_adapter.logger
    previous_level = adapter_logger.level
    adapter_logger.addHandler(handler)
    adapter_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        adapter_logger.removeHandler(handler)
        adapter_logger.setLevel(previous_level)


def _at(handler: _ListHandler, level: int) -> list[str]:
    return [record.getMessage() for record in handler.records if record.levelno == level]


def test_batch_skips_unidentifiable_items_logs_each_and_converts_the_rest(adapter_log: _ListHandler) -> None:
    items = [
        _sentence_item(id="S000001"),
        {"id": "S#2", "type": "sentence"},
        None,
        {"page": 4},
        _sentence_item(id="T000001", type="table"),
    ]
    nodes = _convert(items)

    assert [node.local_id for node in nodes] == ["S000001", "T000001"]
    debug = _at(adapter_log, logging.DEBUG)
    assert len(debug) == 3
    assert "evidence item 1 " in debug[0] and "'S#2'" in debug[0]
    assert "evidence item 2 " in debug[1] and "id=n/a" in debug[1]
    assert "evidence item 3 " in debug[2] and "id=None" in debug[2]
    assert len(_at(adapter_log, logging.WARNING)) == 1


def test_one_warning_per_batch_names_dropped_count_and_source(adapter_log: _ListHandler) -> None:
    items = [{"id": ""}, _sentence_item(), {"id": 5}, {"id": "a#b"}]
    nodes = _convert(items)

    assert len(nodes) == 1
    warnings = _at(adapter_log, logging.WARNING)
    assert len(warnings) == 1
    assert _SOURCE_ID in warnings[0]
    assert "3 of 4 items skipped" in warnings[0]
    assert "0 recorded with fallback evidence kind" in warnings[0]


def test_all_valid_batch_emits_no_warning(adapter_log: _ListHandler) -> None:
    _convert([_sentence_item(), _sentence_item(id="T1", type="table"), _sentence_item(page=None)])

    assert adapter_log.records == []


def test_unknown_or_missing_type_counts_as_coerced_in_the_warning(adapter_log: _ListHandler) -> None:
    no_type = _sentence_item(id="S3")
    del no_type["type"]
    nodes = _convert([_sentence_item(id="S1", type="equation"), _sentence_item(id="S2"), no_type])

    assert [node.evidence_kind for node in nodes] == ["sentence"] * 3
    debug = _at(adapter_log, logging.DEBUG)
    assert len(debug) == 2
    assert "'S1'" in debug[0] and "'equation'" in debug[0]
    assert "'S3'" in debug[1] and "missing" in debug[1]
    warnings = _at(adapter_log, logging.WARNING)
    assert len(warnings) == 1
    assert "0 of 3 items skipped" in warnings[0]
    assert "2 recorded with fallback evidence kind 'sentence'" in warnings[0]


def test_per_item_conversion_logs_type_fallback_without_warning(adapter_log: _ListHandler) -> None:
    evidence_node_from_item(
        _sentence_item(type="equation"), source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=_BACKEND
    )

    assert len(_at(adapter_log, logging.DEBUG)) == 1
    assert _at(adapter_log, logging.WARNING) == []


def test_no_log_record_contains_item_text(adapter_log: _ListHandler) -> None:
    secret = "Confidential patient narrative 0xC0FFEE"
    items = [
        {"id": "S#1", "text": secret},
        {"text": secret},
        {"id": "S2", "type": "equation", "text": secret},
        {"id": None, "type": secret, "text": secret},
    ]
    _convert(items)

    assert adapter_log.records
    for record in adapter_log.records:
        assert secret not in record.getMessage()


def test_per_item_conversion_matches_batch_conversion() -> None:
    item = _sentence_item(page=None, coords=None)
    single = evidence_node_from_item(item, source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=_BACKEND)

    assert [single] == _convert([item])


def test_empty_items_yield_no_nodes() -> None:
    assert _convert([]) == []


def test_order_and_duplicates_are_kept() -> None:
    items = [_sentence_item(id="S2"), _sentence_item(id="S1"), _sentence_item(id="S2")]

    assert [node.local_id for node in _convert(items)] == ["S2", "S1", "S2"]


# ---------------------------------------------------------------------------
# Caller errors (arguments, not items) are raised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_source_id", ["", "a" * 64, "src:", "src:ab#c", "src:a b", None])
def test_invalid_source_id_raises_even_without_items(bad_source_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        evidence_nodes_from_items(
            [], source_id=bad_source_id, run_id=_RUN_ID, producing_backend=_BACKEND
        )


@pytest.mark.parametrize("bad_run_id", ["", None, 3])
def test_invalid_run_id_raises(bad_run_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        evidence_nodes_from_items(
            [_sentence_item()], source_id=_SOURCE_ID, run_id=bad_run_id, producing_backend=_BACKEND
        )


@pytest.mark.parametrize("bad_backend", [3, b"grobid", ""])
def test_invalid_producing_backend_raises(bad_backend: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        evidence_nodes_from_items(
            [_sentence_item()], source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=bad_backend
        )


@pytest.mark.parametrize("bad_items", ["S000001", b"x", None, {"id": "S000001"}, 5])
def test_items_must_be_a_sequence(bad_items: Any) -> None:
    with pytest.raises(TypeError):
        evidence_nodes_from_items(
            bad_items, source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=_BACKEND
        )


def test_items_may_be_a_tuple() -> None:
    nodes = evidence_nodes_from_items(
        (_sentence_item(),), source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend=_BACKEND
    )
    assert [node.local_id for node in nodes] == ["S000001"]


# ---------------------------------------------------------------------------
# Module boundary: re-export, no pipeline import, no hashing
# ---------------------------------------------------------------------------


def test_adapters_package_re_exports_the_evidence_adapter() -> None:
    assert adapters.evidence_nodes_from_items is evidence_nodes_from_items
    assert adapters.evidence_node_from_item is evidence_node_from_item


def _module_tree() -> ast.Module:
    return ast.parse(Path(evidence_adapter.__file__).read_text(encoding="utf-8"))


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_module_imports_only_stdlib_and_provenance_models_and_identity() -> None:
    allowed_provenance = {"provenance.models", "provenance.identity", "provenance.privacy_carrier"}
    for module in _imported_modules():
        root = module.split(".")[0]
        if root == "provenance":
            assert module in allowed_provenance, module
        elif root == "utils":
            assert module == "utils.logging_utils", module
        else:
            assert root in {"__future__", "math", "typing", "collections"}, module


def test_module_performs_no_hashing() -> None:
    hashing = {"hashlib", "hmac", "zlib", "binascii", "secrets", "uuid"}
    assert not {module.split(".")[0] for module in _imported_modules()} & hashing
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "hash", "the adapter must not hash item content"
