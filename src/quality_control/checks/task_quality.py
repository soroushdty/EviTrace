"""Task-quality scaffold for the QC migration.

Returns a JSON-serializable placeholder dict for all eight task-quality
metrics.  No HTTP requests, no LLM API calls, no credential reads.
"""
from __future__ import annotations

import json

_METRICS = [
    "field_recall",
    "critical_field_recall",
    "evidence_validity",
    "evidence_compactness",
    "cost_reduction",
    "manual_qc_rate",
    "interobserver_agreement",
    "pipeline_agreement",
]


def build_task_quality_scaffold() -> dict:
    """Return a JSON-serializable scaffold dict for all eight task-quality metrics.

    Each metric entry has ``status="scaffolded"`` and ``value=None``.
    The top-level dict also carries a ``status`` key set to
    ``"not_computed"`` and a ``details`` key with a human-readable
    explanation.

    The caller is responsible for storing the return value under the key
    ``"task_quality_scaffold"`` in per-PDF output — never under
    ``"semantic_qc"``.
    """
    scaffold: dict = {
        "status": "not_computed",
        "details": (
            "Task-specific quality criteria are not active in this refactor phase."
        ),
    }

    for metric in _METRICS:
        scaffold[metric] = {"status": "scaffolded", "value": None}

    # Verify JSON-serializability at construction time so callers get an
    # immediate, clear error if the structure ever becomes non-serializable.
    json.dumps(scaffold)

    return scaffold
