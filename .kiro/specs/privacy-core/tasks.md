# Implementation Plan — privacy-core

- [ ] 1. Foundation: package scaffold, dependency rules, configuration, policy file, and secret-source description

- [ ] 1.1 Create the `src/privacy/` package skeleton and declare its dependency direction
  - Create the package directory with a placeholder public re-export surface, plus the mirrored test directory.
  - Add nine forbidden-import pairs to the AST dependency-direction rule table: the privacy package must not import the agents, pipeline, PDF-extraction, quality-control, or text-processing packages; and the agents, PDF-extraction, quality-control, text-processing, and provenance packages must not import privacy.
  - Add one named test per new pair alongside the existing per-pair tests so the exhaustive test and the named tests stay consistent.
  - Add an import-isolation test asserting the privacy package imports cleanly with the pipeline and agents packages absent from the module table and performs no network access.
  - Add the agent-client boundary test in two parts, so it stays satisfiable when downstream capabilities add their own agent clients. Part one: an allowed-importer check for the existing OpenAI client module, written as a data-driven check against a declared allowed-importer set. Seed that set with the document processor's three current lazy import sites so the test is green on today's code; the integration tasks replace those entries with the wiring module. Part two: a glob-driven check over every agent-client module in the agents package — the existing one and any added later — asserting each imports no other agent client, makes no direct provider SDK call, and reaches the provider only through an injected gateway **or** transport parameter on its provider-calling entry points, so its egress can only flow through the gateway. The disjunction matters: a client taking an injected gateway satisfies the intent more strictly than one taking a transport, because it cannot reach a transport without first passing the gate check, the vendor-approval check, and the audit append; the known downstream clients are of that stricter form, so a predicate demanding a transport parameter would reject the very clients the rule was written to permit. What is never permitted is a provider-calling entry point with neither.
  - State in the test module docstring why part two exists: a direct call to the client's retry helper bypasses the disclosure gate, the evidence packet, the audit trail, vendor-profile approval, and response scanning entirely. The glob form is deliberate — a fixed module list would have to be edited, and could be quietly widened, when a new agent client lands.
  - Observable: the fast suite passes with the nine new dependency-direction tests, the import-isolation test, and both parts of the agent-client test collected and green against the pre-refactor code base; adding a fabricated second agent-client module that imports the existing client, or that calls the provider SDK directly, or whose provider-calling entry point takes neither an injected gateway nor an injected transport, makes part two fail; a fabricated client whose entry point takes only an injected gateway passes.
  - _Requirements: 1.5, 7.1_
  - _Boundary: PrivacyPackageScaffold_

- [ ] 1.2 Register the privacy configuration block, the policy profile file, and the run-scoped output directory
  - Add a privacy defaults mapping covering the enable flag, mode, local-only flag, output subdirectory, policy file path, vendor profile identifier, declarations path, detector confidence threshold, the three plug-in registries, and the restricted-export flag.
  - Register privacy as a known top-level configuration key so configuration loading does not reject it, and add a loader that returns the merged privacy settings from a passed-in config mapping.
  - Add the run-scoped privacy output directory constant using the existing run-output-path helper, add the privacy block to the shipped configuration file, and create the shipped policy profile file with the three default profiles and one vendor profile described in the design.
  - The shipped defaults must be strict: with no transformation provider registered and a vendor profile approving public-safe content only, a fresh install blocks external disclosure for every label above public-safe.
  - Observable: loading the shipped configuration returns privacy settings with the documented defaults and raises no unknown-key error; the resolved output directory sits under the current run folder; the shipped policy file parses and a test asserts its default posture is blocking for every label above public-safe.
  - _Requirements: 1.3, 5.1, 7.5_

- [ ] 1.3 Change the model-provider configuration loader to describe the credential source instead of returning its value
  - Stop returning a bare credential string from the model-provider config loader; return a non-secret description of where the credential resolves from, covering the environment variable name and the configuration path, so the secret resolver has a source to consult.
  - Retain the existing credential key only for the ungoverned path, so behavior is unchanged when the privacy subsystem is disabled.
  - Observable: a regression test confirms the loader's other returned keys are byte-for-byte unchanged, that the credential description names both candidate sources without exposing a value, and that the existing model-client path still functions when privacy is disabled.
  - _Requirements: 2.1, 2.2_

- [ ] 2. Definition and observation layer: errors, records, audit trail

- [ ] 2.1 Implement the fail-closed error hierarchy
  - Define a root privacy error and a fail-closed error deriving from it that is the base for every condition that must block disclosure, covering classification unavailability, policy evaluation failure, absent applicable policy, transformation unavailability, transformation failure, secret unavailability, packet blocking, unapproved vendor profile, audit write failure, response scan violation, and undecided gateway state.
  - Require every fail-closed error to carry a named rationale. Because this module precedes the record models in the dependency order, the attribute is a plain string at runtime and is narrowed to the rationale vocabulary only under static type checking; the vocabulary itself belongs to the next task.
  - Observable: unit tests construct every error subclass, confirm each carries a non-empty rationale, and confirm the fail-closed base is distinguishable from the root so callers cannot catch a recoverable and a blocking error with one clause; this module imports nothing from the record models.
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6_
  - _Boundary: PrivacyErrors_

- [ ] 2.2 Implement the privacy record models and closed vocabularies
  - Define the closed vocabularies for sensitivity label, classification source, classification state, decision type, privacy mode, audit event kind, and rationale category.
  - Define the label strictness ordering as data covering the vocabulary exactly, plus the conservative default label, and ensure no construction path yields a label more permissive than that default without an explicit declaration or a confident detector result.
  - Define the frozen record types for classification, override, policy profile, vendor profile, disclosure decision, evidence packet, and audit record, each carrying only identifiers, labels, decisions, reasons, and timestamps, with evidence text confined to the packet payload and secret values excluded everywhere.
  - Observable: unit tests construct one instance of every record type, confirm immutability and value equality, enumerate every vocabulary member, confirm the strictness ordering is total and covers the vocabulary exactly, and confirm every rationale raised by the error hierarchy from the previous task is a member of the rationale vocabulary.
  - _Requirements: 1.2, 1.3, 3.1, 3.2, 5.6, 6.2, 6.3, 8.2, 10.2_
  - _Depends: 2.1_
  - _Boundary: PrivacyModels_

- [ ] 2.3 Implement the append-only write-through privacy audit trail
  - Provide an append method plus read-only projections over the recorded events; expose no update, delete, or truncate method.
  - Write and flush each record to the run-scoped append-only file before the append call returns, so a caller that appends before transmitting cannot transmit an unrecorded disclosure; raise the fail-closed audit write error on any input or output failure.
  - Stamp every record with the audit schema version, and record the artifact identifiers, policy profile identity and version, decision identity, packet identity, vendor profile identity, rationale, key version, responsible component, human-involvement flag, and event time.
  - Implement the restricted export as a pure projection over the recorded events that drops the declared-protected detail keys, emits no payload, secret, or transformed-away fragment, and carries the fixed anti-overclaiming disclaimer text as a header.
  - Define, as the single owner, both the disclaimer text constant and the prohibited-claim term list. The disclaimer states that institutional governance, provider agreements, review-board approval, and legal review remain required, and frames capabilities as safeguards, controls, and audit support. Later tasks consume both constants rather than defining their own copies.
  - Guard all appends with a single lock so concurrent per-document recording cannot interleave.
  - Observable: unit tests confirm the class exposes no mutating method beyond append, that a record is durable on disk when append returns, that a failing write raises the fail-closed error, that the restricted export contains no protected detail key and carries the disclaimer, and that a concurrent append test from multiple threads preserves every record.
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 11.4_
  - _Depends: 2.2_
  - _Boundary: PrivacyAuditTrail_

- [ ] 3. Core layer: credential access, labelling, policy, transformation dispatch, and carrier population

- [ ] 3.1 (P) Implement the managed secret access path
  - Implement resolution of a secret reference at the point of use against the credential source description added in the foundation phase, returning a handle rather than a bare value, with nothing bound at import time.
  - Make redaction a property of the handle type: every rendering, including representation, string conversion, formatted interpolation, and serialization of its non-secret description, yields a fixed redacted token, and the value is reachable only through an explicit reveal accessor.
  - Derive a non-secret key-version identifier so rotation is detectable and privacy-relevant events remain attributable without ever storing the value.
  - Raise the fail-closed secret-unavailable error naming the reference identity and the consulted source when the secret is absent, empty, or malformed, with no fallback source and no partial proceed.
  - Enforce the mode rule: a development-mode source resolves only while the subsystem is in development mode, and requesting one under production mode raises; the resolved mode travels on the handle for status reporting.
  - Observable: unit tests plus a property test over arbitrary secret values confirm no rendering of a handle contains the value as a substring; failure tests confirm no handle is returned for absent, empty, malformed, and wrong-mode cases; a rotation test confirms a changed value yields a changed key version.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 6.7, 9.4_
  - _Depends: 1.3, 2.3_
  - _Boundary: SecretResolver_

- [ ] 3.2 (P) Implement the classifier, the detector protocol, and the detector registry
  - Implement label assignment in the fixed resolution order of operator override, operator declaration, registered detectors in registration order, and the conservative default, recording which of those produced the label.
  - Define the detector protocol and load detectors by fully qualified class path from configuration, using the same mechanism as the existing pluggable text-processor hook; ship no detector implementation.
  - Treat a detector that raises, times out, or reports confidence below the configured threshold as producing a review-required state with a label no more permissive than the default, discarding the detector output and recording the failure.
  - Keep classification total with an empty detector registry so no run fails for want of a detector, and map heightened-sensitivity declarations to the label that ranks above the protected-health-information label in the strictness ordering, so stricter policy treatment follows from the ordering rather than from special-casing.
  - Implement operator override so it applies the new label, records the prior label, the reason, the operator identity, and that a human intervened, and retains the prior record rather than erasing it.
  - Attach labels to caller-supplied artifact identifiers only; define no identity scheme.
  - Observable: unit tests confirm all four assignment sources, that raising, slow, and low-confidence detectors each yield review-required, that an empty registry still classifies, that an override records the prior label with the human-involvement flag set, and that no branch returns the most permissive label without a declaration or a confident detector.
  - _Requirements: 1.1, 3.2, 3.3, 3.4, 3.6, 3.7, 9.1, 9.5_
  - _Depends: 2.3_
  - _Boundary: Classifier_

- [ ] 3.3 (P) Implement the policy profile registry and structural validation
  - Load the shipped policy profile file and validate it against a closed structural schema in code, exposing profile lookup by label and vendor profile lookup by identifier.
  - Treat structural defects as disqualifying for the whole file: an unknown label, an unknown disclosure value, a missing profile version, a limited-use profile naming no transformation, a duplicate profile identifier, or two profiles matching one label each render every profile unusable while reporting the specific defect.
  - Return a loaded result rather than raising on a malformed file, so the decision layer blocks and the run continues.
  - Check profile identifiers and descriptions at load time against the prohibited-claim term list owned by the audit trail module, so a profile cannot name itself compliant, certified, or approved. Consume that constant; do not define a second list.
  - Observable: unit tests confirm a well-formed file yields the expected profile and vendor lookups, each of the six defect cases yields an empty profile set with a named defect and no exception, and a profile description asserting compliance is rejected at load time.
  - _Requirements: 5.1, 5.5, 5.6, 11.3, 11.6_
  - _Depends: 2.3_
  - _Boundary: PolicyRegistry_

- [ ] 3.4 (P) Implement the transformation provider protocol, registry, and dispatch
  - Define the provider protocol and its result type, carrying the transformed payload, a completeness flag, the preserved evidence identifiers, and a non-content detail field.
  - Load providers by fully qualified class path from configuration, keyed by transformation identifier; ship no transformation implementation of any kind.
  - Make dispatch fail closed on three distinct conditions with distinct rationales: no provider registered for the named transformation, the provider raised, and the provider returned a result it did not mark complete. None of the three may return a payload.
  - Observable: unit tests with fake providers confirm the success path returns a complete result, and that each of the three failure conditions raises the documented fail-closed error and returns no payload; a registry-empty test confirms the unavailable path is reached rather than the raw payload being passed through.
  - _Requirements: 5.3, 5.8, 6.5, 9.3_
  - _Depends: 2.2_
  - _Boundary: TransformationRegistry_

- [ ] 3.5 (P) Implement the provenance privacy carrier populator
  - Convert a classification record and an optional disclosure decision into the mapping shape the provenance carrier accepts, supplying the label and decision as opaque strings and identifying this subsystem as the supplying component together with the supply time.
  - Use the provenance-defined carrier as the only attachment mechanism; define no parallel privacy field on any provenance record.
  - On a reported non-conforming carrier, append a carrier-rejected audit record and mark the artifact review-required, with no retry using a coerced value.
  - Keep this the only module in the privacy package that imports the provenance package, and make its tests skip cleanly while the provenance package does not yet exist so the rest of the package remains buildable ahead of its upstream.
  - Consume the provenance evidence node identity form when parsing artifact identifiers; construct no competing identity scheme.
  - Observable: unit tests against a stand-in carrier confirm the label and decision are supplied byte-identically, the supplying component and time are set, a rejection yields an audit record plus a review-required artifact, and an artifact with no provenance record is still classified, decided, and audited; a boundary test confirms this is the only privacy module importing the provenance package.
  - _Requirements: 3.5, 4.1, 4.2, 4.3, 4.4, 4.5_
  - _Depends: 2.3_
  - _Boundary: CarrierPopulator_

- [ ] 4. Decision, packaging, egress, and status

- [ ] 4.1 Implement the disclosure gate as the sole decision authority
  - Implement evaluation as a pure function of the label, the loaded policy file, the local-only flag, the enable flag, and transformation registry membership, plus a supplied timestamp, producing a content-addressed decision identity over those inputs so repeated evaluation of identical inputs yields an identical decision.
  - Implement the fixed decision precedence: privacy disabled yields raw disclosure with the privacy-disabled rationale, so turning the subsystem off leaves pre-existing pipeline behavior intact while the run is reported as ungoverned; local-only mode blocks; a defective policy file blocks; no matching profile yields review-required with no transmission; a blocking profile blocks; a limited-use profile with no registered provider blocks; a limited-use profile with a provider allows transformed disclosure; and an allowing profile allows raw disclosure.
  - Make the disabled row reachable only from explicit operator configuration and never from a failure, so no error path can produce a permitting decision.
  - Own the local-only decision here and nowhere else, so exactly one component authorizes or refuses external transmission.
  - Convert any exception raised during evaluation into a block with the undecided rationale, so the gate is total and can never return without a decision.
  - Append every decision to the audit trail before returning it, and expose the external-permission check as a lookup against a two-member frozen set rather than a chain of conditions.
  - Make this the only place in the repository that constructs a disclosure decision.
  - Observable: a table-driven test covers every precedence row with its documented decision and rationale, including the disabled row yielding raw disclosure; a property test confirms determinism of the decision identity across repeated evaluation; a boundary test confirms the decision record is constructed in no other module; and a test confirms the permission check is true only for the two permitting decision types.
  - _Requirements: 1.2, 1.4, 5.1, 5.2, 5.3, 5.4, 5.6, 5.7, 5.8, 9.2, 9.6_
  - _Depends: 2.3, 3.3, 3.4_
  - _Boundary: DisclosureGate_

- [ ] 4.2 Implement the packet builder with govern-once semantics
  - Build exactly one evidence packet per document from the assembled evidence package string, passing the payload through unchanged for a raw-disclosure decision and dispatching to the named provider for a transformed-disclosure decision.
  - Preserve the contributing evidence identifiers unchanged in the provenance identity form regardless of what the transformation did to the text, and block the packet when the transformation result fails to preserve them.
  - Derive a reproducible packet identity over the payload and the decision identity so the packet resolves back to both the content actually sent and the decision that authorized it.
  - Raise the fail-closed packet-blocked error carrying its rationale on every block path so the caller reports that document as blocked and continues with other documents.
  - Accept only strings and identifier sequences as input and never receive a secret resolver, so no credential can structurally reach a payload.
  - Keep the builder deliberately shape-agnostic — a payload string plus identifier sequences, with no knowledge of evidence-package structure — so that it is the sole construction path for every model-visible, document-derived string regardless of shape or originating component. This spec's paper evidence package is the first such payload, not the only possible one; a later capability adding a different model-visible payload must build it here rather than assembling it independently. State govern-once as a **per-payload-instance** rule: build is called at most once per distinct model-visible payload instance, and any payload carried by more than one call must be byte-identical across those calls. It is not a per-document limit of one — a later capability may legitimately produce many payload instances for one document, each carried by a single call — and what the rule forbids is rebuilding a payload that more than one call carries, because that is what breaks prompt-cache stability.
  - Observable: unit tests confirm identifier preservation through a fake transformation, a reproducible packet identity for identical inputs, a block when identifiers are lost, a block when the transformation fails, that repeated builds for the same inputs produce a byte-identical payload, and that building several payload instances for one document is permitted while the single instance this spec builds is reused byte-identically across every call that carries it; a shape-agnosticism test confirms a fabricated payload of an unrelated shape builds through the same entry point with no builder change.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8_
  - _Depends: 4.1_
  - _Boundary: PacketBuilder_

- [ ] 4.3 Implement the external model gateway call path
  - Define the model transport protocol and hold an injected transport so the gateway never imports the model client package. Any agent client added by a later capability is adapted to this same protocol in the wiring module and reaches the provider only from here.
  - Define the call-kind vocabulary as a named literal seeded with the three kinds the existing extraction path issues, and document it in the module as an **open extension point**: later capabilities widen that literal in place in this module when they add a call kind, each addition firing a revalidation. There must be no untyped call-kind parameter and no kind-free send path, so the vocabulary is closed at any instant and open across capabilities.
  - Accept an evidence packet on the send path, never a bare payload string, and provide no overload, parameter, or helper that takes raw text, so an ungoverned document-derived string cannot be routed through the chokepoint.
  - Fix the send entry point's full parameter list so downstream callers have one unambiguous contract: the call kind, the evidence packet, the authorizing decision, the model in effect, a non-payload request mapping, an optional caller-owned concurrency semaphore, and the supplied timestamp. The request mapping carries every piece of request material that is not the governed payload — the instruction or user message text for the call, the unit or chunk index, the field list, prior-chunk results, and provider parameters — and the gateway forwards it to the transport unchanged, neither reading, rewriting, augmenting, nor validating it. The split is the contract: every model-visible, document-derived string is in the packet payload and everything else is in the request mapping.
  - Keep concurrency gating inside the transport while giving callers a channel to reach it: forward the optional semaphore to the transport verbatim and never acquire, release, create, or default it in the gateway, which holds no concurrency state of its own. Because the audit append precedes transmission, it also precedes semaphore acquisition, so audit durability never waits on concurrency admission.
  - Implement the fixed call sequence: defensive local-only re-check, permission check against the supplied decision, vendor profile approval, credential handle resolution, append and flush of the disclosure audit record, then transmission.
  - Construct no disclosure decision anywhere in this component: consume the gate's decision, and express every gateway-side refusal as a fail-closed error plus a blocked audit record, with the blocked-call accessor returning audit records rather than decisions.
  - Implement vendor approval by comparing the content label against the profile's maximum sensitivity in the strictness ordering and confirming the model in effect is listed, blocking and naming the profile when it is not; this is a gateway-time check because the model in effect is unknown at decision time.
  - Raise the undecided error and block if a permitting decision ever arrives while local-only is set, emitting no second decision record.
  - Add nothing to the request payload: pass the packet payload through and record the packet identity, policy profile, vendor profile, and decision exclusively in the audit trail. Convert any unclassifiable internal failure into a block, never a transmission.
  - Observable: unit tests with a fake transport confirm zero transport invocations on each block path, that the audit record exists on disk before the transport is invoked, that an over-sensitive label against a public-safe-only vendor profile blocks with the profile named, that a permitting decision under local-only is refused, that the blocked-call accessor returns audit records, that the payload the transport receives is byte-identical to the packet payload, that the request mapping and the supplied semaphore reach the transport unchanged and unacquired by the gateway, and that no send entry point accepts a bare string payload.
  - _Requirements: 2.2, 6.6, 6.8, 7.1, 7.2, 7.3, 7.4, 7.5, 7.7, 7.8, 7.9_
  - _Depends: 3.1, 4.2_
  - _Boundary: LLMGateway_

- [ ] 4.4 Implement the response scanner protocol, registry, and post-response scanning point
  - Define the response scanner protocol and its result type carrying the scanner identity, a violation flag, a rationale, and a non-content detail field; ship no scanner implementation.
  - Load scanners by fully qualified class path from configuration and invoke every registered scanner on the returned text at a single post-response scanning point inside the gateway's call path.
  - Block persistence when any scanner reports a violation by raising the fail-closed scan-violation error and recording a blocked audit record, and record the scanning point as an audit event even when the registry is empty so the control is visible either way.
  - Observable: unit tests confirm a violating scanner prevents the response from being returned for persistence and produces a blocked audit record, that an empty registry passes the response through while still recording the scanning event, and that a raising scanner is treated as a violation rather than being ignored.
  - _Requirements: 7.6, 9.3_
  - _Depends: 4.3_
  - _Boundary: LLMGateway_

- [ ] 4.5 (P) Implement the privacy status projection
  - Project document-level status carrying the label, state, assigning component, and assignment source; evidence-level status carrying the decision, rationale, and applied transformation; output-level status derived from an explicit mapping of each produced output identifier to its contributing artifact identifiers, reporting the strictest contributing label and the rationale category blocking export; and a run-level status carrying counts by label and by decision type.
  - Depend on the record models and the audit trail only; do not import the gateway, so gateway-side blocks reach the projection as audit records like any other event.
  - Accept only records and identifier mappings as input, never a payload or a text argument, so no evidence text, secret, or transformed-away fragment can structurally reach the projection.
  - Report the run as ungoverned with the privacy-disabled reason when the subsystem is disabled, rather than reporting it as governed.
  - Embed the disclaimer text in the run-level status, and write the status as data to the run-scoped privacy directory without rendering or formatting a user interface.
  - Observable: unit tests confirm the projected artifact contains no substring of a fabricated source text, that all four levels are populated, that an output's reported label is the strictest of its contributors, that the disabled run reports ungoverned with its reason, that the run-level counts match the input records, and that the disclaimer is present.
  - _Requirements: 1.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 11.4_
  - _Depends: 2.3_
  - _Boundary: PrivacyStatusProjector_

- [ ] 5. Integration: model client, wiring, and pipeline routing

- [ ] 5.1 Replace the import-time credential binding in the model client with a lazy client and an injected-client parameter
  - Replace the module-level client construction with a lazily constructed accessor that builds the client on first use and caches it, so importing the module no longer requires a credential and a missing credential surfaces as a named operational error at first use rather than an import crash.
  - Add an optional client keyword to the cache-warmup and chunk-extraction entry points; when supplied that client is used, and when omitted behavior is exactly as today. This task changes no other parameter, default, or return type — but it does not freeze those entry points against later capabilities, which add further optional parameters of their own; the standing constraint they inherit is that every addition is optional with a default under which the default-path request payload stays byte-identical. This task makes no change to the prompt-building module; in particular the shared prompt prefix's source and output are unchanged and the default-path request payload stays byte-identical.
  - Update the existing agents tests that monkeypatch the module-level client to patch the accessor instead; confine this ripple to the agents test module.
  - Observable: the agents test module passes unchanged in intent, importing the model client module with no credential present succeeds, and a test confirms an injected client is used when supplied and the cached one otherwise.
  - _Requirements: 2.6, 7.1_
  - _Boundary: ApiClientIntegration_

- [ ] 5.2 Implement the pipeline-side privacy wiring
  - Build the model transport by constructing a client from the revealed credential and adapting the cache-warmup and chunk-extraction entry points to the transport protocol; this is the only place in the repository where the credential is revealed.
  - Assemble the gateway from the resolver, the audit trail, the vendor profile, the scanner registry, and the local-only flag, resolving the credential handle per gateway construction rather than at import.
  - Contain no policy logic, no classification, and no decision in this module.
  - Adapt any future agent client the same way: this module is where an added client is wrapped in the transport protocol, so the client itself never imports the existing OpenAI client module and never calls its retry helper.
  - Add a boundary test asserting the credential reveal accessor is called in this module only. Leave the allowed-importer set unchanged for now: it still names the document processor's import sites, which the routing task removes.
  - Observable: a test builds a gateway from a fake resolver and confirms the transport adapts both call kinds; the reveal-call-site test passes; both parts of the agent-client test remain green because the allowed set has not yet been narrowed.
  - _Requirements: 2.1, 2.6, 7.1_
  - _Depends: 4.4, 5.1_
  - _Boundary: PrivacyWiring_

- [ ] 5.3 Wire the privacy runtime lifecycle into the run orchestrator
  - Construct the audit trail, secret resolver, policy registry, classifier, gate, packet builder, and gateway once per run from the resolved configuration and pass them explicitly down the call chain, with no global and no module-level singleton.
  - Classify each document on ingest, before any evidence derived from it is prepared, and supply the resulting label and any decision to the provenance carrier where a provenance record exists.
  - Collect the output-to-artifact mapping the status projection requires, and write the run-level privacy status artifact at the end of the run, including the ungoverned reason when the subsystem is disabled.
  - Keep the subsystem free of network and model-provider requirements for classification, policy evaluation, audit, and status, so those stages work with no credential present.
  - Observable: an orchestrator test over two fabricated documents confirms both are classified before evidence preparation, the status artifact is written with both documents and their outputs present, and the run completes with no credential configured when every document is blocked.
  - _Requirements: 1.1, 1.5, 1.6, 3.5, 10.7_
  - _Depends: 3.2, 3.5, 4.5, 5.2_
  - _Boundary: PipelineIntegration_

- [ ] 5.4 Route the document extraction path through the packet builder and the gateway
  - Call the packet builder exactly once per document, immediately after the paper evidence package is assembled and before any call is issued, and assign the resulting payload to every per-chunk source entry so the existing byte-identity property is preserved rather than a new one introduced.
  - Route the cache-warmup, extraction-chunk, and synthesis calls through the gateway and remove all three existing lazy imports of the model client from the document processor.
  - Narrow the allowed-importer set to the wiring module alone, now that the document processor's import sites are gone; part two of the agent-client test is unaffected and continues to cover any client added later.
  - Treat a packet block or a gateway block as a per-document privacy block recorded in the run manifest, allowing the remaining documents to process and be reported individually.
  - Add no privacy value to any prompt material, and leave the shared prompt prefix's source and output unchanged so the default-path request payload stays byte-identical.
  - Observable: a document-processing test with a fake transport confirms exactly one packet build per document, three call kinds issued with the same payload object, a blocked document recorded in the manifest as privacy-blocked while a second document completes; the agent-client test is green with the wiring module as the sole permitted importer of the OpenAI client module.
  - _Requirements: 6.1, 6.6, 7.1, 7.2, 9.7_
  - _Depends: 5.3_
  - _Boundary: PipelineIntegration_

- [ ] 6. Validation: cache stability, fail-closed enforcement, anti-overclaiming, and end-to-end

- [ ] 6.1 Add the prompt-cache stability regression test for the governed path
  - Assert that, for one governed document, the evidence string reaching the shared prompt prefix is byte-identical across the cache-warmup, extraction-chunk, and synthesis calls.
  - Assert that for a raw-disclosure decision the request payload the transport receives is byte-identical to the payload the ungoverned path produces for the same document, so the gateway contributes zero bytes.
  - Assert prefix stability rather than file immutability: pin a digest of the shared-prefix function's own source text and a digest of its output for one fixed input, and assert both are unchanged, together with the default-path request payload being byte-identical. Do not assert that the prompt-building module as a whole is unmodified — an added optional parameter with a null default elsewhere in that module, which produces byte-identical output on the default path, must leave this test green.
  - Assert that no policy identifier, packet identifier, decision timestamp, or gateway metadata appears anywhere in the assembled prompt material.
  - Observable: the regression test fails if governance is moved from once-per-document to once-per-call, or if the shared prefix's source or output changes; it passes on the delivered implementation and would still pass if a later capability added a null-defaulted optional parameter to another prompt builder in the same module.
  - _Requirements: 7.7_
  - _Depends: 5.4_
  - _Boundary: PipelineIntegration_

- [ ] 6.2 (P) Add the cross-cutting fail-closed enforcement tests
  - Assert by source analysis that no fail-closed error is caught anywhere in the source tree and followed by a transport invocation.
  - Assert that no configuration key, environment variable, or code path permits disclosure when a decision could not be reached, by exercising every undecided, defective, and unavailable path and confirming each yields a blocking or review-required decision; confirm the one permitting outcome not derived from a policy profile is reachable only from the explicit disabled configuration.
  - Assert that the decision state transitions contain no edge from an undecided, no-policy, or awaiting-transformation state to a permitting state.
  - Assert that a block on one artifact leaves other artifacts in the run fully processed and individually reported.
  - Observable: the enforcement suite is green, and deliberately introducing a permissive fallback in any of the covered paths makes it fail.
  - _Requirements: 9.2, 9.5, 9.6, 9.7_
  - _Depends: 5.4_
  - _Boundary: PrivacyErrors, DisclosureGate_

- [ ] 6.3 (P) Add the anti-overclaiming scan and fixture-hygiene tests
  - Scan exactly the strings this feature ships — the privacy package source, the policy profile file, the privacy block of the shipped configuration, and the exported audit and status artifacts — for claims of regulatory compliance, certification, or external approval, consuming the prohibited-claim term list owned by the audit trail module and permitting those terms only inside the disclaimer's explicitly negating context.
  - Assert that every exported audit and status artifact carries the disclaimer text distinguishing technical controls from legal compliance conclusions, and that no artifact field or value asserts a document, run, or export is compliant, certified, or approved.
  - Assert that every privacy test fixture is synthetic: identifiers fabricated and text lorem, with no fixture containing a value resembling a real identifier or clinical record.
  - Observable: the scan is green on the delivered implementation and fails when a compliance claim is introduced into any covered file; the fixture-hygiene test enumerates every privacy fixture.
  - _Requirements: 11.1, 11.2, 11.3, 11.5, 11.6_
  - _Depends: 4.5_
  - _Boundary: AntiOverclaimingCheck_

- [ ] 6.4 Add the end-to-end governed-run and audit-ordering integration tests
  - Exercise a full governed run over a fabricated multi-document evidence bundle with a fake transport: confirm one packet per document, one audit record per event, the carrier supplied for every classified artifact, and the status artifact written with correct run-level counts.
  - Confirm the audit write-through ordering by asserting a transport that records invocation order never observes a call whose disclosure record is absent from disk, and that a failing audit write blocks the call entirely.
  - Exercise local-only mode across a whole run and confirm every external call is blocked as a policy block while a complete status artifact is still produced.
  - Exercise the disabled configuration and confirm the run completes with calls transmitted as before and the status reporting the run as ungoverned.
  - Observable: the integration suite is green, and the audit trail on disk after the run reconstructs which document text was and was not transmitted from the recorded packet identities.
  - _Requirements: 4.1, 7.3, 7.5, 7.8, 8.1, 8.7, 10.7_
  - _Depends: 6.1_
  - _Boundary: PipelineIntegration_

- [ ] 6.5 (P) Add the payload sole-construction boundary test
  - Assert by source analysis that the only string reaching a model transport's send entry point is an evidence packet payload produced by the packet builder: the gateway's send signature accepts an evidence packet and no bare payload string, and no module in the source tree invokes a transport's send outside the gateway module.
  - Record in the test module docstring that this is the counterpart to the agent-client boundary test: that test proves every external call passes through the gateway, this one proves every string the gateway carries was governed. Without both, the coverage claim is a topology claim rather than a safety property.
  - Assert that the packet builder entry point is shape-agnostic, so a later capability introducing a differently shaped model-visible payload can satisfy this rule without a builder change; a fabricated payload of an unrelated shape must build and transmit through the same path.
  - Observable: the boundary test is green on the delivered implementation, and fails when a fabricated caller passes a raw document-derived string to the transport or adds a bare-string send path to the gateway.
  - _Requirements: 6.8, 7.1_
  - _Depends: 5.4_
  - _Boundary: PacketBuilder, LLMGateway_
