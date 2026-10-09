"""privacy_carrier — the single inert carrier for privacy labels and disclosure decisions.

Every provenance node embeds exactly one :class:`PrivacyCarrier` (provenance-core
Requirement 9.1). The carrier *holds* a privacy label and a disclosure decision
produced elsewhere (``privacy-core`` owns their vocabulary); provenance never
classifies sensitivity, evaluates disclosure policy, or decides anything (9.4).

Inert by construction:

- ``label`` and ``decision`` are opaque strings, stored verbatim (9.2) — never
  normalized, mapped, defaulted, or inferred.
- Absent input yields :data:`UNKNOWN_PRIVACY`, never a substituted value (9.3).
- Conformance checking is structural only — the input is a mapping with only the
  declared keys, ``label`` is a ``str``, and ``decision`` is a ``str`` or absent.
  Non-conforming input yields ``state="rejected"`` with a reason and no label
  (9.6); the caller records the ``privacy_carrier_invalid`` finding.
- This module contains no comparison against, or branch on, a label or decision
  value. ``tests/src/provenance/test_provenance_privacy_carrier.py`` enforces
  that by AST scan.

A leaf module: it imports only the standard library, and precedes
``provenance.models`` in the dependency order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "PrivacyState",
    "PrivacyCarrier",
    "UNKNOWN_PRIVACY",
    "attach_privacy",
    "check_carrier_conformance",
]

PrivacyState = Literal["unknown", "labeled", "rejected"]

_LABEL_KEY = "label"
_DECISION_KEY = "decision"
_CARRIER_KEYS: frozenset[str] = frozenset({_LABEL_KEY, _DECISION_KEY})


@dataclass(frozen=True)
class PrivacyCarrier:
    """Privacy label and disclosure decision attached to one provenance node.

    ``supplied_by`` names the component that supplied the values and
    ``supplied_at`` is the ISO 8601 time it did so (9.5). ``rejection_reason`` is
    set only when ``state == "rejected"``.
    """

    state: PrivacyState = "unknown"
    label: str | None = None
    decision: str | None = None
    supplied_by: str | None = None
    supplied_at: str | None = None
    rejection_reason: str | None = None


UNKNOWN_PRIVACY: PrivacyCarrier = PrivacyCarrier()
"""The carrier of every node for which no privacy label has been supplied (9.3)."""


def check_carrier_conformance(carrier_input: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Check *carrier_input* against the declared carrier structure (9.6).

    Structural only: the input must be a mapping whose keys are a subset of
    ``{"label", "decision"}``; ``label`` must be present and a ``str``;
    ``decision``, when present and not ``None``, must be a ``str``. The *values*
    of ``label`` and ``decision`` are never inspected.

    Returns ``(True, None)`` when the input conforms, otherwise ``(False, reason)``.
    """
    if not isinstance(carrier_input, Mapping):
        return False, f"carrier input must be a mapping, got {type(carrier_input).__name__}"

    unknown_keys = set(carrier_input) - _CARRIER_KEYS
    if unknown_keys:
        listed = ", ".join(sorted(repr(key) for key in unknown_keys))
        return False, f"carrier input has undeclared keys: {listed}"

    if _LABEL_KEY not in carrier_input:
        return False, "carrier input is missing required key 'label'"
    label = carrier_input[_LABEL_KEY]
    if not isinstance(label, str):
        return False, f"'label' must be a str, got {type(label).__name__}"

    decision = carrier_input.get(_DECISION_KEY)
    if decision is not None and not isinstance(decision, str):
        return False, f"'decision' must be a str or None, got {type(decision).__name__}"

    return True, None


def attach_privacy(
    carrier_input: Mapping[str, Any] | None,
    *,
    supplied_by: str,
    supplied_at: str,
) -> PrivacyCarrier:
    """Build the carrier for one node from a supplied label/decision mapping.

    - ``None`` → :data:`UNKNOWN_PRIVACY` (9.3); nothing is inferred.
    - Conforming mapping → ``state="labeled"`` with ``label`` and ``decision``
      stored verbatim (9.2), plus ``supplied_by`` and ``supplied_at`` (9.5).
    - Non-conforming mapping, or non-``str`` supplier metadata →
      ``state="rejected"`` with ``rejection_reason`` and ``label``/``decision``
      left unset (9.6). The supplier metadata is retained when it is a ``str``.
    """
    if carrier_input is None:
        return UNKNOWN_PRIVACY

    conforms, reason = check_carrier_conformance(carrier_input)
    if conforms:
        reason = _supplier_metadata_issue(supplied_by, supplied_at)

    if reason is not None:
        return PrivacyCarrier(
            state="rejected",
            supplied_by=supplied_by if isinstance(supplied_by, str) else None,
            supplied_at=supplied_at if isinstance(supplied_at, str) else None,
            rejection_reason=reason,
        )

    return PrivacyCarrier(
        state="labeled",
        label=carrier_input[_LABEL_KEY],
        decision=carrier_input.get(_DECISION_KEY),
        supplied_by=supplied_by,
        supplied_at=supplied_at,
    )


def _supplier_metadata_issue(supplied_by: object, supplied_at: object) -> str | None:
    """Return a reason when the supplier metadata is not a ``str``, else ``None``."""
    if not isinstance(supplied_by, str):
        return f"'supplied_by' must be a str, got {type(supplied_by).__name__}"
    if not isinstance(supplied_at, str):
        return f"'supplied_at' must be a str, got {type(supplied_at).__name__}"
    return None
