# Requirements Document

## Project Description (Input)
Researchers running clinical or institutionally restricted documents through EviTrace have no way to govern what leaves the machine. Every extraction chunk is shipped verbatim to an external model provider with no classification, no policy check, and no record of what was disclosed. There is nothing to show an IRB, a data steward, or a collaborator, and nothing that stops a sensitive page from being transmitted. Privacy is entirely greenfield: there is no privacy package, no sensitivity classifier, and secret handling is a single unmanaged path that binds the model-provider key into a module-level constant at import time, with no rotation story, no key-version identity, and no dev/prod distinction.

`privacy-core` introduces a first-class privacy subsystem: documents are classified on ingest against a declared sensitivity vocabulary; every secret is resolved through one managed access path with key-version identity and safe failure; a declarative disclosure policy is evaluated before any evidence crosses the local boundary; model-safe evidence packets are emitted that retain provenance identifiers; 100% of external model calls are routed through a privacy gateway that can be configured to block all external egress; every classification, disclosure, and block decision is recorded in a privacy audit trail; every uncertainty fails closed; and privacy status is exposed to the operator without exposing content. The subsystem defines the interfaces that `privacy-transformations` will implement, populates the inert privacy carrier that `provenance-core` defined, and documents explicitly that it ships controls and audit surfaces only — never a legal compliance claim.

## Introduction

This spec establishes privacy as core infrastructure rather than an afterthought bolted onto the model call path. It defines five things and nothing more: a **sensitivity label vocabulary** with a conservative default, a **managed secret access path** with key-version identity, a **declarative disclosure policy gate** that is the single decision authority, a **model-safe evidence packet** that survives transformation with its provenance identifiers intact, and a **gateway** through which every external model call passes. Around those five it adds an append-only **privacy audit trail**, a **fail-closed** rule that applies uniformly to every uncertainty, an operator-facing **privacy status** projection that reveals decisions without revealing content, and **anti-overclaiming** documentation obligations.

The governing design principle is **privacy decides, provenance consumes**. `provenance-core` has already pinned an inert carrier structure on every provenance node whose label and decision fields are opaque strings to provenance. This spec is the sole component that populates that carrier; it never redefines evidence node identity, which `provenance-core` owns and this spec consumes as-is.

The second governing principle is **mechanism here, algorithm elsewhere**. Classification in this spec is declaration-driven plus a pluggable detector interface: it establishes the label vocabulary, the uncertainty state, and the fail-closed default, not a protected-health-information detection algorithm. Likewise the policy gate can require a transformation and dispatch to a registered provider, but implements no redaction, pseudonymization, minimization, or date-shifting logic — `privacy-transformations` implements those behind the interfaces defined here.

A hard operational constraint runs through the whole spec: the gateway sits in front of the existing model client and must not perturb the prompt-cache-stable shared prefix that all model calls for a given document share. Privacy metadata is recorded alongside a call, never injected into the material that must remain byte-identical across warmup, extraction chunks, and synthesis.

## Boundary Context

- **In scope**: The sensitivity label vocabulary, the uncertain-and-review state, and the conservative default when privacy status is unknown; declaration-driven classification and the pluggable detector interface; a single managed access path for every secret, token, and key, including key-version identity, dev-versus-production distinction, and safe failure; the disclosure policy profile format, policy evaluation, the block-when-no-policy-applies rule, and disclosure decision records; dispatch to registered transformation providers when a policy requires a transformation; construction of model-safe evidence packets that preserve the evidence identifiers needed for provenance linking, and the rule that every model-visible document-derived string is constructed through that packet builder regardless of which component originates it; a gateway that every external model call passes through, including vendor and model profile approval, a configurable mode that blocks all external egress, packet-and-decision recording, and a post-response scanning hook; an append-only privacy audit trail with a restricted export form; uniform fail-closed behavior on every privacy failure; an operator-facing privacy status projection that exposes classifications and decisions without exposing restricted content; population of the privacy carrier defined by `provenance-core`; and documentation obligations that keep the project from claiming legal compliance.
- **Out of scope**: The transformation algorithms themselves — redaction, pseudonymization, minimization, date-shifting, and leakage-risk scoring belong to `privacy-transformations`, which implements the provider interface this spec defines. Any protected-health-information detection model, named-entity recognizer, or clinical encoder. Semantic firewall behavior and leakage-risk estimation. A private identity or evidence vault, cryptographic commitments, signing, tamper-evidence, and public-versus-private provenance views, all of which belong to `public-private-provenance`. Provenance graph structure, evidence node identity, and chain validation, which `provenance-core` owns and this spec consumes without redefining. Any legal compliance claim, certification, or attestation of any kind. Project-level access control for a multi-user deployment is explicitly deferred: EviTrace is a single-user local tool today and multi-user authentication and authorization is an unmade product decision, not a privacy-mechanism gap; when that decision is made, this spec is the intended home.
- **Adjacent expectations**: This spec expects `provenance-core` to supply the evidence node identity and the inert privacy carrier structure, and it populates that carrier rather than defining a second mechanism. It expects `privacy-transformations` to register transformation providers against the interface defined here; until such a provider exists, a policy that requires a transformation blocks rather than degrades. It expects the existing configuration loader to remain the single source of truth for resolved settings, and the existing model client to remain the only component that talks to the model provider, now reached exclusively through the gateway. It expects the operator, not this subsystem, to author policy profiles and to declare document sensitivity where automatic classification is not available. It does not own, and must not absorb, the behavior of any of those adjacent components.
- **Standing product boundaries**: This spec does not admit autonomous systematic review generation, automated clinical recommendations, guaranteed extraction correctness without human validation, scanned-document workflows beyond fallback support, or meta-analysis automation.

## Requirements

### Requirement 1: First-Class Privacy Subsystem and Conservative Default

**Objective:** As a clinical AI infrastructure designer, I want privacy to exist as a first-class subsystem with a conservative default, so that sensitive evidence is governed before it reaches models, logs, exports, or provenance artifacts rather than after.

#### Acceptance Criteria

1. When a document enters the workflow, the privacy subsystem shall determine whether privacy controls apply to that document before any evidence derived from it is prepared for external use.
2. When evidence is prepared for model use, the privacy subsystem shall determine which disclosure form applies to that evidence from a declared set comprising raw, transformed, blocked, and review-required.
3. If the privacy status of a document, evidence unit, or run is unknown, then the privacy subsystem shall represent that status as restricted rather than as permitted.
4. The privacy subsystem shall be the single component that decides disclosure; no other component shall evaluate sensitivity or authorize an external transmission.
5. The privacy subsystem shall operate without requiring a network service, and shall reach a decision for every document processed in a run.
6. Where the privacy subsystem is disabled by configuration, the workflow shall record that privacy governance was not applied and shall expose that fact in the run's privacy status rather than reporting the run as governed.

### Requirement 2: Managed Secret Access and Key-Version Identity

**Objective:** As a system operator, I want every credential resolved through one managed access path, so that secrets are not scattered through the codebase, never leak into observable artifacts, and can be rotated with the key version attributable to each event.

#### Acceptance Criteria

1. When any component requires an API key, token, signing key, encryption key, or other secret, the privacy subsystem shall resolve it through a single managed access path.
2. When a secret is resolved, the privacy subsystem shall return a handle that exposes the secret value only at the point of use and shall not place the secret value into logs, audit records, provenance artifacts, model prompts, status output, or exported files.
3. If a required secret is missing, expired, malformed, or otherwise unavailable, then the privacy subsystem shall fail with an operational error that names the secret by identifier and its resolution source, shall not substitute a fallback value, and shall not proceed with the operation that required it.
4. When a privacy-relevant event occurs that used a secret, the privacy subsystem shall record a non-secret key-version identifier for the key in effect so that the event remains attributable across key rotations.
5. Where a development-mode secret source is permitted, the privacy subsystem shall label the resolved secret as development-mode, shall expose that mode in the run's privacy status, and shall refuse to resolve a development-mode secret while the subsystem is configured for production mode.
6. The privacy subsystem shall resolve each secret at the point of use rather than once at workflow startup, so that a rotated secret takes effect for subsequent operations without restarting the workflow.

### Requirement 3: Sensitivity Classification and Label Vocabulary

**Objective:** As a researcher working with clinical documents, I want content classified against a declared sensitivity vocabulary, so that evidence can be governed according to its risk and uncertainty is never silently resolved as safe.

#### Acceptance Criteria

1. The privacy subsystem shall define a closed sensitivity label vocabulary that includes, at minimum, protected-health-information-bearing, restricted, internal-only, research-only, and public-safe.
2. When a document is ingested, the privacy subsystem shall assign it a sensitivity label from an operator declaration, from a registered detector, or from the conservative default, and shall record which of those three produced the label.
3. When a heightened-sensitivity category such as a substance-use-disorder-related record is declared or detected, the privacy subsystem shall assign a label that selects stricter policy treatment than the general restricted label.
4. If classification is uncertain, unavailable, or produced by a detector that reports low confidence, then the privacy subsystem shall mark the content as requiring review and shall not assign a permissive label.
5. When a sensitivity label is assigned, the privacy subsystem shall make it linkable to the source document and to the evidence units derived from it using the evidence identity defined by the provenance subsystem, without defining a second identity scheme.
6. If an operator manually overrides an assigned classification, then the privacy subsystem shall apply the override, shall record the prior label, the new label, the stated reason, and that a human intervened, and shall not permit an override to silently widen disclosure without that record.
7. Where no detector is registered, the privacy subsystem shall continue to classify from operator declarations and the conservative default rather than failing the run.

### Requirement 4: Privacy Label Handoff to Provenance

**Objective:** As a privacy-conscious investigator, I want privacy labels and disclosure decisions attached to provenance records by the privacy subsystem alone, so that traceability and disclosure control integrate without either subsystem interpreting the other's data.

#### Acceptance Criteria

1. When a sensitivity label or disclosure decision exists for an artifact that has a corresponding provenance record, the privacy subsystem shall supply that label and decision to the provenance subsystem through the carrier structure the provenance subsystem defines.
2. When the privacy subsystem supplies a label or decision, it shall identify itself as the supplying component and shall supply the time of supply.
3. The privacy subsystem shall use the provenance-defined carrier as the only mechanism for attaching privacy metadata to provenance records, and shall not introduce a second attachment mechanism.
4. If the provenance subsystem rejects a supplied label or decision as non-conforming, then the privacy subsystem shall record the rejection in the privacy audit trail and shall treat the affected artifact as review-required.
5. While no provenance record exists for an artifact, the privacy subsystem shall still classify, decide, and audit that artifact rather than deferring its governance.

### Requirement 5: Policy-Gated Disclosure

**Objective:** As an institutional user, I want every movement of evidence across the local boundary to be gated by a declarative policy, so that restricted content is never disclosed accidentally and every decision is explainable after the fact.

#### Acceptance Criteria

1. When evidence is about to leave the local boundary, the privacy subsystem shall evaluate the applicable disclosure policy profile before the evidence is transmitted or written to an external target.
2. When an applicable policy blocks external use for the evidence's sensitivity label, the privacy subsystem shall prevent the evidence from reaching an external model or export target.
3. When an applicable policy permits limited use, the privacy subsystem shall require the transformation named by that policy and shall dispatch it to a registered transformation provider before disclosure.
4. If no policy profile applies to the evidence's sensitivity label, then the privacy subsystem shall block the disclosure and mark the evidence review-required rather than permitting it by default.
5. If a policy profile is malformed, references an unknown label, or names a transformation for which no provider is registered, then the privacy subsystem shall block every disclosure governed by that profile and shall report the specific defect.
6. When a disclosure decision is reached, the privacy subsystem shall record the policy profile identity and version, the decision, a rationale category drawn from a declared set, and the affected artifact identifiers.
7. The privacy subsystem shall reach the same decision for the same evidence, label, and policy profile on every evaluation, so that a decision is reproducible from the recorded inputs.
8. The privacy subsystem shall evaluate policy without implementing any transformation algorithm itself.

### Requirement 6: Model-Safe Evidence Packets

**Objective:** As a biomedical AI researcher, I want sensitive documents converted into model-safe evidence packets, so that external reasoning is possible without unnecessary exposure and without losing the identifiers that make results traceable.

#### Acceptance Criteria

1. When evidence governed by a permitting policy is prepared for external use, the privacy subsystem shall construct an evidence packet containing only the content that policy permits.
2. When an evidence packet is constructed, the privacy subsystem shall preserve the evidence identifiers needed for provenance linking, unchanged, alongside any transformed content.
3. When an evidence packet is constructed, the privacy subsystem shall assign it a packet identity that is recorded with the disclosure decision and that resolves back to the contributing evidence units.
4. If an evidence packet cannot be constructed within the permitted content set, then the privacy subsystem shall block external use of that evidence and shall report the reason.
5. If a required transformation reports failure, is unavailable, or returns content the policy does not permit, then the privacy subsystem shall block the packet and shall not transmit the untransformed content.
6. When an evidence packet is transmitted, the privacy subsystem shall record the packet identity together with the disclosure decision that authorized it.
7. The privacy subsystem shall not place any secret value, credential, or key material into an evidence packet.
8. When any model-visible string derived from document content is prepared for external use — whatever its shape or its originating component — it shall be constructed through the evidence packet builder and shall reach an external model only as an evidence packet payload; no component shall assemble document-derived model-visible content by another path, and any subsequent capability that introduces a new model-visible payload shall route that payload through the packet builder before it is disclosed.

### Requirement 7: External Model Gateway and Local-Only Mode

**Objective:** As a system operator, I want every external model call to pass through a privacy-aware gateway, so that nothing reaches an off-site service without policy review and so that I can choose a mode in which nothing leaves the machine at all.

#### Acceptance Criteria

1. When any component prepares an external model call, that call shall pass through the privacy gateway, and no component shall reach the model provider by any other path.
2. When the gateway receives content whose sensitivity label is not authorized for external transmission by the applicable policy, the gateway shall block the call.
3. When the gateway transmits a request, it shall record the packet identity, the vendor and model profile in effect, the policy profile identity, and the disclosure decision.
4. If the vendor or model profile in effect is not approved for the sensitivity level of the content, then the gateway shall block the request and shall report the unapproved profile.
5. Where local-only mode is selected, the gateway shall block every external call for the entire run, shall make that selection effective before any external call is attempted, and shall report each blocked call as a policy block rather than as an error.
6. When a model response returns, the gateway shall offer a post-response scanning point at which registered scanners may inspect the response before it is persisted or exported, and shall block persistence when a scanner reports a violation.
7. The gateway shall not alter the content of the prompt material that must remain byte-identical across a document's warmup, extraction, and synthesis calls, and shall record its own metadata outside that material.
8. When the gateway blocks or permits a call, the operator shall be able to determine from the recorded decision which document text was and was not transmitted.
9. If the gateway itself fails to reach a decision, then it shall block the call rather than transmitting.

### Requirement 8: Privacy Audit Trail

**Objective:** As a compliance-aware researcher, I want every privacy decision recorded in an append-only trail, so that classification, transformation, disclosure, and blocking can be reviewed by a data steward or an institutional reviewer.

#### Acceptance Criteria

1. When content is classified, transformed, disclosed, blocked, or exported, the privacy subsystem shall create an audit record for that event.
2. When an audit record is created, it shall identify the affected artifact, the policy profile in effect, the decision type, the time of the event, the responsible workflow component, and the key-version identifier where a secret was used.
3. When a manual review or an operator override occurs, the audit trail shall record that human intervention was involved and shall retain the pre-override state.
4. If a disclosure is blocked, then the audit trail shall record the blocking reason drawn from a declared set of reason categories.
5. When the audit trail is exported, the privacy subsystem shall support a restricted export form that omits protected content and secret values while retaining decisions, identifiers, and reasons.
6. The privacy audit trail shall be append-only; no component shall modify or delete a previously written audit record.
7. If writing an audit record fails, then the privacy subsystem shall treat the governed operation as failed and shall block the disclosure it would have recorded.

### Requirement 9: Fail-Closed Behavior

**Objective:** As a clinical AI safety reviewer, I want every privacy failure to fail closed, so that uncertainty never results in accidental disclosure.

#### Acceptance Criteria

1. If sensitivity detection fails, errors, or times out, then the privacy subsystem shall treat the affected content as sensitive.
2. If policy evaluation fails for any reason, then the privacy subsystem shall block the disclosure it was evaluating.
3. If a transformation fails, is unavailable, or completes with an unverifiable result, then the privacy subsystem shall prevent the untransformed content from being sent externally.
4. If secret resolution fails, then the privacy subsystem shall not fall back to any alternative source that would expose a credential or transmit sensitive evidence.
5. If privacy status is incomplete for any artifact in a run, then the privacy subsystem shall mark that artifact and the run as requiring review.
6. The privacy subsystem shall have no configuration setting, environment variable, or code path whose effect is to permit disclosure when a privacy decision could not be reached.
7. When a privacy failure blocks an operation, the workflow shall continue processing artifacts that are unaffected rather than aborting the run, and shall report each blocked artifact individually.

### Requirement 10: Operator-Facing Privacy Status

**Objective:** As a researcher using EviTrace, I want clear privacy status for documents, evidence, and outputs, so that I understand what may be used, exported, shared, or sent to a model without having to read restricted content to find out.

#### Acceptance Criteria

1. When a document has been processed, the privacy subsystem shall expose that document's sensitivity classification and the component that assigned it.
2. When evidence has been prepared for model use, the privacy subsystem shall expose whether that evidence was disclosed raw, disclosed transformed, blocked, or marked review-required.
3. When an output has been generated, the privacy subsystem shall expose whether it derives from public-safe, restricted, or private evidence.
4. If an artifact cannot be exported, then the privacy subsystem shall expose the policy category that prevents the export.
5. The privacy status projection shall contain no restricted content, no secret value, and no transformed-away material, and shall remain safe to display or share on its own.
6. The privacy subsystem shall expose privacy status as queryable data suitable for presentation alongside provenance status, and shall not render, format, or export a user interface.
7. When the run completes, the privacy subsystem shall expose a run-level privacy status summarizing counts by classification and by decision type.

### Requirement 11: Anti-Overclaiming Documentation

**Objective:** As a responsible project maintainer, I want the privacy subsystem to state plainly what it does and does not provide, so that EviTrace remains scientifically and institutionally credible and no operator mistakes a control for a certification.

#### Acceptance Criteria

1. The privacy subsystem's documentation shall not claim automatic compliance with any health-information regulation or confidentiality statute.
2. When documentation describes a compliance-oriented workflow, it shall state that institutional governance, agreements with providers, review-board approval, and legal review remain required.
3. When a policy profile is described in documentation, configuration, or status output, it shall be described as a workflow-control profile rather than a legal certification.
4. If an audit or status artifact is exported, then that artifact shall carry a statement distinguishing technical controls from legal compliance conclusions.
5. When privacy capabilities are presented anywhere in the project, they shall be framed as safeguards, controls, and audit support rather than as guarantees.
6. The privacy subsystem shall not produce any artifact whose name, field, or value asserts that a document, run, or export is compliant, certified, or approved by any external authority.
