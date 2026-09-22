"""Page-classification sidecar beside the TEI cache and the cache-hit routing path.

Covers the ``PageClassificationCache`` helpers in ``pipeline.extraction_pipeline``
(``_scan_detection_config_hash``, ``_page_class_cache_read``,
``_page_class_cache_write``) and the first-ever cache-hit tests of
``build_qc_bundle``:

- sidecar round-trip preserves every ``PageScanClassification`` field;
- read returns ``None`` on absence, corrupt JSON, version mismatch and
  config-hash mismatch, each logged at INFO with the reason;
- write is a no-op with ``cache_dir=None`` and never fails the run;
- TEI hit + valid sidecar with scanned pages: no page is opened for
  classification, OCR runs, branches and routing equal a miss run;
- TEI hit + no sidecar / stale hash: classification recomputed and written;
- TEI hit + unreadable sidecar + no ``fitz``: ERROR logged, all-native default.

Requirements: risk-remediation 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SCAN_CFG = {
    "text_density_threshold": 50,
    "alpha_ratio_threshold": 0.60,
    "image_dominance_threshold": 0.85,
}

TEI = "<TEI>cached</TEI>"


def _make_qc_config(*, ocr: bool = True, tei_cache_dir: str = "") -> dict:
    return {
        "ocr": ocr,
        "quality_control": {
            "ocr": {"rasterization_dpi": 150},
            "grobid_integration": {"failure_behavior": "fallback"},
            "grobid": {
                "url": "http://localhost:8070",
                "timeout": 300,
                "tei_cache_dir": tei_cache_dir,
            },
            "scan_detection": dict(SCAN_CFG),
        },
        "text_processor": {
            "class": "text_processing.composite.DefaultTextProcessor",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
    }


def _cls(page_index: int, is_native: bool, triggered_stages=None, stage_values=None):
    return PageScanClassification(
        page_index=page_index,
        is_native=is_native,
        triggered_stages=list(triggered_stages or []),
        stage_values=dict(stage_values or {}),
    )


def _block(page_index: int, text: str) -> dict:
    return {"text": text, "page_index": page_index, "block_bbox": None, "spans": []}


def _mixed_classifications() -> list[PageScanClassification]:
    """3 pages: native, scanned (stages 2+3), native — with realistic stage values."""
    return [
        _cls(0, True, [], {"word_count": 412.0, "alpha_ratio": 0.93, "font_count": 4.0, "image_coverage": 0.1}),
        _cls(1, False, [2, 3], {"word_count": 3.0, "alpha_ratio": 0.2}),
        _cls(2, True, [], {"word_count": 380.0, "alpha_ratio": 0.9, "font_count": 4.0, "image_coverage": 0.05}),
    ]


def _mock_fitz_for_pages(page_count: int):
    mock_fitz = MagicMock()
    mock_pages = [MagicMock() for _ in range(page_count)]
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz


def _make_pdf(tmp_path: Path, name: str = "paper.pdf", content: bytes = b"%PDF-1.4 fake") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(content)
    return pdf


def _digest_of(pdf: Path) -> str:
    return hashlib.sha256(pdf.read_bytes()).hexdigest()


def _seed_tei_cache(cache_dir: Path, digest: str, tei: str = TEI) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{digest}.tei.xml").write_text(tei, encoding="utf-8")


class _Run:
    """Result of one mocked ``build_qc_bundle`` run."""

    def __init__(self):
        self.branches: list = []
        self.content: dict = {}
        self.classify_page = MagicMock()
        self.paddleocr = MagicMock()
        self.pymupdf = MagicMock()
        self.pdfplumber = MagicMock()
        self.grobid = MagicMock()
        self.fitz = None


def _run_bundle(
    pdf: Path,
    qc_config: dict,
    *,
    classifications: list[PageScanClassification],
    plumber_blocks: list[dict],
    paddle_blocks: list[dict],
    fitz_module="mock",
    grobid_side_effect=None,
) -> _Run:
    """Run build_qc_bundle with every backend mocked; ``fitz_module=None``
    makes ``import fitz`` raise ImportError; ``grobid_side_effect`` makes the
    GROBID backend raise."""
    run = _Run()
    mock_unified = MagicMock()
    mock_unified.content = run.content
    mock_bundle = MagicMock()
    mock_bundle.unified = mock_unified

    def _capture_qc(branches, *args, **kwargs):
        run.branches = list(branches)
        return mock_bundle

    run.classify_page.side_effect = list(classifications)
    run.paddleocr.return_value = paddle_blocks
    run.pymupdf.return_value = ([_block(b["page_index"], "pymupdf") for b in paddle_blocks], [])
    run.pdfplumber.return_value = plumber_blocks
    run.grobid.return_value = (TEI, [])
    run.grobid.side_effect = grobid_side_effect

    if fitz_module == "mock":
        run.fitz = _mock_fitz_for_pages(len(classifications))
    else:
        run.fitz = fitz_module  # None -> ImportError on `import fitz`

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", run.paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", run.pymupdf), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", run.pdfplumber), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", run.grobid), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", side_effect=_capture_qc), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": run.fitz}):

        mock_scan_mod.classify_page = run.classify_page
        mock_scan_mod.PageScanClassification = PageScanClassification

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(pdf_path=pdf, pdf_name=pdf.stem, qc_config=qc_config)

    return run


# ---------------------------------------------------------------------------
# Config hash
# ---------------------------------------------------------------------------


def test_config_hash_is_sha256_of_sorted_scan_detection_subsection():
    """The sidecar hash is sha256(json.dumps(scan_detection, sort_keys=True)).

    Requirements: risk-remediation 6.3
    """
    from pipeline.extraction_pipeline import _scan_detection_config_hash

    expected = hashlib.sha256(json.dumps(SCAN_CFG, sort_keys=True).encode("utf-8")).hexdigest()
    assert _scan_detection_config_hash(_make_qc_config()) == expected

    # Key order does not matter; threshold values do.
    reordered = _make_qc_config()
    reordered["quality_control"]["scan_detection"] = dict(reversed(list(SCAN_CFG.items())))
    assert _scan_detection_config_hash(reordered) == expected

    changed = _make_qc_config()
    changed["quality_control"]["scan_detection"]["text_density_threshold"] = 51
    assert _scan_detection_config_hash(changed) != expected


# ---------------------------------------------------------------------------
# Sidecar read / write
# ---------------------------------------------------------------------------


def test_sidecar_round_trip_preserves_every_field(tmp_path):
    """write then read → equal classifications incl. triggered_stages and stage_values.

    Requirements: risk-remediation 6.3, 6.4
    """
    from pipeline.extraction_pipeline import (
        _page_class_cache_read,
        _page_class_cache_write,
        _scan_detection_config_hash,
    )

    digest = "ab" * 32
    config_hash = _scan_detection_config_hash(_make_qc_config())
    classifications = _mixed_classifications()

    _page_class_cache_write(classifications, digest, tmp_path, config_hash)

    sidecar = tmp_path / f"{digest}.pages.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["scan_detection_config_hash"] == config_hash
    assert [p["page_index"] for p in payload["pages"]] == [0, 1, 2]
    assert set(payload["pages"][1]) == {"page_index", "is_native", "triggered_stages", "stage_values"}

    restored = _page_class_cache_read(digest, tmp_path, config_hash)
    assert restored == classifications
    assert restored[1].triggered_stages == [2, 3]
    assert all(isinstance(s, int) for s in restored[1].triggered_stages)
    assert restored[1].stage_values == {"word_count": 3.0, "alpha_ratio": 0.2}


def test_read_returns_none_when_cache_dir_is_none():
    from pipeline.extraction_pipeline import _page_class_cache_read

    assert _page_class_cache_read("ab" * 32, None, "h") is None


def test_read_returns_none_when_sidecar_absent(tmp_path, caplog):
    """Requirements: risk-remediation 6.1, 6.5"""
    from pipeline.extraction_pipeline import _page_class_cache_read

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        assert _page_class_cache_read("ab" * 32, tmp_path, "h") is None

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("absent" in r.getMessage() for r in infos), [r.getMessage() for r in infos]


def test_read_returns_none_on_corrupt_json(tmp_path, caplog):
    """Requirements: risk-remediation 6.1, 6.5"""
    from pipeline.extraction_pipeline import _page_class_cache_read

    digest = "ab" * 32
    (tmp_path / f"{digest}.pages.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        assert _page_class_cache_read(digest, tmp_path, "h") is None

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("unreadable" in m for m in infos), infos


def test_read_returns_none_on_malformed_pages_payload(tmp_path, caplog):
    """Valid JSON whose page entries lack required keys is treated like corruption.

    Requirements: risk-remediation 6.1, 6.5
    """
    from pipeline.extraction_pipeline import _page_class_cache_read

    digest = "ab" * 32
    payload = {"version": 1, "scan_detection_config_hash": "h", "pages": [{"page_index": 0}]}
    (tmp_path / f"{digest}.pages.json").write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        assert _page_class_cache_read(digest, tmp_path, "h") is None

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("unreadable" in m for m in infos), infos


def test_read_returns_none_on_version_mismatch(tmp_path, caplog):
    """Requirements: risk-remediation 6.1, 6.5"""
    from pipeline.extraction_pipeline import _page_class_cache_read, _page_class_cache_write

    digest = "ab" * 32
    _page_class_cache_write([_cls(0, True)], digest, tmp_path, "h")
    sidecar = tmp_path / f"{digest}.pages.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["version"] = 2
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        assert _page_class_cache_read(digest, tmp_path, "h") is None

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("version" in m for m in infos), infos


def test_read_returns_none_on_config_hash_mismatch(tmp_path, caplog):
    """Requirements: risk-remediation 6.1, 6.5"""
    from pipeline.extraction_pipeline import _page_class_cache_read, _page_class_cache_write

    digest = "ab" * 32
    _page_class_cache_write([_cls(0, True)], digest, tmp_path, "old-hash")

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        assert _page_class_cache_read(digest, tmp_path, "new-hash") is None

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("hash" in m for m in infos), infos


def test_write_is_noop_when_cache_dir_is_none(tmp_path):
    """Requirements: risk-remediation 6.3"""
    from pipeline.extraction_pipeline import _page_class_cache_write

    _page_class_cache_write([_cls(0, True)], "ab" * 32, None, "h")
    assert list(tmp_path.iterdir()) == []


def test_write_swallows_oserror_with_a_log(tmp_path, caplog):
    """A cache dir that cannot be created never fails the run.

    Requirements: risk-remediation 6.3, 6.5
    """
    from pipeline.extraction_pipeline import _page_class_cache_write

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # mkdir(parents=True, exist_ok=True) raises FileExistsError

    with caplog.at_level(logging.DEBUG, logger="pdf_extractor"):
        _page_class_cache_write([_cls(0, True)], "ab" * 32, blocker, "h")

    assert any("sidecar" in r.getMessage().lower() and r.levelno >= logging.WARNING
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# build_qc_bundle: miss path persists the sidecar
# ---------------------------------------------------------------------------


def test_miss_path_writes_sidecar_after_classification(tmp_path):
    """First run: no TEI, no sidecar → both are written, keyed by the PDF digest.

    Requirements: risk-remediation 6.3
    """
    from pipeline.extraction_pipeline import _page_class_cache_read, _scan_detection_config_hash

    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    classifications = _mixed_classifications()

    run = _run_bundle(
        pdf, qc_config,
        classifications=classifications,
        plumber_blocks=[_block(0, "p0"), _block(1, "p1"), _block(2, "p2")],
        paddle_blocks=[_block(0, "o0"), _block(1, "o1"), _block(2, "o2")],
    )

    assert run.classify_page.call_count == 3
    digest = _digest_of(pdf)
    assert (cache_dir / f"{digest}.tei.xml").exists()
    restored = _page_class_cache_read(digest, cache_dir, _scan_detection_config_hash(qc_config))
    assert restored == classifications


def test_miss_path_writes_sidecar_even_when_grobid_fails(tmp_path):
    """The sidecar is keyed by PDF digest and independent of TEI success.

    Requirements: risk-remediation 6.3
    """
    from pipeline.extraction_pipeline import _page_class_cache_read, _scan_detection_config_hash

    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    classifications = [_cls(0, True), _cls(1, True)]

    run = _run_bundle(
        pdf, qc_config,
        classifications=classifications,
        plumber_blocks=[_block(0, "p0"), _block(1, "p1")],
        paddle_blocks=[],
        grobid_side_effect=RuntimeError("grobid down"),
    )

    run.grobid.assert_called_once()
    digest = _digest_of(pdf)
    assert not (cache_dir / f"{digest}.tei.xml").exists()
    assert _page_class_cache_read(digest, cache_dir, _scan_detection_config_hash(qc_config)) == classifications


def test_miss_path_with_cache_disabled_calls_write_with_none(tmp_path):
    """tei_cache_dir="" → the sidecar write is invoked with cache_dir=None (a no-op).

    Requirements: risk-remediation 6.3
    """
    pdf = _make_pdf(tmp_path)
    with patch("pipeline.extraction_pipeline._page_class_cache_write") as mock_write:
        _run_bundle(
            pdf, _make_qc_config(tei_cache_dir=""),
            classifications=[_cls(0, True)],
            plumber_blocks=[_block(0, "p0")],
            paddle_blocks=[],
        )
    mock_write.assert_called_once()
    assert mock_write.call_args.args[2] is None
    assert list(tmp_path.iterdir()) == [pdf]


# ---------------------------------------------------------------------------
# build_qc_bundle: hit path
# ---------------------------------------------------------------------------


def test_hit_with_valid_sidecar_routes_scanned_pages_to_ocr_without_opening_pages(tmp_path):
    """Second run on a mixed PDF: TEI hit + valid sidecar → classify_page never
    called, no page opened, OCR invoked, branches/routing equal to the miss run.

    Requirements: risk-remediation 6.1, 6.2, 6.4, 6.6
    """
    classifications = _mixed_classifications()
    plumber_blocks = [_block(0, "p0"), _block(1, "p1"), _block(2, "p2")]
    paddle_blocks = [_block(0, "o0"), _block(1, "o1"), _block(2, "o2")]

    # --- run 1: miss (fresh classification; writes TEI + sidecar) ---
    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    miss = _run_bundle(pdf, qc_config, classifications=classifications,
                       plumber_blocks=plumber_blocks, paddle_blocks=paddle_blocks)
    assert miss.classify_page.call_count == 3
    assert miss.paddleocr.call_count == 1
    assert (cache_dir / f"{_digest_of(pdf)}.pages.json").exists()

    # --- run 2: hit (identical mocks; classifications only reachable via sidecar) ---
    hit = _run_bundle(pdf, qc_config, classifications=[],
                      plumber_blocks=plumber_blocks, paddle_blocks=paddle_blocks)

    hit.classify_page.assert_not_called()
    hit.fitz.open.assert_not_called()          # 6.6: no page opened for classification
    hit.grobid.assert_not_called()             # TEI came from the cache
    hit.pdfplumber.assert_called_once()        # structural extraction still runs
    hit.paddleocr.assert_called_once()         # 6.2: scanned page routed to OCR
    hit.pymupdf.assert_called_once()

    # 6.4: identical branches and routing results
    assert hit.branches == miss.branches
    assert hit.content["page_routing"] == miss.content["page_routing"]
    reasons = [r["routing_reason"] for r in hit.content["page_routing"]]
    assert reasons == ["mixed_native_page", "stages_2_3", "mixed_native_page"]
    assert [r["selected_extractor"] for r in hit.content["page_routing"]] == [
        "grobid+pdfplumber", "paddleocr+pymupdf", "grobid+pdfplumber",
    ]
    assert "all_native" not in reasons
    assert [b.source for b in hit.branches] == ["grobid", "paddleocr"]
    assert hit.content["grobid_tei_xml"] == TEI


def test_hit_without_sidecar_recomputes_and_writes_it(tmp_path):
    """Migration: TEI cached by an older version, no sidecar → classify via fitz
    and persist; routing is not all-native for the scanned page.

    Requirements: risk-remediation 6.1, 6.2, 6.3
    """
    from pipeline.extraction_pipeline import _page_class_cache_read, _scan_detection_config_hash

    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    digest = _digest_of(pdf)
    _seed_tei_cache(cache_dir, digest)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    classifications = _mixed_classifications()

    run = _run_bundle(pdf, qc_config, classifications=classifications,
                      plumber_blocks=[_block(0, "p0"), _block(1, "p1"), _block(2, "p2")],
                      paddle_blocks=[_block(1, "o1")])

    assert run.classify_page.call_count == 3
    run.fitz.open.assert_called_once_with(str(pdf))
    run.grobid.assert_not_called()
    run.paddleocr.assert_called_once()
    assert [r["routing_reason"] for r in run.content["page_routing"]] == [
        "mixed_native_page", "stages_2_3", "mixed_native_page",
    ]
    assert _page_class_cache_read(digest, cache_dir, _scan_detection_config_hash(qc_config)) == classifications


def test_hit_with_stale_config_hash_recomputes_and_rewrites(tmp_path):
    """A sidecar written under different scan-detection thresholds is a miss.

    Requirements: risk-remediation 6.1, 6.3
    """
    from pipeline.extraction_pipeline import (
        _page_class_cache_read, _page_class_cache_write, _scan_detection_config_hash,
    )

    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    digest = _digest_of(pdf)
    _seed_tei_cache(cache_dir, digest)
    # Stale sidecar: all-native under old thresholds.
    _page_class_cache_write([_cls(0, True), _cls(1, True)], digest, cache_dir, "stale-hash")

    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    fresh = [_cls(0, True), _cls(1, False, [1])]

    run = _run_bundle(pdf, qc_config, classifications=fresh,
                      plumber_blocks=[_block(0, "p0"), _block(1, "p1")],
                      paddle_blocks=[_block(1, "o1")])

    assert run.classify_page.call_count == 2
    run.paddleocr.assert_called_once()
    assert [r["routing_reason"] for r in run.content["page_routing"]] == [
        "mixed_native_page", "stage_1_empty_text",
    ]
    new_hash = _scan_detection_config_hash(qc_config)
    assert _page_class_cache_read(digest, cache_dir, new_hash) == fresh
    payload = json.loads((cache_dir / f"{digest}.pages.json").read_text(encoding="utf-8"))
    assert payload["scan_detection_config_hash"] == new_hash


def test_hit_with_unreadable_sidecar_and_no_fitz_logs_error_and_uses_all_native(tmp_path, caplog):
    """Sidecar corrupt and PyMuPDF not importable → ERROR log, all-native
    default over pdfplumber's pages, document still processed.

    Requirements: risk-remediation 6.5
    """
    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    digest = _digest_of(pdf)
    _seed_tei_cache(cache_dir, digest)
    (cache_dir / f"{digest}.pages.json").write_text("{corrupt", encoding="utf-8")
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))

    with caplog.at_level(logging.INFO, logger="pdf_extractor"):
        run = _run_bundle(pdf, qc_config, classifications=[],
                          plumber_blocks=[_block(0, "p0"), _block(1, "p1")],
                          paddle_blocks=[], fitz_module=None)

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR log for the undeterminable classification"
    assert any("all-native" in m.lower() or "all native" in m.lower() for m in errors), errors
    run.classify_page.assert_not_called()
    run.paddleocr.assert_not_called()
    assert [b.source for b in run.branches] == ["grobid", "pdfplumber"]
    routing = run.content["page_routing"]
    assert [r["page_index"] for r in routing] == [0, 1]
    assert all(r["routing_reason"] == "all_native" for r in routing)
    assert all(r["selected_extractor"] == "grobid+pdfplumber" for r in routing)
    # Nothing to persist: the all-native default is a guess, not a classification.
    assert (cache_dir / f"{digest}.pages.json").read_text(encoding="utf-8") == "{corrupt"


def test_hit_path_all_native_sidecar_routes_like_before(tmp_path):
    """Hit + valid all-native sidecar: [grobid, pdfplumber], every page all_native,
    no OCR — the pre-change behaviour for genuinely native documents.

    Requirements: risk-remediation 6.4
    """
    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))
    plumber_blocks = [_block(0, "p0"), _block(1, "p1")]
    native = [_cls(0, True, [], {"word_count": 300.0}), _cls(1, True, [], {"word_count": 250.0})]

    miss = _run_bundle(pdf, qc_config, classifications=native,
                       plumber_blocks=plumber_blocks, paddle_blocks=[])
    hit = _run_bundle(pdf, qc_config, classifications=[],
                      plumber_blocks=plumber_blocks, paddle_blocks=[])

    hit.classify_page.assert_not_called()
    hit.paddleocr.assert_not_called()
    assert hit.branches == miss.branches
    assert hit.content["page_routing"] == miss.content["page_routing"]
    assert [b.source for b in hit.branches] == ["grobid", "pdfplumber"]
    assert all(r["routing_reason"] == "all_native" for r in hit.content["page_routing"])
    for b in hit.branches[1].payload:
        assert b["source"] == "pdfplumber" and b["ocr_derived"] is False


def test_miss_path_without_fitz_does_not_persist_the_all_native_guess(tmp_path):
    """Miss path + PyMuPDF missing: pages are labelled native by default; that
    guess is not written to the sidecar, so a later run with fitz installed
    classifies for real instead of trusting it.

    Requirements: risk-remediation 6.3, 6.5
    """
    cache_dir = tmp_path / "cache"
    pdf = _make_pdf(tmp_path)
    qc_config = _make_qc_config(tei_cache_dir=str(cache_dir))

    mock_pdfplumber_mod = MagicMock()
    mock_pdfplumber_mod.open.return_value.__enter__.return_value.pages = [object(), object()]
    with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber_mod}):
        run = _run_bundle(pdf, qc_config, classifications=[],
                          plumber_blocks=[_block(0, "p0"), _block(1, "p1")],
                          paddle_blocks=[], fitz_module=None)

    run.classify_page.assert_not_called()
    assert all(r["routing_reason"] == "all_native" for r in run.content["page_routing"])
    assert [r["page_index"] for r in run.content["page_routing"]] == [0, 1]
    assert (cache_dir / f"{_digest_of(pdf)}.tei.xml").exists()
    assert not (cache_dir / f"{_digest_of(pdf)}.pages.json").exists()
