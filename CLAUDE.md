# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

EviTrace ingests biomedical/scientific PDFs and produces structured, auditable per-paper JSON via a pipeline of independent, swappable stages: multi-backend PDF text extraction → four-stage QC/adjudication → W3C JSON-LD annotation → chunked LLM extraction (OpenAI Responses API) against a user-defined field map. Each stage (extractor, QC, LLM agent) is usable standalone.

Source lives under `src/` (a `src/`-layout package); `configs/` and `tests/` live at the repo root alongside it, and Kiro specs/steering live under `.kiro/`.

## Commands

```bash
# Setup (pinned to Python 3.12.x — pip rejects other interpreters via requirements.txt guard)
uv venv --python 3.12 .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."

# Run the end-to-end pipeline
python main.py
python main.py --pdf-dir /data/papers
python main.py --concurrency 2          # dial down if hitting rate limits
python main.py --no-cache-prewarm

# Run only the PDF extractor (no OpenAI key required)
python -m pdf_extractor.pdf_extractor
python -m pdf_extractor.pdf_extractor --config /path/to/config.yaml

# Tests — always from repo root; pytest config is in pyproject.toml (pythonpath = "src")
python -m pytest -q                     # fast suite (slow tests deselected by default)
python -m pytest -q -m slow             # slow tests only
python -m pytest -q -m ""               # everything
python -m pytest tests/src/quality_control/test_qc_models.py -q   # single file
python -m pytest tests/src/quality_control/test_qc_models.py::test_name -q  # single test
```

There is no configured linter/formatter in this repo (no ruff/flake8/mypy config) — don't invent lint commands.

## Architecture

```
main.py  (CLI entry — asyncio.run)
  └── src/pipeline/orchestrator.py          # async run_pipeline(); PDF-level concurrency
        ├── src/pipeline/extraction_pipeline.py  # build_qc_bundle(): single source of truth for
        │                                        # scan detection → backend routing → QC → annotation
        ├── src/pdf_extractor/               # multi-backend extraction (GROBID, PyMuPDF, pdfplumber, PaddleOCR)
        ├── src/text_processing/             # standalone text package: normalizers, tokenizers, matchers, embedding
        ├── src/quality_control/             # generic 4-stage QC: rater → IAA → adjudicator → reconciler
        ├── src/agents/openai/                # Responses API client + prompt builders
        ├── src/pipeline/evidence_index.py   # GROBID TEI → ranked evidence bundle + disk cache
        └── src/utils/                       # config_utils, path_utils, logging_utils, grobid_manager
```

### Per-PDF flow

1. **Scan detection** — `scan_detector.classify_page()`, a stateless function running 5 sequential stages (empty text, low word count, low alpha-char ratio, zero embedded fonts, image-area dominance). Page is `native` only if none fire.
2. **Backend routing** (centralized in `extraction_pipeline.py::build_qc_bundle()`) — backends are complementary, never competing:
   - Native pages → GROBID (semantic authority, TEI XML) + pdfplumber (structural blocks); PyMuPDF font metadata stored separately.
   - Scanned pages + `ocr=true` → PaddleOCR (primary) + PyMuPDF built-in OCR (cross-validator). Standalone Tesseract is never used.
   - Scanned pages + `ocr=false` → skipped, logged as WARNING.
3. **Quality control** — `run_quality_control()` runs branches through local heuristics + exact-match search, reconciling a `UnifiedRecord`. Semantic search (FAISS) is scaffolded but disabled by default.
4. **QC-to-LLM handoff guard** — `validate_qc_context_input()` (`src/quality_control/validate_context.py`) does pre-flight checks before extraction starts.
5. **Evidence index** — `build_or_load_evidence_bundle()` parses GROBID TEI XML into a ranked, section-scored index, cached to disk by `{paper_id}_{pdf_hash}`. Fields 1–2 (author, year) are pre-filled from TEI metadata and never sent to the LLM.
6. **Cache prewarm** (optional) — tiny call warms the shared `(system + evidence package)` prefix.
7. **Parallel extraction chunks** — chunks `1..N-1` run concurrently with per-chunk evidence packages.
8. **Local validation** — each chunk's JSON checked against expected `field_index` set, key schema, confidence enum, `loc` ID membership.
9. **Synthesis chunk** — final chunk runs with prior chunk results as read-only context.
10. **Persist** — merged fields → `outputs/<paper>.extracted.json`; manifest marked complete.
11. **QC report** — `generate_qc_report()` writes `outputs/qc_report.csv`.

### Key design principles

- **No global mutation.** Config loaded once (`src/utils/config_utils.py`), passed explicitly. CLI overrides are forwarded as parameters to `run_pipeline()`, never written back to module-level constants.
- **Dependency direction is enforced by tests** (`tests/test_dependency_directions.py`, AST-based): `pdf_extractor` must not import `quality_control`; `quality_control` must not import `agents`/`pipeline`/`pdf_extractor`; `agents` must not import `quality_control`/`pipeline`/`pdf_extractor`; `text_processing` must not import `quality_control`. Run this suite after adding any cross-package import.
- **Schema validators are singletons with a single owner:** `configs/agent_schema.json` → `src/agents/validator.py::AgentSchemaValidator`; `configs/structure_schema.json` → `src/quality_control/structure_validator.py::StructureSchemaValidator`. Never read these files elsewhere.
- **All shared QC dataclasses live in `src/quality_control/models.py`** — import from there, never from individual submodules. Concrete default implementations (`QualityReport`, `InterRaterReport`, `AdjudicationDecision`) live in `src/quality_control/builtin_impls/`.
- **W3C annotations are produced in one place** — `src/artifact_generation/w3c_annotation.py` is the sole producer (`AnnotationRecord`, `project()`, `generate_w3c_jsonld()`). `project(unified)` reads only `unified.semantic` and `unified.alignment`.
- **Prompt cache stability** — `_shared_paper_prefix` (in `src/agents/openai/prompts.py`) must be byte-identical across warmup, extraction chunks, and synthesis for the same PDF. Never inject filenames, timestamps, chunk numbers, or run IDs into the shared prefix; variable material goes after it.
- **Heavy optional deps are lazy** — `sentence-transformers`, `faiss`, `torch`, `paddleocr` are imported inside function bodies, never at module level. Don't add top-level imports of these.
- **OOP extensibility via ABCs**: `QualityMetrics`, `InterRaterMetrics`, `AdjudicationRules`, `TextProcessor`/`SentenceSegment` (loaded via fully-qualified class path in config), concern strategies (`TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern`).
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`, or `load_local_config` raises `ValueError`.

### Config

Single source of truth: `configs/config.yaml` (note: `configs/`, not `config/`). Loaded via `src/utils/config_utils.py`'s `load_openai_config()` / `load_qc_config()` / `load_local_config()`. Override rule: **env > yaml > default** — all `openai.*` keys can be overridden via env vars (`OPENAI_API_KEY`, `OPENAI_CHUNK_MODEL`, `OPENAI_SYNTHESIS_MODEL`, `OPENAI_NUM_CHUNKS`, etc.). Full key reference: `.kiro/steering/config.md`.

`configs/extraction_map.json` defines the 62 canonical extraction fields (13 domain groups); `configs/agent_schema.json` holds the LLM system prompt/policies; `configs/structure_schema.json` is the JSON Schema (Draft 7) for pipeline dataclasses.

### Testing conventions

- Two `conftest.py` files put `src/` on `sys.path` (repo root, and `src/pdf_extractor/`) — both must exist.
- Test tree mirrors `src/` under `tests/src/` (e.g. `tests/src/quality_control/` mirrors `src/quality_control/`). `tests/steering/` holds cross-cutting architectural-boundary tests. Root-level `tests/test_*.py` files enforce dependency direction and migration/regression contracts.
- Naming: `test_<module-or-feature>_<aspect>.py`.
- Slow tests: `pytestmark = pytest.mark.slow` at module level (deselected by default).
- Never call real GROBID, OpenAI, or PaddleOCR in unit tests — mock heavy deps (`faiss`, `torch`, `sentence-transformers`, `paddleocr`) via `patch.dict(sys.modules, {...})`.
- Full conventions, mocking patterns, and QCBundle test-construction examples: `.kiro/steering/testing.md`.

### Steering docs and specs

`.kiro/steering/` (`product.md`, `config.md`, `testing.md`, `changelog-rules.md`, `roadmap.md`) are Kiro spec-workflow steering docs (`inclusion: always`) kept in sync with the code — more current and more detailed than this file for deep dives; consult them before non-trivial changes. `.kiro/steering/roadmap.md` is the plan of record: it holds the spec decomposition in dependency order, the cross-cutting NFRs, the standing product boundaries, and the unresolved open questions. `.kiro/specs/<feature>/` holds active specs (each with `brief.md`, then `requirements.md` → `design.md` → `tasks.md`). `.kiro/specs/archive/` holds completed specs plus `original-idea-documents/`, the superseded source documents the briefs still cite — treat everything under `archive/` as historical, not current.

`CHANGELOG.md` at the repo root is permanent — never delete or truncate it. Follow `.kiro/steering/changelog-rules.md` for when/how to add an entry.


# Agentic SDLC and Spec-Driven Development

Kiro-style Spec-Driven Development on an agentic SDLC

## Project Context

### Paths
- Steering: `.kiro/steering/`
- Specs: `.kiro/specs/`

### Steering vs Specification

**Steering** (`.kiro/steering/`) - Guide AI with project-wide rules and context
**Specs** (`.kiro/specs/`) - Formalize development process for individual features

### Active Specifications
- Check `.kiro/specs/` for active specifications
- Use `/kiro-spec-status [feature-name]` to check progress

## Development Guidelines
- Think in English, generate responses in English. All Markdown content written to project files (e.g., requirements.md, design.md, tasks.md, research.md, validation reports) MUST be written in the target language configured for this specification (see spec.json.language).

## Minimal Workflow
- Phase 0 (optional): `/kiro-steering`, `/kiro-steering-custom`
- Discovery: `/kiro-discovery "idea"` — determines action path, writes brief.md + roadmap.md for multi-spec projects
- Phase 1 (Specification):
  - Single spec: `/kiro-spec-quick {feature} [--auto]` or step by step:
    - `/kiro-spec-init "description"`
    - `/kiro-spec-requirements {feature}`
    - `/kiro-validate-gap {feature}` (optional: for existing codebase)
    - `/kiro-spec-design {feature} [-y]`
    - `/kiro-validate-design {feature}` (optional: design review)
    - `/kiro-spec-tasks {feature} [-y]`
  - Multi-spec: `/kiro-spec-batch` — creates all specs from roadmap.md in parallel by dependency wave
- Phase 2 (Implementation): `/kiro-impl {feature} [tasks]`
  - Without task numbers: autonomous mode (subagent per task + independent review + final validation)
  - With task numbers: manual mode (selected tasks in main context, still reviewer-gated before completion)
  - `/kiro-validate-impl {feature}` (standalone re-validation)
- Progress check: `/kiro-spec-status {feature}` (use anytime)

## Skills Structure
Skills are located in `.claude/skills/kiro-*/SKILL.md`
- Each skill is a directory with a `SKILL.md` file
- Skills run inline with access to conversation context
- Skills may delegate parallel research to subagents for efficiency
- Additional files (templates, examples) can be added to skill directories
- `kiro-review` — task-local adversarial review protocol used by reviewer subagents
- `kiro-debug` — root-cause-first debug protocol used by debugger subagents
- `kiro-verify-completion` — fresh-evidence gate before success or completion claims
- **If there is even a 1% chance a skill applies to the current task, invoke it.** Do not skip skills because the task seems simple.

## Development Rules
- 3-phase approval workflow: Requirements → Design → Tasks → Implementation
- Human review required each phase; use `-y` only for intentional fast-track
- Keep steering current and verify alignment with `/kiro-spec-status`
- Follow the user's instructions precisely, and within that scope act autonomously: gather the necessary context and complete the requested work end-to-end in this run, asking questions only when essential information is missing or the instructions are critically ambiguous.

## Steering Configuration
- Load entire `.kiro/steering/` as project memory
- Default files: `product.md`, `tech.md`, `structure.md`
- Custom files are supported (managed via `/kiro-steering-custom`)
