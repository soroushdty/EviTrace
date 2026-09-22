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
        ok, failure = _save_pdf_output(pdf_name, fields)

    assert (ok, failure) == (True, None)
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
            ok, failure = _save_pdf_output("pbt_paper", fields)

        assert (ok, failure) == (True, None)
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
    # Task 10.3 / design "FailureRecorder": a non-repair exception still
    # yields one failure record per failed chunk (error_type = class name,
    # attempts unknown -> 0) beside the compatibility int list.
    assert manifest[pdf_name]["failed_chunks"] == [2]
    assert manifest[pdf_name]["error"] == "API error on chunk 2"
    assert manifest[pdf_name]["failures"] == [{
        "stage": "extraction_chunk",
        "chunk": 2,
        "error_type": "RuntimeError",
        "last_error": "API error on chunk 2",
        "attempts": 0,
    }]


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
        "max_evidence_items_per_chunk": 150,
        "max_evidence_chars_per_chunk": 30000,
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
        "max_evidence_items_per_chunk": 150,
        "max_evidence_chars_per_chunk": 30000,
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


# ---------------------------------------------------------------------------
# process_pdf: per-paper evidence coverage record in the manifest
# (feature: risk-remediation, task 9.2; design.md "EvidenceCoverage", D8)
# Requirements: 10.4
# ---------------------------------------------------------------------------

import logging

_COVERAGE_KEYS = {"ratio", "selected_chars", "substantive_chars", "below_threshold"}


def _coverage_item(item_id, text, *, section="Methods", score=10):
    return {
        "id": item_id,
        "type": "sentence",
        "section_path": section,
        "page": 1,
        "coords": None,
        "score": score,
        "text": text,
        "annotations": {},
    }


def _coverage_bundle(items):
    """Duck-typed bundle whose ``evidence_items`` drive the real ranker."""
    return types.SimpleNamespace(
        paper_id="paper_cov",
        evidence_items=items,
        evidence_map={item["id"]: item for item in items},
        prefilled_fields={},
    )


def _coverage_fields():
    """Two chunks of LLM fields (chunk 3 = synthesis owns none)."""
    chunk_fields = {
        1: [{"field_index": 3, "domain_group": "2. X", "field_name": "Study design",
             "definition": "randomised trial design", "reviewer_question": ""}],
        2: [{"field_index": 4, "domain_group": "2. X", "field_name": "Sample size",
             "definition": "participants enrolled", "reviewer_question": ""}],
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
    }
    return chunk_fields, field_lookup


def _coverage_api():
    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-a"], "c": "h"}]})
        if chunk_num == 2:
            return json.dumps({"extractions": [{"i": 4, "v": "120", "loc": ["ev-b"], "c": "m"}]})
        raise AssertionError(f"extract_chunk should not be called for chunk {chunk_num}")

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()
    return mock_api


def _run_coverage_case(tmp_path, *, max_chars, threshold, caplog=None):
    """Three substantive items (50 + 50 + 52 chars) plus one 29-char Metadata item.

    ``substantive_chars`` is therefore 152 regardless of caps (the Metadata
    item is excluded from the denominator but, scoring 0, is ranked last and
    only ever selected when the cap leaves room for it); ``max_chars`` decides
    how many of the three substantive items the ranker can afford. Field
    keywords (randomised/trial/design/participants/enrolled) lift ev-a and
    ev-b above ev-c so the selection order is ev-a, ev-b, ev-c, ev-m.
    """
    items = [
        _coverage_item("ev-a", "Randomised trial design with participants enrolled".ljust(50, "."), score=10),
        _coverage_item("ev-b", "Participants enrolled in the randomised trial".ljust(50, "."), score=10),
        _coverage_item("ev-c", "Unrelated filler sentence that scores lower overall".ljust(52, "."), score=1),
        _coverage_item("ev-m", "Title of the paper (metadata)", section="Metadata", score=0),
    ]
    assert [len(i["text"]) for i in items] == [50, 50, 52, 29]
    bundle = _coverage_bundle(items)
    chunk_fields, field_lookup = _coverage_fields()
    openai_config = _base_openai_config(num_chunks=3)
    openai_config["max_evidence_items_per_chunk"] = 150
    openai_config["max_evidence_chars_per_chunk"] = max_chars
    openai_config["min_evidence_coverage_ratio"] = threshold
    mock_api = _coverage_api()
    manifest: dict = {}
    qc_context = _make_qc_context("paper_cov")

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "validate_qc_context_input"), \
         patch.object(_pdf_processor, "build_or_load_evidence_bundle", return_value=bundle):

        async def _run():
            return await _pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=chunk_fields,
                field_lookup=field_lookup,
                api_semaphore=asyncio.Semaphore(5),
                manifest=manifest,
                manifest_lock=asyncio.Lock(),
                openai_config=openai_config,
            )

        if caplog is not None:
            with caplog.at_level(logging.INFO, logger=_pdf_processor.logger.name):
                result = asyncio.run(_run())
        else:
            result = asyncio.run(_run())

    return result, manifest, mock_api, bundle, openai_config, chunk_fields


def test_process_pdf_completion_records_full_evidence_coverage(tmp_path, caplog):
    """Requirement 10.4 / design "EvidenceCoverage": a completed paper's
    manifest entry carries ``evidence_coverage`` with the measured values.
    A 152-char cap admits exactly the three substantive items (the 29-char
    Metadata item would overflow and is skipped): 152/152."""
    result, manifest, *_ = _run_coverage_case(
        tmp_path, max_chars=152, threshold=0.6, caplog=caplog
    )

    assert result is not None
    entry = manifest["paper_cov"]
    assert entry["status"] == "complete"
    assert set(entry["evidence_coverage"].keys()) == _COVERAGE_KEYS
    cov = entry["evidence_coverage"]
    assert cov["substantive_chars"] == 152          # 50 + 50 + 52; Metadata excluded
    assert cov["selected_chars"] == 152
    assert cov["ratio"] == 1.0
    assert cov["below_threshold"] is False
    assert isinstance(cov["selected_chars"], int)
    assert isinstance(cov["substantive_chars"], int)
    assert isinstance(cov["ratio"], float)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "coverage" in r.getMessage().lower()]
    assert warnings == []
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "Evidence coverage" in r.getMessage()]
    assert infos, "an INFO line reporting the coverage ratio is expected"
    assert "paper_cov" in infos[0]


def test_process_pdf_below_threshold_warns_and_flags(tmp_path, caplog):
    """Requirement 10.4: when the ratio falls below
    ``min_evidence_coverage_ratio`` the paper is flagged and a WARNING naming
    the paper, the ratio and the threshold is emitted. A 100-char cap admits
    the two top-scored 50-char items only: 100/152 = 0.6579 < 0.7."""
    result, manifest, *_ = _run_coverage_case(
        tmp_path, max_chars=100, threshold=0.7, caplog=caplog
    )

    assert result is not None
    cov = manifest["paper_cov"]["evidence_coverage"]
    assert cov["substantive_chars"] == 152
    assert cov["selected_chars"] == 100
    assert cov["ratio"] == round(100 / 152, 4)
    assert cov["below_threshold"] is True

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "coverage" in r.getMessage().lower()]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "paper_cov" in message
    assert "0.6579" in message
    assert "0.7" in message


def test_process_pdf_above_threshold_no_warning(tmp_path, caplog):
    """Same 100/152 selection but with the default 0.6 threshold: no
    WARNING and ``below_threshold`` is False."""
    result, manifest, *_ = _run_coverage_case(
        tmp_path, max_chars=100, threshold=0.6, caplog=caplog
    )

    assert result is not None
    cov = manifest["paper_cov"]["evidence_coverage"]
    assert cov["ratio"] == round(100 / 152, 4)
    assert cov["below_threshold"] is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "coverage" in r.getMessage().lower()]
    assert warnings == []


def test_process_pdf_evidence_package_bytes_unchanged_by_coverage_measurement(tmp_path):
    """Measuring coverage must not alter the shared paper package: the source
    string handed to every ``extract_chunk`` call is byte-identical to what
    ``build_paper_evidence_package`` produces for the same inputs (prompt
    cache stability)."""
    _, _, mock_api, bundle, openai_config, chunk_fields = _run_coverage_case(
        tmp_path, max_chars=100, threshold=0.6
    )
    evidence_index = importlib.import_module("pipeline.evidence_index")
    all_llm_fields = [f for fields in chunk_fields.values() for f in fields]
    expected = evidence_index.build_paper_evidence_package(
        bundle,
        all_llm_fields,
        max_items=openai_config["max_evidence_items_per_chunk"],
        max_chars=openai_config["max_evidence_chars_per_chunk"],
    )
    assert mock_api.extract_chunk.call_count == 2
    for call in mock_api.extract_chunk.call_args_list:
        assert call.args[1] == expected
    assert '"evidence_count":2' in expected.replace(" ", "")


def test_completed_entry_with_evidence_coverage_is_still_read_as_complete(tmp_path):
    """Design "Migration Strategy": ``_load_completed_result`` reads only
    ``status`` and ``is_stale`` reads only identity fields -- the added
    ``evidence_coverage`` key changes neither verdict."""
    pdf_name = "paper_cov_cached"
    fields = [{"field_index": 1, "extracted_value": "Smith", "confidence": "h"}]
    (tmp_path / f"{pdf_name}.extracted.json").write_text(json.dumps(fields), encoding="utf-8")
    entry = {
        "status": "complete",
        "evidence_coverage": {
            "ratio": 0.5, "selected_chars": 5, "substantive_chars": 10, "below_threshold": True,
        },
    }
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        assert _load_completed_result(pdf_name, {pdf_name: entry}) == fields

    manifest_mod = importlib.import_module("pipeline.manifest")
    identity = manifest_mod.ManifestIdentity(
        pdf_content_hash="a" * 64,
        config_hash="c" * 64,
        extraction_map_hash="b" * 64,
        model_id="gpt-test",
        schema_version="1",
        output_path=f"{pdf_name}.extracted.json",
    )
    with_identity = {**entry, **identity.to_dict()}
    assert manifest_mod.is_stale(with_identity, identity) is False
    assert manifest_mod.is_stale({**with_identity, "evidence_coverage": None}, identity) is False


# ---------------------------------------------------------------------------
# Task 9.3: the manifest ``evidence_coverage`` record shape (design.md
# "EvidenceCoverage" / "Logical Data Model"; Requirement 10.4) and its
# invisibility to manifest validity checks.
# ---------------------------------------------------------------------------

_COVERAGE_RECORD_TYPES = {
    "ratio": float,
    "selected_chars": int,
    "substantive_chars": int,
    "below_threshold": bool,
}


def _assert_coverage_record_shape(cov):
    assert set(cov) == set(_COVERAGE_RECORD_TYPES)
    for key, expected_type in _COVERAGE_RECORD_TYPES.items():
        # ``type(...) is`` on purpose: bool is a subclass of int, so isinstance
        # would let a bool through for the int fields (and vice versa).
        assert type(cov[key]) is expected_type, (key, cov[key], type(cov[key]))
    assert cov["ratio"] >= 0.0
    assert cov["selected_chars"] >= 0
    assert cov["substantive_chars"] >= 0


def test_evidence_coverage_record_has_exactly_the_documented_shape(tmp_path):
    """The manifest entry's ``evidence_coverage`` is exactly
    ``{ratio: float, selected_chars: int, substantive_chars: int, below_threshold: bool}``.
    Both a capped and an under-covered run are checked."""
    _, full_manifest, *_ = _run_coverage_case(tmp_path, max_chars=152, threshold=0.6)
    _assert_coverage_record_shape(full_manifest["paper_cov"]["evidence_coverage"])

    _, short_manifest, *_ = _run_coverage_case(tmp_path, max_chars=100, threshold=0.7)
    _assert_coverage_record_shape(short_manifest["paper_cov"]["evidence_coverage"])


def test_build_evidence_coverage_record_shape_from_stats():
    """Same shape contract at the unit level, including numpy-free plain
    ints even when the stats carry bools/ints that could be coerced."""
    evidence_index = importlib.import_module("pipeline.evidence_index")
    stats = evidence_index.EvidenceSelectionStats(
        total_items=5, substantive_chars=1000, selected_items=3, selected_chars=333,
    )
    record = _pdf_processor._build_evidence_coverage_record("paper_x", stats, 0.6)
    _assert_coverage_record_shape(record)
    assert record == {
        "ratio": 0.333, "selected_chars": 333, "substantive_chars": 1000, "below_threshold": True,
    }


def test_evidence_coverage_ratio_may_marginally_exceed_one_when_metadata_selected(tmp_path):
    """Documented (configs/README.md), not clamped: ``selected_chars`` counts a
    selected Metadata item while ``substantive_chars`` excludes it by
    definition. With a cap wide enough for all four fixture items the ratio is
    (50+50+52+29)/152 > 1.0 and ``below_threshold`` is False."""
    _, manifest, *_ = _run_coverage_case(tmp_path, max_chars=1000, threshold=0.6)
    cov = manifest["paper_cov"]["evidence_coverage"]
    _assert_coverage_record_shape(cov)
    assert cov["substantive_chars"] == 152
    assert cov["selected_chars"] == 181
    assert cov["ratio"] == round(181 / 152, 4)
    assert cov["ratio"] > 1.0
    assert cov["below_threshold"] is False


def test_is_output_valid_is_insensitive_to_evidence_coverage(tmp_path):
    """``manifest._is_output_valid`` reads only ``output_path`` (resolved
    against ``output_dir``) and the file's JSON validity; adding, nulling or
    removing ``evidence_coverage`` never changes its verdict."""
    manifest_mod = importlib.import_module("pipeline.manifest")
    coverage = {"ratio": 0.5, "selected_chars": 5, "substantive_chars": 10, "below_threshold": True}

    valid_name = "paper_valid.extracted.json"
    (tmp_path / valid_name).write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")
    corrupt_name = "paper_corrupt.extracted.json"
    (tmp_path / corrupt_name).write_text("{not json", encoding="utf-8")
    missing_name = "paper_missing.extracted.json"

    for output_path, expected in ((valid_name, True), (corrupt_name, False), (missing_name, False)):
        base = {"status": "complete", "output_path": output_path}
        variants = (
            base,
            {**base, "evidence_coverage": coverage},
            {**base, "evidence_coverage": None},
            {**base, "evidence_coverage": {}},
        )
        verdicts = {manifest_mod._is_output_valid(entry, output_dir=tmp_path) for entry in variants}
        assert verdicts == {expected}, (output_path, verdicts)

    # No output_path at all is invalid regardless of the coverage record.
    assert manifest_mod._is_output_valid({"status": "complete", "evidence_coverage": coverage}, output_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# process_pdf: synthesis stage runs through RepairRetryLoop
# (feature: risk-remediation, task 10.2; design.md "SynthesisRepair", D4)
# Requirements: 5.1, 5.2, 5.4
# ---------------------------------------------------------------------------

_SYNTH_CHUNK_FIELDS = {
    1: [{"field_index": 3, "domain_group": "2. Clinical context", "field_name": "Study design"}],
    2: [{"field_index": 4, "domain_group": "2. Clinical context", "field_name": "Sample size"}],
    3: [{"field_index": 5, "domain_group": "13. Reviewer assessment", "field_name": "Synthesis notes"}],
}
_SYNTH_FIELD_LOOKUP = {
    3: {"domain_group": 2, "field_name": "Study design"},
    4: {"domain_group": 2, "field_name": "Sample size"},
    5: {"domain_group": 13, "field_name": "Synthesis notes"},
}


def _synth_bundle():
    return _make_fake_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "Randomised controlled trial evidence."},
        "ev-2": {"id": "ev-2", "type": "sentence", "text": "One hundred twenty participants."},
    })


def _synth_api(synthesis_responses):
    """extract_chunk mock: chunks 1/2 always valid; chunk 3 (synthesis) pops
    from ``synthesis_responses`` in order (the last entry repeats)."""
    remaining = list(synthesis_responses)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        if chunk_num == 2:
            return json.dumps({"extractions": [{"i": 4, "v": "120", "loc": ["ev-2"], "c": "m"}]})
        if chunk_num == 3:
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]
        raise AssertionError(f"unexpected chunk {chunk_num}")

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()
    return mock_api


def _run_synth_case(tmp_path, mock_api, *, max_repair_attempts=None,
                    save_manifest=None, extra_patches=(), field_lookup=None):
    """Run process_pdf with a real manifest dict; save_manifest is stubbed so
    the repo's manifest file is never touched.

    ``save_manifest`` may be a callable used as the stub's side effect (it
    receives the manifest dict); ``extra_patches`` are entered after the
    standard ones. The lock handed to process_pdf is created inside the
    event loop and exposed on ``_run_synth_case.last_lock`` so a
    ``save_manifest`` side effect can assert it is held. ``field_lookup``
    defaults to ``_SYNTH_FIELD_LOOKUP``; task 10.4 passes a lookup carrying
    an invalid ``domain_group`` to drive the real validator's negative path.
    """
    if field_lookup is None:
        field_lookup = _SYNTH_FIELD_LOOKUP
    openai_config = _base_openai_config(num_chunks=3)
    if max_repair_attempts is not None:
        openai_config["max_repair_attempts"] = max_repair_attempts
    manifest: dict = {}
    qc_context = _make_qc_context("paper_synth_repair")

    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path))
        stack.enter_context(patch.dict(sys.modules, {"agents.openai.api_client": mock_api}))
        stack.enter_context(patch.object(_pdf_processor, "validate_qc_context_input"))
        stack.enter_context(patch.object(_pdf_processor, "save_manifest", side_effect=save_manifest))
        stack.enter_context(patch.object(
            _pdf_processor, "build_or_load_evidence_bundle", return_value=_synth_bundle(),
        ))
        for extra in extra_patches:
            stack.enter_context(extra)

        async def _run():
            _run_synth_case.last_lock = asyncio.Lock()
            return await _pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=_SYNTH_CHUNK_FIELDS,
                field_lookup=field_lookup,
                api_semaphore=asyncio.Semaphore(5),
                manifest=manifest,
                manifest_lock=_run_synth_case.last_lock,
                openai_config=openai_config,
            )

        result = asyncio.run(_run())
    return result, manifest


_VALID_SYNTH = json.dumps({"extractions": [{"i": 5, "v": "Repaired verdict", "loc": [], "c": "h"}]})


def test_process_pdf_synthesis_malformed_then_valid_is_repaired_and_merged(tmp_path):
    """Requirements 5.1, 5.2: a synthesis response that fails parsing is
    repaired through the same RepairRetryLoop as extraction chunks, and the
    repaired response is what lands in the output. The initial synthesis call
    carries stage="synthesis" and the compact prior context; the repair call
    carries a repair_prompt, stage="validation_repair", and the SAME prior
    context (design.md "SynthesisRepair": prior context on every call)."""
    mock_api = _synth_api(["this is not json {", _VALID_SYNTH])

    result, manifest = _run_synth_case(tmp_path, mock_api)

    calls = mock_api.extract_chunk.call_args_list
    assert len(calls) == 4, [c.args[0] for c in calls]
    # Synthesis remains the third model call (existing helper tests rely on it).
    initial = calls[2]
    assert initial.args[0] == 3
    assert initial.kwargs["stage"] == "synthesis"
    assert "repair_prompt" not in initial.kwargs
    prior_context = initial.kwargs["prior_context"]
    assert {e["field_index"] for e in prior_context} == {3, 4}

    repair = calls[3]
    assert repair.args[0] == 3
    assert repair.kwargs["stage"] == "validation_repair"
    assert repair.kwargs["repair_prompt"]
    assert repair.kwargs["repair_attempt"] == 1
    assert repair.kwargs["prior_context"] == prior_context

    assert result is not None
    by_index = {f["field_index"]: f for f in result}
    assert by_index[5]["extracted_value"] == "Repaired verdict"
    assert by_index[3]["extracted_value"] == "RCT"
    assert manifest["paper_synth_repair"]["status"] == "complete"


def test_process_pdf_synthesis_repair_limit_comes_from_config(tmp_path):
    """Requirement 5.4: ``max_repair_attempts`` from the loaded config reaches
    the loop -- with 3 configured, a persistently malformed synthesis response
    gets exactly three repair attempts, and exhaustion lands in the
    ``failed_chunk_{n}`` manifest entry carrying a synthesis failure record
    (Requirement 5.3)."""
    mock_api = _synth_api(["still not json"])

    result, manifest = _run_synth_case(tmp_path, mock_api, max_repair_attempts=3)

    assert result is None
    synth_calls = [c for c in mock_api.extract_chunk.call_args_list if c.args[0] == 3]
    assert len(synth_calls) == 4  # 1 initial + 3 repairs
    assert [c.kwargs.get("repair_attempt") for c in synth_calls] == [None, 1, 2, 3]
    assert all(c.kwargs["stage"] == "validation_repair" for c in synth_calls[1:])
    # Task 10.3 / design "FailureRecorder": exhaustion lands in the single
    # failure-record shape; ``error`` mirrors the record's ``last_error``.
    entry = manifest["paper_synth_repair"]
    assert set(entry) == {"status", "error", "failures"}
    assert entry["status"] == "failed_chunk_3"
    assert entry["failures"] == [{
        "stage": "synthesis",
        "chunk": 3,
        "error_type": "parse",
        "last_error": entry["error"],
        "attempts": 3,
    }]
    assert entry["error"].startswith("JSON parse failed")


def test_process_pdf_synthesis_repair_limit_defaults_to_two(tmp_path):
    """Requirement 5.4: without the key in openai_config the loop falls back
    to the documented default of 2 (matching ``retry.max_repair_attempts``)."""
    mock_api = _synth_api(["still not json"])

    result, manifest = _run_synth_case(tmp_path, mock_api)

    assert result is None
    synth_calls = [c for c in mock_api.extract_chunk.call_args_list if c.args[0] == 3]
    assert len(synth_calls) == 3  # 1 initial + 2 repairs
    assert manifest["paper_synth_repair"]["status"] == "failed_chunk_3"
    assert manifest["paper_synth_repair"]["failures"][0]["stage"] == "synthesis"
    assert manifest["paper_synth_repair"]["failures"][0]["attempts"] == 2


def test_process_pdf_synthesis_budget_checked_once_per_call_no_double_mitigation(tmp_path):
    """design.md "SynthesisRepair" risk note: the repair loop performs the
    synthesis budget check itself (with the prior-context JSON and the
    protected ids), so the old standalone check must be gone -- exactly one
    stage="synthesis" check per paper, and one "validation_repair" check per
    repair attempt for the synthesis chunk."""
    mock_api = _synth_api(["not json", _VALID_SYNTH])
    real_check = _pdf_processor._check_and_mitigate_budget

    with patch.object(_pdf_processor, "_check_and_mitigate_budget", wraps=real_check) as spy:
        result, _ = _run_synth_case(tmp_path, mock_api)

    assert result is not None
    synth_calls = [c for c in spy.call_args_list if c.kwargs["chunk_num"] == 3]
    synthesis_stage = [c for c in synth_calls if c.kwargs["stage"] == "synthesis"]
    repair_stage = [c for c in synth_calls if c.kwargs["stage"] == "validation_repair"]
    assert len(synthesis_stage) == 1
    assert len(repair_stage) == 1
    assert {c.kwargs["stage"] for c in synth_calls} == {"synthesis", "validation_repair"}

    prior_context = mock_api.extract_chunk.call_args_list[2].kwargs["prior_context"]
    assert synthesis_stage[0].kwargs["prior_context_text"] == json.dumps(prior_context)
    assert synthesis_stage[0].kwargs["protected_evidence_ids"] == {"ev-1"}  # field 3 is "h"


def test_process_pdf_shares_one_repair_loop_between_chunks_and_synthesis(tmp_path):
    """design.md "SynthesisRepair": ``RepairRetryLoop`` is constructed once per
    paper (with the configured limit) and the chunk stage receives that same
    instance rather than building its own."""
    mock_api = _synth_api([_VALID_SYNTH])
    real_cls = _pdf_processor.RepairRetryLoop
    instances: list = []

    class _Spy(real_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

    with patch.object(_pdf_processor, "RepairRetryLoop", _Spy):
        result, _ = _run_synth_case(tmp_path, mock_api, max_repair_attempts=3)

    assert result is not None
    assert len(instances) == 1
    assert instances[0].max_repair_attempts == 3


# ---------------------------------------------------------------------------
# One manifest failure shape for chunks, synthesis, and the output write
# (feature: risk-remediation, task 10.3; design.md "FailureRecorder",
# "Logical Data Model")
# Requirements: 1.3, 1.4, 5.3, 5.4
# ---------------------------------------------------------------------------

import inspect

import pytest

_FAILURE_RECORD_KEYS = {"stage", "chunk", "error_type", "last_error", "attempts"}


def _record(stage, chunk, error_type="parse", last_error="boom", attempts=2):
    return {
        "stage": stage, "chunk": chunk, "error_type": error_type,
        "last_error": last_error, "attempts": attempts,
    }


def test_record_failure_failed_chunks_shape_carries_records_and_compat_list():
    """design "FailureRecorder": ``failed_chunks`` writes status, the last
    record's error, the records, and the compatibility int list -- and
    nothing else."""
    manifest = {"paper": {"status": "pending"}}
    failures = [
        _record("extraction_chunk", 1, "parse", "first"),
        _record("extraction_chunk", 2, "schema", "second", attempts=3),
    ]

    with patch.object(_pdf_processor, "save_manifest") as save:
        _pdf_processor._record_failure(
            manifest, "paper", status="failed_chunks", failures=failures,
        )

    entry = manifest["paper"]
    assert set(entry) == {"status", "error", "failures", "failed_chunks"}
    assert entry["status"] == "failed_chunks"
    assert entry["error"] == "second"
    assert entry["failures"] == failures
    assert entry["failed_chunks"] == [1, 2]
    save.assert_called_once_with(manifest)


@pytest.mark.parametrize(
    "status, stage, chunk",
    [
        ("failed_chunk_3", "synthesis", 3),
        ("failed_schema_validation", "schema_validation", None),
        ("failed_output_write", "output_write", None),
    ],
)
def test_record_failure_non_chunks_statuses_have_exact_three_keys(status, stage, chunk):
    """design "FailureRecorder": every other status writes exactly
    ``status`` / ``error`` / ``failures`` -- no ``failed_chunks`` list."""
    manifest = {}
    record = _record(stage, chunk, "OSError", "disk full", attempts=1)

    with patch.object(_pdf_processor, "save_manifest") as save:
        _pdf_processor._record_failure(
            manifest, "paper", status=status, failures=[record],
        )

    entry = manifest["paper"]
    assert set(entry) == {"status", "error", "failures"}
    assert entry["status"] == status
    assert entry["error"] == "disk full"
    assert entry["failures"] == [record]
    assert set(entry["failures"][0]) == _FAILURE_RECORD_KEYS
    save.assert_called_once_with(manifest)


def _valid_fields():
    return [{
        "field_index": 3,
        "domain_group": 2,
        "field_name": "Study design",
        "extracted_value": "RCT",
        "evidence": "randomised controlled trial",
        "location": ["ev-001"],
        "location_metadata": [],
        "confidence": "h",
    }]


def test_save_pdf_output_has_no_manifest_parameter():
    """design "FailureRecorder": the ``manifest`` parameter is removed; the
    gate is ``(pdf_name, fields, normalizer=None)``."""
    params = list(inspect.signature(_save_pdf_output).parameters)
    assert params == ["pdf_name", "fields", "normalizer"]


def test_save_pdf_output_valid_returns_true_none_and_never_touches_manifest(tmp_path):
    """Requirement 1.2 / design "FailureRecorder": success is ``(True, None)``
    and the gate never persists the manifest."""
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.object(_pdf_processor, "save_manifest") as save:
        result = _save_pdf_output("paper_ok", _valid_fields())

    assert result == (True, None)
    assert (tmp_path / "paper_ok.extracted.json").exists()
    save.assert_not_called()


def test_save_pdf_output_invalid_returns_failure_record_and_never_touches_manifest(tmp_path, caplog):
    """Requirements 1.3, 1.4 / design "FailureRecorder": a validation
    failure returns ``(False, record)`` with ``stage="schema_validation"``,
    ``last_error`` = the joined error strings naming the offending field,
    ``attempts=1``; no file is written and the manifest is untouched."""
    fields = _valid_fields()
    fields[0]["confidence"] = "very high"  # not in the confidence enum
    fields[0]["location_metadata"] = [{"id": "ev-999"}]  # not in location

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.object(_pdf_processor, "save_manifest") as save, \
         caplog.at_level(logging.WARNING, logger=_pdf_processor.logger.name):
        ok, record = _save_pdf_output("paper_bad", fields)

    assert ok is False
    assert set(record) == _FAILURE_RECORD_KEYS
    assert record["stage"] == "schema_validation"
    assert record["chunk"] is None
    assert record["attempts"] == 1
    assert isinstance(record["error_type"], str) and record["error_type"]
    # Both the schema error and the cross-reference error are joined in.
    assert "field_index=3" in record["last_error"]
    assert "ev-999" in record["last_error"]
    assert not (tmp_path / "paper_bad.extracted.json").exists()
    save.assert_not_called()
    # Logging is kept: one WARNING per error plus the ERROR summary.
    assert any(r.levelno == logging.ERROR and "paper_bad" in r.getMessage() for r in caplog.records)
    assert sum(1 for r in caplog.records if r.levelno == logging.WARNING) >= 2


def test_save_pdf_output_write_error_propagates_without_manifest_write(tmp_path):
    """design "FailureRecorder": ``_atomic_write_json`` errors propagate
    unchanged -- the gate does not catch I/O errors and does not touch the
    manifest."""
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.object(_pdf_processor, "save_manifest") as save, \
         patch.object(_pdf_processor, "_atomic_write_json", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _save_pdf_output("paper_io", _valid_fields())

    save.assert_not_called()


def test_process_pdf_output_write_error_records_failed_output_write_and_reraises(tmp_path):
    """Requirement 1.4 / design "FailureRecorder": a write error is recorded
    as ``failed_output_write`` (stage ``output_write``) under the manifest
    lock and then re-raised so the orchestrator still logs it."""
    mock_api = _synth_api([_VALID_SYNTH])
    held_at_save: list[bool] = []
    # process_pdf re-raises, so _run_synth_case never returns the manifest;
    # the save_manifest stub captures it instead.
    captured: dict = {}

    def _capture(manifest):
        captured.update(manifest)
        held_at_save.append(_run_synth_case.last_lock.locked())

    with pytest.raises(OSError, match="disk full"):
        _run_synth_case(
            tmp_path, mock_api, save_manifest=_capture,
            extra_patches=[patch.object(
                _pdf_processor, "_atomic_write_json", side_effect=OSError("disk full"),
            )],
        )

    entry = captured["paper_synth_repair"]
    assert set(entry) == {"status", "error", "failures"}
    assert entry["status"] == "failed_output_write"
    assert entry["error"] == "disk full"
    assert entry["failures"] == [{
        "stage": "output_write",
        "chunk": None,
        "error_type": "OSError",
        "last_error": "disk full",
        "attempts": 1,
    }]
    assert held_at_save and all(held_at_save), "manifest persisted outside manifest_lock"
    assert not (tmp_path / "paper_synth_repair.extracted.json").exists()


def test_process_pdf_schema_validation_failure_records_failure_record_under_lock(tmp_path):
    """Requirements 1.3, 1.4 / design "FailureRecorder": ``(False, record)``
    from the gate becomes ``failed_schema_validation`` with that record,
    persisted under the manifest lock; no output file is written."""
    mock_api = _synth_api([_VALID_SYNTH])
    held_at_save: list[bool] = []

    def _spy(manifest):
        held_at_save.append(_run_synth_case.last_lock.locked())

    fake_validator = MagicMock()
    fake_validator.validate.return_value = _pdf_processor.ValidationResult(
        is_valid=False, errors=["field_index=5 | field_name='Synthesis notes' | bad value"],
    )

    result, manifest = _run_synth_case(
        tmp_path, mock_api, save_manifest=_spy,
        extra_patches=[patch.object(_pdf_processor, "_final_output_validator", fake_validator)],
    )

    assert result is None
    entry = manifest["paper_synth_repair"]
    assert set(entry) == {"status", "error", "failures"}
    assert entry["status"] == "failed_schema_validation"
    assert "field_index=5" in entry["error"]
    assert len(entry["failures"]) == 1
    record = entry["failures"][0]
    assert set(record) == _FAILURE_RECORD_KEYS
    assert record["stage"] == "schema_validation"
    assert record["chunk"] is None
    assert record["last_error"] == entry["error"]
    assert record["attempts"] == 1
    assert held_at_save and all(held_at_save), "manifest persisted outside manifest_lock"
    assert not (tmp_path / "paper_synth_repair.extracted.json").exists()


def test_run_parallel_chunks_repair_exhaustion_records_one_record_per_failed_chunk(tmp_path):
    """Requirements 5.3, 5.4 / design "FailureRecorder": chunk exhaustion
    writes ``failed_chunks`` with one ``extraction_chunk`` record per failed
    chunk (built from ``RepairExhaustedError.metadata``) beside the
    compatibility int list, under the manifest lock."""
    chunk_fields = {
        1: [{"field_index": 3, "field_name": "Study design", "definition": "..."}],
        2: [{"field_index": 10, "field_name": "Sample size", "definition": "..."}],
    }
    chunk_sources = {1: "evidence text chunk 1", 2: "evidence text chunk 2"}
    pdf_name = "paper_exhausted"
    manifest = {pdf_name: {"status": "pending"}}
    held_at_save: list[bool] = []
    lock_box: dict = {}

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 2:
            return "never valid json {"
        return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]})

    def _spy(_manifest):
        held_at_save.append(lock_box["lock"].locked())

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    with patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "save_manifest", side_effect=_spy):

        async def _run():
            lock_box["lock"] = asyncio.Lock()
            return await _pdf_processor._run_parallel_chunks(
                chunk_sources=chunk_sources,
                chunk_fields=chunk_fields,
                valid_location_ids={"ev-001"},
                api_semaphore=asyncio.Semaphore(5),
                pdf_name=pdf_name,
                num_chunks=3,
                enable_prewarm=False,
                chunk_model="gpt-test",
                synthesis_model="gpt-test",
                prewarm_synthesis_diff=False,
                manifest=manifest,
                manifest_lock=lock_box["lock"],
            )

        result = asyncio.run(_run())

    assert result is None
    entry = manifest[pdf_name]
    assert set(entry) == {"status", "error", "failures", "failed_chunks"}
    assert entry["status"] == "failed_chunks"
    assert entry["failed_chunks"] == [2]
    assert len(entry["failures"]) == 1
    record = entry["failures"][0]
    assert set(record) == _FAILURE_RECORD_KEYS
    assert record["stage"] == "extraction_chunk"
    assert record["chunk"] == 2
    assert record["error_type"] == "parse"
    assert record["attempts"] == 2  # default max_repair_attempts
    assert record["last_error"].startswith("JSON parse failed")
    assert entry["error"] == record["last_error"]
    assert held_at_save and all(held_at_save), "manifest persisted outside manifest_lock"


def test_load_completed_result_ignores_failure_record_entries(tmp_path):
    """design "FailureRecorder" implementation note: ``_load_completed_result``
    reads only ``status``; the added ``error`` / ``failures`` keys never make
    a failed entry look complete, even when a stale output file exists."""
    pdf_name = "paper_failed_shape"
    (tmp_path / f"{pdf_name}.extracted.json").write_text("[]", encoding="utf-8")
    manifest = {pdf_name: {
        "status": "failed_output_write",
        "error": "disk full",
        "failures": [_record("output_write", None, "OSError", "disk full", 1)],
    }}

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        assert _load_completed_result(pdf_name, manifest) is None


# ---------------------------------------------------------------------------
# Final-output negative path end to end through process_pdf, real validator
# (feature: risk-remediation, task 10.4; design.md "FailureRecorder",
# "Testing Strategy")
# Requirements: 1.1, 1.2, 1.3, 1.4
# ---------------------------------------------------------------------------


def test_process_pdf_valid_fields_write_output_file_and_mark_complete(tmp_path):
    """Requirements 1.1, 1.2: with every merged field valid -- ``domain_group``
    in the integer form ``_build_field_lookup`` produces -- ``process_pdf``
    writes ``outputs/<paper>.extracted.json`` through the real
    ``FinalOutputValidator`` and marks the paper ``complete``. The file's
    content is the returned field list, so the on-disk record is usable."""
    mock_api = _synth_api([_VALID_SYNTH])
    out_file = tmp_path / "paper_synth_repair.extracted.json"

    result, manifest = _run_synth_case(tmp_path, mock_api)

    assert result is not None
    assert out_file.exists(), "valid fields must produce the output file"
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written == result
    assert {f["field_index"] for f in written} == {3, 4, 5}
    assert all(isinstance(f["domain_group"], int) and f["domain_group"] >= 1 for f in written)
    assert manifest["paper_synth_repair"]["status"] == "complete"
    assert "failures" not in manifest["paper_synth_repair"]


@pytest.mark.parametrize(
    "bad_domain_group, expected_fragment",
    [
        (0, "minimum"),                       # integer below the schema minimum of 1
        ("13. Reviewer assessment", "type"),  # raw map string, never the emitted form
    ],
    ids=["below_minimum", "wrong_type"],
)
def test_process_pdf_invalid_field_no_output_and_failure_record_names_field(
    tmp_path, caplog, bad_domain_group, expected_fragment,
):
    """Requirements 1.3, 1.4 (end to end, real validator): one merged field
    whose ``domain_group`` violates ``final_output_schema.json`` means

    * no output file exists at ``outputs/<paper>.extracted.json`` (1.4);
    * the manifest entry is ``failed_schema_validation`` with a single
      ``schema_validation`` record whose ``last_error`` carries the offending
      field's identity as ``FinalOutputValidator.format_error`` renders it
      (``field_index=`` and ``field_name=``) (1.3);
    * a WARNING and an ERROR log line name the paper, and the WARNING carries
      the same field identity, so the skipped write is attributable from the
      logs alone (1.4).

    The other two fields are valid, so the rejection is pinned to field 5.
    """
    mock_api = _synth_api([_VALID_SYNTH])
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
        5: {"domain_group": bad_domain_group, "field_name": "Synthesis notes"},
    }
    out_file = tmp_path / "paper_synth_repair.extracted.json"

    with caplog.at_level(logging.WARNING, logger=_pdf_processor.logger.name):
        result, manifest = _run_synth_case(tmp_path, mock_api, field_lookup=field_lookup)

    # (a) nothing written -- not even a partial or temp file for this paper
    assert result is None
    assert not out_file.exists()
    assert list(tmp_path.glob("paper_synth_repair*")) == []

    # (b) manifest carries the failure record naming the field
    entry = manifest["paper_synth_repair"]
    assert set(entry) == {"status", "error", "failures"}
    assert entry["status"] == "failed_schema_validation"
    assert len(entry["failures"]) == 1
    record = entry["failures"][0]
    assert set(record) == _FAILURE_RECORD_KEYS
    assert record["stage"] == "schema_validation"
    assert record["chunk"] is None
    assert record["attempts"] == 1
    assert record["last_error"] == entry["error"]
    assert "field_index=5" in record["last_error"]
    assert "field_name='Synthesis notes'" in record["last_error"]
    assert "domain_group" in record["last_error"]
    assert expected_fragment in record["last_error"]
    # Only field 5 is rejected; the valid fields must not appear as errors.
    assert "field_index=3" not in record["last_error"]
    assert "field_index=4" not in record["last_error"]

    # (c) the rejection is logged with the field identity
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("paper_synth_repair" in m and "field_index=5" in m
               and "field_name='Synthesis notes'" in m for m in warnings), warnings
    assert any("paper_synth_repair" in m and "output not written" in m for m in errors), errors


def test_process_pdf_invalid_field_leaves_no_stale_output_from_an_earlier_run(tmp_path):
    """Requirement 1.4 corollary: a stale output file from a previous run is
    not what makes a paper look complete -- after a schema rejection the
    manifest says ``failed_schema_validation`` and ``_load_completed_result``
    refuses to serve the stale file."""
    mock_api = _synth_api([_VALID_SYNTH])
    field_lookup = dict(_SYNTH_FIELD_LOOKUP)
    field_lookup[5] = {"domain_group": -1, "field_name": "Synthesis notes"}
    out_file = tmp_path / "paper_synth_repair.extracted.json"
    out_file.write_text(json.dumps([{"stale": True}]), encoding="utf-8")

    result, manifest = _run_synth_case(tmp_path, mock_api, field_lookup=field_lookup)

    assert result is None
    assert manifest["paper_synth_repair"]["status"] == "failed_schema_validation"
    # The gate never overwrote the stale file with a rejected payload ...
    assert json.loads(out_file.read_text(encoding="utf-8")) == [{"stale": True}]
    # ... and the stale file cannot be read back as a completed result.
    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path):
        assert _load_completed_result("paper_synth_repair", manifest) is None
