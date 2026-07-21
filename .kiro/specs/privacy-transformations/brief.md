# Brief: privacy-transformations

## Problem
`privacy-core` can decide that evidence may leave only in redacted, pseudonymized, or minimized form —
but nothing can actually produce that form. Without the transformations, every policy that is not
"allow raw" collapses into "block", and EviTrace is unusable for exactly the clinical corpora the
privacy work exists to serve. Worse, without leakage-risk evaluation, a transformed artifact would be
treated as safe merely because a transformation ran.

## Current State
Fully greenfield, downstream of an also-greenfield dependency. No redaction, de-identification,
pseudonymization, date-shifting, or leakage-risk code exists anywhere in `src/` or `configs/`.
`src/text_processing/` supplies normalizers, tokenizers, matchers, and an embedding path that are the
natural substrate for span-level transformation and for the encoder outputs priv R7.1 declares
potentially sensitive — but none of it is privacy-aware and it must not import `quality_control`.
The transformation dispatch point and the "meaning-altered" flag surface will exist only once
`privacy-core` ships them.

## Desired Outcome
Policy-selected transformations that reliably produce a disclosable artifact from a restricted one,
each carrying a machine-readable record of what was changed, whether clinical meaning may have been
altered, and a leakage-risk verdict that gates external use rather than rubber-stamping it.

## Approach
Pluggable transformer strategies behind the interfaces `privacy-core` defines, each independently
testable on synthetic fixtures, plus a separate leakage-risk evaluator whose verdict — including
`unresolved` — is attached to the disclosure decision. Deterministic transforms (structural
suppression, surrogate mapping, temporal abstraction) ship first; detection-dependent and
risk-scoring components are staged behind them so the deterministic layer is not blocked.

## Scope
- **In**: priv R6.1 redaction (remove/replace restricted identifiers); R6.2 pseudonymization with
  controlled surrogate identifiers; R6.3 minimization (drop task-unnecessary content); R6.4
  policy-controlled temporal abstraction — relative dates, date shifting, suppression; R6.5 the
  meaning-altered marking; R7 semantic firewall — encoder outputs treated as sensitive by default
  (R7.1), representations evaluated for unnecessary identifying detail (R7.2), task-relevant clinical
  structure preserved (R7.3), unevaluated representations never treated as safe (R7.4), private
  provenance anchors kept local (R7.5); R8 leakage-risk evaluation across direct identifiers,
  quasi-identifiers, rare clinical patterns, reconstructability, and linkage risk (R8.2), threshold
  gating (R8.3), attachment to packet/decision (R8.4), and `unresolved` rather than `safe` when risk
  cannot be estimated (R8.5).
- **Out**: the policy gate, gateway, audit trail, packet envelope, and secret handling
  (`privacy-core`). Surrogate-to-real mapping *storage* — that is the vault, priv R10, owned by
  `public-private-provenance`; this spec produces mappings and hands them off. Any clinical NER model
  selection or vendor de-identification service. Any legal or expert-determination claim.

## Boundary Candidates
- Transformation strategy interface and registry (redact / pseudonymize / minimize / temporal).
- Surrogate identifier generation and stability contract, separate from mapping persistence.
- Temporal abstraction as its own strategy — date shifting has corpus-wide consistency semantics
  that do not resemble span redaction.
- Meaning-alteration detection and marking.
- Leakage-risk evaluator, deliberately separate from every transformer so it cannot be self-scored.

## Out of Boundary
- Vault storage, access logging, and authorization (priv R10).
- Cryptographic commitments (priv R13).
- The disclosure decision itself — this spec supplies inputs and verdicts; `privacy-core` decides.
- Legal compliance. This work provides controls and audit surfaces; it never constitutes a
  compliance claim, and no risk score here is an expert determination.

## Upstream / Downstream
- **Upstream**: `privacy-core` (policy profiles, transformation dispatch, packet envelope, audit
  records, fail-closed contract per priv R14.3); `provenance-core` (evidence node identity that must
  survive transformation, priv R5.3); `src/text_processing/`.
- **Downstream**: `public-private-provenance` (consumes surrogate mappings and safe references);
  `multiagent-extraction` and any external model traffic that needs non-raw disclosure;
  `reviewer-ui` (surfacing meaning-altered and risk-unresolved states).

## Existing Spec Touchpoints
- **Extends**: `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` R6, R7, R8.
- **Adjacent**: `privacy-core` (owns policy and gateway — do not re-decide disclosure here);
  `public-private-provenance` (owns the vault); `src/text_processing/` (reuse, do not fork).

## Constraints
- **This is the highest-research-risk spec in the roadmap and its acceptance criteria are the
  vaguest of any privacy requirement.** priv R6.3 "information not necessary for the task", R6.5 "if
  a transformation changes clinical meaning", R7.2 "unnecessary identifying detail", R7.3
  "task-relevant clinical structure", and all of R8.2's risk dimensions are currently unfalsifiable
  as written — there is no threshold, no measurable, and no defined evaluation corpus. **Before
  design starts these must be tightened into testable criteria**: a concrete necessity/minimization
  definition tied to the 62-field extraction map, an operational definition and detection method for
  meaning alteration, a named leakage-risk scoring method with declared inputs and a numeric policy
  threshold, and a synthetic evaluation corpus with ground truth. If any of these cannot be pinned,
  the corresponding requirement must be explicitly deferred rather than designed against.
- Depends entirely on `privacy-core`; do not begin design before its interfaces are approved.
- No PHI or real patient data in the repo or fixtures — all evaluation corpora are synthetic, which
  itself bounds how strongly any risk claim can be validated. Say so in the spec.
- Fail-closed: transformation failure must prevent untransformed egress (priv R14.3); risk that
  cannot be estimated is `unresolved`, never `safe` (priv R8.5).
- `text_processing` must not import `quality_control`; heavy deps (encoders, torch) stay lazily
  imported inside function bodies. Python 3.12.x.
