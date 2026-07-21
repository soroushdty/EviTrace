# Research & Design Decisions — reviewer-ui

## Summary

- **Feature**: `reviewer-ui`
- **Discovery Scope**: New Feature (greenfield technology surface) layered onto an existing headless system — full discovery.
- **Key Findings**:
  - The repository has **no** user-interface technology of any kind. Grepping `fastapi|flask|streamlit|django|uvicorn|gradio|dash` across `requirements.txt` and `pyproject.toml` returns nothing; there is no HTTP server, no template engine, no static asset directory, and no client build toolchain. The stack choice is genuinely open and is made here.
  - **PyMuPDF is AGPL and deliberately excluded from the default install** (`pyproject.toml` `[project.optional-dependencies] ocr`). That single fact eliminates server-side PDF rasterization as the rendering strategy for the default install and pushes rendering to the client. It is the strongest constraint on the stack decision.
  - Everything the workspace needs to display already exists as **records produced by upstream specs**: evidence anchors and anchor precision (`provenance-core`), values/confidence/verification/adjudication (`multiagent-extraction`), projects/documents/status history/schema versions/unresolved queue (`corpus-and-schema-builder`), cost figures (`cost-and-run-reporting`). This spec adds no producer of any of them.
  - Evidence coordinates in the existing pipeline are GROBID TEI `coords` parsed by `src/pipeline/evidence_index.py::_parse_coords` into `page` (1-based int) and `coords` (`[x, y, w, h]` floats, PDF points, **top-left origin**). `provenance-core` promotes these onto `SourceAnchor` with an explicit `precision` of `exact` / `approximate` / `absent`.
  - The dependency-direction rules are AST-enforced (`tests/test_dependency_directions.py`). A UI package that imports `pipeline.multiagent` types directly would violate the roadmap constraint that the UI must not reach into pipeline internals. This forces a mapping-based adapter, exactly as `provenance-core` solved the same problem.

## Research Log

### Existing interface surface and output artifacts

- **Context**: Establish what a reviewer has today and what the workspace must consume.
- **Sources Consulted**: `requirements.txt`, `pyproject.toml`, `src/artifact_generation/csv_exporter.py`, `src/artifact_generation/w3c_annotation.py`, `src/utils/path_utils.py`, `src/pipeline/evidence_index.py`.
- **Findings**:
  - Outputs are files only: `outputs/run_<ts>/<paper>.extracted.json`, `flagged_fields.csv` (`FLAGGED_FIELDS_FILE`, `src/utils/path_utils.py`), and token/cost reports.
  - `csv_exporter.py` flattens `field_name` → `extracted_value` only. It carries no evidence text, evidence identifiers, pages, confidence, verification status, review status, or provenance — precisely the gap multiagent R21.2 names.
  - `src/artifact_generation/w3c_annotation.py` is documented in its own module docstring as *"the ONLY place in EviTrace that defines annotation records and constructs W3C JSON-LD annotation dicts."* Any second producer would contradict both that module and the steering rules.
- **Implications**: the merger and exporters are net-new work, not a refactor of `csv_exporter.py`; the existing exporter stays where it is and is not extended. The workspace consumes serialized annotation JSON-LD and never imports the annotation module.

### Rendering strategy under the AGPL constraint

- **Context**: Highlighting evidence on a page requires rendering the page. Rendering can happen on the server (rasterize to images) or on the client (render the PDF in the browser).
- **Sources Consulted**: `pyproject.toml` optional-dependency comments; the repository constraint that PyMuPDF is AGPL and opt-in; PDF.js project licensing (Apache License 2.0); `pdfplumber` (core dependency) page-geometry API.
- **Findings**:
  - Server-side rasterization in Python realistically means PyMuPDF (AGPL, opt-in) or `pdf2image`/poppler (already only present in the `ocr` extra). Either makes the default install unable to render, or drags an AGPL component into a browser-facing path.
  - PDF.js renders PDFs entirely in the browser from raw bytes and is Apache-2.0, which is compatible with the repository's permissive default posture. Its prebuilt distribution is plain JavaScript and can be loaded as ES modules with **no build step and no client package manager**.
  - `pdfplumber` (already a core dependency) exposes `page.width` / `page.height` without rasterizing, which is all the server needs to convert absolute PDF-point rectangles into resolution-independent fractional rectangles.
- **Implications**: the server streams PDF bytes and resolution-independent highlight rectangles; the browser rasterizes. This removes the AGPL constraint from the rendering path entirely and keeps geometry conversion in Python, where it is unit-testable.

### Anchor fidelity and the approximation obligation

- **Context**: multiagent R4.7–R4.8 require that where no bounding-box coordinates exist, page and text identifiers are still preserved and annotation precision is marked **approximate**. The roadmap requires the workspace to surface that approximation honestly.
- **Sources Consulted**: `provenance-core` design (`SourceAnchor`, `AnchorPrecision`, `missing`), `src/pipeline/evidence_index.py::_parse_coords`.
- **Findings**:
  - `provenance-core` already classifies precision as `exact` (page and coords present), `approximate` (coordinates absent but page or section path present, or only a weak structural path), or `absent` (no usable anchor, with `missing` enumerating which anchor kinds are gone).
  - `_parse_coords` takes only the **first** coordinate group of a multi-group TEI `coords` attribute (`coords.strip().split()[0]`). A sentence spanning two lines therefore yields one box covering the first line only, while still being classified `exact`.
- **Implications**: the workspace renders three visually distinct highlight classes driven **solely** by the upstream `precision` value, and never upgrades or downgrades a precision. The first-box limitation is recorded as a known upstream fidelity risk; this spec does not compensate for it by re-deriving geometry, because that would make the workspace a second anchor producer.

### Dependency direction for consuming extraction results

- **Context**: The workspace must show `FieldDecisionRecord`, `AnswerVerdict`, and `VerificationOutcome`, all defined in `src/pipeline/multiagent/models.py`. The roadmap forbids the UI reaching into pipeline internals.
- **Sources Consulted**: `tests/test_dependency_directions.py` (AST-based `FORBIDDEN_PAIRS`), `provenance-core` design (adapters accept `Mapping[str, Any]` only), `multiagent-extraction` design (`MultiagentArtifactStore` writes plain JSON to `outputs/multiagent/{key}/`).
- **Findings**:
  - `multiagent-extraction` already commits to writing `decisions.json`, `verdicts.json`, `verifications.json`, `answers.json`, and `result.json` as plain JSON that is *"queryable without the pipeline"*.
  - `provenance-core` solved the identical problem by having adapters accept plain mappings, so the package never imports `pipeline`.
- **Implications**: `src/review/` reads extraction results through a `ExtractionResultProvider` protocol whose file-backed implementation parses the published JSON artifacts into review-local dataclasses. `review` never imports `pipeline`. `provenance` and `corpus` are leaf packages and are imported directly, which is what "consume anchors rather than re-derive them" requires.

### Review record durability model

- **Context**: The brief names "view/command layer vs. the headless review-decision store" as a boundary candidate and requires review actions to be replayable and auditable with the interface absent.
- **Sources Consulted**: `corpus-and-schema-builder` design (`events.jsonl`, atomic writes, `ProjectLock`), `provenance-core` design (`project_event_log` as a pure projection, "no parallel store").
- **Findings**: both upstream specs converge on the same pattern — an append-only event log plus a pure projection function, with atomic temp-file writes and a directory lock for concurrency.
- **Implications**: adopt the same pattern rather than inventing a third. The review action log is append-only JSONL; current review state is a pure function of the log; the workspace holds no authoritative state.

### Framework survey

- **Context**: Selecting the server and client stack for a single-reviewer, local, offline-capable document review surface in a Python 3.12 `src/`-layout repository with no configured linter and no JS toolchain.
- **Sources Consulted**: repository constraints (`pyproject.toml`, `CLAUDE.md`, `.kiro/steering/`), the candidate frameworks' interaction models.
- **Findings**: summarized in the Architecture Pattern Evaluation table below.
- **Implications**: see Decision 1.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|--------|-------------|-----------|---------------------|---------|
| **FastAPI + Uvicorn server, vendored no-build PDF.js client** | Typed JSON API over the headless review domain; browser renders the PDF and draws highlight overlays | Client-side rendering sidesteps the AGPL rasterization constraint; typed request/response models satisfy the type-safety rule; async matches the repo's `asyncio` orientation; no client package manager; fully offline | Vendoring a prebuilt JS library into the repository; first browser-facing dependency stack | **Selected** |
| Streamlit | Python-only reactive app framework | No client code at all; very fast to a first screen | Whole-script rerun per interaction destroys reviewer flow; no primitive for per-span overlays on a rendered PDF page; keyboard shortcuts and focus management are not addressable; would still need server-side rasterization | Rejected |
| Gradio / Dash | Component-based Python UI frameworks | Rich prebuilt components | Same overlay problem as Streamlit; large transitive dependency trees; component models fight pixel-space highlight positioning | Rejected |
| Flask + Jinja server-rendered pages | Classic synchronous web stack | Small, familiar, minimal | Synchronous against an `asyncio` codebase; no typed request/response contract; still needs a client PDF renderer, so it saves nothing on the hard part | Rejected |
| Desktop GUI (PyQt / Tkinter) | Native application | No HTTP surface | PyQt is GPL/commercial-dual — worse than the AGPL problem being avoided; Tk has no usable PDF renderer; ships a display-server dependency into the repository | Rejected |
| React/Vue single-page app with a bundler | Modern SPA | Best long-term client ergonomics | Introduces a Node toolchain, a lockfile, and a build step into a repository with no configured linter or formatter; disproportionate for a single-reviewer local tool | Rejected |
| Server-side page rasterization + image overlay | Render pages to PNG on the server, position highlights over images | Trivial client | Requires PyMuPDF (AGPL, opt-in) or poppler; loses the text layer, which is what makes text-search approximate highlights possible; degrades text selection and accessibility | Rejected |

## Design Decisions

### Decision 1: FastAPI + Uvicorn behind an optional extra, with a vendored no-build PDF.js client

- **Context**: The repository has no UI stack. Requirement 1.3 demands the headless pipeline keep running with none of the workspace's runtime present. Requirement 1.7 demands offline operation. Requirement 2 demands per-span highlighting over a rendered page.
- **Alternatives Considered**: the six rejected rows above.
- **Selected Approach**: `fastapi` + `uvicorn` declared as a **new optional extra** `ui` in `pyproject.toml`, imported only inside `src/reviewer_ui/`. The client is a vendored PDF.js prebuilt distribution plus a small hand-written ES-module application, served as static files. No client package manager, no bundler, no lockfile, no network fetch at runtime.
- **Rationale**: it is the only candidate that satisfies the AGPL constraint, the offline constraint, the per-span overlay requirement, and the "must not become a required dependency of headless extraction" constraint at once. FastAPI's Pydantic models give the design's typed-contract requirement a real enforcement point at the HTTP boundary, which matters because the boundary is the only place untyped JSON crosses into the system.
- **Trade-offs**: a prebuilt JavaScript library is committed to the repository, which is a maintenance obligation (version bump = vendored file replacement, recorded in `CHANGELOG.md`). Accepted, because the alternative is a Node toolchain in a repository that has deliberately avoided even a Python linter configuration.
- **Follow-up**: pin the vendored PDF.js version in a `VENDOR.md` beside the files, including its Apache-2.0 license text; assert in a test that no module under `src/review/` imports `fastapi`.

### Decision 2: Two packages — a headless review domain and a thin web adapter

- **Context**: Requirements 8.3, 11.7, and 12.8 require the store, the merger, and the exporters to be callable with no workspace runtime installed. The brief names this as a boundary candidate.
- **Alternatives Considered**: (a) one package with the web framework imported lazily; (b) two packages with a hard boundary.
- **Selected Approach**: `src/review/` owns models, reviewer identity, the action log, the projection, anchor resolution, evidence linking, the merger, the exporters, and a CLI. `src/reviewer_ui/` owns the FastAPI application, the HTTP DTOs, and the static client, and is the **only** module in the repository permitted to import `fastapi` or `uvicorn`.
- **Rationale**: a lazy import inside one package makes the guarantee a convention; two packages plus an AST test make it a checked invariant, matching how every other boundary in this repository is enforced.
- **Trade-offs**: one extra package and one extra DTO translation layer. Accepted; the translation layer is where HTTP-shaped concerns (pagination, error envelopes) stay out of the domain.
- **Follow-up**: extend `tests/test_dependency_directions.py` with the new pairs and add an import-isolation test that imports `review.merger` with `fastapi` removed from `sys.modules`.

### Decision 3: Server-side normalization of highlight geometry to fractional rectangles

- **Context**: Anchors arrive as absolute PDF-point rectangles with a top-left origin; the browser renders at an arbitrary zoom.
- **Alternatives Considered**: (a) send raw points and convert in JavaScript against the PDF.js viewport; (b) convert to fractions of page width and height on the server.
- **Selected Approach**: the server reads page width and height with `pdfplumber` (a core dependency, no rasterization) and emits each highlight as `(x, y, w, h)` fractions in `[0, 1]` plus the declared source coordinate space. The client multiplies by the rendered page size.
- **Rationale**: geometry conversion becomes a pure Python function with unit tests, and the client stays a renderer rather than a coordinate system. It also means a future non-browser consumer of the same API gets usable geometry.
- **Trade-offs**: one `pdfplumber` page-geometry read per document, cached per session.
- **Follow-up**: a test asserting that a known TEI `coords` value maps to the expected fractional rectangle for a known page size, and that a rotated page is reported rather than silently mis-projected.

### Decision 4: Extraction results are consumed as published JSON through a provider protocol

- **Context**: The workspace must display `FieldDecisionRecord`, `AnswerVerdict`, and `VerificationOutcome`, which live in `src/pipeline/multiagent/`, while being forbidden from importing pipeline internals.
- **Alternatives Considered**: (a) import the multiagent dataclasses directly; (b) read the published JSON artifacts through a protocol.
- **Selected Approach**: (b). `ExtractionResultProvider` is a `Protocol`; `FileExtractionResultProvider` parses `outputs/multiagent/<key>/decisions.json`, `verdicts.json`, `verifications.json`, and `answers.json` — artifacts `multiagent-extraction` already commits to writing as plain, pipeline-independent JSON — into review-local dataclasses.
- **Rationale**: preserves the dependency direction, makes the workspace testable against literal fixture dicts, and keeps the workspace working when the multiagent layer is disabled (the provider then reports the compact single-extractor output instead).
- **Trade-offs**: a shape-drift risk between the published artifact and the reader. Mitigated by tolerating unknown keys, treating absent keys as unavailable rather than as defaults, and a fixture test built from the shapes documented in the multiagent design.
- **Follow-up**: the reader must never substitute a default for an absent uncertainty signal (Requirement 9.6).

### Decision 5: Append-only action log plus a pure projection

- **Context**: Requirements 5.2, 8.1, 8.2, and 8.4.
- **Alternatives Considered**: (a) a mutable current-state JSON document; (b) an append-only log with a derived projection; (c) an embedded relational database.
- **Selected Approach**: (b), with the same physical patterns already used by `corpus-and-schema-builder` — JSONL append, atomic temp-file writes for derived files, and a project-scoped lock directory.
- **Rationale**: (a) cannot satisfy "no recorded action is modified or removed"; (c) adds a storage engine and a migration story for a single-reviewer local tool, and would be a third persistence idiom in a repository that already has exactly one. Reusing the corpus pattern also means the concurrency semantics are already specified and tested.
- **Trade-offs**: projection cost grows with log length. Bounded by a cached projection snapshot that is always regenerable from the log and is never authoritative.
- **Follow-up**: an uninterpretable event must be skipped with a report, not abort the projection (8.4).

### Decision 6: Anonymized reviewer identity as a stable salted digest

- **Context**: multiagent R26.7 and Requirements 7.2 and 7.3 — actions must carry a reviewer identity or a configured anonymized identifier, and the anonymized form must be stable across sessions so a reviewer's actions remain groupable.
- **Alternatives Considered**: (a) a random per-session identifier; (b) a stable digest of the reviewer identifier and a project-scoped salt; (c) a stored lookup table mapping people to pseudonyms.
- **Selected Approach**: (b). `anonymous_id = sha256(reviewer_id + project_salt)[:16]`, with the salt generated once per project and stored in the project record.
- **Rationale**: (a) breaks groupability across sessions, which is what makes correction-burden measurement possible for `evaluation-harness`; (c) stores the very mapping anonymization exists to avoid.
- **Trade-offs**: the pseudonym is re-identifiable by anyone holding both the salt and a candidate reviewer identifier. This is documented rather than overclaimed — the feature provides pseudonymization, not anonymity, and makes no compliance claim.
- **Follow-up**: refuse to record an action when no identity is configured (7.4) rather than falling back to an "unknown" reviewer.

### Decision 7: The workspace never constructs an annotation artifact

- **Context**: The roadmap and the brief both state that `src/artifact_generation/w3c_annotation.py` is and remains the sole producer of W3C annotations.
- **Selected Approach**: neither `src/review/` nor `src/reviewer_ui/` imports `artifact_generation` at all. Manual and newly linked evidence is recorded as a **location-resolved evidence record**, structurally the same kind of "candidate" that `corpus-and-schema-builder` emits. Where an annotation artifact is wanted, the existing producer consumes those candidates.
- **Rationale**: an import ban is mechanically checkable; a "please don't call `generate_w3c_jsonld`" convention is not.
- **Follow-up**: an AST test asserting the absence of that import, alongside the dependency-direction pairs.

### Decision 8: Excel export reuses the existing optional spreadsheet component

- **Context**: Requirement 12.4 and 12.5.
- **Selected Approach**: `openpyxl`, already introduced by `corpus-and-schema-builder` as the optional `imports` extra, imported inside the exporter function body. Absence produces a named, actionable error and leaves CSV, JSON, and queue exports working.
- **Rationale**: adding a second spreadsheet library would be a duplicate capability; the lazy-import convention is already established for `paddleocr`, `faiss`, `torch`, and `openpyxl`.

## Synthesis Outcomes

### Generalization

- Accept, edit, reject, mark-not-reported, add-evidence, link-evidence, request-re-extraction, and comment are all **one shape**: an attributed, timestamped, append-only command against a `(document, field)` target carrying a typed payload. They are modelled as one `ReviewEvent` type with a discriminated payload rather than eight record types, so adding an action later is a vocabulary addition rather than a schema change.
- Per-document JSON, master JSON, CSV, Excel, and the manual-review queue are all **projections of the same `FullRecord` sequence**. The merger produces the sequence once; each exporter is a formatter over it. This is why Requirement 11 is separable from Requirement 12 and why the merger is independently testable.
- The three highlight classes (coordinate, approximate, absent) are one `ViewAnchor` with a precision discriminator, not three rendering paths.

### Build vs. Adopt

- **Adopted**: PDF.js for rendering and text-layer search; `pdfplumber` for page geometry; `openpyxl` for spreadsheets; the corpus package's lock and atomic-write patterns; `provenance-core`'s graph reader for anchors; the multiagent artifact JSON contract.
- **Built**: the review event model, the projection, reviewer identity, anchor-to-view geometry conversion, the merger, and the exporters — none of these have an off-the-shelf equivalent that matches the record shapes this system already produces.
- **Explicitly not built**: an annotation producer, an anchor deriver, a status vocabulary, an agreement or cost calculator, an authentication layer.

### Simplification

- Dropped a planned separate `ReviewProjectionCache` component: the cache is a write-through file owned by the store, not its own boundary.
- Dropped a planned pluggable `HighlightRenderer` abstraction: there is exactly one renderer and no foreseeable second one; the precision discriminator carries the variation.
- Dropped a planned WebSocket live-update channel: single-reviewer scope means there is no second writer to push updates from, and polling the projection on navigation is sufficient. Multi-reviewer deployment is explicitly out of scope.
- Merged the planned `ReviewQueryService` into the store's projection reader; it added an interface with one implementation and one caller.

## Risks & Mitigations

- **Vendored client library drift** — a security fix in PDF.js requires a manual vendored-file update. Mitigated by recording the pinned version and its provenance in `VENDOR.md`, and by the client having no network egress and rendering only local files.
- **Published artifact shape drift** between `multiagent-extraction` and the review reader — mitigated by tolerating unknown keys, never defaulting absent keys, and fixture tests built from the documented shapes; a drift shows up as "unavailable", never as a wrong value.
- **Multi-line evidence renders as a single first-line box** because `_parse_coords` keeps only the first coordinate group. Mitigated by disclosure: the workspace renders exactly the anchor supplied and does not claim completeness of the span. Correcting it belongs upstream, in the evidence index and the provenance adapter, not here.
- **Reviewer accepts everything to clear a queue**, producing a dataset that looks reviewed but is not. Mitigated by recording per-action timestamps and durations that `evaluation-harness` consumes for correction-burden analysis, and by never presenting acceptance as correctness (14.3).
- **Pseudonymization mistaken for anonymity** — mitigated by an explicit statement in the design's security section and in the workspace itself; no compliance claim is made anywhere.
- **A large corpus makes the projection slow** — mitigated by the write-through projection snapshot and by per-document scoping of every read path.
- **Rotated or non-zero-origin PDF pages** would mis-project fractional rectangles. Mitigated by reading the page rotation and media box through `pdfplumber` and reporting an unsupported page geometry as an approximate highlight rather than a wrong exact one.

## References

- `.kiro/steering/roadmap.md` — spec decomposition, cross-cutting NFRs, standing product boundaries.
- `.kiro/specs/reviewer-ui/brief.md` — problem framing, scope, boundary candidates.
- `.kiro/specs/provenance-core/design.md` — `SourceAnchor`, `AnchorPrecision`, evidence node identity, graph serialization.
- `.kiro/specs/corpus-and-schema-builder/design.md` — project layout, status vocabulary, `UnresolvedEvidenceQueue`, `AnnotationCandidate`, lock and atomic-write patterns.
- `.kiro/specs/multiagent-extraction/design.md` — `FieldDecisionRecord`, `AnswerVerdict`, `VerificationOutcome`, published artifact paths.
- `.kiro/specs/cost-and-run-reporting/design.md` — `cost_report.json` and `run_manifest.json` content, owned there and only rendered here.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — multiagent R19, R20.6, R21, R26.7, and the Usability non-functional requirements.
