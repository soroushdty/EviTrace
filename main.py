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
from pipeline.extraction_report import generate_qc_report
from utils.config_utils import load_openai_config, load_local_config
from utils.path_utils import PDF_DIR
from utils.logging_utils import get_logger, setup_logging
from utils.grobid_manager import GrobidServerManager

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
    parser.add_argument(
        "--tear-down-grobid",
        action="store_true",
        help=(
            "Stop the persistent GROBID container on exit. Default: leave it "
            "running between invocations to preserve JVM + CRF model warmup."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    import time as _time
    _t_start = _time.time()

    args = parse_args()
    logger.debug("Parsed CLI args: %r", vars(args))
    cfg = load_openai_config()
    logger.debug(
        "Loaded OpenAI config: chunk_model=%s, synthesis_model=%s, num_chunks=%s, "
        "chunk_max_tokens=%s, global_api_limit=%s, pdf_concurrency=%s, base_url=%s",
        cfg.get("chunk_model"),
        cfg.get("synthesis_model"),
        cfg.get("num_chunks"),
        cfg.get("chunk_max_tokens"),
        cfg.get("global_api_limit"),
        cfg.get("pdf_concurrency"),
        cfg.get("base_url"),
    )

    if args.concurrency is not None:
        logger.info(f"PDF concurrency overridden to {args.concurrency}")
    if args.no_cache_prewarm:
        logger.info("Cache prewarm disabled by CLI flag")

    # Validate API key.
    if not cfg["api_key"]:
        logger.error("OPENAI_API_KEY is not set. Export it before running.")
        sys.exit(1)
    logger.debug("OpenAI API key present (length=%d)", len(cfg["api_key"]))

    # Discover PDFs.
    pdf_dir = args.pdf_dir
    if not pdf_dir.exists():
        logger.error(f"PDF directory not found: {pdf_dir}")
        sys.exit(1)

    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_paths:
        logger.error(f"No PDF files found in {pdf_dir}")
        sys.exit(1)
    logger.debug(
        "Discovered %d PDFs in %s: %s",
        len(pdf_paths),
        pdf_dir,
        [p.name for p in pdf_paths],
    )

    # Resolve effective values for logging (mirrors orchestrator defaults).
    effective_concurrency = args.concurrency if args.concurrency is not None else pipeline.PDF_CONCURRENCY
    effective_prewarm = False if args.no_cache_prewarm else pipeline.ENABLE_CACHE_PREWARM

    logger.info(f"Found {len(pdf_paths)} PDFs in {pdf_dir}")
    logger.info(f"PDF concurrency     : {effective_concurrency}")
    logger.info(f"API concurrency     : {pipeline.GLOBAL_API_LIMIT}")
    logger.info(f"Chunk model         : {pipeline.CHUNK_MODEL}")
    logger.info(f"Synthesis model     : {pipeline.SYNTHESIS_MODEL}")
    logger.info(f"Cache prewarm       : {effective_prewarm}")
    logger.info(f"Cache key prefix    : {cfg['prompt_cache_key_prefix']}")
    logger.info(f"Cache retention     : {cfg['prompt_cache_retention'] or 'default'}")
    logger.info(f"Synthesis prewarm   : {pipeline.PREWARM_SYNTHESIS_IF_MODEL_DIFF}")

    from utils.path_utils import OUTPUT_DIR
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Run pipeline — pass CLI overrides as arguments instead of mutating globals.
    if args.tear_down_grobid:
        # Surface CLI intent to GrobidServerManager via config override.
        local_cfg.setdefault("quality_control", {}).setdefault("grobid", {})["stop_on_exit"] = True
    with GrobidServerManager(local_cfg):
        results = await pipeline.run_pipeline(
            pdf_paths,
            pdf_concurrency=args.concurrency,
            enable_cache_prewarm=False if args.no_cache_prewarm else None,
        )

    if not results:
        logger.error("No PDFs were successfully processed.")
        sys.exit(1)

    generate_qc_report(results, elapsed_seconds=_time.time() - _t_start)


if __name__ == "__main__":
    asyncio.run(main())
