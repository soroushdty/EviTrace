# Research & Design Decisions — provenance-core

## Summary

- **Feature**: `provenance-core`
- **Discovery Scope**: Complex Integration — a new leaf package (`src/provenance/`) whose entire value comes from adopting identity-bearing artifacts that already exist in four other packages.
- **Key Findings**:
  1. A de-facto evidence node already exists as an untyped `dict` in `src/pipeline/evidence_index.py`, with stable per-run IDs (`S%06d`, `F%06d`, `T%06d`) and all five anchor kinds the requirements name. Promotion, not re-derivation, is the correct move.
  2. Source and run identity already exist as `ManifestIdentity` in `src/pipeline/manifest.py`, scoped to cache invalidation. It carries exactly the five fields Requirement 3 needs and satisfies xtrace `R-X-2` once re-labelled as identity rather than staleness input.
  3. Reference validation already happens in `src/pipeline/validator.py` but is **fail-fast in one path and silent-drop in another** — `_validate_extraction_item` raises `ValidationError` on unknown `loc` IDs, while `reconstruct_fields` silently discards them. Requirement 5.4 requires both to become *recorded issues* with the claim retained.
  4. There is no existing provenance graph, derivation record, or chain validator of any kind.

## Research Log

### Existing evidence identity

- **Context**: Requirement 4 forbids a competing evidence identity. Needed the exact current shape.
- **Sources Consulted**: `src/pipeline/evidence_index.py` L107–134, L186–207, L384–417, L420–575, L869–966.
- **Findings**:
  - Evidence items are plain `dict`s: `{id, type, section_path, page, coords, xpath, text, source_pdf, score, annotations}`.
  - `type` vocabulary today is `sentence | figure_caption | table` — already a kind discriminator, already non-collapsing (Requirement 10.1).
  - `_parse_coords` yields `{"page": int, "coords": [x, y, w, h]}` or `{"page": None, "coords": None}` — the `None` case is exactly the anchor-absence condition of Requirement 4.4, currently unrecorded.
  - `_tei_xpath` falls back to a weak `.//{localtag}` when no `xml:id` exists — a *degraded* structural anchor, which is Requirement 4.5's "approximate" case.
  - `EvidenceBundle.prefilled_fields` (fields 1–2 from TEI metadata) are produced **without any model call** — the direct driver of Requirement 5.7.
  - `YearResolution.provenance` (`tei_header | pdf_metadata | first_page_text | filename_pattern`) is the repo's existing precedent for a provenance-string vocabulary; the derivation-kind vocabulary should follow the same closed-string-set style.
- **Implications**: `EvidenceNode` wraps, never replaces, the dict. IDs are scoped, not regenerated (see Design Decision "Scoped adoption of evidence IDs").

### Existing source and run identity

- **Context**: Requirement 3.6 forbids a second fingerprint set; the roadmap requires de-duplicating xtrace `R-X-2`.
- **Sources Consulted**: `src/pipeline/manifest.py` L24–158; `src/pipeline/evidence_index.py` L107–123.
- **Findings**:
  - `ManifestIdentity` (frozen dataclass, all `str`): `pdf_content_hash`, `config_hash`, `extraction_map_hash`, `model_id`, `schema_version`, `output_path`. `to_dict()` exists.
  - `_compute_extraction_map_hash()` is **already duplicated** between `manifest.py` L92 and `evidence_index.py` L107 — a pre-existing instance of the exact anti-pattern 3.6 prohibits. This spec does not fix that duplication (out of boundary) but must not add a third.
  - `MANIFEST_SCHEMA_VERSION = "1.0.0"` establishes the SemVer string convention for schema metadata.
- **Implications**: Provenance consumes `ManifestIdentity.to_dict()` as a plain mapping. No new hashing code is written for documents, configs, or the extraction map.

### Existing reference validation

- **Context**: Requirement 5.3/5.4 and the brief's note that a bad reference raises rather than records.
- **Sources Consulted**: `src/pipeline/validator.py` L120–241.
- **Findings**:
  - `_validate_extraction_item(..., valid_location_ids=None)` raises `ValidationError("Item {i}: loc contains unknown evidence IDs: ...")` at L164; the check is skipped entirely when `valid_location_ids is None`.
  - `reconstruct_fields` at L220 resolves via `[evidence_map[eid] for eid in loc_ids if eid in evidence_map]` — unknown IDs vanish with no signal at all.
  - `location_metadata` (L232–241) already emits `{id, type, section_path, page, coords, xpath, source_pdf}` per resolved item — no `text`, no `score`.
- **Implications**: Add an **optional issue sink** parameter to both functions. Absent sink ⇒ today's behavior byte-for-byte; present sink ⇒ issues recorded and the claim retained. This keeps the change additive and keeps `pipeline` free to adopt it incrementally.

### Dependency direction

- **Context**: The brief mandates declaring `src/provenance/`'s direction before any cross-package import lands.
- **Sources Consulted**: `tests/test_dependency_directions.py` L20–37 (`FORBIDDEN_PAIRS`, 8 entries), L79 (`_check_forbidden_imports`), L113–201 (one named test per pair plus an exhaustive test).
- **Findings**: Rules are a module-level list of `(source_package, forbidden_import_prefix)` tuples; each pair also has its own named test function. Adding rules requires editing both.
- **Implications**: `provenance` is a **leaf**, peer to `text_processing`: it may import only `utils` and the standard library. All four other feature packages are forbidden from importing it, so `pipeline` is the sole integration point — and `pipeline` is deliberately *not* in the forbidden list because it is the caller.

### Configuration and output paths

- **Context**: Constraint: new top-level YAML keys must be registered.
- **Sources Consulted**: `src/utils/config_utils.py` L237–247, L511–513; `src/utils/path_utils.py` L60–93.
- **Findings**: `_ALL_KNOWN_TOP_LEVEL_KEYS` currently admits 20 keys; an unregistered key raises `ValueError` in `load_local_config`. `resolve_run_output_path(fragment)` places artifacts under `<main_output_dir>/run_<timestamp>/<fragment>`, the pattern used by `evidence_cache`.
- **Implications**: One new top-level `provenance:` block; artifacts land in `resolve_run_output_path("provenance")`.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|--------|-------------|-----------|---------------------|---------|
| Leaf domain package with adapters | Pure record/graph/validation core in `src/provenance/`; adapters accept plain mappings; `pipeline` wires | Zero forbidden imports; testable without PDFs, GROBID, or a model key (1.5); parallel-safe task split | Requires a wiring layer in `pipeline` | **Selected** |
| Provenance mixed into `src/pipeline/` | Records emitted and assembled where they arise | No new package, no new dependency rule | Provenance becomes unusable standalone; violates the swappable-stage principle; no single owner for evidence identity | Rejected |
| Adopt a general provenance library (PROV-O / `prov`, `rdflib`, `networkx`) | Model on W3C PROV and store in a graph library | Standards alignment; free traversal algorithms | Heavy non-lazy deps for a graph that is hundreds of nodes; PROV-O serialization is export-shaped, and export is explicitly another spec's boundary (7.7) | Rejected for the store; the PROV *vocabulary* is adopted for naming only |
| Event-log-first (append-only ledger as the store) | Store ordered events; derive graph on read | Matches xtrace `R-GOV-1` literally | Inverts the roadmap's ruling that the ledger is a projection of the graph, not the reverse; cycle and orphan detection over a log is awkward | Rejected — ordering is delivered as a **projection** (7.6) |

## Design Decisions

### Decision: Scoped adoption of evidence identifiers

- **Context**: 4.7 forbids a competing identifier; 4.2 requires run-stable identity; the graph needs identifiers unique across documents in one run, but `S000001` recurs in every paper.
- **Alternatives Considered**: (1) use the bare local ID and accept collisions across documents; (2) hash the evidence text into a fresh ID; (3) scope the local ID by source.
- **Selected Approach**: `EvidenceNode.node_id = f"{source_id}#{local_id}"`, where `local_id` is the pipeline-assigned `S%06d`/`F%06d`/`T%06d` string **verbatim** and is exposed unchanged as `EvidenceNode.local_id`. Round-tripping `node_id → local_id` is total and lossless.
- **Rationale**: Satisfies "adopt, do not issue a competing identifier" in substance — no second numbering scheme exists, and the model-facing `loc` vocabulary is untouched, which also keeps `_shared_paper_prefix` and the chunk-output contract unchanged.
- **Trade-offs**: Consumers must know the `#` convention; mitigated by exposing parse/format helpers as the only sanctioned way to build the ID.

### Decision: Content-addressed derivation step identity

- **Context**: 6.1–6.7 plus 3.3 (re-runs on identical content must be comparable).
- **Selected Approach**: `DerivationStep.node_id = "drv:" + sha256(stage, kind, sorted(input_ids), sorted(output_ids))[:16]`.
- **Rationale**: Two runs over byte-identical inputs produce identical derivation IDs, which is what makes cross-run comparison meaningful; it also makes recording idempotent, so a retried stage cannot double-count.
- **Trade-offs**: Two genuinely distinct steps with the same stage, kind, and endpoints collapse into one. Accepted: they are indistinguishable by every field the requirements demand.

### Decision: Stage contracts drive completeness, not stage self-reporting

- **Context**: 2.1 requires detecting that a stage *did not* emit what it should have — undetectable if only emitted records are consulted.
- **Selected Approach**: A declarative `STAGE_CONTRACTS` table maps each pipeline stage to the record kinds it must emit and the artifact scope it must cover. `compute_completeness` diffs declared against recorded.
- **Rationale**: Absence is only observable against a declaration. Keeping the declaration as data (not code in each stage) means a stage that crashes before emitting anything is still detected.
- **Trade-offs**: The contract table must be maintained alongside pipeline changes; a stale table under-reports. Mitigated by a test asserting every recorded stage name appears in the table.

### Decision: Privacy carrier is inert and provenance-owned

- **Context**: The roadmap's resolution of the prov R7.5 ↔ priv R1.3 mutual recursion, and the bootstrapping problem that `provenance-core` must pin the interface before `privacy-core` exists.
- **Selected Approach**: `PrivacyCarrier` is defined here as a frozen record with `label`, `decision`, `supplied_by`, `supplied_at`, and a derived `state` of `unknown | labeled | rejected`. Provenance stores supplied values verbatim, never infers, never interprets. `label` and `decision` are opaque strings to provenance; their *vocabulary* is owned by `privacy-core`. Conformance checking is structural only (required fields present, types correct) and never semantic.
- **Rationale**: Makes the interface pinnable without knowing the label vocabulary, which is precisely the bootstrapping constraint. `privacy-core` can ship any vocabulary without a provenance change.
- **Trade-offs**: Provenance cannot detect a semantically wrong label. That is correct: detecting it would be a policy decision, which is out of boundary.

### Decision: Ordered event log is a projection, never a store

- **Context**: xtrace `R-GOV-1` de-duplication.
- **Selected Approach**: `project_event_log(graph)` returns a deterministically ordered, append-only-shaped sequence derived from graph nodes at read time. No event rows are persisted independently of the graph artifact.
- **Rationale**: Directly implements the roadmap's ruling. A test asserts the projection is a pure function of the graph — regenerating it twice yields identical output, and it contains no field absent from the graph.

### Decision: Additive issue sink instead of changing raise semantics

- **Context**: 5.4 requires retaining a claim with a bad reference; today one path raises and another silently drops.
- **Selected Approach**: Optional `issue_sink` parameter on `_validate_extraction_item`, `validate_chunk_output`, and `reconstruct_fields`. Default `None` preserves current behavior exactly.
- **Rationale**: `validate_chunk_output`'s raise is load-bearing for the existing retry path; flipping it wholesale would change pipeline control flow, which this spec does not own.

## Risks & Mitigations

- **Wiring `pipeline` for emission touches the LLM call path** — Mitigation: recording happens strictly *after* prompt assembly and *after* response parsing; no provenance value enters `_shared_paper_prefix`. A prompt-cache-stability regression test is an explicit task.
- **Stage-contract table drifts from the pipeline** — Mitigation: a test asserting every recorded stage name is declared in `STAGE_CONTRACTS`.
- **Serialized artifacts grow large on big corpora** — Mitigation: the graph artifact is per-run and per-document-scoped, node payloads carry references rather than duplicated evidence text, and no evidence text is stored on claim nodes.
- **Nothing is end-to-end verifiable until `risk-remediation` Requirement 1 ships** (final-output writes are silently rejected) — Mitigation: every acceptance criterion is verified at the provenance-package level against constructed records, not via an end-to-end run.
- **`extraction_map` hash is already computed in two places** — Mitigation: provenance adds no third computation; it consumes `ManifestIdentity`. A test asserts no hashing of the extraction map occurs inside `src/provenance/`.

## References

- `.kiro/specs/archive/original-idea-documents/provenance_requirements.md` — prov R1–R14, the source requirements.
- `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` — priv R1.3, R3.4, R5.3, the consumer side of the carrier interface.
- `.kiro/steering/roadmap.md` §Boundary Strategy — the privacy/provenance direction ruling and the two straddling-requirement splits.
- `.kiro/specs/xtrace-toolkit/requirements.md` — `R-GOV-1`, `R-X-2`, `R-QC-3`.
- W3C PROV-DM — vocabulary reference for entity/activity/agent naming only; not adopted as a storage or serialization format.
