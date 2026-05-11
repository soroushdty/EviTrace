from pathlib import Path
import importlib.util
import sys

from quality_control.models import QCBundle, UnifiedRecord

_EVIDENCE_PATH = Path(__file__).resolve().parents[2] / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_direct", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

build_or_load_evidence_bundle = _MODULE.build_or_load_evidence_bundle
build_chunk_evidence_package = _MODULE.build_chunk_evidence_package


def _qc_context_with_tei(tmp_path: Path) -> QCBundle:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sample Title</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>Smith</surname></author><imprint><date when="2021"/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <front><abstract><p coords="1;10,10,20,20">Abstract sentence.</p></abstract></front>
    <body>
      <div>
        <head>Introduction</head>
        <p><s coords="1;10,20,30,40">We used MIMIC-III data.</s></p>
      </div>
    </body>
  </text>
</TEI>
"""
    unified = UnifiedRecord(
        document_id="paper1",
        content={
            "exact_text": "Abstract sentence. We used MIMIC-III data.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


def test_build_evidence_bundle_prefills_study_fields(tmp_path: Path):
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    bundle = build_or_load_evidence_bundle(ctx, cfg)
    assert bundle.prefilled_fields[1] == "Smith 2021"
    assert bundle.prefilled_fields[2] == "2021"
    assert any(item["section_path"] == "Abstract" for item in bundle.evidence_items)


def test_chunk_package_uses_stable_ids(tmp_path: Path):
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    bundle = build_or_load_evidence_bundle(ctx, cfg)
    fields = [{"field_index": 8, "field_name": "Dataset / database name", "definition": "", "reviewer_question": ""}]
    package = build_chunk_evidence_package(bundle, fields, max_items=10, max_chars=2000)
    assert "S000001" in package or "S000002" in package
