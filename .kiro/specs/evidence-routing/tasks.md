# Implementation Plan

- [ ] 1. Foundation: configuration, shared types, and the routing agent schema

- [ ] 1.1 Add the routing configuration section, register it, and implement strict resolution
  - Add an `evidence_routing` section to the run configuration file covering routing granularity, hint sources and synonyms, minimum candidate score and candidate cap, locator and counterfactual model identities and retry limits, counterfactual gating threshold and per-document call bound, the issue-to-destination mapping, default criticality, maximum snippet length, extraction pack token cap, artifact directory, and the enable flag
  - Register the new top-level key so the local configuration loader accepts it instead of rejecting the run
  - Resolve the section into an immutable settings object, applying a documented default for every omitted value and retaining the fully resolved mapping for later reporting
  - Reject an invalid value with an error naming the setting and the offending value before any routing work begins
  - Observable: loading a configuration file with the new section returns a fully populated settings object whose resolved mapping lists every defaulted value; loading one with an out-of-range threshold raises an error naming that setting; loading one with an unknown sibling top-level key still raises the existing unknown-key error
  - _Requirements: 13.1, 13.2, 13.3_

- [ ] 1.2 Define the routing data model and vocabularies
  - Define frozen records for index entries and document indices, field hints, retrieval candidates and outcomes, routing units and excluded fields, route objects, route issues and verdicts, counterfactual outcomes, evidence provenance, adjudicated routes, pack snippets and extraction packs, the call ledger, and the aggregate routing result
  - Define the closed vocabularies for evidence kind, content source, section-path origin, route kind, route confidence, candidate source, parser-risk state, escalation destination, route status, discard reason, and routing granularity
  - Store evidence references as the bare pipeline-assigned identifier only; no record may construct or store a scoped provenance identifier
  - Carry the resolved routing confidence on the adjudicated route record, so the downstream dual-extraction gate can read confidence from the adjudicated route rather than from the discarded raw route
  - Expose a derived priority mapping from the aggregate result for downstream evidence selection
  - Observable: a test constructs one instance of every record, asserts all are immutable and all collection fields are tuples, asserts no record field stores a scoped identifier, and asserts the adjudicated route carries a confidence drawn from the closed confidence vocabulary
  - _Requirements: 1.2, 1.5, 2.3, 2.6, 3.5, 3.6, 4.3, 4.4, 6.2, 8.5, 9.2, 9.8, 10.1, 11.3_
  - _Boundary: RoutingModels_

- [ ] 1.3 Create the routing agent schema file and its single-owner validator
  - Author a schema artifact holding the locator system prompt, the counterfactual system prompt, the shared routing policies, and the response schemas for both agents
  - Define the route response schema so that it requires one route per requested field with primary and backup identifiers, pages, section names, confidence, risk flags, rationale, and route kind, and so that it forbids any value-bearing property
  - Define the counterfactual response schema so that it accepts either alternative locations or an explicit confirmation, and likewise forbids any value-bearing property
  - Implement a validator that loads and checks the artifact once at construction, exposes the two system prompts and the policies, and converts a validated payload into the corresponding routing records; export it as the sole singleton reader of that artifact
  - Observable: a well-formed route payload validates and converts to route records; a payload carrying an extracted value is rejected with an error naming the offending property; a second module reading the schema artifact directly fails a guard test
  - _Requirements: 4.2, 4.3, 5.1, 8.4_
  - _Boundary: RoutingAgentSchemaValidator_

- [ ] 2. Deterministic retrieval and planning

- [ ] 2.1 Build the four document indices as a projection over the evidence bundle
  - Project the existing evidence items into section, paragraph, table, and caption indices, adopting each item's pipeline-assigned identifier verbatim and never issuing a competing one
  - Record for each entry its kind, page, section path, whether that section path came from an explicit heading or was inherited, its document-order position, every index it belongs to, the parser that supplied its content, and its length
  - Synthesize one section entry per distinct section path with the first page on which it appears, and build the document outline from those entries
  - Take table content from the structural block parser when the scholarly parser yields no rows for that table or the page carries a table-detection disagreement, and record the substitution on the entry
  - Return an index-unavailable state naming the missing structure when no usable structure exists, rather than raising
  - Observable: building indices twice from the same fixture bundle yields equal results; a fixture with an empty scholarly table and a structural candidate produces an entry marked as sourced from the structural parser; an empty bundle yields an unavailable reason and empty indices
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_
  - _Boundary: DocumentIndexBuilder_

- [ ] 2.2 (P) Derive field-specific retrieval hints from the extraction schema
  - Derive per-field hint terms from the configured hint sources, normalized and tokenized through the project's existing text processing, then deduplicated and ordered stably
  - Merge configured synonyms and expected evidence locations for the field or its domain group
  - Derive the expected evidence kinds from the field's declared value format, adding table kinds for numeric and cardinality formats and caption kinds for figure-oriented formats
  - Observable: a fixture extraction map produces one hint record per field with sorted, deduplicated terms and non-empty expected kinds, identical across repeated calls
  - _Requirements: 2.1_
  - _Boundary: FieldHintBuilder_

- [ ] 2.3 (P) Implement the parser-risk adapter over published page signals
  - Convert the agreement publication into the page-indexed view this feature consumes: the upstream feature publishes a *report container* holding one entry per analyzed parser pair, each entry carrying its own nested list of per-page agreement records, with **no page list at the top level** and no cross-pair rollup, and no other feature performs this conversion, so it belongs here
  - Read the page records from every pair entry's nested page list, not from any top-level key, and group them by page index; treat a container missing its pair list, or one whose pair entries are unusable, as malformed rather than as an empty successful read, so a shape change upstream can never degrade silently into "risk unknown everywhere"
  - Merge across **all** pair entries — not only the designated primary pair — so that combining evidence can only make a page look less safe: risky and audit-recommended combine by logical or, the skip signal combines by logical and, failing metrics and unavailable signals combine as a sorted union, and each contributing pair's thresholds are retained under that pair's identity
  - Skip a pair entry whose own status is not computed, since a computed container may legitimately hold an undefined pair
  - Carry the published primary-pair designation, its basis, and the available pair list onto the view for audit only, and never use them to select or filter which pair entries are read
  - Treat an absent publication, a publication reporting that the feature is disabled, one reporting an undefined result, and a malformed one all as an empty view answering unknown for every page and safe-to-skip for none, retaining which of those four cases applied so an operator can distinguish "no risk found" from "risk never measured"
  - Skip a record carrying a missing or non-integer page index with a warning rather than failing the document
  - Expose the resolved risk state for a page, the reasons a page was marked risky, whether numeric or table disagreement is present, and the resolved state for a whole route
  - Resolve a page absent from the mapping, and any evidence entry carrying no page, as unknown; never resolve an absent signal as safe
  - Resolve a route to the most severe state across its pages so an unknown page can never make a route appear safe to skip
  - Compute nothing: read only the published flags, reasons, and thresholds, never mutate the supplied publication, and never retain a reference into it
  - Observable: fixtures are built to the published container shape (pair entries with nested page lists); an absent, disabled, undefined, and malformed publication each yield an all-unknown view with the matching recorded reason and no safe-to-skip page; a computed publication whose two pair entries each carry a record for the same page, one risky and one not, resolves that page risky and lists both pairs as contributing; a legacy-shaped block carrying a top-level page list and no pair list is recorded as malformed rather than read; a route spanning a risky page and a skip-signalled page resolves to risky; a route containing a page-less entry never resolves to safe-to-skip
  - _Requirements: 7.1, 7.6, 7.7_
  - _Boundary: ParserRiskView_

- [ ] 2.4 (P) Plan routing units and record exclusions
  - Plan one routing unit per domain group by default, and one unit per field when per-field granularity is configured, recording the effective granularity alongside the plan
  - Exclude fields the pipeline pre-fills without a model call and record each exclusion with its reason
  - Provide a refinement planner that produces one single-field unit per flagged field
  - Observable: a fixture extraction map with two pre-filled fields yields units covering every remaining field exactly once plus two recorded exclusions; refinement planning of three flagged fields yields three single-field units
  - _Requirements: 3.1, 3.4, 3.5, 3.6_
  - _Boundary: RoutingUnitPlanner_

- [ ] 2.5 Retrieve deterministic ranked local candidates per field
  - Score each index entry against a field using its existing section score plus fixed weights for matched hint terms, expected kind, and expected section, with weights held as module constants rather than configuration
  - Order candidates by descending score with the stable identifier as tie-break, matching the ordering the existing evidence package builder already uses
  - Cap the candidate list at the configured maximum, exclude entries below the configured minimum score, and record a no-candidate reason for a field left with none
  - Record for every candidate its score and which hint terms contributed to it, and retain the full list so later stages can consider candidates the locator ignores
  - Observable: retrieval over a fixture bundle and extraction map is byte-identical across repeated calls; a field whose terms match nothing yields an outcome with a no-candidate reason and an empty candidate tuple
  - _Requirements: 2.2, 2.3, 2.5, 2.6, 2.7_
  - _Boundary: LocalRetriever_
  - _Depends: 2.1, 2.2_

- [ ] 3. Locator agent stage

- [ ] 3.1 Build the routing prompt builders with their own stable prefixes
  - Build one document-level index package per document, serializing the outline, index entries, and per-field hints deterministically
  - Define a stable locator prefix and a stable counterfactual prefix, each wrapping only fixed instruction text and that one package, with no unit identifier, field list, timestamp, or run identifier inside either
  - Place the routing unit's field definitions and hints, or the challenged route with its field definition, candidates, and selected snippets, strictly after the prefix
  - Provide a repair message builder that names the specific validation failure after the prefix
  - Observable: for one index package, every message produced by every builder begins with identical prefix bytes; a guard test asserts this module never references the existing extraction shared prefix
  - _Requirements: 4.1, 4.6, 8.3, 12.1, 12.2, 12.3_
  - _Boundary: routing_prompts_

- [ ] 3.2 Implement the routing provider client on the governed egress path
  - Issue every locator and counterfactual request through the privacy subsystem's gateway, using a model transport received by injection; inherit request construction, the concurrency gate, the retry ladder, and backoff from that transport rather than reimplementing any of them
  - Match the gateway send contract exactly: no function in this module takes a concurrency-gate parameter, because the gate lives inside the injected transport and the gateway's send call exposes none; all non-payload request material travels in the request mapping the gateway already accepts
  - Do not import the existing extraction provider client and do not call its retry helper: the gateway sits in front of that client, so reaching it directly would bypass the disclosure decision, the evidence packet, the audit trail, vendor approval, and response scanning
  - Take an already-built evidence packet and its disclosure decision from the caller and transmit the packet payload unchanged, adding no bytes of its own
  - Issue routing calls under their own call-kind labels rather than reusing an extraction call kind, since vendor approval and audit records key off that label
  - Return the raw response text and leave all validation to the caller
  - Record telemetry for both new stages so their token cost and prefix stability are visible in the existing summaries
  - Import nothing from the pipeline, quality-control, or extractor packages, and nothing from the privacy package either — not at module level and not under a type-checking guard, since the privacy feature forbids the agents package importing it and its check does not exempt type-checking-only imports; name every privacy type as a quoted forward-reference annotation on an injected parameter instead
  - Observable: a fake transport records exactly one gateway send per call and zero direct transport invocations; a gateway block yields no transmission and an escalation rather than a raw send; a dependency guard test confirms the module imports neither the extraction provider client nor the privacy package, and nothing outside the agents, utils, and standard-library surface; no routing agent function declares a concurrency-gate parameter
  - _Requirements: 4.7, 12.6_
  - _Boundary: routing_client_
  - _Depends: 3.1_

- [ ] 3.3 Implement routing artifact persistence, reuse, and invalidation
  - Persist raw agent requests and responses unconditionally, independent of log level, each recording the model identity and the identifiers of the routes derived from it
  - Persist routes, verdicts, counterfactual outcomes, packs, discards, and the aggregate result as plain queryable data using the project's atomic write helper
  - Key every artifact by document identity, document fingerprint, and extraction schema fingerprint, and reuse recorded artifacts on resume only when the key matches
  - Record a persistence failure and continue routing rather than discarding routes; discard and recompute a corrupt artifact with a warning
  - Observable: a routing run writes raw files plus the artifact set under the keyed directory; re-running with an unchanged key loads them and issues no provider call; changing the extraction schema fingerprint discards them
  - _Requirements: 5.5, 5.6, 5.7, 8.7, 11.5, 13.7, 13.8_
  - _Boundary: RoutingArtifactStore_

- [ ] 3.4 Implement the locator stage with validation, repair, and escalation
  - Build the document-level index package once per document and construct it through the privacy subsystem's packet builder, transmitting the resulting governed payload rather than the raw serialization; build each routing unit's snippet block as its own governed packet citing the identifiers it discloses. This honours the privacy feature's govern-once rule as stated — the builder is called at most once per distinct model-visible payload instance, and any payload carried by more than one call is byte-identical across those calls — so the one index-package instance is reused byte-identically across every routing call while each unit and challenged-route block is a separate instance carried by exactly one call
  - Treat a blocked document package as a document-level routing failure and a blocked unit packet as a per-field failure for that unit's fields; never retry either as an ungoverned send
  - Request routes for a routing unit, validate the response against the route schema before any route is used, and require exactly one route per field in the requested unit
  - Retry with a targeted repair instruction naming the validation failure up to the configured limit, then escalate the unit to the configured destination and record which escalation was applied and why
  - Reject any response carrying an extracted value as a contract violation rather than treating it as a route
  - Verify every returned identifier against the document indices, dropping an unknown identifier individually with a recorded issue while retaining the rest of the route
  - Accept absence-verification routes that point at the locations where absence can be checked
  - Persist the raw request and response for every attempt, and convert provider failures into the escalation path instead of raising
  - Observable: a mocked first-invalid, second-valid exchange produces usable routes and a repair message naming the failure; an always-invalid exchange produces no routes and the configured escalation destination; a response citing one unknown identifier yields a surviving route plus one recorded invalid-identifier issue; every string reaching the gateway in these runs is a packet payload, and the document package is built exactly once
  - _Requirements: 3.2, 3.3, 4.2, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4_
  - _Boundary: LocatorStage_
  - _Depends: 1.3, 2.5, 3.2, 3.3_

- [ ] 4. Deterministic route judgement

- [ ] 4.1 Implement route quality control
  - Verify that every field expected to be routed has exactly one route, and detect missing routes, duplicate coverage, invalid field identifiers, invalid evidence identifiers, empty primary evidence, and missing confidence as named issues
  - Detect routes whose evidence points only at references, bibliography, acknowledgements, funding, author or affiliation sections, or at content removed during cleaning, reading the non-evidential vocabulary from the existing section-score table so the two cannot drift
  - Verify that every critical field has backup evidence or a scheduled counterfactual review
  - Evaluate plausibility against the field's declared value format and expected locations, flagging a route that cannot contain a value of that shape
  - Attach the route's resolved parser-risk state and the reasons it was marked risky, and mark a critical field routed to a risky page as requiring stricter downstream handling
  - Resolve an escalation destination from the configured issue mapping, taking the most severe destination among a route's issues, and record a pass or fail status with the full issue list for every route
  - Observable: a targeted fixture produces each issue code exactly once; a route carrying two issues resolves to the more severe destination; a critical route on a risky page carries the stricter-handling marking; repeated evaluation of the same input yields equal verdicts
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 7.1, 7.2, 7.6_
  - _Boundary: RouteQualityControl_
  - _Depends: 2.3, 3.4_

- [ ] 4.2 Implement the gated counterfactual locator stage
  - Select routes for challenge by fixed-order gating rules covering route quality control failure, field criticality, a page-level audit recommendation, and low routing confidence, recording for every route whether it was selected and by which rule
  - Honour the published skip signal by suppressing parser-risk-based selection only, leaving quality-control, criticality, and confidence selection intact
  - Select a route for challenge or for human-review escalation, per configuration, when the pages it depends on show numeric-token or table-detection disagreement, and record which was chosen
  - Enforce the per-document call bound with deterministic ordering, counting suppressed calls and leaving suppressed routes unchanged
  - Accept either alternative locations or an explicit confirmation, record which was returned, and pass alternatives to adjudication rather than applying them to the route
  - Reject any response carrying an extracted value; on failure or exhausted retries retain the original route, mark the challenge not completed, and record the reason; persist raw output linked to the challenged route
  - Observable: a four-route fixture selects exactly the two matching gates; lowering the call bound to one records one suppression and leaves the second route untouched; a failing challenge leaves the original route intact with a recorded reason
  - _Requirements: 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 12.5_
  - _Boundary: CounterfactualStage_
  - _Depends: 4.1_

- [ ] 4.3 Implement deterministic route adjudication
  - Merge locator primaries, locator backups, counterfactual alternatives, and every local retrieval candidate, including candidates the locator ignored, into exactly one route per field without any model call
  - Order the merged evidence by locator primaries, then counterfactual alternatives for critical fields, then table and caption evidence where the field's expected kinds indicate it, then locator backups, then counterfactual alternatives for non-critical fields, then remaining local candidates, using descending retrieval score with identifier tie-break inside each tier
  - Represent an identifier proposed by several sources once at its best tier while recording every proposing source, its rank, and the rule under which it was retained
  - Record the decision rule applied for each field, and produce an empty-route record naming the field and reason, marked for human review, when no source proposed anything
  - Carry the routing confidence onto the adjudicated route instead of discarding it with the raw route: take it from the route that was adjudicated, use the lowest confidence level for a local-retrieval-only route and for an empty route, and cap it at the middle level when a counterfactual alternative was promoted for a critical field, recording the rule that produced the value alongside the decision rule
  - Observable: a fixture where a counterfactual alternative and a locator backup compete for a critical field places the alternative first and caps the carried confidence; an identifier proposed by three sources appears once listing all three; a field with no candidates yields an empty route with a reason and the lowest confidence; a local-only route carries the lowest confidence; repeated adjudication of the same input is equal
  - _Requirements: 2.4, 2.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_
  - _Boundary: RouteAdjudicator_
  - _Depends: 4.2_

- [ ] 5. Pack assembly on the shared pruning path

- [ ] 5.1 Pin the current synthesis pruning behavior with a characterization test
  - Capture the existing confidence-aware evidence pruning output for a representative over-budget synthesis prompt, covering the case where every unprotected item is dropped and the case where pruning succeeds part-way
  - Assert the exact returned text and change flag so the subsequent refactor cannot alter observable behavior
  - Observable: the test passes against the current implementation before any pruning code is moved, and is the gate for task 5.2
  - _Requirements: 10.7_
  - _Boundary: token_budget, pdf_processor pruning_

- [ ] 5.2 Promote and generalize the identifier-aware pruner into the shared budget module
  - Move the identifier-aware pruner into the shared token budget module and generalize it to accept a per-item priority value, a non-droppable identifier set, the other prompt sections, a stage label, and a serializer
  - Drop the highest-priority-value items first with an identifier tie-break that preserves the existing earliest-listed-survives-longest convention, and never drop a non-droppable identifier
  - Return the kept items, the resulting token estimate, and one discard record per dropped item naming the identifier, the reason, the phase, and the stage
  - Reduce the previous pruner to a thin wrapper preserving its existing signature and behavior, and leave the flat-text mitigation ladder and its ordering untouched
  - Observable: the characterization test from 5.1 still passes unchanged; a non-droppable identifier is never present in the returned discard records; the synthesis path now yields discard records for what it drops
  - _Requirements: 10.4, 10.5, 10.7_
  - _Boundary: token_budget, pdf_processor pruning_
  - _Depends: 5.1_

- [ ] 5.3 Assemble token-capped extraction packs with recorded discards
  - Build one pack per routing unit carrying the field definitions, evidence snippets, the route trace, the parser-risk flags for the routed pages, and the document metadata
  - Attach verbatim text only to primary and promoted evidence, truncated at the configured maximum snippet length on a word boundary, and represent all other evidence by identifier, kind, page, and section alone
  - Estimate and check the pack against the configured cap using the pipeline's existing estimator and budget check, with no second estimator
  - Trim through the shared pruner in a fixed phase order that shortens non-critical quotes, then drops non-critical quotes, then drops identifiers from the lowest tier upward, never dropping an identifier while any quote remains shortenable
  - Always retain critical fields' primary identifiers; when the pack still exceeds the cap with only those remaining, record the affected field indices as oversize and return the pack rather than raising
  - Observable: an over-cap fixture yields a pack within the cap plus discard records naming every shortened and dropped identifier; a fixture whose critical primaries alone exceed the cap returns an oversize pack retaining them; repeated assembly of the same input yields equal packs and equal discard records
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - _Boundary: ExtractionPackAssembler_
  - _Depends: 4.3, 5.2_

- [ ] 6. Emit routing derivation records into the provenance subsystem
  - Register the seven routing stage names — index build, local retrieval, locator, route quality control, counterfactual, adjudication, and pack assembly — in the provenance subsystem's stage-contract table and its stage ordering, placed after the evidence-index stage and before the extraction stages; that table is closed, so an unregistered stage is rejected at record time rather than recorded
  - Pin, as a lookup table rather than a per-call decision, the derivation kind and the determinism value for each of the seven stages, choosing only members of the provenance subsystem's closed derivation-kind vocabulary and using its determinism vocabulary rather than a boolean flag
  - Emit each record through the provenance subsystem's stage-event adapter, passing the derivation kind that adapter requires, and naming the input artifacts, the output artifacts, and the stage
  - Carry the model identity in effect on the two model-driven stage records and no identity on the five deterministic ones
  - Record what was discarded and the reason on the stages that drop evidence — retrieval exclusions, dropped invalid identifiers, collapsed duplicates, and pack-assembly discards
  - Construct scoped evidence identifiers only through the provenance subsystem's sanctioned constructor, never by string formatting
  - Continue routing and record that emission did not occur when the provenance recorder is absent or disabled, and treat an unknown-stage rejection as recorded contract drift rather than a routing failure
  - Observable: a drift guard asserts every routing stage appears in the local kind table, the stage-contract table, and the stage ordering, and that every mapped kind is a member of the closed derivation-kind vocabulary; a routing run against a recording double emits one record per stage whose determinism value matches the table and whose model identity is present exactly on the two model-driven stages; a run with no recorder completes and reports that provenance emission did not occur; a guard test asserts no routing module formats an evidence identifier itself
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.6, 11.7_
  - _Boundary: RoutingProvenanceEmitter, provenance stage-contract table_
  - _Depends: 5.3_

- [ ] 7. Orchestration and pipeline integration

- [ ] 7.1 Implement the routing service that sequences the stages and isolates failures
  - Sequence configuration resolution, artifact reuse, index build, hint derivation, retrieval, unit planning, location, route quality control, counterfactual selection and challenge, per-field re-routing of flagged fields, adjudication, pack assembly, provenance emission, and persistence
  - Return a disabled result carrying no routes and no priority map when routing is turned off in configuration
  - Capture a per-field failure against that field and continue with the remaining fields; capture a document-wide failure, including an unavailable index, onto the result rather than raising
  - Accept the agreement publication in the exact shape the upstream feature writes it and convert it internally through the parser-risk adapter, so no caller has to know the conversion rule
  - Accept the gateway and packet builder by injection and construct neither
  - Account for locator calls, counterfactual calls, suppressions, and the trigger rule per challenged field, and carry the fully resolved configuration on the result, including which parser-risk availability case applied
  - Dispatch routing units concurrently under the pipeline's existing concurrency gate without introducing a new one
  - Observable: a mocked end-to-end routing run returns a populated result whose call ledger matches the mocked call count; injecting an exception for one field yields a result with that field recorded as failed and every other field routed; a disabled configuration returns an empty, disabled result
  - _Requirements: 1.6, 11.5, 11.6, 12.4, 12.5, 13.2, 13.4, 13.5, 13.6, 13.7_
  - _Boundary: EvidenceRoutingService_
  - _Depends: 6_

- [ ] 7.2 (P) Make the shared paper evidence package route-priority aware
  - Add an optional deterministic priority mapping to the paper-level evidence package builder, used only as the primary sort key ahead of the existing score and identifier ordering
  - Keep the emitted package byte-identical to today when no priority mapping is supplied
  - Continue emitting exactly one package per paper so its bytes remain identical across warmup, extraction chunks, and synthesis
  - Observable: building with no priority mapping reproduces the pre-change bytes exactly; building with a priority mapping reorders selection deterministically and still yields one package reused across every chunk
  - _Requirements: 12.1, 12.3_
  - _Boundary: evidence_index package builder_

- [ ] 7.3 (P) Extend telemetry and the dependency-direction guard for the routing stages
  - Record routing calls under the new locator and counterfactual telemetry stage labels so their cost appears in the existing stage summaries and the prefix-drift check covers them; add the labels as recorded values only and introduce no enumerated or allowlisted set of stage names, because the reporting feature requires an unrecognized stage to be reported as-is
  - Add the two routing call kinds to the gateway and transport call-kind vocabulary so routing calls cross the governed egress path under their own label instead of borrowing an extraction label
  - Extend the dependency-direction test to assert the routing prompt, client, and schema-validator modules import nothing from the pipeline, quality-control, extractor, or privacy packages, that the routing client imports neither the extraction provider client nor its retry helper, and that quality control imports nothing from the routing package
  - Observable: a run recording routing calls shows both new stages in the stage summary and an unrecognized label is still reported rather than rejected; the dependency-direction suite fails if a routing agent module is given a pipeline import, a privacy import under any guard, or an import of the extraction provider client
  - _Requirements: 12.6_
  - _Boundary: telemetry, dependency direction tests_

- [ ] 7.4 Wire routing into per-document processing
  - Await routing between the evidence bundle build and the paper package build, passing the bundle, the unified content, the full field list, the pre-filled field indices, the artifact key, the resolved configuration, the published agreement block forwarded verbatim from the quality-control metrics hierarchy, the gateway and packet builder already held for the extraction calls, the provenance recorder, and the telemetry collector; no concurrency gate is passed, since gating lives inside the injected transport
  - Pass the resulting priority mapping into the paper package builder, and pass no mapping when routing is disabled or failed
  - Persist the routing result alongside the existing document outputs and record a document-level routing failure without aborting the run
  - Observable: processing a fixture document with routing enabled writes the routing artifact set and completes; with routing disabled the emitted package bytes match the pre-change baseline; a document whose routing fails is recorded and the remaining documents still process
  - _Requirements: 13.4, 13.5, 13.6_
  - _Depends: 7.1, 7.2_

- [ ] 8. Validation

- [ ] 8.1 Verify the locator repair, escalation, and coverage paths end to end
  - Exercise a first-invalid then valid exchange, an always-invalid exchange that escalates and still yields routes from local retrieval alone, and a response citing an unknown identifier
  - Assert every expected field is covered by exactly one adjudicated route in all three cases
  - Observable: all three scenarios complete with full field coverage and the expected recorded issues and escalations
  - _Requirements: 5.2, 5.3, 6.1, 9.1_
  - _Depends: 7.4_

- [ ] 8.2 Verify parser-risk consumption and counterfactual gating end to end
  - Exercise a critical field routed to a risky page with table-detection disagreement, a route on a skip-signalled page, a low-confidence route, and a clean route
  - Assert the stricter-handling marking, the scheduled audit, the honoured skip signal, the recorded trigger rules, and that risk flags reach the assembled pack
  - Exercise the per-document call bound and assert the suppression is counted and the suppressed route is unchanged
  - Exercise the default upstream state in which the agreement feature is disabled and publishes only a skipped record, and assert routing completes with every route's risk state unknown, no route treated as safe to skip, and the availability case recorded
  - Observable: the fixture produces exactly the expected selection set, ledger counts, and pack-level risk flags, and the disabled-upstream run produces no safe-to-skip route
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 8.1, 8.2, 10.1, 12.4, 12.5_
  - _Depends: 7.4_

- [ ] 8.3 (P) Verify prompt-cache stability and the disabled path
  - Assert that with routing enabled every extraction chunk prefix for one document is byte-identical and that the existing extraction shared prefix source is unchanged
  - Assert every routing agent message for one document shares identical prefix bytes across units and across both agents' own prefixes
  - Assert that with routing disabled the serialized paper package bytes match the pre-change baseline exactly
  - Observable: the prefix-stability and disabled-path assertions pass, and the prefix-drift check reports no drift for a routing-enabled run
  - _Requirements: 12.1, 12.2, 12.3, 13.4_
  - _Boundary: routing_prompts, evidence_index package builder_
  - _Depends: 7.4_

- [ ] 8.4 (P) Verify resumability, invalidation, and configuration failure handling
  - Assert a resumed run with a matching artifact key reuses recorded routes, counterfactual outcomes, and packs and issues no provider call
  - Assert a changed document fingerprint or extraction schema fingerprint discards the recorded artifacts and re-routes
  - Assert an invalid configuration value prevents routing from starting with an error naming the setting, and that an artifact write failure is recorded while routing continues
  - Observable: the resume scenario records zero provider calls, the invalidation scenario re-routes, and the write-failure scenario completes with the failure recorded
  - _Requirements: 5.7, 13.3, 13.7, 13.8_
  - _Boundary: RoutingArtifactStore, RoutingConfig_
  - _Depends: 7.4_

- [ ] 8.5 (P) Verify determinism across the deterministic stages
  - Assert that index building, hint derivation, retrieval, route quality control, adjudication, and pack assembly each produce equal output across repeated runs on the same fixture
  - Assert that adjudicated evidence ordering and discard records are stable under input permutation that does not change content
  - Observable: the determinism suite passes for all six stages, including a permuted-input case
  - _Requirements: 1.7, 2.7, 6.8, 9.7, 10.8_
  - _Boundary: DocumentIndexBuilder, FieldHintBuilder, LocalRetriever, RouteQualityControl, RouteAdjudicator, ExtractionPackAssembler_
  - _Depends: 7.4_

- [ ] 8.6 (P) Verify governed egress and payload governance end to end
  - Assert that a routing-enabled run drives every provider call through the privacy gateway with a fake transport, and that the transport is never invoked from any routing module directly
  - Assert that no routing module imports the extraction provider client or references its retry helper anywhere in the source tree
  - Assert that every model-visible string reaching the gateway during a run is a governed packet payload, that the document index package is built exactly once and reaches every routing call byte-identically, and that each unit snippet block is governed as its own payload instance carried by exactly one call and citing the identifiers it discloses
  - Assert that a blocked document package records a document-level routing failure, a blocked unit packet records per-field failures, and neither path transmits anything
  - Observable: the governance suite passes with zero direct transport invocations, zero ungoverned payloads, and no transmission on either block path
  - _Requirements: 4.7, 12.6_
  - _Boundary: routing_client, LocatorStage, CounterfactualStage_
  - _Depends: 7.4_
