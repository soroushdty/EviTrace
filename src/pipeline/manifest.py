"""Manifest file I/O for the EviTrace pipeline.

Provides identity-aware resumability: each manifest entry carries identity
fields (pdf_content_hash, config_hash, extraction_map_hash, model_id,
schema_version, output_path) that allow staleness detection when inputs or
configuration change between runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from utils.logging_utils import get_logger
from utils.path_utils import EXTRACTION_MAP, MANIFEST_FILE

logger = get_logger(__name__)

# Current schema version for manifest entries. Bump when the manifest entry
# format changes in a backward-incompatible way.
MANIFEST_SCHEMA_VERSION: str = "1.0.0"


# ---------------------------------------------------------------------------
# Identity dataclass and helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestIdentity:
    """Identity fields for cache invalidation.

    Each field captures one dimension of the processing context. A change
    in any field means the previous result is stale and the PDF must be
    re-processed.
    """

    pdf_content_hash: str
    """SHA-256 of PDF file bytes."""

    config_hash: str
    """SHA-256 of serialized relevant config sections."""

    extraction_map_hash: str
    """SHA-256 of configs/extraction_map.json."""

    model_id: str
    """Chunk model name used for extraction."""

    schema_version: str
    """Manifest schema version string (e.g. '1.0.0')."""

    output_path: str
    """Relative path to the expected output file."""

    def to_dict(self) -> dict[str, str]:
        """Serialize identity fields to a plain dict for JSON storage."""
        return asdict(self)


def _compute_file_sha256(file_path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes.

    Returns ``"nohash"`` if the file cannot be read.
    """
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "nohash"


def _compute_config_hash(config: dict) -> str:
    """Return the SHA-256 hex digest of the relevant config sections.

    Serializes the config dict deterministically (sorted keys) and hashes
    the resulting JSON string. Only keys that affect extraction output are
    included: ``openai``, ``extraction``, ``quality_control``.
    """
    relevant_keys = ("openai", "extraction", "quality_control")
    relevant = {k: config[k] for k in relevant_keys if k in config}
    serialized = json.dumps(relevant, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _compute_extraction_map_hash() -> str:
    """Return the SHA-256 hex digest of configs/extraction_map.json.

    Returns ``"no_extraction_map"`` if the file cannot be read.
    """
    try:
        data = EXTRACTION_MAP.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return "no_extraction_map"


def compute_identity(
    pdf_path: Path | str,
    config: dict,
    *,
    model_id: str | None = None,
    output_path: str | None = None,
) -> ManifestIdentity:
    """Compute identity fields for a PDF + config combination.

    Args:
        pdf_path: Path to the PDF file.
        config: Full pipeline config dict (as returned by load_openai_config
                or the raw YAML dict containing openai/extraction/quality_control).
        model_id: Override for the chunk model name. If None, reads from
                  ``config["chunk_model"]`` or ``config.get("openai", {}).get("chunk_model")``.
        output_path: Relative path to the expected output file. If None,
                     derived from the PDF filename.

    Returns:
        A frozen ManifestIdentity instance.
    """
    pdf_path = Path(pdf_path)

    # Resolve model_id from config if not explicitly provided
    if model_id is None:
        model_id = config.get("chunk_model") or config.get("openai", {}).get("chunk_model", "unknown")

    # Resolve output_path from PDF name if not explicitly provided
    if output_path is None:
        pdf_stem = pdf_path.stem
        output_path = f"{pdf_stem}.extracted.json"

    return ManifestIdentity(
        pdf_content_hash=_compute_file_sha256(pdf_path),
        config_hash=_compute_config_hash(config),
        extraction_map_hash=_compute_extraction_map_hash(),
        model_id=str(model_id),
        schema_version=MANIFEST_SCHEMA_VERSION,
        output_path=output_path,
    )


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

# Identity fields that are compared for staleness. output_path is also
# checked but separately (for file existence/validity).
_IDENTITY_FIELDS: tuple[str, ...] = (
    "pdf_content_hash",
    "config_hash",
    "extraction_map_hash",
    "model_id",
    "schema_version",
)


def is_stale(entry: dict[str, Any], current_identity: ManifestIdentity) -> bool:
    """Return True if any identity component has changed.

    Compares the stored identity fields in ``entry`` against the
    ``current_identity``. If any field is missing from the entry or
    differs from the current value, the entry is considered stale.

    Args:
        entry: A manifest entry dict (as stored in the manifest JSON).
        current_identity: The freshly computed identity for the current run.

    Returns:
        True if the entry is stale and the PDF should be re-processed.
    """
    identity_dict = current_identity.to_dict()
    for field in _IDENTITY_FIELDS:
        stored_value = entry.get(field)
        current_value = identity_dict[field]
        if stored_value is None or stored_value != current_value:
            return True
    return False


def _is_output_valid(entry: dict[str, Any], output_dir: Path | None = None) -> bool:
    """Check whether the output file referenced by the entry exists and is valid JSON.

    Args:
        entry: A manifest entry dict.
        output_dir: Directory where output files are stored. If None, uses
                    the default OUTPUT_DIR from path_utils.

    Returns:
        True if the output file exists and parses as valid JSON.
    """
    output_path_str = entry.get("output_path")
    if not output_path_str:
        return False

    if output_dir is None:
        from utils.path_utils import OUTPUT_DIR
        output_dir = OUTPUT_DIR

    out_file = output_dir / output_path_str
    if not out_file.exists():
        return False

    try:
        with open(out_file, encoding="utf-8") as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Manifest I/O with identity-aware loading
# ---------------------------------------------------------------------------


def load_manifest() -> dict:
    """Load the manifest from disk.

    If the manifest file exists but fails JSON parsing (e.g. corrupted),
    logs a warning and returns an empty dict (fresh start).
    """
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError, OSError):
            logger.warning(
                "Manifest file %s exists but failed to parse; starting fresh",
                MANIFEST_FILE,
            )
            return {}
    return {}


def load_manifest_with_identity_check(
    current_identities: dict[str, ManifestIdentity],
    *,
    output_dir: Path | None = None,
) -> dict:
    """Load the manifest and reset stale or incomplete entries.

    For each entry in the manifest:
    1. If the PDF has a current identity and the entry's identity fields
       don't match, the entry is logged at INFO and its status is reset
       (treated as stale).
    2. If the entry is marked ``"complete"`` but its output file is missing
       or invalid, the entry is treated as incomplete (status reset).

    Args:
        current_identities: Mapping of pdf_name → ManifestIdentity for all
                            PDFs in the current run.
        output_dir: Directory where output files are stored. If None, uses
                    the default OUTPUT_DIR.

    Returns:
        The manifest dict with stale/incomplete entries reset.
    """
    manifest = load_manifest()

    for pdf_name, entry in list(manifest.items()):
        if not isinstance(entry, dict):
            continue

        # Check identity staleness
        if pdf_name in current_identities:
            identity = current_identities[pdf_name]
            if is_stale(entry, identity):
                logger.info(
                    "Manifest entry for %s is stale (identity mismatch); "
                    "resetting status for re-processing",
                    pdf_name,
                )
                manifest[pdf_name] = {"status": "pending"}
                continue

        # Check output file validity for completed entries
        if entry.get("status") == "complete":
            if not _is_output_valid(entry, output_dir=output_dir):
                logger.info(
                    "Manifest entry for %s is marked complete but output "
                    "file is missing or invalid; resetting for re-processing",
                    pdf_name,
                )
                manifest[pdf_name] = {"status": "pending"}

    return manifest


def save_manifest(manifest: dict) -> None:
    """Save the manifest atomically via temp file + os.replace.

    Uses the same atomic write pattern as _save_pdf_output: writes to a
    temporary file first, then renames to the final path. If the write
    fails before rename, the final manifest remains unchanged.
    """
    path = Path(MANIFEST_FILE)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        # Clean up temp file on any failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
