# Implementation Plan

- [ ] 1. Foundation: shared models, the statistic contract, and configuration

- [ ] 1.1 Add agreement data models to the shared quality-control model module
  - Define the agreement dimension names, measurement levels, undefined reason codes, and the two rating sentinels for missing and not-reported ratings
  - Define the rater field output record, the comparison unit, the single-dimension rating sample, the statistic result, and the disagreement stratum
  - Define the page view with its heading-available and table-available signal flags, the page agreement record with its unavailable-signal list, the parser agreement threshold set, and the per-branch-pair parser agreement result carrying its own two parser identities
  - Define the published parser agreement report as a container holding a **list** of per-pair results plus the designated primary pair, the basis on which it was designated, and the list of available pairs, so a run with three branches has somewhere to put all three pair results instead of only one
  - Keep every new type a plain dataclass or module-level constant so the module stays free of behavior, matching the existing convention that this module holds only data containers and abstract base classes
  - Enforce that a statistic result carries a numeric value if and only if it carries no undefined reason
  - Observable completion: the new types import cleanly from the shared model module, the existing suite still passes, and a unit test asserts the value/undefined-reason exclusivity invariant raises on violation
  - _Requirements: 1.6, 3.1, 7.6, 9.8_
  - _Boundary: quality_control models_

- [ ] 1.2 Create the agreement package and the statistic contract
  - Create the agreement package and its statistics subpackage with the abstract statistic contract: canonical name, accepted measurement levels, rater-count range, a declared-assumptions accessor, an applicability precheck that names the failed assumption, and the compute method
  - Add the shared helpers every statistic needs: contingency table construction for two raters, marginal proportion vectors, and observed-agreement counting over a single dimension
  - Establish the worked-example fixture format: input rating table, expected published value, numeric tolerance, and a mandatory citation string; a fixture without a citation must fail to construct
  - Observable completion: a throwaway statistic subclass can be registered and computed end to end in a unit test, and a fixture missing its citation raises at construction
  - _Requirements: 3.1, 3.2, 3.6_
  - _Boundary: quality_control agreement, quality_control agreement statistics base_

- [ ] 1.3 Add the configuration surface and defaults
  - Extend the quality-control defaults with the inter-rater agreement implementation path, weighting scheme, difference function, prevalence imbalance threshold, and minimum unit count, keeping the existing metric-list and thresholds keys and their current defaults
  - Add the parser agreement configuration block with its enabled flag, example cap, both threshold groups, the optional primary-pair designation naming two parser identities in order, and the parser preference ordering used to derive a primary pair when none is configured, disabled by default
  - Mirror both blocks into the main configuration file, which has no inter-rater agreement block today
  - Keep both blocks nested under the existing quality-control key so no new top-level key registration is required
  - Establish these as the single source of the default numbers, and add a test that **discovers** every threshold-bearing constructor default across the agreement package by walking the package rather than enumerating a fixed list of call sites, then asserts each equals its counterpart in the loaded defaults
  - Write the discovery test so it also fails when a package default has no counterpart in configuration, so that defaults introduced by later tasks — the minimum unit count in the degenerate guard, the prevalence imbalance threshold in the prevalence-robust statistic, the example cap and thresholds in the parser analyzer — are caught automatically instead of relying on each of those tasks to remember to register itself
  - Observable completion: loading an existing unmodified configuration still succeeds and now returns the new keys at their documented defaults, the discovery-based equality test passes and demonstrably fails when a deliberately unregistered default is introduced, and a test asserts no new top-level key was introduced
  - _Requirements: 12.3, 12.4_
  - _Boundary: config defaults_

- [ ] 2. Core: the five statistic implementations

- [ ] 2.1 (P) Implement percent agreement
  - Compute the proportion of comparison units on which all present ratings are identical, for a single dimension
  - Report it under a name that identifies it as percent agreement, with the unit count it was computed over, and never under a bare "agreement" label
  - Accept any measurement level and two or more raters
  - Observable completion: unit tests show the value on hand-built samples including all-agree, all-disagree, and mixed cases, and the emitted result name and unit count are asserted
  - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - _Boundary: quality_control agreement statistics percent_agreement_

- [ ] 2.2 (P) Implement Cohen's kappa and weighted kappa
  - Compute Cohen's kappa from observed agreement and the chance agreement derived from marginal proportion products, for exactly two raters on a nominal or binary dimension
  - Compute weighted kappa over an explicit ordered category list using a disagreement weight matrix with a zero diagonal, supporting linear and quadratic weighting, defaulting to linear, and reporting the scheme in force
  - Assert the weight matrix diagonal is zero so the agreement-weight convention can never be silently substituted
  - Exclude units where either rater's rating is missing, and report the excluded count and the exclusion basis in the result details
  - Report both as not applicable, naming the failed assumption, when more than two raters are present
  - Add worked-example tests against the named published fixtures: unweighted and linear-weighted on the four-by-four sexual-fun table, quadratic-weighted on the three-by-three abstractors table, and the two paradox tables whose quadratic value is exactly zero
  - Observable completion: each worked-example test reproduces its published value within the stated tolerance, the more-than-two-raters case yields a not-applicable result with no numeric value, and a test asserts the wrong-convention value is never produced
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - _Boundary: quality_control agreement statistics cohen_kappa_

- [ ] 2.3 (P) Implement Gwet's AC1 and prevalence detection
  - Compute the prevalence-robust chance agreement from rater-averaged category proportions and derive the statistic on the same shell as kappa, including the weighted generalization needed for ordinal dimensions
  - Detect and record that a sample is prevalence-imbalanced when the observed prevalence of a category exceeds the configured threshold, recording the observed prevalence on the result; attaching the resulting caution to kappa results is the orchestrator's job, not this task's
  - Guard only the single-category and empty-sample cases, since the chance term cannot reach one for two or more categories
  - Add worked-example tests against the named published fixtures: the paradox two-by-two table, the three-by-three cross-validated table, and the weighted values for the two paradox tables
  - Observable completion: each worked-example test reproduces its published value within the stated tolerance, a deliberately imbalanced sample carries the imbalance marking and observed prevalence, and a test asserts the statistic is unchanged when a two-by-two table is transposed
  - _Requirements: 5.1, 5.2, 5.3, 5.5_
  - _Boundary: quality_control agreement statistics gwet_ac1_

- [ ] 2.4 (P) Implement Krippendorff's alpha
  - Build the coincidence matrix so each unit rated by two or more raters contributes its ordered rating pairs at the reciprocal-of-pairs weight, keeping partially-rated units in the computation
  - Count only pairable values in the sample size, excluding units carrying a single rating, and apply the small-sample correction to expected disagreement
  - Support nominal and ordinal difference functions, reporting which one was used along with the units and ratings actually used
  - Return an insufficient-data undefined result when fewer than two units carry ratings from at least two raters
  - Add worked-example tests against the canonical four-observer twelve-unit table for both difference functions, the small binary table, and the three-by-three cross-validated table
  - Observable completion: each worked-example test reproduces its published value within the stated tolerance, a test asserts the pairable-value count on the canonical fixture excludes the singly-rated unit, and a sample with partially-rated units yields a value rather than dropping those units
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - _Boundary: quality_control agreement statistics krippendorff_alpha_

- [ ] 3. Core: normalization, gating, stratification, and parser comparison

- [ ] 3.1 Implement the degenerate-case guard
  - Evaluate an empty comparison set, a single rater, a unit count below the configured minimum, and zero observed variance in that fixed order, returning the first matching reason
  - Keep the zero-variance case distinct so percent agreement is still reported while every chance-corrected statistic is undefined
  - Own the undefined reason codes exclusively, so no statistic implementation invents its own
  - Never substitute a default numeric value for a statistic that could not be computed
  - Observable completion: a parametrized test over all four degenerate inputs asserts the exact reason code, that no numeric value is present, and that the all-agree case still reports percent agreement of one
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_
  - _Boundary: quality_control agreement degenerate_

- [ ] 3.2 Implement the statistic registry and metric-name resolution
  - Resolve a configured list of metric names to statistic implementations, case-insensitively, with names asserted unique at construction
  - Return an unsupported result and log a warning naming any metric name that is not implemented, then continue resolving the remaining names
  - Treat an empty configured list as percent agreement only
  - Guarantee that the same comparison set produces identical results on repeated runs
  - Observable completion: a test with a mixed list of valid and invalid names produces results for the valid ones plus an unsupported entry for each invalid one, and a warning naming each invalid name is captured
  - _Requirements: 3.3, 3.4, 3.5_
  - _Boundary: quality_control agreement registry_
  - _Depends: 2.1, 2.2, 2.3, 2.4_

- [ ] 3.3 Implement comparison normalization
  - Group rater field outputs by document and field and emit one comparison unit per group, identifying the document, field, raters, and each rater's rating
  - Derive a rating on each of the five agreement dimensions and record each dimension's measurement level
  - Represent a not-reported answer as an explicit not-reported rating and an absent rater as a missing rating, retaining the unit in both cases
  - Apply the same rules to both raters so the result is independent of which rater is listed first, and record every applied normalization rule on the unit
  - Reject duplicate rater-document-field triples with an error naming the collision
  - Observable completion: tests assert that swapping the two raters leaves every emitted rating set equivalent, that not-reported and missing produce different sentinels, that one-sided fields survive, and that the applied rule names appear on the unit
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7_
  - _Boundary: quality_control agreement comparison_

- [ ] 3.4 Implement disagreement stratification
  - Produce one stratum per axis-and-key pair across field, field group, document, parser-risk status, and criticality, each carrying its unit count, disagreement rate, and statistics
  - Report an undefined stratum key with an availability marker when criticality is unavailable, and likewise when no location association exists to derive parser-risk status, rather than defaulting either
  - Emit under-populated strata with their unit count and undefined statistics rather than omitting them
  - Name the agreement dimension each stratum's disagreement was measured on
  - Observable completion: a test over a mixed unit set asserts that summed stratum unit counts equal the total on every axis, and that missing criticality and missing location each produce an undefined stratum with the correct availability marker
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  - _Boundary: quality_control agreement stratification_
  - _Depends: 3.1, 3.2_

- [ ] 3.5 (P) Preserve structural block types and build per-page views
  - This is an explicit two-boundary task: it edits the structured-parser payload extraction in the quality-control pipeline module *and* creates the page-view builder in the agreement package. Both halves are listed in the boundary annotation
  - Stamp each block emitted by the structured-parser payload extraction with an optional structural type of heading, table, figure, or paragraph; the extraction already visits heading and figure elements separately and currently discards the distinction
  - Distinguish tables from other figures by the figure element's type attribute, not by a separate element, and record in the code comment that the existing figure loop only emits a block when a caption element is present, so the derived table count is a captioned-table count and a caption-less table yields no signal
  - Keep the change additive: block dictionaries are not described by the structural schema at all, so nothing needs to be revalidated, and no existing reader inspects the new key
  - Before making the change, add a characterization test pinning the current extraction output for a fixed structured-parser payload — full text, per-page texts, and block count and order — since no existing test exercises either payload-extraction helper today
  - Add a page-view builder that buckets a branch's blocks by page, joins their text, and derives per-page headings and table counts from the structural type tag
  - When a branch's blocks carry no structural type at all, mark the heading and table signals unavailable on that page view rather than emitting an empty heading list, so a genuine absence and an uninformative parser are distinguishable
  - Derive nothing else: no heading heuristic, no table detection, no font analysis, and no import from the extraction package
  - Observable completion: the characterization test passes both before and after the tagging, proving full text, per-page texts, and block count and order are unchanged; a structured-parser payload produces page views with headings and tables marked available and populated; and an untagged block-list payload produces page views with both marked unavailable
  - _Requirements: 9.8, 9.9_
  - _Boundary: quality_control agreement page_views, quality_control tei payload extraction_

- [ ] 3.6 Implement the per-page parser agreement metrics
  - Compare two page views and compute token overlap, numeric-token overlap, table-detection agreement, section-heading agreement, and text-presence agreement for each shared page
  - Report a metric as undefined when either side marks its signal unavailable, and record which signal was missing on the page record
  - Record a text-presence disagreement naming the parser that produced no text, and retain differing numeric tokens as examples up to the configured limit
  - Report an undefined single-source result when only one parser output exists, and report pages covered by only one parser separately from shared-page metrics
  - Return an undefined metric rather than full agreement when both sides of an overlap metric are empty, and name both parser identities on every record and on the per-pair result itself
  - Add the multi-pair entry point: given the per-branch page-view mapping, emit exactly one per-pair result for every unordered pair of branches, so three branches yield three results and none is dropped, merged, or overwritten; order the pair list deterministically by the two parser identities
  - Resolve the primary pair on the emitted report: the configured pair when both its parsers were analyzed, otherwise the first pair in the configured preference ordering whose parsers were both analyzed with a warning naming the missing configured parsers, and no pair at all when fewer than two branches exist; record which basis was used
  - Build no index over the pair list and no cross-pair rollup — the page-indexed mapping downstream risk consumption wants belongs to the routing spec and is not built here
  - Observable completion: tests over hand-built page views produce full agreement for identical pages, a named text-presence disagreement for a blank page, retained numeric examples for a differing number, an undefined rather than perfect result for the both-empty case, an undefined heading metric with the missing signal named when one side is untagged, and a three-branch mapping producing three per-pair results with the configured primary pair designated and the preference-ordering fallback exercised
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_
  - _Boundary: quality_control agreement parser_agreement_
  - _Depends: 3.5_

- [ ] 3.7 Implement parser risk flags and threshold resolution
  - Mark a page parser-risky when any defined metric falls below its configured risky threshold, recording which metrics failed, with defaults of token overlap below 0.80, numeric-token overlap below 0.95, any table-detection disagreement, or any text-presence disagreement
  - Publish a skip-parser-counterfactual signal only when every metric is defined and meets its configured high-agreement threshold, defaulting to token overlap at or above 0.95 with full numeric, table, heading, and text-presence agreement
  - Never let an undefined metric mark a page risky or count toward skip eligibility, and record the unavailable signal on the page instead
  - Publish a counterfactual-audit-recommended flag for every risky page and additionally for any page with numeric-token or table-detection disagreement
  - Record the threshold values in effect on every emitted record, and publish all three flags as data without triggering re-extraction, verification, or review
  - Observable completion: tests assert that a page is never simultaneously risky and skip-eligible, that a page above the risky floor but with one differing number still gets the audit flag, that a page with an unavailable heading signal is neither risky nor skip-eligible, and that the effective thresholds appear on each record
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - _Boundary: quality_control agreement parser_agreement_
  - _Depends: 3.6_

- [ ] 3.8 Implement the default agreement report implementation
  - Add a concrete inter-rater metrics implementation that receives comparison units and configuration at construction, orchestrates the registry, guard, and stratifier, and records branch status concordance from the quality reports it is given
  - Attach the prevalence caution to every kappa-family result computed over a sample the prevalence detector marked imbalanced, so the cross-statistic annotation lives in one place rather than inside either statistic
  - Report percent agreement alongside every chance-corrected statistic, never in place of one, and carry the declared assumptions, unit counts, and undefined reason codes through to the output
  - Serialize the whole report to plain nested dictionaries so it survives the existing artifact writer
  - Export the new implementation from the builtin-implementations package and the quality-control package so callers can name it by fully-qualified path
  - Write nothing outside itself: no branch status, no adjudication decision, no reconciled record
  - Observable completion: a unit test builds the report from hand-made units, asserts the serialized form round-trips through JSON, asserts an imbalanced sample produces a caution on the kappa result but not on the prevalence-robust one, asserts the implementation is importable from both package entry points, and asserts a bundle's branch status, decision, and unified record are untouched
  - _Requirements: 2.2, 3.1, 3.2, 5.4, 12.1, 13.1, 13.3_
  - _Boundary: quality_control builtin_impls agreement_report_
  - _Depends: 3.2, 3.3, 3.4_

- [ ] 4. Renaming the mislabelled ratios

- [ ] 4.1 (P) Rename the pass/fail comparison in the default inter-rater report
  - Make the pass/fail comparison dictionary a status-concordance field and expose the previously shipped name as a read-only alias of it so existing readers keep working with unchanged meaning
  - Rewrite the class and module documentation so the value is described as pass/fail status concordance rather than agreement
  - Rewrite the matching export docstring line in the builtin-implementations package initializer, which currently describes this report as the default inter-rater agreement report, so the later rename guard has no unowned offender to hit
  - Leave the pairwise comparison arithmetic exactly as it is
  - Observable completion: a new test asserts the alias and the field are the same object, that the computed values match the previous behaviour on a fixed set of reports, and that neither the module nor the package initializer describes the value as agreement
  - _Requirements: 11.1, 11.4, 11.5_
  - _Boundary: quality_control builtin_impls inter_rater_report, quality_control builtin_impls package docstring_

- [ ] 4.2 (P) Rename the branch-scoring overlap term
  - Rename the bag-of-words overlap helper and the branch quality score field to names identifying the quantity as a lexical overlap fraction
  - Leave the weighting and every arithmetic step of the composite score untouched
  - Update the accompanying documentation so the term is no longer described as agreement
  - Observable completion: a preservation test asserts the composite score returns numerically identical values for a fixed set of branch inputs before and after the rename, and the existing branch-scoring tests pass against the new names
  - _Requirements: 11.2, 11.4_
  - _Boundary: quality_control adjudicator_

- [ ] 4.3 (P) Rename and version the extractor-agreement check report
  - Replace the match-to-total ratio key with a name identifying it as a sentence match rate, keeping the same computation
  - Stamp the report with a version number and emit a status key on the computed path so the enabled and disabled paths return the same key set
  - Update the existing extractor-agreement check tests in this same task: the assertions naming the old ratio key and the fixed report-key contract, including the property test of the ratio formula, must move to the renamed key and the versioned shape so the suite is never left failing
  - Update the module documentation so the ratio is not described as an agreement statistic
  - Observable completion: the updated extractor-agreement tests pass against the renamed key, and a test asserts the enabled and disabled paths return identical key sets including the version and status keys
  - _Requirements: 11.3, 11.4, 11.5_
  - _Boundary: quality_control checks extractor_agreement_

- [ ] 5. Integration: pipeline wiring

- [ ] 5.1 Add implementation loading and the stage entry point
  - Add a loader that resolves the configured fully-qualified implementation path, importing and instantiating it and raising an import error naming the configured path on failure, following the existing text-processor loading pattern
  - Add the agreement entry point that builds the configured implementation over a set of comparison units and returns its serialized form, and treat it as a **documented public entry point rather than a pipeline-internal helper**: re-export it, together with the comparison unit, the rater field output record, and the normalizer, from the quality-control package initializer so any caller can reach it by a stable import
  - Document and enforce its contract: pure and side-effect free, no I/O, no argument mutation, no global state, no bundle, branch status, decision, unified record, or metrics-hierarchy write — publication is the caller's job — and rater-source agnostic, so the two raters may be two extraction agents, an external human reference and a system output, or two runs of the same agent
  - Name its consumers in the docstring: this spec's own inter-rater stage, the multi-agent extraction spec, and the evaluation harness, which needs it for human-reference-versus-system agreement and has no other reachable path to this computation
  - Keep it importing only the shared model module, the agreement package, and the standard library, so a caller in any other package depends inward on quality control and no dependency-direction rule is broken
  - Keep the existing stub function's parameter list and every one of its return keys, including its deferred decision value, and change only its metric values so configured names resolve through the registry instead of resolving to nothing
  - Correct the stub's documentation reference to the configuration path the code actually reads
  - Observable completion: the existing preservation test for the stub still passes unmodified, an unknown metric name resolves to an unsupported entry rather than a null, a bogus implementation path raises an import error naming the path, and a test calls the public entry point directly — outside the pipeline and with no bundle in existence — over units whose two raters are an external reference and a system output, asserting the documented dict comes back, that two identical calls return equal dicts, and that the input unit list is not mutated
  - _Requirements: 3.3, 3.4, 12.5_
  - _Boundary: quality_control iaa_calculator_
  - _Depends: 1.3, 3.8_

- [ ] 5.2 Wire the inter-rater agreement stage and publish its results
  - Replace the hard-coded construction of the default report in the inter-rater agreement stage closure with the configured loader, then compute and publish the serialized report under a new inter-rater agreement key in the metrics hierarchy
  - Report the per-field statistics as undefined for absent comparison data while no second rater exists, so the stage is honest rather than silently perfect
  - Catch a failed load or computation, log an error naming the cause, and record a not-computed result rather than aborting the run
  - Leave branch status, the adjudication decision, and the reconciled record untouched
  - Observable completion: a quality-control run produces the new key in the metrics hierarchy, the run completes with no metrics configured, and a test asserts branch statuses and the decision are identical with and without the stage enabled
  - _Requirements: 12.1, 12.4, 13.3_
  - _Boundary: quality_control pipeline wiring_
  - _Depends: 5.1_

- [ ] 5.3 Wire parser agreement, publish its results, and update the metrics-hierarchy key contract
  - Build per-page views for every branch using the page-view builder, hand the whole per-branch mapping to the analyzer's multi-pair entry point, and publish the resulting **list-shaped** report under a new parser agreement key in the metrics hierarchy: one entry per branch pair plus the resolved primary pair, its designation basis, and the list of available pairs
  - Write a skipped record with an empty pair list when parser agreement is disabled so the key is always present and its absence never has to be inferred
  - Update the two existing assertions that the metrics hierarchy holds exactly three keys, in the unit test and in the property test, to the new five-key set, in this same task so the suite is never left failing
  - Observable completion: a quality-control run with parser agreement enabled over three branches publishes three per-pair entries, each holding one record per shared page, with the primary pair named; the whole hierarchy serializes through the artifact writer; and the updated key-set assertions pass in both places
  - _Requirements: 12.2, 12.4_
  - _Boundary: quality_control pipeline wiring_
  - _Depends: 1.3, 3.7_

- [ ] 6. Validation

- [ ] 6.1 (P) Add the property-based test suite for the agreement core
  - Assert percent agreement stays within its range and equals one exactly when every unit's ratings are identical
  - Assert every chance-corrected statistic is bounded above by one and is never clamped from below, so a legitimately negative value survives
  - Assert a statistic result carries a value if and only if it carries no undefined reason, over arbitrary generated samples
  - Assert every statistic is invariant under permutation of unit order and under renaming of raters, and that summed stratum unit counts equal the total on every axis
  - Assert a page is never both parser-risky and skip-eligible, and that a page with an unavailable signal is never skip-eligible
  - Observable completion: the property suite runs within the fast test selection and every property holds across generated samples
  - _Requirements: 2.1, 3.6, 7.5, 8.1, 8.2, 10.1, 10.3_
  - _Boundary: tests quality_control agreement properties_
  - _Depends: 2.1, 2.2, 2.3, 2.4, 3.4, 3.7_

- [ ] 6.2 (P) Add cross-statistic consistency and implementation-trap tests
  - Build the imbalanced two-by-two case where observed agreement is high, the kappa-family value collapses toward zero, and the prevalence-robust value stays high, asserting all three values plus the imbalance marking and the caution attached to the kappa result
  - Add the cross-validated three-by-three fixture that exercises percent agreement, Cohen's kappa, the prevalence-robust statistic, and Krippendorff's alpha from one published input, so a transcription error in any one implementation shows up as disagreement with the others
  - Assert the weight-direction trap value is never produced, and assert the pairable-value count on the canonical alpha fixture excludes the singly-rated unit
  - Observable completion: all three tests pass and each carries the citation for the case it encodes
  - _Requirements: 4.5, 5.4, 6.4_
  - _Boundary: tests quality_control agreement fixtures_
  - _Depends: 2.2, 2.3, 2.4, 3.8_

- [ ] 6.3 (P) Add rename-guard, behaviour-preservation, and boundary regression tests
  - Add a source-scanning guard banning the specific mislabels this feature removes, namely the branch-scoring overlap field and helper names, the extractor-agreement ratio key name, and any docstring under the quality-control package describing the pairwise pass/fail comparison as agreement, including the builtin-implementations package initializer
  - Encode the deliberate exceptions as an explicit allowlist, since each survives by design: the extractor-agreement check class and module names, the retained metric-list configuration key, the metrics-hierarchy key that carries the check's report, and the alignment record's agreement field, whose values are the categorical labels full, partial, divergent, and one-engine-only rather than a chance-uncorrected ratio
  - Assert the branch composite score is numerically identical to its pre-rename behaviour for a fixed input set
  - Re-run the cross-package dependency-direction suite and the quality-control text-processor separation suite to confirm the new package introduces no forbidden import
  - Observable completion: the guard fails on a deliberately reintroduced banned name, passes with every allowlisted exception present in the tree, the composite-score preservation test passes, and both boundary suites pass
  - _Requirements: 11.2, 11.4, 13.1, 13.3_
  - _Boundary: tests quality_control rename_guard_
  - _Depends: 4.1, 4.2, 4.3_

- [ ] 6.4 Add end-to-end integration tests for the wired pipeline
  - Assert a run with parser agreement enabled produces per-page records that survive serialization to the per-document artifact, that every branch pair appears in the published pair list, and that the primary pair is named on the report
  - Assert a run with no metrics configured and parser agreement disabled completes with both new keys present and reporting not-computed or skipped
  - Assert a configured alternative inter-rater implementation is loaded and used, and that a bogus path yields a logged error and a recorded not-computed result rather than a failed run
  - Assert that a field whose evidence fails a deterministic validity check is still reported as failing regardless of the agreement value, and that low agreement produces no escalation action
  - Observable completion: all four scenarios pass against the wired pipeline and the full fast test suite is green
  - _Requirements: 12.1, 12.2, 12.4, 12.5, 13.2, 13.4_
  - _Boundary: tests quality_control integration_
  - _Depends: 5.2, 5.3_
