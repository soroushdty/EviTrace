"""Unit tests closing genuine gaps for evidence index stability (task 6.4).

Scope note (task 6.4 boundary): this task's four requirement bullets span two
packages — src/pipeline/evidence_index.py + src/pipeline/validator.py (Req
3.4, 3.5) and src/agents/openai/prompts.py (Req 2.4, 2.7). Before writing
anything here, the following pre-existing coverage was read in full:

- tests/src/pipeline/test_evidence_cache.py (task 6.2) already covers Req 3.5
  thoroughly: test_cache_hit_reuses_index_without_reparsing_tei asserts the
  TEI is not re-parsed on a cache hit and that evidence_items/prefilled_fields
  are unchanged; test_cache_miss_on_pdf_content_change_triggers_reparse
  asserts a re-parse happens when the PDF content hash changes. That is a
  complete, non-duplicable treatment of Req 3.5 — nothing new is added for
  it here.
- tests/src/pipeline/test_pipeline_validator_loc.py already covers
  validate_chunk_output()/reconstruct_fields() loc-membership and provenance
  resolution, but only against small hand-built evidence_map fixtures.
- tests/src/pipeline/test_pipeline_evidence_index.py already covers
  Evidence_ID determinism/pattern (Property 6) and
  build_paper_evidence_package()'s sort order, byte-identical repeats, and
  item/char limit enforcement, but never inspects what keys end up in the
  serialized package.
- tests/src/agents/openai/test_prompts_builders.py (task 6.1) already covers
  Req 2.7 (get_system_prompt() singleton identity via
  test_get_system_prompt_returns_same_object_reference and
  test_get_system_prompt_module_level_cache_populated) and the prompts.py
  side of Req 2.4 (compute_stable_prefix ordering/determinism; prompts.py's
  builders accept no timestamp/run-id/chunk-number/pdf-name parameters)
  thoroughly.

Given that, this file adds exactly two genuinely new, non-duplicate tests:

1. Req 3.4 — an INTEGRATION test tying evidence_index.py's on-disk cached
   evidence index directly to validator.py's loc-membership validation and
   provenance reconstruction. Nothing previously proved that an Evidence_ID
   minted by evidence_index.py's real TEI-parsing/caching path is actually
   accepted and fully resolvable by validator.py once reloaded from the
   on-disk cache file — i.e. the literal Req 3.4 claim ("reconstruct full
   evidence provenance from the evidence index cached on disk"). The
   existing validator tests use hand-built evidence_map fixtures; the
   existing evidence-index tests never call into validator.py. Two tests
   below close that integration gap (positive: known cached ID resolves
   full provenance; negative: an ID absent from the cached set is rejected).

2. Req 2.4 — a pipeline-side angle on "exclude runtime metadata ... such as
   ... PDF file names ... from the Stable_Prefix". prompts.py's own
   responsibility (never accepting such parameters) is already fully tested
   in test_prompts_builders.py. The complementary pipeline-side
   responsibility — that build_paper_evidence_package()'s return value,
   which is embedded verbatim into the Stable_Prefix by
   agents.openai.prompts._shared_paper_prefix(), strips the runtime
   `source_pdf` file-path field present on every internal evidence_items
   entry before serialization — was untested. One test below closes that gap.

Req 2.7 (get_system_prompt() singleton identity) is agents.openai.prompts-only
with no distinct pipeline-side angle to test: evidence_index.py never touches
the system prompt, and task 6.1's
test_get_system_prompt_returns_same_object_reference /
test_get_system_prompt_module_level_cache_populated in
test_prompts_builders.py already fully cover it end to end. Re-testing it
here (in a file that lives under tests/src/pipeline/ purely due to this
task's filename, not because evidence_index.py has any Req-2.7-relevant
behavior) would only duplicate task 6.1's coverage with no new signal, so it
is deliberately not re-tested — this mirrors task 6.3's precedent for
cross-package property deferrals.
"""

from pathlib import Path
import importlib.util
import json
import sys

from quality_control.models import QCBundle, UnifiedRecord

_PIPELINE_SRC = Path(__file__).resolve().parents[3] / "src" / "pipeline"


def _load_module(name: str, filename: str):
    path = _PIPELINE_SRC / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_EVIDENCE_MODULE = _load_module("pipeline_evidence_index_stability", "evidence_index.py")
_VALIDATOR_MODULE = _load_module("pipeline_validator_stability", "validator.py")

build_or_load_evidence_bundle = _EVIDENCE_MODULE.build_or_load_evidence_bundle
build_paper_evidence_package = _EVIDENCE_MODULE.build_paper_evidence_package
EvidenceBundle = _EVIDENCE_MODULE.EvidenceBundle

validate_chunk_output = _VALIDATOR_MODULE.validate_chunk_output
reconstruct_fields = _VALIDATOR_MODULE.reconstruct_fields
ValidationError = _VALIDATOR_MODULE.ValidationError


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
    <body>
      <div>
        <head>Methods</head>
        <p><s coords="1;10,20,30,40">We used MIMIC-III data for cohort selection.</s></p>
        <table coords="1;10,60,30,40"><row><cell>MIMIC-III cohort size</cell></row></table>
      </div>
    </body>
  </text>
</TEI>
"""
    unified = UnifiedRecord(
        document_id="paper_stability",
        content={
            "exact_text": "We used MIMIC-III data for cohort selection.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


# ---------------------------------------------------------------------------
# Req 3.4 — Evidence_IDs minted + cached by evidence_index.py are accepted
# and fully resolvable by validator.py's loc validation/reconstruction, using
# ONLY the evidence_map reloaded from the on-disk cache file.
# ---------------------------------------------------------------------------


def test_cached_evidence_ids_validate_and_reconstruct_full_provenance(tmp_path: Path):
    """An Evidence_ID minted by evidence_index.py and reloaded from the
    on-disk cache validates as a known `loc` member and reconstructs full
    provenance (page, section_path, type) — Req 3.4's literal claim that the
    pipeline can "reconstruct full evidence provenance from the evidence
    index cached on disk".
    """
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}

    # First build: computes from TEI and writes the disk cache.
    bundle_first = build_or_load_evidence_bundle(ctx, cfg)
    assert bundle_first.index_path.exists()

    # Second call: cache-hit path reloads the evidence_map purely from the
    # JSON cache file on disk (build_or_load_evidence_bundle's
    # idx_path.exists() branch) rather than re-parsing TEI.
    bundle_cached = build_or_load_evidence_bundle(ctx, cfg)

    sentence_id = next(
        eid for eid, item in bundle_cached.evidence_map.items() if item["type"] == "sentence"
    )
    table_id = next(
        eid for eid, item in bundle_cached.evidence_map.items() if item["type"] == "table"
    )

    # Simulate a real LLM chunk response citing these cached Evidence_IDs.
    raw = (
        '{"extractions":[{"i":8,"v":"MIMIC-III","loc":["%s","%s"],"c":"h"}]}'
        % (sentence_id, table_id)
    )
    validated = validate_chunk_output(
        raw, [8], valid_location_ids=set(bundle_cached.evidence_map.keys())
    )

    lookup = {8: {"domain_group": "3. Cohort and data source", "field_name": "Dataset / database name"}}
    reconstructed = reconstruct_fields(validated, lookup, evidence_map=bundle_cached.evidence_map)

    metadata_by_id = {m["id"]: m for m in reconstructed[0]["location_metadata"]}
    assert set(metadata_by_id) == {sentence_id, table_id}
    assert metadata_by_id[sentence_id]["type"] == "sentence"
    assert metadata_by_id[sentence_id]["page"] == 1
    assert metadata_by_id[sentence_id]["section_path"] == "Methods"
    assert metadata_by_id[table_id]["type"] == "table"
    assert "MIMIC-III" in reconstructed[0]["evidence"]


def test_cached_evidence_map_rejects_ids_outside_disk_cache_membership(tmp_path: Path):
    """A loc ID absent from the reloaded on-disk cache's evidence_map must be
    rejected — proving valid_location_ids enforcement is anchored to the real
    cached ID set produced by evidence_index.py, not an arbitrary fixture.
    """
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    build_or_load_evidence_bundle(ctx, cfg)
    bundle_cached = build_or_load_evidence_bundle(ctx, cfg)

    assert "S999999" not in bundle_cached.evidence_map
    raw = '{"extractions":[{"i":8,"v":"MIMIC-III","loc":["S999999"],"c":"h"}]}'
    try:
        validate_chunk_output(raw, [8], valid_location_ids=set(bundle_cached.evidence_map.keys()))
    except ValidationError:
        pass
    else:
        raise AssertionError(
            "expected ValidationError for a loc ID absent from the cached evidence_map"
        )


# ---------------------------------------------------------------------------
# Req 2.4 — pipeline-side responsibility: build_paper_evidence_package()'s
# output is embedded verbatim into the Stable_Prefix via
# agents.openai.prompts._shared_paper_prefix(). It must exclude runtime
# metadata (here: the PDF's own file path, a filename artifact) even though
# every internal evidence_items entry carries a `source_pdf` field used
# elsewhere (e.g. figure/table crop generation via location_metadata, which
# is part of the FINAL extraction output, not the prompt).
# ---------------------------------------------------------------------------


def _bundle_with_source_pdf(tmp_path: Path) -> EvidenceBundle:
    pdf_path = str(tmp_path / "some_very_identifying_paper_name_2024.pdf")
    items = [
        {
            "id": "S000001",
            "type": "sentence",
            "section_path": "Methods",
            "page": 1,
            "coords": None,
            "text": "We used MIMIC-III data.",
            "source_pdf": pdf_path,
            "score": 10,
            "annotations": {},
        },
    ]
    return EvidenceBundle(
        paper_id="paper_runtime_meta",
        tei_xml="",
        evidence_items=items,
        evidence_map={item["id"]: item for item in items},
        prefilled_fields={},
        index_path=tmp_path / "paper_runtime_meta.evidence.json",
    )


def test_paper_evidence_package_excludes_source_pdf_filename_from_stable_prefix_input(
    tmp_path: Path,
):
    """The evidence package that flows into the Stable_Prefix must not leak
    the PDF file path — a runtime/filename artifact — even though the
    underlying evidence item carries it for other (non-prompt) purposes.
    """
    bundle = _bundle_with_source_pdf(tmp_path)
    fields = [{"field_index": 8, "field_name": "Dataset", "definition": "MIMIC-III", "reviewer_question": ""}]

    package_json = build_paper_evidence_package(bundle, fields, max_items=10, max_chars=10000)

    assert "some_very_identifying_paper_name_2024.pdf" not in package_json, (
        "build_paper_evidence_package() leaked the PDF file path into the "
        "serialized evidence package, which is embedded verbatim in the "
        "Stable_Prefix by agents.openai.prompts._shared_paper_prefix(); Req "
        "2.4 requires runtime metadata such as PDF file names be excluded "
        "from the Stable_Prefix."
    )
    parsed = json.loads(package_json)
    for item in parsed["evidence"]:
        assert "source_pdf" not in item
