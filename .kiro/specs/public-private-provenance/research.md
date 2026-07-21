# Research & Design Decisions — public-private-provenance

## Summary

- **Feature**: `public-private-provenance`
- **Discovery Scope**: Complex Integration — greenfield subsystem sitting above two completed upstream specs (`provenance-core`, `privacy-core`) and one adjacent completed spec (`provenance-audit-export`), with a security-sensitive surface and a parked idea document to fold in.
- **Key Findings**:
  1. The privacy-to-level mapping this spec needs **already exists upstream**. `provenance-audit-export` ships `derive_sharing_marking(graph, decision_levels=...)` reading only `PrivacyCarrier.state/.decision/.supplied_by/.supplied_at`, never `.label`, defaulting to `restricted`. Building a second decision-to-level map here would fork the fail-closed behavior. This spec therefore consumes `provenance.sharing.decision_levels` and contributes only a *level-to-disposition* table.
  2. `src/disclosure/` must be a **new top-level package**, not a subpackage of either upstream. `privacy` imports `provenance` (one file, `carrier.py`), and `provenance` must not import `privacy`. Any module needing both must sit above both. Placing this work inside `src/provenance/` would force `provenance → privacy` and break the pinned one-directional rule.
  3. The parked merkle-tree document describes a **linear hash chain**, not a Merkle tree, and two of its three advertised properties do not follow from its own construction. Its class sketch, its mandatory-QC-persistence directive, and its MD5 entry are all rejected; its chained-content-hash core is adopted. Details in "Parked idea assessment" below.
  4. Honest capability ceiling: this subsystem can deliver tamper-**evidence** (detect modification), not tamper-**proofing**; and with a symmetric signing scheme it can deliver internal authenticity checking, not third-party-verifiable authorship. Both limits are pushed into the artifacts themselves rather than only into prose.

## Research Log

### Upstream contract inventory — what already exists and must not be rebuilt

- **Context**: The brief warns three times against duplicating upstream mechanisms (fingerprints, ledger, annotation production, sharing marking).
- **Sources Consulted**: `.kiro/specs/provenance-core/design.md`, `.kiro/specs/privacy-core/design.md`, `.kiro/specs/provenance-audit-export/design.md` + `requirements.md`; `src/pipeline/manifest.py`, `src/utils/path_utils.py`, `src/utils/config_utils.py`.
- **Findings**:
  | Needed here | Already owned by | Consumed as |
  |---|---|---|
  | Graph, node identity `source_id#local_id`, typed edges | `provenance-core` | read-only input |
  | Ordered run history | `provenance-core` `project_event_log()` | ordering source for the integrity chain |
  | `PrivacyCarrier{state,label,decision,supplied_by,supplied_at}` | `provenance-core` | consulted, never written |
  | Decision-to-level map, run-level `SharingMarking` | `provenance-audit-export` | input to disposition derivation |
  | Per-artifact file digests (`ArtifactFileHash`) | `provenance-audit-export` reproducibility collector | chained over, not recomputed |
  | Document/config/schema fingerprints | `provenance-core` (adopted from `ManifestIdentity`) | referenced, not recomputed |
  | `DISCLAIMER_TEXT`, `PROHIBITED_CLAIM_TERMS` | `privacy-core` `audit.py` | embedded verbatim |
  | Managed secret path, `SecretHandle`, key-version identity | `privacy-core` `secrets.py` | reached only via an injected signer built by `pipeline` |
- **Implications**: The only genuinely new persistent state this spec introduces is the **vault** (outside `outputs/`) and three run-scoped artifacts (public view, integrity manifest, disclosure transaction). Everything else is projection.

### Dependency direction placement

- **Context**: `src/provenance/` may import only `utils` + stdlib. `src/privacy/` may import `utils`, `provenance`, stdlib — with `provenance` confined to `carrier.py`. Both are enforced by AST tests in `tests/test_dependency_directions.py`.
- **Findings**: A subsystem that consults the carrier *and* embeds the privacy disclaimer *and* consumes export-side records needs `provenance`, `provenance.export`, and one constant from `privacy`. No existing package may take that dependency set.
- **Implications**: New leaf-above-leaves package `src/disclosure/`. Allowed: `utils`, `provenance`, `provenance.export`, `privacy` (constant only), stdlib. Forbidden inbound: nothing outside `pipeline` may import it; `provenance` and `privacy` must not. Twelve new `FORBIDDEN_PAIRS` entries. The "never interpret a label" rule is enforced the same way both upstreams enforce theirs — a source-level AST test asserting no module under `src/disclosure/` references the carrier's `label` field.

### Commitment and safe-reference schemes

- **Context**: prov R12.3 and priv R13.1 require commitment-based references; priv R10.5 requires an exported reference to reveal nothing alone; R3.6 requires determinism within a run.
- **Sources Consulted**: standard commitment-scheme properties (hiding/binding); stdlib `hashlib`, `hmac`, `secrets` module capabilities.
- **Findings**:
  - A salted hash `H(salt || canonical_bytes(content))` is a binding commitment under collision resistance of `H`, and hiding **only while the salt is secret**. With a low-entropy or guessable content space and no salt, a bare hash is trivially invertible by enumeration — which is exactly the failure mode for short clinical values. The salt is therefore load-bearing, not decorative, and must live in the vault.
  - A safe reference is the same construction applied to the *identifier* rather than the content, with a per-run salt: `ref = H(run_salt || node_id)`. Deterministic within a run (R3.6), opaque without the run salt (R3.5), and resolvable by a vault holder (R5.1).
  - Neither construction provides confidentiality for material the recipient already holds, and neither says anything about whether disclosure was lawful.
- **Implications**: One shared canonicalization + algorithm module. Vault holds `run_salt`, per-commitment salts, and openings. `scheme_id = "salted-hash-v1"` is versioned so a future scheme can coexist. Every commitment artifact carries an explicit `proves` / `does_not_prove` pair.

### Signing: what is honestly available

- **Context**: priv R13.3 requires signing or integrity-marking a finalized disclosure transaction; the brief requires keys to come only through privacy-core's managed access path and never appear in artifacts.
- **Findings**: Asymmetric signing would need a key-pair management story, a public-key distribution story, and a third-party verification story — none of which exist anywhere in the repo, and none of which are in this spec's scope. Symmetric HMAC over the canonical transaction bytes is implementable today with stdlib `hmac` and the existing `SecretResolver`, and is a genuine integrity marking.
- **Implications**: Ship `HmacTransactionSigner` behind a `TransactionSigner` protocol. Record `externally_verifiable: false` **in the transaction record itself**, because a party without the key cannot check it. That field is the concrete expression of R8.4 and R10.6, and it is the difference between an honest artifact and an overclaiming one. Asymmetric signing is a later provider against the same protocol; nothing in the artifact shape blocks it.

### Vault storage: what is and is not protected

- **Context**: priv R10.1/R10.4 require private mappings to be inaccessible without authorization; the brief forbids vault storage in the repo, in `outputs/`, or in any fixture.
- **Findings**: This spec ships a filesystem-backed store. Filesystem permissions are the only confidentiality control it actually provides. At-rest encryption would require a key-management design (rotation, escrow, recovery) that belongs with the secrets subsystem, not here. Authorization is likewise not something this spec can supply: EviTrace is a single-user local tool and multi-user access control is explicitly deferred by `privacy-core`.
- **Implications**: Ship a `VaultStore` protocol plus `FileVaultStore`, an injected `AuthorizationCheck` callable, and an append-only access log recording every resolution attempt and its outcome. Refuse a configured vault path that resolves inside the repository root or under `OUTPUT_DIR` — a startup-time, testable, fail-closed rule (R5.6). State in the artifact and the module docstring that no at-rest confidentiality claim is made (R5.8). An operator wanting encryption supplies their own `VaultStore`.

## Parked idea assessment — `parked-merkle-tree-idea.md`

The file was read for the first time during this spec's discovery, as the roadmap intended. It is assessed clause by clause rather than adopted or discarded wholesale.

### Mature enough to specify (adopted)

| Idea in the parked file | Where it lands | Note |
|---|---|---|
| Chain each stage's hash to its predecessor: `node_hash = H(content_hash + parent_hash)` | Requirement 6.2, `IntegrityLedger` | This is the load-bearing idea and it works. Modifying any entry invalidates every later entry. |
| Detect post-hoc mutation of an intermediate result | Requirement 6.3, `verify_ledger()` | Delivered, including the first-divergence position, which the parked file did not ask for but which makes a failure actionable. |
| Serialization must exclude volatile fields (its `_serialize` override hook) | `canonical.py` declared exclusion list | Kept as data, not as a subclass hook. |
| Pluggable hash algorithm (its `_hash` override hook) | `ALGORITHMS` registry, `integrity.algorithm` config | Kept as a named registry rather than inheritance, so an artifact can name its algorithm. |
| A `root_hash` summarizing a run | `IntegrityLedger.root_hash` (chain head) | Named honestly as a chain head, not a Merkle root. |

### Not mature enough to specify (recorded, deliberately not built)

1. **It is a hash chain, not a Merkle tree.** The file's own diagram is a linear parent-chain over five pipeline stages. Calling it a Merkle tree is a misnomer, and the misnomer matters because it makes a property look available that is not.
2. **Its "partial audit" claim does not follow.** Verifying one stage against its inputs without re-deriving the rest requires sibling paths from a real Merkle tree. In a linear chain, checking any entry against the root requires every entry after it. Inclusion proofs are therefore **out of scope**, and the artifact must not imply otherwise. The chain is versioned (`scheme_id = "hash-chain-v1"`) so a `merkle-v1` scheme can be added later against the same artifact shape (Requirement 9).
3. **Its "reproducibility proof" claim is overstated.** Equal roots across runs prove only that the canonicalized inputs were equal. Model calls are probabilistic and timestamps are volatile, so a differing root is expected far more often than it is diagnostic. Root comparison is specified as a *reproducibility indicator* reported by the comparator, never as a proof, and never as a run-failure condition.
4. **MD5 is rejected outright.** The parked file lists MD5 among built-in algorithms. It is not collision-resistant and offering it for tamper-evidence would be a defect, so Requirement 6.7 forbids it. `blake3` is also dropped: it is a third-party dependency and this spec adds none. Shipped set: `sha256` (default), `sha3_256`, `blake2b` — all stdlib.
5. **`Provenance` living inside `QCBundle`, populated by `run_pipeline` after each stage, is rejected.** `QCBundle` belongs to `src/quality_control/`, which this spec must not modify and which must not import `provenance` or `disclosure`. The equivalent capability already exists upstream: `provenance-core` records derivation steps per stage and projects an ordered event log. The chain is built over that projection instead — which also satisfies "do not maintain a second ordering" (Requirement 6.4).
6. **"Remove the control for not saving QC layer outputs; make it hard-coded mandatory" is rejected.** It is a change to the quality-control configuration surface, out of this spec's boundary, and it contradicts the explicit-config-passing rule. Recorded here so the decision is visible rather than silently dropped; it belongs to a quality-control spec if anyone still wants it.
7. **"The merkle tree feature is also mandatory" is rejected as written.** This subsystem ships `disclosure.enabled: false` by default, because it requires an operator-supplied vault path and produces artifacts nobody has asked to publish yet. Forcing it on would either fail closed on every run or force a default vault location, and the latter is forbidden by the brief.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|--------|-------------|-----------|---------------------|---------|
| Subpackage under `src/provenance/` | Mirror `provenance/export/` | Reuses an accepted layout | Forces `provenance → privacy`; breaks the pinned one-directional rule | Rejected |
| Subpackage under `src/privacy/` | Treat views as a privacy output | Carrier and disclaimer are local | Makes privacy a provenance-graph consumer; contradicts "privacy decides, provenance consumes" | Rejected |
| Filter inside `provenance-audit-export` | Redact artifacts as they are written | No new package | Export owns contents and must not alter them on the basis of a marking (its own R4.6); would fork the sharing marking | Rejected |
| **New leaf-above-leaves package `src/disclosure/`** | Consults both upstreams, imports neither into the other | Keeps one-directionality intact; label-inertness testable; parallel-safe module seams | One more package and twelve more dependency-direction pairs | **Selected** |

## Design Decisions

### Decision: consume the upstream decision-to-level map; add only level-to-disposition

- **Context**: R2.2/R2.3. `provenance-audit-export` already maps a privacy decision string to a `SharingLevel` from configuration, fail-closed to `restricted`.
- **Alternatives**: (1) define an independent decision-to-disposition map here; (2) reuse the upstream map and add a level-to-disposition table.
- **Selected**: (2). `provenance.sharing.decision_levels` stays the single mapping; this spec adds `disclosure.public_view.level_dispositions`.
- **Rationale**: One fail-closed default, one place to audit it. Two maps would drift and could disagree, producing an artifact marked `restricted` at run level whose nodes were nonetheless included.
- **Trade-offs**: A configuration dependency on another spec's block. Accepted — it is a read, and the alternative is a correctness hazard.

### Decision: omission is always a marker node, never a deletion

- **Context**: prov R7.4, prov R12.3, priv R12.4, R1.4, R3.4.
- **Selected**: Every excluded element is replaced by an `OmissionMarker` carrying the node kind, the disposition basis, and a restricted-evidence-exists flag — never by absence.
- **Rationale**: Silent absence is indistinguishable from "no evidence ever existed", which is precisely the failure `provenance-core` Requirement 2 exists to prevent. It also satisfies priv R12.4 with no extra mechanism.
- **Trade-offs**: The public view leaks the *shape* of the private graph — how many restricted nodes exist and where. That is an intentional, documented trade: structural counts in exchange for a trace that cannot silently understate itself. Recorded as a residual risk.

### Decision: disagreement conditions are enumerated, not inferred

- **Context**: prov R12.5 / R2.7 requires "privacy and provenance disagree ⇒ requires review", but "disagree" is undefined in the source document.
- **Selected**: Three concrete, testable conditions: (a) the carrier state is `rejected`; (b) a decision is present while the carrier state is not `labeled`; (c) a node's resolved level is strictly more permissive than the resolved level of the source node it belongs to.
- **Rationale**: Each is decidable from graph content alone, needs no sensitivity judgement, and maps to a real failure. (c) is the one that matters in practice — a child evidence node cannot be more open than its document.
- **Trade-offs**: Not exhaustive. New conditions may be added; each becomes a Revalidation Trigger.

### Decision: `disclosure.enabled: false` by default

- **Context**: R1.7, R5.7.
- **Selected**: Off unless configured, with a run-level record stating no disclosure view was generated.
- **Rationale**: The subsystem cannot function without an operator-supplied vault path, and defaulting one would violate the brief. A disabled run reporting "not generated" is honest; a disabled run silently reporting a shareable artifact would not be.

## Risks & Mitigations

- **Structural leakage from omission markers** — the count and position of restricted nodes is disclosed. Mitigation: document it in the artifact's proof-semantics block under `does_not_prove` / limitations, and expose reduced counts (R1.5) so an operator can see the exposure before publishing.
- **Salt loss makes commitments unopenable** — vault loss is unrecoverable by construction. Mitigation: opening material is written to the vault *before* the commitment is emitted; a vault write failure blocks the commitment (R3.7, R11.3). Backup remains an operator responsibility, stated explicitly.
- **HMAC mistaken for a third-party-verifiable signature** — the single most likely overclaim. Mitigation: `externally_verifiable: false` as a required field, an explicit `does_not_prove` entry, and a test asserting no shipped string calls the marking a signature that a third party can check.
- **A future contributor branching on the sensitivity label** — would silently reintroduce sensitivity judgement into provenance. Mitigation: AST test over all of `src/disclosure/`, mirroring the identical guards in `provenance-core` and `provenance-audit-export`.
- **Chain head drifting from a "Merkle root"** in later prose or code comments. Mitigation: the field is named `chain_head`, `scheme_id` is `hash-chain-v1`, and a repository text check rejects the word "merkle" in shipped strings until a real Merkle scheme lands.
- **Vault path misconfiguration writing protected material into `outputs/`** — the highest-consequence operational error. Mitigation: constructor-time refusal with a named error, covered by test (R5.6).

## References

- `.kiro/specs/provenance-core/design.md` — `PrivacyCarrier`, `ProvenanceGraph`, `project_event_log`, node identity.
- `.kiro/specs/privacy-core/design.md` — `DisclosureDecision`, `SecretResolver`, `DISCLAIMER_TEXT`, `PROHIBITED_CLAIM_TERMS`.
- `.kiro/specs/provenance-audit-export/design.md` — `SharingMarking`, `derive_sharing_marking`, `ArtifactFileHash`, `SLOT_REGISTRY`.
- `.kiro/specs/public-private-provenance/parked-merkle-tree-idea.md` — parked idea, assessed above.
- `.kiro/specs/archive/original-idea-documents/provenance_requirements.md` — prov R7, R10, R12.
- `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` — priv R10, R12, R13.
- `.kiro/steering/roadmap.md` — spec ordering, shared seams, the prov R12.1 split directive.
