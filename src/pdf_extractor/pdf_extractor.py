"""
pdf_extractor.py
----------------
Self-sufficient CLI entry point for the pdf_extractor module.

Runs the full multi-backend extraction pipeline for one or more PDFs:
per-page scan detection, backend routing (GROBID + pdfplumber for native
pages; PaddleOCR + PyMuPDF for scanned pages), QC pipeline, and writes
a structured ``UnifiedRecord``-based JSON artifact per PDF.

This CLI is independent of ``main.py``.  It does not require an OpenAI API
key and produces extraction artifacts without LLM-based field extraction.

Usage
-----
    python -m pdf_extractor.pdf_extractor                          # default config
    python -m pdf_extractor.pdf_extractor --config /path/to/cfg   # explicit config
"""

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from utils import path_utils
from utils.config_utils import load_local_config, load_qc_config
from utils.logging_utils import setup_logging
from utils.grobid_manager import GrobidServerManager

from pipeline.extraction_pipeline import build_qc_bundle
from artifact_generation import unified_to_artifact, save_artifact


def _unified_to_artifact(pdf_name: str, pdf_info: dict, ctx) -> dict:
    """Serialise a QCBundle into a JSON-serialisable artifact dict."""
    return unified_to_artifact(pdf_name, pdf_info, ctx)


def _save_artifact(output_folder: str, pdf_name: str, artifact: dict) -> str:
    """Write a single extraction artifact JSON file and return its path."""
    return save_artifact(output_folder, pdf_name, artifact)


def run_pipeline(config_path: str) -> None:
    """Run the full extraction pipeline for all PDFs in the configured source."""
    local_cfg = load_local_config(config_path)
    qc_cfg = load_qc_config(config_path)

    logger = setup_logging(
        log_file=local_cfg["log_file"],
        console_level=local_cfg["log_level"],
    )
    logger.info("pdf_extractor started | config=%s", config_path)

    # ------------------------------------------------------------------ #
    # Step 1 – Resolve sources                                            #
    # ------------------------------------------------------------------ #
    pdf_sources_root, pdf_files = path_utils.list_pdf_files_from_source(
        local_cfg["pdfs_path"]
    )
    output_folder = path_utils.create_output_folder(local_cfg["output_folder_path"])

    logger.info("PDF sources root : %s", pdf_sources_root)
    logger.info("Output folder    : %s", output_folder)
    logger.info("PDFs found       : %d", len(pdf_files))

    # ------------------------------------------------------------------ #
    # Step 2 – Process each PDF (inside GROBID lifecycle)                 #
    # ------------------------------------------------------------------ #
    with GrobidServerManager(local_cfg):
        for pdf_name, pdf_info in pdf_files.items():
            pdf_path = Path(pdf_info["local_path"])
            logger.info("Processing: %s", pdf_name)
            t0 = time.time()

            try:
                ctx = build_qc_bundle(pdf_path, pdf_name, qc_cfg)
            except Exception as exc:
                logger.error(
                    "Extraction failed | pdf=%s | error=%s", pdf_name, exc
                )
                continue

            elapsed = time.time() - t0
            branch_summary = ", ".join(
                f"{b.source}={b.status}" for b in ctx.branches
            )
            logger.info(
                "Extraction complete | pdf=%s | elapsed=%.1fs | branches=[%s]",
                pdf_name, elapsed, branch_summary,
            )

            # ---------------------------------------------------------- #
            # Step 3 – Save artifact                                      #
            # ---------------------------------------------------------- #
            artifact = _unified_to_artifact(pdf_name, pdf_info, ctx)
            out_path = _save_artifact(output_folder, pdf_name, artifact)
            logger.info("Artifact saved: %s", out_path)

    logger.info("pdf_extractor pipeline complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "pdf_extractor — full multi-backend PDF extraction pipeline.\n"
            "Produces UnifiedRecord-based JSON artifacts without requiring "
            "an OpenAI API key."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=path_utils.resolve_project_path("configs/config.yaml"),
        metavar="PATH",
        help="Path to the pipeline config file (default: configs/config.yaml)",
    )
    args = parser.parse_args()

    config_path = path_utils.resolve_project_path(args.config)
    if not Path(config_path).exists():
        parser.error(f"Config file not found: {config_path}")

    run_pipeline(config_path)


if __name__ == "__main__":
    main()
