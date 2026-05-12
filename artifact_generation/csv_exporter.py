"""
Extract JSON extraction artifacts to CSV format.

Converts extracted.json files (with field_name and extracted_value) to CSV format,
grouped by source_pdf with field_name as columns and extracted_value as cell values.

Public functions:
  - extract_to_csv: Convert a single JSON file to CSV
  - process_folder: Process all JSONs in a folder structure
"""

import json
import csv
from pathlib import Path
from collections import defaultdict
import re

from utils.logging_utils import get_logger

logger = get_logger(__name__)


def clean_cell_value(value):
    """Clean cell value by removing weird characters and stripping whitespace.

    Uses regex to keep only alphanumeric, spaces, and common punctuation.

    Args:
        value: Raw cell value (string or other type)

    Returns:
        Cleaned string value
    """
    if not isinstance(value, str):
        value = str(value) if value is not None else ''

    # Remove non-printable and weird characters, keep alphanumeric, basic punctuation, and spaces
    value = re.sub(r'[^\w\s\.\,\:\;\-\(\)\%\&\'\"\n]', '', value)

    # Strip leading and trailing whitespace
    value = value.strip()

    return value


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
        # Handle nested structures
        if isinstance(item, dict):
            # Try to get source_pdf from various possible locations
            source_pdf = item.get('source_pdf', 'unknown')

            field_name = item.get('field_name', 'unknown')
            extracted_value = item.get('extracted_value', '')
            extracted_value = clean_cell_value(extracted_value)
            field_index = item.get('field_index', float('inf'))

            # Store the data
            if source_pdf and field_name:
                grouped_data[source_pdf][field_name] = extracted_value
                # Track field_index for sorting
                field_index_map[field_name] = field_index

    return grouped_data, field_index_map


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
