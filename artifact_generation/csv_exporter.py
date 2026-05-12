"""
Extract JSON extraction artifacts to CSV format.

Converts extracted.json files (with field_name and extracted_value) to CSV format,
grouped by source_pdf with field_name as columns and extracted_value as cell values.

Sanitization of extracted_value is performed at JSON write time by the pipeline
(pdf_processor._save_pdf_output), not here. CSV values are written as-is.

Public functions:
  - export_all_extracted_jsons_to_csv: Combine all *.extracted.json in a dir into one CSV
  - extract_to_csv: Convert a single JSON file to CSV
  - process_folder: Process all JSONs in a folder structure
"""

import json
import csv
from pathlib import Path
from collections import defaultdict

from utils.logging_utils import get_logger

logger = get_logger(__name__)


def extract_from_json(json_file_path):
    """Extract field_name and extracted_value from JSON file.

    Args:
        json_file_path: Path to JSON file

    Returns:
        Tuple of (grouped_data dict, field_index_map dict)
        - grouped_data: Dictionary with source_pdf as keys and field data as values
        - field_index_map: Dictionary mapping field_name to field_index for sorting
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Ensure data is a list
    if not isinstance(data, list):
        data = [data]

    # Group by source_pdf and track field_index
    grouped_data = defaultdict(dict)
    field_index_map = {}

    for item in data:
        if isinstance(item, dict):
            # Try top-level source_pdf first; fall back to location_metadata;
            # then fall back to the JSON filename stem.
            source_pdf = item.get('source_pdf', '')
            if not source_pdf:
                # Derive from location_metadata if present
                loc_meta = item.get('location_metadata')
                if loc_meta and isinstance(loc_meta, list) and len(loc_meta) > 0:
                    first_meta = loc_meta[0]
                    if isinstance(first_meta, dict):
                        raw_path = first_meta.get('source_pdf', '')
                        if raw_path:
                            # Extract just the filename (stem) from the full path
                            source_pdf = Path(raw_path).stem
            if not source_pdf:
                # Last resort: use the JSON filename itself as source identifier
                source_pdf = Path(json_file_path).stem

            field_name = item.get('field_name', 'unknown')
            extracted_value = item.get('extracted_value', '')
            if not isinstance(extracted_value, str):
                extracted_value = str(extracted_value) if extracted_value is not None else ''
            field_index = item.get('field_index', float('inf'))

            if source_pdf and field_name:
                grouped_data[source_pdf][field_name] = extracted_value
                field_index_map[field_name] = field_index

    return grouped_data, field_index_map


def export_all_extracted_jsons_to_csv(run_dir, output_csv_path):
    """Combine all *.extracted.json files in run_dir into a single CSV.

    Scans run_dir (non-recursively) for files matching *.extracted.json,
    merges their data, and writes one CSV with source_pdf as the first column
    and field names (sorted by field_index) as subsequent columns.

    Args:
        run_dir: Path to directory containing *.extracted.json files
        output_csv_path: Destination path for the combined CSV file
    """
    run_dir = Path(run_dir)
    output_csv_path = Path(output_csv_path)

    json_files = sorted(run_dir.glob("*.extracted.json"))
    if not json_files:
        logger.warning("No *.extracted.json files found in %s", run_dir)
        return

    combined_data = defaultdict(dict)
    combined_field_index_map = {}

    for json_file in json_files:
        try:
            grouped_data, field_index_map = extract_from_json(json_file)
            for source_pdf, fields in grouped_data.items():
                combined_data[source_pdf].update(fields)
            combined_field_index_map.update(field_index_map)
        except Exception as e:
            logger.error("Error processing %s: %s", json_file.name, e)

    if not combined_data:
        logger.warning("No data extracted from JSON files in %s", run_dir)
        return

    all_field_names = sorted(
        combined_field_index_map.keys(),
        key=lambda x: combined_field_index_map.get(x, float('inf')),
    )

    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['source_pdf'] + all_field_names
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for source_pdf in sorted(combined_data.keys()):
            row = {'source_pdf': source_pdf}
            row.update(combined_data[source_pdf])
            writer.writerow(row)

    message = (
        f"Exported combined CSV: {output_csv_path} | "
        f"PDFs: {len(combined_data)} | "
        f"Fields: {len(all_field_names)}"
    )
    logger.info(message)


def extract_to_csv(json_file_path, output_csv_path):
    """Convert a single JSON file to CSV format.

    Args:
        json_file_path: Path to input JSON file
        output_csv_path: Path to output CSV file
    """
    print(f"Reading JSON from: {json_file_path}")

    if not Path(json_file_path).exists():
        print(f"Error: File not found: {json_file_path}")
        return

    grouped_data, field_index_map = extract_from_json(json_file_path)

    if not grouped_data:
        print("No data found in JSON file.")
        return

    # Collect all unique field names and sort by field_index
    all_field_names = set()
    for fields in grouped_data.values():
        all_field_names.update(fields.keys())

    # Sort by field_index from the map
    all_field_names = sorted(list(all_field_names), key=lambda x: field_index_map.get(x, float('inf')))

    # Write to CSV
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['source_pdf'] + all_field_names
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()

        for source_pdf in sorted(grouped_data.keys()):
            row = {'source_pdf': source_pdf}
            row.update(grouped_data[source_pdf])
            writer.writerow(row)

    message = (
        f"Successfully extracted data to: {output_csv_path} | "
        f"Total PDFs: {len(grouped_data)} | "
        f"Total unique field names: {len(all_field_names)}"
    )
    print(message)
    logger.info(message)


def process_folder(input_path, output_base):
    """Process all JSON files in a folder structure.

    Args:
        input_path: Path to folder containing JSON files
        output_base: Base output directory for CSVs
    """
    # Find all subdirectories (including root) and group JSON files by directory
    subdirs = []

    # Add the root directory itself if it has JSON files
    root_json_files = list(input_path.glob("*.json"))
    if root_json_files:
        subdirs.append((input_path, root_json_files))

    # Add all subdirectories with JSON files
    for subdir in sorted(input_path.rglob("*")):
        if subdir.is_dir() and subdir != input_path:
            subdir_json_files = list(subdir.glob("*.json"))
            if subdir_json_files:
                subdirs.append((subdir, sorted(subdir_json_files)))

    if not subdirs:
        print(f"No .json files found in: {input_path} or its subdirectories")
        return

    output_base.mkdir(parents=True, exist_ok=True)
    print(f"Processing JSON files from: {input_path}")
    print(f"Output will be saved to: {output_base}\n")

    for source_dir, json_files in subdirs:
        # Create output CSV named after the source directory
        dir_name = source_dir.name if source_dir != input_path else input_path.name

        # If output is specified and it's different from input, preserve directory structure
        if output_base != input_path:
            # Preserve relative structure
            rel_path = source_dir.relative_to(input_path) if source_dir != input_path else Path('.')
            output_subdir = output_base / rel_path
            output_subdir.mkdir(parents=True, exist_ok=True)
            output_csv = output_subdir / f"{dir_name}.csv"
        else:
            output_csv = source_dir / f"{dir_name}.csv"

        print(f"Processing directory: {source_dir.name or input_path.name}")
        print(f"  Combining {len(json_files)} JSON file(s)...")

        # Combine all JSON files from this directory into one CSV
        combined_data = defaultdict(dict)
        combined_field_index_map = {}

        for json_file in json_files:
            try:
                grouped_data, field_index_map = extract_from_json(str(json_file))
                combined_data.update(grouped_data)
                combined_field_index_map.update(field_index_map)
            except Exception as e:
                print(f"    Error processing {json_file.name}: {e}")

        if combined_data:
            # Write combined CSV for this directory
            all_field_names = set()
            for fields in combined_data.values():
                all_field_names.update(fields.keys())

            all_field_names = sorted(list(all_field_names), key=lambda x: combined_field_index_map.get(x, float('inf')))

            with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['source_pdf'] + all_field_names
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                for source_pdf in sorted(combined_data.keys()):
                    row = {'source_pdf': source_pdf}
                    row.update(combined_data[source_pdf])
                    writer.writerow(row)

            message = (
                f"  Created: {output_csv} | "
                f"Total PDFs: {len(combined_data)} | "
                f"Total unique field names: {len(all_field_names)}"
            )
            print(message)
            logger.info(message)

        print()



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert EviTrace extracted JSON files to CSV format."
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Path to input folder containing JSON files, or a single JSON file.",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output path. For a folder input: output directory (defaults to input folder). "
             "For a single file input: output CSV path (defaults to <input_stem>.csv).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path does not exist: {input_path}")
        raise SystemExit(1)

    if input_path.is_file():
        # Single JSON file mode
        output_csv = Path(args.output) if args.output else input_path.with_suffix(".csv")
        extract_to_csv(str(input_path), str(output_csv))
    else:
        # Folder mode
        output_base = Path(args.output) if args.output else input_path
        process_folder(input_path, output_base)
