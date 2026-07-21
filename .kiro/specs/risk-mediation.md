# Design Document: Top-10 Audit Risk Remediation

## Architecture Note

All 10 fixes are surgical — they do not cross module dependency boundaries, introduce new modules, or change public interfaces (except `QualityReport` gaining one field). The most complex are C2 (reconciler wiring, ~20 lines), C6 (cache-path scan detection, ~30 lines), and C7 (TEI section_path, ~20 lines). Each fix is isolated to a single function or class.

---

## Components

### C1 — Final_Schema type fix (R1)

**File:** `configs/final_output_schema.json` line ~22

**Change:** Replace `"type": "integer"` with `"type": "string"` on the `domain_group` property; remove the sibling `"minimum": 1` line. No code changes required — the existing `FinalOutputValidator` will immediately work correctly because it loads this file at runtime.

**Why it/home/soroush/myrepos/vault/INBOX broke:** The extraction map produces string values like `"1. Study identification"` (matching the `domain_group` key in `configs/extraction_map.json`), but the schema declared the field as an integer. JSON Schema rejects every field, causing `_save_pdf_output()` to skip all writes.

---

### C2 — Reconciler branch selection wiring (R2)

**File:** `src/pipeline/quality_control.py` → `_pdf_reconciler_fn` (lines ~600–608)

**Change:** Replace the hard-coded GROBID/pdfplumber source lookups with decision-driven selection:

```python
primary_branch = next(
    (b for b in all_branches if b.source == decision.primary_extractor),
    next((b for b in all_branches if b.source == "grobid"), None)
    or (all_branches[0] if all_branches else None),
)
secondary_branch = next(
    (b for b in all_branches if b is not primary_branch), None
)
```

If no branch matches `decision.primary_extractor`, log a WARNING and fall back to GROBID, then first branch.

After reconciliation, write `decision.rationale` (or `""` if absent) into `ctx.unified.content["adjudication_rationale"]`.

---

### C3 — Deterministic W3C annotation IDs (R3)

**File:** `src/artifact_generation/w3c_annotation.py`

**Change:** Before the per-sentence annotation loop in `generate_w3c_jsonld()`, build an occurrence counter:

```python
import hashlib as _hashlib
_occurrence: dict[str, int] = {}
```

Per sentence, replace `uuid.uuid4()` with:

```python
occ = _occurrence.get(rec.sentence_text, 0)
_occurrence[rec.sentence_text] = occ + 1
digest = _hashlib.sha256(
    f"{document_source}\x00{rec.sentence_text}\x00{rec.page_index}\x00{occ}".encode()
).hexdigest()[:16]
anno_id = f"urn:evitrace:anno:{digest}"
```

The null-byte separators prevent collisions across field boundaries.

---

### C4 — OCR FragmentSelector bbox fix (R4)

**Two-file change:**

**File 1:** `src/quality_control/reconciler.py`

When populating `sentence_to_char_range` entries for OCR-derived sentences, include the sentence's bounding box:

```python
entry = {
    "sentence": text,
    "start": start_char,
    "end": end_char,
    "page_index": page_idx,
    "block_bbox": block.get("block_bbox"),  # None for native sentences
}
```

This is backward-compatible — native entries simply have `"block_bbox": None`.

**File 2:** `src/artifact_generation/w3c_annotation.py`

Extend `char_range_lookup` to store the full entry dict (not just `(start, end)`). For OCR sentences in the FragmentSelector builder (lines ~136–148), look up `block_bbox` from the lookup entry:

```python
bbox = char_range_lookup.get(sent_text, {}).get("block_bbox")
if bbox is None:
    logger.warning("No block_bbox for OCR sentence on page %d: %r", page_idx, sent_text[:40])
    bbox = (0, 0, 0, 0)
xywh = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
```

Remove the current `break`-after-first-block scan for OCR sentences.

---

### C5 — Synthesis RepairRetryLoop (R5)

**File:** `src/pipeline/pdf_processor.py` lines ~717–757

**Change:** Replace the direct `extract_chunk()` + `validate_chunk_output()` synthesis call with the same `RepairRetryLoop.extract_with_repair()` pattern already used for extraction chunks. The `RepairRetryLoop` class is already defined earlier in the same file.

```python
synthesis_repair_loop = RepairRetryLoop(
    max_log_response_chars=int(openai_config.get("max_log_response_chars", 500)),
    debug_artifact_dir=openai_config.get("debug_artifact_dir"),
)
final_compact = await synthesis_repair_loop.extract_with_repair(
    synthesis_chunk,
    chunk_sources.get(synthesis_chunk, paper_source),
    synthesis_fields,
    api_semaphore,
    valid_location_ids=valid_location_ids,
    expected_indices=synthesis_expected_idx,
    pdf_name=pdf_name,
)
```

Catch `RepairExhaustedError` from synthesis identically to extraction chunks; update manifest status to `f"failed_chunk_{synthesis_chunk}"` with `.metadata`.

---

### C6 — Scan detection on GROBID cache hit (R6)

**File:** `src/pipeline/extraction_pipeline.py` lines ~287–310

**Change:** On the GROBID TEI cache-hit path, run the `scan_detector` closure before constructing `page_routing_results`. The `_run_scan_detector` closure is already defined in `build_qc_bundle()` and used on the non-cached path — extract its call before the `if cached_tei is not None` branch.

```python
if cached_tei is not None:
    # Always classify pages — cache only skips the GROBID HTTP call
    page_classifications = _run_scan_detector()
    all_native = all(c.is_native for c in page_classifications)
    if not all_native:
        logger.warning(
            "GROBID cache hit for mixed PDF %s — %d scanned pages detected",
            pdf_name, sum(1 for c in page_classifications if not c.is_native),
        )
    # Build page_routing_results from real classifications
    page_routing_results = _build_routing_from_classifications(page_classifications, ...)
```

If `scan_detector` raises unexpectedly, log ERROR and proceed with the previous `all_native=True` default (conservative safe fallback).

---

### C7 — Figure/table `section_path` fix (R7)

**File:** `src/pipeline/evidence_index.py` lines ~496–545

**Change:** Move the `for fig in body.findall(...)` and `for table in body.findall(...)` loops from their current post-div-loop position to inside the `for div in body.findall(f".//{_NS}div")` loop, changing them to iterate `div.findall(...)` rather than `body.findall(...)`. This gives each figure/table the `section_path` of its enclosing div.

```python
for div in body.findall(f".//{_NS}div"):
    head = div.find(f"./{_NS}head")
    if head is not None:
        section_path = _safe_text("".join(head.itertext())) or section_path
    for sent in div.findall(f".//{_NS}s"):
        ...  # existing sentence handling
    for fig in div.findall(f"./{_NS}figure"):   # note: single-level, not .//{NS}
        ...  # existing figure handling, uses current section_path
    for table in div.findall(f"./{_NS}table"):
        ...  # existing table handling, uses current section_path
```

Remove the duplicate figure/table loops that currently appear after the div loop closes.

---

### C8 — ABC enforcement (R8)

**File:** `src/quality_control/models.py`

**Change:** Add `ABC` to the existing `abstractmethod` import and change three class headers:

```python
from abc import ABC, abstractmethod

class QualityMetrics(ABC):        # was: class QualityMetrics:
    ...
class InterRaterMetrics(ABC):     # was: class InterRaterMetrics:
    ...
class AdjudicationRules(ABC):     # was: class AdjudicationRules:
    ...
```

All three existing concrete subclasses (`ExtractionCoverageReport`, `InterRaterReport`, `AdjudicationDecision`) already implement the required abstract methods — no changes needed to them.

---

### C9 — Correct extractor names in IAA / adjudication (R9)

**Four-file change:**

**File 1:** `src/quality_control/builtin_impls/quality_report.py`
Add `source_name: str = field(default="")` to the `QualityReport` (or `ExtractionCoverageReport`) dataclass.

**File 2:** `src/quality_control/quality_control.py` → `_pdf_rater_fn`
Set `report.source_name = branch.source` on the report before returning it.

**File 3:** `src/quality_control/builtin_impls/inter_rater_report.py` line ~42
Replace `getattr(a, "extractor", str(i))` with:
```python
getattr(a, "source_name", None) or getattr(a, "source", None) or str(i)
```
Same for `b`.

**File 4:** `src/quality_control/builtin_impls/adjudication_decision.py` line ~37
Same replacement as File 3 for the `name` lookup.

**Result:** IAA pairwise keys become `"grobid_vs_pdfplumber"`; `primary_extractor` becomes `"grobid"` or `"pdfplumber"`.

---

### C10 — Evidence char budget defaults (R10)

**File:** `configs/config.yaml` lines ~43–44

**Change:**
```yaml
max_evidence_chars_per_chunk: 40000   # was 10000 — tuned for papers up to ~10 000 words
max_evidence_items_per_chunk: 200      # was 150
```

Update the config comment to state the rationale. No code changes required.

---

## Data Model Changes

| Change | Scope | Backward-compatible? |
|--------|-------|----------------------|
| `domain_group` type: `integer` → `string` in schema | Config file | Yes — no valid prior outputs exist |
| `QualityReport.source_name: str = ""` field added | `QualityReport` dataclass | Yes — default empty string |
| `sentence_to_char_range` entries gain optional `block_bbox` key | Dict entries in `DocumentAlignment` | Yes — omitted for native sentences |
| `QualityMetrics`, `InterRaterMetrics`, `AdjudicationRules` gain `ABC` | Class inheritance | Yes — existing subclasses already implement abstract methods |

---

## Correctness Properties

**P1 — Schema accepts string domain_group:** For any field dict where `domain_group` is a non-empty string, `FinalOutputValidator.validate([field])` returns `is_valid=True`.

**P2 — Reconciler uses adjudication decision:** For any two branches and a decision with `primary_extractor` equal to one branch's `source`, the reconciled `UnifiedRecord`'s primary content is derived from that branch.

**P3 — Annotation IDs are stable:** For any fixed `UnifiedRecord` and `base_uri`, two successive calls to `generate_w3c_jsonld(project(ur), base_uri)` produce output where every `id` field is identical.

**P4 — Annotation IDs are unique:** For any two distinct `(sentence_text, page_idx, occurrence)` triples, their derived IDs differ.

**P5 — Synthesis repair recovers:** For any sequence `[invalid_json, valid_json]` of synthesis responses, `extract_with_repair` returns the parsed content of `valid_json`.

**P6 — Cache-hit routing is correct:** For any mixed PDF whose GROBID TEI is cached, `build_qc_bundle()` does not label scanned pages as `routing_reason="all_native"`.

**P7 — Figure section_path matches parent div:** For any TEI XML, every figure/table evidence item's `section_path` equals the heading of its nearest ancestral `<div>` with a `<head>`.

**P8 — Incomplete QC subclass raises TypeError:** For any class that inherits from `QualityMetrics`, `InterRaterMetrics`, or `AdjudicationRules` without implementing all abstract methods, instantiation raises `TypeError`.

**P9 — IAA keys use source names:** For any QCBundle with named branches, `iaa_metrics.pairwise` contains no keys of the form `"N_vs_M"` where N and M are bare integers.

**P10 — Evidence coverage threshold:** For a paper whose substantive text is 30 000 chars, `build_paper_evidence_package()` with default config selects ≥18 000 chars (60%).

---

## Error Handling

| Component | Failure mode | Handling |
|-----------|-------------|----------|
| C2 | No branch matches `decision.primary_extractor` | Log WARNING; fall back to GROBID then first branch |
| C4 | No `block_bbox` in alignment entry for OCR sentence | Use `(0,0,0,0)`; log WARNING with sentence snippet and page |
| C5 | All synthesis repair retries exhausted | Catch `RepairExhaustedError`; manifest `"failed_chunk_{n}"`; return `None` |
| C6 | `scan_detector` raises on cache-hit path | Log ERROR; proceed with `all_native=True` (safe default) |
| C8 | Incomplete subclass instantiated | `TypeError` raised — this is the desired and correct behavior |

---

## Testing Strategy

All tests follow existing patterns: example-based unit tests and property-based with Hypothesis where applicable.

### New test files

| File | Covers |
|------|--------|
| `tests/src/pipeline/test_final_output_schema_string.py` | R1 |
| `tests/src/pipeline/test_synthesis_repair.py` | R5 |
| `tests/src/pipeline/test_cache_hit_scan_detection.py` | R6 |
| `tests/src/pipeline/test_figure_section_path.py` | R7 |
| `tests/src/quality_control/test_abc_enforcement.py` | R8 |
| `tests/src/quality_control/test_extractor_names.py` | R9 |

### Extensions to existing test files

| File | Additions |
|------|-----------|
| `tests/src/pipeline/test_final_output_validator.py` | R1 regression case with string `domain_group` |
| `tests/src/pdf_extractor/test_w3c_annotation.py` | R3 stability + R4 per-sentence bbox cases |
| `tests/src/quality_control/test_quality_control_adjudicator.py` | R2 reconciler wiring case |
| `tests/src/pipeline/test_evidence_cache.py` | R10 coverage assertion |