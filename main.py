"""
Scoping Review Extraction Pipeline - OpenAI entry point.

Usage:
    python main.py                          # use pdfs/ folder, default concurrency
    python main.py --pdf-dir /path/to/pdfs
    python main.py --concurrency 2          # dial down if hitting 429s
    python main.py --no-cache-prewarm       # disable warmup calls
"""
import argparse
import asyncio
import sys
from pathlib import Path

import pipeline
from qc_report import generate_qc_report
from utils.config_utils import load_openai_config, load_local_config
from utils.path_utils import PDF_DIR
from utils.logging_utils import get_logger, setup_logging

# Initialize logging at startup using config values (idempotent)
local_cfg = load_local_config(None)
setup_logging(log_file=local_cfg.get("log_file", "pipeline.log"), console_level=local_cfg.get("log_level", "INFO"))
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scoping review PDF to JSON extraction pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=PDF_DIR,
        help="Directory containing PDF files",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override PDF_CONCURRENCY (number of PDFs processed in parallel)",
    )
    parser.add_argument(
        "--no-cache-prewarm",
        action="store_true",
        help="Disable one-call-per-PDF cache warmup; extraction still works",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    cfg = load_openai_config()

    # Runtime override of concurrency without editing config files.
    if args.concurrency is not None:
        pipeline.PDF_CONCURRENCY = args.concurrency
        logger.info(f"PDF concurrency overridden to {args.concurrency}")

    if args.no_cache_prewarm:
        pipeline.ENABLE_CACHE_PREWARM = False
        logger.info("Cache prewarm disabled by CLI flag")

    # Validate API key.
    if not cfg["api_key"]:
        logger.error("OPENAI_API_KEY is not set. Export it before running.")
        sys.exit(1)

    # Discover PDFs.
    pdf_dir = args.pdf_dir
    if not pdf_dir.exists():
        logger.error(f"PDF directory not found: {pdf_dir}")
        sys.exit(1)

    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_paths:
        logger.error(f"No PDF files found in {pdf_dir}")
        sys.exit(1)

    logger.info(f"Found {len(pdf_paths)} PDFs in {pdf_dir}")
    logger.info(f"PDF concurrency     : {pipeline.PDF_CONCURRENCY}")
    logger.info(f"API concurrency     : {pipeline.GLOBAL_API_LIMIT}")
    logger.info(f"Chunk model         : {pipeline.CHUNK_MODEL}")
    logger.info(f"Synthesis model     : {pipeline.SYNTHESIS_MODEL}")
    logger.info(f"Cache prewarm       : {pipeline.ENABLE_CACHE_PREWARM}")
    logger.info(f"Cache key prefix    : {cfg['prompt_cache_key_prefix']}")
    logger.info(f"Cache retention     : {cfg['prompt_cache_retention'] or 'default'}")
    logger.info(f"Synthesis prewarm   : {pipeline.PREWARM_SYNTHESIS_IF_MODEL_DIFF}")
    from utils.path_utils import OUTPUT_DIR
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Run pipeline.
    results = await pipeline.run_pipeline(pdf_paths)

    if not results:
        logger.error("No PDFs were successfully processed.")
        sys.exit(1)

    # QC report and master output.
    generate_qc_report(results)


if __name__ == "__main__":
    asyncio.run(main())
