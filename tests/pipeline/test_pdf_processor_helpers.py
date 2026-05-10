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
        {"field_index": 1, "extracted_value": "Smith 2020", "confidence": "h"},
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
            "domain_group": "2. Clinical context",
            "field_name": "Study design",
            "extracted_value": "RCT",
            "evidence": "randomised controlled trial",
            "location": ["ev-001"],
            "location_metadata": [],
            "confidence": "h",
        },
        {
            "field_index": 5,
            "domain_group": "3. Population",
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
    "domain_group": st.text(min_size=1, max_size=30),
    "field_name": st.text(min_size=1, max_size=30),
    "evidence": st.text(max_size=100),
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
    """Build a minimal QCContext for pdf_processor tests."""
    from quality_control.models import BranchOutput, QCContext, UnifiedRecord

    unified = UnifiedRecord(
        document_id=pdf_name,
        content={"exact_text": exact_text, "source_pdf_path": ""},
    )
    return QCContext(
        branches=[BranchOutput(extractor="grobid", branch=0, payload="<TEI/>", status=None)],
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

    # A valid compact chunk result (list of field dicts with compact keys)
    valid_result = [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]

    with patch.object(_pdf_processor, "extract_chunk", new_callable=AsyncMock) as mock_ec:
        mock_ec.return_value = valid_result

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

    valid_result = [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 2:
            raise RuntimeError("API error on chunk 2")
        return valid_result

    with patch.object(_pdf_processor, "extract_chunk", new_callable=AsyncMock) as mock_ec, \
         patch.object(_pdf_processor, "save_manifest"):
        mock_ec.side_effect = _side_effect

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
        {"field_index": 1, "extracted_value": "Smith 2020", "confidence": "h"},
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

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.object(_pdf_processor, "extract_chunk", new_callable=AsyncMock) as mock_ec, \
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

    mock_ec.assert_not_called()
    assert result == fields
