"""Unit tests for _load_completed_result and _save_pdf_output in pipeline/pdf_processor.py.

Requirements: 10.1, 10.2, 10.3, 10.4
"""
import importlib
import json
import sys
from unittest.mock import MagicMock, patch


def _import_pdf_processor():
    """Import pipeline.pdf_processor directly, bypassing pipeline/__init__.py.

    pipeline/__init__.py imports orchestrator which imports api_client which
    requires the 'openai' package. We bypass this by patching the problematic
    modules before importing pdf_processor directly.
    """
    # Remove any cached pipeline modules to get a clean import
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("pipeline"):
            del sys.modules[mod_name]

    # Stub out the heavy dependencies that pdf_processor imports
    mock_api_client = MagicMock()
    mock_api_client.extract_chunk = MagicMock()
    mock_api_client.warm_pdf_cache = MagicMock()

    with patch.dict(
        sys.modules,
        {
            "agents": MagicMock(),
            "agents.openai": MagicMock(),
            "agents.openai.api_client": mock_api_client,
        },
    ):
        import pipeline.pdf_processor as m  # noqa: PLC0415

    return m


_pdf_processor = _import_pdf_processor()
_load_completed_result = _pdf_processor._load_completed_result
_save_pdf_output = _pdf_processor._save_pdf_output


# ---------------------------------------------------------------------------
# _load_completed_result tests
# ---------------------------------------------------------------------------


def test_load_completed_result_complete_with_file(tmp_path):
    """Manifest status 'complete' + output file present → returns the fields list."""
    pdf_name = "paper_alpha"
    fields = [
        {"field_index": 1, "extracted_value": "Smith", "confidence": "h"},
        {"field_index": 2, "extracted_value": "2020", "confidence": "h"},
    ]
    # Write the expected output file
    out_file = tmp_path / f"{pdf_name}.extracted.json"
    out_file.write_text(json.dumps(fields), encoding="utf-8")

    manifest = {pdf_name: {"status": "complete"}}

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        result = _load_completed_result(pdf_name, manifest)

    assert result == fields


def test_load_completed_result_not_complete(tmp_path):
    """Manifest status is not 'complete' → returns None regardless of file presence."""
    pdf_name = "paper_beta"
    manifest = {pdf_name: {"status": "failed_chunks", "failed_chunks": [2]}}

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        result = _load_completed_result(pdf_name, manifest)

    assert result is None


def test_load_completed_result_complete_missing_file(tmp_path):
    """Manifest status 'complete' but output file absent → returns None."""
    pdf_name = "paper_gamma"
    manifest = {pdf_name: {"status": "complete"}}

    # No file written to tmp_path
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        result = _load_completed_result(pdf_name, manifest)

    assert result is None


# ---------------------------------------------------------------------------
# _save_pdf_output tests
# ---------------------------------------------------------------------------


def test_save_pdf_output_round_trip(tmp_path):
    """_save_pdf_output writes JSON that can be read back as an equal list."""
    pdf_name = "paper1"
    fields = [
        {
            "field_index": 3,
            "domain_group": 2,
            "field_name": "Study design",
            "extracted_value": "RCT",
            "evidence": "randomised controlled trial",
            "location": ["ev-001"],
            "location_metadata": [],
            "confidence": "h",
        },
        {
            "field_index": 5,
            "domain_group": 3,
            "field_name": "Sample size",
            "extracted_value": "120",
            "evidence": "n=120 participants",
            "location": ["ev-042"],
            "location_metadata": [],
            "confidence": "m",
        },
    ]

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        _save_pdf_output(pdf_name, fields)

    out_file = tmp_path / f"{pdf_name}.extracted.json"
    assert out_file.exists(), "Output JSON file was not created"

    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded == fields


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

st_confidence = st.sampled_from(["h", "m", "l", "nr"])

st_field_entry = st.fixed_dictionaries({
    "field_index": st.integers(min_value=1, max_value=62),
    "confidence": st_confidence,
    "extracted_value": st.one_of(st.just("nr"), st.text(min_size=1, max_size=50)),
    "domain_group": st.integers(min_value=1, max_value=13),
    "field_name": st.text(min_size=1, max_size=30),
    "evidence": st.text(max_size=100),
    "location": st.just([]),
    "location_metadata": st.just([]),
})


# ---------------------------------------------------------------------------
# Property 11: _save_pdf_output round-trip
# Validates: Requirements 10.4
# ---------------------------------------------------------------------------

@given(st.lists(st_field_entry, min_size=0, max_size=20))
@settings(max_examples=50)
def test_save_pdf_output_round_trip_pbt(fields):
    """For any fields list, _save_pdf_output writes a JSON file such that
    reading and parsing that file returns a list equal to the original.

    Uses tempfile.TemporaryDirectory() instead of tmp_path fixture because
    Hypothesis health-checks reject function-scoped pytest fixtures.

    **Validates: Requirements 10.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        from pathlib import Path
        tmp_path = Path(tmp_dir)

        with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
            _save_pdf_output("pbt_paper", fields)

        out_file = tmp_path / "pbt_paper.extracted.json"
        assert out_file.exists(), "Output JSON file was not created"

        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded == fields, (
            f"Round-trip mismatch: saved {fields!r}, loaded {loaded!r}"
        )


# ---------------------------------------------------------------------------
# Async tests: _run_parallel_chunks and process_pdf
# Requirements: 10.5, 10.6, 10.7
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, patch


def _make_qc_context(pdf_name: str, exact_text: str = "sample text"):
    """Build a minimal QCBundle for pdf_processor tests."""
    from quality_control.models import Candidate, QCBundle, UnifiedRecord

    unified = UnifiedRecord(
        document_id=pdf_name,
        content={"exact_text": exact_text, "source_pdf_path": ""},
    )
    return QCBundle(
        branches=[Candidate(source="grobid", index=0, payload="<TEI/>", status=None)],
        unified=unified,
    )


def test_run_parallel_chunks_all_succeed(tmp_path):
    """All extract_chunk calls succeed → returned list length equals chunk count."""
    # Two extraction chunks (chunk 1 and chunk 2); num_chunks=3 means synthesis is chunk 3.
    chunk_fields = {
        1: [{"field_index": 3, "field_name": "Study design", "definition": "..."}],
        2: [{"field_index": 10, "field_name": "Sample size", "definition": "..."}],
    }
    chunk_sources = {1: "evidence text chunk 1", 2: "evidence text chunk 2"}
    valid_location_ids = {"ev-001", "ev-002"}
    manifest = {"paper_test": {"status": "pending"}}
    pdf_name = "paper_test"

    # extract_chunk returns raw JSON strings; RepairRetryLoop validates them.
    # Each chunk must return a JSON string matching the expected field indices.
    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]})
        elif chunk_num == 2:
            return json.dumps({"extractions": [{"i": 10, "v": "120", "loc": [], "c": "m"}]})
        return json.dumps({"extractions": []})

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    with patch.dict(sys.modules, {"agents": MagicMock(), "agents.openai": MagicMock(), "agents.openai.api_client": mock_api}):
        async def _run():
            semaphore = asyncio.Semaphore(5)
            lock = asyncio.Lock()
            return await _pdf_processor._run_parallel_chunks(
                chunk_sources=chunk_sources,
                chunk_fields=chunk_fields,
                valid_location_ids=valid_location_ids,
                api_semaphore=semaphore,
                pdf_name=pdf_name,
                num_chunks=3,
                enable_prewarm=False,
                chunk_model="gpt-test",
                synthesis_model="gpt-test",
                prewarm_synthesis_diff=False,
                manifest=manifest,
                manifest_lock=lock,
            )

        result = asyncio.run(_run())

    assert result is not None
    assert len(result) == len(chunk_fields)


def test_run_parallel_chunks_one_fails(tmp_path):
    """One extract_chunk raises → returns None; manifest updated with 'failed_chunks'."""
    chunk_fields = {
        1: [{"field_index": 3, "field_name": "Study design", "definition": "..."}],
        2: [{"field_index": 10, "field_name": "Sample size", "definition": "..."}],
    }
    chunk_sources = {1: "evidence text chunk 1", 2: "evidence text chunk 2"}
    valid_location_ids = {"ev-001"}
    pdf_name = "paper_fail"
    manifest = {pdf_name: {"status": "pending"}}

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 2:
            raise RuntimeError("API error on chunk 2")
        return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]})

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    with patch.dict(sys.modules, {"agents": MagicMock(), "agents.openai": MagicMock(), "agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "save_manifest"):

        async def _run():
            semaphore = asyncio.Semaphore(5)
            lock = asyncio.Lock()
            return await _pdf_processor._run_parallel_chunks(
                chunk_sources=chunk_sources,
                chunk_fields=chunk_fields,
                valid_location_ids=valid_location_ids,
                api_semaphore=semaphore,
                pdf_name=pdf_name,
                num_chunks=3,
                enable_prewarm=False,
                chunk_model="gpt-test",
                synthesis_model="gpt-test",
                prewarm_synthesis_diff=False,
                manifest=manifest,
                manifest_lock=lock,
            )

        result = asyncio.run(_run())

    assert result is None
    assert manifest[pdf_name]["status"] == "failed_chunks"


def test_process_pdf_cache_hit_skips_extract_chunk(tmp_path):
    """When manifest is 'complete' and output file exists, extract_chunk is never called."""
    pdf_name = "paper_cached"
    fields = [
        {"field_index": 1, "extracted_value": "Smith", "confidence": "h"},
        {"field_index": 2, "extracted_value": "2020", "confidence": "h"},
    ]

    # Write the cached output file
    out_file = tmp_path / f"{pdf_name}.extracted.json"
    out_file.write_text(json.dumps(fields), encoding="utf-8")

    manifest = {pdf_name: {"status": "complete"}}
    qc_context = _make_qc_context(pdf_name)

    chunk_fields = {
        1: [{"field_index": 3, "field_name": "Study design", "definition": "..."}],
    }
    field_lookup = {
        3: {"domain_group": "2. Clinical context", "field_name": "Study design"},
    }
    openai_config = {
        "chunk_model": "gpt-test",
        "synthesis_model": "gpt-test",
        "enable_cache_prewarm": False,
        "num_chunks": 3,
        "prewarm_synthesis_if_model_diff": False,
        "max_evidence_items_per_chunk": 250,
        "max_evidence_chars_per_chunk": 60000,
    }

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock()
    mock_api.warm_pdf_cache = AsyncMock()

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"agents": MagicMock(), "agents.openai": MagicMock(), "agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "validate_qc_context_input"):

        async def _run():
            semaphore = asyncio.Semaphore(5)
            lock = asyncio.Lock()
            return await _pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=chunk_fields,
                field_lookup=field_lookup,
                api_semaphore=semaphore,
                manifest=manifest,
                manifest_lock=lock,
                openai_config=openai_config,
            )

        result = asyncio.run(_run())

    mock_api.extract_chunk.assert_not_called()
    assert result == fields


# ---------------------------------------------------------------------------
# process_pdf integration: deterministic merge + compact synthesis input
# (feature: token-efficient-extraction, task 8.2)
# Requirements: 4.1, 4.2, 4.3, 4.4, 5.6
# ---------------------------------------------------------------------------

import types


def _make_fake_bundle(evidence_map=None, prefilled_fields=None):
    """Minimal duck-typed EvidenceBundle stand-in.

    pdf_processor.py only ever does attribute access on the bundle it's
    handed (``bundle.evidence_map``, ``bundle.prefilled_fields``,
    ``bundle.evidence_items``) -- never an isinstance check -- so a plain
    SimpleNamespace is a safe, minimal stand-in that avoids importing the
    real EvidenceBundle dataclass (which, like every other pipeline.*
    submodule, would need to be imported from *inside* the same
    patch.dict(sys.modules, ...) block used by _import_pdf_processor() to
    avoid ending up as a second, distinct module -- see the note in
    test_repair_retry.py for why).
    """
    return types.SimpleNamespace(
        paper_id="fake_paper",
        evidence_items=[],
        evidence_map=evidence_map or {},
        prefilled_fields=prefilled_fields or {},
    )


def _base_openai_config(num_chunks: int) -> dict:
    return {
        "chunk_model": "gpt-test",
        "synthesis_model": "gpt-test",
        "enable_cache_prewarm": False,
        "num_chunks": num_chunks,
        "prewarm_synthesis_if_model_diff": False,
        "max_evidence_items_per_chunk": 250,
        "max_evidence_chars_per_chunk": 60000,
    }


def _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path):
    manifest: dict = {}
    qc_context = _make_qc_context(pdf_name)
    # NOTE: only agents.openai.api_client is mocked here -- agents.openai
    # itself must resolve to the REAL (empty __init__.py) package so that
    # RepairRetryLoop._get_system_prompt_text()'s lazy
    # `from agents.openai.prompts import get_system_prompt` (used for token
    # budget estimation, always active now that process_pdf always computes
    # real budgets) can import the real, lightweight prompts.py module.
    # agents.openai.prompts has no heavy dependency (no `openai` PyPI import)
    # so this is always safe, matching how test_repair_retry.py's per-test
    # patches already only touch agents.openai.api_client.
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "validate_qc_context_input"), \
         patch.object(_pdf_processor, "build_or_load_evidence_bundle", return_value=bundle):

        async def _run():
            semaphore = asyncio.Semaphore(5)
            lock = asyncio.Lock()
            return await _pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=chunk_fields,
                field_lookup=field_lookup,
                api_semaphore=semaphore,
                manifest=manifest,
                manifest_lock=lock,
                openai_config=openai_config,
            )

        return asyncio.run(_run())


def test_process_pdf_synthesis_runs_with_compact_prior_context(tmp_path):
    """Requirements 4.1, 4.2: when the synthesis chunk owns its own exclusive
    fields (e.g. domain 13 "reviewer assessment" in the real 5-chunk config),
    synthesis still runs, and the prior_context it receives is a compact
    (value/confidence only) summary -- never the full evidence text or
    location metadata that reconstruct_fields() would normally attach.
    """
    pdf_name = "paper_synth"
    chunk_fields = {
        1: [{"field_index": 3, "domain_group": "2. Clinical context", "field_name": "Study design"}],
        2: [{"field_index": 4, "domain_group": "2. Clinical context", "field_name": "Sample size"}],
        3: [{"field_index": 5, "domain_group": "13. Reviewer assessment", "field_name": "Synthesis notes"}],
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
        5: {"domain_group": 13, "field_name": "Synthesis notes"},
    }
    bundle = _make_fake_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "Randomised controlled trial evidence text here."},
        "ev-2": {"id": "ev-2", "type": "sentence", "text": "One hundred twenty participants were enrolled."},
    })
    openai_config = _base_openai_config(num_chunks=3)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        elif chunk_num == 2:
            return json.dumps({"extractions": [{"i": 4, "v": "120", "loc": ["ev-2"], "c": "m"}]})
        elif chunk_num == 3:
            return json.dumps({"extractions": [{"i": 5, "v": "Solid paper", "loc": [], "c": "h"}]})
        return json.dumps({"extractions": []})

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    result = _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path)

    assert mock_api.extract_chunk.call_count == 3  # synthesis NOT skipped (owns field 5)

    synthesis_call = mock_api.extract_chunk.call_args_list[2]
    assert synthesis_call.args[0] == 3

    prior_context = synthesis_call.kwargs["prior_context"]
    by_index = {entry["field_index"]: entry for entry in prior_context}
    assert set(by_index.keys()) == {3, 4}
    for entry in by_index.values():
        # Compact: value-only summary, no evidence text / location metadata.
        assert set(entry.keys()) == {"field_index", "field_name", "value", "confidence"}
    assert by_index[3]["value"] == "RCT"
    assert by_index[4]["value"] == "120"

    # Full evidence text must NOT leak into the synthesis prior_context.
    prior_context_json = json.dumps(prior_context)
    assert "Randomised controlled trial evidence text" not in prior_context_json
    assert "One hundred twenty participants" not in prior_context_json

    # The FINAL saved output still carries full evidence/location metadata.
    assert result is not None
    final_by_index = {f["field_index"]: f for f in result}
    assert final_by_index[3]["evidence"] == "Randomised controlled trial evidence text here."
    assert final_by_index[5]["extracted_value"] == "Solid paper"


def test_process_pdf_skips_synthesis_when_no_conflicts_and_no_exclusive_fields(tmp_path):
    """Requirement 5.6: when Deterministic_Merge finds no conflicts and the
    synthesis chunk owns no exclusive fields of its own, synthesis is
    skipped entirely -- no LLM call is made for the synthesis chunk."""
    pdf_name = "paper_skip"
    chunk_fields = {
        1: [{"field_index": 3, "domain_group": "2. X", "field_name": "Study design"}],
        2: [{"field_index": 4, "domain_group": "2. X", "field_name": "Sample size"}],
        # chunk 3 (synthesis) intentionally owns no fields.
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
    }
    bundle = _make_fake_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "evidence 1"},
        "ev-2": {"id": "ev-2", "type": "sentence", "text": "evidence 2"},
    })
    openai_config = _base_openai_config(num_chunks=3)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        elif chunk_num == 2:
            return json.dumps({"extractions": [{"i": 4, "v": "120", "loc": ["ev-2"], "c": "m"}]})
        raise AssertionError(f"extract_chunk should not be called for chunk {chunk_num}")

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    result = _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path)

    assert mock_api.extract_chunk.call_count == 2  # synthesis skipped
    assert result is not None
    final_by_index = {f["field_index"]: f for f in result}
    assert final_by_index[3]["extracted_value"] == "RCT"
    assert final_by_index[4]["extracted_value"] == "120"


def test_process_pdf_conflict_forces_synthesis_even_without_exclusive_fields(tmp_path):
    """A genuine cross-chunk conflict for the same field_index still
    requires an LLM synthesis call to adjudicate, even when the synthesis
    chunk itself owns no exclusive fields -- proving synthesis is not
    skipped merely because chunk_fields_for_llm[synthesis_chunk] is empty.

    (This pipeline's real domain-to-chunk partitioning never assigns a
    field_index to more than one chunk, so this scenario is deliberately
    atypical -- but deterministic_merge.py and this integration make no such
    assumption, and must resolve a genuine conflict correctly if one ever
    occurs, e.g. under a future domain_to_chunk reconfiguration.)
    """
    pdf_name = "paper_conflict"
    conflict_field_def = {"field_index": 3, "domain_group": "2. X", "field_name": "Study design"}
    chunk_fields = {
        1: [conflict_field_def],
        2: [conflict_field_def],
        # chunk 3 (synthesis) owns no fields.
    }
    field_lookup = {3: {"domain_group": 2, "field_name": "Study design"}}
    bundle = _make_fake_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "chunk one evidence text"},
        "ev-2": {"id": "ev-2", "type": "sentence", "text": "chunk two evidence text"},
    })
    openai_config = _base_openai_config(num_chunks=3)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        elif chunk_num == 2:
            return json.dumps({"extractions": [{"i": 3, "v": "Cohort study", "loc": ["ev-2"], "c": "m"}]})
        elif chunk_num == 3:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        return json.dumps({"extractions": []})

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    result = _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path)

    # Synthesis MUST run to adjudicate the conflict.
    assert mock_api.extract_chunk.call_count == 3
    synthesis_call = mock_api.extract_chunk.call_args_list[2]
    assert synthesis_call.args[0] == 3

    # The synthesis dispatch's own field list (3rd positional arg) must
    # include field_index 3 so the model knows to resolve it.
    synthesis_fields_arg = synthesis_call.args[2]
    assert any(f["field_index"] == 3 for f in synthesis_fields_arg)

    # The compact prior_context marks field 3 as a conflict, with candidate
    # records for both disagreeing chunks (Req 4.2, 4.4).
    prior_context = synthesis_call.kwargs["prior_context"]
    conflict_entries = [e for e in prior_context if e.get("conflict")]
    assert len(conflict_entries) == 1
    assert conflict_entries[0]["field_index"] == 3
    candidates = conflict_entries[0]["candidates"]
    assert len(candidates) == 2
    assert {c["value"] for c in candidates} == {"RCT", "Cohort study"}
    for c in candidates:
        assert set(c.keys()) == {
            "field_index", "field_name", "value", "confidence", "evidence_ids", "snippet",
        }

    assert result is not None
    final_by_index = {f["field_index"]: f for f in result}
    assert final_by_index[3]["extracted_value"] == "RCT"  # synthesis's adjudicated value


# ---------------------------------------------------------------------------
# Compact candidate record helpers (Requirements 4.2, 4.7; Properties 12, 13)
# ---------------------------------------------------------------------------


def test_truncate_snippet_within_limit_unchanged():
    text = "short evidence text"
    assert _pdf_processor._truncate_snippet(text) == text


def test_truncate_snippet_truncates_at_word_boundary():
    text = "word " * 100  # 500 chars, well over the 200-char limit
    snippet = _pdf_processor._truncate_snippet(text)
    assert len(snippet) <= 200
    assert not snippet.endswith(" wor")  # not mid-word cut
    assert text.startswith(snippet.rstrip())


def test_truncate_snippet_hard_cuts_when_no_word_boundary():
    text = "x" * 500
    snippet = _pdf_processor._truncate_snippet(text)
    assert len(snippet) == 200


def test_build_conflict_candidate_records_caps_at_five_highest_confidence():
    """Requirement 4.7 / Property 12: candidates are capped at 5, keeping the
    highest-confidence ones."""
    candidates = [
        {"i": 9, "v": "low1", "loc": [], "c": "l"},
        {"i": 9, "v": "high1", "loc": [], "c": "h"},
        {"i": 9, "v": "med1", "loc": [], "c": "m"},
        {"i": 9, "v": "high2", "loc": [], "c": "h"},
        {"i": 9, "v": "nr1", "loc": [], "c": "nr"},
        {"i": 9, "v": "high3", "loc": [], "c": "h"},
        {"i": 9, "v": "low2", "loc": [], "c": "l"},
    ]
    field_lookup = {9: {"domain_group": 2, "field_name": "Some field"}}
    records = _pdf_processor._build_conflict_candidate_records(9, candidates, field_lookup, {})

    assert len(records) == 5
    values = [r["value"] for r in records]
    # The 3 "h" and the 1 "m" candidates plus the highest remaining ("l")
    # must be kept -- "nr1" (rank 0) and the second "l" wouldn't fit if a
    # higher-ranked candidate existed, but here there are exactly 3 "h" + 1
    # "m" + 2 "l"s + 1 "nr" = 7 total, so only one "l" survives the cap.
    assert values.count("high1") == 1 and values.count("high2") == 1 and values.count("high3") == 1
    assert "med1" in values
    assert "nr1" not in values


def test_process_pdf_normalizes_whitespace_via_deterministic_merge(tmp_path):
    """Regression test (token-efficient-extraction task 8.2 review fix,
    rejection finding 1).

    process_pdf() now calls deterministic_merge() UNCONDITIONALLY for every
    extraction-chunk result. deterministic_merge.py's single-contributor /
    all-agree merge path uses the NORMALIZED value (whitespace-stripped,
    internal runs collapsed to a single space -- see
    deterministic_merge.normalize_value()) as the canonical value, an
    already-reviewed design decision (task 2.1) made for order-independence
    (Property 8). This is INTENTIONAL, tested behavior, not a silent
    regression: a chunk value with non-canonical whitespace
    ("  Randomized   controlled  trial  ") is persisted in the FINAL
    process_pdf() output as its whitespace-normalized form
    ("Randomized controlled trial") -- contrasted below with
    reconstruct_fields() called directly on the same raw value (the OLD
    code path, bypassing deterministic_merge), which does NOT normalize.

    **Validates: Requirements 5.1, 5.5, 5.7**
    """
    pdf_name = "paper_whitespace"
    raw_value = "  Randomized   controlled  trial  "
    chunk_fields = {
        1: [{"field_index": 3, "domain_group": "2. X", "field_name": "Study design"}],
        # chunk 2 (synthesis) intentionally owns no fields -> synthesis skipped.
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
    }
    bundle = _make_fake_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "evidence 1"},
    })
    openai_config = _base_openai_config(num_chunks=2)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps(
                {"extractions": [{"i": 3, "v": raw_value, "loc": ["ev-1"], "c": "h"}]}
            )
        raise AssertionError(f"extract_chunk should not be called for chunk {chunk_num}")

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    result = _run_process_pdf(
        pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path
    )

    assert result is not None
    final_by_index = {f["field_index"]: f for f in result}

    # Contrast: reconstruct_fields() called directly on the raw compact dict
    # (the OLD code path, bypassing deterministic_merge) does NOT normalize.
    unnormalized = _pdf_processor.reconstruct_fields(
        [{"i": 3, "v": raw_value, "loc": ["ev-1"], "c": "h"}],
        field_lookup,
        bundle.evidence_map,
    )
    assert unnormalized[0]["extracted_value"] == raw_value

    # process_pdf()'s actual, merged, FINAL persisted output is
    # whitespace-normalized -- pinned as intentional behavior.
    assert final_by_index[3]["extracted_value"] == "Randomized controlled trial"


def test_gather_field_candidates_collects_matching_entries_across_chunks():
    validated_results = [
        [{"i": 3, "v": "A", "loc": [], "c": "h"}, {"i": 4, "v": "X", "loc": [], "c": "h"}],
        [{"i": 3, "v": "B", "loc": [], "c": "m"}],
    ]
    candidates = _pdf_processor._gather_field_candidates(3, [1, 2], validated_results)
    assert [c["v"] for c in candidates] == ["A", "B"]
