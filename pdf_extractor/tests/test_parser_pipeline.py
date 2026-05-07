"""
tests/test_parser_pipeline.py
==============================
Integration-style tests for the EviTrace parser pipeline.

Covers:
  - config loading (evi_trace.utils.config.load_config)
  - PDF source resolution (evi_trace.utils.path_utils)
  - text extraction (text_extractor, mocked)
  - sentence processing (sentence_processor)
  - artifact saving (_save_artifact)
  - end-to-end run_pipeline with a real stub PDF
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from evi_trace.utils import path_utils
from evi_trace.utils.config_utils import load_config
import evi_trace.cli as run
import evi_trace.processing.sentence_processor as sentence_processor
import evi_trace.extraction as text_extractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_stub_pdf(path: Path) -> None:
    """Write a minimal valid-looking PDF byte sequence."""
    path.write_bytes(b"%PDF-1.4\n%stub\n")


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def _write_config(self, tmp_path, data: dict) -> str:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(data), encoding="utf-8")
        return str(cfg_file)

    def test_loads_valid_config_with_defaults(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {"pdfs_path": "data/pdfs"})

        cfg = load_config(cfg_file)

        assert cfg["len_filter"] == 40
        assert cfg["ocr"] is True
        assert cfg["ocr_text_quality_threshold"] == 0.7
        assert cfg["log_file"] == "log.txt"
        assert cfg["output_folder_path"] == "output"

    def test_pdfs_path_resolved_to_absolute(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {"pdfs_path": "data/pdfs"})

        cfg = load_config(cfg_file)

        from pathlib import Path
        assert Path(cfg["pdfs_path"]).is_absolute()

    def test_explicit_values_override_defaults(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "len_filter": 80,
            "ocr": False,
        })

        cfg = load_config(cfg_file)

        assert cfg["len_filter"] == 80
        assert cfg["ocr"] is False

    def test_raises_on_unknown_key(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {"pdfs_path": "data/pdfs", "typo_key": True})

        with pytest.raises(ValueError, match="Unknown config keys"):
            load_config(cfg_file)

    def test_raises_on_empty_pdfs_path(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {"pdfs_path": ""})

        with pytest.raises(ValueError, match="pdfs_path"):
            load_config(cfg_file)

    def test_raises_on_missing_pdfs_path(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {})

        with pytest.raises(ValueError, match="pdfs_path"):
            load_config(cfg_file)

    def test_raises_on_wrong_len_filter_type(self, tmp_path):
        cfg_file = self._write_config(tmp_path, {"pdfs_path": "data/pdfs", "len_filter": "forty"})

        with pytest.raises(TypeError, match="len_filter"):
            load_config(cfg_file)

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "missing.yaml"))


# ---------------------------------------------------------------------------
# _save_artifact
# ---------------------------------------------------------------------------

class TestSaveArtifact:
    def test_writes_json_file(self, tmp_path):
        artifact = {
            "pdf_name": "paper.pdf",
            "pdf_id": "abc123",
            "pdf_uri": "file:///paper.pdf",
            "blocks": [],
            "sentence_records": [],
            "full_pdf_text": "hello world",
            "page_texts": {"0": "hello world"},
        }
        out_path = run._save_artifact(str(tmp_path), "paper.pdf", artifact)

        assert Path(out_path).exists()
        loaded = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert loaded["pdf_name"] == "paper.pdf"
        assert loaded["full_pdf_text"] == "hello world"

    def test_stem_used_as_filename(self, tmp_path):
        artifact = {"pdf_name": "my paper.pdf", "blocks": []}
        out_path = run._save_artifact(str(tmp_path), "my paper.pdf", artifact)
        assert Path(out_path).name == "my paper.json"


# ---------------------------------------------------------------------------
# sentence_processor – unit tests
# ---------------------------------------------------------------------------

class TestNormaliseText:
    def test_heals_soft_linebreak(self):
        result = sentence_processor.normalise_text("hello\nworld")
        assert result == "hello world"

    def test_preserves_hard_linebreak_before_uppercase(self):
        result = sentence_processor.normalise_text("First sentence.\nSecond sentence.")
        assert "\n" in result

    def test_collapses_multiple_spaces(self):
        result = sentence_processor.normalise_text("hello   world")
        assert result == "hello world"

    def test_strips_surrounding_whitespace(self):
        result = sentence_processor.normalise_text("  hello  ")
        assert result == "hello"


class TestIsNoise:
    def test_doi_is_noise(self):
        assert sentence_processor.is_noise("doi:10.1001/jama.2020.123")

    def test_email_is_noise(self):
        assert sentence_processor.is_noise("Contact: author@example.com for details.")

    def test_url_is_noise(self):
        assert sentence_processor.is_noise("See https://example.com for more.")

    def test_orcid_is_noise(self):
        assert sentence_processor.is_noise("ORCID: 0000-0002-1234-5678")

    def test_clean_sentence_is_not_noise(self):
        assert not sentence_processor.is_noise(
            "This study investigated the effects of exercise on cognitive function."
        )

    def test_short_nonalpha_is_noise(self):
        assert sentence_processor.is_noise("--- 42 ---")


class TestProcessSentences:
    def _make_block(self, text: str, page_index: int = 0) -> dict:
        return {
            "text": text,
            "page_index": page_index,
            "block_bbox": (0.0, 0.0, 100.0, 20.0),
            "spans": [],
        }

    def test_returns_sentence_records(self):
        blocks = [self._make_block(
            "This is the first sentence. This is the second sentence.", 0
        )]
        records = sentence_processor.process_sentences(blocks, len_filter=10)
        assert len(records) >= 1
        for r in records:
            assert "sentence" in r
            assert "page_index" in r

    def test_filters_short_sentences(self):
        blocks = [self._make_block("Hi.", 0)]
        records = sentence_processor.process_sentences(blocks, len_filter=40)
        assert records == []

    def test_filters_noise(self):
        blocks = [self._make_block("doi:10.1001/jama.2020.1234", 0)]
        records = sentence_processor.process_sentences(blocks, len_filter=5)
        assert records == []

    def test_page_index_preserved(self):
        blocks = [self._make_block("This is a long enough sentence to survive filtering.", 3)]
        records = sentence_processor.process_sentences(blocks, len_filter=10)
        assert all(r["page_index"] == 3 for r in records)


class TestBuildFullText:
    def _make_block(self, text: str, page_index: int) -> dict:
        return {"text": text, "page_index": page_index, "block_bbox": None, "spans": []}

    def test_full_text_joins_all_blocks(self):
        blocks = [
            self._make_block("Hello world.", 0),
            self._make_block("Second block.", 1),
        ]
        full_text, page_texts = sentence_processor.build_full_text(blocks)
        assert "Hello world." in full_text
        assert "Second block." in full_text

    def test_page_texts_keyed_by_page(self):
        blocks = [
            self._make_block("Page zero text.", 0),
            self._make_block("Also on page zero.", 0),
            self._make_block("Page one text.", 1),
        ]
        _, page_texts = sentence_processor.build_full_text(blocks)
        assert 0 in page_texts
        assert 1 in page_texts
        assert "Page zero text." in page_texts[0]
        assert "Page one text." in page_texts[1]

    def test_empty_blocks_returns_empty_strings(self):
        full_text, page_texts = sentence_processor.build_full_text([])
        assert full_text == ""
        assert page_texts == {}


# ---------------------------------------------------------------------------
# text_extractor – quality score
# ---------------------------------------------------------------------------

class TestComputeQualityScore:
    def test_clean_text_high_score(self):
        blocks = [{"text": "This is clean English text with lots of alphabetic characters."}]
        score = text_extractor._compute_quality_score(blocks, None)
        assert score >= 0.8

    def test_empty_blocks_returns_zero(self):
        assert text_extractor._compute_quality_score([], None) == 0.0

    def test_all_symbols_returns_low_score(self):
        blocks = [{"text": "!@#$%^&*()_+{}|:<>?"}]
        score = text_extractor._compute_quality_score(blocks, None)
        assert score == 0.0


# ---------------------------------------------------------------------------
# path_utils – PDF source resolution
# ---------------------------------------------------------------------------

class TestDriveUtils:
    def test_local_file_returns_metadata(self, tmp_path):
        pdf_path = tmp_path / "paper.pdf"
        _write_stub_pdf(pdf_path)

        local_folder, pdf_files = path_utils.list_pdf_files_from_source(str(pdf_path))

        assert "paper.pdf" in pdf_files
        assert pdf_files["paper.pdf"]["local_path"] == str(pdf_path.resolve())

    def test_local_folder_returns_all_pdfs(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        _write_stub_pdf(pdf_dir / "a.pdf")
        _write_stub_pdf(pdf_dir / "b.pdf")

        local_folder, pdf_files = path_utils.list_pdf_files_from_source(str(pdf_dir))

        assert set(pdf_files.keys()) == {"a.pdf", "b.pdf"}

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            path_utils.list_pdf_files_from_source(str(tmp_path / "nonexistent.pdf"))

    def test_non_pdf_file_raises(self, tmp_path):
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("not a pdf", encoding="utf-8")
        with pytest.raises(ValueError, match="Expected a .pdf file"):
            path_utils.list_pdf_files_from_source(str(txt_file))

    def test_create_output_folder_creates_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("evi_trace.utils.path_utils.PROJECT_ROOT", tmp_path)
        folder = path_utils.create_output_folder("parser_output")
        assert Path(folder).is_dir()
        assert Path(folder) == tmp_path / "parser_output"


# ---------------------------------------------------------------------------
# run_pipeline – integration test with mocked extraction
# ---------------------------------------------------------------------------

class TestRunPipeline:
    def test_pipeline_creates_artifact(self, tmp_path, monkeypatch):
        """run_pipeline should produce one <stem>.json artifact per PDF."""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        _write_stub_pdf(pdf_dir / "paper.pdf")

        cfg = {
            "log_file": str(tmp_path / "log.txt"),
            "log_level": "WARNING",
            "len_filter": 10,
            "ocr": False,
            "ocr_text_quality_threshold": 0.7,
            "pdfs_path": str(pdf_dir),
            "output_folder_path": str(tmp_path / "output"),
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        fake_blocks = [
            {
                "text": "This is a long enough test sentence for the pipeline.",
                "page_index": 0,
                "block_bbox": (0.0, 0.0, 100.0, 20.0),
                "spans": [],
            }
        ]

        with patch("evi_trace.cli.extract_pdf", return_value=(fake_blocks, [])):
            run.run_pipeline(str(cfg_file))

        artifact_path = tmp_path / "output" / "paper.json"
        assert artifact_path.exists(), "Parser artifact was not created"

        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["pdf_name"] == "paper.pdf"
        assert len(artifact["sentence_records"]) >= 1
        assert artifact["full_pdf_text"] != ""
