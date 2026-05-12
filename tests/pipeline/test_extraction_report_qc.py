"""Unit tests for pipeline/extraction_report.py — flagged fields collection and CSV output.

Imports extraction_report directly from its file to avoid triggering
pipeline/__init__.py, which imports orchestrator → api_client → openai.

Covers:
  - _collect_qc_data: flagging, exclusion, not_reported counts, sort order
  - _write_flagged_fields_csv: CSV header correctness
  - generate_flagged_fields_report: end-to-end row count

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
"""
import csv
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Direct module import — bypasses pipeline/__init__.py → orchestrator → openai
# ---------------------------------------------------------------------------
_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "pipeline" / "extraction_report.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "pipeline.extraction_report", _MODULE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_ER_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["pipeline.extraction_report"] = _ER_MODULE
_SPEC.loader.exec_module(_ER_MODULE)

_collect_qc_data = _ER_MODULE._collect_qc_data
_write_flagged_fields_csv = _ER_MODULE._write_flagged_fields_csv
generate_flagged_fields_report = _ER_MODULE.generate_flagged_fields_report


# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------

def _make_results(entries):
    """Build a pipeline results list from a compact spec.

    Parameters
    ----------
    entries:
        Iterable of ``(pdf_name, [(field_index, confidence, extracted_value)])``.

    Returns
    -------
    list[dict]
        A list of result dicts in the format consumed by ``_collect_qc_data``
        and ``generate_flagged_fields_report``.
    """
    return [
        {
            "pdf": pdf,
            "fields": [
                {
                    "field_index": fi,
                    "confidence": conf,
                    "extracted_value": val,
                    "domain_group": f"{fi}. Group",
                    "field_name": f"Field {fi}",
                    "evidence": "",
                }
                for fi, conf, val in fields
            ],
        }
        for pdf, fields in entries
    ]


# ---------------------------------------------------------------------------
# _collect_qc_data tests
# ---------------------------------------------------------------------------

def test_collect_qc_data_flags_low_confidence():
    """Fields with confidence 'l' or 'nr' must all appear in flagged_rows."""
    results = _make_results([
        ("paper_a.pdf", [
            (1, "l",  "some value"),
            (2, "nr", "nr"),
            (3, "h",  "high value"),
        ]),
    ])

    flagged_rows, _ = _collect_qc_data(results)

    flagged_keys = {(r["pdf"], r["field_index"]) for r in flagged_rows}
    assert ("paper_a.pdf", 1) in flagged_keys, "field with confidence 'l' must be flagged"
    assert ("paper_a.pdf", 2) in flagged_keys, "field with confidence 'nr' must be flagged"


def test_collect_qc_data_excludes_high_confidence():
    """Fields with confidence 'h' or 'm' must NOT appear in flagged_rows."""
    results = _make_results([
        ("paper_b.pdf", [
            (10, "h", "high value"),
            (11, "m", "medium value"),
        ]),
    ])

    flagged_rows, _ = _collect_qc_data(results)

    flagged_keys = {(r["pdf"], r["field_index"]) for r in flagged_rows}
    assert ("paper_b.pdf", 10) not in flagged_keys, "field with confidence 'h' must not be flagged"
    assert ("paper_b.pdf", 11) not in flagged_keys, "field with confidence 'm' must not be flagged"


def test_collect_qc_data_not_reported_count():
    """not_reported dict must count fields where extracted_value == 'nr'."""
    results = _make_results([
        ("paper_c.pdf", [
            (5, "nr", "nr"),
            (5, "nr", "nr"),
            (7, "nr", "nr"),
            (5, "l",  "some text"),   # confidence 'l' but value is not 'nr'
        ]),
        ("paper_d.pdf", [
            (5, "nr", "nr"),
        ]),
    ])

    _, not_reported = _collect_qc_data(results)

    # field 5: 3 entries with extracted_value == "nr" (2 from paper_c + 1 from paper_d)
    assert not_reported[5] == 3, f"expected 3 for field 5, got {not_reported[5]}"
    # field 7: 1 entry with extracted_value == "nr"
    assert not_reported[7] == 1, f"expected 1 for field 7, got {not_reported[7]}"


def test_collect_qc_data_sort_order():
    """flagged_rows must be sorted ascending by (field_index, pdf)."""
    results = _make_results([
        ("z_paper.pdf", [
            (3, "l", "val"),
            (1, "nr", "nr"),
        ]),
        ("a_paper.pdf", [
            (3, "nr", "nr"),
            (1, "l", "val"),
        ]),
    ])

    flagged_rows, _ = _collect_qc_data(results)

    sort_keys = [(r["field_index"], r["pdf"]) for r in flagged_rows]
    assert sort_keys == sorted(sort_keys), (
        f"flagged_rows not sorted by (field_index, pdf): {sort_keys}"
    )


# ---------------------------------------------------------------------------
# _write_qc_csv tests
# ---------------------------------------------------------------------------

def test_write_flagged_fields_csv_header(tmp_path):
    """CSV written by _write_flagged_fields_csv must have exactly the expected header columns."""
    flagged_csv = tmp_path / "flagged_fields.csv"

    flagged_rows = [
        {
            "pdf": "paper.pdf",
            "field_index": 1,
            "domain_group": "1. Group",
            "field_name": "Field 1",
            "extracted_value": "some value",
            "evidence": "",
            "confidence": "l",
        }
    ]

    with (
        patch.object(_ER_MODULE, "OUTPUT_DIR", tmp_path),
        patch.object(_ER_MODULE, "FLAGGED_FIELDS_FILE", flagged_csv),
    ):
        _write_flagged_fields_csv(flagged_rows)

    assert flagged_csv.exists(), "Flagged fields CSV file was not created"

    with open(qc_csv, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)

    expected_header = [
        "pdf", "field_index", "domain_group", "field_name",
        "extracted_value", "evidence", "confidence",
    ]
    assert header == expected_header, f"unexpected header: {header}"


# ---------------------------------------------------------------------------
# generate_flagged_fields_report tests
# ---------------------------------------------------------------------------

def test_generate_flagged_fields_report_row_count(tmp_path):
    """Data rows in the CSV must equal the number of flagged (l/nr) fields."""
    flagged_csv = tmp_path / "flagged_fields.csv"

    # 3 flagged fields (l or nr), 2 non-flagged (h, m)
    results = _make_results([
        ("paper_e.pdf", [
            (1, "l",  "low val"),
            (2, "nr", "nr"),
            (3, "h",  "high val"),
        ]),
        ("paper_f.pdf", [
            (4, "nr", "nr"),
            (5, "m",  "medium val"),
        ]),
    ])

    with (
        patch.object(_ER_MODULE, "OUTPUT_DIR", tmp_path),
        patch.object(_ER_MODULE, "FLAGGED_FIELDS_FILE", flagged_csv),
    ):
        generate_flagged_fields_report(results)

    assert flagged_csv.exists(), "Flagged fields CSV file was not created by generate_flagged_fields_report"

    with open(flagged_csv, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        data_rows = list(reader)

    # Expected: fields 1(l), 2(nr), 4(nr) → 3 flagged rows
    assert len(data_rows) == 3, (
        f"expected 3 data rows (flagged fields), got {len(data_rows)}"
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
from hypothesis import given, settings
from hypothesis import strategies as st

st_confidence = st.sampled_from(["h", "m", "l", "nr"])

st_field_entry = st.fixed_dictionaries({
    "field_index": st.integers(min_value=1, max_value=62),
    "confidence": st_confidence,
    "extracted_value": st.one_of(st.just("nr"), st.text(min_size=1, max_size=50)),
    "domain_group": st.text(min_size=1, max_size=30),
    "field_name": st.text(min_size=1, max_size=30),
    "evidence": st.text(max_size=100),
})

st_result_entry = st.fixed_dictionaries({
    "pdf": st.text(min_size=1, max_size=30),
    "fields": st.lists(st_field_entry, min_size=0, max_size=10),
})


# ---------------------------------------------------------------------------
# Property 9: _collect_qc_data completeness and exclusion
# Validates: Requirements 8.1, 8.2, 9.1
# ---------------------------------------------------------------------------

@given(st.lists(st_result_entry))
@settings(max_examples=100)
def test_collect_qc_data_completeness_pbt(results):
    """For any results list, flagged_rows must contain exactly the l/nr fields
    and exclude all h/m fields.

    **Validates: Requirements 8.1, 8.2, 9.1**
    """
    flagged_rows, _ = _collect_qc_data(results)

    # Build expected flagged set from input
    expected_flagged = set()
    expected_excluded = set()
    for entry in results:
        pdf = entry["pdf"]
        for field in entry.get("fields", []):
            key = (pdf, field["field_index"], field["confidence"], field["extracted_value"])
            if field["confidence"] in ("l", "nr"):
                expected_flagged.add(key)
            else:
                expected_excluded.add(key)

    # Build actual flagged set from output
    actual_flagged = {
        (r["pdf"], r["field_index"], r["confidence"], r["extracted_value"])
        for r in flagged_rows
    }

    # Every l/nr field must appear in flagged_rows
    for key in expected_flagged:
        assert key in actual_flagged, (
            f"Expected flagged field {key} not found in flagged_rows"
        )

    # No h/m field must appear in flagged_rows
    for key in expected_excluded:
        assert key not in actual_flagged, (
            f"Non-flagged field {key} (h/m confidence) found in flagged_rows"
        )


# ---------------------------------------------------------------------------
# Property 10: _collect_qc_data not_reported count accuracy
# Validates: Requirements 8.3, 9.2
# ---------------------------------------------------------------------------

@given(st.lists(st_result_entry))
@settings(max_examples=100)
def test_collect_qc_data_not_reported_count_pbt(results):
    """For any results list, not_reported[fi] must equal the count of fields
    with extracted_value == 'nr' and field_index == fi across all PDFs.

    **Validates: Requirements 8.3, 9.2**
    """
    _, not_reported = _collect_qc_data(results)

    # Compute expected counts from input
    expected_counts: dict[int, int] = {}
    for entry in results:
        for field in entry.get("fields", []):
            if field["extracted_value"] == "nr":
                fi = field["field_index"]
                expected_counts[fi] = expected_counts.get(fi, 0) + 1

    # Every field_index with nr entries must have the correct count
    for fi, expected in expected_counts.items():
        actual = not_reported.get(fi, 0)
        assert actual == expected, (
            f"not_reported[{fi}] == {actual}, expected {expected}"
        )

    # not_reported must not contain field_indexes with zero nr entries
    for fi in not_reported:
        assert fi in expected_counts, (
            f"not_reported contains field_index {fi} which has no 'nr' entries"
        )
        assert not_reported[fi] > 0, (
            f"not_reported[{fi}] is {not_reported[fi]}, expected > 0"
        )
