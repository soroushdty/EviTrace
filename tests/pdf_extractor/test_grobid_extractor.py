"""Tests for extract_with_grobid client-side behavior."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from pdf_extractor.extraction import GROBID as grobid_mod


_TEI_NS = "http://www.tei-c.org/ns/1.0"

_MINIMAL_TEI = f"""<?xml version="1.0"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader><fileDesc><titleStmt><title>X</title></titleStmt></fileDesc></teiHeader>
  <text><body><p>hello world</p></body></text>
</TEI>"""


def _fake_session_returning(tei_xml: str, status: int = 200):
    sess = MagicMock()
    sess.post.return_value = MagicMock(
        status_code=status,
        content=tei_xml.encode("utf-8"),
        text=tei_xml,
    )
    return sess


def test_extract_with_grobid_parse_blocks_false_skips_parse(tmp_path):
    """parse_blocks=False must skip _parse_tei_to_blocks (cache-miss perf win)."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess), \
         patch.object(grobid_mod, "_parse_tei_to_blocks") as parse_spy:
        tei, blocks = grobid_mod.extract_with_grobid(
            str(pdf_path), parse_blocks=False, max_retries=0,
        )

    assert tei == _MINIMAL_TEI
    assert blocks == []
    parse_spy.assert_not_called()


def test_extract_with_grobid_parse_blocks_true_parses(tmp_path):
    """Default parse_blocks=True still produces blocks for standalone callers."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess):
        tei, blocks = grobid_mod.extract_with_grobid(str(pdf_path), max_retries=0)

    assert tei == _MINIMAL_TEI
    assert isinstance(blocks, list) and len(blocks) > 0


def test_grobid_call_passes_no_proxy(tmp_path):
    """All GROBID POSTs must set proxies={} so HTTP_PROXY can't intercept loopback."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess):
        grobid_mod.extract_with_grobid(
            str(pdf_path), parse_blocks=False, max_retries=0,
        )

    kwargs = fake_sess.post.call_args.kwargs
    assert kwargs.get("proxies") == {"http": None, "https": None}


def test_grobid_extract_default_generate_ids_is_false(tmp_path):
    """Function default for generate_ids should match config (False).

    A True default would silently re-enable a flag callers expect to be off.
    """
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess):
        grobid_mod.extract_with_grobid(
            str(pdf_path), parse_blocks=False, max_retries=0,
        )

    form_data = fake_sess.post.call_args.kwargs["data"]
    assert form_data["generateIDs"] == "0"
