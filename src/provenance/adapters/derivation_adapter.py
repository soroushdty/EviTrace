"""derivation_adapter — QC provenance mappings and stage events into ``DerivationStep`` records.

Two entry points (provenance-core design, "Emission Layer → Adapters"):

:func:`derivations_from_qc_provenance`
    Converts the reconciled record's provenance mapping
    (``unified.content["provenance"]``) without importing ``quality_control``
    or ``pipeline``. The mapping is built by ``_build_provenance_dict`` in
    ``src/quality_control/reconciler.py`` and extended with branch-role keys by
    ``run_quality_control``:

    ============================ ============================================
    Key                          Meaning
    ============================ ============================================
    ``primary_artifact_id``      reconciler artifact id (``""`` on the real
                                 path: QC artifacts carry no ``id``)
    ``secondary_artifact_id``    likewise
    ``primary_observation``      observation mapping (``{}`` on the real path)
    ``secondary_observation``    likewise
    ``investigator_object``      likewise
    ``adjudication_decisions``   ``primary_extractor``, ``confidence``,
                                 ``rationale``
    ``primary_branch_source``    backend name of the primary branch, or None
    ``secondary_branch_source``  backend name of the secondary branch, or None
    ``branch_selection``         ``"adjudicated"`` | ``"fallback_index_order"``
    ============================ ============================================

    QC reconciliation is rule-based, so it becomes **one** ``reconcile`` step
    on the ``quality_control`` stage, ``deterministic`` with no model id
    (6.4). Its only input is the source node (``source_id``): the extractor
    branches it reconciles and the unified record it produces are not
    provenance nodes, so naming them as inputs or outputs would create
    references no graph node can satisfy. The step has no outputs — the
    evidence nodes are built from the GROBID TEI by the ``evidence_index``
    stage, not from the unified record. What the mapping says about the
    branches is preserved, never dropped: every key is carried in
    ``extensions`` as ``x-qc-<key>`` with its value unchanged (10.2, 10.4);
    a key that cannot form a conforming extension key (not a string, or
    characters outside the field charset) is kept, stringified, in the single
    mapping ``x-qc-unnamespaced`` and logged at WARNING.

:func:`derivation_from_stage_event`
    The adapter every downstream spec calls to record a stage event. Its call
    contract is binding:

    - ``stage`` must be declared in ``STAGE_CONTRACTS`` **and** ``STAGE_ORDER``
      (:func:`provenance.contracts.require_declared_stage`); an undeclared
      stage raises :class:`~provenance.contracts.UnknownStageError` here, the
      same error the recorder raises at record time.
    - ``kind`` is required and positional, and must be a member of the closed
      ``DerivationKind`` literal; anything else raises ``ValueError``. It is
      never inferred from ``stage``.
    - ``determinism`` is the ``Determinism`` literal, never a ``bool``. Omitted
      (``None``), it is ``"probabilistic"`` when ``model_id`` is given and
      ``"deterministic"`` otherwise (6.3, 6.4). A ``bool`` raises
      ``TypeError`` (no bool→literal coercion); a non-member string raises
      ``ValueError``; ``"deterministic"`` together with a ``model_id`` raises
      ``ValueError``, because a model-performed step is probabilistic (6.3).
      ``"probabilistic"`` without a model id is accepted (a model-free but
      sampled component).
    - ``inputs`` and ``outputs`` are sequences of non-empty identifier strings
      (a bare string is rejected). A repeated identifier is kept once, in
      first-occurrence order; the step ``node_id`` is
      :func:`provenance.identity.derive_derivation_id` over the result. Many
      inputs to one output is one step (6.5); one input to many outputs
      references the input by identifier only, so a fan-out across several
      steps reuses the single input identifier and never duplicates the input
      artifact (6.6). Either side may be empty; whether such a step is
      consistent is the chain validator's decision.
    - Removal (6.7): ``removed_ids`` require a non-blank ``removal_reason``
      (ids without one raise ``ValueError``), and a ``remove`` step must name
      what it removed (``ValueError`` otherwise). A ``removal_reason`` given
      with no ``removed_ids`` is accepted and recorded as ``None`` (logged at
      DEBUG), so a step never claims a removal it did not make; callers may
      pass a per-stage reason whether or not anything was removed.
    - Every field ``DerivationStep`` does not declare travels in
      ``extensions`` under ``x-<namespace>-<field>`` (10.4), e.g.
      ``x-multiagent-prompt_version``. A non-conforming key raises
      ``ValueError``; conforming keys are carried unchanged. There is no
      ``**kwargs``, so an undeclared field passed as a keyword is a
      ``TypeError``.
    - ``run_id`` must be a non-empty string (``InvalidIdentifierError``).

Both entry points are pure: no clock, no randomness, no I/O; the input
mappings are not mutated. Every step carries
:data:`~provenance.privacy_carrier.UNKNOWN_PRIVACY`. Identifiers come from
:mod:`provenance.identity` (the only provenance module that hashes); this
module imports no hashing library.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, get_args

from provenance.contracts import require_declared_stage
from provenance.identity import InvalidIdentifierError, derive_claim_id, derive_derivation_id
from provenance.models import (
    DerivationKind,
    DerivationStep,
    Determinism,
    is_extension_key,
)
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from utils.logging_utils import get_logger

logger = get_logger(__name__)

__all__ = [
    "QC_EXTENSION_NAMESPACE",
    "derivation_from_stage_event",
    "derivations_from_qc_provenance",
]

QC_EXTENSION_NAMESPACE = "qc"
"""Extension namespace for keys carried over from the QC provenance mapping (``x-qc-<key>``)."""

_QC_STAGE = "quality_control"
_QC_KIND: DerivationKind = "reconcile"
_QC_UNNAMESPACED_KEY = f"x-{QC_EXTENSION_NAMESPACE}-unnamespaced"

_DERIVATION_KINDS: frozenset[str] = frozenset(get_args(DerivationKind))
_DETERMINISMS: frozenset[str] = frozenset(get_args(Determinism))

_REPR_LIMIT = 80


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_LIMIT else text[: _REPR_LIMIT - 3] + "..."


# ---------------------------------------------------------------------------
# Argument checks
# ---------------------------------------------------------------------------


def _require_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise InvalidIdentifierError(f"run_id must be a non-empty string, got {_short_repr(run_id)}")
    return run_id


def _require_kind(kind: object) -> DerivationKind:
    if not isinstance(kind, str) or kind not in _DERIVATION_KINDS:
        raise ValueError(
            f"kind must be a member of the closed DerivationKind vocabulary {sorted(_DERIVATION_KINDS)}, "
            f"got {_short_repr(kind)}; it is never inferred from the stage"
        )
    return kind  # type: ignore[return-value]


def _require_model_id(model_id: object) -> str | None:
    if model_id is None:
        return None
    if not isinstance(model_id, str):
        raise TypeError(f"model_id must be a string or None, got {type(model_id).__name__}")
    if not model_id.strip():
        raise ValueError("model_id must be a non-blank string or None")
    return model_id


def _resolve_determinism(determinism: object, model_id: str | None) -> Determinism:
    if determinism is None:
        return "probabilistic" if model_id is not None else "deterministic"
    if isinstance(determinism, bool):
        raise TypeError(
            "determinism must be the Determinism literal ('deterministic' | 'probabilistic'), never a bool; "
            f"got {determinism!r}"
        )
    if not isinstance(determinism, str) or determinism not in _DETERMINISMS:
        raise ValueError(
            f"determinism must be one of {sorted(_DETERMINISMS)} or None, got {_short_repr(determinism)}"
        )
    if determinism == "deterministic" and model_id is not None:
        raise ValueError(
            f"a step performed by model {model_id!r} must be 'probabilistic', not 'deterministic' (6.3)"
        )
    return determinism  # type: ignore[return-value]


def _unique_ids(ids: object, what: str) -> tuple[str, ...]:
    """Validate a sequence of identifiers and return it without repeats, in first-occurrence order."""
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence):
        raise TypeError(f"{what} must be a sequence of identifiers, got {_short_repr(ids)}")
    unique: dict[str, None] = {}
    for item in ids:
        if not isinstance(item, str) or not item:
            raise InvalidIdentifierError(f"each of {what} must be a non-empty string, got {_short_repr(item)}")
        unique.setdefault(item, None)
    if len(unique) < len(ids):
        logger.debug("%s listed %d repeated identifier(s); each is kept once", what, len(ids) - len(unique))
    return tuple(unique)


def _check_removal(kind: str, removal_reason: object, removed_ids: tuple[str, ...]) -> str | None:
    if removal_reason is not None and not isinstance(removal_reason, str):
        raise TypeError(f"removal_reason must be a string or None, got {type(removal_reason).__name__}")
    has_reason = isinstance(removal_reason, str) and bool(removal_reason.strip())
    if removed_ids and not has_reason:
        raise ValueError("removed_ids were given without a non-blank removal_reason (6.7)")
    if kind == "remove" and not removed_ids:
        raise ValueError("a 'remove' step must name what it removed in removed_ids, with a removal_reason (6.7)")
    if removal_reason is not None and not removed_ids:
        # A step never claims a removal it did not make.
        logger.debug("removal_reason %s given with no removed_ids; recorded as no removal", _short_repr(removal_reason))
        return None
    return removal_reason  # type: ignore[return-value]


def _check_extensions(extensions: object) -> Mapping[str, Any]:
    if extensions is None:
        return {}
    if not isinstance(extensions, Mapping):
        raise TypeError(f"extensions must be a mapping or None, got {type(extensions).__name__}")
    bad = [key for key in extensions if not is_extension_key(key)]
    if bad:
        listed = ", ".join(_short_repr(key) for key in bad)
        raise ValueError(
            "fields not declared on DerivationStep must be passed in extensions under "
            f"'x-<namespace>-<field>' keys (e.g. 'x-multiagent-prompt_version'); rejected: {listed}"
        )
    return extensions


def _build_step(
    stage: str,
    kind: DerivationKind,
    *,
    run_id: str,
    determinism: Determinism,
    model_id: str | None,
    input_ids: tuple[str, ...],
    output_ids: tuple[str, ...],
    removal_reason: str | None,
    removed_ids: tuple[str, ...],
    extensions: Mapping[str, Any],
) -> DerivationStep:
    step = DerivationStep(
        node_id=derive_derivation_id(stage, kind, input_ids, output_ids),
        kind="derivation",
        run_id=run_id,
        privacy=UNKNOWN_PRIVACY,
        extensions=extensions,
        stage=stage,
        derivation_kind=kind,
        determinism=determinism,
        model_id=model_id,
        input_ids=input_ids,
        output_ids=output_ids,
        removal_reason=removal_reason,
        removed_ids=removed_ids,
    )
    logger.debug(
        "derivation step %s: stage=%s kind=%s %s inputs=%d outputs=%d removed=%d",
        step.node_id,
        stage,
        kind,
        determinism,
        len(input_ids),
        len(output_ids),
        len(removed_ids),
    )
    return step


# ---------------------------------------------------------------------------
# Stage events
# ---------------------------------------------------------------------------


def derivation_from_stage_event(
    stage: str,
    kind: DerivationKind,
    *,
    run_id: str,
    inputs: Sequence[str],
    outputs: Sequence[str],
    model_id: str | None = None,
    determinism: Determinism | None = None,
    removal_reason: str | None = None,
    removed_ids: Sequence[str] = (),
    extensions: Mapping[str, Any] | None = None,
) -> DerivationStep:
    """Build the derivation step for one stage event (6.1–6.7); see the module docstring for the contract.

    Raises ``UnknownStageError`` for an undeclared ``stage``; ``ValueError``
    for a ``kind`` outside ``DerivationKind``, a ``determinism`` outside
    ``Determinism`` or contradicting ``model_id``, an incomplete removal, or a
    non-namespaced ``extensions`` key; ``TypeError`` for a ``bool``
    ``determinism`` or a wrongly typed argument; ``InvalidIdentifierError``
    for a malformed ``run_id`` or identifier.
    """
    require_declared_stage(stage)
    checked_kind = _require_kind(kind)
    checked_run_id = _require_run_id(run_id)
    checked_model_id = _require_model_id(model_id)
    checked_determinism = _resolve_determinism(determinism, checked_model_id)
    input_ids = _unique_ids(inputs, "inputs")
    output_ids = _unique_ids(outputs, "outputs")
    removed = _unique_ids(removed_ids, "removed_ids")
    checked_reason = _check_removal(checked_kind, removal_reason, removed)
    checked_extensions = _check_extensions(extensions)
    return _build_step(
        stage,
        checked_kind,
        run_id=checked_run_id,
        determinism=checked_determinism,
        model_id=checked_model_id,
        input_ids=input_ids,
        output_ids=output_ids,
        removal_reason=checked_reason,
        removed_ids=removed,
        extensions=checked_extensions,
    )


# ---------------------------------------------------------------------------
# QC reconciliation provenance
# ---------------------------------------------------------------------------


def _qc_extensions(provenance: Mapping[Any, Any], source_id: str) -> dict[str, Any]:
    """Carry every mapping key as ``x-qc-<key>``; keys that cannot be namespaced go to ``x-qc-unnamespaced``."""
    extensions: dict[str, Any] = {}
    unnamespaced: dict[str, Any] = {}
    for key, value in provenance.items():
        candidate = f"x-{QC_EXTENSION_NAMESPACE}-{key}" if isinstance(key, str) else None
        if candidate is not None and is_extension_key(candidate) and candidate != _QC_UNNAMESPACED_KEY:
            extensions[candidate] = value
        else:
            unnamespaced[str(key)] = value
    if unnamespaced:
        extensions[_QC_UNNAMESPACED_KEY] = unnamespaced
        logger.warning(
            "QC provenance for %s: %d key(s) cannot form an 'x-%s-<field>' extension key; kept under %s: %s",
            source_id,
            len(unnamespaced),
            QC_EXTENSION_NAMESPACE,
            _QC_UNNAMESPACED_KEY,
            ", ".join(_short_repr(key) for key in unnamespaced),
        )
    return extensions


def derivations_from_qc_provenance(
    provenance: Mapping[str, Any],
    *,
    source_id: str,
    run_id: str,
) -> list[DerivationStep]:
    """Convert a reconciled record's provenance mapping into its derivation steps (6.1, 6.4).

    Returns one deterministic ``reconcile`` step on the ``quality_control``
    stage whose input is ``source_id`` and whose extensions carry the mapping
    (see the module docstring). Unknown keys are preserved, never rejected;
    an empty mapping still yields the step. Raises only on caller errors:
    ``provenance`` not a mapping (``TypeError``); a malformed ``source_id`` or
    ``run_id`` (``InvalidIdentifierError``).
    """
    if not isinstance(provenance, Mapping):
        raise TypeError(f"provenance must be a mapping, got {type(provenance).__name__}")
    derive_claim_id(source_id, 0)  # raises InvalidIdentifierError on a malformed source_id
    checked_run_id = _require_run_id(run_id)
    require_declared_stage(_QC_STAGE)
    step = _build_step(
        _QC_STAGE,
        _QC_KIND,
        run_id=checked_run_id,
        determinism="deterministic",
        model_id=None,
        input_ids=(source_id,),
        output_ids=(),
        removal_reason=None,
        removed_ids=(),
        extensions=_qc_extensions(provenance, source_id),
    )
    return [step]
