"""
pdf_extractor.py
----------------
CLI entry point for the pdf_extractor parser pipeline.

Resolves one or more PDF sources, extracts text through a four-tier cascade
(PyMuPDF → pdfplumber → Tesseract → PaddleOCR), processes the extracted text
into sentence records, and writes structured parser artifacts to the output
folder.

Usage
-----
    python -m pdf_extractor.pdf_extractor                          # uses the default config file
    python -m pdf_extractor.pdf_extractor --config /path/to/cfg   # explicit config path
"""

import argparse
import json
import os
import time
from pathlib import Path

from .extraction import extract_pdf
from .processing import sentence_processor
from utils import path_utils
from utils.config_utils import load_config
from utils.logging_utils import setup_logging


def _save_artifact(output_folder: str, pdf_name: str, artifact: dict) -> str:
    """Write a single parser artifact JSON file and return its path."""
    stem = Path(pdf_name).stem
    out_path = Path(output_folder) / f"{stem}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)
    return str(out_path)


def run_pipeline(config_path: str) -> None:
    cfg = load_config(config_path)

    logger = setup_logging(
        log_file=cfg["log_file"],
        console_level=cfg["log_level"],
    )
    logger.info("pdf_extractor started | config=%s", config_path)

    # ------------------------------------------------------------------ #
    # Step 1 – Resolve sources                                            #
    # ------------------------------------------------------------------ #
    pdf_sources_root, pdf_files = path_utils.list_pdf_files_from_source(cfg["pdfs_path"])
    output_folder = path_utils.create_output_folder(cfg["output_folder_path"])

    logger.info("PDF sources root: %s", pdf_sources_root)
    logger.info("Output folder: %s", output_folder)
    logger.info("PDFs found: %d", len(pdf_files))

    ocr: bool = cfg["ocr"]
    ocr_threshold: float = cfg["ocr_text_quality_threshold"]
    len_filter: int = cfg["len_filter"]

    # ------------------------------------------------------------------ #
    # Step 2 – Process each PDF                                           #
    # ------------------------------------------------------------------ #
    for pdf_name, pdf_info in pdf_files.items():
        pdf_path = pdf_info["local_path"]
        logger.info("Processing: %s", pdf_name)

        t0 = time.time()
        try:
            # Step 3 – Extract text (three-tier cascade)
            blocks, font_metadata = extract_pdf(pdf_path, ocr, ocr_threshold)
        except Exception as exc:
            logger.error("Extraction failed | pdf=%s | error=%s", pdf_name, exc)
            continue

        logger.info(
            "Extraction complete | pdf=%s | elapsed=%.1fs | blocks=%d",
            pdf_name, time.time() - t0, len(blocks),
        )

        # Step 4 – Process sentences and assemble full text
        sentence_records = sentence_processor.process_sentences(blocks, len_filter)
        full_pdf_text, page_texts = sentence_processor.build_full_text(blocks)

        logger.info(
            "Sentences processed | pdf=%s | sentences=%d",
            pdf_name, len(sentence_records),
        )

        # Step 5 – Save parser artifact
        artifact = {
            "pdf_name": pdf_name,
            "pdf_id": pdf_info["id"],
            "pdf_uri": pdf_info["uri"],
            "blocks": blocks,
            "sentence_records": sentence_records,
            "full_pdf_text": full_pdf_text,
            "page_texts": page_texts,
        }
        out_path = _save_artifact(output_folder, pdf_name, artifact)
        logger.info("Artifact saved: %s", out_path)

    logger.info("pdf_extractor pipeline complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pdf_extractor PDF parser pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=path_utils.resolve_project_path("config/config.yaml"),
        metavar="PATH",
        help="Path to the pipeline config file",
    )
    args = parser.parse_args()

    # Resolve config path relative to the project root when necessary
    config_path = path_utils.resolve_project_path(args.config)
    if not Path(config_path).exists():
        parser.error(f"Config file not found: {config_path}")

    run_pipeline(config_path)


if __name__ == "__main__":
    main()
