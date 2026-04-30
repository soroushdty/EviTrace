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
import logging
import sys
from pathlib import Path

import config  # imported as module so CLI flags can patch it at runtime
import orchestrator
from qc_report import generate_qc_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scoping review PDF to JSON extraction pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=config.PDF_DIR,
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

    # Runtime override of concurrency without editing config.py.
    if args.concurrency is not None:
        config.PDF_CONCURRENCY = args.concurrency
        orchestrator.PDF_CONCURRENCY = args.concurrency
        logger.info(f"PDF concurrency overridden to {args.concurrency}")

    if args.no_cache_prewarm:
        config.ENABLE_CACHE_PREWARM = False
        orchestrator.ENABLE_CACHE_PREWARM = False
        logger.info("Cache prewarm disabled by CLI flag")

    # Validate API key.
    if not config.OPENAI_API_KEY:
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
    logger.info(f"PDF concurrency     : {config.PDF_CONCURRENCY}")
    logger.info(f"API concurrency     : {config.GLOBAL_API_LIMIT}")
    logger.info(f"Chunk model         : {config.CHUNK_MODEL}")
    logger.info(f"Synthesis model     : {config.SYNTHESIS_MODEL}")
    logger.info(f"Cache prewarm       : {config.ENABLE_CACHE_PREWARM}")
    logger.info(f"Cache key prefix    : {config.PROMPT_CACHE_KEY_PREFIX}")
    logger.info(f"Cache retention     : {config.PROMPT_CACHE_RETENTION or 'default'}")
    logger.info(f"Synthesis prewarm   : {config.PREWARM_SYNTHESIS_IF_MODEL_DIFF}")
    config.OUTPUT_DIR.mkdir(exist_ok=True)

    # Run pipeline.
    results = await orchestrator.run_pipeline(pdf_paths)

    if not results:
        logger.error("No PDFs were successfully processed.")
        sys.exit(1)

    # QC report and master output.
    generate_qc_report(results)


if __name__ == "__main__":
    asyncio.run(main())
