# Brief: corpus-and-schema-builder

## Problem
Evidence-synthesis reviewers work one review question at a time, over a curated set of papers, with a field list specific to that review. EviTrace offers neither: there is no project concept, no way to add documents other than dropping files in a directory, and the field list is a checked-in file the reviewer cannot change without editing the repo. A second review means a second checkout. Invalid inputs are discovered mid-pipeline rather than at admission, and evidence a reviewer already extracted elsewhere (a screening spreadsheet) cannot be brought in at all.

## Current State
- Input is a bare directory: `pdfs_path: "input"` in `configs/config.yaml`, resolved to `PDF_DIR` at `src/utils/path_utils.py:84`. No upload step, no admission-time PDF validation, no per-project config.
- Document identity exists but only as a pipeline-internal detail: `compute_identity()` (`src/pipeline/manifest.py:104`) computes `pdf_content_hash` (sha256), `config_hash`, `extraction_map_hash`, `model_id`, `schema_version` — used for staleness detection, not as a user-visible document ID.
- Corpus status today is the manifest's per-PDF `status`: `complete` (`manifest.py:281`), `failed_qc_pipeline` (`orchestrator.py:130`), `failed_chunks` (`pdf_processor.py:1132`). No uploaded/parsed/reviewed/flagged vocabulary, no corpus-level rollup.
- The schema is static: `configs/extraction_map.json`, a flat 62-object array across 13 `domain_group`s with keys `field_index`/`field_name`/`definition`/`reviewer_question`/`format`/`categories_or_examples`. No criticality, no evidence requirement, no typed allowed-values, no versioning, no builder or API.
- No external-evidence ingest anywhere; `src/artifact_generation/csv_exporter.py` is export-only.

## Desired Outcome
A reviewer creates a project (multiagent R1.1), admits PDFs with hash-stable document IDs (R1.2) and recorded filename/size/page-count/timestamp/status (R1.3), sees invalid files rejected with a reported reason (R1.4), runs batches without collapsing per-document audit trails (R1.5), and reads corpus-level status across uploaded/parsed/extracted/reviewed/failed/flagged (R1.6). The same reviewer defines fields with criticality and evidence requirements (R2.1–R2.2), imports a schema from CSV/Excel/JSON/YAML with ambiguous columns held for human mapping (R2.5–R2.6), and gets schema versioning that pins the version used by prior outputs (R2.7). External evidence tables import, fuzzy-match to canonical text, use page/citation hints, and land in an unresolved queue when unplaceable (R20.1–R20.5, R20.7).

## Approach
Headless-first, per the roadmap's governing decision: this spec ships a project/corpus store, a versioned schema model, and importer/validator libraries with CLI entry points — no UI. Schema stays declarative and file-backed (project directory plus versioned schema documents) rather than database-backed, matching xtrace R-X-1 and the repo's config-driven design. `extraction_map.json` becomes the default seed schema, migrated into the new model rather than deleted, so existing runs keep working.

## Scope
- **In**: project record and per-project configuration profile; PDF admission with validation and hash-derived document IDs; per-document metadata and status vocabulary; corpus status rollup; the extraction-field model (criticality, data type, allowed values, evidence requirement, review instructions); schema versioning and version pinning of outputs; schema import and column mapping from CSV/Excel/JSON/YAML; external evidence import with fuzzy matching and an unresolved queue.
- **Out**: LLM-assisted field generation and mandatory human approval of LLM-proposed schemas (multiagent R2.3–R2.4), deferred pending the privacy gate and scheduled as a follow-on phase of this spec (see Downstream); any UI for the builder, mapping, or unresolved queue (R20.6), which belongs to `reviewer-ui`; multi-user access control (multiagent R26.6), deferred and recorded as out-of-scope by `privacy-core`; reviewer identity / anonymized reviewer ID (multiagent R26.7), owned by `reviewer-ui`; parser and canonical-document work (R3–R6), already built.

## Boundary Candidates
- Project/corpus store and document admission vs. the existing per-run manifest checkpointing.
- Schema authoring and versioning vs. schema *consumption* by prompt builders and `src/pipeline/extraction_map.py`.
- Import parsing and column mapping vs. evidence anchoring — matching to canonical text is delegated, not reimplemented.

## Out of Boundary
- Defining evidence node identity — owned by `provenance-core`, consumed here.
- Producing W3C annotations for imported evidence — `src/artifact_generation/w3c_annotation.py` stays the sole producer.
- Enforcing the `critical` flag in routing, extraction, verification, and repair (R2.2 downstream behaviour).

## Upstream / Downstream
- **Upstream**: `src/utils/config_utils.py` (new top-level YAML keys register in `_ALL_KNOWN_TOP_LEVEL_KEYS`), `src/utils/path_utils.py`, `src/pipeline/manifest.py`, `configs/extraction_map.json`, `src/text_processing/` matchers for fuzzy import matching.
- **Downstream**: `evidence-routing` and `multiagent-extraction` (field criticality, evidence requirements), `cost-and-run-reporting` (schema version in run metadata), `reviewer-ui` (corpus view, builder surface, unresolved-evidence queue), `evaluation-harness`. LLM-assisted candidate field generation with mandatory human approval (multiagent R2.3–R2.4) returns as a follow-on phase of this spec once `privacy-core`'s LLM gateway ships, so the deferral has a landing point rather than being dropped.

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` R-X-1 (declarative config, no hardcoded domain vocabulary) and R-X-3 (per-PDF resumability) — generalized from directory-scoped to project-scoped.
- **Adjacent**: `provenance-core` (evidence node identity — consume, never redefine); `provenance-audit-export` (per-document audit packages); `privacy-core` (sensitivity labels on admitted documents).

## Constraints
Python 3.12.x, `src/` layout. Spreadsheet/Excel readers must be lazy, optional extras — no new heavy top-level imports, matching the PyMuPDF/faiss pattern. Dependency direction is AST-test enforced (`tests/test_dependency_directions.py`): a new corpus/schema package must not be imported by `quality_control` or `text_processing`. Document IDs stay content-derived so re-uploading an identical file is idempotent and consistent with the existing manifest hash. No PHI or real patient data in fixtures.
