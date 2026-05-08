"""Generate extraction report after the pipeline completes."""
import csv
from collections import defaultdict

from utils.path_utils import OUTPUT_DIR, QC_REPORT_FILE
from utils.logging_utils import get_logger

logger = get_logger(__name__)


def _collect_qc_data(results: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """Aggregate flagged rows and not-reported counts from pipeline results."""
    flagged_rows: list[dict] = []
    not_reported: dict[int, int] = defaultdict(int)

    for entry in results:
        pdf_name = entry["pdf"]
        for field in entry.get("fields", []):
            fi   = field["field_index"]
            conf = field["confidence"]

            if field.get("extracted_value") == "nr":
                not_reported[fi] += 1

            if conf in ("l", "nr"):
                flagged_rows.append({
                    "pdf"            : pdf_name,
                    "field_index"    : fi,
                    "domain_group"   : field["domain_group"],
                    "field_name"     : field["field_name"],
                    "extracted_value": field["extracted_value"],
                    "evidence"       : field["evidence"],
                    "confidence"     : conf,
                })

    flagged_rows.sort(key=lambda r: (r["field_index"], r["pdf"]))
    return flagged_rows, not_reported


def _write_qc_csv(flagged_rows: list[dict]) -> None:
    """Write flagged rows to outputs/qc_report.csv."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    csv_cols = [
        "pdf", "field_index", "domain_group", "field_name",
        "extracted_value", "evidence", "confidence",
    ]
    with open(QC_REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols)
        writer.writeheader()
        writer.writerows(flagged_rows)
    logger.info(f"QC report     → {QC_REPORT_FILE.name}  ({len(flagged_rows)} rows)")


def _print_summary(
    total_pdfs: int,
    total_fields: int,
    flagged_rows: list[dict],
    not_reported: dict[int, int],
) -> None:
    """Print pipeline summary and top-10 not-reported fields to stdout."""
    sep = "═" * 60
    print(f"\n{sep}")
    print("PIPELINE COMPLETE")
    print(sep)
    print(f"  PDFs processed        : {total_pdfs}")
    print(f"  Total fields extracted: {total_fields}")
    print(f"  Flagged for review    : {len(flagged_rows)}")
    print(f"  QC report             : {QC_REPORT_FILE}")
    print(sep)

    if not_reported:
        print("\nTop 10 fields by 'nr' rate:")
        top10 = sorted(not_reported.items(), key=lambda x: x[1], reverse=True)[:10]
        for fi, count in top10:
            rate = count / total_pdfs * 100 if total_pdfs else 0
            print(f"  Field {fi:3d}  {count:3d}/{total_pdfs}  ({rate:4.0f}%)")
    print()


def generate_qc_report(results: list[dict]) -> None:
    """
    Write two files:

    outputs/qc_report.csv
        Every field with confidence "low" or "not reported", flagged for
        manual review. Sorted by field_index then PDF name.

    Also prints a summary to stdout including top-10 not-reported fields.
    """
    total_pdfs   = len(results)
    total_fields = sum(len(e.get("fields", [])) for e in results)

    flagged_rows, not_reported = _collect_qc_data(results)
    _write_qc_csv(flagged_rows)
    _print_summary(total_pdfs, total_fields, flagged_rows, not_reported)
