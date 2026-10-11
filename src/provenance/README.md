# `provenance/` — Claim-to-Source Provenance Graph

A leaf package that records which source document, evidence items and
derivation steps produced each extracted claim, validates the resulting chain,
computes completeness at claim, document and run level, and serialises one
run-scoped artifact. It knows nothing about PDFs, GROBID or model providers:
adapters take plain mappings, and [`pipeline`](../pipeline/README.md) does all
the wiring (`document_provenance.py`, `claim_provenance.py`,
`run_provenance.py`). Provenance never aborts extraction.

Design, requirements and the artifact schema:
[`.kiro/specs/provenance-core/`](../../.kiro/specs/provenance-core/design.md).

---

## Module map (dependency order; each imports only modules above it)

| Module | Role |
| ------ | ---- |
| `privacy_carrier.py` | `PrivacyCarrier`, `UNKNOWN_PRIVACY`, `check_carrier_conformance` — stored verbatim, never interpreted |
| `models.py` | All record types and closed vocabularies (nodes, edges, findings, reports); `is_extension_key` |
| `identity.py` | Source, run, evidence, claim, derivation and validation id derivation/parsing; value and text digests (stdlib only) |
| `contracts.py` | `STAGE_CONTRACTS`, `STAGE_ORDER`, `require_declared_stage`, `UnknownStageError` |
| `recorder.py` | `ProvenanceRecorder`: thread-safe append-only sink (`record_*`, `issue_sink`, `mark_stage_complete/failed`, `snapshot`) |
| `graph.py` | `build_graph`, `ProvenanceGraph`, `attach_validation`, `attach_completeness` |
| `validation.py` | `validate_chain` — chain checks and findings |
| `completeness.py` | `compute_completeness` — complete / incomplete / undetermined per claim, document, run |
| `projection.py` | `project_event_log` — deterministic ordered view of the graph |
| `serialization.py` | `graph_to_dict`, `graph_from_dict`, `write_graph`, `read_graph`, schema-version guard |
| `adapters/` | Mapping to record conversion: `source_node_from_identity`, `run_identity_from_identities`, `evidence_node_from_item(s)`, `claim_nodes_from_fields`, `derivation_from_stage_event`, `derivations_from_qc_provenance` |

`from provenance import ProvenanceRecorder, ...` exposes the models, the
recorder and the graph API.

## Artifact

`provenance_graph.json` (schema id `evitrace/provenance-graph`, version in
`graph.PROVENANCE_SCHEMA_VERSION`) is written once per run to
`<main_output_dir>/run_<ts>/<provenance.output_subdir>/` (default
`provenance`). It stores references and digests, never document text.
Settings: [`provenance` in configs/README.md](../../configs/README.md). Read
it back with `serialization.read_graph(directory)`.

## Extending

- **Extension fields.** Node `extensions` accept only namespaced keys
  `x-<namespace>-<field>` (lower-case
  alphanumeric namespace, e.g. `x-manifest-output_path`,
  `x-pipeline-chunk`); `x-multiagent-*` is reserved for the
  `multiagent-extraction` spec. Values must be JSON-native.
- **New pipeline stages.** Extend BOTH `STAGE_CONTRACTS` and `STAGE_ORDER` in
  `contracts.py`, in the same commit as the emitting code, with non-empty
  required record kinds. Always record through `ProvenanceRecorder`; never
  catch `UnknownStageError` to skip a declaration or keep a private stage list.
  `tests/src/provenance/test_provenance_stage_contract_drift.py` fails with the
  exact dictionary entries to add for any new or renamed stage-taking function.
- **Closed vocabularies** (node, edge and derivation kinds) and schema
  changes follow the compatibility rule in the design (major bump for
  top-level additions).

## Dependency direction

`provenance` imports only `utils` (in practice `utils.logging_utils`) and the
standard library. It must not import `pipeline`, `agents`, `pdf_extractor` or
`quality_control`, and `quality_control`, `agents`, `pdf_extractor` and
`text_processing` must not import it. `pipeline` is the sole integrator. Both
directions are enforced by `tests/test_dependency_directions.py`; import
isolation is additionally checked by
`tests/src/provenance/test_provenance_import_isolation.py`.
