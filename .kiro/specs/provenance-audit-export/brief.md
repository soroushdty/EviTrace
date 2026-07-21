# Brief: provenance-audit-export

## Problem
A completed EviTrace run leaves nothing an external reviewer can inspect. Academic evaluators, manuscript reviewers, and IRB-adjacent readers need a per-run artifact they can read outside the runtime (prov R9.1, R11.2), compare against a prior run (prov R9.4), and trust to declare its own gaps. Today, an incomplete trace and a complete one produce output files that look identical — the failure mode the whole provenance track exists to eliminate (prov R13.2).

## Current State
- `src/pipeline/manifest.py` writes a per-PDF manifest with `ManifestIdentity` (`pdf_content_hash`, `config_hash`, `extraction_map_hash`, `model_id`, `schema_version`) and a completion marker. It is a cache-invalidation record, not an audit artifact: no evidence references, no claim references, no transformation summary, no validation status.
- Final outputs are `outputs/<paper>.extracted.json` plus `outputs/qc_report.csv` from `generate_qc_report()`. Neither is versioned as a provenance artifact, neither declares partial fidelity.
- `src/artifact_generation/w3c_annotation.py` is the only interoperable export EviTrace has (`generate_w3c_jsonld()`). It exports annotations, not claim→evidence provenance, and reports nothing about fields it cannot represent (prov R11.4).
- `src/pipeline/validator.py` raises `ValidationError` on an unknown `loc` ID; there is no state between "valid" and "crashed", so "provenance-incomplete" cannot currently be represented anywhere.
- No `src/provenance/` package exists — this spec builds strictly on the graph that `provenance-core` will produce.

## Desired Outcome
Every run emits a versioned, self-describing run-level provenance artifact containing source identities, evidence and claim references, transformation summaries, validation status, and run metadata; it can be exported into at least one interoperable representation that names exactly which fields were lost or transformed; and `provenance-incomplete` / `partially-traceable` are first-class reportable states visible at claim, document, and run level.

## Approach
Serializer over the core graph, not a second data model. The run artifact is a projection of `provenance-core`'s graph plus run metadata; the interoperable export is a second projection with an explicit fidelity report emitted as data. Partial-fidelity reporting is generated from the projection mapping itself rather than hand-maintained, so an unmapped node type surfaces as a reported omission instead of a silent drop.

## Scope
- **In**: prov R9 (run-level artifact contents, versioning, cross-run comparability, internal/restricted marking); prov R11 (native structured format, runtime-independent shareable representation, preserved claim→evidence relationships, omitted/transformed-field reporting, schema/version info); prov R13 (stage-level failure reporting, partially-traceable marking, missing-anchor vs unsupported-claim distinction, recorded reason for incomplete validation, status visible at claim/document/run level).
- **In**: multiagent R22 (per-document audit package covering stage outputs, preservation of original vs human-edited output, route/evidence backing an accepted output, manual-review reason, exportability for reproducibility and manuscript supplement).
- **Out**: the graph, node types, identity, and chain-validation logic (provenance-core); public vs private view generation, redaction of artifact contents, commitments, tamper-evidence and integrity markers (public-private-provenance); token/cost accounting (cost-and-run-reporting); multi-agent stage outputs themselves — this spec defines the container, the producing specs fill their slots.

## Boundary Candidates
- Run artifact assembly (what goes in) vs. serialization format (how it is written).
- Interoperable projection vs. the fidelity/omission report that projection produces.
- Provenance status computation (a graph-validation result) vs. status surfacing at claim/document/run level.
- Audit-package slot contract vs. the stage producers that populate slots.

## Out of Boundary
- Deciding whether an artifact may be shared externally. prov R9.5 ("mark as internal or restricted") is a *marking* obligation here; the disclosure decision comes from privacy-core via the interface pinned in provenance-core — privacy decides, provenance consumes.
- Signing, hashing for tamper-evidence, or any cryptographic integrity marker (prov R10 → public-private-provenance).
- Re-litigating the token-budget thresholds set by the completed token-efficient-extraction spec.

## Upstream / Downstream
- **Upstream**: provenance-core (required); `src/pipeline/manifest.py`, `src/pipeline/orchestrator.py`, `src/artifact_generation/w3c_annotation.py`, `generate_qc_report()`.
- **Downstream**: public-private-provenance (redacts these artifacts into public views), reviewer-ui (renders status and audit packages), evaluation-harness (consumes run artifacts for ablation comparison), cost-and-run-reporting (contributes a cost slot).

## Existing Spec Touchpoints
- **Extends**: xtrace-toolkit — `R-X-2` (per-run reproducibility manifest: git commit, environment, seeds, resolved config, per-artifact hashes) is the same artifact as prov R9.2 and must be unified here, not shipped twice; `R-GOV-1`'s append-only ledger is a projection of the core graph and is exported through this spec's serializers.
- **Adjacent**: cost-and-run-reporting (multiagent R23/R27 — owns cost content, this spec owns its placement in the package); corpus-and-schema-builder (multiagent R1–R2 per-document audit trails on batch upload — must share this container); multiagent-extraction and evidence-routing (produce stage outputs referenced by multiagent R22.2).

## Constraints
- Python 3.12.x, `src/`-layout; must respect the dependency direction declared for `src/provenance/` in `tests/test_dependency_directions.py`.
- `configs/agent_schema.json` and `configs/structure_schema.json` remain single-owner (`src/agents/validator.py`, `src/quality_control/structure_validator.py`) — never read them here.
- Artifact schema versioning must let older artifacts stay interpretable (prov R14.5); no breaking rewrite of the existing manifest schema without a version bump and migration.
- New top-level YAML keys registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` (`src/utils/config_utils.py`).
- No PHI, credentials, or real patient data in fixtures.
