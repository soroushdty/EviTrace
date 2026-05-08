"""Manifest and output file I/O for the EviTrace pipeline."""
import json

from utils.logging_utils import get_logger
from utils.path_utils import MANIFEST_FILE, OUTPUT_DIR

logger = get_logger(__name__)


def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def save_pdf_output(pdf_name: str, fields: list[dict]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2)
    logger.info(f"Saved -> {out.name}")
