"""
QC pipeline orchestrator. Provides a generic ``run_pipeline`` entry point
that accepts injectable stage callables, and a PDF-specific
``run_quality_control`` wrapper that wires the existing five-module pipeline
(Artifact Generator, Rater, IAA Calculator, Adjudicator, Reconciler) into it.

Generic pipeline
----------------
``run_pipeline`` is domain-agnostic.  Pass any callables that satisfy the
four stage signatures to adjudicate between agents, LLM outputs, or any set
of branch outputs — not just PDF extraction results.

PDF pipeline
------------
``run_quality_control`` builds PDF-specific stage closures and delegates to
``run_pipeline``.  Metrics tracking is layered on top via
``ctx.metrics_hierarchy``.

Public API
----------
- run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config) -> QCBundle
- run_quality_control(branches, document_id, config) -> QCBundle
"""

from __future__ import annotations

import importlib
import logging
from typing import Callable

from . import rater, iaa_calculator, adjudicator, reconciler
from .checks import (
    ExtractorAgreementCheck,
    SemanticSourceVerificationCheck,
    SourceTextPresenceCheck,
)
from .local_metrics import ExtractionCoverageReport
from .models import (
    AdjudicationRules,
    DocumentAlignment,
    Candidate,
    InterRaterMetrics,
    QCBundle,
    QualityMetrics,
    SemanticLayer,
    StructuralLayer,
    UnifiedRecord,
    VerificationResult,
)
from .builtin_impls import (
    AdjudicationDecision,
    InterRaterReport,
    QualityReport,
)

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# Generic pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    branches: list[Candidate],
    *,
    rater_fn: Callable[[Candidate, list[Candidate], int, dict], QualityMetrics],
    iaa_fn: Callable[[list[QualityMetrics], dict], InterRaterMetrics],
    adjudicator_fn: Callable[[list[QualityMetrics], InterRaterMetrics, dict], AdjudicationRules],
    reconciler_fn: Callable[[AdjudicationRules, list[Candidate], dict], UnifiedRecord],
    config: dict | None = None,
) -> QCBundle:
    """Generic four-stage QC pipeline with injectable stage callables.

    Each stage callable receives the outputs of the previous stages plus the
    shared config dict, making it straightforward to swap in custom
    implementations for any domain — LLM attribute extraction, multi-agent
    adjudication, or any other use case beyond PDF text extraction.

    Stage signatures
    ----------------
    rater_fn(branch, branches, index, config) -> QualityMetrics
        Called once per branch.  Must call ``passes_check`` internally and set
        ``report.status`` to ``"pass"`` or ``"fail"`` before returning.

    iaa_fn(reports, config) -> InterRaterMetrics
        Receives all reports after every branch has been rated.

    adjudicator_fn(reports, iaa_metrics, config) -> AdjudicationRules
        Receives all reports and the IAA metrics; returns a decision that
        carries ``primary_extractor`` (or ``primary_agent``), ``confidence``,
        and ``rationale``.

    reconciler_fn(decision, branches, config) -> UnifiedRecord
        Produces the final reconciled output from the adjudication decision
        and the original branch payloads.

    Parameters
    ----------
    branches:
        List of branch outputs (one per agent / extractor).
    rater_fn:
        Per-branch rating callable.
    iaa_fn:
        Inter-rater agreement callable.
    adjudicator_fn:
        Adjudication callable.
    reconciler_fn:
        Reconciliation callable.
    config:
        Shared config dict passed verbatim to every stage.  Defaults to ``{}``.

    Returns
    -------
    QCBundle
        Fully populated context with ``branches``, ``reports``,
        ``iaa_metrics``, ``decision``, and ``unified`` set.

    Raises
    ------
    TypeError
        If ``branches`` is not a list.
    """
    if not isinstance(branches, list):
        raise TypeError(f"branches must be a list, got {type(branches).__name__!r}")

    config = config or {}
    ctx = QCBundle(branches=branches)
    logger.debug(
        "QC run_pipeline: %d branches (sources=%s)",
        len(branches), [getattr(b, "source", "?") for b in branches],
    )

    for i, branch in enumerate(branches):
        report = rater_fn(branch, branches, i, config)
        ctx.reports.append(report)
        branch.status = "pass" if report.status == "pass" else "fail"
        logger.debug(
            "QC stage 1 (rater) branch %d source=%s status=%s",
            i, getattr(branch, "source", "?"), branch.status,
        )

    ctx.iaa_metrics = iaa_fn(ctx.reports, config)
    logger.debug("QC stage 2 (IAA) complete: %s", type(ctx.iaa_metrics).__name__)
    ctx.decision = adjudicator_fn(ctx.reports, ctx.iaa_metrics, config)
    logger.debug(
        "QC stage 3 (adjudicator) complete: primary=%s, confidence=%s",
        getattr(ctx.decision, "primary_extractor", "?"),
        getattr(ctx.decision, "confidence", "?"),
    )
    ctx.unified = reconciler_fn(ctx.decision, branches, config)
    logger.debug("QC stage 4 (reconciler) complete")

    return ctx


# ---------------------------------------------------------------------------
# Internal helpers (shared by PDF-specific stage closures)
# ---------------------------------------------------------------------------

def _load_text_processor(config: dict) -> object:
    """Resolve and instantiate the configured TextProcessor class.

    Expects config["text_processor"]["class"] to be a fully-qualified import
    path (eg. "text_processing.composite.DefaultTextProcessor").
    If absent, defaults to text_processing.composite.DefaultTextProcessor.
    """
    tp_cfg = (config or {}).get("text_processor", {})
    class_path = tp_cfg.get("class", "text_processing.composite.DefaultTextProcessor")
    logger.debug("_load_text_processor: class_path=%s", class_path)
    try:
        module_name, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls(config=tp_cfg)
    except Exception as exc:  # pragma: no cover - defensive path
        raise ImportError(f"Could not load TextProcessor class {class_path}: {exc}")


def _coerce_page_index(page_index: object) -> int:
    try:
        return int(page_index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _extract_tei_payload(tei_xml: str) -> tuple[str, dict[int, str], list[dict]]:
    """Parse a GROBID TEI XML string into (full_text, page_texts, blocks).

    Uses the ``coords`` attribute on TEI elements (format ``"page;x0,y0,x1,y1"``;
    1-indexed page) to route each sentence / paragraph to its real PDF page.
    Elements without coords are routed to page 0, which is correct for
    abstracts and front matter. This makes per-page QC metrics (min chars per
    page, extraction coverage ratio) compare apples to apples against the
    pdfplumber branch, instead of comparing pdfplumber's pages against the
    whole TEI XML as one giant "page".

    Falls back to treating the raw XML as plain text on parse failure so a
    malformed TEI never crashes the rater.
    """
    import xml.etree.ElementTree as _ET  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    tei_ns = "http://www.tei-c.org/ns/1.0"
    ns = f"{{{tei_ns}}}"

    try:
        root = _ET.fromstring(tei_xml)
    except _ET.ParseError:
        return tei_xml, {0: tei_xml} if tei_xml.strip() else {}, (
            [{"text": tei_xml, "page_index": 0, "block_bbox": None, "spans": []}]
            if tei_xml.strip() else []
        )

    def _page_from_coords(coords: str) -> int:
        if not coords:
            return 0
        first = coords.strip().split()[0]
        parts = first.split(";")
        if len(parts) != 2:
            return 0
        try:
            return max(0, int(parts[0]) - 1)  # 1-indexed -> 0-indexed
        except ValueError:
            return 0

    def _text(elem: _ET.Element) -> str:
        return _re.sub(r"\s+", " ", "".join(elem.itertext()).strip())

    blocks: list[dict] = []
    page_texts: dict[int, list[str]] = {}
    text_parts: list[str] = []

    # Abstract paragraphs.
    for p in root.findall(f".//{ns}abstract//{ns}p"):
        t = _text(p)
        if not t:
            continue
        page = _page_from_coords(p.attrib.get("coords", ""))
        blocks.append({"text": t, "page_index": page, "block_bbox": None, "spans": []})
        page_texts.setdefault(page, []).append(t)
        text_parts.append(t)

    # Body: sentences if present, otherwise paragraphs.
    body = root.find(f".//{ns}body")
    if body is not None:
        sentences = list(body.findall(f".//{ns}s"))
        if sentences:
            # Build a sentence→page map from parent <p> coords so that sentences
            # without their own coords (when 's' is absent from teiCoordinates)
            # still get the right page instead of defaulting to 0.
            sent_page: dict[int, int] = {}
            for p in body.findall(f".//{ns}p"):
                p_page = _page_from_coords(p.attrib.get("coords", ""))
                for s in p.findall(f"{ns}s"):
                    sent_page[id(s)] = p_page

            for s in sentences:
                t = _text(s)
                if not t:
                    continue
                coord = s.attrib.get("coords", "")
                page = _page_from_coords(coord) if coord else sent_page.get(id(s), 0)
                blocks.append({"text": t, "page_index": page, "block_bbox": None, "spans": []})
                page_texts.setdefault(page, []).append(t)
                text_parts.append(t)
        else:
            for p in body.findall(f".//{ns}p"):
                t = _text(p)
                if not t:
                    continue
                page = _page_from_coords(p.attrib.get("coords", ""))
                blocks.append({"text": t, "page_index": page, "block_bbox": None, "spans": []})
                page_texts.setdefault(page, []).append(t)
                text_parts.append(t)

        # Figure captions and headings contribute to coverage too.
        for fig in body.findall(f".//{ns}figure"):
            cap = fig.find(f".//{ns}figDesc")
            if cap is None:
                continue
            t = _text(cap)
            if not t:
                continue
            page = _page_from_coords(fig.attrib.get("coords", ""))
            blocks.append({"text": t, "page_index": page, "block_bbox": None, "spans": []})
            page_texts.setdefault(page, []).append(t)
            text_parts.append(t)

        for head in body.findall(f".//{ns}head"):
            t = _text(head)
            if not t:
                continue
            page = _page_from_coords(head.attrib.get("coords", ""))
            blocks.append({"text": t, "page_index": page, "block_bbox": None, "spans": []})
            page_texts.setdefault(page, []).append(t)
            text_parts.append(t)

    if not blocks:
        # Tiny / malformed TEI: fall back to stripped document text.
        txt = _text(root)
        if txt:
            blocks.append({"text": txt, "page_index": 0, "block_bbox": None, "spans": []})
            page_texts.setdefault(0, []).append(txt)
            text_parts.append(txt)

    return (
        "\n".join(text_parts),
        {p: "\n".join(vals) for p, vals in page_texts.items()},
        blocks,
    )


def _extract_branch_payload(payload: object) -> tuple[str, dict[int, str], list[dict]]:
    """Extract full text, per-page texts, and block-like dicts from a branch payload.

    Recognises three payload shapes:

    - ``list[BlockDict]`` — pdfplumber / PyMuPDF / PaddleOCR. Blocks already
      carry ``page_index`` so per-page bucketing is trivial.
    - ``dict`` with ``"blocks"`` or ``"text"`` — generic wrapper.
    - ``str`` — either TEI XML (detected by the ``<TEI`` root prefix) or a
      plain text dump. TEI is parsed into per-page blocks via
      :func:`_extract_tei_payload` so the QC rater compares GROBID's real
      text-per-page against pdfplumber's text-per-page. A non-TEI string is
      treated as a single page-0 block (the previous behaviour).
    """
    blocks: list[dict] = []

    if isinstance(payload, str):
        stripped = payload.lstrip()
        if stripped.startswith("<?xml") or stripped.startswith("<TEI") or "http://www.tei-c.org/ns/1.0" in stripped[:2048]:
            return _extract_tei_payload(payload)
        full_text = payload
        page_texts = {0: payload} if payload.strip() else {}
        if payload.strip():
            blocks = [{"text": payload, "page_index": 0, "block_bbox": None, "span_bboxes": []}]
        return full_text, page_texts, blocks

    if isinstance(payload, list):
        blocks = [block for block in payload if isinstance(block, dict)]
    elif isinstance(payload, dict):
        candidate_blocks = payload.get("blocks")
        if isinstance(candidate_blocks, list):
            blocks = [block for block in candidate_blocks if isinstance(block, dict)]
        elif isinstance(payload.get("text"), str):
            full_text = payload.get("text", "")
            page_texts = {0: full_text} if full_text.strip() else {}
            if full_text.strip():
                blocks = [{"text": full_text, "page_index": 0, "block_bbox": None, "span_bboxes": []}]
            return full_text, page_texts, blocks

    page_texts_acc: dict[int, list[str]] = {}
    text_parts: list[str] = []
    for block in blocks:
        text = block.get("text", "")
        if text:
            text_parts.append(text)
        page_index = _coerce_page_index(block.get("page_index", 0))
        if text:
            page_texts_acc.setdefault(page_index, []).append(text)

    return (
        "\n".join(text_parts),
        {pi: "\n".join(vals) for pi, vals in page_texts_acc.items()},
        blocks,
    )


def _build_native_page_texts(branches: list[Candidate], current_index: int) -> dict[int, str]:
    """Build a best-effort native backend page-text map from the other branches."""
    native_page_texts: dict[int, list[str]] = {}
    for index, branch in enumerate(branches):
        if index == current_index:
            continue
        _, page_texts, _ = _extract_branch_payload(branch.payload)
        for page_index, text in page_texts.items():
            if text:
                native_page_texts.setdefault(page_index, []).append(text)

    return {
        page_index: "\n".join(values)
        for page_index, values in native_page_texts.items()
    }


def _build_local_metrics_report(
    branch: Candidate,
    branches: list[Candidate],
    branch_index: int,
    config: dict,
    text_processor,
) -> ExtractionCoverageReport:
    """Create and evaluate the local metrics report for a single branch."""
    full_text, page_texts, blocks = _extract_branch_payload(branch.payload)
    sentence_records = [
        {"sentence": sentence}
        for sentence in (text_processor.tokenize_sentences(full_text) if text_processor else [])
    ]
    native_page_texts = _build_native_page_texts(branches, branch_index)

    report = ExtractionCoverageReport(
        config=config,
        blocks=blocks,
        sentence_records=sentence_records,
        full_pdf_text=full_text,
        page_texts=page_texts,
        native_page_texts=native_page_texts,
    )
    report.passes_check()
    return report


def _build_placeholder_sentence_store(full_text: str, text_processor) -> dict:
    """Return a scaffold sentence store used only to record semantic verification attempts."""
    first_sentence = (
        (text_processor.tokenize_sentences(full_text)[:1] if text_processor else [])
    )
    return {
        "pdf_path": "",
        "sentences": first_sentence,
        "pages": [0] if first_sentence else [],
        "block_bboxes": [None] if first_sentence else [],
        "span_bboxes": [[]] if first_sentence else [],
        "embeddings": [],
        "faiss_index": None,
    }


# ---------------------------------------------------------------------------
# PDF-specific pipeline (wraps run_pipeline with concrete stage closures)
# ---------------------------------------------------------------------------

def run_quality_control(
    branches: list[Candidate],
    document_id: str,
    config: dict,
    *,
    exact_match_fn: "Callable | None" = None,
    semantic_search_fn: "Callable | None" = None,
) -> QCBundle:
    """PDF-specific QC pipeline built on top of ``run_pipeline``.

    Accepts a list of :class:`~quality_control.models.Candidate` instances
    (one per extractor branch), wires the five-module PDF pipeline into the
    generic ``run_pipeline`` orchestrator, and returns a fully populated
    :class:`~quality_control.models.QCBundle`.

    Metrics hierarchy
    -----------------
    The pipeline runs three layers of quality checks, independent of the
    extractor hierarchy:

    - **Extraction coverage** — cheap heuristics, always run.
    - **Source text verification** (exact/fuzzy text comparison) — run on
      borderline extraction-coverage results (1–2 triggered metrics).
      Requires ``exact_match_fn`` to be provided; skipped when ``None``.
    - **Semantic verification** (FAISS semantic comparison) — scaffolded; not
      yet wired into adjudication.  Requires ``semantic_search_fn`` to be
      provided; skipped when ``None``.

    Parameters
    ----------
    branches:
        List of extractor branch outputs.
    document_id:
        Stable document identifier (non-empty string).
    config:
        Loaded pipeline config dict.
    exact_match_fn:
        Optional callable ``(sentence, full_text, page_texts, blocks) -> result``
        used for source-text verification search.  When ``None``, source-text
        verification is skipped.
    semantic_search_fn:
        Optional callable used for semantic verification search.  When ``None``,
        semantic verification is skipped even when ``semantic_verification.enabled``
        is ``True``.

    Returns
    -------
    QCBundle
        Fully populated context with ``branches``, ``reports``,
        ``iaa_metrics``, ``decision``, and ``unified`` set.

    Raises
    ------
    TypeError
        If ``branches`` is not a list or ``document_id`` is not a non-empty str.
    """
    if not isinstance(branches, list):
        raise TypeError(f"branches must be a list, got {type(branches).__name__!r}")
    if not isinstance(document_id, str) or not document_id:
        raise TypeError(
            f"document_id must be a non-empty str, got {type(document_id).__name__!r}"
        )

    logger.info("QC pipeline start: document_id=%s", document_id)

    # --- PDF-specific state captured by stage closures ---
    borderline_branches: list[tuple[int, Candidate, ExtractionCoverageReport]] = []
    metrics_hierarchy: dict = {"extraction_coverage": [], "source_text_verification": [], "semantic_verification": {}}

    text_processor = _load_text_processor(config)

    # --- Stage 1: PDF rater (ExtractionCoverageReport) ---
    # Rater policy: a branch passes when the FRACTION of triggered metrics
    # is below a configurable threshold (default 0.5 = majority must pass).
    # Previously the stub always returned status=None, which the generic
    # pipeline coerced to "fail", so EVERY branch failed adjudication.
    # The old strict "any triggered -> fail" was too fragile: on a clean
    # scientific paper, a single noisy metric (Greek letters tripping
    # weird_char_ratio, inline "[1]" citations tripping references_in_body)
    # would sink an otherwise perfect branch.
    rater_cfg = (config.get("quality_control") or {}).get("rater") or {}
    max_triggered_fraction = float(rater_cfg.get("max_triggered_fraction", 0.5))

    def _pdf_rater_fn(
        branch: Candidate,
        all_branches: list[Candidate],
        index: int,
        cfg: dict,
    ) -> QualityMetrics:
        report = _build_local_metrics_report(branch, all_branches, index, cfg, text_processor)
        total = len(report.metric_records) or 1
        triggered = sum(1 for m in report.metric_records if m.triggered)
        triggered_fraction = triggered / total
        passed = triggered_fraction <= max_triggered_fraction
        report.status = "pass" if passed else "fail"
        logger.debug(
            "rater: source=%s index=%d triggered=%d/%d fraction=%.2f threshold=%.2f status=%s",
            getattr(branch, "source", "?"), index, triggered, total,
            triggered_fraction, max_triggered_fraction, report.status,
        )
        metrics_hierarchy["extraction_coverage"].append(
            {"source": branch.source, "index": branch.index, "report": report}
        )
        if 0 < triggered <= 2:
            borderline_branches.append((index, branch, report))
        return report

    # --- Stage 2: IAA ---
    def _pdf_iaa_fn(reports: list[QualityMetrics], cfg: dict) -> InterRaterMetrics:  # noqa: ARG001
        iaa = InterRaterReport()
        iaa.compute(reports)
        return iaa

    # --- Stage 3: Adjudication ---
    def _pdf_adjudicator_fn(
        reports: list[QualityMetrics],
        iaa_metrics: InterRaterMetrics,
        cfg: dict,  # noqa: ARG001
    ) -> AdjudicationRules:
        # Maintain legacy AdjudicationDecision behavior for now
        decision = AdjudicationDecision()
        decision.adjudicate(reports, iaa_metrics)
        return decision

    # --- Stage 4: Reconciliation (strategy-driven, extractor-agnostic call site) ---
    def _build_reconciler_artifact(branch: Candidate | None) -> dict:
        if branch is None:
            return {"document_id": document_id, "blocks": []}
        full_text, _page_texts, blocks = _extract_branch_payload(branch.payload)
        return {
            "document_id": document_id,
            "source": branch.source,
            "index": branch.index,
            "text": full_text,
            "blocks": blocks,
        }

    def _as_adjudication_decisions(decision_obj: AdjudicationRules | dict | None) -> dict:
        if isinstance(decision_obj, dict):
            confidence = float(decision_obj.get("confidence", 1.0))
            primary = str(decision_obj.get("primary_extractor", "primary"))
            rationale = str(decision_obj.get("rationale", "strategy-driven reconciliation"))
            return {
                "primary_extractor": primary,
                "confidence": confidence,
                "rationale": rationale,
            }
        confidence = float(getattr(decision_obj, "confidence", 1.0))
        primary = str(getattr(decision_obj, "primary_extractor", "primary"))
        rationale = str(getattr(decision_obj, "rationale", "strategy-driven reconciliation"))
        return {
            "primary_extractor": primary,
            "confidence": confidence,
            "rationale": rationale,
        }

    def _pdf_reconciler_fn(
        decision: AdjudicationRules,
        all_branches: list[Candidate],
        cfg: dict,
    ) -> UnifiedRecord:
        from quality_control.concerns import (
            DEFAULT_SECTION_VERIFICATION,
            DEFAULT_TABLE_FIGURE_MERGE,
            DEFAULT_TEXT_FIDELITY,
        )

        grobid_branch = next(
            (b for b in all_branches if b.extractor == "grobid"),
            None,
        )
        secondary_branch = next(
            (b for b in all_branches if b.extractor in {"pdfplumber", "pymupdf"}),
            None,
        )

        primary_artifact = _build_reconciler_artifact(grobid_branch)
        secondary_artifact = _build_reconciler_artifact(secondary_branch)

        updated_unified = reconciler.reconcile(
            primary_artifact=primary_artifact,
            secondary_artifact=secondary_artifact,
            adjudication_decisions=_as_adjudication_decisions(decision),
            config=cfg,
            text_fidelity_strategy=DEFAULT_TEXT_FIDELITY,
            section_strategy=DEFAULT_SECTION_VERIFICATION,
            table_figure_strategy=DEFAULT_TABLE_FIGURE_MERGE,
            text_processor=text_processor,
        )

        if (
            text_processor is not None
            and updated_unified.semantic is not None
            and not updated_unified.semantic.sentences
        ):
            for para in updated_unified.semantic.paragraphs:
                para_text = para.get("text", "")
                page_index = para.get("page_index", 0)
                for sentence in text_processor.tokenize_sentences(para_text):
                    updated_unified.semantic.sentences.append(
                        {
                            "text": sentence,
                            "page_index": page_index,
                            "ocr_derived": False,
                        }
                    )

        # If any branch exists, enforce non-None typed layers.
        if all_branches:
            if updated_unified.semantic is None:
                updated_unified.semantic = SemanticLayer()
            if updated_unified.structural is None:
                updated_unified.structural = StructuralLayer()
            if updated_unified.alignment is None:
                updated_unified.alignment = DocumentAlignment()

        return updated_unified

    # --- Run the generic pipeline ---
    ctx = run_pipeline(
        branches,
        rater_fn=_pdf_rater_fn,
        iaa_fn=_pdf_iaa_fn,
        adjudicator_fn=_pdf_adjudicator_fn,
        reconciler_fn=_pdf_reconciler_fn,
        config=config,
    )
    ctx.metrics_hierarchy = metrics_hierarchy

    qc_cfg = config.get("quality_control", {})
    stv_cfg = qc_cfg.get("source_text_verification", {})
    sem_cfg = qc_cfg.get("semantic_verification", {})
    ea_cfg = sem_cfg.get("extractor_agreement", {})

    stv_enabled: bool = stv_cfg.get("enabled", True)
    sem_enabled: bool = sem_cfg.get("enabled", False)
    ea_enabled: bool = ea_cfg.get("enabled", False)

    _SENTINEL_EVIDENCE = {
        "found_sentence": None,
        "page_index": None,
        "prefix": None,
        "suffix": None,
        "block_bbox": None,
        "span_bboxes": None,
    }

    # --- Source text verification ---
    if not stv_enabled:
        # Bypass: record a passing sentinel result
        metrics_hierarchy["source_text_verification"].append(
            VerificationResult(
                check_name="source_text_presence",
                status="skipped",
                score=1.0,
                evidence=dict(_SENTINEL_EVIDENCE),
                details={},
            )
        )
    elif exact_match_fn is not None:
        stv_check = SourceTextPresenceCheck(matcher=exact_match_fn)
        for branch_index, branch, report in borderline_branches:
            exact_sentence = (
                report.sentence_records[0]["sentence"]
                if report.sentence_records
                else report.full_pdf_text
            )
            if not exact_sentence:
                continue

            for candidate_index, candidate_branch in enumerate(branches):
                if candidate_index == branch_index:
                    continue

                candidate_text, candidate_page_texts, candidate_blocks = _extract_branch_payload(
                    candidate_branch.payload
                )
                vr = stv_check.run(
                    exact_sentence,
                    candidate_text,
                    candidate_page_texts,
                    candidate_blocks,
                )
                metrics_hierarchy["source_text_verification"].append(vr)
                if vr.status == "verified":
                    break

            else:
                continue
            break

    # --- Semantic verification ---
    if not sem_enabled:
        # Bypass: record a passing sentinel result
        metrics_hierarchy["semantic_verification"]["result"] = VerificationResult(
            check_name="semantic_source_verification",
            status="skipped",
            score=1.0,
            evidence=dict(_SENTINEL_EVIDENCE),
            details={},
        )
    elif semantic_search_fn is not None and borderline_branches:
        on_index_unavailable: str = sem_cfg.get("on_index_unavailable", "skip")
        similarity_threshold: float = sem_cfg.get("similarity_threshold", 0.85)

        sem_check = SemanticSourceVerificationCheck(
            matcher=semantic_search_fn,
            on_index_unavailable=on_index_unavailable,
        )

        branch_index, branch, report = borderline_branches[0]
        query_sentence = (
            report.sentence_records[0]["sentence"]
            if report.sentence_records
            else report.full_pdf_text
        )
        if query_sentence:
            sentence_store = _build_placeholder_sentence_store(
                report.full_pdf_text, text_processor
            )
            _, candidate_page_texts, _ = _extract_branch_payload(branch.payload)
            try:
                sem_vr = sem_check.run(
                    query_sentence,
                    sentence_store,
                    lambda query_text, model=None, query_prefix="": None,
                    similarity_threshold,
                    candidate_page_texts,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Semantic verification failed for document_id=%s: %s",
                    document_id,
                    exc,
                )
                sem_vr = VerificationResult(
                    check_name="semantic_source_verification",
                    status="unavailable",
                    score=0.0,
                    evidence=dict(_SENTINEL_EVIDENCE),
                    details={"error": str(exc)},
                )
            metrics_hierarchy["semantic_verification"]["result"] = sem_vr

    # --- Extractor agreement (optional, observational only) ---
    if ea_enabled and exact_match_fn is not None:
        ea_check = ExtractorAgreementCheck(
            exact_matcher=exact_match_fn,
            semantic_matcher=semantic_search_fn if sem_enabled else None,
        )
        # Use the first two branches for agreement comparison
        primary_blocks: list = []
        candidate_blocks: list = []
        if len(branches) >= 1:
            _, _, primary_blocks = _extract_branch_payload(branches[0].payload)
        if len(branches) >= 2:
            _, _, candidate_blocks = _extract_branch_payload(branches[1].payload)
        try:
            ea_result = ea_check.run(primary_blocks, candidate_blocks, config)
        except ImportError as exc:
            logger.warning(
                "ExtractorAgreementCheck failed for document_id=%s: %s",
                document_id,
                exc,
            )
            ea_result = {
                "status": "error",
                "error": str(exc),
            }
        metrics_hierarchy["semantic_verification"]["extractor_agreement"] = ea_result

    logger.info("QC pipeline complete: document_id=%s", document_id)
    return ctx
