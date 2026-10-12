"""Shared test helpers for the extraction-contract-hardening spec.

Imported as ``tests.helpers.extraction_contract_helpers`` (the repo root is put on
``sys.path`` by the root ``conftest.py``; ``tests/`` directories have no ``__init__.py``,
so sibling test modules cannot import each other and shared code lives here, next to
``grobid_tei.py``).

What this module provides
-------------------------
* **Builders**: :func:`make_selected_items` / :func:`build_package_text` (an evidence
  package with the pipeline's envelope, from a list of ids), :func:`build_bundle` (an
  ``EvidenceBundle`` whose ``evidence_map`` matches the ids), :func:`extraction_item` /
  :func:`build_model_output` (the model's ``{"extractions": [...]}`` text).
* **Scripted model call**: :class:`ScriptedExtractChunk`, a replacement for
  ``agents.openai.api_client.extract_chunk`` that records every call and returns scripted
  responses in order.
* **Message reader**: :func:`evidence_ids_in_user_message` reads the evidence ids from a
  user message built by the real prompt builder by parsing what sits between the package
  markers. It deliberately does not use the pipeline's own shown-set code, so it can be
  used to cross-check that code (Requirement 10.3).
* **Resolver**: :func:`resolve_module` imports a module under test at call time through
  one path, so tests tolerate the ``pipeline*`` ``sys.modules`` purge done by
  ``test_pdf_processor_helpers.py`` (Requirement 10.9: compare values, never class
  identity across re-imports).
* **Verdict oracle**: :func:`oracle_verdict` is an independent, test-side statement of
  the verdict table of Requirement 2.10; :data:`VERDICT_TABLE` is that table as data
  (one row per value kind x confidence class x citation state), and
  :func:`assert_response_compliant` checks that a scripted response is accepted by the
  oracle against a given package and absence-phrase mapping.

Independence rule: nothing here imports ``pipeline.*``, ``agents.*`` or any pipeline
constant at module level (importing the helpers must pull in no network, GROBID or OCR
code), and the oracle never calls the validator it is meant to check. The literal
vocabulary below (token, confidence labels, violation codes, package markers) is a
deliberate test-side copy of the requirements and the prompt builder; a drift between
the copies and the source is a test failure, not something to be "fixed" by importing.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, AbstractSet, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Test-side vocabulary (independent copies; see the module docstring)
# ---------------------------------------------------------------------------

NOT_REPORTED = "nr"
ASSERTED_CONFIDENCES = frozenset({"h", "m", "l"})
ALL_CONFIDENCES = frozenset({"h", "m", "l", "nr"})

CODE_UNCITED_VALUE = "uncited_value"
CODE_CONFIDENCE_NR_WITH_VALUE = "confidence_nr_with_value"
CODE_NR_VALUE_WITH_CONFIDENCE = "nr_value_with_confidence"
CODE_EMPTY_VALUE = "empty_value"
CODE_UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
CODE_EVIDENCE_NOT_SHOWN = "evidence_not_shown"
VIOLATION_CODES = (
    CODE_UNCITED_VALUE,
    CODE_CONFIDENCE_NR_WITH_VALUE,
    CODE_NR_VALUE_WITH_CONFIDENCE,
    CODE_EMPTY_VALUE,
    CODE_UNKNOWN_EVIDENCE_ID,
    CODE_EVIDENCE_NOT_SHOWN,
)

# Lines of ``agents.openai.prompts._shared_paper_prefix`` that delimit the package.
PACKAGE_BEGIN_MARKER = "EVIDENCE PACKAGE JSON:"
PACKAGE_END_MARKER = "END SHARED EVIDENCE PACKAGE"

# Positional order of ``agents.openai.api_client.extract_chunk`` (names only; used to
# read a recorded call whether an argument was passed positionally or by keyword).
EXTRACT_CHUNK_PARAMETER_NAMES: tuple[str, ...] = (
    "chunk_num",
    "source_package",
    "chunk_fields",
    "semaphore",
    "valid_location_ids",
    "prior_context",
    "pdf_name",
    "repair_prompt",
    "collector",
    "stage",
    "domain_group",
    "repair_attempt",
    "error_type",
)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_module(name: str) -> ModuleType:
    """Import and return module ``name`` at call time, through the single import path.

    Tests call this inside test bodies or fixtures instead of a module-level
    ``from pipeline... import ...`` so that, when ``test_pdf_processor_helpers.py``
    has purged the ``pipeline*`` entries of ``sys.modules`` and a later import has
    produced a second copy of a module, the test works against the copy that is
    current *now*. Compare values (codes, messages, fields), never classes.
    """
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_selected_items(
    ids: Iterable[str], *, texts: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return evidence items in the shape ``select_paper_evidence`` emits.

    The order of ``ids`` is kept (the pipeline emits items in stable id order, so
    pass sorted ids to mirror it). ``texts`` overrides the default sentence per id.
    """
    texts = texts or {}
    return [
        {
            "id": evidence_id,
            "type": "sentence",
            "section": "Results",
            "page": 1,
            "coords": None,
            "text": texts.get(evidence_id, f"Evidence sentence {evidence_id}."),
            "annotations": {},
        }
        for evidence_id in ids
    ]


def build_package_text(
    ids: Iterable[str],
    *,
    paper_id: str = "paper",
    texts: Mapping[str, str] | None = None,
    items: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Return an evidence package text for ``ids`` with the pipeline's envelope.

    The envelope is ``{"paper_id", "evidence_count", "evidence"}`` serialised with
    ``ensure_ascii=False`` and ``sort_keys=False``. Pass ``items`` to use prebuilt
    items instead of generating them from ``ids``.
    """
    selected = list(items) if items is not None else make_selected_items(ids, texts=texts)
    package = {
        "paper_id": paper_id,
        "evidence_count": len(selected),
        "evidence": selected,
    }
    return json.dumps(package, ensure_ascii=False, sort_keys=False)


def build_bundle(
    ids: Iterable[str],
    *,
    paper_id: str = "paper",
    texts: Mapping[str, str] | None = None,
    tei_xml: str = "",
    prefilled_fields: Mapping[int, str] | None = None,
    index_path: Path | str | None = None,
):
    """Return an ``EvidenceBundle`` whose ``evidence_map`` holds exactly ``ids``.

    ``evidence_items`` holds full index items (the shape of the evidence cache), and
    ``evidence_map`` maps each id to the same item, as ``build_or_load_evidence_bundle``
    produces. The class is resolved at call time (see :func:`resolve_module`).
    """
    texts = texts or {}
    id_list = list(ids)
    evidence_items = [
        {
            "id": evidence_id,
            "type": "sentence",
            "section_path": "Results",
            "page": 1,
            "coords": None,
            "xpath": f"/TEI/text/body/div/p/s[{position}]",
            "text": texts.get(evidence_id, f"Evidence sentence {evidence_id}."),
            "source_pdf": "paper.pdf",
            "score": 0,
            "annotations": {},
        }
        for position, evidence_id in enumerate(id_list, start=1)
    ]
    evidence_index = resolve_module("pipeline.evidence_index")
    return evidence_index.EvidenceBundle(
        paper_id=paper_id,
        tei_xml=tei_xml,
        evidence_items=evidence_items,
        evidence_map={item["id"]: item for item in evidence_items},
        prefilled_fields=dict(prefilled_fields or {}),
        index_path=Path(index_path) if index_path is not None else Path("unused"),
    )


def extraction_item(
    index: int, value: str, loc: Sequence[str] = (), confidence: str = "h"
) -> dict[str, Any]:
    """Return one compact model item ``{"i", "v", "loc", "c"}``."""
    return {"i": index, "v": value, "loc": list(loc), "c": confidence}


def build_model_output(
    items: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> str:
    """Return the model's response text ``{"extractions": [...]}``.

    Each entry is either an item dict (see :func:`extraction_item`) or a tuple
    ``(i, v, loc, c)``.
    """
    extractions: list[dict[str, Any]] = []
    for entry in items:
        if isinstance(entry, Mapping):
            extractions.append(dict(entry))
        else:
            index, value, loc, confidence = entry
            extractions.append(extraction_item(index, value, loc, confidence))
    return json.dumps({"extractions": extractions}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Scripted replacement for the model call
# ---------------------------------------------------------------------------


class ScriptExhaustedError(AssertionError):
    """The scripted model call was invoked more often than responses were scripted."""


@dataclass(frozen=True)
class RecordedCall:
    """One recorded invocation of a :class:`ScriptedExtractChunk`."""

    args: tuple[Any, ...]
    kwargs: Mapping[str, Any]

    def get(self, name: str, default: Any = None) -> Any:
        """Return argument ``name`` whether passed positionally or by keyword."""
        if name in self.kwargs:
            return self.kwargs[name]
        if name in EXTRACT_CHUNK_PARAMETER_NAMES:
            position = EXTRACT_CHUNK_PARAMETER_NAMES.index(name)
            if position < len(self.args):
                return self.args[position]
        return default

    @property
    def chunk_num(self) -> int:
        return self.get("chunk_num")

    @property
    def source_package(self) -> str:
        return self.get("source_package")

    @property
    def chunk_fields(self) -> list[dict]:
        return self.get("chunk_fields")

    @property
    def repair_prompt(self) -> str | None:
        return self.get("repair_prompt")

    @property
    def is_repair(self) -> bool:
        return self.repair_prompt is not None


@dataclass
class ScriptedExtractChunk:
    """Async replacement for ``extract_chunk`` returning scripted responses in order.

    Each scripted entry is a response text, an exception instance (raised), or a
    callable taking the :class:`RecordedCall` and returning the response text. Every
    invocation is appended to :attr:`calls` before the entry is consumed, so a call
    that raises is still recorded. Calling it more often than scripted raises
    :class:`ScriptExhaustedError`.

    Use it as ``patch.dict(sys.modules, {"agents.openai.api_client":
    SimpleNamespace(extract_chunk=scripted)})`` or patch the attribute on a module.
    """

    responses: Sequence[Any]
    calls: list[RecordedCall] = field(default_factory=list)

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        call = RecordedCall(args=tuple(args), kwargs=dict(kwargs))
        self.calls.append(call)
        position = len(self.calls) - 1
        if position >= len(self.responses):
            raise ScriptExhaustedError(
                f"scripted extractor called {len(self.calls)} time(s) but only "
                f"{len(self.responses)} response(s) were scripted"
            )
        entry = self.responses[position]
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry):
            return entry(call)
        return entry

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def assert_all_consumed(self) -> None:
        """Fail unless every scripted response was used."""
        assert len(self.calls) == len(self.responses), (
            f"{len(self.responses) - len(self.calls)} scripted response(s) unused "
            f"({len(self.calls)} of {len(self.responses)} consumed)"
        )


# ---------------------------------------------------------------------------
# Reading the evidence ids out of what the model is sent
# ---------------------------------------------------------------------------


def _item_ids(package: object) -> tuple[str, ...]:
    """Ids of the items of a decoded package; empty for anything not a package."""
    if not isinstance(package, dict):
        return ()
    evidence = package.get("evidence")
    if not isinstance(evidence, list):
        return ()
    return tuple(
        item["id"]
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    )


def evidence_ids_in_package_text(package_text: str) -> tuple[str, ...]:
    """Return the evidence ids of a package text, in package order.

    Text that is not a JSON evidence package (not an object, no ``evidence`` list,
    truncated) yields no ids, the contract's treatment of an unreadable package.
    Items with a missing, empty or non-string id are ignored.
    """
    try:
        return _item_ids(json.loads(package_text))
    except (TypeError, ValueError):
        return ()


def evidence_ids_in_user_message(message: str) -> tuple[str, ...]:
    """Return the evidence ids placed in a user message built by the real prompt builder.

    The package is the text between the first :data:`PACKAGE_BEGIN_MARKER` line and the
    first following blank line plus :data:`PACKAGE_END_MARKER` (the shape of
    ``_shared_paper_prefix``); its ids are read with :func:`evidence_ids_in_package_text`,
    so the two functions agree by construction on every package text, including an
    unreadable one (undecodable JSON, trailing junk after the envelope), which yields no
    ids, and surrounding JSON whitespace, which is accepted exactly as ``json.loads``
    accepts it. Text after the end marker (extraction map, prior results) is never read,
    and marker-like text cannot occur inside a decodable package, because JSON strings
    escape their newlines.

    Raises:
        ValueError: when the message has no begin marker line or no end marker after it
            (the message was not built by the prompt builder).
    """
    begin = message.find(PACKAGE_BEGIN_MARKER + "\n")
    if begin < 0:
        raise ValueError("user message has no evidence package begin marker")
    start = begin + len(PACKAGE_BEGIN_MARKER) + 1
    end = message.find("\n\n" + PACKAGE_END_MARKER, start)
    if end < 0:
        raise ValueError("user message has no evidence package end marker")
    return evidence_ids_in_package_text(message[start:end])


# ---------------------------------------------------------------------------
# The verdict oracle (Requirement 2.10), independent of the validator
# ---------------------------------------------------------------------------

KIND_ASSERTED = "asserted"
KIND_PHRASE = "phrase"
KIND_NR = "nr"
KIND_EMPTY = "empty"
VALUE_KINDS = (KIND_ASSERTED, KIND_PHRASE, KIND_NR, KIND_EMPTY)

CITES_NONE = "none"
CITES_SHOWN = "shown"
CITES_NOT_SHOWN = "not_shown"
CITATION_STATES = (CITES_NONE, CITES_SHOWN, CITES_NOT_SHOWN)

CONF_ASSERTED = "hml"
CONF_NR = "nr"
CONFIDENCE_CLASSES = (CONF_ASSERTED, CONF_NR)


@dataclass(frozen=True)
class OracleVerdict:
    """The oracle's verdict on one item.

    ``accepted`` is the table verdict. ``codes`` are the violation codes that apply
    (empty exactly when accepted): at most one *content* code from the table plus the
    id code(s) of the citation check, which applies to every value kind; a cited id
    that is outside the shown set yields ``unknown_evidence_id`` when it is also
    outside the paper's evidence index and ``evidence_not_shown`` when it is in it.
    ``kind`` is the value kind the rule table classified the value as.
    """

    accepted: bool
    codes: frozenset[str]
    kind: str

    @property
    def outcome(self) -> str:
        return "accept" if self.accepted else "reject"


def value_kind(value: str, absence_phrases: AbstractSet[str] = frozenset()) -> str:
    """Classify a value: ``nr`` token, ``empty``, ``phrase`` of the field, or ``asserted``."""
    stripped = value.strip()
    folded = stripped.casefold()
    if folded == NOT_REPORTED:
        return KIND_NR
    if not stripped:
        return KIND_EMPTY
    if any(folded == phrase.strip().casefold() for phrase in absence_phrases):
        return KIND_PHRASE
    return KIND_ASSERTED


def oracle_verdict(
    value: str,
    confidence: str,
    loc: Sequence[str] = (),
    *,
    shown: AbstractSet[str] | None,
    known: AbstractSet[str] | None = None,
    absence_phrases: AbstractSet[str] = frozenset(),
) -> OracleVerdict:
    """Return the verdict of Requirement 2.10 for one model item.

    Args:
        value: the item's ``v``.
        confidence: the item's ``c``; one of ``h``, ``m``, ``l``, ``nr`` (other labels
            are structural errors outside the table and raise ``ValueError``).
        loc: the cited evidence ids.
        shown: the ids the model was shown in this call; ``None`` skips the id check.
        known: the ids of the paper's evidence index; ``None`` means no id outside
            ``shown`` is known, so every such id is unknown.
        absence_phrases: the absence phrases of the item's own field.
    """
    if confidence not in ALL_CONFIDENCES:
        raise ValueError(f"confidence {confidence!r} is outside the verdict table")
    ids = list(loc)
    kind = value_kind(value, absence_phrases)
    asserted_confidence = confidence in ASSERTED_CONFIDENCES

    codes: set[str] = set()
    if kind == KIND_ASSERTED:
        if not asserted_confidence:
            codes.add(CODE_CONFIDENCE_NR_WITH_VALUE)
        elif not ids:
            codes.add(CODE_UNCITED_VALUE)
    elif kind == KIND_PHRASE:
        if not asserted_confidence:
            codes.add(CODE_CONFIDENCE_NR_WITH_VALUE)
    elif kind == KIND_NR:
        if asserted_confidence:
            codes.add(CODE_NR_VALUE_WITH_CONFIDENCE)
    else:  # KIND_EMPTY
        if asserted_confidence or ids:
            codes.add(CODE_EMPTY_VALUE)

    if shown is not None:
        outside = [evidence_id for evidence_id in ids if evidence_id not in shown]
        if any(known is not None and evidence_id in known for evidence_id in outside):
            codes.add(CODE_EVIDENCE_NOT_SHOWN)
        if any(known is None or evidence_id not in known for evidence_id in outside):
            codes.add(CODE_UNKNOWN_EVIDENCE_ID)

    return OracleVerdict(accepted=not codes, codes=frozenset(codes), kind=kind)


@dataclass(frozen=True)
class VerdictRow:
    """One row of Requirement 2.10 expanded to a single (kind, confidence, citation) cell."""

    kind: str
    confidence_class: str
    cites: str
    accepted: bool
    content_code: str | None  # the table's non-citation reason; None when it has none


def _row(kind, conf, cites, accepted, content_code=None) -> VerdictRow:
    return VerdictRow(kind, conf, cites, accepted, content_code)


#: Requirement 2.10 as data. Rows the requirement states for "any" cited ids are expanded
#: to the three citation states, so the table covers the full product of 4 value kinds x
#: 2 confidence classes x 3 citation states exactly once (24 rows). A "not shown" citation
#: always rejects (code: unknown or not-shown id) on top of the row's own content code.
VERDICT_TABLE: tuple[VerdictRow, ...] = (
    # asserted, not an absence phrase
    _row(KIND_ASSERTED, CONF_ASSERTED, CITES_SHOWN, True),
    _row(KIND_ASSERTED, CONF_ASSERTED, CITES_NONE, False, CODE_UNCITED_VALUE),
    _row(KIND_ASSERTED, CONF_ASSERTED, CITES_NOT_SHOWN, False),
    _row(KIND_ASSERTED, CONF_NR, CITES_NONE, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    _row(KIND_ASSERTED, CONF_NR, CITES_SHOWN, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    _row(KIND_ASSERTED, CONF_NR, CITES_NOT_SHOWN, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    # absence phrase of the field
    _row(KIND_PHRASE, CONF_ASSERTED, CITES_NONE, True),
    _row(KIND_PHRASE, CONF_ASSERTED, CITES_SHOWN, True),
    _row(KIND_PHRASE, CONF_ASSERTED, CITES_NOT_SHOWN, False),
    _row(KIND_PHRASE, CONF_NR, CITES_NONE, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    _row(KIND_PHRASE, CONF_NR, CITES_SHOWN, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    _row(KIND_PHRASE, CONF_NR, CITES_NOT_SHOWN, False, CODE_CONFIDENCE_NR_WITH_VALUE),
    # the not-reported token
    _row(KIND_NR, CONF_NR, CITES_NONE, True),
    _row(KIND_NR, CONF_NR, CITES_SHOWN, True),
    _row(KIND_NR, CONF_NR, CITES_NOT_SHOWN, False),
    _row(KIND_NR, CONF_ASSERTED, CITES_NONE, False, CODE_NR_VALUE_WITH_CONFIDENCE),
    _row(KIND_NR, CONF_ASSERTED, CITES_SHOWN, False, CODE_NR_VALUE_WITH_CONFIDENCE),
    _row(KIND_NR, CONF_ASSERTED, CITES_NOT_SHOWN, False, CODE_NR_VALUE_WITH_CONFIDENCE),
    # empty (or whitespace-only) value
    _row(KIND_EMPTY, CONF_NR, CITES_NONE, True),
    _row(KIND_EMPTY, CONF_NR, CITES_SHOWN, False, CODE_EMPTY_VALUE),
    _row(KIND_EMPTY, CONF_NR, CITES_NOT_SHOWN, False, CODE_EMPTY_VALUE),
    _row(KIND_EMPTY, CONF_ASSERTED, CITES_NONE, False, CODE_EMPTY_VALUE),
    _row(KIND_EMPTY, CONF_ASSERTED, CITES_SHOWN, False, CODE_EMPTY_VALUE),
    _row(KIND_EMPTY, CONF_ASSERTED, CITES_NOT_SHOWN, False, CODE_EMPTY_VALUE),
)


# ---------------------------------------------------------------------------
# Compliance check for scripted responses
# ---------------------------------------------------------------------------


def _decode_items(raw: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AssertionError(f"scripted response is not JSON: {exc}") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("extractions")
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise AssertionError("scripted response has no 'extractions' list of objects")
    return parsed


def assert_response_compliant(
    raw_response: str,
    package_text: str,
    absence_phrases: Mapping[int, AbstractSet[str]] | None = None,
    *,
    known: AbstractSet[str] | None = None,
) -> None:
    """Assert that the oracle accepts every item of a scripted response.

    ``package_text`` is the evidence package the call is made with (its item ids are
    the shown set). ``absence_phrases`` maps a field index to its absence phrases
    (missing indexes have none). ``known`` is the paper's evidence index when the test
    wants ``evidence_not_shown`` to be told apart from ``unknown_evidence_id``.
    The failure message names each rejected item, its field index and the codes.
    """
    phrases = absence_phrases or {}
    shown = frozenset(evidence_ids_in_package_text(package_text))
    problems: list[str] = []
    for position, item in enumerate(_decode_items(raw_response)):
        if not {"i", "v", "loc", "c"} <= item.keys():
            problems.append(f"item {position}: missing keys, has {sorted(item)}")
            continue
        if not isinstance(item["v"], str) or not isinstance(item["loc"], list):
            problems.append(f"item {position}: v must be str and loc a list")
            continue
        verdict = oracle_verdict(
            item["v"],
            item["c"],
            item["loc"],
            shown=shown,
            known=known,
            absence_phrases=phrases.get(item["i"], frozenset()),
        )
        if not verdict.accepted:
            problems.append(
                f"item {position} (field {item['i']!r}, v={item['v']!r}, c={item['c']!r}, "
                f"loc={item['loc']!r}) rejected by the oracle: {sorted(verdict.codes)}"
            )
    assert not problems, "scripted response is not contract-compliant:\n" + "\n".join(problems)


__all__ = [
    "ALL_CONFIDENCES",
    "ASSERTED_CONFIDENCES",
    "CITATION_STATES",
    "CITES_NONE",
    "CITES_NOT_SHOWN",
    "CITES_SHOWN",
    "CONFIDENCE_CLASSES",
    "CONF_ASSERTED",
    "CONF_NR",
    "EXTRACT_CHUNK_PARAMETER_NAMES",
    "NOT_REPORTED",
    "OracleVerdict",
    "PACKAGE_BEGIN_MARKER",
    "PACKAGE_END_MARKER",
    "RecordedCall",
    "ScriptExhaustedError",
    "ScriptedExtractChunk",
    "VALUE_KINDS",
    "VERDICT_TABLE",
    "VIOLATION_CODES",
    "VerdictRow",
    "assert_response_compliant",
    "build_bundle",
    "build_model_output",
    "build_package_text",
    "evidence_ids_in_package_text",
    "evidence_ids_in_user_message",
    "extraction_item",
    "make_selected_items",
    "oracle_verdict",
    "resolve_module",
    "value_kind",
]
