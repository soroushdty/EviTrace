import pytest
import importlib.util
import sys
from pathlib import Path

_VALIDATOR_PATH = Path(__file__).resolve().parents[2] / "pipeline" / "validator.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_validator_direct", _VALIDATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

ValidationError = _MODULE.ValidationError
validate_chunk_output = _MODULE.validate_chunk_output
reconstruct_fields = _MODULE.reconstruct_fields


def test_validate_chunk_output_accepts_loc_arrays():
    raw = '{"extractions":[{"i":8,"v":"MIMIC-III","loc":["S000001","T000001"],"c":"h"}]}'
    result = validate_chunk_output(raw, [8], valid_location_ids={"S000001", "T000001"})
    assert result[0]["loc"] == ["S000001", "T000001"]


def test_validate_chunk_output_rejects_unknown_loc_ids():
    raw = '{"extractions":[{"i":8,"v":"MIMIC-III","loc":["S999999"],"c":"h"}]}'
    with pytest.raises(ValidationError):
        validate_chunk_output(raw, [8], valid_location_ids={"S000001"})


def test_reconstruct_fields_resolves_evidence_text_and_location_metadata():
    compact = [{"i": 8, "v": "MIMIC-IV", "loc": ["S000001"], "c": "h"}]
    lookup = {8: {"domain_group": "3. Cohort and data source", "field_name": "Dataset / database name"}}
    evidence_map = {
        "S000001": {
            "id": "S000001",
            "type": "sentence",
            "section_path": "Methods",
            "page": 3,
            "coords": [1.0, 2.0, 3.0, 4.0],
            "xpath": "//*[@xml:id='s1']",
            "text": "We used MIMIC-IV for model training.",
            "source_pdf": "paper.pdf",
        }
    }
    out = reconstruct_fields(compact, lookup, evidence_map)
    assert out[0]["evidence"] == "We used MIMIC-IV for model training."
    assert out[0]["location"] == ["S000001"]
    assert out[0]["location_metadata"][0]["page"] == 3




# ---------------------------------------------------------------------------
# Commit 13 regression tests — clean_json_string robustness
# ---------------------------------------------------------------------------


from pipeline.validator import clean_json_string


def test_clean_json_string_happy_path_unchanged():
    """Plain JSON is returned verbatim (whitespace stripped only)."""
    payload = '{"extractions":[{"i":1,"v":"x","loc":[],"c":"h"}]}'
    assert clean_json_string(payload) == payload


def test_clean_json_string_fenced_json_is_unwrapped():
    """Fenced block with 'json' tag has fences stripped."""
    payload = '{"extractions":[{"i":1,"v":"x","loc":[],"c":"h"}]}'
    wrapped = f"```json\n{payload}\n```"
    assert clean_json_string(wrapped) == payload


def test_clean_json_string_fenced_no_tag_is_unwrapped():
    """Fenced block without a language tag still has fences stripped."""
    payload = '{"extractions":[]}'
    wrapped = f"```\n{payload}\n```"
    assert clean_json_string(wrapped) == payload


def test_clean_json_string_prose_around_fence_extracts_block():
    """Prose before and after a fenced block: only the fenced content is kept."""
    payload = '{"extractions":[{"i":2,"v":"y","loc":[],"c":"m"}]}'
    wrapped = f"Here is the result:\n```json\n{payload}\n```\nLet me know."
    assert clean_json_string(wrapped) == payload


def test_clean_json_string_prose_around_bare_json_extracts_span():
    """Bare JSON with no fences but leading/trailing prose: outermost span kept."""
    payload = '{"extractions":[{"i":3,"v":"z","loc":[],"c":"l"}]}'
    wrapped = f"Sure! {payload} done."
    assert clean_json_string(wrapped) == payload


def test_clean_json_string_empty_input():
    """Empty input returns empty string (preserves old behaviour)."""
    assert clean_json_string("") == ""
    assert clean_json_string("   ") == ""
