"""
tests/text_processing/test_normalizers_properties.py
====================================================
Property-based idempotence tests via Hypothesis (Property 3).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from text_processing.normalizers import (
    WhitespaceNormalizer,
    FullNormalizer,
    LineHealingNormalizer,
    UnicodeNormalizer,
    OcrCleaner,
    OULNormalizer,
)


_ws = WhitespaceNormalizer()
_full = FullNormalizer()
_line = LineHealingNormalizer()
_unicode_nfkc = UnicodeNormalizer(form="NFKC")
_oul = OULNormalizer()
_unicode_nfc = UnicodeNormalizer(form="NFC")
_ocr = OcrCleaner()


@settings(max_examples=100)
@given(st.text())
def test_whitespace_normalizer_idempotent(s: str):
    """WhitespaceNormalizer.normalize is idempotent."""
    assert _ws.normalize(_ws.normalize(s)) == _ws.normalize(s)


@settings(max_examples=100)
@given(st.text())
def test_full_normalizer_idempotent(s: str):
    """FullNormalizer.normalize is idempotent."""
    assert _full.normalize(_full.normalize(s)) == _full.normalize(s)


@settings(max_examples=100)
@given(st.text())
def test_line_healing_normalizer_idempotent(s: str):
    """LineHealingNormalizer.normalize is idempotent."""
    assert _line.normalize(_line.normalize(s)) == _line.normalize(s)


@settings(max_examples=100)
@given(st.text())
def test_unicode_normalizer_nfkc_idempotent(s: str):
    """UnicodeNormalizer(NFKC).normalize is idempotent."""
    assert _unicode_nfkc.normalize(_unicode_nfkc.normalize(s)) == _unicode_nfkc.normalize(s)


@settings(max_examples=100)
@given(st.text())
def test_unicode_normalizer_nfc_idempotent(s: str):
    """UnicodeNormalizer(NFC).normalize is idempotent."""
    assert _unicode_nfc.normalize(_unicode_nfc.normalize(s)) == _unicode_nfc.normalize(s)


@settings(max_examples=100)
@given(st.text())
def test_ocr_cleaner_idempotent(s: str):
    """OcrCleaner.normalize is idempotent."""
    assert _ocr.normalize(_ocr.normalize(s)) == _ocr.normalize(s)


@settings(max_examples=200)
@given(st.text())
def test_oul_normalizer_idempotent(s: str):
    """OULNormalizer.normalize is idempotent."""
    assert _oul.normalize(_oul.normalize(s)) == _oul.normalize(s)
