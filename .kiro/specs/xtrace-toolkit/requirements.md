# Requirements — Evidence-Traceability Toolkit (`xtrace`)

**Status:** Draft (Phase 1 — Requirements)
**Language:** English
**Owner:** Soroush Dianaty

---

## 1. Overview

`xtrace` is a **headless, permissively-licensed Python toolkit** for **traceable,
auditable extraction of structured claims from clinical and scientific PDFs.**
It turns a corpus of papers into structured records where every extracted value
is anchored back to the exact source text that supports it, quality-controlled
by a multi-rater adjudication pass, and gated by an optional human-in-the-loop
review with an append-only provenance trail.

The toolkit is delivered as a **monorepo of four independently-installable
packages**:

| Package | Responsibility |
|---|---|
| `xtrace-pdf` | Multi-backend PDF extraction + W3C evidence anchoring |
| `xtrace-qc` | Multi-rater QC / adjudication / reconciliation framework |
| `xtrace-llm` | Provider-agnostic, schema-validated LLM extraction |
| `xtrace-gov` | Human-in-the-loop gate + append-only provenance ledger |

It is the productized successor to the `EviTrace` pipeline; the retired
`lit_synth` prototype is its ancestor (see `research.md`).

## 2. Goals & non-goals

**Goals**
- Extraction whose every field carries a verifiable evidence anchor.
- QC that is honest about its own confidence and agreement.
- Domain-neutral engine; clinical specificity lives in config, not code.
- A default install that is permissively licensed and dependency-light.

**Non-goals**
- **No GUI in this spec.** The toolkit is library + CLI only. No web app, no
  viewer, no server-rendered dashboards. (A static machine-readable report is
  allowed; an interactive UI is not.) This is a scope boundary for the toolkit,
  not a product-wide prohibition: a reviewer UI is sequenced separately as the
  `reviewer-ui` spec and consumes these packages without any of them depending
  on it. See `.kiro/steering/roadmap.md`.
- Not a systematic-review manager, reference manager, or annotation editor.
- Not a hosting/SaaS product in v1 (though the license must not preclude one).

## 3. Personas

- **P1 — Evidence-synthesis researcher.** Runs the toolkit over a folder of
  clinical papers to produce a structured, auditable extraction table.
- **P2 — Clinical-LLM engineer.** Embeds the extraction/QC libraries in their
  own pipeline; needs stable, provider-agnostic interfaces.
- **P3 — Reviewer/auditor.** Approves or rejects flagged extractions and later
  needs to reconstruct exactly what happened and why.

## 4. Functional requirements (EARS)

### 4.1 `xtrace-pdf` — extraction & anchoring

- **R-PDF-1** THE SYSTEM SHALL extract text, per-page bounding boxes, and font
  metadata from a PDF using only permissively-licensed backends in the default
  install.
- **R-PDF-2** WHERE the `ocr` extra is installed, THE SYSTEM SHALL detect
  scanned pages and route them to an OCR backend.
- **R-PDF-3** IF no scan-detection backend is available, THEN THE SYSTEM SHALL
  treat all pages as native and continue extraction rather than fail.
- **R-PDF-4** WHEN a GROBID service is configured, THE SYSTEM SHALL use its TEI
  output as the semantic-structure authority and cache it content-addressed by
  PDF hash.
- **R-PDF-5** THE SYSTEM SHALL emit, for each anchored claim, a W3C Web
  Annotation (JSON-LD) containing a `TextQuoteSelector` (exact/prefix/suffix)
  and, where coordinates exist, a `FragmentSelector`.
- **R-PDF-6** WHEN a claimed sentence is not found verbatim, THE SYSTEM SHALL
  attempt a punctuation-insensitive match and then a semantic fallback before
  reporting `not_found`.

### 4.2 `xtrace-qc` — quality control

- **R-QC-1** THE SYSTEM SHALL run each extraction candidate through a
  configurable rater → agreement → adjudicator → reconciler pipeline whose
  stages are injectable callables.
- **R-QC-2** THE SYSTEM SHALL express every confidence value on a `[0, 1]`
  scale and record which check produced it.
- **R-QC-3** THE SYSTEM SHALL compute a named, statistically-defined
  inter-rater agreement statistic (e.g. Krippendorff's α) and SHALL NOT label
  a binary pass/fail ratio as "agreement".
- **R-QC-4** THE SYSTEM SHALL produce a per-field flag CSV identifying every
  field whose QC did not pass.
- **R-QC-5** THE `xtrace-qc` package SHALL NOT import from `xtrace-llm`,
  `xtrace-pdf` orchestration, or `xtrace-gov` (dependency-direction rule,
  enforced by test).

### 4.3 `xtrace-llm` — extraction client

- **R-LLM-1** THE SYSTEM SHALL expose a single extraction interface that
  returns a schema-validated object regardless of the underlying provider.
- **R-LLM-2** THE SYSTEM SHALL support hosted API models and self-hosted
  clinical models through a uniform path, with a local-inference fallback.
- **R-LLM-3** WHEN a model returns output that violates the schema, THE SYSTEM
  SHALL attempt bounded automatic repair before recording a failure.
- **R-LLM-4** THE SYSTEM SHALL never silently drop a failed call; every failure
  SHALL be recorded and retrievable.
- **R-LLM-5** THE SYSTEM SHALL cache responses keyed by
  `(model, prompt, item)` so an interrupted run is resumable.
- **R-LLM-6** THE SYSTEM SHALL read credentials only from the environment and
  SHALL NOT persist them.
- **R-LLM-7** THE SYSTEM SHALL construct LLM input only from the anchored
  evidence bundle, never from raw un-QC'd PDF text.

### 4.4 `xtrace-gov` — governance & provenance

- **R-GOV-1** THE SYSTEM SHALL append every routing, extraction, QC, and review
  decision to an append-only provenance ledger.
- **R-GOV-2** WHERE human review is enabled, THE SYSTEM SHALL route any
  extraction matching a configured always-human category to a review queue and
  SHALL NOT auto-commit it.
- **R-GOV-3** IF the review-policy configuration is missing or unreadable, THEN
  THE SYSTEM SHALL fail closed by routing everything to review.
- **R-GOV-4** THE SYSTEM SHALL make every external side effect (send/share/
  write) pass through a single audited gate with idempotency and rollback.
- **R-GOV-5** THE SYSTEM SHALL let a reviewer approve or reject a queued item
  via CLI, recording the actor and outcome in the ledger.

### 4.5 Cross-cutting

- **R-X-1** THE SYSTEM SHALL be driven by declarative configuration (extraction
  map, model settings, QC thresholds, review policy) with no hardcoded domain
  vocabulary in engine code.
- **R-X-2** THE SYSTEM SHALL emit a reproducibility manifest per run (git
  commit, environment, seeds, resolved config, per-artifact hashes).
- **R-X-3** THE SYSTEM SHALL be resumable and idempotent at the per-PDF level.

## 5. Non-functional requirements

- **NFR-1 (License).** The default install of every package SHALL depend only
  on permissively-licensed (MIT/BSD/Apache) libraries. Copyleft dependencies
  (e.g. PyMuPDF/AGPL) SHALL be reachable only via opt-in extras.
- **NFR-2 (Headless).** No component SHALL require a browser, display server,
  or long-running web service to perform extraction or QC.
- **NFR-3 (Runtime).** Python 3.12; installable via `uv`/`pip`.
- **NFR-4 (Testing).** Every package SHALL ship unit + property-based
  (Hypothesis) tests; dependency-direction rules SHALL be test-enforced.
- **NFR-5 (Observability).** All significant steps SHALL log through the stdlib
  logging framework; failures SHALL be visible, auditable, and reversible.
- **NFR-6 (Privacy).** No credentials, PHI, or PII SHALL be committed to the
  repository at any tier.

## 6. Acceptance criteria (spec-level)

1. `pip install xtrace-pdf` pulls in **zero** AGPL/copyleft packages; a native
   text-layer PDF extracts fully.
2. `xtrace-qc` runs end-to-end with **no** LLM, PDF-backend, or governance
   import present, and its dependency-direction test passes.
3. A single extraction call against both a hosted model and a self-hosted
   clinical model returns the **same** validated Pydantic type.
4. Deleting the response cache mid-run and re-running produces an identical
   result set (resumability + idempotency).
5. An always-human-category extraction never auto-commits; its full lifecycle
   is reconstructable from the ledger alone.

## 7. Out of scope (v1)

- GUI / interactive viewer (out of scope for this spec; sequenced separately as
  the `reviewer-ui` spec — see `.kiro/steering/roadmap.md`). NFR-2 still holds
  and is unaffected: extraction and QC must remain runnable headlessly, so any
  future UI is a separate consumer of these packages, never a dependency of
  them.
- The `lit_synth` post-hoc spreadsheet-audit workflow and GROBID metadata
  conflict detection (explicitly dropped; see `research.md`).
- Multi-tenant hosting, auth, billing.
