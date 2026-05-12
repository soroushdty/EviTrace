"""
Script to extract field_name and extracted_value from JSON and convert to CSV.

This is a CLI wrapper around artifact_generation.csv_exporter.

Usage:
    python extract_to_csv.py                              # Auto-detect .extracted.json files
    python extract_to_csv.py --input file.json            # Specify input JSON file
    python extract_to_csv.py --input file.json --output result.csv  # Specify both input and output
    python extract_to_csv.py --input folder/              # Process all JSONs in folder and subdirs
    python extract_to_csv.py --input folder/ --output output_folder/  # Process folder, save to output_folder
    python extract_to_csv.py --help                       # Show help
"""

import argparse
import sys
from pathlib import Path

from utils.config_utils import load_local_config
from utils.logging_utils import setup_logging
from artifact_generation.csv_exporter import extract_to_csv, process_folder


if __name__ == "__main__":
    local_cfg = load_local_config(None)
    setup_logging(
        log_file=local_cfg.get("log_file", "run.log"),
        console_level=local_cfg.get("log_level", "INFO"),
    )

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
    → Processes folder structure, creates a CSV for each subdirectory
    → Combines all JSON files in each subdirectory into one CSV named after that subdir

  python extract_to_csv.py --input folder/ --output output_folder/
    → Processes folder structure, saves CSVs to output_folder preserving subdirectory structure

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
            # Determine output directory structure
            if args.output:
                output_base = Path(args.output).resolve()
            else:
                output_base = input_path

            process_folder(input_path, output_base)

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

