"""Tests for source resolution helpers used by the pipeline config."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

try:
    import gdown  # type: ignore
except ImportError:
    fake_gdown = ModuleType("gdown")
    fake_gdown.download = lambda *args, **kwargs: True
    fake_gdown.download_folder = lambda *args, **kwargs: True
    sys.modules["gdown"] = fake_gdown

from utils import path_utils
from utils.path_utils import PROJECT_ROOT


def _write_stub_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%stub\n")


class TestPdfSourceResolution:
    def test_local_folder_returns_pdf_metadata(self, tmp_path):
        pdf_dir = tmp_path / "pdf"
        pdf_dir.mkdir()
        pdf_path = pdf_dir / "paper.pdf"
        _write_stub_pdf(pdf_path)

        local_folder, pdf_files = path_utils.list_pdf_files_from_source(str(pdf_dir))

        assert Path(local_folder) == pdf_dir.resolve()
        assert "paper.pdf" in pdf_files
        assert pdf_files["paper.pdf"]["local_path"] == str(pdf_path.resolve())

    def test_drive_folder_url_downloads_folder_tree(self, monkeypatch, tmp_path):
        fake_gdown = ModuleType("gdown")

        def fake_download_folder(url, output, quiet=False, use_cookies=False):
            root = Path(output)
            root.mkdir(parents=True, exist_ok=True)
            nested = root / "nested"
            nested.mkdir(parents=True, exist_ok=True)
            _write_stub_pdf(nested / "evidence.pdf")
            return str(root)

        fake_gdown.download_folder = fake_download_folder
        monkeypatch.setitem(sys.modules, "gdown", fake_gdown)

        local_folder, pdf_files = path_utils.list_pdf_files_from_source(
            "https://drive.google.com/drive/folders/1234567890abcdef"
        )

        assert Path(local_folder).exists()
        assert "evidence.pdf" in pdf_files
        assert Path(pdf_files["evidence.pdf"]["local_path"]).suffix == ".pdf"

    def test_dir_helper_accepts_folder_and_scans_pdfs(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        _write_stub_pdf(pdf_dir / "one.pdf")
        _write_stub_pdf(pdf_dir / "two.pdf")

        local_folder, pdf_files = path_utils.list_pdf_files_from_dir(str(pdf_dir))

        assert Path(local_folder) == pdf_dir.resolve()
        assert set(pdf_files) == {"one.pdf", "two.pdf"}

    def test_dir_helper_rejects_single_file(self, tmp_path):
        pdf_file = tmp_path / "paper.pdf"
        _write_stub_pdf(pdf_file)

        with pytest.raises(ValueError, match="Expected a folder"):
            path_utils.list_pdf_files_from_dir(str(pdf_file))


class TestOutputFolderResolution:
    def test_default_output_folder_is_project_root_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.path_utils.PROJECT_ROOT", tmp_path)

        output_folder = path_utils.create_output_folder()

        assert Path(output_folder) == tmp_path / "output"
        assert Path(output_folder).exists()

    def test_custom_output_folder_path_is_resolved_from_project_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.path_utils.PROJECT_ROOT", tmp_path)

        output_folder = path_utils.create_output_folder("results/output")

        assert Path(output_folder) == tmp_path / "results" / "output"
        assert Path(output_folder).exists()
