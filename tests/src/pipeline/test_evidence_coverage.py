"""Evidence selection coverage (risk-remediation task 9.1; Requirements 10.1, 10.2, 10.5).

``select_paper_evidence`` returns the chosen items plus ``EvidenceSelectionStats``;
``build_paper_evidence_package`` is a serialising wrapper around it whose JSON must be
byte-identical to the pre-refactor builder. The 10.1 test is pinned to the real bioRxiv
GROBID fixture at the canonical defaults (150 items / 30 000 chars).

Measured at implementation time (2026-09-22, after 8.1's table de-duplication), via
``_build_items_from_tei`` on the real fixtures at 150/30000 with the 62 canonical fields:

    biorxiv: 184 items (183 substantive + 1 Metadata), 31457 substantive chars, ratio 0.946
    plosone: 241 items, 42942 substantive chars, ratio 0.699
    arxiv:   496 items, 96573 substantive chars, ratio 0.311 (97k-char paper, outside 10.1)

Only bioRxiv is asserted (design.md "EvidenceCoverage": "10.1 fixture is pinned").
"""

from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.evidence_index import (
    EvidenceBundle,
    EvidenceSelectionStats,
    _build_items_from_tei,
    build_paper_evidence_package,
    select_paper_evidence,
)
from tests.helpers.grobid_tei import load_tei_text
from utils.path_utils import EXTRACTION_MAP

DEFAULT_MAX_ITEMS = 150
DEFAULT_MAX_CHARS = 30000
COVERAGE_FLOOR = 0.6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    item_id: str,
    text: str,
    *,
    section: str = "Methods",
    score: int = 10,
    item_type: str = "sentence",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": item_type,
        "section_path": section,
        "page": 1,
        "coords": None,
        "text": text,
        "score": score,
        "annotations": {},
        "source_pdf": "/runtime/only/paper.pdf",
    }


def _bundle(items: list[dict[str, Any]], paper_id: str = "paper_cov") -> EvidenceBundle:
    return EvidenceBundle(
        paper_id=paper_id,
        tei_xml="",
        evidence_items=items,
        evidence_map={item["id"]: item for item in items},
        prefilled_fields={},
        index_path=Path("/nonexistent") / f"{paper_id}.evidence.json",
    )


def _fields() -> list[dict[str, Any]]:
    return [
        {"field_index": 8, "field_name": "Dataset", "definition": "MIMIC-III cohort", "reviewer_question": ""},
        {"field_index": 9, "field_name": "Model", "definition": "random forest classifier", "reviewer_question": ""},
    ]


def _canonical_fields() -> list[dict[str, Any]]:
    fields = json.loads(Path(EXTRACTION_MAP).read_text(encoding="utf-8"))
    assert isinstance(fields, list) and fields, "extraction map must be a non-empty list"
    return fields


def _fixture_bundle(name: str) -> EvidenceBundle:
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    return _bundle(items, paper_id=name)


def _legacy_build_paper_evidence_package(
    bundle: EvidenceBundle,
    all_fields: list[dict],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    """Verbatim copy of ``build_paper_evidence_package`` as it stood before task 9.1
    (logging removed). It is the oracle for the byte-equivalence tests below."""
    if not all_fields:
        return '{"paper_id":"","evidence":[]}'

    keywords = " ".join(
        f"{field.get('field_name', '')} {field.get('definition', '')} {field.get('reviewer_question', '')}"
        for field in all_fields
    ).lower()
    keyword_tokens = set(re.findall(r"[a-z]{4,}", keywords))

    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in bundle.evidence_items:
        text = (item.get("text") or "").lower()
        overlap = sum(1 for token in keyword_tokens if token in text)
        score = int(item.get("score", 0)) + overlap * 3
        ranked.append((score, str(item.get("id", "")), item))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    selected: list[dict[str, Any]] = []
    char_budget = 0
    for _, _, item in ranked:
        if len(selected) >= max_items:
            break
        text = item.get("text", "") or ""
        if char_budget + len(text) > max_chars:
            continue
        selected.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "section": item.get("section_path"),
                "page": item.get("page"),
                "coords": item.get("coords"),
                "text": text,
                "annotations": item.get("annotations", {}),
            }
        )
        char_budget += len(text)

    selected.sort(key=lambda x: str(x.get("id", "")))
    package = {
        "paper_id": bundle.paper_id,
        "evidence_count": len(selected),
        "evidence": selected,
    }
    return json.dumps(package, ensure_ascii=False, sort_keys=False)


# ---------------------------------------------------------------------------
# EvidenceSelectionStats — value object
# ---------------------------------------------------------------------------


def test_stats_is_frozen_dataclass():
    stats = EvidenceSelectionStats(total_items=3, substantive_chars=300, selected_items=2, selected_chars=150)
    with pytest.raises(FrozenInstanceError):
        stats.selected_chars = 0  # type: ignore[misc]


def test_coverage_ratio_is_selected_over_substantive():
    stats = EvidenceSelectionStats(total_items=3, substantive_chars=300, selected_items=2, selected_chars=150)
    assert stats.coverage_ratio == pytest.approx(0.5)


def test_coverage_ratio_is_zero_when_denominator_is_zero():
    stats = EvidenceSelectionStats(total_items=0, substantive_chars=0, selected_items=0, selected_chars=0)
    assert stats.coverage_ratio == 0.0


# ---------------------------------------------------------------------------
# select_paper_evidence — stats arithmetic on synthetic bundles
# ---------------------------------------------------------------------------


def test_stats_arithmetic_excludes_metadata_from_denominator():
    # Metadata title item: counted in total_items, excluded from substantive_chars.
    items = [
        _item("S000001", "a" * 200, section="Methods"),
        _item("S000002", "b" * 300, section="Results"),
        _item("T000001", "c" * 100, section="Results", item_type="table"),
        _item("S000000", "Title " * 20, section="Metadata", score=-30),
    ]
    bundle = _bundle(items)

    selected, stats = select_paper_evidence(bundle, _fields(), max_items=10, max_chars=100_000)

    assert stats.total_items == 4
    assert stats.substantive_chars == 600
    # Everything fits under the caps, including the Metadata item.
    assert stats.selected_items == 4 == len(selected)
    assert stats.selected_chars == 600 + len("Title " * 20)
    assert stats.selected_chars == sum(len(item["text"]) for item in selected)


def test_metadata_only_bundle_has_zero_ratio_but_counts_items():
    items = [_item("S000000", "Just a title", section="Metadata", score=-30)]
    selected, stats = select_paper_evidence(_bundle(items), _fields(), max_items=10, max_chars=1000)

    assert stats.total_items == 1
    assert stats.substantive_chars == 0
    assert stats.selected_items == 1 == len(selected)
    assert stats.coverage_ratio == 0.0


def test_empty_bundle_yields_zero_stats():
    selected, stats = select_paper_evidence(_bundle([]), _fields(), max_items=10, max_chars=1000)
    assert selected == []
    assert stats == EvidenceSelectionStats(total_items=0, substantive_chars=0, selected_items=0, selected_chars=0)
    assert stats.coverage_ratio == 0.0


def test_selected_counts_reflect_the_caps_and_ratio_follows():
    items = [_item(f"S{i:06d}", "x" * 100) for i in range(1, 11)]  # 10 × 100 chars
    selected, stats = select_paper_evidence(_bundle(items), _fields(), max_items=4, max_chars=100_000)
    assert len(selected) == 4
    assert stats == EvidenceSelectionStats(total_items=10, substantive_chars=1000, selected_items=4, selected_chars=400)
    assert stats.coverage_ratio == pytest.approx(0.4)

    selected, stats = select_paper_evidence(_bundle(items), _fields(), max_items=100, max_chars=250)
    assert len(selected) == 2
    assert stats.selected_chars == 200
    assert stats.coverage_ratio == pytest.approx(0.2)


def test_selected_items_are_in_ascending_id_order_and_strip_runtime_keys():
    items = [
        _item("T000001", "table text", score=30, item_type="table"),
        _item("S000003", "MIMIC-III cohort", score=40),
        _item("F000001", "figure caption", score=20, item_type="figure_caption"),
        _item("S000001", "plain", score=10),
    ]
    selected, _ = select_paper_evidence(_bundle(items), _fields(), max_items=10, max_chars=10_000)
    ids = [item["id"] for item in selected]
    assert ids == ["F000001", "S000001", "S000003", "T000001"]
    for item in selected:
        assert set(item) == {"id", "type", "section", "page", "coords", "text", "annotations"}


def test_no_fields_selects_nothing_but_still_measures_the_bundle():
    items = [_item("S000001", "a" * 50), _item("S000000", "t", section="Metadata")]
    selected, stats = select_paper_evidence(_bundle(items), [], max_items=10, max_chars=1000)
    assert selected == []
    assert stats == EvidenceSelectionStats(total_items=2, substantive_chars=50, selected_items=0, selected_chars=0)


@given(
    n_items=st.integers(min_value=0, max_value=40),
    text_len=st.integers(min_value=1, max_value=50),
    n_meta=st.integers(min_value=0, max_value=3),
    max_items=st.integers(min_value=0, max_value=20),
    max_chars=st.integers(min_value=0, max_value=400),
)
@settings(max_examples=60)
def test_stats_invariants_property(n_items, text_len, n_meta, max_items, max_chars):
    items = [_item(f"S{i + 1:06d}", "x" * text_len) for i in range(n_items)]
    items += [_item(f"M{i + 1:06d}", "m" * text_len, section="Metadata", score=-30) for i in range(n_meta)]
    selected, stats = select_paper_evidence(_bundle(items), _fields(), max_items=max_items, max_chars=max_chars)

    assert stats.total_items == n_items + n_meta
    assert stats.substantive_chars == n_items * text_len
    assert stats.selected_items == len(selected) <= max_items
    assert stats.selected_chars == sum(len(item["text"]) for item in selected) <= max_chars
    if stats.substantive_chars == 0:
        assert stats.coverage_ratio == 0.0
    else:
        assert stats.coverage_ratio == pytest.approx(stats.selected_chars / stats.substantive_chars)


# ---------------------------------------------------------------------------
# build_paper_evidence_package is a serialising wrapper — byte-identical output
# ---------------------------------------------------------------------------


def test_wrapper_matches_legacy_builder_on_synthetic_bundle():
    items = [
        _item("T000001", "table about the MIMIC-III cohort", score=30, item_type="table"),
        _item("S000003", "random forest classifier trained on the cohort", score=40),
        _item("F000001", "figure caption", score=20, item_type="figure_caption"),
        _item("S000001", "plain sentence " * 5, score=10),
        _item("S000000", "Paper title", section="Metadata", score=-30),
    ]
    bundle = _bundle(items)
    for max_items, max_chars in ((10, 10_000), (2, 10_000), (10, 60), (0, 10_000), (10, 0)):
        assert build_paper_evidence_package(bundle, _fields(), max_items=max_items, max_chars=max_chars) == (
            _legacy_build_paper_evidence_package(bundle, _fields(), max_items=max_items, max_chars=max_chars)
        )


def test_wrapper_matches_legacy_builder_with_no_fields():
    bundle = _bundle([_item("S000001", "text")])
    assert build_paper_evidence_package(bundle, [], max_items=10, max_chars=100) == '{"paper_id":"","evidence":[]}'
    assert build_paper_evidence_package(bundle, [], max_items=10, max_chars=100) == (
        _legacy_build_paper_evidence_package(bundle, [], max_items=10, max_chars=100)
    )


@pytest.mark.parametrize("name", ["biorxiv", "plosone", "arxiv"])
def test_wrapper_matches_legacy_builder_on_real_fixtures(name):
    bundle = _fixture_bundle(name)
    fields = _canonical_fields()
    new = build_paper_evidence_package(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS)
    old = _legacy_build_paper_evidence_package(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS)
    assert new == old


def test_wrapper_serialises_exactly_the_selected_items():
    bundle = _fixture_bundle("biorxiv")
    fields = _canonical_fields()
    selected, stats = select_paper_evidence(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS)
    package = json.loads(build_paper_evidence_package(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS))
    assert package == {"paper_id": "biorxiv", "evidence_count": stats.selected_items, "evidence": selected}


# ---------------------------------------------------------------------------
# Requirement 10.1 — pinned bioRxiv fixture at default configuration
# Requirement 10.5 — pruning still happens
# ---------------------------------------------------------------------------


def test_biorxiv_fixture_at_defaults_covers_at_least_60_percent():
    bundle = _fixture_bundle("biorxiv")
    selected, stats = select_paper_evidence(
        bundle, _canonical_fields(), max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS
    )

    # Sanity on the fixture premise (10.1 talks about a ~30k-char paper).
    assert 25_000 <= stats.substantive_chars <= 40_000, stats
    assert stats.coverage_ratio >= COVERAGE_FLOOR, (
        f"bioRxiv coverage {stats.coverage_ratio:.3f} < {COVERAGE_FLOOR}: {stats}"
    )
    assert stats.selected_items == len(selected)
    assert stats.selected_chars == sum(len(item["text"]) for item in selected)


def test_biorxiv_fixture_at_defaults_is_still_pruned():
    bundle = _fixture_bundle("biorxiv")
    _, stats = select_paper_evidence(
        bundle, _canonical_fields(), max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS
    )
    assert stats.total_items > DEFAULT_MAX_ITEMS, "fixture premise: more items than the item cap"
    assert stats.selected_items < stats.total_items
    assert stats.selected_items <= DEFAULT_MAX_ITEMS
    assert stats.selected_chars <= DEFAULT_MAX_CHARS


def test_char_cap_reduces_coverage_rather_than_being_exceeded():
    # 10.2: a tighter char budget lowers coverage; the budget is never exceeded.
    bundle = _fixture_bundle("biorxiv")
    fields = _canonical_fields()
    _, wide = select_paper_evidence(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS)
    _, tight = select_paper_evidence(bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=10_000)
    assert tight.selected_chars <= 10_000
    assert tight.coverage_ratio < wide.coverage_ratio
    assert tight.substantive_chars == wide.substantive_chars


# ---------------------------------------------------------------------------
# Requirement 10.3 — the evidence-budget documentation must agree with what
# the ranker actually does at the configured defaults (task 9.3).
#
# Every figure asserted below is recomputed from the real fixtures on each
# run, so the three documentation sites cannot silently drift from the code.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_YAML = _REPO_ROOT / "configs" / "config.yaml"
_CONFIG_README = _REPO_ROOT / "configs" / "README.md"
_STEERING_CONFIG = _REPO_ROOT / ".kiro" / "steering" / "config.md"
_DOC_SITES = {
    "configs/config.yaml": _CONFIG_YAML,
    "configs/README.md": _CONFIG_README,
    ".kiro/steering/config.md": _STEERING_CONFIG,
}
_FIXTURES = ("biorxiv", "plosone", "arxiv")


def _stats_at_defaults(name: str) -> EvidenceSelectionStats:
    _, stats = select_paper_evidence(
        _fixture_bundle(name), _canonical_fields(), max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS
    )
    return stats


def _mean_len(items: list[dict[str, Any]]) -> float:
    return sum(len(item["text"]) for item in items) / len(items)


def test_item_cap_binds_on_biorxiv_but_char_cap_binds_on_plosone_and_arxiv():
    """The prose claim "whichever cap binds first -- the item cap on papers
    with short sentences, the 30 000-char cap otherwise" is a statement about
    the ranker, so it is measured here rather than trusted.

    A cap "binds" when it is the constraint that stopped selection: the item
    cap when exactly ``max_items`` were taken with room left under the char
    cap; the char cap when fewer than ``max_items`` were taken although
    unselected items remained (the only thing that can then exclude an item
    is the character budget).
    """
    biorxiv = _stats_at_defaults("biorxiv")
    assert biorxiv.selected_items == DEFAULT_MAX_ITEMS, biorxiv
    assert biorxiv.selected_chars < DEFAULT_MAX_CHARS, biorxiv

    for name in ("plosone", "arxiv"):
        stats = _stats_at_defaults(name)
        assert stats.selected_items < DEFAULT_MAX_ITEMS, (name, stats)
        assert stats.selected_items < stats.total_items, (name, stats)
        assert stats.selected_chars <= DEFAULT_MAX_CHARS, (name, stats)


@pytest.mark.parametrize("name", _FIXTURES)
def test_ranker_selects_longer_than_average_items(name):
    """Why the char cap can bind before 150 x mean-item-length would predict:
    the keyword-overlap ranker prefers longer sentences, so the selected mean
    exceeds the population mean on every real fixture."""
    bundle = _fixture_bundle(name)
    selected, _ = select_paper_evidence(
        bundle, _canonical_fields(), max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS
    )
    substantive = [item for item in bundle.evidence_items if item.get("section_path") != "Metadata"]
    assert _mean_len(selected) > _mean_len(substantive), name


@pytest.mark.parametrize("name", _FIXTURES)
def test_uncapped_ratio_marginally_exceeds_one_because_metadata_is_selected(name):
    """The numerator/denominator asymmetry documented in configs/README.md:
    ``selected_chars`` counts a selected Metadata item, ``substantive_chars``
    excludes it by definition, so with no cap binding the ratio lands just
    above 1.0. At the defaults the Metadata item is never selected on these
    fixtures, so the capped ratio stays at or below 1.0."""
    bundle = _fixture_bundle(name)
    fields = _canonical_fields()
    selected, uncapped = select_paper_evidence(bundle, fields, max_items=10**9, max_chars=10**9)
    assert any(item["section"] == "Metadata" for item in selected)
    assert 1.0 < uncapped.coverage_ratio < 1.01, (name, uncapped)

    capped_selected, capped = select_paper_evidence(
        bundle, fields, max_items=DEFAULT_MAX_ITEMS, max_chars=DEFAULT_MAX_CHARS
    )
    assert not any(item["section"] == "Metadata" for item in capped_selected)
    assert 0.0 <= capped.coverage_ratio <= 1.0, (name, capped)


def test_docs_state_the_measured_coverage_and_the_binding_rule():
    """Requirement 10.3: the three documentation sites state the coverage at
    defaults as measured on the reference fixtures (2 dp, bioRxiv / PLOS ONE
    / arXiv order) and describe the binding cap as "whichever cap binds
    first" rather than naming one cap unconditionally."""
    ratios = " / ".join(f"{_stats_at_defaults(name).coverage_ratio:.2f}" for name in _FIXTURES)
    for label, path in _DOC_SITES.items():
        text = path.read_text(encoding="utf-8")
        assert ratios in text, f"{label} must state the measured coverage at defaults: {ratios}"
        assert "whichever cap binds first" in text, f"{label} must describe the binding cap conditionally"
        assert "is the binding constraint" not in text, f"{label} still names one cap as always binding"
        assert "item cap binds first" not in text, f"{label} still names one cap as always binding"


def test_readme_documents_the_metadata_ratio_asymmetry():
    """The 9.2 decision (task 9.3): the asymmetry is documented, not clamped."""
    text = _CONFIG_README.read_text(encoding="utf-8")
    assert "exceed 1.0" in text
    assert "denominator excludes it" in text
