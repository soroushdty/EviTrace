# Requirements Document

## Project Description (Input)
EviTrace reports a number labelled "agreement" that is not an agreement statistic. Researchers, reviewers, and anyone reading a QC report are handed a chance-uncorrected ratio under a name that implies chance correction. For a tool whose value proposition is auditability, publishing a mislabelled reliability figure is a credibility defect, and it is a live violation of `xtrace-toolkit` R-QC-3 ("SHALL compute a named, statistically-defined inter-rater agreement statistic (e.g. Krippendorff's α) and SHALL NOT label a binary pass/fail ratio as 'agreement'").

Three sites currently mislabel a ratio as agreement — `src/quality_control/builtin_impls/inter_rater_report.py` (binary pass/fail per extractor pair), `src/quality_control/adjudicator.py::_compute_agreement_score` (bag-of-words overlap feeding a 0.15-weighted branch-scoring term), and `src/quality_control/checks/extractor_agreement.py` (`agreement_rate` = matches / total) — while `src/quality_control/iaa_calculator.py` is a stub that reads metric names from config and returns `{metric: None}`.

This spec makes the IAA stage compute named, chance-corrected statistics with declared assumptions, reported alongside the raw percent agreement they replace; renames every remaining ratio to what it actually measures; adds parser-agreement metrics and the parser-risky page signal; and resolves roadmap Open Question 4 (what threshold triggers a parser-counterfactual audit). All work stays inside `src/quality_control/`, which must not import `agents`, `pipeline`, or `pdf_extractor`.

## Boundary Context

- **In scope**: Normalizing two raters' per-field outputs into comparable rating vectors across five agreement dimensions; percent agreement reported under its own name; the four named chance-corrected statistics (Cohen's kappa, weighted kappa, Gwet's AC1, Krippendorff's alpha) with declared assumptions and explicit degenerate-case results; disagreement breakdowns stratified by field, field group, document, parser-risk status, and criticality; parser agreement metrics (token overlap, numeric-token overlap, table detection, section heading, text presence); marking pages parser-risky and publishing both the risky flag and the high-agreement skip signal; the parser-counterfactual audit threshold (roadmap Open Question 4); and renaming the three existing mislabelled ratios.
- **Out of scope**: The escalation policy that consumes low agreement (increase verification, dual extraction, or manual review) — that behavior belongs to `multiagent-extraction`; this feature supplies the numbers, the flags, and the configuration surface only. Acting on parser-risk flags (stricter extraction or verification for critical fields, triggering the counterfactual audit or human review) — owned by `evidence-routing`. Producing the second independent extraction that makes two-rater comparison possible — owned by `multiagent-extraction`. Human-versus-agent benchmark collection — owned by `evaluation-harness`. Deterministic single-parser quality metrics (character counts, `(cid:)` artifacts, empty pages) and the parser QC report artifact — inputs to and consumers of this feature, not produced by it. Changing which branch adjudication selects or how reconciliation merges: only the mislabelled scoring term is renamed, its weight and numeric behavior unchanged. Any reporting or review user interface.
- **Adjacent expectations**: This feature assumes deterministic evidence validity checks continue to run and remain authoritative — no agreement value may relax or override them. It assumes the existing four-stage quality-control sequence (rater → inter-rater agreement → adjudicator → reconciler) and its report-passing contract stay in place. It expects the per-field criticality designation to come from `corpus-and-schema-builder` and the field-to-page routing association to come from `evidence-routing`/`multiagent-extraction`; while either is absent, the corresponding breakdown dimension is reported as undefined rather than guessed. It expects existing consumers of the currently shipped inter-rater report to keep reading a documented field whose meaning has not silently changed.

## Requirements

### Requirement 1: Comparison Normalization

**Objective:** As a researcher comparing two independent extractions of the same paper, I want each pair of field outputs turned into a comparable pair of ratings before any statistic is computed, so that reported agreement measures the same thing for every field.

#### Acceptance Criteria

1. When two raters have both produced an output for the same field of the same document, the agreement statistics module shall produce a normalized comparison unit identifying the document, the field, the two rater names, and the rating each rater contributed.
2. The agreement statistics module shall derive, for each comparison unit, a rating on each of five agreement dimensions: extracted value, supporting evidence, confidence, support status, and not-reported status.
3. When a rater reports a field as not reported, the agreement statistics module shall record that as an explicit not-reported rating rather than as a missing rating.
4. When only one of the two raters produced an output for a field, the agreement statistics module shall record the other rater's rating as missing and shall retain the unit rather than discarding it.
5. When the same field is compared for two raters, the agreement statistics module shall apply the same normalization rules to both raters' outputs, and the normalization shall not depend on which rater is designated primary.
6. The agreement statistics module shall record, for each dimension, the measurement level of its ratings as nominal, ordinal, or binary, so that consumers can tell which statistics are applicable.
7. When normalization discards or transforms rater content in order to compare it, the agreement statistics module shall record which normalization rule was applied so the comparison is auditable.

### Requirement 2: Percent Agreement Reported Under Its Own Name

**Objective:** As a reviewer reading a quality report, I want raw percent agreement to be labelled percent agreement, so that I am not shown a chance-uncorrected number under a name implying chance correction.

#### Acceptance Criteria

1. When a set of comparison units is evaluated, the agreement statistics module shall compute the observed proportion of units on which the two raters gave identical ratings and shall report it under a name that identifies it as percent agreement.
2. The agreement statistics module shall report percent agreement alongside, and never as a substitute for, the chance-corrected statistics computed for the same comparison set.
3. The agreement statistics module shall not label percent agreement, or any other chance-uncorrected ratio, with an unqualified name such as "agreement" or "agreement score".
4. When percent agreement is reported, the agreement statistics module shall also report the number of comparison units it was computed over.

### Requirement 3: Named Statistics with Declared Assumptions and Metric Selection

**Objective:** As a researcher who may publish these numbers, I want every reported statistic to carry its name and its assumptions, so that a reader can judge whether the statistic was applicable to the data it was computed on.

#### Acceptance Criteria

1. When the agreement statistics module reports a statistic value, it shall report with it the statistic's canonical name, the measurement level it assumes, the number of raters it assumes, and the number of comparison units used.
2. When a requested statistic's assumptions are not satisfied by the comparison set, the agreement statistics module shall report the statistic as not applicable together with the assumption that failed, and shall not report a numeric value for it.
3. When the configured list of agreement metric names is empty, the agreement statistics module shall compute percent agreement only and shall report no chance-corrected statistic.
4. If a configured metric name is not one the module implements, then the agreement statistics module shall report that name as unsupported, shall log a warning identifying it, and shall continue computing the remaining configured metrics.
5. The agreement statistics module shall compute only the statistics named in configuration, and shall produce identical values for the same comparison set on repeated runs.
6. When a statistic is reported, the agreement statistics module shall report the value on the statistic's own defined range, without clamping a legitimately negative chance-corrected value to zero.

### Requirement 4: Cohen's Kappa and Weighted Kappa

**Objective:** As a researcher measuring agreement between two extraction agents on categorical and ordered labels, I want Cohen's kappa and weighted kappa, so that agreement on those labels is corrected for chance and, for ordered labels, credits near-misses appropriately.

#### Acceptance Criteria

1. When exactly two raters have rated a comparison set on a nominal dimension, the agreement statistics module shall compute Cohen's kappa for that set.
2. When exactly two raters have rated a comparison set on an ordered dimension such as confidence, the agreement statistics module shall compute weighted kappa using a configured disagreement weighting scheme and shall report which weighting scheme was used.
3. The agreement statistics module shall support at least linear and quadratic disagreement weighting for weighted kappa, and shall default to a documented one of them when configuration does not specify a scheme.
4. If more than two raters have rated the comparison set, then the agreement statistics module shall report Cohen's kappa and weighted kappa as not applicable for that set.
5. When Cohen's kappa or weighted kappa is computed, its value shall match the value published for a documented worked example of the same statistic to within a stated numeric tolerance.
6. When ratings are missing for one rater on some units, the agreement statistics module shall report how many units were excluded from the kappa computation and on what basis.

### Requirement 5: Prevalence-Robust Agreement for Imbalanced Binary Labels

**Objective:** As a researcher measuring agreement on a label that is almost always the same value, I want a prevalence-robust statistic, so that near-perfect agreement is not reported as near-zero reliability by a statistic known to collapse under imbalance.

#### Acceptance Criteria

1. When a comparison set has a binary dimension rated by two raters, the agreement statistics module shall compute a prevalence-robust chance-corrected statistic for that dimension.
2. When both a kappa-family statistic and the prevalence-robust statistic are computed for the same binary dimension, the agreement statistics module shall report both values rather than replacing one with the other.
3. When the prevalence of one category exceeds a configured imbalance threshold, the agreement statistics module shall record that the comparison set is prevalence-imbalanced, together with the observed prevalence.
4. When a comparison set is recorded as prevalence-imbalanced, the agreement statistics module shall record a caution alongside any kappa-family value reported for that set, stating that the kappa value is affected by prevalence.
5. When the prevalence-robust statistic is computed, its value shall match the value published for a documented worked example of the same statistic to within a stated numeric tolerance.

### Requirement 6: Krippendorff's Alpha

**Objective:** As a reviewer checking that EviTrace satisfies its own stated reliability requirement, I want Krippendorff's alpha computed on the general case, so that agreement is reported by a named statistic that tolerates missing ratings and more than two raters.

#### Acceptance Criteria

1. When a comparison set has two or more raters, the agreement statistics module shall compute Krippendorff's alpha for it.
2. When some units are rated by only a subset of the raters, the agreement statistics module shall compute Krippendorff's alpha over the available ratings rather than dropping the affected units wholesale, and shall report the number of units and ratings actually used.
3. The agreement statistics module shall support at least nominal and ordinal difference functions for Krippendorff's alpha and shall report which difference function was used.
4. When Krippendorff's alpha is computed, its value shall match the value published for a documented worked example of the same statistic to within a stated numeric tolerance.
5. When fewer than two units carry ratings from at least two raters, the agreement statistics module shall report Krippendorff's alpha as undefined for insufficient data rather than returning a numeric value.

### Requirement 7: Degenerate and Insufficient-Data Cases

**Objective:** As an operator reading agreement output for a small or uniform comparison set, I want degenerate cases named explicitly, so that "no information" is never presented as perfect agreement.

#### Acceptance Criteria

1. When a comparison set is empty, the agreement statistics module shall report every requested statistic as undefined for insufficient data and shall not report a numeric value for any of them.
2. When a comparison set contains ratings from only one rater, the agreement statistics module shall report every inter-rater statistic as undefined for a single rater and shall not report a numeric value for any of them.
3. When every unit in a comparison set carries the same rating from every rater, the agreement statistics module shall report percent agreement as one, shall report each chance-corrected statistic as undefined for zero observed variance, and shall state which degenerate condition applied.
4. When a comparison set falls below a configured minimum number of units, the agreement statistics module shall report the chance-corrected statistics as undefined for insufficient data and shall report the observed unit count.
5. The agreement statistics module shall never emit a numeric value of one, or any other default value, to stand in for a statistic it could not compute.
6. When a statistic is reported as undefined, the agreement statistics module shall report a reason code identifying which degenerate condition applied.

### Requirement 8: Disagreement Breakdown and Stratification

**Objective:** As a reviewer deciding where to spend review effort, I want disagreement broken out by field, group, document, parser risk, and criticality, so that I can see which parts of the extraction are unreliable rather than only a single corpus-level number.

#### Acceptance Criteria

1. When agreement has been computed for a run, the agreement statistics module shall report disagreement rates stratified by field, by field group, and by document.
2. When agreement has been computed for a run, the agreement statistics module shall report disagreement rates stratified by the parser-risk status of the location the compared evidence came from, and by the criticality designation of the field.
3. If the criticality designation for a field is unavailable, then the agreement statistics module shall report the criticality stratum for that field as undefined rather than assuming a default criticality.
4. If the parser-risk status for a compared unit is unavailable because no location association exists, then the agreement statistics module shall report the parser-risk stratum for that unit as undefined rather than treating it as not risky.
5. When a stratum contains too few comparison units for its chance-corrected statistics to be defined, the agreement statistics module shall report the stratum with its unit count and the undefined result rather than omitting the stratum.
6. When disagreement is reported for a stratum, the agreement statistics module shall report which agreement dimension the disagreement occurred on.

### Requirement 9: Parser Agreement Metrics

**Objective:** As a reviewer deciding whether to trust the text a claim was extracted from, I want to know how much the PDF parsers disagreed on each page, so that unreliable pages are visible before extraction is trusted.

#### Acceptance Criteria

1. When two or more parser outputs exist for the same document, the parser agreement module shall compute parser agreement metrics for each page covered by those outputs.
2. The parser agreement module shall compute, per page, token overlap, numeric-token overlap, table-detection agreement, section-heading agreement, and text-presence agreement between the compared parser outputs.
3. When a page yields text from one parser and no text from another, the parser agreement module shall record a text-presence disagreement for that page and shall name the parser that produced no text.
4. When a numeric token appears in one parser's output for a page and not in the other's, the parser agreement module shall record the numeric-token disagreement and shall retain the differing tokens as examples up to a configured limit.
5. When only one parser output exists for a document, the parser agreement module shall report parser agreement as undefined for a single source rather than reporting full agreement.
6. When the compared parser outputs do not cover the same set of pages, the parser agreement module shall compute metrics for the pages they share and shall report the pages covered by only one parser separately.
7. The parser agreement module shall report each per-page metric together with the identities of the two parser outputs it was computed from.
8. If a compared parser output carries no heading signal or no table signal for a page, then the parser agreement module shall report the corresponding agreement metric as undefined for that page rather than reporting either agreement or disagreement.
9. When a parser output distinguishes headings, tables, figures, and body text as it is produced, the pipeline shall preserve that distinction per page so the heading and table metrics are computable rather than permanently undefined.

### Requirement 10: Parser-Risky Pages and the Counterfactual Audit Threshold

**Objective:** As a reviewer, I want pages with low parser agreement marked as risky and pages with high agreement marked as safe to skip, so that downstream verification effort is directed by evidence rather than applied uniformly.

#### Acceptance Criteria

1. When any of a page's parser agreement metrics falls below its configured threshold, the parser agreement module shall mark that page as parser-risky and shall record which metrics fell below threshold.
2. The parser agreement module shall mark a page as parser-risky by default when token overlap is below 0.80, when numeric-token overlap is below 0.95, when the parsers disagree on whether the page contains a table, or when the parsers disagree on whether the page contains text.
3. When all of a page's parser agreement metrics meet their configured high-agreement thresholds, the parser agreement module shall publish a skip-parser-counterfactual signal for that page, and by default those thresholds shall be token overlap at or above 0.95 with full numeric-token, table-detection, section-heading, and text-presence agreement.
4. When a page is marked parser-risky, the parser agreement module shall publish a counterfactual-audit-recommended flag for that page, and shall additionally set it when the page shows any numeric-token or table-detection disagreement even if the page is not otherwise risky.
5. The parser agreement module shall expose every threshold in configuration and shall report, with each risky or skip decision, the threshold values in effect when the decision was made.
6. The parser agreement module shall publish the parser-risky flag, the skip signal, and the counterfactual-audit-recommended flag as data, and shall not itself trigger re-extraction, additional verification, or human review.
7. When a page is marked parser-risky, the parser agreement module shall record the page identifier so that the flag can be associated with the locations later routed to that page.
8. While a page's table-detection or section-heading agreement is undefined, the parser agreement module shall not mark that page parser-risky on that basis, shall not publish the skip-parser-counterfactual signal for it, and shall record which signal was unavailable.

### Requirement 11: Renaming the Existing Mislabelled Ratios

**Objective:** As a reviewer auditing EviTrace's outputs, I want every remaining ratio to be named for what it measures, so that no output of the system continues to present a chance-uncorrected number as an agreement statistic.

#### Acceptance Criteria

1. When the default inter-rater report is produced, the pairwise pass/fail comparison it currently emits shall be reported under a name identifying it as a pass/fail status concordance, not as agreement.
2. When the branch-scoring stage scores a parser branch, the bag-of-words overlap term shall be reported under a name identifying it as a lexical overlap fraction, and its weight and numeric contribution to branch selection shall be unchanged.
3. When the extractor-agreement check emits its report, the match-to-total ratio it currently calls an agreement rate shall be reported under a name identifying it as a sentence match rate.
4. The quality control pipeline shall not emit any field, key, or log message that labels a chance-uncorrected ratio as an agreement statistic.
5. When an output field is renamed, the quality control pipeline shall either continue to populate the previously shipped field with its original meaning or shall version the containing report, and shall not reuse a previously shipped field name for a different quantity.

### Requirement 12: Publication of Agreement Results and Configuration Surface

**Objective:** As an operator running the pipeline, I want the computed statistics to actually appear in the run's outputs and to be controlled from configuration, so that the inter-rater agreement stage produces something a reviewer can read.

#### Acceptance Criteria

1. When the quality control pipeline completes a run, the computed agreement statistics, their declared assumptions, their undefined reason codes, and the stratified disagreement breakdown shall be present in the run's quality control output.
2. When parser agreement has been computed, the per-page metrics and the parser-risky, skip, and counterfactual-audit flags shall be present in the run's quality control output.
3. The quality control pipeline shall allow an operator to select which agreement statistics are computed, to set the weighting scheme, difference function, imbalance threshold, minimum unit count, and parser agreement thresholds, without modifying code.
4. When configuration selects no agreement statistics and no parser agreement thresholds, the quality control pipeline shall run to completion with agreement results reported as not computed, and shall not fail.
5. When an alternative implementation of the inter-rater agreement stage is configured in place of the default, the quality control pipeline shall use it, and shall report an error identifying the configured implementation if it cannot be loaded.

### Requirement 13: Agreement Never Overrides Deterministic Checks

**Objective:** As a reviewer relying on evidence validation, I want agreement statistics to stay observational, so that two raters agreeing on a wrong or unsupported value can never make it pass validation.

#### Acceptance Criteria

1. The agreement statistics module shall not alter the outcome of any deterministic evidence validity check.
2. When two raters agree on a field whose evidence fails a deterministic validity check, the quality control pipeline shall report the field as failing that check regardless of the agreement value.
3. The agreement statistics module shall not modify branch pass/fail status, the adjudication decision, or the reconciled record.
4. When agreement is low for any field or field group, the quality control pipeline shall report the value and shall take no escalation action of its own.
