# Scoping Review Extraction Pipeline - OpenAI API version with cache prewarm

Automated attribute extraction from scientific literature.
Generates a structured JSON from PDFs using a multi-agent counterfactual design.

## Workflow

1. Extract PDF text locally with `pdfplumber`.
2. Run a tiny cache warmup call for each PDF using the chunk model.
3. Run chunks 1-4 in parallel with the OpenAI chunk model.
4. If the synthesis model differs, run a tiny synthesis-model warmup while chunks 1-4 are running.
5. Validate every chunk locally against expected field indices and schema.
6. Pass chunks 1-4 as read-only context to chunk 5.
7. Run chunk 5 with the OpenAI synthesis model.
8. Merge all 62 fields, save per-paper JSON, update the manifest, and generate QC files.

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

## Recommended model configuration

Maximum cache reuse is simplest when chunk and synthesis models match:

```bash
export OPENAI_CHUNK_MODEL="gpt-4.1"
export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
export OPENAI_PROMPT_CACHE_RETENTION="24h"
```

For cheaper testing:

```bash
export OPENAI_CHUNK_MODEL="gpt-4.1-mini"
export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
```

The pipeline will run a separate synthesis-model warmup during chunks 1-4 if the models differ.

## Cache controls

```bash
export OPENAI_ENABLE_CACHE_PREWARM=1
export OPENAI_PROMPT_CACHE_KEY_PREFIX="scoping-review-v1"
export OPENAI_CACHE_WARMUP_MAX_TOKENS=32
# export OPENAI_PROMPT_CACHE_RETENTION="24h"
# export OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF=1
```

Disable warmup without changing code:

```bash
python main.py --no-cache-prewarm
```

## Add PDFs

```bash
mkdir -p pdfs
cp /path/to/your/papers/*.pdf pdfs/
```

## Run

```bash
python main.py
python main.py --concurrency 2
python main.py --pdf-dir /data/papers
```

A `pipeline.log` file is written alongside the run. Look for lines like:

```text
[paper | warmup | gpt-4.1] tokens: input=..., cached=..., cache_hit=..., output=...
[paper | chunk 1 | gpt-4.1] tokens: input=..., cached=..., cache_hit=..., output=...
```

Expected cache pattern:

```text
warmup: cached can be 0
chunks 1-4: cached should be high
chunk 5: cached should be high if same model, or if synthesis warmup succeeded
```

## Output

| File                             | Description                                                       |
| -------------------------------- | ----------------------------------------------------------------- |
| `outputs/{paper}.extracted.json` | Per-paper 62-field extraction                                     |
| `outputs/qc_report.csv`          | Fields flagged for manual review, low confidence, or not reported |
| `manifest.json`                  | Progress checkpoint, safe to re-run after a crash                 |

## Architecture

```text
pdfs/paper.pdf
      |
      v
[Python] extract text once with pdfplumber
      |
      v
[OpenAI chunk model] tiny cache warmup, output ignored
      |
      +--- [OpenAI chunk model] Chunk 1  fields 1-15   domains 1-3
      +--- [OpenAI chunk model] Chunk 2  fields 16-25  domains 4-5
      +--- [OpenAI chunk model] Chunk 3  fields 26-44  domains 6-9
      +--- [OpenAI chunk model] Chunk 4  fields 45-56  domains 10-12
      |                               all run in parallel after warmup
      |
      +--- [optional] synthesis-model warmup if synthesis model differs
      |
      v
[Python] validate chunks 1-4
      |
      v
[OpenAI synthesis model] Chunk 5 fields 57-62, receives prior context after PDF text
      |
      v
[Python] merge 62 fields, sort by field_index, save JSON, QC report
```

Up to 3 PDFs run this flow concurrently by default. A global semaphore of 15 caps total concurrent OpenAI API calls across all active PDFs.

## Important cache notes

- The filename, run ID, timestamp, chunk number, and attempt number are not included before the PDF text.
- The same universal schema is sent for every chunk.
- The validator, not the API schema, enforces chunk-specific field indices.
- Warmup failure does not fail the run; extraction continues normally.
- Cached token counts are the source of truth. Review `pipeline.log` after the run.

## GPT-5.5 note

Some GPT-5.5 accounts/models reject the `temperature` parameter. This build omits `temperature` unless you explicitly set `OPENAI_TEMPERATURE`. For GPT-5.5, leave `OPENAI_TEMPERATURE` unset.
