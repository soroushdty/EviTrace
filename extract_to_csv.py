"""
Script to extract field_name and extracted_value from JSON and convert to CSV.
Groups data by source_pdf, with field_name as columns and extracted_value as cell values.

Usage:
    python extract_to_csv.py                              # Auto-detect .extracted.json files
    python extract_to_csv.py --input file.json            # Specify input JSON file
    python extract_to_csv.py --input file.json --output result.csv  # Specify both input and output
    python extract_to_csv.py --input folder/              # Process all JSONs in a folder
    python extract_to_csv.py --input folder/ --output output_folder/  # Process folder, save to folder
    python extract_to_csv.py --help                       # Show help
"""

import json
import csv
import argparse
import sys
from pathlib import Path
from collections import defaultdict


def flatten_nested_data(data, parent_key=""):
    """
    Recursively flatten nested structures to handle complex input.

    Args:
        data: Data structure to flatten (dict, list, or scalar)
        parent_key: Key prefix for nested items

    Returns:
        List of flattened items
    """
    items = []

    if isinstance(data, dict):
        for key, value in data.items():
            new_key = f"{parent_key}_{key}" if parent_key else key
            if isinstance(value, (dict, list)):
                items.extend(flatten_nested_data(value, new_key))
            else:
                items.append((new_key, value))

    elif isinstance(data, list):
        for idx, item in enumerate(data):
            new_key = f"{parent_key}[{idx}]" if parent_key else f"[{idx}]"
            if isinstance(item, (dict, list)):
                items.extend(flatten_nested_data(item, new_key))
            else:
                items.append((new_key, item))

    return items


def extract_from_json(json_file_path):
    """
    Extract field_name and extracted_value from JSON file.

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
            field_index = item.get('field_index', float('inf'))

            # Store the data
            if source_pdf and field_name:
                grouped_data[source_pdf][field_name] = extracted_value
                # Track field_index for sorting
                field_index_map[field_name] = field_index

    return grouped_data, field_index_map


def extract_to_csv(json_file_path, output_csv_path):
    """
    Main function to extract JSON data and write to CSV.

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

    print(f"Successfully extracted data to: {output_csv_path}")
    print(f"Total PDFs: {len(grouped_data)}")
    print(f"Total unique field names: {len(all_field_names)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract field_name and extracted_value from JSON and convert to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract_to_csv.py
    → Auto-detects all .extracted.json files in outputs/ directory

  python extract_to_csv.py --input data.json
    → Converts data.json to data.csv

  python extract_to_csv.py --input data.json --output results.csv
    → Converts data.json to results.csv

  python extract_to_csv.py --input folder/
    → Processes all .json files in folder/, creates CSV for each

  python extract_to_csv.py --input folder/ --output output_folder/
    → Processes all JSONs in folder/, saves CSVs to output_folder/

  python extract_to_csv.py --input "path/to/file.json" --output "path/to/output.csv"
    → Specify full paths for input and output
        """
    )

    parser.add_argument(
        '-i', '--input',
        type=str,
        help='Input JSON file or folder path (e.g., outputs/data.json or data_folder/)'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Output CSV file or folder path (default: same as input with .csv extension)'
    )

    args = parser.parse_args()

    # If input is specified
    if args.input:
        input_path = Path(args.input).resolve()

        if not input_path.exists():
            print(f"Error: Input path not found: {input_path}")
            sys.exit(1)

        # Handle file input
        if input_path.is_file():
            # Single file processing
            if args.output:
                output_path = Path(args.output)
            else:
                # Replace extension with .csv
                output_path = input_path.with_suffix('').with_suffix('.csv')

            print(f"Processing file: {input_path}")
            extract_to_csv(str(input_path), str(output_path))

        # Handle directory input
        elif input_path.is_dir():
            # Find all JSON files in the directory
            json_files = sorted(list(input_path.glob("*.json")))

            if not json_files:
                print(f"No .json files found in: {input_path}")
                sys.exit(1)

            # Determine output directory
            if args.output:
                output_dir = Path(args.output).resolve()
                # Create output directory if it doesn't exist
                output_dir.mkdir(parents=True, exist_ok=True)
            else:
                output_dir = input_path

            print(f"Processing {len(json_files)} JSON file(s) from: {input_path}")
            print(f"Output will be saved to: {output_dir}\n")

            for json_file in json_files:
                output_csv = output_dir / json_file.with_suffix('').with_suffix('.csv').name
                print(f"  [{json_files.index(json_file) + 1}/{len(json_files)}] {json_file.name}")
                extract_to_csv(str(json_file), str(output_csv))
                print()

        else:
            print(f"Error: Input path is neither a file nor a directory: {input_path}")
            sys.exit(1)

    else:
        # Auto-detect mode: find all .extracted.json files in outputs directory
        outputs_dir = Path(__file__).parent / "outputs"
        extracted_files = list(outputs_dir.glob("*.extracted.json"))

        if not extracted_files:
            print("No .extracted.json files found in outputs directory.")
            print(f"Looking in: {outputs_dir}")
            print("\nUsage:")
            print("  python extract_to_csv.py --input <json_file_or_folder> [--output <csv_file_or_folder>]")
            print("  python extract_to_csv.py --help")
            sys.exit(1)
        else:
            print(f"Auto-detect mode: Processing {len(extracted_files)} file(s)\n")
            for json_file in extracted_files:
                output_csv = json_file.with_suffix('').with_suffix('.csv')
                print(f"Processing: {json_file.name}")
                extract_to_csv(str(json_file), str(output_csv))
                print()
