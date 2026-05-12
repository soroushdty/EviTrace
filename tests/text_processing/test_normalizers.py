"""
tests/text_processing/test_normalizers.py
=========================================
Example-based tests for all 5 normalizer subclasses.
"""

import pytest

from text_processing.normalizers import (
    WhitespaceNormalizer,
    FullNormalizer,
    LineHealingNormalizer,
    UnicodeNormalizer,
    OcrCleaner,
    OULNormalizer,
)


# ---------------------------------------------------------------------------
# WhitespaceNormalizer
# ---------------------------------------------------------------------------

class TestWhitespaceNormalizer:
    @pytest.fixture
    def norm(self):
        return WhitespaceNormalizer()

    def test_empty_string(self, norm):
        assert norm.normalize("") == ""

    def test_collapses_whitespace_and_lowercases(self, norm):
        assert norm.normalize("  Hello   World  ") == "hello world"

    def test_preserves_punctuation(self, norm):
        assert norm.normalize("hello, world!") == "hello, world!"

    def test_idempotent(self, norm):
        text = "  Hello   World  "
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_tabs_and_newlines(self, norm):
        assert norm.normalize("a\t\tb\n\nc") == "a b c"

    def test_unrelated_methods_raise(self, norm):
        with pytest.raises(NotImplementedError):
            norm.tokenize_words("x")
        with pytest.raises(NotImplementedError):
            norm.tokenize_sentences("x")
        with pytest.raises(NotImplementedError):
            norm.clean_ocr("x")
        with pytest.raises(NotImplementedError):
            norm.compare("a", "b")
        with pytest.raises(NotImplementedError):
            norm.extract_keywords("x")


# ---------------------------------------------------------------------------
# FullNormalizer
# ---------------------------------------------------------------------------

class TestFullNormalizer:
    @pytest.fixture
    def norm(self):
        return FullNormalizer()

    def test_empty_string(self, norm):
        assert norm.normalize("") == ""

    def test_strips_punctuation(self, norm):
        assert norm.normalize("hello, world!") == "hello world"

    def test_collapses_whitespace(self, norm):
        assert norm.normalize("  hello   world  ") == "hello world"

    def test_idempotent(self, norm):
        text = "hello, world!"
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_upper_case_with_punctuation(self, norm):
        assert norm.normalize("  UPPER-CASE: test.  ") == "uppercase test"

    def test_unrelated_methods_raise(self, norm):
        with pytest.raises(NotImplementedError):
            norm.tokenize_words("x")


# ---------------------------------------------------------------------------
# LineHealingNormalizer
# ---------------------------------------------------------------------------

class TestLineHealingNormalizer:
    @pytest.fixture
    def norm(self):
        return LineHealingNormalizer()

    def test_empty_string(self, norm):
        assert norm.normalize("") == ""

    def test_heals_mid_sentence_line_break(self, norm):
        result = norm.normalize("hello\nworld")
        assert result == "hello world"

    def test_preserves_newline_before_uppercase(self, norm):
        result = norm.normalize("end.\nStart of new")
        assert "\n" in result

    def test_collapses_multiple_newlines(self, norm):
        # Multiple newlines followed by lowercase → all healed to spaces → collapsed
        result = norm.normalize("hello\n\n\nworld")
        assert result == "hello world"

    def test_collapses_multiple_spaces(self, norm):
        result = norm.normalize("hello   world")
        assert result == "hello world"

    def test_strips_leading_trailing(self, norm):
        result = norm.normalize("  hello  ")
        assert result == "hello"

    def test_idempotent(self, norm):
        text = "hello\nworld\n\n\nfoo   bar"
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_preserves_bullet_line_break(self, norm):
        result = norm.normalize("items:\n- first\n- second")
        assert "\n- first" in result

    def test_unrelated_methods_raise(self, norm):
        with pytest.raises(NotImplementedError):
            norm.tokenize_words("x")


# ---------------------------------------------------------------------------
# UnicodeNormalizer
# ---------------------------------------------------------------------------

class TestUnicodeNormalizer:
    def test_empty_string(self):
        norm = UnicodeNormalizer()
        assert norm.normalize("") == ""

    def test_nfkc_expands_fi_ligature(self):
        norm = UnicodeNormalizer(form="NFKC")
        assert norm.normalize("\ufb01le") == "file"

    def test_nfkc_expands_fl_ligature(self):
        norm = UnicodeNormalizer(form="NFKC")
        assert norm.normalize("\ufb02oor") == "floor"

    def test_nfc_preserves_fi_ligature(self):
        norm = UnicodeNormalizer(form="NFC")
        result = norm.normalize("\ufb01le")
        assert "\ufb01" in result

    def test_whitespace_collapsed(self):
        norm = UnicodeNormalizer()
        assert norm.normalize("  hello   world  ") == "hello world"

    def test_idempotent_nfkc(self):
        norm = UnicodeNormalizer(form="NFKC")
        text = "\ufb01le \ufb02oor  caf\u00e9"
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_idempotent_nfc(self):
        norm = UnicodeNormalizer(form="NFC")
        text = "caf\u00e9 na\u00efve"
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_invalid_form_raises(self):
        with pytest.raises(ValueError, match="Unknown Unicode"):
            UnicodeNormalizer(form="bogus")

    def test_configurable_form(self):
        norm_nfc = UnicodeNormalizer(form="NFC")
        norm_nfkc = UnicodeNormalizer(form="NFKC")
        # fi ligature is treated differently
        assert norm_nfc.normalize("\ufb01le") != norm_nfkc.normalize("\ufb01le")

    def test_unrelated_methods_raise(self):
        norm = UnicodeNormalizer()
        with pytest.raises(NotImplementedError):
            norm.tokenize_words("x")


# ---------------------------------------------------------------------------
# OcrCleaner
# ---------------------------------------------------------------------------

class TestOcrCleaner:
    @pytest.fixture
    def cleaner(self):
        return OcrCleaner()

    def test_empty_string(self, cleaner):
        assert cleaner.normalize("") == ""

    def test_removes_replacement_character(self, cleaner):
        result = cleaner.normalize("hello\ufffdworld")
        assert "\ufffd" not in result
        assert result == "helloworld"

    def test_removes_null_byte(self, cleaner):
        result = cleaner.normalize("a\x00b")
        assert result == "ab"

    def test_removes_c0_controls_range(self, cleaner):
        for code in range(0x00, 0x09):
            result = cleaner.normalize(f"a{chr(code)}b")
            assert chr(code) not in result

    def test_removes_0x0b_and_0x0c(self, cleaner):
        for code in (0x0b, 0x0c):
            result = cleaner.normalize(f"a{chr(code)}b")
            assert chr(code) not in result

    def test_removes_0x0e_to_0x1f(self, cleaner):
        for code in range(0x0e, 0x20):
            result = cleaner.normalize(f"a{chr(code)}b")
            assert chr(code) not in result

    def test_preserves_tab_lf_cr(self, cleaner):
        text = "a\tb\nc\rd"
        result = cleaner.normalize(text)
        assert "\t" in result
        assert "\n" in result
        assert "\r" in result

    def test_preserves_normal_text(self, cleaner):
        text = "The quick brown fox."
        assert cleaner.normalize(text) == text

    def test_unrelated_methods_raise(self, cleaner):
        with pytest.raises(NotImplementedError):
            cleaner.tokenize_words("x")


# ---------------------------------------------------------------------------
# OULNormalizer
# ---------------------------------------------------------------------------

class TestOULNormalizer:
    @pytest.fixture
    def norm(self):
        return OULNormalizer()

    def test_empty_string(self, norm):
        assert norm.normalize("") == ""

    def test_strips_c0_controls(self, norm):
        assert "\x00" not in norm.normalize("a\x00b")
        assert "\x0b" not in norm.normalize("a\x0b b")

    def test_removes_replacement_character(self, norm):
        assert "�" not in norm.normalize("hello�world")

    def test_expands_fi_ligature(self, norm):
        # NFKC decomposes U+FB01 (ﬁ) → "fi"
        assert norm.normalize("ﬁeld") == "field"

    def test_heals_mid_sentence_line_break(self, norm):
        assert norm.normalize("measured\nby") == "measured by"

    def test_preserves_case(self, norm):
        assert norm.normalize("BRCA1 gene") == "BRCA1 gene"

    def test_preserves_scientific_punctuation(self, norm):
        result = norm.normalize("p < 0.001, 95% CI (0.42–0.68)")
        assert "0.001" in result
        assert "95%" in result
        assert "0.42" in result

    def test_preserves_doi(self, norm):
        assert norm.normalize("10.1000/demo") == "10.1000/demo"

    def test_collapses_whitespace(self, norm):
        assert norm.normalize("mean   ±   SD") == "mean ± SD"

    def test_idempotent(self, norm):
        text = "measured\nby BRCA1\x00 gene (ﬁrst� cohort)"
        assert norm.normalize(norm.normalize(text)) == norm.normalize(text)

    def test_unrelated_methods_raise(self, norm):
        with pytest.raises(NotImplementedError):
            norm.tokenize_words("x")
        with pytest.raises(NotImplementedError):
            norm.tokenize_sentences("x")
        with pytest.raises(NotImplementedError):
            norm.clean_ocr("x")
        with pytest.raises(NotImplementedError):
            norm.compare("a", "b")
        with pytest.raises(NotImplementedError):
            norm.extract_keywords("x")
