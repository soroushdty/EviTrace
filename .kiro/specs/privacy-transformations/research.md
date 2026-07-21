# Research & Design Decisions — privacy-transformations

## Summary

- **Feature**: `privacy-transformations`
- **Discovery Scope**: New Feature (greenfield package) with a Complex Integration seam against the completed `privacy-core` spec
- **Key Findings**:
  - `privacy-core`'s dependency rules make an in-package implementation impossible: `src/privacy/` may import only `utils`, `provenance`, and stdlib, and an AST test forbids `privacy → text_processing`. The provider must therefore live in a **new sibling package**, `src/privacy_transformations/`, reached only through `privacy-core`'s `importlib` class-path registry. That dynamic loading is what keeps the static dependency graph acyclic.
  - `privacy-core` fixes three contract shapes this spec cannot change without a cross-spec revalidation: `TransformationProvider.transform(payload, *, label, evidence_ids) -> TransformationResult`; `load_providers(class_paths)` constructs providers **with no arguments**; and `TransformationResult` carries only `payload`, `complete`, `preserved_evidence_ids`, and `detail: str | None`. Every requirement in this spec had to be expressed through those four fields.
  - The gate already blocks on `complete is False`. Every fail-closed obligation here — undetectable category, meaning-alteration escalation, leakage risk above threshold, `unresolved` risk — is therefore delivered as "return an incomplete result", with **no new blocking mechanism and no new decision authority**.
  - Four of the five inherited requirement areas (priv R6.1, R6.2, R6.4, and the identifier half of R8.2) are the *same* operation with different actions per category. Generalizing to one detect→plan→rewrite engine removes three near-duplicate implementations and makes offset correctness a single testable property.
  - The roadmap's warning is accurate: five inherited criteria were unfalsifiable. Each is given an operational definition below, and the gap between the definition and the concept it stands for is recorded as a shipped limit rather than hidden.

## Research Log

### Upstream contract analysis (`privacy-core`)

- **Context**: This spec is defined entirely by what `privacy-core` will call and what it will accept back. Anything not expressible through that surface must be deferred or delivered out-of-band.
- **Sources Consulted**: `.kiro/specs/privacy-core/design.md` (TransformationRegistry, PacketBuilder, DisclosureGate, Allowed Dependencies, Revalidation Triggers); `.kiro/specs/privacy-core/requirements.md` R5, R6, R9; `tests/test_dependency_directions.py`.
- **Findings**:
  - Dispatch is fail-closed on exactly three conditions: no provider registered, provider raised, provider returned `complete=False`. Two of those are ours to trigger deliberately.
  - `PacketBuilder` blocks when `TransformationResult.preserved_evidence_ids` is not a superset of the identifiers it requires. Identifier preservation is therefore a hard postcondition, not a nicety, and minimization must never delete a unit's identifier.
  - `DisclosureDecision` and `EvidencePacket` have no field for a risk verdict or a meaning-altered flag. Adding one is listed by `privacy-core` as a Revalidation Trigger.
  - Prompt-cache stability depends on the packet payload being produced **once per document**. The transformation therefore runs once per document, and must be deterministic, or the byte-identity property that the whole cache strategy rests on breaks.
- **Implications**: A run-scoped **transformation record artifact** carries the full findings, and `TransformationResult.detail` carries a compact reference plus the two verdicts. Requirement 10.7 records the direct-attachment deferral explicitly rather than silently under-delivering priv R8.4. Determinism is promoted to a first-class requirement (1.7) with a property test, because prompt-cache stability now depends on it.

### Detection substrate: what is actually achievable without a model

- **Context**: The brief rules out clinical NER models, vendor de-identification services, and any machine-learned detector. What remains is patterns, checksums, and operator-supplied gazetteers.
- **Findings**:
  - Structured identifier categories (email, URL, IP, phone, national-identifier-shaped numbers, record-number-shaped tokens, account/licence/device/vehicle identifiers, dates) are reliably detectable by pattern, with precision the main risk rather than recall.
  - Person names, geographic subdivisions, and free-text institutional references are **not** reliably detectable by pattern. Published de-identification work reaches usable recall on these only with trained sequence models, which this spec excludes by scope.
  - Silence about that asymmetry is the primary overclaiming risk in the whole spec: a profile that names `person_name` and quietly detects nothing would report a clean transformation.
- **Implications**: The catalogue carries a per-category **detection-support level** (`pattern`, `gazetteer`, `unsupported`), and requesting a non-passthrough action on an `unsupported` category with no gazetteer is an *incomplete result*, not a no-op (2.4). Baselines record recall `0.0` for unsupported categories and exclude them from any headline figure (11.5).

### Operationalizing the five unfalsifiable criteria

- **Context**: The roadmap requires these to be tightened into testable criteria before design, or explicitly deferred.
- **Findings and chosen operationalizations**:

| Inherited criterion | Operational definition adopted | How it is falsified | Residual gap (shipped as a limit) |
|---|---|---|---|
| priv R6.3 "information not necessary for the task" | Necessity vocabulary = field names, definitions, reviewer questions, and category/example tokens of the **active extraction field map**; a retention unit is necessary if it matches ≥1 vocabulary term **or** carries a numeric/quantity/unit/statistical token; undetermined ⇒ retained | Ground-truth corpus annotates each unit as field-supporting or not; measured recall of necessary units must be exactly 1.0, drop ratio is reported not guaranteed | Lexical necessity over-retains; it is a conservative proxy for semantic relevance, not a measure of it |
| priv R6.5 "changes clinical meaning" | Verdict is `possibly_altered` iff a rewritten span overlaps a numeric/quantity/unit/statistical token, **or** overlaps a necessity-vocabulary term, **or** a retention unit was dropped, **or** a non-interval-preserving temporal mode ran; assessment failure ⇒ `undetermined`, treated as altered | Fixture pairs with known triggers; each trigger has a dedicated positive and negative case | `unchanged` means "no declared trigger fired", never "clinically equivalent" — stated in the record (7.7) |
| priv R7.2 "unnecessary identifying detail" | Three declared computable checks: raw text retained alongside the representation; declared reconstructability factors; direct-identifier spans detectable in any textual component of the serialized representation | Fixture representations constructed to fail each check individually | Says nothing about embedding invertibility; explicitly disclaimed (8.7) |
| priv R7.3 "task-relevant clinical structure" | Three retention proportions: numeric tokens, section labels, evidence identifiers present in the representation relative to source | Fixture with known counts | Structural proxy for clinical utility, not a utility evaluation |
| priv R8.2 risk dimensions | `RuleBasedLeakageRubric` v1: five dimensions, each scored on `{none, low, moderate, high, indeterminate}`; overall level = worst dimension; any `indeterminate` ⇒ `unresolved`; numeric aggregate = sum of dimension scores; gate = level ≤ `max_leakage_risk_level` **and** score ≤ `max_leakage_risk_score` **and** status evaluated | Table-driven per-dimension fixtures; property test that no input combination containing `indeterminate` yields anything but `unresolved` | Ordinal heuristic with no probability semantics; rarity `k` is computed within the fixture corpus only and does not generalize to a population (9.9) |

- **Implications**: Nothing in the design is written against an aspiration. Every one of the five has a fixture, a measure, and a stated residual gap. The residual gaps are shipped in `LIMITS_TEXT` and embedded in every artifact (11.6, 11.7).

### Surrogate key material and the secret-path constraint

- **Context**: `privacy-core` requires every secret to resolve through one managed path, and its design asserts `SecretHandle.reveal()` has exactly **one** call site in the repository (`pipeline/privacy_wiring.py`). A keyed surrogate derivation appears to need a secret.
- **Alternatives considered**:
  1. Resolve a surrogate key through `privacy-core`'s resolver — impossible: the provider is constructed with no arguments and cannot receive a handle, and a second `reveal()` call site would break an upstream invariant test.
  2. Read a surrogate key from configuration or an environment variable inside this package — rejected: it creates a second unmanaged secret path, violating priv R2.1.
  3. Generate ephemeral, run-scoped key material inside the process, never persisted, and hand off only a key-version identifier — selected.
- **Selected Approach**: `EphemeralRunKeyProvider` draws key material from the platform CSPRNG once per run, holds it in memory only, and exposes a derived non-secret `key_version`. Surrogates are HMAC-derived and are therefore stable **within a run** and unlinkable across runs.
- **Trade-offs**: Cross-run surrogate stability — genuinely useful for longitudinal corpora — is lost. That capability requires persisted key material and a resolvable mapping store, which is the vault (priv R10), owned by `public-private-provenance`. Requirement 4.7 forces the subsystem to declare the run-scoped limitation in the mapping record rather than let an operator assume more.

### Package placement and dependency direction

- **Context**: `src/privacy/` cannot host this work, and `text_processing` reuse is required by the brief.
- **Findings**: The forbidden pairs `privacy → text_processing` and `text_processing → privacy` both hold under a sibling package. `privacy_transformations → privacy` is not forbidden by any existing or `privacy-core`-declared pair, and `privacy → privacy_transformations` never appears statically because loading is by `importlib` class path.
- **Implications**: New package `src/privacy_transformations/`, with the `privacy` import confined to a single module (`entrypoints.py`) — mirroring `privacy/carrier.py`'s single-importer discipline — and four new forbidden pairs added to `tests/test_dependency_directions.py`.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Chained transformers | Four independent providers, each rewriting the output of the last | Simple to add a fifth | Offsets recomputed per stage; double-transformation bugs; four detection passes; profile order becomes semantics | Rejected |
| Single detect-plan-rewrite engine with declarative profiles | One detection pass, one action plan, one offset-safe rewrite, then a unit-level minimization pass | Offset correctness proved once; profiles are data; determinism is structural | Engine is the single point of failure; needs strong property tests | **Selected** |
| Visitor over a parsed document model | Build a structured document tree and transform nodes | Cleanest for structured input | The payload arriving from `privacy-core` is a single assembled string; a parser would have to be invented | Rejected — no upstream structure to exploit |
| Self-scoring transformers | Each transformer reports its own residual risk | Fewer components | Defeats the entire point of R8: running a transformation would be the evidence it worked | Rejected on principle; enforced by an AST test |

## Design Decisions

### Decision: One engine, profiles as data

- **Context**: Requirements 3, 4, and 6 all rewrite detected spans; only the action differs.
- **Selected Approach**: A `TransformationProfile` is a declared mapping from identifier category to action (`passthrough`, `redact`, `pseudonymize`, `temporal`), plus a minimization flag and a temporal mode. Named `transformation_id`s referenced by policy profiles (`redact`, `pseudonymize`, `minimize`, `redact_minimize`, …) are entries in a profile file, not classes.
- **Rationale**: Removes three near-duplicate rewrite implementations; makes adding a policy a configuration change; concentrates offset correctness in one function with a property test.
- **Trade-offs**: A profile file is one more artifact to validate. Mitigated by structural validation that mirrors `privacy-core`'s `PolicyFile` pattern — defects make every profile unusable and every transformation incomplete, rather than partially applying.

### Decision: Fail-closed exclusively through `complete=False`

- **Context**: This spec must block on several conditions but has no authority to block.
- **Selected Approach**: Every blocking condition sets `complete=False` and names a declared failure category. The subsystem raises no exception across the `transform()` boundary; internal exceptions are caught at the entrypoint and converted, because an escaping exception and an incomplete result are handled identically by `privacy-core` but only the latter carries a named cause.
- **Rationale**: No second blocking mechanism, no new decision authority, and the existing upstream test for "no third outcome" continues to hold.

### Decision: The leakage evaluator is structurally prevented from self-scoring

- **Context**: R8's value collapses if the thing that transformed the payload also grades it.
- **Selected Approach**: `leakage.py` is placed to the **left** of every transformer in the package's module order, so the left-to-right import rule alone forbids it importing `surrogates.py`, `temporal.py`, `minimization.py`, or `engine.py`. It re-runs detection over the transformed payload rather than consuming the engine's change log. An AST test pins the non-import.
- **Trade-offs**: Detection runs twice per transformation. The cost is negligible against the network call it gates, and the independence is the whole point.

### Decision: Age handling and unparsed dates

- **Context**: Ages above a cap and malformed dates are the two classic leaks in temporal handling.
- **Selected Approach**: Ages above `age_cap` (default 89) collapse to one bucket marker regardless of value. A detected date that does not parse is **suppressed**, never shifted, and recorded as `unparsed_date` — shifting an unparsed token risks emitting the original.
- **Rationale**: Both are cheap, deterministic, and directly testable; both are fail-closed in the direction of less information.

### Decision: Synthetic corpus values drawn from never-issued ranges

- **Context**: The repository must contain no real identifier, but the corpus must contain identifier-shaped strings or detection cannot be measured.
- **Selected Approach**: Every fabricated value comes from a documented reserved or never-issued range: `example.com`/`example.org` domains, `555-01xx` telephone numbers, documentation IP ranges, national-identifier-shaped values using never-issued prefixes, locally minted record numbers with an `X` sentinel prefix, and lorem-derived clinical prose. The corpus manifest declares the generator seed and a `synthetic: true` flag; a test asserts every ground-truth value matches an allowed reserved pattern.
- **Rationale**: Makes "no real data" mechanically checkable rather than a promise.

## Risks & Mitigations

- **Overclaiming by omission** — an operator assumes a category was handled when nothing could detect it. *Mitigation*: unsupported category with a non-passthrough action ⇒ incomplete result (2.4); baselines record recall 0.0 and exclude the category from headline figures (11.5).
- **Fixture-derived metrics read as real-world performance** — the measurements are only as good as fabricated data. *Mitigation*: `LIMITS_TEXT` states it, embedded in every artifact, and asserted by test (11.6, 11.7).
- **Determinism regression breaking prompt-cache stability** — a non-deterministic transformation would silently destroy the byte-identity property `privacy-core` depends on. *Mitigation*: determinism is Requirement 1.7 with a property test; all key material is derived from a per-run value that is fixed before the first transformation.
- **Precision collapse on aggressive patterns** — a broad record-number pattern can shred ordinary numeric text and destroy extraction quality. *Mitigation*: precision floors are in the baseline file and a regression fails the suite; meaning-alteration triggers on any numeric-token overlap, so over-redaction is visible rather than silent.
- **Minimization removing evidence anchors** — would make `PacketBuilder` block every document. *Mitigation*: dropped units keep their identifier behind a suppression marker (5.3), and identifier preservation is a postcondition test on every profile.
- **Rubric mistaken for a risk estimate** — the single most likely misreading of this spec. *Mitigation*: ordinal-only vocabulary with no numeric probability anywhere in the output, plus a mandatory statement at every recording site (9.9), plus a repository text scan that forbids `de-identified`, `anonymized`, and `safe` as asserted artifact values (11.8).
- **Upstream contract drift** — a change to `TransformationResult` or `TransformationProvider` invalidates the entrypoint. *Mitigation*: listed as a Revalidation Trigger on both sides; the entrypoint is the only module importing `privacy`, so the blast radius is one file.

## References

- `.kiro/specs/privacy-core/design.md` — TransformationRegistry, PacketBuilder, DisclosureGate, dependency rules, Revalidation Triggers.
- `.kiro/specs/privacy-core/requirements.md` — R5.3, R5.8, R6.2, R6.5, R9.3 (the obligations this spec discharges).
- `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` — priv R6, R7, R8 (source requirements).
- `.kiro/steering/roadmap.md` — spec ordering, cross-cutting NFRs, the "highest research risk / tighten acceptance criteria" instruction, and the no-real-data constraint.
- `configs/extraction_map.json` — the 62-field map that supplies the necessity vocabulary.
- `src/text_processing/` — normalizers and tokenizers reused rather than forked.
