"""Step 1: prepare the source project list.

Purpose:
- Read the raw project Excel file.
- Preserve original project information and add safe helper fields.
- Sort by Start Date descending, with invalid or missing dates last.
- Select a configurable 1-based inclusive row range.

Input:
- Raw project Excel configured by INPUT_EXCEL.

Output:
- data/processed/1_source_projects.xlsx

Attention:
- default start:1 ====> end:100
"""
import re
from pathlib import Path

import pandas as pd

from pipeline_utils import (
    require_config_section,
    require_config_value,
    setup_logging,
    validate_columns,
    write_excel_safely,
)


_CONFIG = require_config_section("step1")
INPUT_EXCEL = require_config_value(_CONFIG, "input_excel", "step1")
OUTPUT_EXCEL = require_config_value(_CONFIG, "output_excel", "step1")
SORT_COLUMN = _CONFIG.get("sort_column", "Start Date")
ROW_START = int(_CONFIG.get("row_start", 1))
ROW_END = int(_CONFIG.get("row_end", 100))

OUTPUT_COLUMNS = [
    "Project ID",
    "Project ID Clean",
    "Project Display Name",
    "Project Display Name Raw",
    "Project Display Name No ID",
    "Client",
    "Manager",
    "Start Date",
    "Contract Amount",
    "AddressState",
    "City",
]


def clean_project_id(value):
    if pd.isna(value):
        return ""

    return str(value).replace(":", "").strip().split(".")[0]


def strip_project_id_from_display_name(value):
    if pd.isna(value):
        return ""

    text = str(value).strip()
    match = re.match(r"^\s*\d[\dA-Za-z._-]*\s*:\s*(.+)$", text)
    if match:
        return match.group(1).strip()

    return text


def ensure_column(df, column, default=""):
    if column not in df.columns:
        df[column] = default


def validate_row_range():
    if ROW_START < 1 or ROW_END < ROW_START:
        print("Error: ROW_START and ROW_END must be a 1-based inclusive range.")
        print(f"Current values: ROW_START={ROW_START}, ROW_END={ROW_END}")
        return False

    return True


def main():
    setup_logging("step1_prepare_source_projects")
    input_path = Path(INPUT_EXCEL)

    if not input_path.exists():
        print(f"Error: input Excel not found: {input_path}")
        print("Update step1.input_excel in pipeline_config.json and run again.")
        return

    if not validate_row_range():
        return

    try:
        df = pd.read_excel(input_path)
    except Exception as exc:
        print(f"Error: failed to read {input_path}: {exc}")
        return

    if df.empty:
        print(f"Error: {input_path} has no rows.")
        return

    try:
        validate_columns(df, ["Project ID", SORT_COLUMN], str(input_path))
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    for column in OUTPUT_COLUMNS:
        ensure_column(df, column)

    df["Project ID Clean"] = df["Project ID"].apply(clean_project_id)
    df["Project Display Name Raw"] = df["Project Display Name"]
    df["Project Display Name No ID"] = df["Project Display Name"].apply(
        strip_project_id_from_display_name
    )

    sort_values = pd.to_datetime(df[SORT_COLUMN], errors="coerce")
    df = df.assign(_sort_date=sort_values)
    df = df.sort_values("_sort_date", ascending=False, na_position="last").copy()
    df = df.drop(columns=["_sort_date"])

    selected_df = df.iloc[ROW_START - 1:ROW_END].copy()

    ordered_columns = [column for column in OUTPUT_COLUMNS if column in selected_df.columns]
    ordered_columns += [
        column for column in selected_df.columns if column not in ordered_columns
    ]
    selected_df = selected_df[ordered_columns]

    try:
        write_excel_safely(selected_df, OUTPUT_EXCEL, index=False)
    except PermissionError:
        print(f"Error: cannot save {OUTPUT_EXCEL}. Please close it in Excel and try again.")
        return

    print("Source project list prepared.")
    print(f"Input rows: {len(df)}")
    print(f"Rows selected: {ROW_START} to {ROW_END}")
    print(f"Rows written: {len(selected_df)}")
    print(f"Sorted by: {SORT_COLUMN} descending, invalid/missing dates last")
    print(f"Output: {OUTPUT_EXCEL}")


if __name__ == "__main__":
    main()
