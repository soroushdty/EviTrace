# Brief: privacy-core

## Problem
Researchers running clinical or institutionally restricted documents through EviTrace have no way to
govern what leaves the machine. Every extraction chunk is shipped verbatim to OpenAI with no
classification, no policy check, and no record of what was disclosed. There is nothing to show an IRB,
a data steward, or a collaborator, and nothing that stops a PHI-bearing page from being transmitted.

## Current State
Privacy is 100% greenfield. Grep across `src/` and `configs/` for `redact`, `deidentif`, `phi`,
`vault`, `pseudonym`, or any sensitivity classifier returns nothing; there is no `src/privacy/`
package. Secret handling is a single unmanaged path: `src/utils/config_utils.py:332` reads
`OPENAI_API_KEY` from env (env > yaml > default), and `src/agents/openai/api_client.py:23` binds it
into the module-level constant `OPENAI_API_KEY`, used at line 40 to construct the client at import
time. That module-level binding has no rotation story, no key-version identity, and no dev/prod
distinction, so priv R2.1/R2.4/R2.5 cannot be met without reworking it. All model traffic goes
straight through `api_client.py` with no interposition point.

## Desired Outcome
A `src/privacy/` subsystem that classifies documents on ingest, resolves every secret through one
managed access path, evaluates a disclosure policy before any evidence crosses the local boundary,
emits model-safe evidence packets that retain provenance identifiers, routes 100% of external model
calls through a privacy gateway, records every classification/disclosure/block decision in an audit
trail, fails closed on every uncertainty, and exposes privacy status without exposing content.

## Approach
Build the deterministic, policy-driven spine first and leave the research-heavy transformations to
`privacy-transformations`. Policy profiles are declarative config; the gateway is a thin wrapper that
`api_client.py` is refactored to call, not a rewrite of the OpenAI client. Classification in this spec
is declaration-driven plus a pluggable detector interface — it defines the label vocabulary and the
fail-closed default (priv R1.4, R3.3), not a PHI-detection algorithm.

## Scope
- **In**: priv R1 (first-class subsystem, conservative default, status without content); R2 (managed
  secret access, no secrets in logs/prompts/exports/provenance, safe failure, key-version tracking,
  dev vs prod); R3 (sensitivity label vocabulary incl. heightened-sensitivity categories, uncertain →
  review, labels linkable to evidence nodes, override auditing); R4 (policy gate, block-on-no-policy,
  enforced-transformation dispatch, decision records); R5 (privacy-preserving evidence packets,
  identifier preservation, block-and-report on failure); R9 (external LLM gateway, vendor/model
  profile approval, packet+decision recording, post-response scanning hook); R11 (privacy audit
  trail); R14 (fail-closed); R15 (user-facing privacy status); R16 (anti-overclaiming documentation). Also
  multiagent R26.3 — a local-only parsing mode the user can select before any API call, expressed as
  a gateway policy that blocks all external egress, since this spec owns the LLM gateway.
- **Out**: the transformation algorithms themselves (redaction, pseudonymization, minimization,
  date-shifting, leakage-risk scoring) — `privacy-transformations` implements them behind the
  interfaces this spec defines. Vault, commitments, and public/private provenance views
  (priv R10, R12, R13) belong to `public-private-provenance`. Project-level access controls for a
  multi-user deployment (multiagent R26.6) are explicitly **out** and recorded as deferred here so
  the deferral is not circular: EviTrace is a single-user local tool today, and multi-user
  authentication/authorization is an unmade product decision, not a privacy-mechanism gap. When that
  decision is made, this spec is the intended home.

## Boundary Candidates
- Secret/key resolution and rotation identity — one provider interface, one owner.
- Sensitivity classification and label vocabulary — produces labels, decides nothing.
- Policy evaluation and disclosure decision records — the decision authority.
- Evidence packet construction — consumes a decision, calls out to transformations.
- The LLM gateway — the single egress chokepoint in front of `api_client.py`.
- Audit trail writer and the user-facing status projection.

## Out of Boundary
- Any PHI-detection model, NER, or clinical encoder implementation.
- Leakage-risk estimation (priv R8) and semantic firewall (priv R7).
- Cryptographic commitments, signing, ZK-style attestation scaffolding (priv R13).
- Provenance graph structure, node identity definition, chain validation — owned by
  `provenance-core` and consumed here, never redefined.
- Any legal compliance claim. This spec ships controls and audit surfaces only; HIPAA and 42 CFR
  Part 2 alignment depends on institutional governance, BAAs, IRB approval, and legal review
  (priv R16.1–R16.5). No artifact this spec produces certifies anything.

## Upstream / Downstream
- **Upstream**: `provenance-core` (evidence node identity, consumed by priv R3.4 and R5.3);
  `src/utils/config_utils.py`; `src/agents/openai/api_client.py`.
- **Downstream**: `privacy-transformations`, `public-private-provenance`, `provenance-audit-export`,
  `multiagent-extraction` (all model traffic inherits the gateway), `reviewer-ui` (status display).

## Existing Spec Touchpoints
- **Extends**: `.kiro/specs/archive/original-idea-documents/privacy_requirements.md` R1–R5, R9, R11, R14–R16. Absorbs the
  residual multiagent R26 secrets bullets not already satisfied by `config_utils.py`.
- **Adjacent**: `provenance-core` (do not define provenance node structure here);
  `privacy-transformations` (define interfaces, not algorithms); `xtrace-toolkit` R-GOV-1 ledger.

## Constraints
- **Hard constraint**: the gateway sits in front of `src/agents/openai/api_client.py` and MUST NOT
  perturb `_shared_paper_prefix` (`src/agents/openai/prompts.py`) byte-for-byte across warmup,
  extraction chunks, and synthesis for a given PDF. No policy IDs, packet IDs, decision timestamps,
  or gateway metadata may be injected into the shared prefix; variable material goes after it.
  Prompt-cache hit rate is a regression test, not a nice-to-have.
- **Privacy ↔ provenance recursion**: priv R1.3 (privacy supplies labels/decisions to provenance) and
  prov R7.5 (provenance defers disclosure to privacy) are mutually recursive as written. Roadmap
  resolution is one-directional — **privacy decides, provenance consumes** — and the interface is
  pinned in `provenance-core`'s design. This spec implements the deciding side only.
- New `src/privacy/` must declare its dependency direction before any cross-package import lands;
  `tests/test_dependency_directions.py` is AST-based and must be extended, not bypassed.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `config_utils.py`.
- No PHI, credentials, or real patient data in the repo or any test fixture.
- Python 3.12.x; heavy optional deps lazily imported inside function bodies.
