"""
pdf_extractor/extraction/scan_detector.py
------------------------------------------
Stateless five-stage per-page scan classification.

No module-level state; no caching.  ``classify_page`` is a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PageScanClassification:
    """Result of classifying a single PDF page as native or scanned.

    Attributes
    ----------
    page_index:
        0-based page number within the document.
    is_native:
        ``True`` only when **no** stage fired (i.e., ``triggered_stages``
        is empty).
    triggered_stages:
        Ordered list of stage numbers (1–5) that fired for this page.
        Empty list → native page.
    stage_values:
        Numeric signal computed for each evaluated stage, keyed by signal
        name:

        * ``"word_count"`` — raw word count (stage 2).
        * ``"alpha_ratio"`` — alpha-char / non-whitespace ratio after
          ``clean_ocr`` (stage 3).
        * ``"font_count"`` — number of embedded fonts (stage 4).
        * ``"image_coverage"`` — image area / page area (stage 5).

        Populated only for stages that were actually evaluated (stage 1
        short-circuits, so stages 2–5 signals are absent when stage 1 fires).
    """

    page_index: int
    is_native: bool
    triggered_stages: list[int] = field(default_factory=list)
    stage_values: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_TEXT_DENSITY_THRESHOLD: int = 50
_DEFAULT_ALPHA_RATIO_THRESHOLD: float = 0.60
_DEFAULT_IMAGE_DOMINANCE_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# classify_page
# ---------------------------------------------------------------------------

def classify_page(
    page,
    text_processor,
    config: dict,
    page_index: int = 0,
) -> PageScanClassification:
    """Classify a single PDF page as native or scanned using five stages.

    The function is **stateless**: it holds no module-level variables and
    performs no caching.  Each call is independent.

    Stage evaluation order
    ----------------------
    1. **Empty text** — ``page.get_text("text").strip() == ""``.
       Fires immediately and **short-circuits** stages 2–5.
    2. **Low word count** — word count < ``config["scan_detection"]["text_density_threshold"]``.
    3. **Low alpha ratio** — (alpha chars / non-whitespace chars) in the
       ``clean_ocr``-cleaned page text < ``config["scan_detection"]["alpha_ratio_threshold"]``.
       Uses ``text_processor.clean_ocr()`` before counting characters.
    4. **No embedded fonts** — ``len(page.get_fonts()) == 0``.
    5. **Image dominance** — total image area / page area > ``config["scan_detection"]["image_dominance_threshold"]``.

    Parameters
    ----------
    page:
        A ``fitz.Page`` object (or a compatible mock in tests).  The function
        calls ``page.get_text("text")``, ``page.get_fonts()``,
        ``page.get_images()``, ``page.get_image_bbox(img_ref)``, and
        ``page.rect``.
    text_processor:
        A :class:`~utils.text_processor.TextProcessor` instance.  Its
        ``clean_ocr()`` method is called in stage 3.
    config:
        Pipeline configuration dict.  Expected key path:
        ``config["scan_detection"]["text_density_threshold"]``,
        ``config["scan_detection"]["alpha_ratio_threshold"]``,
        ``config["scan_detection"]["image_dominance_threshold"]``.
    page_index:
        0-based index of the page within its document (default: 0).

    Returns
    -------
    PageScanClassification
    """
    scan_cfg = config.get("scan_detection", {})
    text_density_threshold: int = scan_cfg.get(
        "text_density_threshold", _DEFAULT_TEXT_DENSITY_THRESHOLD
    )
    alpha_ratio_threshold: float = scan_cfg.get(
        "alpha_ratio_threshold", _DEFAULT_ALPHA_RATIO_THRESHOLD
    )
    image_dominance_threshold: float = scan_cfg.get(
        "image_dominance_threshold", _DEFAULT_IMAGE_DOMINANCE_THRESHOLD
    )

    triggered_stages: list[int] = []
    stage_values: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Stage 1 — empty text (short-circuits)
    # ------------------------------------------------------------------
    raw_text: str = page.get_text("text")
    if not raw_text.strip():
        triggered_stages.append(1)
        return PageScanClassification(
            page_index=page_index,
            is_native=False,
            triggered_stages=triggered_stages,
            stage_values=stage_values,
        )

    # ------------------------------------------------------------------
    # Stage 2 — word count
    # ------------------------------------------------------------------
    words = raw_text.split()
    word_count: int = len(words)
    stage_values["word_count"] = float(word_count)
    if word_count < text_density_threshold:
        triggered_stages.append(2)

    # ------------------------------------------------------------------
    # Stage 3 — alpha-character ratio (computed on clean_ocr'd text)
    # ------------------------------------------------------------------
    cleaned_text: str = text_processor.clean_ocr(raw_text)
    non_ws_chars = [c for c in cleaned_text if not c.isspace()]
    if non_ws_chars:
        alpha_count = sum(1 for c in non_ws_chars if c.isalpha())
        alpha_ratio: float = alpha_count / len(non_ws_chars)
    else:
        alpha_ratio = 0.0
    stage_values["alpha_ratio"] = alpha_ratio
    if alpha_ratio < alpha_ratio_threshold:
        triggered_stages.append(3)

    # ------------------------------------------------------------------
    # Stage 4 — zero embedded fonts
    # ------------------------------------------------------------------
    fonts = page.get_fonts()
    font_count: int = len(fonts)
    stage_values["font_count"] = float(font_count)
    if font_count == 0:
        triggered_stages.append(4)

    # ------------------------------------------------------------------
    # Stage 5 — image area dominance
    # ------------------------------------------------------------------
    page_rect = page.rect
    page_area: float = page_rect.width * page_rect.height
    images = page.get_images()
    total_image_area: float = 0.0
    if images and page_area > 0.0:
        for img_ref in images:
            try:
                bbox = page.get_image_bbox(img_ref)
                total_image_area += bbox.get_area()
            except Exception:
                pass
    image_coverage: float = (
        total_image_area / page_area if page_area > 0.0 else 0.0
    )
    stage_values["image_coverage"] = image_coverage
    if image_coverage > image_dominance_threshold:
        triggered_stages.append(5)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------
    is_native: bool = len(triggered_stages) == 0

    return PageScanClassification(
        page_index=page_index,
        is_native=is_native,
        triggered_stages=triggered_stages,
        stage_values=stage_values,
    )
