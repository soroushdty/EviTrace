"""Block-provenance keys on the GROBID TEI blocks built inside quality_control.

``_extract_tei_payload`` is the only block producer that lives outside the
extraction pipeline, so it must stamp its own origin keys: every block it
emits carries ``source == "grobid"`` and ``ocr_derived is False`` so that the
reconciler can read the boolean uniformly, whatever the producer.

Requirements: risk-remediation 4.1
"""
from __future__ import annotations

from quality_control.quality_control import _extract_branch_payload, _extract_tei_payload

_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc><titleStmt><title>Test</title></titleStmt></fileDesc>
    <profileDesc>
      <abstract><div><p>Abstract sentence one.</p></div></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head coords="1,50,50,200,20">Introduction</head>
        <p coords="1,50,100,400,40"><s>Body sentence one.</s><s>Body sentence two.</s></p>
      </div>
      <figure coords="2,10,10,300,200"><head>Figure 1</head><figDesc>A caption.</figDesc></figure>
    </body>
  </text>
</TEI>
"""


def _assert_grobid_provenance(blocks: list[dict]) -> None:
    assert blocks, "expected at least one TEI block"
    for b in blocks:
        assert "source" in b, f"TEI block lacks 'source': {b}"
        assert "ocr_derived" in b, f"TEI block lacks 'ocr_derived': {b}"
        assert b["source"] == "grobid", b
        assert b["ocr_derived"] is False, b
        # The four base keys are still there.
        for k in ("text", "page_index", "block_bbox", "spans"):
            assert k in b, f"TEI block lacks base key {k!r}: {b}"


def test_tei_payload_blocks_carry_grobid_provenance():
    """Every block emitted for a well-formed TEI (abstract, head, paragraph,
    figure caption) is tagged source=grobid, ocr_derived=False."""
    _, _, blocks = _extract_tei_payload(_TEI)
    texts = [b["text"] for b in blocks]
    assert any("Abstract" in t for t in texts)
    assert any("Body sentence" in t for t in texts)
    assert any("caption" in t for t in texts)
    assert "Introduction" in texts
    _assert_grobid_provenance(blocks)


def test_tei_payload_fallback_blocks_carry_grobid_provenance():
    """The parse-failure fallback block and the tiny-TEI fallback block are
    tagged too."""
    _, _, malformed_blocks = _extract_tei_payload("<TEI xmlns='http://www.tei-c.org/ns/1.0'><unclosed>")
    _assert_grobid_provenance(malformed_blocks)

    tiny = "<TEI xmlns='http://www.tei-c.org/ns/1.0'><text>just text</text></TEI>"
    _, _, tiny_blocks = _extract_tei_payload(tiny)
    _assert_grobid_provenance(tiny_blocks)


def test_branch_payload_passes_tei_provenance_through():
    """The branch-payload dispatcher hands TEI strings to the TEI builder, so
    the keys reach the reconciler unchanged."""
    _, _, blocks = _extract_branch_payload(_TEI)
    _assert_grobid_provenance(blocks)
