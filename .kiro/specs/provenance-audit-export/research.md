# Research & Design Decisions — provenance-audit-export

## Summary

- **Feature**: `provenance-audit-export`
- **Discovery Scope**: Extension (integration-focused discovery over an existing pipeline plus a completed upstream spec)
- **Key Findings**:
  - Everything this spec must report is already computed by `provenance-core`. `ProvenanceGraph` carries `ChainValidationReport` and `CompletenessReport`, and `project_event_log(graph)` already yields a deterministic ordered log. The correct posture is projection and serialization, never recomputation.
  - The repository has exactly one interoperable export today — `src/artifact_generation/w3c_annotation.py`, which emits **W3C Web Annotation** JSON-LD and is the declared sole producer of that format. It writes no file of its own; the JSON-LD is embedded in the extraction artifact by `build_qc_bundle`. An interoperable *provenance* export must therefore use a different vocabulary and a different producer, or it violates the single-producer rule.
  - Raw per-backend parser output already exists in memory as `QCBundle.branches[*].payload` (GROBID TEI XML, pdfplumber block lists) but is **dropped on serialization**: `unified_to_artifact` writes branches as `{"source", "index", "status"}` only. Retention of raw parser output (multiagent R6.6) is therefore genuinely new work, not a re-export.
  - `manifest.json` lives at the repo root and is *not* run-scoped, while every other output goes through `resolve_run_output_path` under `outputs/run_<ts>/`. The run artifact must be run-scoped; the manifest stays where it is and is read, not rewritten.
  - There is no `generate_qc_report()` in the repository despite the brief citing it. The live report entry point is `extraction_report.py::generate_flagged_fields_report()` writing `outputs/run_<ts>/flagged_fields.csv`. The brief's reference is stale (the roadmap already records this as a docs-only defect).

## Research Log

### Upstream contract: what `provenance-core` hands over

- **Context**: This spec is forbidden from building a parallel store, so its entire input surface is whatever `provenance-core` exposes.
- **Sources Consulted**: `.kiro/specs/provenance-core/design.md`, `.kiro/specs/provenance-core/requirements.md`.
- **Findings**:
  - `ProvenanceGraph` is frozen and carries `schema_id`, `schema_version`, `run: RunIdentity`, `nodes`, `edges`, `missing_segments`, and — after `attach_validation` / `attach_completeness` — `validation: ChainValidationReport` and `completeness: CompletenessReport`.
  - `CompletenessReport` already splits `run` / `documents` / `claims` and each `ArtifactCompleteness` carries `state`, `reason`, `missing_record_kinds`, `responsible_stages`. The missing-anchor versus unsupported distinction is already encoded in `reason` (core requirement 2.4).
  - `ChainValidationReport.completed is False` is core's "unvalidated" signal, with `incompletion_reason`.
  - `project_event_log(graph)` is a pure, deterministic, total-order projection with no I/O.
  - Every node carries a `PrivacyCarrier` whose `label` and `decision` are **opaque strings** to provenance; `state` is `unknown` until `privacy-core` populates it.
  - Core's `compute_completeness` "shall not format, render, or export a completeness report" (core 2.6). That sentence is this spec's mandate.
- **Implications**: Requirement 7 is a pure projection with no arithmetic of its own. Requirement 4 cannot interpret privacy label semantics, so the decision→marking mapping must come from configuration, not from code.

### Choosing the interoperable representation

- **Context**: prov R11 demands "at least one native structured format" plus a shareable representation readable outside the runtime, preserving claim→evidence relationships.
- **Sources Consulted**: W3C PROV-O / PROV-JSON vocabulary (`prov:Entity`, `prov:Activity`, `prov:used`, `prov:wasGeneratedBy`, `prov:wasDerivedFrom`, `prov:hadPrimarySource`, `prov:wasAssociatedWith`); the existing hand-emitted JSON-LD in `src/artifact_generation/w3c_annotation.py`; `requirements.txt`.
- **Findings**:
  - PROV-O is the standard provenance interchange vocabulary and maps almost one-for-one onto the core node kinds: source → `prov:Entity` with `prov:hadPrimarySource`, evidence → `prov:Entity` derived from its source, claim → `prov:Entity` with `prov:wasDerivedFrom` each cited evidence, derivation step → `prov:Activity` with `prov:used` / `prov:generated`, run → `prov:Activity`, validation record → `prov:Entity` qualified to what it validates.
  - JSON-LD can be emitted by hand exactly as `w3c_annotation.py` already does; no RDF library, no new dependency. `provenance-core` forbids adding one.
  - PROV-O has no natural expression for anchor precision, anchor-absence markers, per-node privacy carriers, severity-classified findings, stage contracts, or `missing_segments`. These become the fidelity report's content rather than being dropped.
- **Implications**: Adopt PROV-O JSON-LD. It is a *different* vocabulary from Web Annotation, so `w3c_annotation.py` remains the sole producer of its format and requirement 5.6 holds. The list of unmappable concepts above is exactly why requirement 6 exists as a separate requirement.

### Making the fidelity report self-maintaining

- **Context**: The brief requires that "an unmapped node type surfaces as a reported omission instead of a silent drop", so a hand-maintained omission list is not acceptable.
- **Findings**:
  - Every core record type is a frozen dataclass, so `dataclasses.fields()` gives a complete, always-current field inventory at runtime with no code generation.
  - A declarative mapping table keyed by `(node_kind, field_name)` with a disposition of `preserved` / `transformed` / `omitted` can therefore be diffed against that inventory. Any field or node kind absent from the table is reported as omitted with reason `unmapped`, and the export emits nothing for it.
- **Implications**: The exporter and the fidelity reporter read the same table, so the two cannot drift. A test asserts full inventory coverage: every core dataclass field is either in the table or reported unmapped, and the assertion is computed, not enumerated.

### Existing packaging and retention surfaces

- **Context**: multiagent R22 requires sixteen named slots; R5.9 and R6.4–R6.6 require parser QC and raw parser output in the package.
- **Sources Consulted**: `src/quality_control/local_metrics.py`, `src/quality_control/quality_control.py`, `src/pipeline/extraction_pipeline.py`, `src/artifact_generation/extraction_artifact.py`, `src/utils/path_utils.py`, `src/pipeline/manifest.py`.
- **Findings**:
  - Parser QC metrics exist today as `ExtractionCoverageReport` with eight Tier-1 metric records, aggregated into `ctx.metrics_hierarchy`. They are computed by `quality_control`, which this spec must not modify or reinterpret.
  - Raw parser payloads exist as `Candidate.payload` on `QCBundle.branches` and are discarded at serialization time.
  - GROBID TEI is separately cached to `<tei_cache_dir>/{digest}.tei.xml`, which is a cache keyed by content, not a per-document audit record; it can be referenced but is not a substitute for retention.
  - Seven of the sixteen R22 slots (route map, route QC, counterfactual route output, final route map, agreement report, verification output, repair output) belong to specs that do not exist yet.
- **Implications**: The slot registry must distinguish "expected but missing" from "belongs to a capability not present in this run". Without that distinction the package would report itself permanently broken, and the distinction is what requirement 8.6 encodes.

### Determinism and byte-identical re-export

- **Context**: Requirements 3.6 and 11.6 demand identical output on repeat serialization; requirement 3.2 demands cross-run comparison, which is only meaningful if incidental noise is excluded.
- **Findings**:
  - `RUN_FOLDER_NAME` is computed once at import time, so the run directory is stable within a process but changes between runs — it must not enter artifact content.
  - `save_manifest` already establishes the atomic temp-file-then-`os.replace` write pattern, with tmp cleanup on `BaseException`.
  - `zipfile.ZipInfo.date_time` defaults to wall-clock and must be pinned for a reproducible bundle.
- **Implications**: All artifact content timestamps come from `RunIdentity.created_at` (fixed for the run) and never from a fresh clock read. JSON is written canonically (`sort_keys=True`, fixed indent, `ensure_ascii=True`). Bundle entries are sorted with pinned entry timestamps.

### Referencing the reproducibility manifest instead of re-implementing it

- **Context**: Requirement 2 originally proposed implementing xtrace `R-X-2` inside this artifact. Cross-spec review found that `cost-and-run-reporting` also claims to be the single implementation of `R-X-2`, that `ReproducibilityMetadata` and its `RunManifest` overlapped on roughly 80% of content (source revision, environment, dependency versions, resolved config with secret omission, determinism settings, model ids, per-document fingerprints), and that the two even disagreed on method — that spec shells out to `git rev-parse` while this one forbade subprocess and read `.git` from the filesystem.
- **Decision (superseding the earlier finding)**: `cost-and-run-reporting` **owns** `R-X-2` and `run_manifest.json`. It ships in wave 1 and already owns the price, stage, and telemetry facts the manifest carries, so it is the natural home. This spec consumes the manifest **by reference** — relative path, `manifest_version`, and a content hash pinning the exact file — and duplicates none of its content. The earlier `.git`-reading approach is withdrawn along with the duplicated section; the subprocess prohibition remains satisfied trivially, because this spec no longer resolves a revision at all.
- **Findings that still apply**:
  - Per-artifact-file content hashes remain owned here: only this spec knows the complete set of files it emitted, and `run_manifest.json` records inputs and configuration rather than the emitted artifact inventory. The manifest file itself is one of the hashed files, provided it is written first.
  - An absent or unreadable manifest (for example when run reporting is disabled) yields an explicit unavailable marker with a reason, never a placeholder path and never an inline substitute capture.
- **Implications**: This spec captures no configuration mapping at all, so its no-credential guarantee is structural rather than pattern-based; secret omission stays where the configuration is captured, inside `cost-and-run-reporting`'s manifest. Ordering is now load-bearing: `run_manifest.json` must be written before reference collection runs.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Serializer subpackage under `src/provenance/` | `src/provenance/export/` sits inside the package core already owns | Inherits core's dependency-direction rules unchanged; no ninth top-level package; imports of core stay intra-package | Package grows large; boundary between core and export must be stated, not assumed | **Selected** |
| New top-level `src/audit_export/` package | Peer package importing `provenance` | Very visible boundary | Requires new forbidden-import pairs, a second README, and re-litigating dependency direction for no behavioural gain | Rejected |
| Extend `src/artifact_generation/` | Put exports beside the W3C annotation producer | Co-locates the two exporters | That package has no dependency rules and is imported by `pipeline`; would let provenance leak sideways, and risks confusing the two JSON-LD producers | Rejected |
| Reuse/extend `manifest.py` | Grow the manifest into the audit artifact | One artifact instead of two | The manifest is a repo-root cache-invalidation record with its own schema version and staleness semantics; growing it breaks cache logic and violates the brief's no-breaking-rewrite constraint | Rejected |

## Design Decisions

### Decision: Report status by projecting core's reports, never by recomputing

- **Context**: prov R1.4/R13 straddle a seam; core computes, this spec reports.
- **Alternatives Considered**:
  1. Recompute a reporting-friendly status from the graph directly.
  2. Project `CompletenessReport` and `ChainValidationReport` into a reported status vocabulary.
- **Selected Approach**: (2). A pure function maps the two upstream reports onto a five-value reported vocabulary — `complete`, `partially_traceable`, `incomplete`, `undetermined`, `unvalidated` — with `unvalidated` dominating everything else, and carries upstream reasons through verbatim.
- **Rationale**: Two sources of truth for "is this traceable?" is the exact failure the provenance track exists to prevent.
- **Trade-offs**: The reported vocabulary is larger than core's, because `partially_traceable` (prov R13.2) has no core counterpart; it is derived from mixed child states, which is a presentation concern and therefore legitimately owned here.
- **Follow-up**: A test asserts the status module never calls a core computation entry point.

### Decision: Adopt W3C PROV-O JSON-LD as the interoperable representation

- **Context**: prov R11.1–R11.3.
- **Alternatives Considered**:
  1. Invent an EviTrace-specific export schema.
  2. Reuse W3C Web Annotation (the format already produced in-repo).
  3. Adopt W3C PROV-O, emitted as JSON-LD by hand.
- **Selected Approach**: (3).
- **Rationale**: PROV-O is the standard for exactly this data and maps cleanly onto core's node kinds; hand-emitted JSON-LD needs no new dependency. Web Annotation models annotations on a document, not derivation, and reusing it would create a second producer of a single-producer format.
- **Trade-offs**: PROV-O cannot carry anchor precision, privacy carriers, severity, or missing segments — which is precisely what the fidelity report reports.
- **Follow-up**: A test asserts the Web Annotation context IRI never appears in this subpackage.

### Decision: Drive the fidelity report from the same mapping table the exporter uses

- **Context**: Requirement 6 plus the brief's "generated from the projection mapping itself".
- **Alternatives Considered**:
  1. Maintain a hand-written list of omitted fields.
  2. Declare a mapping table, drive the exporter from it, and compute omissions by diffing it against the runtime dataclass field inventory.
- **Selected Approach**: (2).
- **Rationale**: A hand-written list silently rots the first time core adds a field; the diff cannot.
- **Trade-offs**: The exporter is table-driven rather than straight-line code, which is marginally less direct to read.
- **Follow-up**: A coverage test computes the inventory rather than enumerating it, so it fails automatically when core grows a field.

### Decision: Declared slot registry with three absence kinds

- **Context**: multiagent R22.2 names sixteen slots, seven of which belong to unbuilt specs.
- **Selected Approach**: A registry declares every slot with its owning spec. A finalized package classifies each slot as `filled`, `absent` (expected, with reason), or `not_applicable` (capability not present in this run, with the owning spec named).
- **Rationale**: Requirement 8.4 forbids presenting an incomplete package as complete; requirement 8.6 forbids conflating "not built yet" with "should have been there". Both are needed or the package is either permanently red or misleadingly green.
- **Trade-offs**: Downstream specs must flip their slot from `not_applicable` to expected when they ship — a deliberate, greppable revalidation point.

### Decision: Extend the existing `provenance` configuration block rather than adding a top-level key

- **Context**: The repository rejects unregistered top-level YAML keys.
- **Selected Approach**: Add nested `export`, `audit_package`, and `sharing` sub-blocks under the `provenance` block that `provenance-core` registers.
- **Rationale**: No new top-level key means no new registration and one coherent provenance configuration surface.
- **Trade-offs**: This spec must not ship before core registers the block; that dependency already exists.

### Decision: Sharing marking is a lookup, not a judgement

- **Context**: prov R9.5 is a marking obligation; the disclosure decision belongs to `privacy-core`.
- **Selected Approach**: The marking is `restricted` unless a disclosure decision is present on the graph's privacy carriers, and when present the decision string is recorded verbatim and resolved to a level through a configuration-supplied decision→level map that defaults to empty. An unmapped decision resolves to `restricted`.
- **Rationale**: Provenance never interprets a label; configuration does. Fail-closed by default.
- **Trade-offs**: An operator must configure the map before any artifact is marked shareable, which is the intended safe default.

## Risks & Mitigations

- Core's report shapes drift after this spec is written — mitigated by listing them as revalidation triggers in both specs and by consuming them as whole objects rather than field-by-field copies.
- Audit packages inflate disk usage on large corpora because raw parser output is retained per document — mitigated by a configuration flag for raw-output retention and by storing references to the existing content-addressed TEI cache where the payload is already on disk.
- The seven unbuilt R22 slots make packages look empty for a long time — mitigated by `not_applicable` classification naming the owning spec.
- The reproducibility reference drifting against `cost-and-run-reporting`'s manifest filename, location, or version — mitigated by a revalidation trigger against that spec's manifest shape, a shared filename constant asserted equal by test, and an explicit unavailable path when the reference does not resolve. Secrets can no longer reach this section at all, because no configuration mapping is captured here.
- A downstream spec writing directly into the audit package directory instead of through the slot contract — mitigated by the slot registry recording the supplying stage for every filled slot, so an undeclared write is invisible in the inventory and therefore reported as an absent slot.

## References

- W3C PROV-O, the PROV Ontology — vocabulary adopted for the interoperable export.
- W3C Web Annotation Data Model — the format already produced in-repo by `src/artifact_generation/w3c_annotation.py`; deliberately *not* reused here.
- `.kiro/specs/provenance-core/design.md` — upstream graph, report shapes, and projection contract.
- `.kiro/specs/xtrace-toolkit/requirements.md` — `R-X-2` reproducibility manifest (owned by `cost-and-run-reporting`, consumed here by reference) and `R-GOV-1` append-only ledger (delivered here as a projection export).
- `.kiro/specs/cost-and-run-reporting/design.md` — `run_manifest.json`, `RUN_MANIFEST_FILENAME`, and `manifest_version`: the referenced artifact and the single implementation of `R-X-2`.
- `.kiro/specs/archive/original-idea-documents/provenance_requirements.md` — prov R9, R11, R13.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — multiagent R5.9, R6.4–R6.6, R22.
