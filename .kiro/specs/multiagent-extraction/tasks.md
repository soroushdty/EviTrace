# Implementation Plan

- [ ] 1. Foundation: configuration, shared types, and the agent schema

- [ ] 1.1 Add the multiagent configuration section, register it, and implement strict resolution
  - Add a configuration section covering the feature enable flag, the dual-extraction mode, the per-agent enable flags, the graduation thresholds and release statistic name, the residual sampling fraction and seed, the per-agent model identities and retry limits, the quote match threshold, the numeric format list, the issue-to-destination mapping and its default, the default criticality, the verification gating threshold, the per-document call bounds for each agent, the repair attempt limit, the agreement escalation thresholds and actions, and the artifact directory
  - Register the new top-level key so the local configuration loader accepts it instead of rejecting the run
  - Resolve the section into an immutable settings object, applying a documented default for every omitted value and retaining the fully resolved mapping for later reporting
  - Reject an invalid value with an error naming the setting and the offending value before any stage begins
  - Observable: loading a configuration file with the new section returns a fully populated settings object whose resolved mapping lists every defaulted value; loading one with a sampling fraction above one raises an error naming that setting; loading one with an unknown sibling top-level key still raises the existing unknown-key error
  - _Requirements: 14.1, 14.2, 14.3_

- [ ] 1.2 Define the multiagent data model and vocabularies
  - Define frozen records for field answers, answer issues and verdicts, group agreement, dual-extraction decisions, second-extraction outcomes, verification selections and outcomes, repair outcomes, per-field decision records, the agent call ledger, excluded fields, and the aggregate result
  - Define the closed vocabularies for agent role, rater name, confidence, dual mode, group state, escalation target, support status, repair action, adjudication outcome, and quality-control issue code
  - Store evidence references as the bare pipeline-assigned identifier only, and import routing and agreement record types from their owning packages rather than redeclaring them
  - Carry the prompt template version and model identity on every answer and every agent outcome
  - Observable: a test constructs one instance of every record, asserts all are immutable and all collection fields are tuples, and asserts no record redeclares a routing or agreement type name
  - _Requirements: 1.3, 2.3, 3.3, 4.6, 5.7, 8.4, 9.2, 10.8, 11.2, 12.5, 13.1_
  - _Boundary: MultiagentModels_

- [ ] 1.3 Create the multiagent agent schema file and its single-owner validator
  - Author a schema artifact holding the second-extraction, verification, and repair system prompts, the shared policies, the response schemas for all three agents, and the per-role prompt template versions
  - Define the answer response schema as the compact answer object with the optional short-quote property, shared by the second extractor and by repair revisions
  - Define the verification response schema to require exactly one of the seven verdict values, to forbid a proposal for any field the request did not name, and to forbid a proposal carrying neither evidence identifiers nor a rationale
  - Define the repair response schema to require exactly one of the four actions and to require a complete answer object whenever the action is a revision
  - Implement a validator that loads and checks the artifact once at construction, exposes the three system prompts, the policies, and the prompt versions, and converts a validated payload into the corresponding records; export it as the sole singleton reader of that artifact
  - Observable: a well-formed payload of each kind validates and converts; a verification payload proposing a value for an unrequested field is rejected naming the offending field; a revision payload missing its rationale is rejected; a second module reading the schema artifact directly fails a guard test
  - _Requirements: 3.2, 9.2, 9.3, 9.4, 11.2, 11.3_
  - _Boundary: MultiagentAgentSchemaValidator_

- [ ] 1.4 Extend the compact answer contract with an optional short quote
  - Add the short quote as an optional property on the provider response schema and on the local answer validation, keeping the required key set and the confidence vocabulary unchanged
  - Validate the quote as a string when present and continue rejecting any key outside the permitted set
  - Observable: an existing four-key answer object still validates unchanged; a five-key object carrying the quote validates; an object carrying any other extra key is still rejected
  - _Requirements: 1.3, 1.4_
  - _Boundary: pipeline answer validator, provider response schema_

- [ ] 2. Deterministic adapters and answer quality control

- [ ] 2.1 (P) Adapt routing packs into request material without modifying them
  - Build one routed-evidence block per extraction chunk from the routing layer's extraction packs, ordered by field index, emitting identifier-only evidence exactly as the pack carries it
  - Expose a per-field view of the adjudicated route covering primary identifiers, ordered evidence, criticality, parser risk, the stricter-handling marking, and the empty reason, reading those values and never recomputing them
  - Return no block and mark the affected fields as extracted without a route when routing is disabled, failed, or covers none of the chunk's fields
  - Observable: building a block twice from the same routing fixture yields byte-identical output; a disabled routing result yields no block and every field marked as route-absent
  - _Requirements: 1.1, 1.2, 12.1, 12.2_
  - _Boundary: RoutedPackAdapter_

- [ ] 2.2 (P) Implement the quote-to-evidence fuzzy matcher
  - Normalize and tokenize both the quote and the cited evidence text through the project's existing text processing, then score the proportion of quote tokens present in the cited evidence
  - Return an explicitly undefined score, distinct from a mismatch, when the answer carries no quote or when every cited identifier resolves to identifier-only evidence
  - Expose a mismatch test that treats an undefined score as not a mismatch
  - Observable: a quote fully contained in its evidence scores one, an unrelated quote scores below the configured threshold, an absent quote yields an undefined score, and the mismatch test returns false for an undefined score
  - _Requirements: 5.3_
  - _Boundary: QuoteMatcher_

- [ ] 2.3 (P) Implement the read-only view over published agreement results
  - Resolve per-field-group agreement from the published report's stratified results, filtering to strata whose axis is the field group axis and whose agreement dimension is the value dimension, matching the stratum whose key is the requested group, and reading only that stratum's key, unit count, disagreement rate, and nested statistics list
  - Select from that nested list the entry whose statistic name matches the configured release statistic and whose dimension is the value dimension, reading only its value, undefined reason, and unit count
  - Never read the report's top-level statistics mapping for group resolution: it is keyed by agreement dimension, not by field group, and carries no per-group result
  - Resolve a group as graduated only when the statistic is present, numeric, free of an undefined reason, at or above the release threshold, and backed by at least the configured minimum unit count; resolve it as not graduated when a bar is missed and as unknown when the strata list is absent, no stratum matches, the statistic is absent or undefined, or the report reports that nothing was computed
  - Compute nothing and mutate nothing, and default to an empty report so the feature runs before the statistics module ships
  - Observable: an empty report resolves every group to unknown; a report whose top-level statistics mapping carries a passing value while the strata list carries none still resolves to unknown; a statistic below threshold and a statistic with too few units both resolve to not graduated; no code path writes to the supplied report and no agreement statistic is computed
  - _Requirements: 4.4, 4.5, 4.8, 7.2, 7.3, 7.5_
  - _Boundary: AgreementView_

- [ ] 2.4 Implement the escalation map and the low-agreement escalation policy
  - Resolve an issue code to an escalation destination from configuration, taking the most severe destination among a field's issues and applying the documented default with a warning for an unmapped code
  - Escalate verification coverage, dual extraction, or manual review for a field group whose published agreement falls below the configured threshold, recording the statistic name, its value, its unit count, and the threshold in force
  - Apply the documented unknown-agreement escalation when the published result is absent or undefined, recording it as unknown rather than as low
  - Provide no path that lowers a destination, relaxes a quality-control verdict, or accepts a field
  - Observable: a field carrying two issues resolves to the more severe destination; an unmapped issue code resolves to the default with a warning naming it; escalation never returns a destination less severe than the one supplied
  - _Requirements: 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.5_
  - _Boundary: EscalationMap, AgreementEscalationPolicy_
  - _Depends: 2.3_

- [ ] 2.5 Implement deterministic extraction quality control over answers
  - Verify that every requested field has exactly one answer and detect missing coverage, duplicate field identifiers, invalid evidence identifiers, empty values, and disallowed confidence labels as named issues
  - Detect a non-not-reported value carrying no evidence identifier as an uncited-value issue
  - Score any supplied quote against the cited evidence through the fuzzy matcher and report a mismatch below the configured threshold, carrying the score on the verdict
  - Detect a high-confidence answer with missing evidence or a failed quote match, a numeric-format field whose value carries no numeric content, and a critical field that is low-confidence, not reported, uncited, or routed to a page published as parser-risky
  - Record a pass or fail status with the full issue list for every field, resolve the escalation destination through the escalation map, and mark a passing unselected field as eligible for acceptance
  - Observable: a targeted fixture produces each issue code exactly once; a passing field carries an empty issue list and a no-escalation destination; repeated checks of the same input yield equal verdicts and no model call is made
  - _Requirements: 1.5, 2.1, 2.2, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.4_
  - _Boundary: ExtractionQualityControl_
  - _Depends: 2.2, 2.4_

- [ ] 3. Gating policies

- [ ] 3.1 Implement the dual-extraction policy
  - Select every field in calibration mode, and in production mode select critical fields, routes whose published status is empty or local-only or whose published resolved route confidence is low — read from that named field on the adjudicated route and never re-derived from the candidate evidence — routes marked as requiring stricter handling, routes depending on parser-risky pages, and fields in groups that have not cleared the release thresholds, evaluating the rules in a fixed order and recording which rule fired
  - Select a configured residual fraction of the remaining fields by a deterministic derivation from the recorded seed, the document identity, and the field index, using no run-time randomness
  - Treat a group with absent or undefined published agreement as not cleared and record the reason as unknown agreement
  - Enforce the per-document second-extraction bound with deterministic ordering, counting suppressions and leaving suppressed fields unchanged, and record a decision carrying the rule, the mode, the group agreement consulted, and the thresholds in effect for every field
  - Observable: calibration mode selects all fields; a production fixture selects exactly the critical, stricter-handling, parser-risky, and ungraduated fields while leaving a clean graduated field unselected; the residual sample is identical across repeated runs with the same seed and differs with a changed seed; lowering the bound records suppressions without changing selections already made
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 7.1_
  - _Boundary: DualExtractionPolicy_
  - _Depends: 2.3, 2.4_

- [ ] 3.2 Implement the verification gate
  - Mark as mandatory every critical field including one answered as not reported, every field whose quality-control issue maps to verification, every field on which the two extractors disagree on the normalized value or the support status, and every field whose route requires stricter handling
  - Consider low-confidence and non-critical not-reported fields as discretionary when discretionary verification is enabled
  - Suppress only discretionary selections under the per-document bound, always perform every mandatory selection, and set a recorded overrun flag when the mandatory set exceeds the bound
  - Record for every field whether it was selected, whether the selection was mandatory, which rule selected it, and whether the bound suppressed it
  - Observable: a fixture with one field per mandatory rule selects all four as mandatory; a bound of one suppresses only the discretionary field; a fixture whose mandatory set exceeds the bound performs all mandatory calls and sets the overrun flag
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - _Boundary: VerificationGate_
  - _Depends: 2.4, 2.5_

- [ ] 4. Agent stages

- [ ] 4.1 Build the multiagent prompt builders with their own stable prefixes
  - Build one document-level package per document and define a stable second-extraction prefix, a stable verification prefix, and a stable repair prefix, each wrapping only fixed instruction text and that one package, with no field index, timestamp, or run identifier inside any of them
  - Give the second-extraction builder a signature that cannot accept the primary answer, so blindness is structural rather than conventional, and place the requested field definitions and routed snippets strictly after the prefix
  - Place the field definition, candidate answers, cited evidence, alternative evidence, and quality-control issues after the verification prefix, and the field definition, current answer, issues, verifier critique, and route snippets after the repair prefix
  - Apply and return the configured decorrelation levers for the second extractor, and expose the per-role prompt template versions
  - Observable: for one document package every message from a given builder begins with identical prefix bytes; the second-extraction builder has no parameter capable of carrying the primary answer; a guard test asserts this module never references the existing extraction shared prefix
  - _Requirements: 3.1, 3.3, 9.1, 11.1, 12.3, 12.4, 12.5_
  - _Boundary: multiagent_prompts_

- [ ] 4.2 Implement the multiagent provider client over the governed egress path
  - Issue second-extraction, verification, and repair requests through the privacy subsystem's single egress gateway over an injected model transport, so request construction, the concurrency gate, the retry ladder, and backoff are reused from behind that transport rather than reimplemented
  - Bind each entry point's parameter list to the gateway send contract rather than inventing a parallel one: fill the request kind with this role's constant and forward the packet, the disclosure decision, the model, the request mapping, and the timestamp; take no semaphore of any kind, because the concurrency gate already lives behind the injected transport
  - Extend the privacy subsystem's request-kind literal, on both the transport protocol and the gateway send entry point, with the three multiagent kinds `second_extraction`, `verification`, and `repair`, and adapt the three roles onto the transport protocol in the privacy wiring module; never mislabel a multiagent call as a chunk call, since vendor approval and audit records key off the request kind
  - Carry the per-request variable material — the assembled user message, the response-format declaration, and the sampling parameters — in the request mapping, keeping the governed packet payload the only document-derived string in the call
  - Import the existing extraction client module not at all, and reach no provider-calling helper through it, since the gateway sits in front of that module and a direct call would bypass the disclosure decision, the evidence packet, the audit trail, vendor approval, and response scanning
  - Carry the authorizing evidence packet and disclosure decision on every call, place only the packet's governed payload into the request, and contribute no governance metadata to any request field
  - Raise rather than fall back to a direct provider call when no gateway is supplied, and surface a blocked, unapproved, undecided, or scan-violating call to the caller as a recorded call failure carrying the gateway's rationale
  - Return the raw response text and leave all validation to the caller
  - Record telemetry for all three new stages carrying the prompt template version, so their cost and prefix stability appear in the existing summaries
  - Import nothing from the pipeline, quality-control, or extractor packages
  - Observable: with a fake transport, each of the three roles reaches the transport only through the gateway, names its own request kind, and returns raw text with one telemetry record under its stage label; a blocked or unapproved call invokes the transport zero times and reports the rationale; with no gateway supplied zero provider invocations occur; a guard test asserts the module contains no import of the existing extraction client module, no reference to its retry helper, and no semaphore parameter on any entry point
  - _Requirements: 12.8_
  - _Boundary: multiagent_client, privacy gateway request-kind literal and privacy wiring (cross-spec addition, owned by privacy-core)_
  - _Depends: 4.1_

- [ ] 4.3 Implement multiagent artifact persistence, reuse, and invalidation
  - Persist every agent's raw request and response unconditionally, independent of log level, each recording the model identity, the prompt template version, and the field indices it covers, with primary and second-extraction raw output written to distinct locations
  - Persist answers, verdicts, dual decisions, verification outcomes, repair outcomes, decision records, and the aggregate result as plain queryable data using the project's atomic write helper
  - Key every artifact by document identity, document fingerprint, extraction schema fingerprint, and prompt template version digest, and reuse recorded artifacts on resume only when the key matches
  - Record a persistence failure and continue the run rather than discarding answers; discard and recompute a corrupt artifact with a warning
  - Observable: a run writes raw files plus the artifact set under the keyed directory; re-running with an unchanged key loads them and issues no provider call; changing the extraction schema fingerprint or a prompt template version discards them
  - _Requirements: 1.7, 3.4, 9.6, 11.7, 12.6, 13.1, 13.2, 13.3, 13.7, 14.8, 14.9_
  - _Boundary: MultiagentArtifactStore_

- [ ] 4.4 Adapt the existing primary extraction output into answers and enforce the primary contract
  - Convert the already-validated compact primary output into answer records carrying the value, cited identifiers, optional quote, confidence, prompt version, and model identity, without issuing the primary call itself
  - Record a rejected metadata-bearing response as a contract violation rather than swallowing it, and leave malformed-output handling to the pipeline's existing bounded repair path
  - Mark an answer whose value is the configured not-reported value as not reported, and mark its absence as verified only when it cites at least one evidence identifier
  - Mark fields extracted without a route so the no-route fallback is visible per field
  - Observable: a fixture of compact answers converts one-for-one with the primary rater name; a not-reported answer with no identifiers is recorded as an unverified absence; a routing-disabled fixture marks every answer as route-absent
  - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 2.3, 2.4_
  - _Boundary: PrimaryExtractionStage_
  - _Depends: 2.1, 1.4_

- [ ] 4.5 Implement the blind second extraction stage
  - Build the document package once per document and reuse it for every request, requesting only the fields the dual policy selected
  - Exclude the primary answer's value, quote, confidence, and identifiers from every request, and validate every response against the answer schema before use, retrying with a targeted repair instruction naming the validation failure up to the configured limit
  - Apply and record the configured decorrelation levers, and persist the raw request and response for every attempt separately from the primary raw output
  - Record an exhausted retry or provider failure as a not-completed outcome with a reason, retain the primary answer, and continue with the remaining fields
  - Observable: the recorded request payload for a field contains none of the primary answer's value, quote, confidence, or identifiers; a first-invalid then valid exchange produces a usable second answer; an always-failing exchange yields a not-completed outcome and leaves the primary answer intact
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 12.4_
  - _Boundary: SecondExtractionStage_
  - _Depends: 3.1, 4.2, 4.3_

- [ ] 4.6 Implement the gated verification stage
  - Send the field definition, the candidate answers, the cited evidence, the alternative evidence from the adjudicated route, and the quality-control issues, and nothing else
  - Require exactly one of the seven verdict values, reject a proposal for a field the request did not name as a contract violation, and reject a proposal carrying neither evidence identifiers nor a rationale
  - Carry an unsupported, contradicted, incomplete, or alternative-found verdict forward as making the field eligible for repair
  - Persist the raw request and response with the model identity and prompt version, associated with the field verified, and record a failed or exhausted call as a not-completed outcome so the field is still adjudicated without a verdict
  - Observable: each of the seven verdicts round-trips; a proposal for an unrequested field is rejected naming it; a failing call yields a not-completed outcome and the field still reaches adjudication
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_
  - _Boundary: VerificationStage_
  - _Depends: 3.2, 4.2, 4.3_

- [ ] 5. Adjudication and repair

- [ ] 5.1 Implement deterministic answer adjudication
  - Evaluate a fixed, first-match-wins rule order covering invalid or unmatched evidence, adverse verifier verdicts, a needs-manual-review verdict, extractor disagreement, the stricter criteria for critical fields, non-critical supported acceptance, and an unresolved fallback to manual review
  - Refuse to accept a field whose evidence identifiers are invalid or whose quote failed to match, regardless of agreement between the two extractors, and never consult an agreement value to relax a rule
  - Produce exactly one decision record per field sent to extraction, including fields whose stages failed, carrying the decision rule, every input the rule consumed, and the provenance of those inputs
  - Reach the decision without any model call and produce identical decisions for identical inputs
  - Observable: a targeted fixture produces each rule exactly once; a field with invalid identifiers on which both extractors agree is not accepted; a critical field passing only the non-critical criteria is not accepted; a field whose second extraction failed still receives a decision record
  - _Requirements: 7.4, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9_
  - _Boundary: AnswerAdjudicator_
  - _Depends: 2.5, 4.6_

- [ ] 5.2 Implement the targeted answer repair stage with post-repair re-checking
  - Send only the field definition, the current answer, the quality-control issues, the verifier critique where one exists, and the route's evidence snippets
  - Require exactly one of the four configured actions, and require a complete answer with value, identifiers, quote, confidence, and rationale whenever the action is a revision, rejecting an incomplete revision through the bounded retry path
  - Re-run the same extraction quality-control implementation on every repaired answer before it is adjudicated, and mark a field for manual review when the re-check fails or the attempt limit is reached
  - Persist the raw output and the final repair decision per field, and never accept schema-invalid extractor output as an input to this stage
  - Observable: a revision is re-checked and accepted; a revision that fails re-check becomes manual review; a field reaching the attempt limit becomes manual review; a schema-invalid extractor response is consumed by the existing malformed-output path and never reaches this stage
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8_
  - _Boundary: AnswerRepairStage_
  - _Depends: 2.5, 5.1_

- [ ] 6. Publication of rater data, provenance, and telemetry

- [ ] 6.1 (P) Emit rater field outputs for the agreement statistics module
  - Emit one rater output record per rater per field in the form the agreement module consumes, populating the rater name, document, field, field group, value, evidence identifiers, confidence, support status, not-reported flag, page association, and criticality
  - Take the support status from the verifier verdict where one exists and leave it unset otherwise, inventing nothing
  - Compute no statistic, build no comparison unit, and call no agreement code
  - Observable: a dual-extracted field yields two records with the fixed rater names and every consumed attribute populated; a field the second extractor skipped yields one record; a guard test confirms no agreement computation occurs here
  - _Requirements: 3.5_
  - _Boundary: RaterOutputEmitter_

- [ ] 6.2 (P) Emit answer derivation records into the provenance subsystem
  - Register the seven multiagent stage names — primary adaptation, extraction quality control, second extraction, verification, adjudication, repair, and post-repair re-checking — in the provenance subsystem's stage contract table and its stage ordering, since that table is closed and recording an undeclared stage raises an unknown-stage error
  - Pin each of the seven stages to one member of the provenance subsystem's closed derivation-kind vocabulary, adding no new member: adaptation normalizes, the three quality-control and verification stages annotate, second extraction and repair extract, and adjudication reconciles
  - Build every record through the provenance subsystem's stage-event adapter rather than constructing a derivation record directly, passing a model identity for the three model-driven stages so the adapter marks them probabilistic and omitting it for the four deterministic ones
  - Carry the prompt template version and the field indices whose answer a later stage replaced in the provenance subsystem's namespaced extensions mapping under a multiagent namespace, following its key naming rule, because neither is a field of the derivation record; pass that mapping to the adapter's own extensions parameter in the same call that builds the step, never by rebuilding, copying, or replacing the returned record afterwards; express a discarded citation through the adapter's own removal reason and removed identifiers instead
  - Construct scoped evidence identifiers only through the provenance subsystem's sanctioned constructor, never by string formatting
  - Continue the run and record that emission did not occur when the recorder is absent or disabled, including when a stage is rejected as undeclared
  - Observable: a run against a recording double emits one record per stage with the expected derivation kind and determinism marking; every extension key matches the namespaced naming rule and round-trips through serialization; a guard test asserts each of the seven stage names appears in both provenance tables, that no argument the adapter does not declare is passed to it, that the prompt template version and the replaced field indices arrive through the adapter's extensions parameter and never as adapter keywords of their own, and that no multiagent module formats an evidence identifier itself; a run with no recorder completes and reports that provenance emission did not occur
  - _Requirements: 13.4, 13.5, 13.6_
  - _Boundary: AnswerProvenanceEmitter, provenance stage contract table (cross-spec addition)_

- [ ] 6.3 (P) Extend telemetry and the dependency-direction guard for the multiagent stages
  - Record telemetry under the three new stage labels — second extraction, answer verification, and answer repair — so their cost appears in the existing stage summaries and the prefix-drift check covers them; keep the stage identifier a free-form string and introduce no enum, literal, or allowlist over stage names, since cost reporting must report an unrecognized stage as itself
  - Extend the dependency-direction test to assert the multiagent prompt, client, and schema-validator modules import nothing from the pipeline, quality-control, or extractor packages, and that quality control imports nothing from the multiagent package
  - Observable: a run recording multiagent calls shows all three new stages in the stage summary; the dependency-direction suite fails if a multiagent agent module is given a pipeline import
  - _Requirements: 12.8_
  - _Boundary: telemetry, dependency direction tests_

- [ ] 7. Orchestration and pipeline integration

- [ ] 7.1 Implement the service that sequences the stages and isolates failures
  - Build the multiagent document package once per document and construct it through the privacy subsystem's packet builder before it reaches any request, using only the returned governed payload thereafter and carrying the returned packet and disclosure decision on every agent call
  - Record the three model-driven stages as not completed with an egress-unavailable reason, and issue no provider call at all, when no gateway or no packet builder is supplied; record a blocked document package as a document-level failure and continue with the next document
  - Sequence configuration resolution, artifact reuse, primary adaptation, extraction quality control, dual-extraction selection and second extraction, quality control of the second answers, rater output emission, agreement escalation, verification gating and verification, adjudication, repair, post-repair re-checking, re-adjudication, provenance emission, and persistence
  - Exclude pipeline-prefilled fields before any stage and record each exclusion with its reason
  - Return a disabled result carrying no decisions when the feature is turned off, and skip an individually disabled agent while recording the disablement and treating its absent output as absent rather than failed
  - Capture a per-field failure against that field and still adjudicate it with the outputs it has; capture a document-wide failure onto the result rather than raising
  - Account for calls, suppressions, mandatory overrun, and the trigger rule per non-primary call, and carry the fully resolved configuration on the result
  - Dispatch per-field agent calls concurrently without introducing a new concurrency control and without holding or forwarding a semaphore, since the concurrency gate lives behind the injected model transport
  - Observable: a mocked end-to-end run returns a result whose call ledger matches the mocked call count and whose decision count equals the non-excluded field count; injecting an exception for one field yields that field recorded as failed and still decided; disabling each agent in turn completes the run with the remaining stages
  - _Requirements: 2.5, 12.7, 13.6, 14.2, 14.4, 14.5, 14.6, 14.7, 14.8_
  - _Boundary: MultiagentExtractionService_
  - _Depends: 5.2, 6.1, 6.2, 4.3_

- [ ] 7.2 Wire the routed evidence block and the answer pipeline into per-document processing
  - Build the routed evidence block per extraction chunk from the routing result, construct it through the privacy subsystem's packet builder before it reaches any request because it is a model-visible payload derived from document content, and pass the returned governed payload into the extraction request so it is emitted after the shared paper prefix and before the extraction map, passing nothing when routing is disabled or failed
  - Await the answer pipeline after chunk extraction and before persisting, supplying the validated primary answers, the routing result, the full field list, the pre-filled field indices, the evidence text map, the artifact key, the resolved configuration, any published agreement report, the provenance recorder, the assembled egress gateway, the packet builder, and the telemetry collector
  - Replace each field's persisted value with the pipeline's final answer where a decision produced one, and record a document-level failure without aborting the run
  - Observable: processing a fixture document with the feature enabled writes the multiagent artifact set and completes; with the feature disabled the persisted extraction bytes match the pre-change baseline; a document whose answer pipeline fails is recorded and the remaining documents still process
  - _Requirements: 1.1, 1.2, 12.1, 12.2, 14.5_
  - _Depends: 7.1, 2.1_

- [ ] 8. Validation

- [ ] 8.1 Verify blindness, dual-extraction gating, and mandatory verification end to end
  - Exercise a document with a critical field, a stricter-handling route, a parser-risky route, an ungraduated group, and a clean graduated field, asserting the expected selection set and recorded rules
  - Assert the second-extraction request payload for every field contains none of the primary answer's content
  - Exercise a mandatory verification set larger than the configured bound and assert every mandatory call is made, the overrun is recorded, and only discretionary selections were suppressed
  - Observable: the fixture produces exactly the expected dual and verification selection sets, ledger counts, and overrun flag, with no primary content in any second-extraction payload
  - _Requirements: 3.1, 4.2, 4.7, 8.1, 8.3, 8.5_
  - _Depends: 7.2_

- [ ] 8.2 Verify the verification, repair, re-check, and adjudication chain end to end
  - Exercise an adverse verifier verdict that routes a field to repair, a revision that passes re-checking and is accepted, a revision that fails re-checking and becomes manual review, and a field reaching the repair attempt limit
  - Exercise two extractors disagreeing on a field and assert it is never accepted on agreement alone, and a field with invalid evidence identifiers on which both extractors agree and assert it is likewise not accepted
  - Observable: every scenario ends in exactly one decision record with the expected outcome and rule, and no accepted field carries an unresolved evidence issue
  - _Requirements: 9.5, 10.5, 10.6, 11.5, 11.6_
  - _Depends: 7.2_

- [ ] 8.3 (P) Verify prompt-cache stability, routed-block placement, and the disabled path
  - Assert that with the feature enabled every extraction chunk prefix for one document is byte-identical and the existing extraction shared prefix source is unchanged
  - Assert that building an extraction message with no routed block reproduces the pre-change bytes exactly, and that with a block the shared prefix bytes are unchanged and the block appears after them and before the extraction map
  - Assert every multiagent agent message for one document shares identical prefix bytes across requests to that agent
  - Assert that both document-derived payloads this feature introduces — the routed evidence block and the multiagent document package — are each one payload instance per document constructed through the privacy subsystem's packet builder exactly once, that only the governed payload reaches a request, and that it is byte-identical across every request carrying it
  - Assert with a fake transport that every multiagent provider call reaches that transport only through the egress gateway, that the transport is invoked zero times on a blocked, unapproved, or undecided path, and that supplying no gateway issues no provider call at all
  - Assert that with the feature disabled the persisted extraction bytes match the pre-change baseline exactly
  - Observable: the prefix-stability, placement, packet-governance, governed-egress, and disabled-path assertions pass and the prefix-drift check reports no drift for an enabled run
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 14.5_
  - _Boundary: multiagent_prompts, extraction prompt builder_
  - _Depends: 7.2_

- [ ] 8.4 (P) Verify resumability, invalidation, ablation, and configuration failure handling
  - Assert a resumed run with a matching artifact key reuses recorded answers, verdicts, verifications, repairs, and decisions and issues no provider call
  - Assert a changed document fingerprint, a changed extraction schema fingerprint, or a changed prompt template version discards the recorded artifacts and re-runs the affected stages
  - Assert disabling each agent in turn produces a complete decision set with the disablement recorded, and that an invalid configuration value prevents the run from starting with an error naming the setting
  - Assert an artifact write failure is recorded while the run continues
  - Observable: the resume scenario records zero provider calls, each invalidation scenario re-runs, each ablation scenario decides every field, and the write-failure scenario completes with the failure recorded
  - _Requirements: 12.6, 14.3, 14.4, 14.8, 14.9_
  - _Boundary: MultiagentArtifactStore, MultiagentConfig_
  - _Depends: 7.2_

- [ ] 8.5 (P) Verify determinism and the agreement-statistics boundary
  - Assert that extraction quality control, dual-extraction selection, verification gating, and adjudication each produce equal output across repeated runs on the same fixture, including the residual sample under a fixed seed
  - Assert that no multiagent module computes an agreement statistic, that the published agreement report is never written to, and that varying an agreement value never accepts a field its quality-control verdict failed
  - Observable: the determinism suite passes for all four stages and the boundary guard fails if a statistic computation is introduced under the multiagent package
  - _Requirements: 4.3, 4.8, 5.8, 7.5, 10.2_
  - _Boundary: ExtractionQualityControl, DualExtractionPolicy, VerificationGate, AnswerAdjudicator_
  - _Depends: 7.2_
