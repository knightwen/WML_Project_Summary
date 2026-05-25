"""Step 6: export final project locations for Google Earth.

Purpose:
- Read 5_final_project_results_with_coordinates.xlsx from Step 5.
- Export valid project coordinates to KML, CSV, and Excel.

Input:
- data/processed/5_final_project_results_with_coordinates.xlsx

Outputs:
- exports/google_earth/6_project_locations_google_earth.kml
- exports/google_earth/6_project_locations_google_earth.csv
- exports/google_earth/6_project_locations_google_earth.xlsx

Resume:
- Not needed. This step is a deterministic export and can be rerun safely.
"""

from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

from pipeline_utils import (
    require_config_section,
    require_config_value,
    setup_logging,
    validate_columns,
    write_csv_safely,
    write_excel_safely,
    write_text_safely,
)


_CONFIG = require_config_section("step6")
INPUT_EXCEL = require_config_value(_CONFIG, "input_excel", "step6")
OUTPUT_DIR = Path(require_config_value(_CONFIG, "output_dir", "step6"))
OUTPUT_KML = OUTPUT_DIR / require_config_value(_CONFIG, "output_kml", "step6")
OUTPUT_CSV = OUTPUT_DIR / require_config_value(_CONFIG, "output_csv", "step6")
OUTPUT_XLSX = OUTPUT_DIR / require_config_value(_CONFIG, "output_xlsx", "step6")


def get_first_available(row, columns):
    for column in columns:
        if column not in row:
            continue
        value = row.get(column)
        if pd.notna(value) and str(value).strip():
            return str(value).strip()
    return ""


def clean_coordinate(value):
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return float(number)


def format_date(value):
    if pd.isna(value) or not str(value).strip():
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value).split()[0]
    return parsed.strftime("%Y-%m-%d")


def build_description(row):
    fields = [
        ("Project ID", get_first_available(row, ["Project ID"])),
        (
            "Project Name",
            get_first_available(
                row,
                ["Generated Project Name", "Original Project Display Name"],
            ),
        ),
        ("Latitude", str(clean_coordinate(row.get("Google Latitude")) or "")),
        ("Longitude", str(clean_coordinate(row.get("Google Longitude")) or "")),
        ("Client", get_first_available(row, ["Client Name", "Original Client"])),
        ("Manager", get_first_available(row, ["Manager"])),
        ("Start Date", format_date(row.get("Start Date"))),
        ("Contract Amount", get_first_available(row, ["Contract Amount"])),
        ("Industry", get_first_available(row, ["Industry/Sector"])),
        ("Keywords", get_first_available(row, ["Key Words"])),
        ("Profile", get_first_available(row, ["Project Profile"])),
        ("Description", get_first_available(row, ["Description"])),
    ]

    rows = []
    for label, value in fields:
        if value:
            rows.append(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>")

    return "<![CDATA[<table>" + "".join(rows) + "</table>]]>"


def build_kml(rows):
    placemarks = []

    for _, row in rows.iterrows():
        lat = clean_coordinate(row.get("Google Latitude"))
        lon = clean_coordinate(row.get("Google Longitude"))
        if lat is None or lon is None:
            continue

        name = get_first_available(row, ["Project ID"])
        if not name:
            name = get_first_available(
                row,
                ["Generated Project Name", "Original Project Display Name"],
            )

        description = build_description(row)
        placemarks.append(
            f"""
    <Placemark>
      <name>{escape(name)}</name>
      <description>{description}</description>
      <Point>
        <coordinates>{lon},{lat},0</coordinates>
      </Point>
    </Placemark>""".rstrip()
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>WML Project Locations</name>
{chr(10).join(placemarks)}
  </Document>
</kml>
"""


def build_export_table(df):
    rows = []

    for _, row in df.iterrows():
        lat = clean_coordinate(row.get("Google Latitude"))
        lon = clean_coordinate(row.get("Google Longitude"))
        if lat is None or lon is None:
            continue

        rows.append(
            {
                "Project ID": get_first_available(row, ["Project ID"]),
                "Project Name": get_first_available(
                    row,
                    ["Generated Project Name", "Original Project Display Name"],
                ),
                "Latitude": lat,
                "Longitude": lon,
                "Client": get_first_available(row, ["Client Name", "Original Client"]),
                "Manager": get_first_available(row, ["Manager"]),
                "Start Date": format_date(row.get("Start Date")),
                "Contract Amount": get_first_available(row, ["Contract Amount"]),
                "Industry/Sector": get_first_available(row, ["Industry/Sector"]),
                "Key Words": get_first_available(row, ["Key Words"]),
                "Project Profile": get_first_available(row, ["Project Profile"]),
                "Description": get_first_available(row, ["Description"]),
            }
        )

    return pd.DataFrame(rows)


def main():
    setup_logging("step6_export_google_earth")
    input_path = Path(INPUT_EXCEL)
    if not input_path.exists():
        print(f"Error: input file not found: {INPUT_EXCEL}")
        print("Run step5_fetch_coordinates.py first.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(input_path)
    if df.empty:
        print("Input file has no rows.")
        return

    try:
        validate_columns(df, ["Project ID", "Google Latitude", "Google Longitude"], INPUT_EXCEL)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    export_df = build_export_table(df)
    if export_df.empty:
        print("No rows with valid Google Latitude/Longitude were found.")
        return

    kml_text = build_kml(df)
    write_text_safely(OUTPUT_KML, kml_text, encoding="utf-8")
    write_csv_safely(export_df, OUTPUT_CSV, index=False, encoding="utf-8-sig")
    write_excel_safely(export_df, OUTPUT_XLSX, index=False)

    print("Google Earth export complete.")
    print(f"Rows exported: {len(export_df)}")
    print(f"KML: {OUTPUT_KML}")
    print(f"CSV: {OUTPUT_CSV}")
    print(f"Excel: {OUTPUT_XLSX}")


if __name__ == "__main__":
    main()
