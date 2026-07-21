# Research & Design Decisions — privacy-core

## Summary

- **Feature**: `privacy-core`
- **Discovery Scope**: New Feature (greenfield subsystem) with a Complex Integration seam into the existing model-call path
- **Key Findings**:
  - Privacy is confirmed 100% greenfield. A case-insensitive sweep of `src/`, `configs/`, and `main.py` for `redact|deidentif|phi|vault|pseudonym|sensitiv|classif|audit_trail|privacy|anonym` returns only false positives (`_classify_block` in the reconciler, `classify_page` in the scan detector, "case-insensitive" in docstrings). The nearest adjacent concept, `sanitize_extracted_values` in `_LOCAL_DEFAULTS`, is CSV value normalization, not privacy.
  - The evidence package that carries paper text off-box is a **single JSON string built exactly once per document**: `build_paper_evidence_package(...)` at `src/pipeline/evidence_index.py:1062`, assigned at `src/pipeline/pdf_processor.py:1263-1270` and then copied into `chunk_sources[chunk_num]` for every chunk (`:1279`) precisely so the bytes are identical across calls. This single choke point is what makes prompt-cache-safe governance possible.
  - `agents/openai/api_client.py` binds the credential at import time (`OPENAI_API_KEY = _openai_config["api_key"]` at `:23`, `_client = AsyncOpenAI(**_client_kwargs)` at `:40`). No managed access path, no key-version identity, no dev/prod distinction, and no rotation without a process restart. Requirement 2 cannot be met without changing this.
  - `logging_utils.py` contains **no** redaction filter of any kind. The only privacy-adjacent behavior is truncation plus SHA-256 digesting in `log_model_response`. Both `api_client.py` DEBUG lines (`prompt_cache_key`, raw-response preview) bypass every existing control.
  - `src/provenance/` does not exist yet; `provenance-core` is fully specced and unimplemented. `privacy-core` must be designed against the pinned `PrivacyCarrier` contract rather than against running code.

## Research Log

### Where can a gateway interpose without breaking `_shared_paper_prefix`?

- **Context**: The roadmap and the brief both make prompt-cache stability a hard constraint. `_shared_paper_prefix` must be byte-identical across warmup, every extraction chunk, and synthesis for one PDF.
- **Sources Consulted**: `src/agents/openai/prompts.py:27` (`_shared_paper_prefix`), `:93` and `:121` (its only two callers, both internal to `prompts.py`), `src/agents/openai/api_client.py:98` (`paper_cache_key`), `:134` (`_base_request_kwargs`), `src/pipeline/pdf_processor.py:1263-1279`, `src/pipeline/evidence_index.py:1062`.
- **Findings**:
  - `_shared_paper_prefix(source_package)` is a pure function of one argument. Its literal framing lines are constants; the only variable material is the evidence JSON string.
  - `paper_cache_key(source_package)` is `sha256(source_package)[:16]`, and `compute_stable_prefix(get_system_prompt(), source_package, "")` feeds the telemetry fingerprint. Both hash the same string.
  - Therefore *transforming* `source_package` does not by itself break cache stability. What breaks it is transforming it **more than once per document** or transforming it **non-deterministically**.
- **Implications**: The design adopts **govern once, transmit many**. Privacy produces exactly one packet payload per document, before `chunk_sources` is populated, and every subsequent call reuses that identical string. The gateway itself adds **zero bytes** to any prompt; all gateway metadata (packet identity, policy profile, decision, vendor profile) travels in the audit trail and the status projection, never in the request payload. A regression test asserts the prefix is byte-identical across warmup, chunk, and synthesis for a governed document.

### How is "100% of external calls pass through the gateway" enforceable?

- **Context**: Requirement 7.1 is a total claim. A runtime-only check is bypassable and a documentation-only rule is not a control.
- **Sources Consulted**: `tests/test_dependency_directions.py:20-37` (`FORBIDDEN_PAIRS` structure, AST-based, absolute imports only), the three lazy import sites of `api_client` (`pdf_processor.py:522`, `:1047`, `:1157`).
- **Findings**: Every import of `agents.openai.api_client` in `src/` is a function-local lazy import inside `pdf_processor.py`. There are exactly three, and no other importer exists anywhere in `src/`.
- **Implications**: The rule is enforceable statically. A new AST test asserts that `agents.openai.api_client` is imported by exactly one module in the repository — the pipeline-side wiring module — and `pdf_processor.py` is refactored to call the gateway instead. This converts a policy claim into a mechanical test.

### Which dependency direction keeps `src/privacy/` legal?

- **Context**: The roadmap requires new packages to declare their direction before any cross-package import lands; the AST test must be extended, not bypassed.
- **Findings**: `privacy` must reach two things it cannot import — the provenance carrier (which will exist) and the model client (which it must not import). The carrier is fine: `provenance` is a leaf peer of `text_processing` and `privacy → provenance` introduces no cycle. The model client is not fine.
- **Implications**: The gateway holds an injected **transport** satisfying a structural protocol, and `pipeline` supplies the real one. `privacy` imports only `utils`, `provenance`, and the standard library. Nine new forbidden pairs are added: `privacy` must not import `agents`, `pipeline`, `pdf_extractor`, `quality_control`, or `text_processing`; and `agents`, `pdf_extractor`, `quality_control`, `text_processing`, and `provenance` must not import `privacy`.

### What does the secret path have to become?

- **Context**: Requirement 2 demands one managed access path, no leakage into observable artifacts, safe failure, key-version identity, and a dev/production distinction. Requirement 9.4 forbids any fallback that would expose a credential.
- **Sources Consulted**: `src/utils/config_utils.py:332` (`os.environ.get("OPENAI_API_KEY", "") or openai_cfg.get("api_key", "")` — no validation, empty string permitted), `api_client.py:17,23,40-49`.
- **Findings**: The current path resolves once at import, silently accepts an empty key, and constructs the client immediately. It also logs client initialization at DEBUG.
- **Implications**: `config_utils` remains the resolver of *where* a secret comes from, but stops being the thing that hands a bare string to a module constant. `privacy.secrets` wraps resolution in a handle whose `repr`, `str`, and serialization are all redacted, and `api_client` moves from an import-time client to a lazily constructed one accepting an injected client. An empty or absent key becomes a named operational error rather than a silently degraded client.

### Is a policy engine worth building?

- **Context**: Design synthesis, build-vs-adopt lens.
- **Findings**: General-purpose policy engines (OPA/Rego, Cedar) solve a much larger problem — multi-tenant authorization over arbitrary resource graphs — and each introduces either a new runtime or a new third-party dependency. The requirement here is narrow and closed: map a label from a six-member vocabulary onto one of four decisions, optionally naming one transformation and a set of approved vendor profiles.
- **Implications**: Build, do not adopt. The policy profile is declarative YAML validated against a small closed schema, evaluated by a pure function. This adds no third-party dependency, keeps evaluation deterministic and reproducible (5.7), and keeps the whole decision auditable from the recorded inputs. The rejection is recorded here rather than relitigated later.

### Do the transformation and detector interfaces belong in this spec?

- **Context**: Simplification lens — an interface with no implementation is usually speculative abstraction.
- **Findings**: They are not speculative: `privacy-transformations` is a named downstream spec whose entire job is implementing them, and the requirements already fix the fail-closed behavior for the "no provider registered" case (5.5, 6.5, 9.3) and the "no detector registered" case (3.7). The interfaces are load-bearing *now* because the blocking behavior is testable now, without any provider existing.
- **Implications**: Both are defined as structural protocols with a registry loaded from fully-qualified class paths, mirroring the existing `TextProcessor`/`SentenceSegment` and concern-strategy patterns. Neither ships an implementation. Tests exercise the empty-registry blocking path, which is the behavior that actually matters until `privacy-transformations` lands.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Leaf domain package with injected transport | `privacy` owns models, policy, packets, gateway; `pipeline` injects the model transport and wires everything | Keeps dependency direction legal; unit-testable with a fake transport; matches `provenance-core`'s adapter approach | Requires refactoring three lazy import sites in `pdf_processor.py` | **Selected** |
| Decorator around `api_client` inside `agents` | Privacy logic lives beside the client it guards | Smallest diff; no new package | `agents` would import `privacy`, and `privacy` would need config/policy/audit — a cycle and a boundary violation. Also contradicts the brief's "first-class subsystem" | Rejected |
| Proxy process / local sidecar intercepting HTTP | Egress control at the network layer | Genuinely unbypassable | Adds a runtime service, contradicts Requirement 1.5 (no network service), unusable for classification or packet construction | Rejected |
| Middleware chain inside the gateway | Ordered pluggable stages for classify, policy, transform, scan | Extensible | Over-general for four fixed stages; obscures which stage produced a block, which 8.4 requires | Rejected |

## Design Decisions

### Decision: Govern once per document, transmit many times

- **Context**: Requirement 7.7 forbids the gateway from altering the byte-stable prompt material, while Requirements 5 and 6 require transformation before disclosure. These pull in opposite directions if governance runs per call.
- **Alternatives Considered**:
  1. Govern per API call inside the gateway — transformation output would have to be byte-identical across three call shapes, which is unverifiable in general and destroys the cache on any nondeterminism.
  2. Govern once per document at packet construction — the packet payload becomes the single string reused by every call.
- **Selected Approach**: Option 2. `PacketBuilder` produces one `EvidencePacket` per document before any call is issued. The gateway performs approval, recording, and blocking, but never rewrites the payload.
- **Rationale**: It satisfies both constraints without compromise, and it makes the cache-stability property mechanically testable with one assertion.
- **Trade-offs**: Per-chunk differential redaction is not possible in this spec. No requirement asks for it, and `privacy-transformations` can add it later by producing a packet per chunk at the cost of one cache prefix per chunk — a deliberate, documented trade rather than an accident.
- **Follow-up**: The cache-stability regression test must cover the governed path, not only the ungoverned one.

### Decision: The gate is the only decision authority; every other component consumes decisions

- **Context**: Requirement 1.4 and the roadmap's "privacy decides, provenance consumes" resolution.
- **Selected Approach**: `DisclosureGate.evaluate()` is the sole producer of a `DisclosureDecision`. The packet builder, the gateway, the carrier populator, and the status projection all take a decision as input and none of them re-derive one.
- **Rationale**: A second decision site is how fail-open behavior gets reintroduced by accident. Concentrating the decision makes 9.6 ("no code path whose effect is to permit disclosure when a decision could not be reached") auditable by reading one module.
- **Trade-offs**: The gate must be threaded explicitly through the call chain. The project already forbids globals, so this is the existing convention, not a new cost.

### Decision: Audit is write-through and precedes transmission

- **Context**: Requirement 8.7 makes a failed audit write fail the governed operation.
- **Selected Approach**: The gateway appends and flushes the disclosure record **before** invoking the transport. A write failure raises and the call is blocked.
- **Rationale**: An audit trail written after the fact cannot prove what was sent when the process dies mid-call. Write-then-send is the only ordering that makes the trail trustworthy.
- **Trade-offs**: One flush per external call. Negligible against network latency.

### Decision: Secrets are handles, not strings

- **Context**: Requirements 2.2 and 9.4.
- **Selected Approach**: `SecretHandle` exposes the value only through an explicit accessor; `__repr__`, `__str__`, and any serialization return a redacted form. The handle carries a non-secret `key_version` used in audit records.
- **Rationale**: The leak paths in this repo are DEBUG logs and f-string interpolation. Making the redacted form the *default* rendering closes both without requiring a log filter that someone must remember to install.
- **Trade-offs**: Call sites must be explicit about revealing the value, which is the point.

### Decision: A restricted export is a projection, not a second store

- **Context**: Requirement 8.5.
- **Selected Approach**: The restricted export is a pure function over the audit records, dropping the fields declared as protected. It is regenerated on demand and never maintained in parallel.
- **Rationale**: Mirrors `provenance-core`'s treatment of the xtrace decision ledger as a projection. Two stores drift; a projection cannot.

## Risks & Mitigations

- **Refactoring the three `api_client` call sites destabilizes extraction** — mitigate by keeping `extract_chunk` and `warm_pdf_cache` signatures unchanged and adding only an optional injected client; the gateway wraps, it does not rewrite.
- **Prompt-cache regression goes unnoticed** — mitigate with a dedicated regression test asserting byte-identical prefix output across warmup, chunk, and synthesis on the governed path, plus an assertion that the gateway contributes no bytes to the request payload.
- **Fail-closed rules erode over time as operators hit blocks** — mitigate with a test asserting no configuration key, environment variable, or code path permits disclosure on an undecided state (9.6), and by making every block carry a named reason category so the fix is a policy edit rather than a bypass.
- **Documentation drifts into compliance claims** — mitigate with a repository text scan asserting that no privacy artifact, config value, or shipped document asserts compliance, certification, or approval (11.1, 11.6).
- **A test fixture accidentally carries real patient data** — mitigate by building every fixture from synthetic identifiers and lorem text, and by asserting fixtures contain no value matching the declared heightened-sensitivity patterns.
- **`provenance-core` is unimplemented when this spec is built** — mitigate by isolating the only import of `provenance` into a single module, so privacy is buildable and testable against the pinned carrier contract with that module's tests skipped until the upstream package exists.

## References

- `.kiro/specs/provenance-core/design.md` — `PrivacyCarrier`, `attach_privacy`, `check_carrier_conformance`, and the `node_id = f"{source_id}#{local_id}"` evidence identity consumed here.
- `.kiro/specs/provenance-core/requirements.md` — Requirement 9, the carrier contract this spec populates.
- `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` — source Requirements 1–5, 9, 11, 14–16.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — source Requirement 26, of which 26.3 (local-only parsing mode) is absorbed here and 26.6 (multi-user access control) is explicitly deferred.
- `.kiro/steering/config.md` — the env > yaml > default rule and the `_ALL_KNOWN_TOP_LEVEL_KEYS` registration requirement.
- `.kiro/steering/testing.md` — test layout, naming, slow marking, and mocking conventions applied by the testing strategy.
