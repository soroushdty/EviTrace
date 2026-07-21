# Requirements Document

## Project Description (Input)
Reviewers extracting 62 fields from a biomedical paper cannot tell why a given passage was shown to the extractor. Today the evidence handed to the LLM is selected by a generic, field-agnostic section-score heuristic. When that heuristic misses the paragraph that actually answers a field, the extractor either hallucinates from adjacent text or reports not-reported, and nothing in the audit trail records that the *location* choice, not the extraction, was the failure.

`evidence-routing` introduces an explicit, audited routing layer between document parsing and value extraction: deterministic field-aware local retrieval over section, paragraph, table, and caption indices; a locator agent that only points at evidence and never extracts values; deterministic route quality control; a gated counterfactual locator that challenges weak or critical routes; and a deterministic route adjudicator that merges all candidate sources into a token-capped extraction pack with route provenance and discarded-evidence records preserved.

## Introduction

This spec delivers the routing layer described by multiagent R7–R11, plus the two consumption clauses of multiagent R5 (R5.6 and R5.8) that act on parser-risk signals. It sits between the existing quality-controlled document representation and the value-extraction agents that `multiagent-extraction` will own.

The layer is deliberately three-tiered, with a deterministic stage on both sides of every model call: deterministic local retrieval produces hints, one locator agent points at evidence, deterministic route quality control judges the result, a gated counterfactual locator challenges only the routes that quality control flags, and a deterministic adjudicator assembles the final token-capped extraction pack. This keeps cost bounded and keeps the audit trail reproducible from recorded inputs.

Three roadmap Open Questions are resolved here rather than by silent default:

- **Open Question 1 — which parser is the default canonical source for biomedical PDFs with complex tables.** Resolved: the structured scholarly parser output remains the canonical source for body text, section structure, and section paths. For table and caption evidence it is canonical only when it yields non-empty structured table content; where it does not, or where the parser-agreement signal reports table-detection disagreement for the page, the structural block parser's table candidate is used as the canonical table content for routing, and the route records which parser supplied it. The system never silently substitutes one parser's table content for another's without recording the substitution.
- **Open Question 5 — should the locator route fields individually or by field group.** Resolved: routing is requested **per field group** by default, using the extraction schema's existing domain groups, and the locator returns **one route object per field** within the requested group. Per-field re-routing is used only for fields that route quality control flags. The granularity is configurable so a deployment can force per-field routing.
- **Open Question 6 — the balance between evidence quote length and paragraph-ID-only evidence.** Resolved: every route always resolves to evidence identifiers, which are never trimmed away. Verbatim quoted text is attached only to primary evidence (and to backup evidence promoted to primary by adjudication), bounded by a configurable per-snippet character limit; all other evidence appears in the pack as identifier, kind, page, and section only. Quote length is the first thing trimmed under token pressure; identifiers are the last.

Requirement coverage relative to the archived source document `evitrace_multiagent.md`: multiagent R7, R8, R9, R10, R11 in full; multiagent R5.6 and R5.8 as consumption clauses only. All other clauses of multiagent R5 belong to `agreement-statistics`.

## Boundary Context

- **In scope**: Section, paragraph, table, and caption indices over the already-parsed document; field-specific retrieval hints derived from the extraction schema; deterministic local candidate retrieval per field or field group; the locator agent's request and response contract, its route object schema, its validation and retry path, and persistence of its raw output; deterministic route quality control checks, their pass/fail records, and the escalation destination each failure produces; gating, invocation, and output handling for the counterfactual locator; deterministic merging of primary, backup, counterfactual, and local-retrieval candidates into a single adjudicated route per field with route provenance retained; assembly of the extraction pack, its token cap, its deterministic trimming order, and the record of every discarded evidence identifier and the reason for discarding it; consumption of parser-risk and parser-agreement signals to require stricter downstream handling for critical fields and to trigger a counterfactual audit or human-review escalation; the configuration surface for all of the above.
- **Out of scope**: Deciding any field's value, the confidence in a value, or whether an answer is correct — owned by `multiagent-extraction`. Computing agreement statistics, parser agreement metrics, parser-risky page marking, and the counterfactual-audit threshold — owned by `agreement-statistics`; this feature reads those published signals and never recomputes or overrides them. Defining evidence node identity, claim records, derivation records, or the provenance graph — owned by `provenance-core`; this feature adopts that identity and emits records into it. Parser ensemble behavior, canonical document construction, cleaning, and reference stripping. Re-tuning the token budget thresholds set by the completed token-efficient-extraction work; this feature extends the existing pruning path rather than introducing a second one. Any user interface surfacing of routes, rationale, or discarded evidence — owned by `reviewer-ui`. Human review itself: this feature can mark a field as requiring human review but does not provide the review surface.
- **Adjacent expectations**: This feature expects the document pipeline to continue producing a ranked evidence index whose items carry stable identifiers, evidence kind, page, section path, and location anchors, and it consumes those identifiers verbatim. It expects `agreement-statistics` to publish, per parser pair and per page within that pair, a parser-risky flag, a skip-counterfactual signal, and a counterfactual-audit-recommended flag, together with the identity of the page and of the pair each applies to; this feature owns the conversion of that per-pair publication into the page-indexed view it consumes, merging every pair rather than reading only the designated primary pair. While those signals are absent, unreadable, or reported as not computed, this feature treats parser risk as unknown rather than as absent, and never as safe. It expects the extraction schema to carry a per-field criticality designation from `corpus-and-schema-builder`; while criticality is absent, a documented default criticality is applied and recorded as defaulted. It expects `multiagent-extraction` to consume extraction packs and route traces without modifying them. It expects the external model call path, including any privacy gateway placed in front of it, to remain the single path through which model calls are made.
- **Standing product boundaries**: This feature does not admit fully autonomous systematic review generation without human approval, automated clinical recommendations, guaranteed extraction correctness without human validation, OCR-heavy scanned-document workflows beyond fallback support, or meta-analysis automation.

## Requirements

### Requirement 1: Structured Document Indices for Routing

**Objective:** As a reviewer, I want the document indexed by section, paragraph, table, and caption before any routing happens, so that evidence can be requested and cited at the granularity a reviewer actually reads.

#### Acceptance Criteria

1. When a document has been parsed and quality controlled, the evidence routing service shall build a section index, a paragraph index, a table index, and a caption index over that document.
2. When an index entry is created, the evidence routing service shall record its evidence identifier, its evidence kind, its page, its section path, and its ordinal position within the document.
3. The evidence routing service shall use the evidence identifiers already assigned by the document pipeline and shall not issue a second, competing identifier for the same evidence unit.
4. When an entry appears in more than one index, the evidence routing service shall represent it once and shall record every index it belongs to, rather than duplicating the entry.
5. When the parsed document yields no structured table content for a table that the page-level signals report as present, the evidence routing service shall take the table content from the structural block parser output and shall record which parser supplied it.
6. If no index can be built for a document because the parsed document carries no usable structure, then the evidence routing service shall record an index-unavailable state naming the missing structure and shall not route that document.
7. The evidence routing service shall produce identical indices for identical parsed input on repeated runs.

### Requirement 2: Field-Specific Retrieval Hints and Local Candidate Retrieval

**Objective:** As a reviewer, I want deterministic, field-aware candidate locations computed before any model is asked where to look, so that routing is cheaper, more reliable, and reproducible without a model call.

#### Acceptance Criteria

1. When an extraction schema is available, the evidence routing service shall derive field-specific retrieval hints for each field from that field's name, description, reviewer question, expected value format, and configured synonyms and expected evidence locations.
2. When retrieval hints have been derived, the evidence routing service shall retrieve ranked candidate paragraphs, sections, tables, and captions for each routing unit.
3. When candidates are ranked, the evidence routing service shall record, for each candidate, the score it received and which hint terms contributed to that score.
4. The evidence routing service shall present local retrieval results to the locator agent as hints and shall not treat them as the final route.
5. When local retrieval produces candidates that the locator agent's route does not reference, the evidence routing service shall retain those candidates as eligible inputs to counterfactual routing and to route adjudication.
6. If local retrieval finds no candidate above the configured minimum score for a routing unit, then the evidence routing service shall record a no-local-candidate state for that unit and shall still request a route for it.
7. The evidence routing service shall produce identical hints, candidates, scores, and ordering for identical inputs on repeated runs.

### Requirement 3: Routing Granularity

**Objective:** As an operator balancing cost against auditability, I want routing requested by field group but recorded per field, so that a single request covers related fields while every field still carries its own auditable route.

#### Acceptance Criteria

1. The evidence routing service shall request routes by field group by default, using the field groups declared in the extraction schema.
2. When a routing request covers a field group, the evidence routing service shall require one route object per field in that group and shall not accept a single route covering several fields.
3. When a field's route fails route quality control, the evidence routing service shall re-request a route for that field individually rather than re-routing its whole group.
4. Where per-field routing granularity is configured, the evidence routing service shall issue one routing request per field.
5. When routing granularity is resolved for a run, the evidence routing service shall record the granularity in effect alongside the routes produced under it.
6. When a field is pre-filled by the pipeline without a model call, the evidence routing service shall not request a route for it and shall record that the field was excluded from routing and why.

### Requirement 4: Locator Agent Contract

**Objective:** As a reviewer, I want a locator that only points at evidence locations and never states a value, so that where-to-look and what-it-says remain separately auditable decisions.

#### Acceptance Criteria

1. When the locator agent is invoked, the evidence routing service shall supply it the cleaned document outline, the section, table, and caption indices, the field definitions for the routing unit, and the local retrieval hints for that unit.
2. The evidence routing service shall reject any locator output that contains an extracted field value, and shall treat that output as a contract violation rather than as a route.
3. When the locator agent returns a route, that route shall identify the field, the primary evidence identifiers, the backup evidence identifiers, the pages, the section names, a routing confidence, any risk flags, and a routing rationale.
4. When a field is likely absent from the document, the evidence routing service shall still require the locator to identify the locations at which that absence can be verified, and shall record the route as an absence-verification route.
5. When the locator agent returns evidence identifiers, the evidence routing service shall verify that every identifier exists in the document indices for that run.
6. The evidence routing service shall not send document content to the locator agent other than the indices, outline, and hint snippets defined for the routing request.
7. When a locator request is issued, the evidence routing service shall route it through the project's single external model call path.

### Requirement 5: Locator Output Validation, Repair, and Raw-Output Persistence

**Objective:** As an operator, I want malformed locator output handled by a bounded, recorded repair path, so that a bad response degrades into a recorded escalation instead of an unexplained failure or a silently dropped field.

#### Acceptance Criteria

1. When the locator agent returns output, the evidence routing service shall validate it against the declared route schema before any route is used.
2. If locator output fails schema validation, then the evidence routing service shall retry the request up to the configured retry limit with a targeted repair instruction naming the validation failure.
3. If locator output still fails schema validation after the configured retry limit, then the evidence routing service shall escalate the affected routing unit to local-retrieval-only routing or to human review according to configuration, and shall record which escalation was applied and why.
4. If a locator route references an evidence identifier that does not exist in the run's indices, then the evidence routing service shall record an invalid-identifier issue naming the route and the identifier, and shall drop only that identifier rather than the whole route.
5. When a locator request completes, the evidence routing service shall persist the raw locator output, the request that produced it, and the model identity in effect, in a form retrievable for the run.
6. When persisted raw locator output is written, the evidence routing service shall associate it with the routes derived from it so a reviewer can compare the two.
7. If persisting raw locator output fails, then the evidence routing service shall record the persistence failure and shall continue the run rather than discarding the routes.

### Requirement 6: Route Quality Control

**Objective:** As a reviewer, I want every route checked before extraction runs, so that the extractor is never pointed at missing, implausible, boilerplate, or removed content without that being recorded.

#### Acceptance Criteria

1. When routes are available for a document, the route quality control stage shall verify that every field expected to be routed has exactly one route.
2. The route quality control stage shall detect missing field identifiers, duplicate coverage of the same field, invalid field identifiers, invalid evidence identifiers, empty primary evidence, and missing routing confidence, and shall report each as a named issue.
3. The route quality control stage shall detect routes whose evidence points only to references, bibliography, headers, footers, boilerplate, or content removed during cleaning, and shall report those routes as pointing at non-evidential content.
4. The route quality control stage shall verify that every field designated critical has either backup evidence or a counterfactual review scheduled, and shall report a failure when neither is present.
5. The route quality control stage shall evaluate route plausibility against the field's expected value format and expected evidence locations, and shall report an implausible-route issue when the routed evidence cannot contain a value of that shape.
6. When route quality control fails for a field, the route quality control stage shall assign that field to counterfactual routing, to local retrieval expansion, or to human review, according to the configured mapping from issue kind to destination, and shall record the assignment.
7. When route quality control completes, the route quality control stage shall record a pass or fail status and the full issue list for every route.
8. The route quality control stage shall reach the same verdicts for the same routes and indices on repeated runs.

### Requirement 7: Parser-Risk Consumption and Escalation

**Objective:** As a reviewer, I want routes that land on unreliable pages to be treated more strictly, so that a parsing failure does not become an unnoticed extraction failure.

#### Acceptance Criteria

1. When a route's evidence lies on a page published as parser-risky, the evidence routing service shall attach that page's risk flag and the reason it was marked risky to the route.
2. When a field designated critical routes to a page published as parser-risky, the evidence routing service shall mark that field as requiring stricter downstream extraction or verification and shall record the marking on the route.
3. When a page published as parser-risky carries a counterfactual-audit-recommended flag, the evidence routing service shall schedule counterfactual routing for the routes on that page.
4. When the published page signals report numeric-token or table-detection disagreement for a page a route depends on, the evidence routing service shall schedule a counterfactual audit or a human-review escalation for that route according to configuration, and shall record which was chosen.
5. When a page carries the published skip-counterfactual signal, the evidence routing service shall not schedule counterfactual routing for routes on that page on parser-risk grounds alone.
6. If no parser-risk signal is available for a page a route depends on, then the evidence routing service shall record the route's parser-risk state as unknown and shall not treat the page as safe.
7. The evidence routing service shall not compute, adjust, or override any parser agreement metric, threshold, or page flag.

### Requirement 8: Counterfactual Locator

**Objective:** As a reviewer, I want weak or critical routes challenged by a second, adversarial locator, so that evidence the first locator missed can still be found without paying for a second pass on every field.

#### Acceptance Criteria

1. When a route is designated critical, carries a routing confidence below the configured threshold, depends on a parser-risky page, or has failed route quality control, the evidence routing service shall consider that route for counterfactual routing.
2. The evidence routing service shall invoke the counterfactual locator only for routes selected by the configured gating rules, and shall record, for every route, whether it was selected and which rule selected it.
3. When the counterfactual locator is invoked, the evidence routing service shall supply it the original route, the field definitions, the document outline, the local retrieval candidates, and the selected evidence snippets.
4. The evidence routing service shall reject any counterfactual locator output that contains an extracted field value, and shall treat that output as a contract violation rather than as a route.
5. When the counterfactual locator responds, the evidence routing service shall accept either alternative or additional evidence locations, or an explicit confirmation that the original route appears sufficient, and shall record which was returned.
6. When the counterfactual locator proposes alternatives, the evidence routing service shall pass those alternatives to route adjudication rather than replacing the original route directly.
7. When the counterfactual locator completes, the evidence routing service shall persist its raw output and associate it with the route it challenged.
8. If the counterfactual locator call fails or its output fails validation after the configured retry limit, then the evidence routing service shall retain the original route, mark the counterfactual challenge as not completed, and record the reason.

### Requirement 9: Deterministic Route Adjudication

**Objective:** As a system developer, I want one deterministic adjudicator to merge every candidate source into a single route per field, so that what the extractor sees is reproducible and every included location can be traced to where it came from.

#### Acceptance Criteria

1. When locator routes, counterfactual outputs, and local retrieval candidates are available for a field, the route adjudicator shall merge them into exactly one adjudicated route for that field.
2. When an evidence identifier is included in an adjudicated route, the route adjudicator shall record which source proposed it, at what rank, and under which rule it was retained.
3. When the same evidence identifier is proposed by more than one source, the route adjudicator shall represent it once and shall record every source that proposed it.
4. The route adjudicator shall order the adjudicated route's evidence by primary route evidence first, then counterfactual alternatives for fields designated critical, then table and caption evidence where the field's expected value format indicates it, then remaining backup and local retrieval candidates.
5. The route adjudicator shall reach an adjudication decision without invoking any model.
6. When adjudication completes, the route adjudicator shall record the decision rule applied for each field.
7. The route adjudicator shall produce identical adjudicated routes for identical inputs on repeated runs.
8. If a field has no candidate evidence from any source, then the route adjudicator shall produce an empty-route record naming the field and the reason, and shall mark the field for human review rather than omitting it.

### Requirement 10: Extraction Pack Assembly and Token Capping

**Objective:** As a system developer, I want extraction packs capped at a configured token size with deterministic trimming and a record of what was dropped, so that a pack is always affordable and a reviewer can always tell what the extractor was not shown.

#### Acceptance Criteria

1. When an adjudicated route is available for a field, the extraction pack assembler shall produce a pack containing the field definitions, the evidence snippets, the route trace, the parser risk flags, and the document metadata for that field or field group.
2. The extraction pack assembler shall attach verbatim quoted text only to evidence designated primary in the adjudicated route, bounded by the configured maximum snippet length, and shall represent all other evidence by identifier, kind, page, and section only.
3. The extraction pack assembler shall cap each pack's estimated token size at the configured limit for the extraction stage, using the pipeline's existing token estimation and budget checking rather than a second estimator.
4. If a pack exceeds its token cap, then the extraction pack assembler shall trim it by the deterministic priority order defined for adjudicated routes, shortening quoted text before removing any evidence identifier, and removing lowest-priority evidence identifiers last.
5. When trimming removes or shortens content, the extraction pack assembler shall record every discarded evidence identifier, every shortened snippet, and the reason each was trimmed.
6. The extraction pack assembler shall never trim away the primary evidence identifier of a field designated critical; if the pack cannot fit while retaining it, the assembler shall record a pack-oversize failure for that field naming the retained identifiers rather than silently dropping them.
7. The extraction pack assembler shall extend the pipeline's existing evidence pruning path and shall not introduce a second, independent pruning implementation.
8. The extraction pack assembler shall produce identical packs and identical discard records for identical inputs on repeated runs.

### Requirement 11: Route Provenance and Audit Records

**Objective:** As an institutional reviewer, I want the routing decision recorded as first-class provenance, so that a wrong extraction can be attributed to the location choice when that is what actually failed.

#### Acceptance Criteria

1. When a routing stage transforms, merges, filters, or discards evidence, the evidence routing service shall emit a derivation record naming the inputs, the outputs, the stage, and whether the stage was deterministic or model-driven.
2. When a routing stage discards evidence, the emitted derivation record shall name what was discarded and the reason.
3. The evidence routing service shall reference evidence using the project's single evidence node identity and shall not define a routing-local identifier scheme for evidence.
4. When a model-driven routing stage runs, the emitted derivation record shall carry the model identity in effect.
5. When a route, route quality control verdict, counterfactual outcome, or adjudication decision is produced, the evidence routing service shall make it available as queryable run data rather than only as a log message.
6. When the provenance subsystem is disabled or unavailable, the evidence routing service shall continue routing and shall record that provenance emission did not occur.
7. The evidence routing service shall not format, render, or export any audit report.

### Requirement 12: Prompt-Cache Stability and Cost Control

**Objective:** As an operator paying per token, I want the new agents to preserve the existing prompt cache and to stay within a predictable call budget, so that adding routing does not multiply the cost of every run.

#### Acceptance Criteria

1. The evidence routing service shall not alter the shared paper prefix used by the existing extraction calls, and that prefix shall remain byte-identical across warmup, extraction chunks, and synthesis for the same document.
2. When a routing agent is invoked, the evidence routing service shall use that agent's own stable prefix and shall place all request-specific material after it.
3. When several routing requests are issued for the same document, the evidence routing service shall keep each agent's prefix byte-identical across those requests.
4. When a run completes, the evidence routing service shall record the number of locator and counterfactual calls made, the routing units they covered, and the reason each counterfactual call was made.
5. The evidence routing service shall enforce a configurable upper bound on counterfactual calls per document and shall record when that bound suppressed a scheduled call.
6. The evidence routing service shall issue routing model calls through the pipeline's existing concurrency limits and retry behavior rather than its own.

### Requirement 13: Configuration, Failure Handling, and Resumability

**Objective:** As an operator, I want routing controlled from configuration and able to fail safely and resume, so that a routing failure costs one field or one document rather than the whole run.

#### Acceptance Criteria

1. The evidence routing service shall expose in configuration the routing granularity, the retrieval hint sources and minimum candidate score, the locator and counterfactual model identities and retry limits, the counterfactual gating thresholds and per-document call bound, the route quality control issue-to-destination mapping, the maximum snippet length, and the extraction pack token cap.
2. When configuration omits a routing setting, the evidence routing service shall apply a documented default and shall record the effective value in the run's routing record.
3. If a configured routing setting is invalid, then the evidence routing service shall report an error naming the setting and the invalid value and shall not start routing for the run.
4. Where routing is disabled in configuration, the pipeline shall run to completion using its existing evidence selection behavior and shall record that routing was not performed.
5. If routing fails for a single field, then the evidence routing service shall record the failure against that field and shall continue routing the remaining fields.
6. If routing fails for an entire document, then the evidence routing service shall record the document as routing-failed with the reason and shall continue processing the remaining documents.
7. When a run is resumed after an interruption, the evidence routing service shall reuse routes, counterfactual outcomes, and adjudicated packs already recorded for that document rather than re-issuing the model calls that produced them.
8. If a recorded routing artifact was produced under a different extraction schema or a different document fingerprint, then the evidence routing service shall discard it and re-route rather than reusing it.
