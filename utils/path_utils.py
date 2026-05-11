"""Local file and path resolution utilities for the parser repository."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import yaml

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ============================================================================
# Centralized Path Constants
# ============================================================================
# These are the standard project paths used throughout the pipeline.

BASE_DIR: Path = PROJECT_ROOT
"""The root directory of the EviTrace project."""


def _load_local_settings() -> dict:
    """Load local-related settings from `config.yaml` if present.

    Returns a dict of local config keys. Supports both top-level keys
    and a nested `local:` mapping for backward compatibility.
    """
    # Prefer configs/config.yaml, fall back to repo-root config.yaml
    cfg_path = PROJECT_ROOT / "configs" / "config.yaml"
    if not cfg_path.exists():
        cfg_path = PROJECT_ROOT / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return {}

    # Support either `local:` block or top-level keys
    return raw.get("local", raw)


_LOCAL_SETTINGS = _load_local_settings()

_extraction_candidate = BASE_DIR / _LOCAL_SETTINGS.get("extraction_map_path", "extraction_map.json")
if not _extraction_candidate.exists():
    _extraction_candidate = BASE_DIR / "configs" / _LOCAL_SETTINGS.get("extraction_map_path", "extraction_map.json")

EXTRACTION_MAP: Path = _extraction_candidate
"""Path to the extraction field definitions mapping."""

PDF_DIR: Path = BASE_DIR / _LOCAL_SETTINGS.get("pdfs_path", "pdfs")
"""Directory where input PDFs are located."""

OUTPUT_DIR: Path = BASE_DIR / _LOCAL_SETTINGS.get("output_folder_path", "outputs")
"""Directory where extraction results are written."""

MANIFEST_FILE: Path = BASE_DIR / "manifest.json"
"""Manifest file tracking which PDFs have been processed."""

QC_REPORT_FILE: Path = OUTPUT_DIR / "qc_report.csv"
"""QC report file for flagged inconsistencies."""


def is_url(value: str) -> bool:
    """Return True when *value* looks like an HTTP(S) URL."""
    if not value or not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resolve_project_path(path_or_url: str) -> str:
    """Resolve a local path from the project root unless it is absolute."""
    path_obj = Path(path_or_url).expanduser()
    if not path_obj.is_absolute():
        path_obj = PROJECT_ROOT / path_obj
    return str(path_obj.resolve())


def resolve_log_path(log_file: str) -> Path:
    """Return an absolute path for a log file relative to the project root."""
    path_obj = Path(log_file)
    if path_obj.is_absolute():
        return path_obj
    return (PROJECT_ROOT / path_obj).resolve()





def _scan_local_pdf_paths(local_path: str) -> list[str]:
    """Return all local PDF paths from a file or directory."""
    path_obj = Path(local_path)
    if path_obj.is_file():
        if path_obj.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a .pdf file, got: {local_path}")
        return [str(path_obj)]

    if not path_obj.is_dir():
        raise FileNotFoundError(f"PDF source does not exist: {local_path}")

    pdf_paths = sorted(str(p) for p in path_obj.rglob("*.pdf"))
    return pdf_paths





def _download_url_to_temp_file(url: str, prefix: str) -> str:
    """Download a URL to a temporary file and return the local path."""
    try:
        import gdown
    except ImportError as exc:  # pragma: no cover - exercised only when missing
        raise ImportError("gdown is required to download URL sources") from exc

    suffix = Path(urlparse(url).path).suffix or ".pdf"
    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    try:
        gdown.download(url=url, output=temp_path, quiet=False, fuzzy=True)
    except TypeError:
        # Older gdown versions do not accept the 'fuzzy' keyword.
        gdown.download(url=url, output=temp_path, quiet=False)
    return temp_path


def _download_pdf_source_url(
    url: str,
    folder_prefix: str = "evi_trace_pdf_folder_",
    file_prefix: str = "evi_trace_pdf_",
) -> tuple[str, list[str]]:
    """Download PDF URL sources and return (local_folder, pdf_paths)."""
    try:
        import gdown
    except ImportError as exc:  # pragma: no cover - exercised only when missing
        raise ImportError("gdown is required to download URL sources") from exc

    if "drive.google.com" in url and "/folders/" in url:
        download_root = Path(tempfile.mkdtemp(prefix=folder_prefix))
        gdown.download_folder(
            url=url,
            output=str(download_root),
            quiet=False,
            use_cookies=False,
        )
        pdf_paths = sorted(str(p) for p in download_root.rglob("*.pdf"))
        return str(download_root), pdf_paths

    downloaded_pdf = _download_url_to_temp_file(url, prefix=file_prefix)
    return str(Path(downloaded_pdf).parent), [downloaded_pdf]


def list_pdf_files_from_source(
    pdf_source: str,
    *,
    folder_prefix: str = "evi_trace_pdf_folder_",
    file_prefix: str = "evi_trace_pdf_",
) -> tuple[str, dict]:
    """Build PDF metadata from a URL or local file/folder path."""
    local_folder: str
    pdf_paths: list[str]

    if is_url(pdf_source):
        local_folder, pdf_paths = _download_pdf_source_url(
            pdf_source,
            folder_prefix=folder_prefix,
            file_prefix=file_prefix,
        )
    else:
        resolved = resolve_project_path(pdf_source)
        pdf_paths = _scan_local_pdf_paths(resolved)
        if Path(resolved).is_dir():
            local_folder = resolved
        else:
            local_folder = str(Path(resolved).parent)

    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found for source: {pdf_source}")

    result: dict[str, dict] = {}
    for abs_path in pdf_paths:
        name = Path(abs_path).name
        result[name] = {
            "id": hashlib.sha256(abs_path.encode("utf-8")).hexdigest(),
            "local_path": abs_path,
            "uri": Path(abs_path).as_uri(),
        }

    return local_folder, result


def list_pdf_files_from_dir(
    pdfs_dir: str,
    *,
    folder_prefix: str = "evi_trace_pdf_folder_",
    file_prefix: str = "evi_trace_pdf_",
) -> tuple[str, dict]:
    """Resolve a local PDF folder and return metadata for all PDFs inside it."""
    resolved = resolve_project_path(pdfs_dir)
    if not Path(resolved).is_dir():
        raise ValueError(f"Expected a folder, got: {pdfs_dir}")
    return list_pdf_files_from_source(
        resolved,
        folder_prefix=folder_prefix,
        file_prefix=file_prefix,
    )


def create_output_folder(output_folder_path: str = "output") -> str:
    """Create or resolve the output folder."""
    output_folder = resolve_project_path(output_folder_path)
    os.makedirs(output_folder, exist_ok=True)
    return output_folder



