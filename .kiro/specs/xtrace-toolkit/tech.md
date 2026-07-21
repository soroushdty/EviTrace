# Tech Stack — Evidence-Traceability Toolkit (`xtrace`)

**Status:** Draft (Phase 1 — Tech)
**Traces:** `requirements.md`, `design.md`
**Language:** English

---

## 1. Foundation

| Concern | Choice | License | Notes |
|---|---|---|---|
| Language / runtime | **Python 3.12** | — | Inherited from EviTrace's pin |
| Env & packaging | **`uv`** workspace + `pyproject.toml` | — | Already used in EviTrace |
| Build backend | **`hatchling`** | MIT | Simple, PEP 621 native |
| Lint / format | **`ruff`** | MIT | Already in use (`noqa: PLC0415`) |
| Typing / contracts | **Pydantic v2** + `typing.Protocol` | MIT | Data contracts + adapter seams |
| Testing | **`pytest`** + **`hypothesis`** | MPL/BSD | House standard (property tests) |
| Docs | **`mkdocs-material`** | MIT | API-only product ⇒ docs matter |
| CI | **GitHub Actions** | — | Incl. a no-extras permissive-install job |

## 2. Per-layer dependencies

### `xtrace-pdf`
| Role | Library | License | Default? |
|---|---|---|---|
| Structural text + font + bbox | **pdfplumber** (→ pdfminer.six) | MIT | ✅ |
| Text / bbox / rasterization | **pypdfium2** | Apache/BSD | ✅ |
| Semantic structure (TEI) | **GROBID** via HTTP + Docker | Apache-2.0 | ✅ (optional service) |
| TEI parsing | stdlib `xml.etree` | — | ✅ |
| OCR | **PaddleOCR** or **pytesseract** | Apache-2.0 | `[ocr]` |
| High-fidelity font / OCR cross-check | **PyMuPDF** | **AGPL** | `[ocr]` only |
| Semantic fallback | **faiss-cpu** + **sentence-transformers** | MIT / Apache | `[semantic]` |

> **pypdfium2 replaces PyMuPDF on the default path.** PyMuPDF stays AGPL and
> opt-in. Avoid `pdf2image` (pulls GPL poppler) — rasterize with pypdfium2.

### `xtrace-qc`
| Role | Library | License |
|---|---|---|
| Schema validation | **jsonschema** (Draft 7) | MIT |
| Stats (bootstrap CIs, ICC) | **numpy** + **scipy** | BSD |
| Inter-rater agreement | **`krippendorff`** (α), `statsmodels` (κ) | BSD/MIT |

### `xtrace-llm`
| Role | Library | License |
|---|---|---|
| Provider routing | **LiteLLM** | MIT |
| Structured output + repair | **instructor** | MIT |
| Schemas | **Pydantic v2** | MIT |
| Self-hosted serving (recommended) | **vLLM** (or **Ollama**) OpenAI-compatible | Apache/MIT |
| Local fallback backend | **transformers** + **huggingface_hub** | Apache | `[local]` |
| Cache | **diskcache** | Apache |
| Retry/backoff | **tenacity** | Apache |

### `xtrace-gov`
| Role | Library | License |
|---|---|---|
| Provenance ledger | **SQLite** (stdlib) + JSONL export | — |
| Review CLI | **Typer** + **Rich** | MIT |
| Config | **pydantic-settings** + **PyYAML** | MIT |

## 3. The LLM decision (recommended: **Hybrid, served-first**)

- **One path for almost everything:** `instructor(LiteLLM(...))` returns
  Pydantic-validated objects and auto-repairs schema violations, across hosted
  APIs *and* self-hosted clinical models served behind an **OpenAI-compatible
  endpoint (vLLM / Ollama)**.
- **Fallback only:** the raw `transformers` batched-generation backend (ported
  from pdm) is used solely for models you can't serve, or notebook/no-server
  environments. This is where model-specific quirks (e.g. MedGemma thinking
  traces) are handled.
- **Why not pure-LiteLLM or pure-bespoke:** pure-hosted leaks on local clinical
  models; pure-bespoke means hand-maintaining provider plumbing that instructor
  already does better. Hybrid minimizes bespoke code to a single fallback.

Target clinical models (from EviTrace/pdm configs): BioMistral, MedGemma,
Meditron, Qwen2.5, ClinicalCamel, MediPhi — all HF/served; plus commercial
(GPT/Claude) via the same path.

## 4. Repository layout

```
xtrace/                       # LICENSE: MIT or Apache-2.0 (decision pending)
  pyproject.toml              # uv workspace; extras: [ocr] [semantic] [local] [all]
  packages/
    xtrace-core/              # Pydantic contracts (tiny, dep-free)
    xtrace-pdf/
    xtrace-qc/
    xtrace-llm/
    xtrace-gov/
  docs/                       # mkdocs-material
  .github/workflows/          # test matrix + permissive-install job
```

## 5. Licensing posture

- **Target:** MIT or Apache-2.0 for all first-party code (**decision pending** —
  Apache-2.0 preferred if any patent-grant value; MIT if minimal).
- **Constraints handled:** PyMuPDF (AGPL) and poppler (GPL) are opt-in/avoided;
  GROBID (Apache) runs as a separate service (no linking); scispacy/spacy
  (MIT/Apache), faiss (MIT), torch (BSD), sentence-transformers (Apache) are all
  compatible.
- **Provenance:** EviTrace is currently GPL-3.0 and single-author — relicense to
  the chosen permissive target when code migrates.

## 6. Known version constraints to reconcile

- **numpy:** EviTrace pins `<2.0` (scispacy 0.5.5); pdm uses numpy 2.x. The
  shared stack must settle on one — likely `<2.0` until scispacy supports 2.x,
  isolated to `[semantic]`/`[nlp]` extras so the core is unconstrained.
- **spaCy:** `>=3.7,<3.8` (scispacy). Keep in the `[nlp]` extra.
- **Python:** 3.12 only (matches EviTrace; some deps lag 3.13).
- **torch / transformers:** heavy — `[local]` extra only.

## 7. Tooling conventions

- `uv sync --extra all` for dev; `uv sync` for the permissive smoke test.
- Reproducibility manifest emitted per run (git SHA, seeds, resolved config,
  per-artifact SHA-256, pinned-vs-installed diff) — ported from pdm.
- Secrets: env vars only (`OPENAI_API_KEY`, `HF_TOKEN`); never committed.
