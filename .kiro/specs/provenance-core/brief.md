# Brief: provenance-core

## Problem
Biomedical AI researchers, clinical evaluators, and institutional reviewers cannot currently answer "which exact span of which exact document version supports this extracted field?" without reading EviTrace's internals. Traceability exists only as incidental by-products of unrelated stages, so an output that lost its evidence link is indistinguishable from one that never had it (prov R1.4). That makes extraction results undefensible in review settings.

## Current State
Pieces exist, none of them compose into a provenance model:
- `src/pipeline/evidence_index.py` builds ranked evidence items from GROBID TEI with stable per-run IDs (`S%06d` sentences, plus table/figure counters), each carrying `section_path`, `page`, `coords`, `xpath`, `source_pdf`, `score` — this is a de-facto evidence node in all but name (prov R3.1–R3.3).
- `src/pipeline/validator.py` already validates LLM `loc` references against `valid_location_ids` and resolves them via `evidence_map` — a partial, un-recorded implementation of prov R4.3; a bad reference raises `ValidationError` instead of recording a validation issue (prov R4.4).
- `src/pipeline/manifest.py::ManifestIdentity` holds `pdf_content_hash`, `config_hash`, `extraction_map_hash`, `model_id`, `schema_version` — most of prov R2.1–R2.3 already exists, scoped to cache invalidation rather than identity.
- `src/quality_control/reconciler.py::_build_provenance_dict` produces a flat extractor-name dict. It is not a graph, has no edges, node types, or derivation steps.
- `src/artifact_generation/w3c_annotation.py` is the sole W3C annotation producer; `project()` reads only `unified.semantic` / `unified.alignment`.
- There is **no** `src/provenance/` package. Derivation tracking (prov R5), graph construction (prov R8), and chain validation (prov R6) have no implementation at all.

## Desired Outcome
A `src/provenance/` package defines source objects, evidence nodes, claims, derivation steps, and validation results as typed, graph-compatible records; the pipeline emits them as it runs; a chain validator reports missing/orphan/cyclic/unanchored structures by severity (prov R6.4–R6.5); every downstream spec consumes one canonical evidence-node identity instead of inventing its own.

## Approach
Model-first, then adopt. Define the node/edge type system and identity rules, back-fill them from the artifacts already produced (evidence index items → evidence nodes, `ManifestIdentity` → source identity, `loc` validation → claim→evidence edges), and only then add the genuinely new parts: derivation steps and the graph builder. This avoids a parallel store: existing IDs are promoted, not duplicated.

## Scope
- **In**: prov R1 (first-class subsystem, provenance-incomplete marking); R2 (source document identity from content fingerprints); R3 (evidence node model with anchors and explicit anchor-absence); R4 (claim→evidence links, unsupported-claim marking, reference validation issues, derivation of merged/synthesized claims); R5 (derivation steps, deterministic vs probabilistic distinction, many-to-one and one-to-many); R6 (graph validation, severity classification); R8 (typed graph construction, partial-graph preservation, schema/version metadata); R14 (extensibility: new evidence types, parser-specific metadata, namespaced extension fields, version-forward interpretability).
- **In**: pinning the privacy↔provenance interface — the read-only shape of privacy labels and disclosure decisions that provenance nodes carry.
- **Out**: audit artifacts and export formats (provenance-audit-export); public/private view generation, vaults, commitments, tamper-evidence (public-private-provenance); any privacy classification or decision logic; graph visualization; a query language; storage-engine or graph-library selection beyond what the in-repo JSON artifacts require.

## Boundary Candidates
- Identity and fingerprinting (source + evidence node ID derivation) vs. relationship modelling (edges, derivation steps).
- Record emission by pipeline stages vs. graph assembly from emitted records.
- Graph assembly vs. chain validation and severity classification.
- The privacy-label carrier field on a node (owned here) vs. who populates it (owned by privacy-core).

## Out of Boundary
- Deciding *whether* a node may be disclosed. **Privacy decides, provenance consumes** — this spec defines only the label/decision struct provenance nodes carry and consults, per the roadmap's resolution of the prov R7.5 ↔ priv R1.3 mutual recursion. `provenance-core` pins that interface; it never implements a policy.
- Emitting run-level audit packages or interoperable exports.
- Replacing `w3c_annotation.py` as the W3C producer; provenance feeds it, does not fork it.

## Upstream / Downstream
- **Upstream**: `src/pipeline/evidence_index.py`, `src/pipeline/manifest.py`, `src/pipeline/validator.py`, `src/quality_control/reconciler.py`, `src/artifact_generation/w3c_annotation.py`.
- **Downstream**: privacy-core, provenance-audit-export, public-private-provenance, evidence-routing, reviewer-ui, cost-and-run-reporting.

## Existing Spec Touchpoints
- **Extends**: xtrace-toolkit — de-duplicate `R-GOV-1` (append-only decision ledger) and `R-X-2` (per-run reproducibility manifest) at design time; the ledger must become a projection of this graph, never a parallel store. `R-X-2`'s per-artifact hashes overlap `ManifestIdentity`.
- **Adjacent**: privacy-core (owns labels/decisions); provenance-audit-export (owns run artifacts); evidence-routing (consumes evidence node identity, must not redefine it); risk-remediation (its Requirement 1 fixes silently-rejected final-output writes — nothing here is verifiable until it ships).

## Constraints
- Python 3.12.x, `src/`-layout. `src/provenance/` must declare its dependency direction and add it to the AST-enforced rules in `tests/test_dependency_directions.py` before any cross-package import lands; `quality_control` must not import it.
- Heavy optional deps (graph libraries, crypto) stay lazily imported inside function bodies.
- Any new top-level YAML key must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`.
- `_shared_paper_prefix` prompt-cache stability must survive any change touching the LLM call path.
- No PHI, credentials, or real patient data in fixtures.
