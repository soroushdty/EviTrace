"""Configuration constants for the OpenAI scoping review extraction pipeline."""
import os
from pathlib import Path

# -- API ---------------------------------------------------------------------
# The OpenAI SDK will also read OPENAI_API_KEY from the environment, but keeping
# it explicit lets main.py fail early with a clear message.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "") or None

# Model choices. Override without editing code, e.g.:
#   export OPENAI_CHUNK_MODEL="gpt-4.1"
#   export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
CHUNK_MODEL = os.environ.get("OPENAI_CHUNK_MODEL", "gpt-5.5")
SYNTHESIS_MODEL = os.environ.get("OPENAI_SYNTHESIS_MODEL", "gpt-5.5")

# Some newer OpenAI models reject the temperature parameter entirely.
# Leave OPENAI_TEMPERATURE unset by default so requests omit it.
_TEMP_RAW = os.environ.get("OPENAI_TEMPERATURE", "").strip()
TEMPERATURE = float(_TEMP_RAW) if _TEMP_RAW else None

# Prompt-cache configuration.
# Prompt caching itself is automatic on supported OpenAI models for long,
# repeated prefixes. This workflow adds one cheap warmup call per PDF and uses
# a per-paper prompt_cache_key derived from the extracted PDF text. Do not use a
# single global cache key for all PDFs.
PROMPT_CACHE_KEY_PREFIX = os.environ.get("OPENAI_PROMPT_CACHE_KEY_PREFIX", "scoping-review-v1")
PROMPT_CACHE_RETENTION = os.environ.get("OPENAI_PROMPT_CACHE_RETENTION", "in_memory")  # e.g. "24h", "in_memory"
ENABLE_CACHE_PREWARM = os.environ.get("OPENAI_ENABLE_CACHE_PREWARM", "1").lower() not in {"0", "false", "no"}
CACHE_WARMUP_MAX_TOKENS = int(os.environ.get("OPENAI_CACHE_WARMUP_MAX_TOKENS", "32"))

# If chunk and synthesis models differ, start a second tiny warmup for the
# synthesis model while prior chunks are running. This usually avoids extra wall
# time and improves final chunk cache eligibility.
PREWARM_SYNTHESIS_IF_MODEL_DIFF = os.environ.get(
    "OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF", "1"
).lower() not in {"0", "false", "no"}

# -- Chunk configuration -----------------------------------------------------
# Number of extraction chunks. The last chunk is synthesis/review and receives
# prior context from all preceding chunks. Default is 3:
#   Chunk 1: domains 1-5
#   Chunk 2: domains 6-12
#   Chunk 3: domain 13 (synthesis/review)
# Override with environment variable: export OPENAI_NUM_CHUNKS=5
NUM_CHUNKS = int(os.environ.get("OPENAI_NUM_CHUNKS", "3"))

# Max output tokens per chunk. These caps are intentionally generous for about
# 30 words each in extracted_value and evidence while avoiding truncation/retry.
def _get_chunk_max_tokens() -> dict[int, int]:
    """Generate appropriate max tokens per chunk based on NUM_CHUNKS."""
    if NUM_CHUNKS == 5:
        return {1: 3500, 2: 3000, 3: 5000, 4: 3500, 5: 2500}
    elif NUM_CHUNKS == 3:
        return {1: 3500, 2: 5000, 3: 2500}
    else:
        # For other values, distribute tokens evenly
        base_tokens = 3500
        return {i: base_tokens for i in range(1, NUM_CHUNKS + 1)}

CHUNK_MAX_TOKENS = _get_chunk_max_tokens()

# -- Concurrency -------------------------------------------------------------
PDF_CONCURRENCY = 3       # PDFs processed in parallel
GLOBAL_API_LIMIT = 15     # max concurrent OpenAI API calls across all PDFs

# -- Retry -------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5      # seconds; actual delay = base * 2^(attempt - 1)

def _get_domain_to_chunk() -> dict[int, int]:
    """Generate domain-to-chunk mapping based on NUM_CHUNKS.

    Domains are distributed to chunks with the last chunk reserved for synthesis/review.
    """
    if NUM_CHUNKS == 5:
        return {
            1: 1,   # 1. Study identification
            2: 1,   # 2. Clinical and study-design context
            3: 1,   # 3. Cohort and data source
            4: 2,   # 4. Prediction task
            5: 2,   # 5. Data inputs and modalities
            6: 3,   # 6. Graph structure and topology
            7: 3,   # 7. Node specification
            8: 3,   # 8. Edge specification
            9: 3,   # 9. Temporal representation
            10: 4,  # 10. Model architecture
            11: 4,  # 11. Evaluation and generalizability
            12: 4,  # 12. Interpretability and representation
            13: 5,  # 13. Reviewer assessment and critique (synthesis)
        }
    elif NUM_CHUNKS == 3:
        return {
            1: 1,   # 1. Study identification
            2: 1,   # 2. Clinical and study-design context
            3: 1,   # 3. Cohort and data source
            4: 1,   # 4. Prediction task
            5: 1,   # 5. Data inputs and modalities
            6: 2,   # 6. Graph structure and topology
            7: 2,   # 7. Node specification
            8: 2,   # 8. Edge specification
            9: 2,   # 9. Temporal representation
            10: 2,  # 10. Model architecture
            11: 2,  # 11. Evaluation and generalizability
            12: 2,  # 12. Interpretability and representation
            13: 3,  # 13. Reviewer assessment and critique (synthesis)
        }
    else:
        # For other values, distribute domains evenly across NUM_CHUNKS-1 extraction chunks,
        # with the last chunk reserved for synthesis.
        mapping = {}
        chunks_for_extraction = NUM_CHUNKS - 1
        domains_per_chunk = 12 // chunks_for_extraction  # Domains 1-12
        remainder = 12 % chunks_for_extraction

        domain = 1
        for chunk_num in range(1, chunks_for_extraction + 1):
            domains_in_this_chunk = domains_per_chunk + (1 if chunk_num <= remainder else 0)
            for _ in range(domains_in_this_chunk):
                mapping[domain] = chunk_num
                domain += 1
        mapping[13] = NUM_CHUNKS  # Synthesis chunk
        return mapping

DOMAIN_TO_CHUNK = _get_domain_to_chunk()

# -- Validation --------------------------------------------------------------
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "e", "c"}

# -- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
EXTRACTION_MAP = BASE_DIR / "extraction_map.json"
PDF_DIR = BASE_DIR / "pdfs"
OUTPUT_DIR = BASE_DIR / "outputs"
MANIFEST_FILE = BASE_DIR / "manifest.json"
QC_REPORT_FILE = BASE_DIR / "outputs" / "qc_report.csv"
