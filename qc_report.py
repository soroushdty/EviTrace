"""Generate QC report and master output after the pipeline completes."""
import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

from config import MASTER_OUTPUT, OUTPUT_DIR, QC_REPORT_FILE

logger = logging.getLogger(__name__)


def generate_qc_report(results: list[dict]) -> None:
    """
    Write two files:

    outputs/all_extractions.json
        Master JSON — list of {pdf, fields} for every processed paper.

    outputs/qc_report.csv
        Every field with confidence "low" or "not reported", flagged for
        manual review. Sorted by field_index then PDF name.

    Also prints a summary to stdout including top-10 not-reported fields.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    # ── Master JSON ───────────────────────────────────────────────────────
    with open(MASTER_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Master output → {MASTER_OUTPUT.name}")

    # ── Aggregate stats ───────────────────────────────────────────────────
    total_pdfs    = len(results)
    flagged_rows  : list[dict] = []
    not_reported  : dict[int, int] = defaultdict(int)   # field_index → count
    conf_dist     : dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for entry in results:
        pdf_name = entry["pdf"]
        for field in entry.get("fields", []):
            fi   = field["field_index"]
            conf = field["confidence"]
            conf_dist[fi][conf] += 1

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

    # Sort flagged rows: field_index first, then PDF name
    flagged_rows.sort(key=lambda r: (r["field_index"], r["pdf"]))

    # ── QC CSV ────────────────────────────────────────────────────────────
    csv_cols = [
        "pdf", "field_index", "domain_group", "field_name",
        "extracted_value", "evidence", "confidence",
    ]
    with open(QC_REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols)
        writer.writeheader()
        writer.writerows(flagged_rows)
    logger.info(f"QC report     → {QC_REPORT_FILE.name}  ({len(flagged_rows)} rows)")

    # ── Console summary ───────────────────────────────────────────────────
    total_fields = sum(len(e.get("fields", [])) for e in results)
    sep = "═" * 60

    print(f"\n{sep}")
    print("PIPELINE COMPLETE")
    print(sep)
    print(f"  PDFs processed        : {total_pdfs}")
    print(f"  Total fields extracted: {total_fields}")
    print(f"  Flagged for review    : {len(flagged_rows)}")
    print(f"  Master output         : {MASTER_OUTPUT}")
    print(f"  QC report             : {QC_REPORT_FILE}")
    print(sep)

    if not_reported:
        print("\nTop 10 fields by 'nr' rate:")
        top10 = sorted(not_reported.items(), key=lambda x: x[1], reverse=True)[:10]
        for fi, count in top10:
            rate = count / total_pdfs * 100 if total_pdfs else 0
            print(f"  Field {fi:3d}  {count:3d}/{total_pdfs}  ({rate:4.0f}%)")
    print()
