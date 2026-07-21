# Research — Evidence-Traceability Toolkit (`xtrace`)

**Status:** Draft (Phase 1 — Research)
**Language:** English
**Purpose:** Record the investigation of the four source repositories, the
lineage findings, what is reusable, and the decisions (and rejected
alternatives) that shaped this spec.

---

## 1. Method

Full read of four repositories under one owner: `obsidian-vault` (the "Lydia"
agentic PKM system), `EviTrace`, `lit_synth`, and `pdm`. Each was read for
purpose, architecture, coupling/extractability, clinical/LLM relevance, and
publishing blockers (secrets/PII/licensing). Cross-comparison focused on the
duplicated evidence-grounding stack.

## 2. What each repo is

- **`obsidian-vault` / "Lydia"** — a deterministic-vs-judgment, four-agent
  (White Tower → Harold → Kumar → Lydia) PKM / document-routing control plane
  with a constitution, an **always-human permission gate**, an **external-write
  gate**, and an append-only **provenance ledger**. ~160K LOC (~112K tests).
  Domain-agnostic engine; clinical relevance lives in *content*, not code. The
  LLM/judgment layer is largely `[future scope]` (v1 is rule-based).
- **`EviTrace`** — evidence-grounded attribute extraction from scientific PDFs:
  multi-backend PDF extractor + 4-stage QC (rater → IAA → adjudicator →
  reconciler) + W3C JSON-LD anchoring + OpenAI structured extraction. ~40K LOC,
  strong test suite, GPL-3.0.
- **`lit_synth`** — the original "hallucination verification / evidence
  anchoring" prototype: PDF→sentences+bbox, BGE+FAISS exact/semantic match,
  W3C/Hypothes.is annotation viewer (GUI). No LICENSE.
- **`pdm`** ("Physician Decision Making") — a clinical-LLM research pipeline
  (privacy-sensitivity context shift). Notable reusable assets: `shared/
  statistical` (cluster bootstrap, ICC), reproducibility/manifest guards, and a
  provider-flexible `LLMClient` with batched local generation + response cache.

## 3. Key finding — `lit_synth` is ~fully absorbed into `EviTrace`

Verified by direct code comparison, not inference:

| `lit_synth` module | Status in `EviTrace` |
|---|---|
| `verifier.exact_match_search` | **Ported → `text_processing/matchers.LexicalMatcher`** — identical two-pass normalization, `difflib` span recovery, cross-page overlap, 64-char prefix/suffix, same 6-key evidence schema; refactored into injectable classes. |
| `verifier.semantic_search` | Ported → `SemanticMatcher` + Tier-3 `SemanticSourceVerificationCheck`. |
| `sentence_processor.py` (incl. `is_noise`, `build_full_text`) | **Verbatim** in `pdf_extractor/processing/sentence_processor.py` (identical regexes + comments). |
| `detect_section_heading` / `location_cross_check` | Ported → `layout_utils` + `SectionVerificationConcern`. |
| PDF extraction / embedding / W3C annotation | EviTrace's are strictly more capable. |
| GUI (viewer, sidebar, FastAPI) | Not carried over — and **deliberately excluded** (headless product). |

**Only two things never made the jump:** `backend/grobid_client.compare_metadata`
(GROBID-header vs claimed-metadata conflict detection, ~70 LOC) and the
post-hoc **spreadsheet-audit workflow** (`route_row` over externally-supplied
claims). **Decision:** both dropped — owner confirmed they are not wanted.
`lit_synth` is therefore retired (deletion is the owner's call; no code depends
on it).

## 4. The real shared DNA

All four repos converge on one thing: **evidence-grounded, QC'd, human-gated LLM
extraction over clinical/scientific PDFs.** Capabilities duplicated across
repos (the consolidation targets):

1. PDF → text+bbox (EviTrace multi-backend; lit_synth 3-tier) — **duplicated**.
2. Evidence anchoring / W3C annotation (EviTrace + lit_synth) — **duplicated**.
3. QC / adjudication / agreement stats (EviTrace 4-stage; pdm ICC/bootstrap) —
   **overlapping**.
4. Provider-agnostic LLM client + schema + cache + repair (EviTrace OpenAI; pdm
   HF) — **duplicated**.
5. Reproducibility / config / manifest discipline (pdm; EviTrace; Lydia
   provenance) — **overlapping**.
6. Human-in-the-loop gate + provenance (Lydia) — **unique**, the governance
   layer.

`xtrace` consolidates 1–5 into `xtrace-pdf/-qc/-llm` and lifts 6 out of Lydia as
`xtrace-gov`.

## 5. Decisions log

| # | Decision | Rationale |
|---|---|---|
| D1 | **Do not publish "Lydia" as a clinical context-engineering platform.** | Lydia is a PKM/document-routing control plane; its clinical/LLM value is `[future scope]`; the label mismatches the code. Its *patterns* (HITL gate, provenance) are the real contribution. |
| D2 | **Center the product on EviTrace + Lydia's governance patterns.** | EviTrace already *is* the evidence-trace engine; it outgrew lit_synth. True to the code and to the owner's research identity. |
| D3 | ~~**Headless only — no GUI, ever.**~~ **Superseded 2026-07-21 — owner-confirmed** → *Headless core first; reviewer UI sequenced separately.* | Original rationale: owner directive; keeps scope tight and the product library-shaped. Revised by owner decision during roadmap discovery and explicitly re-confirmed by the owner afterwards: `evitrace_multiagent.md` R19 requires a PDF annotation UI, which "no GUI, ever" contradicted outright. Resolution keeps the original intent — the toolkit stays library-shaped and every package remains headless-installable — while allowing a `reviewer-ui` spec to be built *on top* as a consumer. NFR-2 is unchanged and still binding: extraction and QC must never require a browser, display server, or long-running web service, so no `xtrace-*` package may take a dependency on the UI. The dependency runs one way only, UI → toolkit. See `.kiro/steering/roadmap.md`. |
| D4 | **Permissive license target (MIT/Apache); PyMuPDF optional.** | Enables adoption/SaaS later. PyMuPDF (AGPL) made optional in EviTrace (done, this branch). |
| D5 | **Retire `lit_synth`; salvage nothing.** | Fully absorbed; the two unique bits are unwanted. |
| D6 | **Monorepo of four packages + tiny `xtrace-core`.** | Coherent umbrella, shared CI/docs, each lib independently useful; owner-selected. |
| D7 | **Hybrid LLM client, served-first.** | Best fit for a clinical-model + commercial-model mix; minimizes bespoke code to one fallback backend (recommended). |
| D8 | **Build order: qc → pdf → llm → gov.** | qc is the cleanest, most novel standalone lift and validates packaging on low risk; gov (new code) last. |

## 6. Alternatives considered & rejected

- **Extract Lydia wholesale as the platform.** Rejected (D1): ~80% liftable but
  yields a generic agent framework in a crowded space, not clinical, entangled
  with Obsidian.
- **Separate standalone repos per library.** Rejected (D6): heavier release/CI
  overhead; monorepo chosen.
- **Pure LiteLLM+instructor** or **pure bespoke client.** Rejected (D7) in favor
  of hybrid: former leaks on local clinical models, latter over-maintains
  provider plumbing.
- **Keep PyMuPDF required.** Rejected (D4): AGPL blocks the permissive/SaaS
  path; replaced on the default path by pdfplumber + pypdfium2.

## 7. Reusable assets by source (migration map)

- **From EviTrace:** the QC framework (near-zero coupling), matchers + anchoring,
  multi-backend extractor (move routing brain into the package), W3C annotation
  generator, evidence index, OpenAI agent (fold into `xtrace-llm`).
- **From pdm:** `shared/statistical` (bootstrap, ICC), reproducibility/manifest
  guards, the local `transformers` backend + `ResponseCache` (→ `xtrace-llm`
  fallback), adapter `Protocol` contracts.
- **From Lydia:** always-human gate, external-write gate, provenance-ledger
  pattern (→ `xtrace-gov`, re-implemented headless on SQLite).

## 8. Risks & open questions

- **License not yet chosen** (MIT vs Apache-2.0) — blocks public release.
- **Evolve EviTrace in place vs migrate into new monorepo** — owner chose
  monorepo structure; the migrate-vs-evolve mechanics are still open.
- **numpy 2.x (pdm) vs <2.0 (EviTrace/scispacy)** — must reconcile; isolate to
  extras.
- **Honesty fix:** current EviTrace IAA is binary pass/fail agreement, not
  κ/α (the `iaa_calculator` is an unwired scaffold) — must implement real
  agreement before claiming it (R-QC-3).
- **PII/history:** obsidian-vault git-history purge must be verified on a fresh
  clone incl. tags before any Lydia-derived code is published; confirm no
  clinical data rides along from pdm.
- **PyMuPDF fully retired?** EviTrace default path still uses fitz for scan
  detection via graceful fallback; `xtrace-pdf` should implement a native
  pypdfium2 scan-detector so PyMuPDF is truly optional, not just degraded.

## 9. Provenance of this research

Derived from a full read of the four repos and a line-level comparison of
`lit_synth`'s core modules against `EviTrace`'s ported equivalents. The
PyMuPDF-optional change referenced in D4/§8 was implemented and pushed to
`EviTrace@claude/obsidian-bolt-clinical-llm-5eqh35`.
