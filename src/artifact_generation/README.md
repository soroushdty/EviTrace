# artifact_generation

Centralized artifact production for EviTrace. This package is the **sole producer** of all artifacts in EviTrace.

## Module Structure

### `canonical.py`

Deterministic canonicalization and export of extraction artifacts:

- **`canonicalize_grobid_xml(tei_xml_str)`** — Normalize GROBID TEI XML to deterministic UTF-8 string
- **`canonicalize_pymupdf_json(pymupdf_dict)`** — Normalize PyMuPDF dict to deterministic JSON string (sorted keys, consistent indent)
- **`build_canonical_artifacts(grobid_output, pymupdf_output, document_id)`** — Produce canonical artifacts dict with SHA-256 content IDs (in-memory only)
- **`export_canonical_artifacts(artifacts, output_dir)`** — Write canonical artifacts to disk when `export_to_disk=true` in config

**Design:** These are pure functions (no side effects) except for `export_canonical_artifacts`, which only runs when explicitly called by the pipeline.

### `w3c_annotation.py`

W3C Web Annotation JSON-LD generation:

- **`generate_w3c_jsonld(records, base_uri="")`** — Sole producer of W3C JSON-LD annotation dicts

Each returned dict contains the five required W3C annotation keys: `@context`, `id`, `type`, `body`, and `target`.

### `csv_exporter.py`

CSV export utilities for extraction artifacts:

- **`extract_to_csv(json_file_path, output_csv_path)`** — Convert single JSON extraction artifact to CSV
- **`process_folder(input_path, output_base)`** — Process all JSONs in a folder structure
- **`extract_from_json(json_file_path)`** — Extract and group field data by source PDF
- **`clean_cell_value(value)`** — Sanitize cell values (remove non-printable characters)

Groups data by `source_pdf`, with `field_name` as columns and `extracted_value` as cell values.

### `extraction_artifact.py`

Extraction result serialization:

- **`unified_to_artifact(pdf_name, pdf_info, ctx)`** — Serialize QCBundle to JSON-serializable dict
- **`save_artifact(output_folder, pdf_name, artifact)`** — Write artifact JSON to disk, return path

## Design Principles

1. **Centralization** — All artifact production in one package
2. **No duplication** — No local implementations of artifact generation logic elsewhere
3. **Pure functions** — Canonical generators are side-effect-free (except explicit disk writes)
4. **Single responsibility** — Each module handles one artifact type
5. **Determinism** — All canonical formats produce byte-for-byte identical output for same input

## Usage

```python
from artifact_generation import (
    build_canonical_artifacts,
    export_canonical_artifacts,
    generate_w3c_jsonld,
    extract_to_csv,
    unified_to_artifact,
    save_artifact,
)

# Build canonical artifacts (in-memory)
artifacts = build_canonical_artifacts(grobid_xml, pymupdf_dict, doc_id)

# Export to disk (only when configured)
export_canonical_artifacts(artifacts, output_dir)

# Generate W3C annotations
w3c_dicts = generate_w3c_jsonld(annotation_records)

# Export extraction results to CSV
extract_to_csv("extraction.json", "extraction.csv")

# Serialize and save extraction artifact
artifact = unified_to_artifact(pdf_name, pdf_info, qc_bundle)
path = save_artifact(output_folder, pdf_name, artifact)
```

## Migration Notes

- `pdf_extractor/artifact_generator.py` → `artifact_generation/canonical.py`
- `pdf_extractor/annotation/artifact_generator.py` → `artifact_generation/w3c_annotation.py`
- `extract_to_csv.py` (root) → `artifact_generation/csv_exporter.py` (library functions)
- `pdf_extractor/pdf_extractor.py::{_unified_to_artifact, _save_artifact}` → `artifact_generation/extraction_artifact.py`

## Related

- `pipeline/` — Orchestration and data flow
- `quality_control/` — Validation and QC logic
- `pdf_extractor/` — PDF extraction backends (GROBID, PyMuPDF, etc.)
