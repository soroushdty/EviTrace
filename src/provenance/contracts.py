"""contracts — what each pipeline stage must emit, and the order stages run in.

Two declarations, held as data (provenance-core design, "StageContracts"):

``STAGE_CONTRACTS``
    ``Mapping[str, StageContract]`` naming, per pipeline stage, the record
    kinds it must emit (members of the closed ``NodeKind`` vocabulary) and the
    artifact scope those records cover (``run``, ``document`` or ``claim``).
    Absence is only detectable against a declaration, so this table — not the
    stage — is authoritative: ``compute_completeness`` diffs it against what
    was recorded to find a stage that did not emit what it declared (2.1) and
    to name that stage and the absent kinds (2.3).

``STAGE_ORDER``
    ``tuple[str, ...]`` giving the deterministic stage ordering. The event-log
    projection sorts on ``(STAGE_ORDER.index(stage), node_kind_rank, node_id)``,
    a total order that needs no clock (7.6).

Core stages, in pipeline order, and where each is emitted:

======================  =======================  ========  =====================
Stage                   Required record kinds    Scope     Emission point
======================  =======================  ========  =====================
``document_extraction`` source                   document  ``extraction_pipeline``
``quality_control``     derivation               document  ``extraction_pipeline``
``evidence_index``      evidence                 document  ``evidence_index``
``metadata_prefill``    claim, derivation        claim     ``pdf_processor``
``extraction_chunk``    claim, derivation        claim     ``pdf_processor``
``synthesis``           claim, derivation        claim     ``pdf_processor``
``chain_validation``    validation               run       ``orchestrator``
======================  =======================  ========  =====================

Both declarations are read-only at runtime. :func:`require_declared_stage` is
the single lookup; it raises :class:`UnknownStageError` for any stage name not
declared in **both** tables, and ``ProvenanceRecorder`` calls it at record time
so an undeclared stage is rejected rather than silently accepted.

Extension protocol for downstream specs
---------------------------------------
The vocabulary is closed but the tables are *extensible*. A spec that
introduces new pipeline stages (``evidence-routing``, ``multiagent-extraction``)
extends both tables **in place**, in this module, in the same commit that adds
the emitting code: one ``StageContract`` entry per stage in
``STAGE_CONTRACTS`` and one ordered position per stage in ``STAGE_ORDER``.
``evidence-routing`` inserts its stages after ``evidence_index`` and before
``metadata_prefill``; ``multiagent-extraction`` inserts its stages after
``extraction_chunk`` and before ``synthesis``. Such a spec continues to route
every record through ``ProvenanceRecorder``: it does not bypass the recorder,
does not catch or suppress ``UnknownStageError`` to avoid declaring a stage,
and does not keep a private stage table. The contract-drift test (every stage
name the recorder accepts is declared in both ``STAGE_CONTRACTS`` and
``STAGE_ORDER``) stays in force and must be re-run whenever a stage is added
or renamed. Extending the tables is a Revalidation Trigger for provenance-core
but not a ``PROVENANCE_SCHEMA_VERSION`` bump, because ``stage`` is a
free-form string on ``DerivationStep``.

Imports only the standard library and ``provenance.models``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from provenance.models import StageContract

__all__ = [
    "STAGE_CONTRACTS",
    "STAGE_ORDER",
    "UnknownStageError",
    "require_declared_stage",
]


class UnknownStageError(ValueError):
    """Raised when a stage name is not declared in both ``STAGE_CONTRACTS`` and ``STAGE_ORDER``."""


_CONTRACT_ROWS: tuple[StageContract, ...] = (
    StageContract(
        stage="document_extraction",
        required_record_kinds=("source",),
        scope="document",
    ),
    StageContract(
        stage="quality_control",
        required_record_kinds=("derivation",),
        scope="document",
    ),
    StageContract(
        stage="evidence_index",
        required_record_kinds=("evidence",),
        scope="document",
    ),
    StageContract(
        stage="metadata_prefill",
        required_record_kinds=("claim", "derivation"),
        scope="claim",
    ),
    StageContract(
        stage="extraction_chunk",
        required_record_kinds=("claim", "derivation"),
        scope="claim",
    ),
    StageContract(
        stage="synthesis",
        required_record_kinds=("claim", "derivation"),
        scope="claim",
    ),
    StageContract(
        stage="chain_validation",
        required_record_kinds=("validation",),
        scope="run",
    ),
)

STAGE_CONTRACTS: Mapping[str, StageContract] = MappingProxyType(
    {row.stage: row for row in _CONTRACT_ROWS}
)
"""Per-stage declaration of required record kinds and artifact scope (2.1, 2.3)."""

STAGE_ORDER: tuple[str, ...] = (
    "document_extraction",
    "quality_control",
    "evidence_index",
    "metadata_prefill",
    "extraction_chunk",
    "synthesis",
    "chain_validation",
)
"""Deterministic stage ordering consumed by the event-log projection (7.6)."""


def require_declared_stage(
    stage: str,
    *,
    contracts: Mapping[str, StageContract] = STAGE_CONTRACTS,
    order: tuple[str, ...] = STAGE_ORDER,
) -> StageContract:
    """Return the contract for ``stage``, or raise :class:`UnknownStageError`.

    A stage is accepted only when it is declared in both ``contracts`` and
    ``order``; a name missing from either is rejected, which is what keeps the
    two tables from drifting apart. The keyword parameters default to the
    module tables and exist so callers holding explicitly passed tables (and
    tests) use the same check.
    """
    if not isinstance(stage, str):
        raise UnknownStageError(f"stage name must be a str, got {type(stage).__name__}: {stage!r}")
    in_contracts = stage in contracts
    in_order = stage in order
    if in_contracts and in_order:
        return contracts[stage]
    if not in_contracts and not in_order:
        where = "STAGE_CONTRACTS or STAGE_ORDER"
    elif not in_contracts:
        where = "STAGE_CONTRACTS"
    else:
        where = "STAGE_ORDER"
    raise UnknownStageError(
        f"stage {stage!r} is not declared in {where}; declare it in both tables "
        "in provenance.contracts before recording against it"
    )
