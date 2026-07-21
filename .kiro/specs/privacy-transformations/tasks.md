# Implementation Plan

> **Upstream prerequisite**: `privacy-core` must be implemented before task 7.1, which is the only task that touches the upstream `TransformationResult` type. Tasks 1 through 6 and 8 are buildable and testable without `src/privacy/` present, by design — the coupling is confined to one module.

- [ ] 1. Foundation: package scaffolding, vocabularies, catalogues, and configuration

- [ ] 1.1 Create the transformation package with its limits statement and internal defect hierarchy
  - Establish `src/privacy_transformations/` as a new package alongside the existing source packages, with an empty public surface to be filled by later tasks
  - Define the fixed limits statement covering fabricated-fixture provenance of all measurements, the inability of pattern matching to find person names or free-text identifiers, the absence of probability semantics in risk levels, the meaning of an unchanged verdict, the structural-only nature of representation checks, and the absence of any de-identification, expert-determination, or compliance conclusion
  - Define the single owning list of assertion terms that may never appear as an artifact name, field, or value, permitted only inside the negating limits statement
  - Define the catalogue, profile-schema, record-schema, surrogate-handoff-schema, and rubric version constants, keeping the surrogate-handoff schema version independent of the record schema version because it is the cross-spec wire contract
  - Define the internal defect hierarchy that never crosses the provider boundary, each defect carrying a declared failure category
  - Observable: importing the package succeeds with no third-party dependency present, and a test asserts the limits statement names every one of the six declared gaps
  - _Requirements: 7.7, 8.7, 9.9, 11.6, 11.8_

- [ ] 1.2 Define all closed vocabularies and shared frozen records
  - Define the identifier-category vocabulary, detection-support levels, rewrite actions, temporal modes, meaning verdicts, leakage dimensions, leakage scores, leakage levels, evaluation status, and failure categories as closed vocabularies
  - Define the ordinal ordering for leakage scores and detection-support strictness as data rather than comparison chains
  - Define the shared immutable records for detected spans, rewrite plan entries, surrogate mappings, minimization and temporal reports, meaning assessments, representation evaluations, leakage verdicts, and the transformation record
  - Enforce that no record type has a field capable of carrying an original identifier surface form, a dropped unit's text, or a surrogate-to-original mapping
  - Observable: a test enumerates every vocabulary, asserts closure and ordering totality, and asserts every record is immutable and value-comparable
  - _Requirements: 1.4, 2.1, 2.2, 2.3, 4.5, 5.4, 6.1, 7.1, 8.2, 9.2, 9.4, 10.2_

- [ ] 1.3 Build the identifier and quasi-identifier catalogues with declared detection support
  - Assign every identifier category a detection-support level, shipping person names and geographic subdivisions as unsupported and every structured category as pattern-based
  - Define the placeholder form per category, the surrogate prefix, the age-bucket marker, and the suppressed-unit marker, each chosen to be itself detectable so a repeated pass changes nothing
  - Define the quasi-identifier category set and the tuple definitions over which rarity is later computed
  - Observable: a test asserts person names and geographic subdivisions report unsupported detection, that every catalogue category has exactly one support level and one placeholder form, and that no two placeholder forms collide
  - _Requirements: 2.1, 2.2, 3.1, 3.4, 6.5_

- [ ] 1.4 Implement the transformation profile format, its file, and its structural validation
  - Define a profile as a category-to-action mapping plus a minimization flag, a temporal mode, an identifier, a version, and a description
  - Ship the profile file with the named transformations that policy profiles reference, deliberately assigning no action to the unsupported categories
  - Validate structurally in code so that any defect — unknown category, unknown action, unknown mode, missing version, duplicate identifier — makes every profile in the file unusable rather than partially applied, and loading never raises
  - Screen profile identifiers and descriptions against the prohibited assertion terms at load time
  - Observable: loading a deliberately defective profile file returns zero usable profiles and a non-empty defect list without raising, and loading the shipped file returns every named transformation
  - _Requirements: 1.6, 2.4, 3.1, 4.1, 6.1, 11.8_

- [ ] 1.5 Add the configuration block, its loader, and the run-scoped output path
  - Add the new top-level configuration block covering profile path, output subdirectory, retention-unit boundary, operator gazetteers, temporal settings, leakage thresholds, meaning-alteration escalation, and the detection time budget
  - Register the new top-level key with the existing configuration loader so an unknown-key check does not reject it, and supply defaults
  - Expose a single settings loader that is the only module in the package reading configuration, so that later zero-argument provider construction remains legal without scattering implicit configuration
  - Add the run-scoped transformation output directory to the existing path utilities
  - Observable: loading the shipped configuration yields the documented defaults, an unknown key inside the block is rejected with a named error, and the run-scoped directory resolves under the current run's output root
  - _Requirements: 1.6, 2.5, 6.5, 6.7, 9.6_

- [ ] 1.6 Declare and enforce the package's dependency direction
  - Add exactly the forbidden-pair entries enumerated in the design's Modified Files list — this package must not import the agent, pipeline, PDF-extraction, quality-control, or provenance packages, and the privacy package must not import this one — asserting the registered set equals that enumerated list rather than checking a count
  - Add a forbidden-pair entry in both directions between this package and the disclosure package, since the surrogate handoff crosses as a versioned file and requires no import edge either way
  - Add a boundary test asserting that the module import order runs strictly left to right, that the leakage module imports no transformer module, and that only the entrypoint module may import the privacy package
  - Add an import-isolation test asserting every module except the entrypoint imports cleanly with the privacy, pipeline, and agent packages absent, and that no heavy optional dependency is reachable at any import depth
  - Observable: the dependency-direction suite passes with the registered pair set equal to the design's enumerated list — no more, no fewer, compared as a set and not as a count — and deliberately adding a transformer import to the leakage module makes the boundary test fail
  - _Requirements: 9.8_

- [ ] 2. Foundation: synthetic evaluation corpus and ground truth

- [ ] 2.1 Build the seeded synthetic corpus generator and its ground truth
  - Generate fabricated documents from a declared seed, drawing every identifier value from a documented never-issued or reserved range and every clinical passage from lorem-derived prose
  - Emit a manifest declaring the synthetic flag and the generator seed
  - Emit ground truth annotating identifier spans with category and offsets, marking each retention unit as field-supporting or not, and listing quasi-identifier tuples for rarity computation
  - Observable: running the generator twice with the same seed produces byte-identical corpus and ground-truth files, and the corpus contains at least one instance of every pattern-supported category
  - _Requirements: 11.1_

- [ ] 2.2 Assert corpus integrity mechanically
  - Assert every ground-truth identifier value matches one of the allowed reserved or never-issued patterns
  - Assert no fixture contains a credential-shaped value, a real institutional identifier, or any value outside the declared fabricated ranges
  - Observable: introducing a plausible-looking real identifier into the corpus makes the integrity test fail
  - _Requirements: 11.2_

- [ ] 3. Core: detection and span machinery

- [ ] 3.1 (P) Build the necessity vocabulary from the active extraction field map
  - Derive the necessity term set by tokenizing each field's name, definition, reviewer question, and category or example vocabulary from the active extraction field map, reusing the existing text-processing tokenizer rather than forking one
  - Implement the numeric, quantity, unit, percentage, interval, and statistical token recognizer shared by minimization and meaning assessment
  - Raise the field-map-unavailable defect when the map is missing, unreadable, or empty, never falling back to an empty vocabulary
  - Return an undetermined result rather than a negative one when a unit is empty or non-textual, so callers can retain on uncertainty
  - Observable: the vocabulary built from the shipped field map contains terms from all declared domain groups, and pointing the loader at an empty map raises the field-map-unavailable defect
  - _Requirements: 5.1, 5.5, 5.6, 7.3_
  - _Boundary: NecessityVocabulary_

- [ ] 3.2 (P) Implement the detection engine over the identifier catalogue
  - Implement pattern detectors for every pattern-supported category using bounded quantifiers, normalizing for matching with a length-preserving normalizer while reporting offsets against the original payload
  - Implement casefolded whole-token gazetteer matching for operator-supplied categories, attributing matches to the gazetteer support level
  - Implement independent absence verification over an arbitrary payload for a given category set, usable by both post-rewrite verification and independent risk scoring
  - Enforce the configured detection time budget, treating an overrun as a named defect rather than using partial results
  - Record per-category detection counts and the set of categories requested but not detectable, never reporting an undetectable category as a zero count
  - Observable: detection over the synthetic corpus finds every ground-truth span for pattern-supported categories, reports zero spans and unsupported status for person names with no gazetteer, and supplying a gazetteer for that category produces gazetteer-attributed spans
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.7, 3.2_
  - _Boundary: DetectionEngine_
  - _Depends: 1.3_

- [ ] 3.3 (P) Implement span precedence resolution and the offset-safe single-pass rewriter
  - Resolve overlapping spans by the declared precedence — longer span first, then stricter detection support, then catalogue declaration order — recording the suppressed span and the rule that suppressed it
  - Apply a rewrite plan in one ascending pass over the original payload into an output buffer, so no rewrite is ever matched against or applied to emitted text
  - Add a property test over arbitrary payloads and arbitrary non-overlapping plans asserting unrewritten regions survive byte-identically and each replacement appears exactly once
  - Observable: the property test passes, and a crafted overlapping-span fixture records the suppressed span with its precedence rule
  - _Requirements: 2.6, 3.3_
  - _Boundary: SpanPlanner_

- [ ] 4. Core: independent judging components

- [ ] 4.1 Implement representation governance for encoder and semantic outputs
  - Inherit the strictest contributing sensitivity label using an ordering supplied by the caller, treating labels as opaque ordered strings rather than redefining the vocabulary
  - Implement the three declared checks: whether raw source text is retained alongside the representation, the declared reconstructability factors, and whether any direct-identifier span is detectable in any textual component of the serialized representation
  - Implement the three structure-retention measures as proportions of numeric tokens, section labels, and evidence identifiers present relative to the source
  - Mark the evaluation unevaluated whenever any check cannot complete, and never report an unevaluated representation as low risk
  - Add no anchor of any kind, and record an anchor-safety finding when a supplied evidence identifier itself matches a detectable identifier pattern, leaving that identifier byte-identical
  - Attach the structural-checks statement to every evaluation
  - Observable: each of the three checks can be failed in isolation by a purpose-built fixture, and a representation whose serialization cannot be read reports unevaluated rather than passing
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_
  - _Depends: 3.2_

- [ ] 4.2 Implement the named leakage-risk rubric and its threshold gate
  - Score the five declared dimensions on the ordinal scale, deriving the surviving-direct-identifier dimension by running detection independently over the transformed payload rather than by consuming any transformer's change log
  - Compute rarity as the minimum equivalence-class size over the declared quasi-identifier tuples within the supplied corpus context, and score the dimension indeterminate rather than none when no corpus context is supplied
  - Derive reconstructability from the representation evaluation, scoring indeterminate when that evaluation is unevaluated
  - Score linkage from corpus-scoped surrogates, externally resolvable identifiers, and unshifted exact dates
  - Aggregate as the worst dimension, absorbing any indeterminate dimension into an unresolved overall verdict regardless of the others, and report the numeric aggregate with indeterminate dimensions excluded from the sum and named separately
  - Expose a disclosure-permission check as a lookup against a frozen permitted set, such that an unresolved verdict passes under no threshold configuration
  - Record per-dimension scores, the inputs behind each, the rubric version, the thresholds in effect, and the ordinal-heuristic statement
  - Observable: a table-driven test covers every documented per-dimension row, and a property test asserts every score combination containing an indeterminate dimension yields unresolved and fails the permission check
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.9_
  - _Depends: 3.2, 4.1_

- [ ] 5. Core: transformation actions and the rewrite engine

- [ ] 5.1 (P) Implement surrogate derivation and the ephemeral run key provider
  - Derive category-tagged surrogates by keyed hashing over the scope identifier, the category, and the normalized surface form, so that the same surface form in one scope always yields the same surrogate and different scopes never agree
  - Ensure no portion of the surface form appears in the surrogate and that the derivation is not invertible from the surrogate alone
  - Draw key material once per run from the platform secure random source, hold it only in memory, expose only a derived non-secret key-version identifier, and read no secret from configuration or the environment
  - Emit the surrogate mapping handoff record naming surrogates, categories, scope, and key version only, declaring stability as run-scoped, and discard the within-call surrogate lookup when the call returns
  - Keep the run-scoped stability declaration unconditional: surrogates are stable within the run only until persisted key material is supplied by the vault, which this package neither provides nor assumes
  - Observable: a property test asserts surrogate stability within a scope, divergence across scopes, and that no substring of length three or more from the input survives into the surrogate; a residue test asserts the mapping record contains no original value
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_
  - _Boundary: SurrogateGenerator_

- [ ] 5.2 Implement the three temporal modes with declared interval semantics
  - Derive one shift offset per temporal scope from the run key and the scope identifier, so the same scope yields the same offset within a run and different scopes differ
  - Implement shifting and relative modes such that every pairwise interval within a scope is preserved exactly, and suppression such that re-running date detection over the output yields no date span
  - Collapse ages above the configured cap into a single bucket marker regardless of value
  - Suppress rather than shift or relativize a detected date token that does not parse into a calendar date, counting it separately as unparsed
  - Record the mode, the scope, whether the mode preserves intervals, and the counts of dates transformed, ages bucketed, and unparsed dates suppressed
  - Observable: a property test over arbitrary date sets asserts exact interval preservation under shift and relative, and absence verification over a suppressed payload returns zero date spans
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_
  - _Depends: 3.2, 5.1_

- [ ] 5.3 (P) Implement the necessity-driven minimization pass
  - Segment the payload at the configured retention-unit boundary and record which boundary was used, so a retention ratio is always interpretable
  - Retain a unit when it matches the necessity vocabulary, when it carries a numeric token, or when necessity is undetermined, so that uncertainty always results in retention
  - Replace a dropped unit's text with a suppression marker that preserves the unit's evidence identifier, so no evidence identifier is lost
  - Report units examined, retained, and dropped, the retention ratio, and the dropped unit identifiers, never the dropped text
  - Observable: minimization over the annotated corpus retains every unit marked field-supporting, and every dropped unit's evidence identifier is still present in the output
  - _Requirements: 5.2, 5.3, 5.4, 5.6_
  - _Boundary: Minimizer_
  - _Depends: 3.1, 3.3_

- [ ] 5.4 Implement the detect-plan-rewrite-verify engine
  - Detect over the original payload, reject the transformation when the profile assigns a non-passthrough action to a category that is not detectable and has no gazetteer, resolve overlaps, build the plan by mapping each span's category to its action and computing its replacement, and apply the plan in a single pass
  - Verify three postconditions with distinct named causes: no span of a redacted category survives, no placeholder form was already present in the original payload, and every supplied evidence identifier is still present in the output
  - Confine all randomness to the run key provider so that identical key material, configuration, profile, and payload always produce a byte-identical payload and an identical set of findings
  - Observable: a property test asserts byte-identical output across repeated transformations, and each of the four rejection conditions is reproducible with its own named cause on a purpose-built fixture
  - _Requirements: 1.7, 2.4, 2.7, 3.1, 3.2, 3.3, 3.5, 4.1, 6.1_
  - _Depends: 3.2, 3.3, 5.1, 5.2_

- [ ] 6. Core: assessment and record artifacts

- [ ] 6.1 Implement the meaning-alteration assessor
  - Evaluate the four declared triggers against the rewrite plan, the minimization report, and the temporal report: a rewritten span overlapping a numeric or statistical token, a rewritten span overlapping a necessity-vocabulary term, any retention unit dropped, and a temporal mode that does not preserve intervals
  - Produce the three-valued verdict, treating an assessment that cannot examine the rewritten spans as undetermined and treating undetermined exactly as possibly altered for every downstream purpose
  - Record the triggering condition and the affected retention-unit identifiers, never the affected text, and attach the statement that an unchanged verdict asserts no clinical equivalence
  - Observable: each trigger yields possibly altered in isolation, none firing yields unchanged, and removing the plan yields undetermined
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  - _Depends: 3.1, 5.3, 5.4_

- [ ] 6.2 Implement append-only transformation records and the run summary
  - Write one content-addressed record per transformation into the run-scoped transformation directory, exposing append and read-only projections with no update, delete, or truncate path, and serializing concurrent writes
  - Include the profile, catalogue, and rubric versions, per-category detection counts, categories requested but undetectable, actions applied, the minimization and temporal reports, the surrogate mapping, the meaning assessment, the leakage verdict, the completion flag and failure cause, and the limits statement
  - Serialize only fields on a declared allowlist so that no original surface form, dropped-unit text, surrogate-to-original mapping, or secret can reach a record, summary, or log line
  - Produce the compact result reference string carrying the record identifier and both verdicts, and record that direct attachment to the disclosure decision is deferred to a coordinated upstream change
  - Write a run-level summary with counts by profile, meaning verdict, leakage level, and incomplete-result cause
  - Write the run-scoped surrogate handoff artifact at the fixed filename and location declared in the design, under its own schema version, aggregating every surrogate mapping emitted in the run and carrying the producer literal, the run-scoped stability declaration, and the limits statement, with an empty scope list when the run produced no surrogates so a consumer can distinguish an empty run from an absent producer
  - Observable: an identical transformation produces an identical record identifier, the record directory rejects any modification path, a residue test over a fixture with known values finds none of them in any artifact or log line, and the handoff artifact validates field-for-field against the pinned schema in the design with no field capable of carrying an original surface form
  - _Requirements: 4.5, 4.6, 4.7, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 11.7_
  - _Depends: 4.2, 6.1_

- [ ] 7. Integration with the privacy subsystem

- [ ] 7.1 Implement the zero-argument provider entrypoints and the complete-or-incomplete contract
  - Provide one zero-argument provider class per shipped transformation identifier, each binding a profile name to the shared runtime, and confine the import of the upstream privacy package and its result type to this module alone
  - Resolve settings, profiles, catalogue, vocabulary, and key provider lazily on first use so that a configuration or profile defect surfaces as an incomplete result on every call with a named cause rather than as a construction failure
  - Orchestrate the pipeline in the fixed order — resolve, detect, rewrite, minimize, verify, assess, evaluate, record — and return the compact record reference in the result detail
  - Catch every internal defect and every unexpected exception, convert each to an incomplete result carrying a declared failure category, and return the original evidence identifiers with no payload, so nothing partially transformed is ever returned
  - Return every supplied evidence identifier unchanged on a complete result, and construct or read no disclosure decision or policy anywhere in the package
  - Observable: a complete transformation returns a transformed payload with every evidence identifier preserved and a record reference in its detail, and each of the declared failure causes reproducibly returns an incomplete result with an empty payload and no raised exception
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.4, 5.5, 7.5, 9.6, 10.4_
  - _Depends: 1.4, 1.5, 4.2, 5.4, 6.1, 6.2_

- [ ] 7.2 Register the providers and verify dispatch through the upstream registry
  - Populate the upstream transformation-provider class-path list in the shipped configuration with this package's entrypoint classes
  - Verify end to end that the upstream registry loads the providers by class path, that a policy requiring a transformation now resolves instead of blocking for want of a provider, and that a rejected transformation still blocks with the upstream transformation-failed rationale
  - Confirm the upstream packet builder accepts the returned preserved-identifier set for every shipped profile
  - Observable: with providers registered, a document whose label maps to the transforming policy profile produces a permitted transformed disclosure, and with a deliberately rejecting profile the same document is blocked rather than disclosed raw
  - _Requirements: 1.1, 1.2, 1.5, 9.6_
  - _Depends: 7.1_

- [ ] 8. Validation: measured baselines, fail-closed coverage, and anti-overclaiming

- [ ] 8.1 Measure per-category detection accuracy against a versioned baseline
  - Compute per-category recall and precision over the synthetic corpus ground truth and compare each to a recorded floor in a versioned baseline file
  - Record a recall floor of zero for every unsupported category and exclude those categories from any aggregate figure reported as a capability
  - Fail the suite when any measured recall or precision falls below its recorded floor
  - Observable: lowering a pattern's coverage makes the baseline suite fail with the offending category named, and the reported aggregate excludes the unsupported categories
  - _Requirements: 11.3, 11.4, 11.5_
  - _Depends: 2.1, 2.2, 3.2_

- [ ] 8.2 Measure minimization necessity recall and pin transformation determinism
  - Assert measured recall of necessary content over the annotated corpus is exactly one, and report the drop ratio as a measurement rather than a guarantee
  - Assert two identical transformations of one document produce byte-identical payloads and identical record identifiers, which is the property the upstream prompt-cache stability depends on
  - Assert transforming an already-transformed payload changes nothing further, so placeholders and surrogates are idempotent under a second pass
  - Observable: the necessity-recall assertion fails if any field-supporting unit is dropped, and the determinism assertion fails if any nondeterministic input reaches the engine
  - _Requirements: 1.7, 3.4, 5.7_
  - _Depends: 5.3, 5.4, 6.2_

- [ ] 8.3 Add the cross-cutting fail-closed suite
  - Assert every documented blocking condition — configuration defect, profile defect, unsupported category with an assigned action, placeholder collision, missing field map, lost identifier, surviving redacted category, meaning-assessment failure, risk above threshold, unresolved risk, time-budget overrun, and unclassifiable internal defect — yields an incomplete result with its own named cause and an empty payload
  - Assert no configuration value, threshold setting, or code path converts an unresolved risk verdict or an undetermined meaning verdict into a passing state
  - Assert a record is written for every incomplete result, so a block is never silent
  - Observable: the suite enumerates every declared failure category and fails if a new one is added without a case
  - _Requirements: 1.3, 1.4, 2.4, 3.2, 3.5, 5.5, 7.5, 9.5, 9.6_
  - _Depends: 7.1_

- [ ] 8.4 Add the anti-overclaiming and residue scans
  - Scan this package's source, its profile file, its configuration block, and every artifact it writes for the prohibited assertion terms, permitting them only inside the negating limits statement
  - Assert the limits statement is embedded in every transformation record, risk verdict, representation evaluation, meaning assessment, and run summary
  - Assert no record, summary, or captured log line contains an original identifier surface form, dropped-unit text, a surrogate input, or key material, using a fixture whose values are known
  - Observable: adding an artifact field asserting a payload is de-identified, anonymized, or safe makes the scan fail, and removing the limits statement from any artifact makes the embedding assertion fail
  - _Requirements: 10.3, 11.6, 11.7, 11.8_
  - _Depends: 6.2, 7.1_
