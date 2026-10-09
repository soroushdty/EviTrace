"""Tests for ``provenance.adapters.claim_adapter`` — reconstructed fields -> ClaimNode.

Field mappings are literal dicts in the exact shape ``reconstruct_fields`` in
``src/pipeline/validator.py`` emits (``field_index``, ``domain_group``,
``field_name``, ``extracted_value``, ``evidence``, ``location``,
``location_metadata``, ``confidence``) and the prefilled-field shape built in
``src/pipeline/pdf_processor.py`` (``location=[]``, ``evidence=""``), plus the
two optional revision keys the adapter defines (``revision`` and
``contributing_claim_ids``).

Covers claim records with citing edges (5.1), the three support
classifications with ``unsupported`` and ``missing_anchor`` kept distinct
(5.2, 5.6), reference findings that retain the claim (5.4 via 5.1), revision
edges to every contributing claim (5.5), and the non-model origin (5.7).
"""

from __future__ import annotations

import ast
import dataclasses
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from provenance import adapters
from provenance.adapters import claim_adapter
from provenance.adapters.claim_adapter import claim_nodes_from_fields
from provenance.identity import InvalidIdentifierError, derive_claim_id, format_evidence_id
from provenance.models import (
    DEFAULT_SEVERITIES,
    ClaimNode,
    ProvenanceEdge,
    RunIdentity,
    SourceAnchor,
    ValidationFinding,
)
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from provenance.recorder import ProvenanceRecorder

_SOURCE_ID = "src:" + "a" * 64
_OTHER_SOURCE_ID = "src:" + "b" * 64
_RUN_ID = "run:0123456789abcdef"

_S1 = format_evidence_id(_SOURCE_ID, "S000001")
_S2 = format_evidence_id(_SOURCE_ID, "S000002")
_S3 = format_evidence_id(_SOURCE_ID, "S000003")
_KNOWN = frozenset({_S1, _S2, _S3})

_EXACT = SourceAnchor(precision="exact", page=3, coords=(1.0, 2.0, 3.0, 4.0))
_APPROXIMATE = SourceAnchor(
    precision="approximate",
    section_path="Methods",
    missing=("page", "coords", "char_span", "structural_path"),
)
_ABSENT = SourceAnchor(
    precision="absent",
    missing=("page", "coords", "section_path", "char_span", "structural_path"),
)
_ANCHORS = {_S1: _EXACT, _S2: _ABSENT, _S3: _APPROXIMATE}


def _field(**overrides: Any) -> dict[str, Any]:
    """One reconstructed field, as ``reconstruct_fields`` emits it."""
    field: dict[str, Any] = {
        "field_index": 7,
        "domain_group": "Study design",
        "field_name": "sample_size",
        "extracted_value": "120 patients",
        "evidence": "A total of 120 patients were enrolled.",
        "location": ["S000001"],
        "location_metadata": [
            {
                "id": "S000001",
                "type": "sentence",
                "section_path": "Methods",
                "page": 3,
                "coords": [1.0, 2.0, 3.0, 4.0],
                "xpath": "//*[@xml:id='_s1']",
                "source_pdf": "data/papers/paper_a.pdf",
            }
        ],
        "confidence": "h",
    }
    field.update(overrides)
    return field


def _prefilled_field(field_index: int = 1, value: str = "Smith J") -> dict[str, Any]:
    """A TEI-prefilled field, exactly as ``process_pdf`` builds it."""
    return {
        "field_index": field_index,
        "domain_group": "Bibliographic",
        "field_name": "author" if field_index == 1 else "year",
        "extracted_value": value or "nr",
        "evidence": "",
        "location": [],
        "location_metadata": [],
        "confidence": "h" if value and value != "nr" else "nr",
    }


def _convert(
    fields: list[Any],
    *,
    origin: str = "model",
    known: Any = _KNOWN,
    anchors: Any = _ANCHORS,
) -> tuple[list[ClaimNode], list[ProvenanceEdge], list[ValidationFinding]]:
    return claim_nodes_from_fields(
        fields,
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        known_evidence_ids=known,
        origin=origin,  # type: ignore[arg-type]
        evidence_anchors=anchors,
    )


def _only(items: list[Any]) -> Any:
    assert len(items) == 1, items
    return items[0]


# ---------------------------------------------------------------------------
# Claim records and citing edges (5.1)
# ---------------------------------------------------------------------------


def test_field_becomes_claim_node_with_field_identity_digest_and_confidence() -> None:
    claims, _, findings = _convert([_field()])

    claim = _only(claims)
    assert isinstance(claim, ClaimNode)
    assert claim.node_id == derive_claim_id(_SOURCE_ID, 7)
    assert claim.kind == "claim"
    assert claim.run_id == _RUN_ID
    assert claim.source_id == _SOURCE_ID
    assert claim.field_index == 7
    assert claim.field_name == "sample_size"
    assert claim.confidence == "h"
    assert claim.origin == "model"
    assert claim.privacy == UNKNOWN_PRIVACY
    assert claim.extensions == {}
    assert isinstance(claim.value_digest, str) and claim.value_digest
    assert findings == []


def test_claim_cites_each_referenced_evidence_node_with_one_edge() -> None:
    claims, edges, _ = _convert([_field(location=["S000001", "S000003"])])

    claim = _only(claims)
    assert claim.cited_evidence_ids == (_S1, _S3)
    assert [(e.kind, e.from_id, e.to_id) for e in edges] == [
        ("cites_evidence", claim.node_id, _S1),
        ("cites_evidence", claim.node_id, _S3),
    ]
    assert [e.edge_id for e in edges] == [
        f"cites_evidence:{claim.node_id}->{_S1}",
        f"cites_evidence:{claim.node_id}->{_S3}",
    ]


def test_fully_qualified_evidence_reference_is_accepted() -> None:
    claims, edges, findings = _convert([_field(location=[_S1])])

    assert _only(claims).cited_evidence_ids == (_S1,)
    assert _only(edges).to_id == _S1
    assert findings == []


def test_value_digest_is_deterministic_and_value_sensitive() -> None:
    first, _, _ = _convert([_field()])
    again, _, _ = _convert([_field()])
    other, _, _ = _convert([_field(extracted_value="121 patients")])

    assert first[0].value_digest == again[0].value_digest
    assert first[0].value_digest != other[0].value_digest


def test_claim_node_carries_no_value_or_evidence_text() -> None:
    claims, edges, findings = _convert([_field()])

    dumped = repr([dataclasses.asdict(_only(claims)), [dataclasses.asdict(e) for e in edges], findings])
    assert "120 patients" not in dumped
    assert "were enrolled" not in dumped


def test_confidence_absent_is_none() -> None:
    field = _field()
    del field["confidence"]

    assert _only(_convert([field])[0]).confidence is None


def test_claims_keep_field_order_and_conversion_is_repeatable() -> None:
    fields = [_field(field_index=9), _field(field_index=3, location=["nope"]), _field(field_index=5)]

    first = _convert(fields)
    assert [c.field_index for c in first[0]] == [9, 3, 5]
    assert _convert(fields) == first


# ---------------------------------------------------------------------------
# Support classification (5.2, 5.6)
# ---------------------------------------------------------------------------


def test_claim_without_evidence_reference_is_unsupported() -> None:
    claims, edges, findings = _convert([_field(location=[])])

    assert _only(claims).support == "unsupported"
    assert _only(claims).cited_evidence_ids == ()
    assert edges == []
    assert findings == []


@pytest.mark.parametrize("location", [None, "missing"])
def test_claim_with_absent_location_key_or_none_is_unsupported(location: Any) -> None:
    field = _field()
    if location == "missing":
        del field["location"]
    else:
        field["location"] = None

    claims, edges, findings = _convert([field])
    assert _only(claims).support == "unsupported"
    assert edges == [] and findings == []


def test_claim_citing_anchored_evidence_is_supported() -> None:
    assert _only(_convert([_field(location=["S000001"])])[0]).support == "supported"


def test_claim_citing_only_anchorless_evidence_is_missing_anchor() -> None:
    claims, edges, findings = _convert([_field(location=["S000002"])])

    claim = _only(claims)
    assert claim.support == "missing_anchor"
    assert claim.cited_evidence_ids == (_S2,)
    assert _only(edges).to_id == _S2
    assert findings == []


def test_missing_anchor_and_unsupported_are_distinct_states() -> None:
    claims, _, _ = _convert(
        [_field(field_index=1, location=[]), _field(field_index=2, location=["S000002"])]
    )

    assert [c.support for c in claims] == ["unsupported", "missing_anchor"]
    # The two claims differ in cited evidence too: one cites nothing, the other cites a node.
    assert claims[0].cited_evidence_ids == ()
    assert claims[1].cited_evidence_ids == (_S2,)


def test_approximate_anchor_counts_as_usable() -> None:
    # anchor.missing is non-empty for approximate nodes too; only precision == "absent" is anchorless.
    assert _APPROXIMATE.missing
    assert _only(_convert([_field(location=["S000003"])])[0]).support == "supported"


def test_one_anchored_citation_makes_the_claim_supported() -> None:
    claims, edges, _ = _convert([_field(location=["S000002", "S000001"])])

    assert _only(claims).support == "supported"
    assert len(edges) == 2


def test_known_evidence_absent_from_anchor_mapping_is_missing_anchor() -> None:
    # Fail closed: a cited node whose anchor was not supplied is never treated as anchored.
    claims, edges, findings = _convert([_field(location=["S000003"])], anchors={_S1: _EXACT})

    claim = _only(claims)
    assert claim.support == "missing_anchor"
    assert claim.cited_evidence_ids == (_S3,)
    assert _only(edges).to_id == _S3
    assert findings == []


def test_non_anchor_entry_in_anchor_mapping_is_missing_anchor() -> None:
    claims, _, _ = _convert([_field(location=["S000001"])], anchors={_S1: {"precision": "exact"}})

    assert _only(claims).support == "missing_anchor"


def test_empty_anchor_mapping_makes_every_citing_claim_missing_anchor() -> None:
    claims, _, _ = _convert(
        [_field(field_index=1, location=["S000001"]), _field(field_index=2, location=[])], anchors={}
    )

    assert [c.support for c in claims] == ["missing_anchor", "unsupported"]


def test_unmapped_citation_alongside_anchored_one_is_supported() -> None:
    claims, _, _ = _convert([_field(location=["S000003", "S000001"])], anchors={_S1: _EXACT})

    assert _only(claims).support == "supported"


# ---------------------------------------------------------------------------
# Reference findings retain the claim (5.1 with 5.4)
# ---------------------------------------------------------------------------


def test_unknown_reference_is_retained_with_a_dangling_reference_finding() -> None:
    claims, edges, findings = _convert([_field(location=["S000001", "S999999"])])

    claim = _only(claims)
    assert claim.support == "supported"
    assert claim.cited_evidence_ids == (_S1,)
    assert [e.to_id for e in edges] == [_S1]

    finding = _only(findings)
    assert finding.code == "dangling_reference"
    assert finding.severity == DEFAULT_SEVERITIES["dangling_reference"]
    assert finding.node_id == claim.node_id
    assert finding.edge_id is None
    assert finding.details["reference"] == "S999999"
    assert finding.details["field_index"] == 7
    assert finding.details["position"] == 1
    assert "S999999" in finding.message


def test_claim_citing_only_unknown_evidence_is_retained_as_unsupported() -> None:
    claims, edges, findings = _convert([_field(location=["S999999"])])

    assert _only(claims).support == "unsupported"
    assert _only(claims).cited_evidence_ids == ()
    assert edges == []
    assert _only(findings).code == "dangling_reference"


@pytest.mark.parametrize("reference", ["", 12, None, ["S000001"]])
def test_malformed_reference_is_a_dangling_reference_finding(reference: Any) -> None:
    claims, edges, findings = _convert([_field(location=[reference])])

    assert _only(claims).support == "unsupported"
    assert edges == []
    finding = _only(findings)
    assert finding.code == "dangling_reference"
    assert finding.details["reference"] == reference
    assert finding.details["reason"] == "malformed"


def test_reference_to_another_source_not_in_the_known_set_is_dangling() -> None:
    foreign = format_evidence_id(_OTHER_SOURCE_ID, "S000001")
    claims, edges, findings = _convert([_field(location=[foreign])])

    assert _only(claims).support == "unsupported"
    assert edges == []
    assert _only(findings).code == "dangling_reference"


def test_duplicated_reference_yields_one_edge_and_one_duplicate_finding() -> None:
    claims, edges, findings = _convert([_field(location=["S000001", "S000002", "S000001", "S000001"])])

    claim = _only(claims)
    assert claim.cited_evidence_ids == (_S1, _S2)
    assert [e.to_id for e in edges] == [_S1, _S2]
    finding = _only(findings)
    assert finding.code == "duplicate_reference"
    assert finding.severity == DEFAULT_SEVERITIES["duplicate_reference"]
    assert finding.node_id == claim.node_id
    assert finding.details["reference"] == "S000001"
    assert finding.details["positions"] == [0, 2, 3]


def test_bare_and_qualified_forms_of_one_reference_are_duplicates() -> None:
    claims, edges, findings = _convert([_field(location=["S000001", _S1])])

    assert _only(claims).cited_evidence_ids == (_S1,)
    assert len(edges) == 1
    assert _only(findings).code == "duplicate_reference"


def test_repeated_unknown_reference_yields_one_dangling_and_one_duplicate_finding() -> None:
    _, _, findings = _convert([_field(location=["S999999", "S999999"])])

    assert sorted(f.code for f in findings) == ["dangling_reference", "duplicate_reference"]


@pytest.mark.parametrize("reference", ["s000001", " S000001", "S000001 "])
def test_near_match_reference_is_ambiguous_and_not_cited(reference: str) -> None:
    claims, edges, findings = _convert([_field(location=[reference])])

    claim = _only(claims)
    assert claim.support == "unsupported"
    assert claim.cited_evidence_ids == ()
    assert edges == []
    finding = _only(findings)
    assert finding.code == "ambiguous_reference"
    assert finding.severity == DEFAULT_SEVERITIES["ambiguous_reference"]
    assert finding.node_id == claim.node_id
    assert finding.details["reference"] == reference
    assert finding.details["candidates"] == [_S1]


def test_reference_matching_several_known_ids_lists_every_candidate() -> None:
    upper = format_evidence_id(_SOURCE_ID, "Fig1")
    lower = format_evidence_id(_SOURCE_ID, "fig1")
    _, _, findings = _convert([_field(location=["FIG1"])], known=frozenset({upper, lower}))

    finding = _only(findings)
    assert finding.code == "ambiguous_reference"
    assert finding.details["candidates"] == sorted([upper, lower])


def test_findings_have_unique_deterministic_ids() -> None:
    fields = [
        _field(field_index=1, location=["S999999", "s000001", "S000001", "S000001"]),
        _field(field_index=2, location=["S999999"]),
    ]
    _, _, findings = _convert(fields)
    _, _, again = _convert(fields)

    ids = [f.finding_id for f in findings]
    assert len(ids) == len(set(ids)) == 4
    assert all(i.startswith("finding:") for i in ids)
    assert again == findings


# ---------------------------------------------------------------------------
# Non-model origin (5.7)
# ---------------------------------------------------------------------------


def test_prefilled_field_carries_the_non_model_origin() -> None:
    claims, edges, findings = _convert([_prefilled_field(1), _prefilled_field(2, "2015")], origin="prefill")

    assert [c.origin for c in claims] == ["prefill", "prefill"]
    assert [c.node_id for c in claims] == [derive_claim_id(_SOURCE_ID, 1), derive_claim_id(_SOURCE_ID, 2)]
    # Literal rule: prefilled fields carry location=[], so they classify as unsupported;
    # the origin on the node is what distinguishes them from a model claim with no evidence.
    assert [c.support for c in claims] == ["unsupported", "unsupported"]
    assert edges == [] and findings == []


def test_model_fields_carry_the_model_origin() -> None:
    assert _only(_convert([_field()], origin="model")[0]).origin == "model"


@pytest.mark.parametrize("origin", ["llm", "", None, "Prefill"])
def test_origin_outside_the_vocabulary_is_rejected(origin: Any) -> None:
    with pytest.raises(ValueError):
        _convert([_field()], origin=origin)


# ---------------------------------------------------------------------------
# Revision edges (5.5)
# ---------------------------------------------------------------------------


def test_merged_claim_links_to_every_contributing_claim() -> None:
    contributors = [derive_claim_id(_SOURCE_ID, 7), derive_claim_id(_OTHER_SOURCE_ID, 7), derive_claim_id(_SOURCE_ID, 8)]
    claims, edges, findings = _convert([_field(revision=1, contributing_claim_ids=contributors)])

    merged = _only(claims)
    assert merged.node_id == derive_claim_id(_SOURCE_ID, 7, revision=1)
    revises = [e for e in edges if e.kind == "revises"]
    assert [(e.from_id, e.to_id) for e in revises] == [(merged.node_id, c) for c in contributors]
    assert [e.edge_id for e in revises] == [f"revises:{merged.node_id}->{c}" for c in contributors]
    assert findings == []


def test_synthesized_claim_links_contributors_and_still_cites_evidence() -> None:
    c1 = derive_claim_id(_SOURCE_ID, 7, revision=1)
    c2 = derive_claim_id(_SOURCE_ID, 7, revision=2)
    claims, edges, _ = _convert([_field(revision=3, contributing_claim_ids=(c1, c2))])

    kinds = [(e.kind, e.to_id) for e in edges]
    assert kinds == [("cites_evidence", _S1), ("revises", c1), ("revises", c2)]
    assert _only(claims).support == "supported"


def test_claim_without_contributors_has_no_revision_edges() -> None:
    _, edges, _ = _convert([_field()])

    assert all(e.kind != "revises" for e in edges)


def test_repeated_contributor_yields_one_edge_and_a_duplicate_finding() -> None:
    c1 = derive_claim_id(_SOURCE_ID, 7)
    claims, edges, findings = _convert([_field(revision=1, contributing_claim_ids=[c1, c1])])

    assert [e.to_id for e in edges if e.kind == "revises"] == [c1]
    finding = _only(findings)
    assert finding.code == "duplicate_reference"
    assert finding.node_id == _only(claims).node_id
    assert finding.details["reference"] == c1
    assert finding.details["reference_kind"] == "contributing_claim"


@pytest.mark.parametrize("contributor", ["", 3, None, "claim:", "S000001", _S1])
def test_malformed_contributor_is_an_invalid_identifier_finding(contributor: Any) -> None:
    claims, edges, findings = _convert([_field(revision=1, contributing_claim_ids=[contributor])])

    assert _only(claims).node_id == derive_claim_id(_SOURCE_ID, 7, revision=1)
    assert [e for e in edges if e.kind == "revises"] == []
    finding = _only(findings)
    assert finding.code == "invalid_identifier"
    assert finding.node_id == claims[0].node_id
    assert finding.details["reference"] == contributor


# ---------------------------------------------------------------------------
# Totality: unidentifiable fields are reported, never silently dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "not a mapping",
        {"field_name": "x", "location": []},
        {"field_index": "7"},
        {"field_index": True},
        {"field_index": -1},
        {"field_index": 7, "revision": -1},
        {"field_index": 7, "revision": "1"},
    ],
)
def test_unidentifiable_field_is_skipped_with_an_invalid_identifier_finding(bad: Any) -> None:
    claims, _, findings = _convert([_field(field_index=1), bad, _field(field_index=3)])

    assert [c.field_index for c in claims] == [1, 3]
    finding = _only(findings)
    assert finding.code == "invalid_identifier"
    assert finding.severity == DEFAULT_SEVERITIES["invalid_identifier"]
    assert finding.node_id is None
    assert finding.details["position"] == 1
    assert finding.details["source_id"] == _SOURCE_ID


def test_malformed_field_name_and_confidence_do_not_raise() -> None:
    claims, _, _ = _convert([_field(field_name=None, confidence=3, extracted_value=object())])

    claim = _only(claims)
    assert claim.field_name == ""
    assert claim.confidence is None
    assert claim.value_digest


def test_string_location_is_one_reference() -> None:
    claims, _, findings = _convert([_field(location="S000001")])

    assert _only(claims).cited_evidence_ids == (_S1,)
    assert findings == []


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def adapter_log() -> Iterator[_ListHandler]:
    """Capture records directly on the adapter's logger.

    ``setup_logging`` sets ``propagate = False`` on the ``evi_trace`` root, so
    pytest's ``caplog`` would miss these records once another test configured logging.
    """
    handler = _ListHandler()
    adapter_logger = claim_adapter.logger
    previous_level = adapter_logger.level
    adapter_logger.addHandler(handler)
    adapter_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        adapter_logger.removeHandler(handler)
        adapter_logger.setLevel(previous_level)


def test_findings_are_logged_with_one_warning_per_call(adapter_log: _ListHandler) -> None:
    _convert([_field(location=["S999999", "s000001"]), "bad"])

    records = adapter_log.records
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _SOURCE_ID in warnings[0].getMessage()
    assert sum(r.levelno == logging.DEBUG for r in records) == 3
    assert all("120 patients" not in r.getMessage() for r in records)


def test_clean_call_logs_no_warning(adapter_log: _ListHandler) -> None:
    _convert([_field()])

    assert not [r for r in adapter_log.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Caller errors raise
# ---------------------------------------------------------------------------


def test_malformed_source_id_raises() -> None:
    with pytest.raises(InvalidIdentifierError):
        claim_nodes_from_fields(
            [_field()], source_id="paper_a", run_id=_RUN_ID, known_evidence_ids=_KNOWN, origin="model",
            evidence_anchors=_ANCHORS,
        )


@pytest.mark.parametrize("run_id", ["", None])
def test_malformed_run_id_raises(run_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        claim_nodes_from_fields(
            [_field()], source_id=_SOURCE_ID, run_id=run_id, known_evidence_ids=_KNOWN, origin="model",
            evidence_anchors=_ANCHORS,
        )


def test_fields_must_be_a_sequence_of_mappings_not_a_string() -> None:
    with pytest.raises(TypeError):
        claim_nodes_from_fields(
            "fields", source_id=_SOURCE_ID, run_id=_RUN_ID, known_evidence_ids=_KNOWN, origin="model",  # type: ignore[arg-type]
            evidence_anchors=_ANCHORS,
        )


def test_known_evidence_ids_must_be_a_set() -> None:
    with pytest.raises(TypeError):
        _convert([_field()], known=[_S1])


def test_evidence_anchors_must_be_a_mapping() -> None:
    with pytest.raises(TypeError):
        _convert([_field()], anchors=[_EXACT])


def test_omitting_evidence_anchors_raises() -> None:
    with pytest.raises(TypeError):
        claim_nodes_from_fields(  # type: ignore[call-arg]
            [_field()], source_id=_SOURCE_ID, run_id=_RUN_ID, known_evidence_ids=_KNOWN, origin="model"
        )


def test_none_evidence_anchors_raises() -> None:
    with pytest.raises(TypeError):
        _convert([_field()], anchors=None)


# ---------------------------------------------------------------------------
# Outputs feed the recorder unchanged
# ---------------------------------------------------------------------------


def test_outputs_record_into_the_recorder() -> None:
    run = RunIdentity(
        run_id=_RUN_ID,
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=("m",),
        document_fingerprints={},
        created_at="2026-01-01T00:00:00Z",
    )
    recorder = ProvenanceRecorder(run)
    claims, edges, findings = _convert(
        [_field(field_index=1, location=["S000001", "S999999"]), _field(field_index=2, location=["S999999"])]
    )
    for claim in claims:
        recorder.record_claim(claim, [e for e in edges if e.from_id == claim.node_id])
    for finding in findings:
        recorder.record_issue(finding)

    snapshot = recorder.snapshot()
    assert set(snapshot.nodes) == {c.node_id for c in claims}
    assert set(snapshot.findings) == set(findings)


# ---------------------------------------------------------------------------
# Module boundary: re-export, imports, no direct hashing
# ---------------------------------------------------------------------------


def test_adapters_package_re_exports_the_claim_adapter() -> None:
    assert adapters.claim_nodes_from_fields is claim_nodes_from_fields


def _imported_modules() -> set[str]:
    tree = ast.parse(Path(claim_adapter.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_module_imports_only_stdlib_provenance_and_logging() -> None:
    allowed_provenance = {"provenance.models", "provenance.identity", "provenance.privacy_carrier"}
    for module in _imported_modules():
        root = module.split(".")[0]
        if root == "provenance":
            assert module in allowed_provenance, module
        elif root == "utils":
            assert module == "utils.logging_utils", module
        else:
            assert root in {"__future__", "typing", "collections"}, module


def test_module_imports_no_hashing_library() -> None:
    hashing = {"hashlib", "hmac", "zlib", "binascii", "secrets", "uuid"}
    assert not {module.split(".")[0] for module in _imported_modules()} & hashing
