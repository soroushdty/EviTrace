# Requirements Document

## Project Description (Input)

`privacy-core` ships a disclosure gate that can decide "this evidence may leave only in transformed form", and a `TransformationProvider` protocol plus registry through which that decision is dispatched. It deliberately implements no transformation. The consequence is that the shipped default posture blocks every sensitivity label above `public_safe`: a policy that requires a transformation has no provider to satisfy it, so it fails closed to `block`. EviTrace is therefore unusable, by construction, for exactly the restricted corpora the privacy work exists to serve.

`privacy-transformations` supplies the missing provider. It delivers a deterministic identifier-detection substrate with declared, measured, per-category detection support; four policy-selectable transformation behaviours built on one rewrite engine (redaction, pseudonymization, minimization scoped to the active extraction field map, and temporal abstraction); an operational definition and detector for meaning alteration; representation governance for encoder outputs, which are treated as sensitive by default and never as safe while unevaluated; and a leakage-risk evaluator, deliberately separated from every transformer, whose declared ordinal rubric gates external use and whose `unresolved` verdict blocks rather than passes.

Everything measurable in this spec is measured against a synthetic evaluation corpus with fabricated ground truth. No protected health information and no real patient data enters the repository or any fixture. That is a hard constraint and it is also a hard limit on how strongly any claim here can be validated, and the spec is required to say so in every artifact it produces. The subsystem provides controls, records, and audit surfaces. It provides no de-identification guarantee, no expert determination, and no legal compliance claim of any kind.

## Introduction

This spec fills the single hole that makes `privacy-core` a blocker rather than an enabler. It is scoped tightly by three decisions.

**One engine, several profiles.** Redaction, pseudonymization, and temporal abstraction are not three algorithms; they are three *actions* selected per identifier category over one detection-and-rewrite pass. Minimization is a separate unit-level pass over the rewritten text. A named transformation such as `redact` or `pseudonymize` is therefore a declared **transformation profile** — a category-to-action mapping plus a minimization setting plus a temporal mode — over one deterministic engine. This is what keeps four requirement areas from becoming four independently drifting implementations.

**Measurable or deferred.** The roadmap flags this spec as its highest research risk and records that its inherited acceptance criteria — "information not necessary for the task", "if a transformation changes clinical meaning", "unnecessary identifying detail", "task-relevant clinical structure", and every leakage-risk dimension — are unfalsifiable as written. Each one is given an operational definition, a computable measure, and a fixture with ground truth in this document, or it is explicitly deferred. Nothing is designed against an aspiration. Where an operational definition is weaker than the concept it stands in for, the requirement says so and the subsystem records the gap in its own output.

**Honest limits are a shipped artifact, not a caveat.** Pattern-based detection cannot find person names or free-text identifiers, ordinal risk levels are not probabilities, and metrics measured on fabricated fixtures do not transfer to real corpora. These are stated as requirements with tests, not as prose in a README.

The subsystem never decides disclosure. `privacy-core`'s disclosure gate is the sole construct site of a disclosure decision; this subsystem is invoked by it, returns a result marked complete or incomplete, and an incomplete result is what causes a block. Every fail-closed behaviour required here is expressed through that existing contract.

## Boundary Context

- **In scope**: A deterministic identifier-detection substrate over a closed, versioned catalogue of identifier categories, with a declared detection-support level per category and measured per-category accuracy on a synthetic corpus; a single offset-safe rewrite engine and the declared transformation profiles that drive it; redaction as removal or category-marked replacement; pseudonymization producing stable, category-tagged, non-invertible surrogate identifiers within a declared scope, plus the surrogate-mapping handoff record; minimization whose necessity definition is derived from the active extraction field map and whose retention of necessary content is a hard gate; temporal abstraction in relative, shifted, and suppressed modes with declared interval-preservation semantics and age-bucketing above a configured cap; an operational definition of meaning alteration, its assessment, and its three-valued verdict in which an undetermined result is treated as altered; representation governance in which encoder and semantic-representation outputs inherit the strictest contributing sensitivity label, are evaluated against declared computable checks, and are never treated as safe while unevaluated; a leakage-risk evaluator with a named ordinal rubric over five declared dimensions, a numeric configurable threshold, an `unresolved` verdict, and threshold gating expressed as an incomplete transformation result; per-transformation records written as run-scoped artifacts and referenced from the result handed back to the disclosure gate; the synthetic evaluation corpus, its fabricated ground truth, and versioned measured baselines that fail on regression; and a fixed limits statement embedded in every artifact this subsystem produces.
- **Out of scope**: The disclosure decision itself, the policy gate, the evidence packet envelope, the model gateway, the privacy audit trail, secret resolution, and sensitivity classification — all owned by `privacy-core` and consumed as-is. Storage of surrogate-to-real mappings, authorization to resolve them, and access logging — that is the private vault, owned by `public-private-provenance`; this subsystem produces mappings and hands them off. Cryptographic commitments and safe-reference substitution for provenance anchors — also `public-private-provenance`. Any clinical named-entity-recognition model, machine-learned identifier detector, clinical encoder selection, or third-party de-identification service. Any statistical disclosure-control estimate, re-identification probability, or expert determination. Any sensitivity detector or model-response scanner registered with `privacy-core`. Any legal, regulatory, or certification claim.
- **Adjacent expectations**: This subsystem expects `privacy-core` to remain the single decision authority, to dispatch transformations only through its registry, to construct providers without constructor arguments, and to block on any transformation result that is not marked complete. It expects `provenance-core` to own evidence node identity, which this subsystem preserves unchanged and never redefines. It expects the active extraction field map to remain the declaration of what the task needs, since minimization's necessity definition is derived from it. It expects the operator, not this subsystem, to supply gazetteers for identifier categories that pattern matching cannot detect, and to choose which transformation profile a policy names. It does not own, and must not absorb, the behaviour of any of those adjacent components.
- **Standing product boundaries**: This spec does not admit autonomous systematic review generation, automated clinical recommendations, guaranteed extraction correctness without human validation, scanned-document workflows beyond fallback support, or meta-analysis automation.

## Requirements

### Requirement 1: Transformation Provider Integration and Fail-Closed Contract

**Objective:** As a clinical researcher whose corpus is currently blocked by the default privacy posture, I want a registered transformation provider that satisfies the disclosure gate's dispatch, so that policies requiring a transformation produce disclosable evidence instead of collapsing into a block.

#### Acceptance Criteria

1. When the privacy subsystem dispatches a named transformation to this subsystem, the transformation subsystem shall return a transformation result that is either marked complete with a transformed payload, or marked incomplete, and shall produce no third outcome.
2. When a transformation completes, the transformation subsystem shall return every evidence identifier it received, unchanged, in the result's preserved identifier set.
3. If any stage of a transformation raises, exceeds its configured time budget, or cannot establish that its own postconditions hold, then the transformation subsystem shall return a result marked incomplete rather than returning partially transformed content.
4. If a result is marked incomplete, then the transformation subsystem shall not return any payload that could be mistaken for a disclosable one, and shall name the stage and the declared failure category that caused it.
5. The transformation subsystem shall not construct, modify, or infer a disclosure decision, and shall not determine whether evidence may leave the local boundary.
6. The transformation subsystem shall be constructible without arguments so that the privacy subsystem's registry can instantiate it from a declared class path, and shall report a declared configuration defect as an incomplete result on every transformation rather than at construction time.
7. When the same payload, the same evidence identifiers, the same sensitivity label, and the same configuration are supplied, the transformation subsystem shall produce a byte-identical transformed payload and an identical set of recorded findings.

### Requirement 2: Identifier Detection Substrate and Declared Detection Support

**Objective:** As a data steward, I want to know exactly which identifier categories this subsystem can and cannot find, so that I am never misled into believing a category was handled when nothing was capable of detecting it.

#### Acceptance Criteria

1. The transformation subsystem shall define a closed, versioned catalogue of identifier categories, and shall assign every detected span exactly one category from that catalogue.
2. The transformation subsystem shall declare, for each identifier category, a detection-support level drawn from a closed set comprising pattern-based, operator-gazetteer-dependent, and unsupported.
3. When detection runs over a payload, the transformation subsystem shall produce a set of non-overlapping spans, each carrying its category, its character offsets in the input payload, and the detection-support level that produced it.
4. If a transformation profile assigns a non-passthrough action to a category whose detection-support level is unsupported, and no operator gazetteer is supplied for that category, then the transformation subsystem shall return an incomplete result naming that category rather than reporting the category as handled.
5. Where an operator supplies a gazetteer for a category, the transformation subsystem shall match its entries case-insensitively against the payload and shall attribute the resulting spans to that category with the gazetteer-dependent support level.
6. When two detected spans overlap, the transformation subsystem shall resolve them by a declared, deterministic precedence rule and shall record which span was suppressed and why.
7. The transformation subsystem shall record, for every transformation, the count of detected spans per category and the count of categories that were requested but not detectable, and shall not report an undetectable category as having zero occurrences.

### Requirement 3: Redaction

**Objective:** As a clinical document user, I want restricted identifiers removed or replaced before external disclosure, so that the payload a model receives no longer carries the identifier surface forms that were present in the source.

#### Acceptance Criteria

1. When a transformation profile assigns the redaction action to an identifier category, the transformation subsystem shall replace every detected span of that category in the payload with a category-marked placeholder that carries no residue of the original surface form.
2. When redaction completes, the transformation subsystem shall verify by re-running detection over the transformed payload that no span of any redacted category survives, and shall return an incomplete result if any does.
3. The transformation subsystem shall apply every rewrite in a single offset-safe pass over the original payload, so that no rewrite is applied to text produced by another rewrite.
4. When redaction produces a placeholder, the transformation subsystem shall use a placeholder form that is stable for a given category and that is itself detectable, so that a repeated transformation of an already-redacted payload changes nothing further.
5. If a redaction placeholder would collide with content already present in the source payload, then the transformation subsystem shall record the collision and shall return an incomplete result.

### Requirement 4: Pseudonymization and Surrogate Identity

**Objective:** As a researcher who needs cross-document reasoning over restricted material, I want direct identifiers replaced by controlled surrogates rather than erased, so that references remain internally consistent without the original identifier leaving the machine.

#### Acceptance Criteria

1. When a transformation profile assigns the pseudonymization action to an identifier category, the transformation subsystem shall replace every detected span of that category with a surrogate identifier that is tagged with its category and is distinguishable from a real identifier of that category by inspection.
2. While a single pseudonymization scope is in effect, the transformation subsystem shall produce the same surrogate for every occurrence of the same normalized surface form within the same category, and different surrogates for different surface forms.
3. When two pseudonymization scopes differ, the transformation subsystem shall produce different surrogates for the same surface form, so that surrogates are not linkable across scopes without the mapping.
4. The transformation subsystem shall derive surrogates in a manner that is not invertible from the surrogate alone, and shall not embed any portion of the original surface form in the surrogate.
5. When pseudonymization completes, the transformation subsystem shall emit a surrogate mapping record containing each surrogate, its category, its scope identifier, and the key-version identifier in effect, and shall hand that record to its caller rather than persisting it as a resolvable store.
6. The transformation subsystem shall not persist any mapping from a surrogate to an original surface form in any run-scoped artifact, log, transformation record, or risk report.
7. Where no persisted key material is available, the transformation subsystem shall declare surrogate stability as holding within the current run only, shall record that limitation in the mapping record, and shall not represent surrogates as stable across runs.

### Requirement 5: Minimization Scoped to the Active Extraction Field Map

**Objective:** As a privacy reviewer, I want "information not necessary for the task" defined against something concrete, so that minimization can be tested rather than asserted.

#### Acceptance Criteria

1. The transformation subsystem shall define task necessity by reference to the active extraction field map, treating the field names, definitions, reviewer questions, and category or example vocabulary of the fields in that map as the declared necessity vocabulary.
2. When minimization runs, the transformation subsystem shall divide the payload into retention units at a declared unit boundary and shall retain every unit that matches at least one term of the necessity vocabulary or that contains at least one numeric, quantity, unit, or statistical token.
3. When minimization drops a retention unit, the transformation subsystem shall replace that unit's text with a suppression marker that preserves the unit's evidence identifier, so that no evidence identifier is lost by minimization.
4. When minimization completes, the transformation subsystem shall record the number of units examined, retained, and dropped, and the resulting retention ratio.
5. If the active extraction field map cannot be loaded or contains no fields, then the transformation subsystem shall return an incomplete result rather than minimizing against an empty necessity vocabulary.
6. Where a unit's necessity cannot be determined, the transformation subsystem shall retain that unit, so that uncertainty results in retention rather than removal.
7. The transformation subsystem shall report the measured recall of necessary content against the synthetic evaluation corpus, and that measured recall shall be exactly one on that corpus.

### Requirement 6: Temporal Abstraction

**Objective:** As an institutional user, I want policy-controlled handling of dates and ages, so that temporal information can be coarsened consistently rather than either transmitted exactly or destroyed wholesale.

#### Acceptance Criteria

1. The transformation subsystem shall support three temporal modes drawn from a closed set comprising relative offsets from a declared anchor, consistent shifting by a scope-wide offset, and suppression.
2. While the shifting mode is in effect for a temporal scope, the transformation subsystem shall apply one offset to every date within that scope, so that every pairwise interval between dates within the scope is preserved exactly.
3. While the relative mode is in effect, the transformation subsystem shall replace each date with its signed day offset from the scope's declared anchor date, and shall preserve intervals between dates within the scope exactly.
4. While the suppression mode is in effect, the transformation subsystem shall replace every detected date with a category marker such that re-running date detection over the transformed payload yields no date span.
5. When an age value exceeds the configured age cap, the transformation subsystem shall replace it with a single bucket marker representing all values above that cap.
6. If a detected date cannot be parsed into a calendar date, then the transformation subsystem shall suppress it rather than shifting or relativizing it, and shall record it as an unparsed date.
7. When a temporal scope is established, the transformation subsystem shall derive its offset from the scope identifier and the run key material only, so that the same scope yields the same offset within a run and different scopes yield different offsets.

### Requirement 7: Meaning-Alteration Assessment

**Objective:** As a clinical reviewer, I want to know when a transformation may have changed what the evidence says, so that a transformed payload is never treated as clinically equivalent to its source without evidence.

#### Acceptance Criteria

1. The transformation subsystem shall produce, for every transformation, a meaning-alteration verdict drawn from a closed set comprising unchanged, possibly altered, and undetermined.
2. When any rewritten span overlaps a numeric, quantity, unit, percentage, interval, or statistical token, the transformation subsystem shall record the verdict as possibly altered.
3. When any rewritten span overlaps a term of the necessity vocabulary derived from the active extraction field map, the transformation subsystem shall record the verdict as possibly altered.
4. When minimization drops any retention unit, or when temporal abstraction applies a mode that does not preserve intervals, the transformation subsystem shall record the verdict as possibly altered.
5. If the assessment itself fails or cannot examine the rewritten spans, then the transformation subsystem shall record the verdict as undetermined and shall treat undetermined as possibly altered for every downstream purpose.
6. When the verdict is possibly altered, the transformation subsystem shall record the specific triggering condition and the identifiers of the affected retention units.
7. The transformation subsystem shall state, wherever the verdict is recorded, that an unchanged verdict means no declared trigger fired and does not assert clinical equivalence.

### Requirement 8: Representation Governance for Encoder Outputs

**Objective:** As a researcher exploring privacy-preserving model workflows, I want encoder and semantic-representation outputs governed rather than assumed harmless, so that a vector is never treated as safe merely because it is not readable.

#### Acceptance Criteria

1. When a representation is derived from source content, the transformation subsystem shall assign that representation the strictest sensitivity label among its contributing sources, and shall not assign a label more permissive than any contributing source.
2. When a representation is evaluated, the transformation subsystem shall record the outcome of each declared computable check, comprising whether raw source text is retained alongside the representation, the representation's declared reconstructability factors, and whether any direct-identifier span is detectable in any textual component of the serialized representation.
3. When a representation is evaluated, the transformation subsystem shall record declared structure-retention measures comprising the proportion of numeric tokens, section labels, and evidence identifiers from the source that are present in the representation.
4. If a representation has not been evaluated, or if any declared check could not be completed, then the transformation subsystem shall record the representation's evaluation status as unevaluated and shall not report it as low risk.
5. The transformation subsystem shall not add any provenance anchor to a transformed payload or representation beyond the evidence identifiers supplied by the privacy subsystem.
6. When an evidence identifier supplied to this subsystem itself matches a detectable identifier pattern, the transformation subsystem shall record that finding as an anchor-safety concern and shall leave the identifier unchanged.
7. The transformation subsystem shall state, wherever a representation evaluation is recorded, that the declared checks are structural, that they do not measure embedding invertibility, and that passing them is not a determination that the representation is non-identifying.

### Requirement 9: Leakage-Risk Evaluation and Threshold Gating

**Objective:** As a privacy reviewer, I want a transformed artifact scored for leakage risk by something that did not produce it, so that running a transformation is not itself the evidence that the output is safe.

#### Acceptance Criteria

1. When a transformed artifact is proposed for external use, the transformation subsystem shall evaluate it against a named, versioned rubric before the result is marked complete.
2. When the rubric is evaluated, the transformation subsystem shall score each of five declared dimensions — surviving direct identifiers, surviving quasi-identifiers, rare pattern rarity, reconstructability, and linkage potential — on a closed ordinal scale that includes an indeterminate value.
3. When the rubric evaluates the surviving-direct-identifier dimension, the transformation subsystem shall derive it by running detection independently over the transformed payload rather than by consuming any transformer's own record of what it changed.
4. When every dimension has been scored, the transformation subsystem shall derive an overall verdict drawn from a closed set comprising low, moderate, high, and unresolved, and a numeric aggregate score.
5. If any dimension scores indeterminate, or if evaluation itself fails, then the transformation subsystem shall set the overall verdict to unresolved and shall never set it to any other value in that situation.
6. If the overall verdict is worse than the configured maximum acceptable level, or the aggregate score exceeds the configured maximum acceptable score, or the verdict is unresolved, then the transformation subsystem shall return the transformation result marked incomplete so that the disclosure gate blocks the disclosure.
7. When a verdict is produced, the transformation subsystem shall record the per-dimension scores, the inputs that produced each score, the rubric version, and the thresholds in effect.
8. The transformation subsystem shall evaluate leakage risk in a component that does not depend on any transformer component, and no transformer shall supply, adjust, or override a dimension score.
9. The transformation subsystem shall state, wherever a verdict is recorded, that the rubric is a declared heuristic over ordinal categories, that its levels carry no probability semantics, and that a verdict is not a statistical disclosure-risk estimate.

### Requirement 10: Transformation Records and Operator-Observable Reporting

**Objective:** As an operator, I want a machine-readable record of what each transformation changed, so that a transformed artifact can be reviewed without re-reading the restricted source.

#### Acceptance Criteria

1. When a transformation runs, the transformation subsystem shall write one transformation record as a run-scoped artifact, identified by a content-addressed record identifier.
2. When a transformation record is written, it shall contain the profile identifier and version, the catalogue and rubric versions, per-category detection counts, the actions applied, the minimization counts, the temporal mode and scope identifier, the meaning-alteration verdict with its triggers, the leakage-risk verdict with its per-dimension scores, and the limits statement.
3. The transformation subsystem shall not place any original identifier surface form, any dropped retention unit's text, any surrogate-to-original mapping, or any secret value into a transformation record, a risk report, or a log line.
4. When a transformation result is returned to the privacy subsystem, the transformation subsystem shall include a compact reference to the transformation record together with the meaning-alteration verdict and the leakage-risk verdict, so that the record can be located from the disclosure decision.
5. When a run completes, the transformation subsystem shall write one run-level summary reporting counts by profile, by meaning-alteration verdict, by leakage-risk verdict, and by incomplete-result cause.
6. The transformation subsystem shall write records in an append-only manner and shall not modify or delete a previously written transformation record.
7. Where full attachment of the leakage verdict to the disclosure decision record would require a field the privacy subsystem does not define, the transformation subsystem shall attach it by reference and shall record that the direct attachment is deferred to a coordinated change in the privacy subsystem.

### Requirement 11: Synthetic Evaluation Corpus, Measured Baselines, and Declared Limits

**Objective:** As a responsible maintainer, I want every claim this subsystem makes to be measured on a fixture whose ground truth is known and whose limits are stated, so that no capability is believed more strongly than it was tested.

#### Acceptance Criteria

1. The transformation subsystem shall ship a synthetic evaluation corpus whose documents, identifier values, dates, and clinical content are entirely fabricated, generated from a declared seed, and accompanied by a ground-truth annotation of identifier spans, retention-unit necessity, and quasi-identifier tuples.
2. The transformation subsystem shall not include any protected health information, real patient data, real credential, or real institutional identifier in any fixture, and every fabricated identifier value shall be drawn from a range that is never issued in practice.
3. When the evaluation suite runs, the transformation subsystem shall compute per-category detection recall and precision against the corpus ground truth and shall compare them to a versioned baseline file.
4. If a measured per-category recall or precision falls below its recorded baseline, then the evaluation suite shall fail.
5. Where a category's detection-support level is unsupported, the transformation subsystem shall record its baseline recall as zero and shall exclude it from any aggregate accuracy figure reported as a capability.
6. The transformation subsystem shall ship a fixed limits statement declaring that all measurements derive from fabricated fixtures, that pattern-based detection does not find person names or free-text identifiers, that risk levels are ordinal heuristics rather than probabilities, and that nothing this subsystem produces is a de-identification guarantee, an expert determination, or a compliance conclusion.
7. The transformation subsystem shall embed the limits statement in every transformation record, risk report, and run-level summary it writes.
8. The transformation subsystem shall not produce any artifact whose name, field, or value asserts that a payload is de-identified, anonymized, safe, compliant, certified, or approved.
