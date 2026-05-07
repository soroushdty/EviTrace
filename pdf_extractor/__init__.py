from .extraction import extract_pdf
from .processing.sentence_processor import build_full_text


def extract_pdf_text(
    pdf_path,
    ocr: bool = False,
    ocr_text_quality_threshold: float = 0.5,
) -> str:
    """Extract all text from *pdf_path* and return it as a single string.

    Thin wrapper over the full extraction cascade, for callers that only
    need a plain text string (e.g. the OpenAI orchestrator pipeline).
    """
    blocks, _ = extract_pdf(
        str(pdf_path),
        ocr=ocr,
        ocr_text_quality_threshold=ocr_text_quality_threshold,
    )
    full_text, _ = build_full_text(blocks)
    return full_text
