# Brief: reviewer-ui

## Problem
Human reviewers are the final authority on every extracted value, but today they have no way to do that work. Verifying a field means opening `outputs/<paper>.extracted.json` in a text editor and hunting for the quoted sentence in the PDF by hand — the exact task evidence anchors were built to eliminate. Accepting, editing, or rejecting a value requires hand-editing JSON, which destroys the distinction between model output and human correction. The system therefore cannot deliver human-in-the-loop review, and none of the human-facing success metrics (correction burden, time saved, manual-review rate) can be measured.

## Current State
- **There is no UI of any kind.** `requirements.txt` and `pyproject.toml` contain no web framework — grepping `fastapi|flask|streamlit|django|uvicorn|gradio|dash` returns nothing. No HTTP server, templates, static assets, or JS toolchain. This spec introduces an entirely new technology surface for the repo.
- Outputs are files only: `outputs/<paper>.extracted.json`, `outputs/flagged_fields.csv` (`src/pipeline/extraction_report.py::generate_flagged_fields_report()`, `FLAGGED_FIELDS_FILE` at `src/utils/path_utils.py:93`), and `token_report.json`.
- Evidence anchoring already exists headlessly: `src/artifact_generation/w3c_annotation.py` is the sole producer of W3C Web Annotation JSON-LD (`AnnotationRecord`, `project()`, `generate_w3c_jsonld()`), reading only `unified.semantic` and `unified.alignment`. `src/pipeline/evidence_index.py` builds the ranked, section-scored index with stable `loc` IDs.
- Export is thin: `src/artifact_generation/csv_exporter.py` flattens `*.extracted.json` into `field_name`/`extracted_value` only — no evidence text, evidence IDs, pages, confidence, verification status, review status, or provenance (multiagent R21.2).
- No reviewer identity, review actions, edit history, or manual-review-queue surface exists.

## Desired Outcome
A reviewer opens a document and sees evidence highlighted on the page, with page-level or text-search approximate highlights where coordinates are missing (multiagent R19.1–R19.2); selecting a field navigates to its evidence (R19.3); selecting a highlight shows field name, value, evidence text, confidence, verification status, review status (R19.4); the reviewer can accept, edit, reject, mark not-reported, add evidence, or request re-extraction (R19.5), with original model output preserved alongside every human edit (R19.6), manual evidence linked to page/span/paragraph ID (R19.7), and every action stored with reviewer ID, timestamp, action, and comment (R19.8). Unresolved imported evidence can be manually linked (R20.6). The merger expands compact agent output into full records (R21.1–R21.2) and exports per-PDF JSON, master JSON, CSV/Excel review tables, manual-review queues, and optional QC/agreement/cost/audit artifacts (R21.3–R21.8). Usability NFRs 1–4 hold: status timeline, visual separation of model/imported/human-edited evidence, visible uncertainty, keyboard shortcuts.

## Approach
Sequenced **last** by explicit roadmap decision — headless core first, UI as a consumer — so it builds on stabilized interfaces rather than co-evolving with them. **No framework has been chosen, and this brief deliberately makes no choice: selecting the web/UI stack (server, PDF renderer, packaging, local vs. hosted) is work for this spec's design phase, not this brief.** The UI is a view and command surface over existing artifacts: it consumes provenance evidence anchors and never re-derives them from the PDF, and it issues review actions the headless core records. This keeps the core independently usable and keeps `xtrace-toolkit` NFR-2 satisfiable in its amended form (GUI out of scope *for that spec*, not permanently out of the product).

## Scope
- **In**: PDF rendering with evidence highlighting and approximate fallback; field↔evidence navigation; the review action set and its persistence with reviewer identity and timestamps, including ownership of the reviewer-identity model itself — every stored human review action carries reviewer identity or a configured anonymized reviewer ID (multiagent R26.7); original-vs-edited value preservation; manual evidence creation and linking; the unresolved-evidence linking surface; the final merger expanding compact records into full records; export of per-PDF JSON, master JSON, CSV/Excel review tables, and manual-review queues; document status timeline, uncertainty display, keyboard shortcuts.
- **Out**: framework/stack selection rationale (design phase); authentication, multi-tenancy, hosting; producing or re-deriving evidence anchors, coordinates, or annotations; agent orchestration — "request re-extraction" enqueues work, it does not run it; audit-package format (`provenance-audit-export`); cost report content (`cost-and-run-reporting`).

## Boundary Candidates
- View/command layer vs. the headless review-decision store — review actions must be replayable and auditable with the UI absent.
- Merger and export as deterministic library functions over records plus schema vs. the interactive surface that triggers them; the merger must be callable headlessly.
- Coordinate resolution and approximate-highlight fallback vs. anchor production upstream.

## Out of Boundary
- **Becoming a second producer of W3C annotations.** `src/artifact_generation/w3c_annotation.py` is and remains the sole producer; the UI consumes its `AnnotationRecord`/JSON-LD output.
- Defining evidence node identity (owned by `provenance-core`) or re-deriving evidence anchors.
- Disclosure and redaction decisions about what a viewer may see — `privacy-core` decides, the UI renders.
- Benchmark and ablation reporting (`evaluation-harness`), even though R21.7 exports its artifacts.

## Upstream / Downstream
- **Upstream**: `corpus-and-schema-builder` (projects, documents, schema versions, unresolved-evidence queue), `multiagent-extraction` (values, confidence, verification and adjudication status), `provenance-core` (evidence nodes, anchors, derivation lineage), plus existing `src/artifact_generation/` and `src/pipeline/evidence_index.py`.
- **Downstream**: `evaluation-harness` consumes reviewer timing, correction burden, and usability data (multiagent R24.6); any future hosted or multi-user deployment.

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` — depends on its **NFR-2 amendment** from "no GUI, permanently out" to "out of scope for this spec"; that edit must land before this spec is approved. Also extends `xtrace` R-GOV-2/R-GOV-5 (review queue, approve/reject, currently CLI-only) with a graphical path.
- **Adjacent**: `provenance-audit-export` (the UI displays audit artifacts, never defines them); `privacy-core` and `public-private-provenance` (public vs. private views constrain what is rendered); `cost-and-run-reporting`.

## Constraints
Introduces the repo's first browser-facing dependency stack; whatever is chosen must not become a required dependency of headless extraction or QC — the core stays runnable with no display server, per the spirit of xtrace NFR-2. Python 3.12.x; new packages must declare and respect dependency direction before any cross-package import lands: `quality_control` must not import UI code, and the UI must not import `quality_control`/`pdf_extractor` internals. PyMuPDF is AGPL and opt-in, which constrains server-side PDF rasterization choices. Review actions must carry reviewer identity or a configured anonymized reviewer ID (multiagent R26.7). No PHI or real patient data in fixtures or demo assets.
