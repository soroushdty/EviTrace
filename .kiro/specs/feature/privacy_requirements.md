# Requirements Document: Privacy Module

## Introduction

The privacy module establishes EviTrace as a privacy-aware biomedical AI workflow framework. Its purpose is to control how sensitive, PHI-bearing, institutionally restricted, or legally protected documents move through evidence extraction, model interaction, provenance generation, and export.

This module should not be framed as a simple secrets manager or redaction utility. It should provide the foundation for policy-gated evidence disclosure, private/public provenance separation, privacy-preserving semantic evidence packets, audit records, and future cryptographic attestations.

The module should help EviTrace support workflows involving internal clinical research documents, protected health information, institutional policies, HIPAA-aligned controls, and 42 CFR Part 2-sensitive records, without claiming automatic legal compliance.

## Scope

This requirements document focuses on workflow design, architectural scaffolding, privacy boundaries, and system behavior. It does not prescribe final implementations of PHI detection, legal review, cryptographic proof systems, specific clinical encoders, cloud vendors, or compliance certification processes.

The privacy module should define the conceptual contract for:

- Secret and key management
- PHI and sensitive-data classification
- Policy-gated disclosure
- Redaction and pseudonymization workflows
- Semantic evidence packet generation
- External model gateway controls
- Private identity and evidence vaults
- Privacy audit records
- Public/private provenance coordination
- Future cryptographic commitments and zero-knowledge-style attestations

## Non-Compliance Disclaimer

This module may support HIPAA-aligned and 42 CFR Part 2-aware workflows, but EviTrace SHALL NOT claim automatic compliance. Compliance depends on institutional governance, deployment environment, BAAs, IRB approvals, access controls, security safeguards, expert determination when applicable, and legal review.

## Requirements

### Requirement 1: First-Class Privacy Subsystem

**User Story:**  
As a clinical AI infrastructure designer, I want privacy to exist as a first-class subsystem so that sensitive evidence is governed before it reaches models, logs, exports, or provenance artifacts.

#### Acceptance Criteria

1. WHEN a document enters EviTrace, THEN the privacy module SHALL be able to classify whether privacy controls are required.
2. WHEN evidence is prepared for model use, THEN the privacy module SHALL determine whether raw, redacted, pseudonymized, minimized, or blocked disclosure is appropriate.
3. WHEN provenance artifacts are generated, THEN the privacy module SHALL provide privacy labels and disclosure decisions to the provenance module.
4. IF privacy status is unknown, THEN the system SHALL default to a conservative restricted state.
5. WHEN users inspect workflow outputs, THEN the system SHALL expose privacy status without exposing restricted content.

---

### Requirement 2: Secret and Key Management Foundation

**User Story:**  
As a system operator, I want keys, secrets, tokens, and credentials to be managed through a dedicated privacy-layer interface so that sensitive credentials are not scattered across the codebase.

#### Acceptance Criteria

1. WHEN EviTrace requires an API key, token, signing key, encryption key, or secret, THEN the privacy module SHALL provide a managed access path.
2. WHEN secrets are requested, THEN the system SHALL avoid exposing secret values in logs, provenance artifacts, model prompts, or exported files.
3. WHEN a secret is missing, expired, or unavailable, THEN the system SHALL fail safely with a clear operational error.
4. WHEN key rotation is required, THEN the system SHALL support identifying which key version was used for a privacy-relevant event.
5. IF a development fallback is allowed, THEN the system SHALL clearly distinguish development-mode secret handling from production-mode secret handling.

---

### Requirement 3: Sensitive Data Classification

**User Story:**  
As a researcher working with clinical documents, I want the privacy module to classify sensitive content so that evidence can be governed according to its risk.

#### Acceptance Criteria

1. WHEN a document is ingested, THEN the privacy module SHALL support classification of PHI-bearing, restricted, internal-only, research-only, and public-safe content.
2. WHEN substance-use-disorder-related records or similar heightened-sensitivity categories are detected or declared, THEN the module SHALL support stricter policy labels.
3. WHEN classification is uncertain, THEN the system SHALL mark the content as requiring review.
4. WHEN classification metadata is created, THEN it SHALL be linkable to source documents, evidence nodes, and downstream artifacts.
5. IF a user manually overrides classification, THEN the override SHALL be recorded in the privacy audit trail.

---

### Requirement 4: Policy-Gated Disclosure

**User Story:**  
As an institutional user, I want every movement of sensitive evidence across boundaries to be policy-gated so that restricted content is not disclosed accidentally.

#### Acceptance Criteria

1. WHEN evidence is about to leave the local/private boundary, THEN the privacy module SHALL evaluate the applicable disclosure policy.
2. WHEN a disclosure policy blocks external use, THEN the system SHALL prevent the evidence from being sent to an external model or export target.
3. WHEN a disclosure policy allows limited use, THEN the system SHALL enforce the required transformation before disclosure.
4. IF no applicable policy exists, THEN the system SHALL block disclosure or require explicit review.
5. WHEN a disclosure decision is made, THEN the system SHALL record the policy profile, decision, rationale category, and affected artifacts.

---

### Requirement 5: Privacy-Preserving Evidence Packets

**User Story:**  
As a biomedical AI researcher, I want sensitive documents to be converted into model-safe evidence packets so that external reasoning can occur without unnecessary exposure of raw PHI.

#### Acceptance Criteria

1. WHEN a document contains sensitive information, THEN the privacy module SHALL support creation of a privacy-preserving evidence packet.
2. WHEN an evidence packet is created, THEN it SHALL include only the content permitted by the selected policy profile.
3. WHEN evidence is transformed, THEN the packet SHALL preserve evidence identifiers needed for provenance linking.
4. IF an evidence packet cannot be generated safely, THEN the system SHALL block external use and report the reason.
5. WHEN an evidence packet is sent to a model, THEN the system SHALL record the packet identity and disclosure decision.

---

### Requirement 6: Redaction, Pseudonymization, and Minimization

**User Story:**  
As a clinical document user, I want EviTrace to support multiple privacy transformations so that different workflows can apply different levels of protection.

#### Acceptance Criteria

1. WHEN a policy requires redaction, THEN the privacy module SHALL remove or replace restricted identifiers before external disclosure.
2. WHEN a policy requires pseudonymization, THEN the module SHALL replace direct identifiers with controlled surrogate identifiers.
3. WHEN a policy requires minimization, THEN the module SHALL remove information not necessary for the task.
4. WHEN exact dates are restricted, THEN the module SHALL support policy-controlled temporal abstraction such as relative dates, date shifting, or date suppression.
5. IF a transformation changes clinical meaning, THEN the system SHALL mark the transformed evidence as potentially meaning-altered.

---

### Requirement 7: Semantic Privacy Firewall

**User Story:**  
As a researcher exploring privacy-preserving model workflows, I want local encoders and semantic transformations to produce controlled representations without assuming embeddings are automatically de-identified.

#### Acceptance Criteria

1. WHEN clinical encoders are used on PHI-bearing content, THEN the system SHALL treat their outputs as potentially sensitive unless privacy evaluation determines otherwise.
2. WHEN semantic representations are created, THEN the privacy module SHALL evaluate whether they preserve unnecessary identifying detail.
3. WHEN semantic evidence packets are produced, THEN they SHALL preserve task-relevant clinical structure while minimizing identity-bearing detail.
4. IF a representation has not passed leakage-risk checks, THEN it SHALL NOT be treated as safe for unrestricted external use.
5. WHEN semantic transformations are used, THEN the system SHALL preserve private provenance links locally rather than exposing raw private anchors externally.

---

### Requirement 8: Leakage-Risk Evaluation

**User Story:**  
As a privacy reviewer, I want transformed evidence to be assessed for leakage risk so that embeddings, summaries, and structured packets are not assumed safe by default.

#### Acceptance Criteria

1. WHEN a transformed artifact is proposed for external use, THEN the privacy module SHALL support leakage-risk assessment.
2. WHEN leakage-risk assessment is performed, THEN it SHALL consider direct identifiers, quasi-identifiers, rare clinical patterns, reconstructability, and linkage risk.
3. IF leakage risk exceeds the selected policy threshold, THEN the system SHALL block disclosure or require review.
4. WHEN leakage-risk results are produced, THEN they SHALL be attached to the relevant evidence packet or disclosure decision.
5. WHEN risk cannot be estimated, THEN the system SHALL mark the artifact as unresolved rather than safe.

---

### Requirement 9: External LLM Gateway

**User Story:**  
As a system operator, I want external model calls to pass through a privacy-aware gateway so that sensitive content is never sent directly to off-site services without policy review.

#### Acceptance Criteria

1. WHEN EviTrace prepares an external LLM call, THEN the call SHALL pass through the privacy gateway.
2. WHEN the gateway receives raw PHI-bearing evidence, THEN it SHALL block direct external transmission unless an authorized policy explicitly permits it.
3. WHEN the gateway transmits a model-safe evidence packet, THEN it SHALL record the packet ID, model/vendor profile, policy profile, and disclosure decision.
4. IF a model/vendor profile is not approved for the evidence sensitivity level, THEN the gateway SHALL block the request.
5. WHEN a model response returns, THEN the gateway SHALL support post-response privacy scanning before the response is persisted or exported.

---

### Requirement 10: Private Identity and Evidence Vault

**User Story:**  
As an institutional user, I want private identity mappings and raw evidence anchors to remain in a protected vault so that public artifacts can preserve traceability without exposing PHI.

#### Acceptance Criteria

1. WHEN pseudonymous identifiers are created, THEN the mapping to real identifiers SHALL be stored only in a private controlled location.
2. WHEN public provenance references private evidence, THEN it SHALL use safe references, commitments, or vault pointers rather than raw identifiers.
3. WHEN an authorized user resolves a private anchor, THEN the access event SHALL be logged.
4. IF a user lacks authorization, THEN private identity mappings and raw anchors SHALL remain inaccessible.
5. WHEN a vault reference is exported, THEN it SHALL not reveal the underlying PHI by itself.

---

### Requirement 11: Privacy Audit Trail

**User Story:**  
As a compliance-aware researcher, I want privacy decisions to be auditable so that use, transformation, and disclosure of sensitive evidence can be reviewed.

#### Acceptance Criteria

1. WHEN sensitive content is classified, transformed, disclosed, blocked, or exported, THEN the privacy module SHALL create an audit record.
2. WHEN an audit record is created, THEN it SHALL identify the affected artifact, policy profile, decision type, timestamp, and responsible workflow component.
3. WHEN manual review or override occurs, THEN the audit trail SHALL record that human intervention was involved.
4. IF a disclosure is blocked, THEN the audit trail SHALL record the blocking reason.
5. WHEN audit logs are exported, THEN the system SHALL support a restricted form that does not expose protected content unnecessarily.

---

### Requirement 12: Public and Private Provenance Coordination

**User Story:**  
As a provenance reviewer, I want privacy controls to coordinate with provenance generation so that public traces are useful but do not disclose restricted evidence.

#### Acceptance Criteria

1. WHEN private provenance contains sensitive evidence, THEN the privacy module SHALL determine which fields may appear in public provenance.
2. WHEN public provenance is generated, THEN private identifiers SHALL be replaced, omitted, encrypted, or committed according to policy.
3. WHEN a public claim references private evidence, THEN the system SHALL preserve a safe link to the private provenance tree.
4. IF privacy policy prevents evidence-level disclosure, THEN the public provenance artifact SHALL indicate that evidence exists but is restricted.
5. WHEN private and public provenance artifacts are linked, THEN the relationship SHALL be auditable without exposing raw PHI.

---

### Requirement 13: Cryptographic Commitments and Future Proofs

**User Story:**  
As an advanced infrastructure designer, I want the privacy module to support cryptographic commitments and future zero-knowledge-style attestations so that public artifacts can be verified without revealing private evidence.

#### Acceptance Criteria

1. WHEN private evidence is represented in a public artifact, THEN the system SHALL support commitment-based references where appropriate.
2. WHEN public provenance is derived from private provenance, THEN the system SHALL support recording the private provenance root, public provenance root, and disclosure transaction metadata.
3. WHEN a disclosure transaction is finalized, THEN the system SHALL support signing or integrity-marking the transaction.
4. IF zero-knowledge-style proof support is unavailable, THEN the system SHALL still preserve enough structure for future proof integration.
5. WHEN cryptographic proof artifacts are present, THEN the system SHALL clearly distinguish what they prove from what they do not prove.

---

### Requirement 14: Conservative Failure Behavior

**User Story:**  
As a clinical AI safety reviewer, I want privacy failures to fail closed so that uncertainty does not result in accidental disclosure.

#### Acceptance Criteria

1. WHEN PHI detection fails, THEN the system SHALL treat the affected content as sensitive.
2. WHEN policy evaluation fails, THEN the system SHALL block disclosure.
3. WHEN redaction or transformation fails, THEN the system SHALL prevent the untransformed content from being sent externally.
4. WHEN secret retrieval fails, THEN the system SHALL avoid fallback behavior that exposes credentials or sensitive evidence.
5. WHEN privacy status is incomplete, THEN the system SHALL mark the workflow as requiring review.

---

### Requirement 15: User-Facing Privacy Status

**User Story:**  
As a researcher using EviTrace, I want clear privacy status indicators so that I understand what can be used, exported, shared, or sent to models.

#### Acceptance Criteria

1. WHEN a document is processed, THEN the system SHALL expose its privacy classification status.
2. WHEN evidence is prepared for model use, THEN the system SHALL expose whether the evidence is raw, redacted, pseudonymized, minimized, blocked, or review-required.
3. WHEN an output is generated, THEN the system SHALL expose whether it came from public-safe, restricted, or private evidence.
4. IF an artifact cannot be exported, THEN the system SHALL explain the policy category that prevents export.
5. WHEN users review results, THEN privacy status SHALL be visible alongside provenance status.

---

### Requirement 16: No Automatic Compliance Claims

**User Story:**  
As a responsible project maintainer, I want the privacy module to avoid overclaiming legal compliance so that EviTrace remains scientifically and institutionally credible.

#### Acceptance Criteria

1. WHEN documentation describes privacy features, THEN it SHALL avoid claiming automatic HIPAA or 42 CFR Part 2 compliance.
2. WHEN compliance-oriented workflows are described, THEN documentation SHALL state that institutional governance and legal review remain required.
3. WHEN a policy profile is used, THEN the system SHALL describe it as a workflow-control profile rather than a legal certification.
4. IF users attempt to export a compliance report, THEN the report SHALL distinguish technical controls from legal compliance conclusions.
5. WHEN privacy capabilities are presented, THEN they SHALL be framed as safeguards, controls, and audit support rather than guarantees.
