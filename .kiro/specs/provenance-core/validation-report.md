# Validation Report — provenance-core

Feature-level GO/NO-GO (`/kiro-validate-impl provenance-core`), run 2026-10-11 against HEAD `792dbf1` plus the documentation remediation committed with this report.

## Decision: GO

All 39 sub-tasks are `[x]` (the seven group headers 1–7 stay unticked, as in `risk-remediation`), no task is `_Blocked:_`, and every sub-task has exactly one `feat(provenance-core): <id> …` commit. No Critical finding. The Warnings below are recorded for the user; none is an unmet requirement text. **Section 3 lists the decisions that need the user.**

## 1. Mechanical results

| Check | Result |
|---|---|
| Full suite incl. slow (`pytest -q -m ""`) | PASS — 4065 passed, 3 skipped, exit 0 |
| Fast suite (`pytest -q`) | PASS — 4043 passed, 2 skipped, 23 deselected |
| `tests/test_dependency_directions.py`, `tests/steering` | PASS (17 / 43) |
| TODO/FIXME/HACK grep over feature files | CLEAN |
| Secrets grep | CLEAN (one match: `OPENAI_API_KEY` env lookup in `config_utils.py`, pre-existing) |
| Smoke boot (`python main.py --help`) | PASS, exit 0 |
| Real end-to-end run (below) | PASS |

## 2. Validation dimensions

**Requirements coverage** — 63 acceptance criteria (1.1–10.6): every one mapped to ≥1 task, production code and a test; 56 COVERED, 7 PARTIAL (2.5, 4.5, 6.1, 6.2, 6.7, 9.2, 10.1), 0 GAP. 27 of 28 scratch mutants were killed by the named test (the survivor, an aliasing evasion of the 9.4 scanner, is a documented heuristic limit). The PARTIALs are deliberate decisions (anchor rule B2, closed vocabulary 10.1) or scope outside this spec (privacy attach path 9.2, evidence-pruning/normalize steps 6.x).

**Design alignment / boundary audit** — dependency direction verified by a separate AST scan (including function-local and `TYPE_CHECKING` imports): clean. `quality_control`, `agents`, `pdf_extractor`, `text_processing`, `artifact_generation` and `main.py` are untouched. Single-owner rules hold (graph.py attribution, identity.py hashing, serialization reads no config, pipeline computes fingerprints via `manifest.compute_identity`). Every hunk in existing pipeline modules is additive and default-off (`provenance=None` is byte-identical). Design drift was documented in the remediation commit (see §4).

**Cross-task integration (real run)** — `orchestrator.run_pipeline` with the real `build_qc_bundle`, evidence index, validator, repair loop, `process_pdf`, manifest and provenance; only GROBID, OpenAI, the sentence tokenizer and PyMuPDF were stubbed. 3 documents: 1137 nodes (926 evidence, 186 claim, 21 derivation, 3 source, 1 validation), 1736 edges, 0 missing segments, 42 warnings / 0 error / 0 critical. All edge endpoints and step inputs/outputs exist; every claim has a producing step and a claim→evidence→source path; one `src:<sha>` is used everywhere; artifacts round-trip and rewrite byte-identically; two runs and a `pdf_concurrency=4` run are byte-identical. With provenance on vs off: `*.extracted.json` (modulo workdir path), `manifest.json`, evidence cache and the 15 `extract_chunk` calls (arguments, prompt-source hashes) are identical. Resume, same-content PDFs, provider outage + GROBID down, `fail_run_on_severity`, invalid `output_subdir` and a stale pre-6.6 evidence cache all behave as designed.

## 3. Open items needing a user decision (Warnings)

- **W1 — "not reported" model claims make real runs `incomplete`.** On the clean real run, 36 of 186 claims (value `nr`, no `loc`) raise `unsupported_claim`, so every document and the run read `incomplete` (reason `unsupported`); with an `nr`-free stub the same run reads `complete` with 6 warnings. Neither requirements nor design mention `nr` for model claims. Options: treat a model `nr` like prefill (claim-level only, a dedicated overridable finding code, e.g. `nr_unsupported`), or accept and document. This makes the run/document completeness signal nearly constant on real data until decided.
- **W2 — scale.** 100 documents × ~500 evidence nodes: whole-run wall time 25.5 s → 42.5 s with provenance on, RSS 70 MB → 706 MB, artifact 95 MB (≈950 KB/doc, ≈17× the extraction JSON); `recorder.snapshot()` per document is quadratic under the recorder lock (4.1 s of 203 calls). Fine for tens to hundreds of PDFs; ~1000 documents extrapolates to ~20 min of lock-held CPU, multi-GB RSS and a ~1 GB single JSON — it could OOM a long run. Remedy: public `ProvenanceRecorder.run`/`get_node` accessor (no per-document snapshot), and a decision on artifact size (digests only today; per-document files would be a schema/major-version change).
- **W3 — enabled vs disabled are not identical on one path.** With provenance enabled, retry exhaustion on reference-only `loc` problems retains the output with a critical `dangling_reference` finding (Req 5.4) instead of failing the document. `enabled: true` is the default, so this is live. Documented in design.md (Out of Boundary), CHANGELOG and the 6.4 note.
- **W4 — a critical `invalid_identifier` finding (node_id=None) does not degrade completeness.** Reproduced: 2 critical findings, document and run `complete`. Reachable only through a corrupt or hand-edited evidence cache. Fix: attribute by `details["source_id"]`, or mark `evidence_index` failed for the document when any item is dropped.
- **W5 — empty / fully-resumed run writes no artifact** (INFO log) instead of an `undetermined` artifact, because `run_identity_from_identities` refuses an empty set. Design amended to say so; changing it needs a `src/provenance` change.
- **W6 — stage status is not serialized** (schema decision; a new top-level key is a major bump under the compatibility rule). A reader sees `chain_validation` only via `validation.completed` and the absence of a completeness gap; stages that never ran (`not_run`) are never reported missing (policy 4.5). Consider in provenance-audit-export.
- **W7 — config fingerprint scope.** `sanitize_extracted_values` / `exported_value_normalizer` (from `load_local_config`), `agent_schema.json` and `final_output_schema.json` are not in the config fingerprint (the manifest's own hash omits them too). Two runs differing only there share `run_id`; `run_id` is a configuration identity, `created_at` distinguishes runs.
- **W8 — no designed path for privacy-core to put a carrier on a node.** Adapters hard-code `UNKNOWN_PRIVACY`; nodes are frozen; re-recording a changed node yields `duplicate_identity`. privacy-core (`populate_carrier`) needs an adapter parameter, a pre-record hook or a graph-level attach. Decide before implementing privacy-core.
- **W9 — header metadata nodes are always `absent`** (GROBID header elements carry no coords/xml:id), so TEI-sourced prefill claims read `missing_anchor` at claim level (≈5 warnings per 3 documents). Decided 2026-10-09; listed for completeness.

## 4. Documentation remediation applied with this report

CHANGELOG entry (2026-10); design.md amended (new modules, signatures, exhaustion-retain declared in Out of Boundary, disabled path passes `provenance=None`, empty-run behaviour, `fail_run_on_severity` wording, adapter dependency wording, `PROVENANCE_DIR` default-only); `.kiro/steering/{config,testing}.md`, `configs/README.md`, `src/pipeline/README.md`, `src/utils/README.md` synced; new `src/provenance/README.md`; `test_provenance_contracts.py` no longer uses the real downstream stage name `routing_locator` as an "undeclared" example; `spec.json` phase → `implemented`.

## 5. Follow-ups outside this feature (not done; listed for the user)

- **Stale references left untouched** (outside the remediation scope): `CLAUDE.md:75,93` and `.kiro/steering/product.md:19-54,87,134` and `roadmap.md:66` do not mention `src/provenance` / the new pipeline modules / the 8 dependency rules; comments in `configs/config.yaml:123`, `src/utils/config_utils.py:258-268` ("recorder no-op", "run summary") and `src/utils/path_utils.py:91` (`PROVENANCE_DIR` unused) are stale.
- **Downstream specs** to correct (read-only here): evidence-routing `design.md` L317, 362, 1163, 1300, 1360 and multiagent-extraction `design.md` L397, 1208, 1347, 1417 call `provenance.format_evidence_id`, which is not exported (it is `provenance.identity.format_evidence_id`; or add a one-line re-export); evidence-routing/multiagent-extraction `design.md` L1164 / L1209 say a disabled recorder is a no-op — the orchestrator now passes no recorder; provenance-audit-export expects `stage_failures` from recorded per-stage failure reasons that are not serialized (derive from `missing_segments` with `record_kind == "stage"`); reviewer-ui expects evidence text in the artifact (it is content-free — digests only; text comes from the evidence cache, and the two header nodes are not in that cache); cost-and-run-reporting plans its own `RunProvenance` in `pipeline/run_manifest.py` (name collision with `pipeline/run_provenance.py`) and a resume gate that must stay in sync with `pdf_processor.is_resumed`.
- **Code follow-ups** (from the implementation notes): public `ProvenanceRecorder.run`/`get_node`; public accessor for the `provenance` YAML block instead of the private `config_utils._load_config_yaml`; derive `run_provenance._SEVERITY_RANK` (drift guard); multi-chunk conflict-path validator finding names the base claim id (unreachable with the stock chunk partition — `domain_to_chunk` is derived from `num_chunks`, field ranges are disjoint); `fail_run_on_severity` has no machine-readable run summary; fallback author/year prefills are labelled confidence `h` (pre-existing out-of-scope defect).
