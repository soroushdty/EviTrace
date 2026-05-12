import csv
import json
from pathlib import Path

from artifact_generation.csv_exporter import export_all_extracted_jsons_to_csv


def test_export_all_extracted_jsons_to_csv_combines_outputs(tmp_path: Path):
    run_dir = tmp_path / "run_01"
    run_dir.mkdir()

    paper_a = run_dir / "paper_a.extracted.json"
    paper_a.write_text(
        json.dumps([
            {"source_pdf": "paper_a.pdf", "field_name": "title", "field_index": 1, "extracted_value": "Alpha"},
            {"source_pdf": "paper_a.pdf", "field_name": "year", "field_index": 2, "extracted_value": "2026"},
        ]),
        encoding="utf-8",
    )

    paper_b = run_dir / "paper_b.extracted.json"
    paper_b.write_text(
        json.dumps([
            {"source_pdf": "paper_b.pdf", "field_name": "title", "field_index": 1, "extracted_value": "Beta"},
            {"source_pdf": "paper_b.pdf", "field_name": "doi", "field_index": 3, "extracted_value": "10.1000/demo"},
        ]),
        encoding="utf-8",
    )

    output_csv = tmp_path / "combined.csv"
    export_all_extracted_jsons_to_csv(run_dir, output_csv)

    with output_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert [row["source_pdf"] for row in rows] == ["paper_a.pdf", "paper_b.pdf"]
    assert rows[0]["title"] == "Alpha"
    assert rows[0]["year"] == "2026"
    assert rows[1]["title"] == "Beta"
    # Note: sanitization now happens at JSON write time (in pipeline), not CSV read time.
    # The test assumes JSON files arrive pre-sanitized from the pipeline.
    assert rows[1]["doi"] == "10.1000/demo"
