# EviTrace Quality Control Module

Standalone quality control and adjudication pipeline for EviTrace text extraction.

## What This Module Does

The `quality_control` package sits between extractor branches and downstream reconciliation. It evaluates each branch, compares the branches, selects a preferred result when needed, and builds the unified record consumed by the rest of EviTrace.

It is designed to work in two modes:

1. A generic branch-adjudication pipeline that can be reused outside PDF extraction.
2. A PDF-specific wrapper that plugs into the current `pdf_extractor` workflow.

## Current Structure

### `quality_control/quality_control.py`

Main orchestrator for the module.

- `run_pipeline(...)` executes a generic four-stage QC flow with injected rater, IAA, adjudicator, and reconciler callables.
- `run_quality_control(...)` wires the PDF-specific branch flow into that orchestrator.
- The PDF path also tracks a three-tier metrics hierarchy:
  - Tier 1: `LocalQCReport` heuristics
  - Tier 2: exact-match search for borderline branches
  - Tier 3: semantic search scaffold, currently not part of adjudication

### `quality_control/models.py`

Shared dataclass models for all stages.

- `BranchOutput` holds one branch's payload, extractor name, and status.
- `QualityMetrics` / `QualityReport` define the per-branch quality-check contract.
- `InterRaterMetrics` / `InterRaterReport` define agreement computation.
- `AdjudicationRules` / `AdjudicationDecision` define branch selection logic.
- `UnifiedRecord` is the reconciled output.
- `QCContext` carries the full run state across stages.

### `quality_control/local_metrics.py`

Concrete Tier 1 quality checks implemented by `LocalQCReport`.

- Measures page coverage, GROBID-vs-native length ratio, long-sentence fraction, section coverage, caption coverage, coordinate availability, references-in-body leakage, and unusual character ratio.
- Reads thresholds from `config["quality_control"]["local_metrics"]`.
- Produces one `LocalQCMetricRecord` per metric.

### `quality_control/artifact_generator.py`

Builds canonical in-memory artifacts for the two extractor branches.

- Canonicalizes GROBID TEI XML and PyMuPDF JSON into deterministic content strings.
- Computes stable SHA-256 artifact IDs.
- Optionally exports canonical artifacts to disk when configured.

### `quality_control/rater.py`

Builds observation objects for each extractor branch.

- Captures extractor name, document ID, placeholder attributes, status, and provenance references.

### `quality_control/iaa_calculator.py`

Produces the investigator object used to represent branch agreement and artifact references.

### `quality_control/adjudicator.py`

Evaluates branch quality and chooses the preferred extractor.

- Uses quality scoring to pick a primary branch and confidence.
- Passes the decision to the reconciler.

### `quality_control/reconciler.py`

Builds the final unified output.

- Reconciles the selected branch output into pages, segments, geometry, provenance, and status.
- Falls back to placeholder output when no adjudication decision is available.

## How It Fits Into EviTrace

Current workflow:

1. `pdf_extractor` extracts branch outputs.
2. `quality_control.run_quality_control(...)` evaluates the branches and produces a `QCContext`.
3. The reconciled `UnifiedRecord` becomes the handoff to downstream consumers.

In practice, this module is the bridge between raw extraction and the stabilized, inspectable output used by the rest of the system.

## Configuration Surface

The module reads QC settings from the `quality_control` section of `config.yaml`, including thresholds for local metrics, optional artifact export, and semantic-QC toggles.

## Public API

```python
from quality_control import run_pipeline, run_quality_control
```

- Use `run_pipeline(...)` when you want to inject custom stage implementations.
- Use `run_quality_control(...)` for the current PDF extraction workflow.

## Notes

- Semantic Tier 3 support is scaffolded, but not currently wired into final adjudication.
- Quality control module is planned to support adjudication of multi-agentic workflow (coming soon...)
