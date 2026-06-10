"""Prepare a human-editable AI queue from Step 3 text cache files.

Purpose:
- Merge one or more Step 3 JSONL text cache files.
- Write an Excel queue that can be manually edited before Step 4.
- Keep JSONL as the machine-readable text source for Step 4.
"""

import json
from pathlib import Path

import pandas as pd

from pipeline_utils import (
    require_config_section,
    require_config_value,
    setup_logging,
    write_excel_safely,
)


_CONFIG = require_config_section("step3_optional_ai_queue")
INPUT_JSONL_FILES = _CONFIG.get("input_jsonl_files", [])
OUTPUT_EXCEL = require_config_value(_CONFIG, "output_excel", "step3_optional_ai_queue")
OUTPUT_JSONL = require_config_value(_CONFIG, "output_jsonl", "step3_optional_ai_queue")
READY_STATUSES = set(_CONFIG.get("ready_statuses", ["Success", "Partial Success"]))


def load_jsonl(path):
    records = []
    with open(path, "r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"Skipping invalid JSON in {path} line {line_number}: {exc}")
    return records


def load_queue_records(input_files):
    records_by_project_id = {}
    for input_file in input_files:
        path = Path(input_file)
        if not path.exists():
            print(f"Warning: queue input not found: {path}")
            continue
        for record in load_jsonl(path):
            project_id = str(record.get("project_id", "")).strip()
            if project_id:
                records_by_project_id[project_id] = record
    return list(records_by_project_id.values())


def get_skip_reason(record):
    status = str(record.get("status", "")).strip()
    combined_text = record.get("combined_text") or ""
    if status not in READY_STATUSES:
        return status or "Not ready"
    if not combined_text.strip():
        return "No text"
    return ""


def build_queue_rows(records):
    rows = []
    for index, record in enumerate(records, start=1):
        combined_text = record.get("combined_text") or ""
        skip_reason = get_skip_reason(record)
        rows.append(
            {
                "Project ID": str(record.get("project_id", "")).strip(),
                "Ready For AI": "No" if skip_reason else "Yes",
                "AI Batch": "",
                "AI Priority": index,
                "Local Status": record.get("status", ""),
                "Text Length": len(combined_text),
                "Project Display Name": record.get("project_display_name", "")
                or record.get("project_name", ""),
                "Client": record.get("client", ""),
                "Manager": record.get("manager", ""),
                "Start Date": record.get("start_date", ""),
                "AddressState": record.get("address_state", ""),
                "City": record.get("city", ""),
                "Source Project Folder": record.get("source_project_folder", ""),
                "Archived Files": "\n".join(record.get("archived_files") or []),
                "Text Preview": combined_text[:30000],
                "Skip Reason": skip_reason,
            }
        )
    return rows


def write_jsonl(records, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    setup_logging("step3_optional_ai_queue")
    input_files = INPUT_JSONL_FILES
    if not input_files:
        print("Error: step3_optional_ai_queue.input_jsonl_files is empty.")
        return

    records = load_queue_records(input_files)
    if not records:
        print("No Step 3 records found for AI queue.")
        return

    write_jsonl(records, OUTPUT_JSONL)
    write_excel_safely(pd.DataFrame(build_queue_rows(records)), OUTPUT_EXCEL, index=False)

    ready_count = sum(1 for row in build_queue_rows(records) if row["Ready For AI"] == "Yes")
    print("AI queue prepared.")
    print(f"Rows: {len(records)}")
    print(f"Ready for AI: {ready_count}")
    print(f"Queue Excel: {OUTPUT_EXCEL}")
    print(f"Queue JSONL: {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
