"""Step 5: fetch project coordinates from Google Maps.

Purpose:
- Read 4_project_analysis_results.xlsx from Step 4.
- Use Google Maps Query or Project Address to geocode project locations.
- Add latitude, longitude, formatted address, place ID, and review flags.

Input:
- data/processed/4_project_analysis_results.xlsx

Output:
- data/processed/5_final_project_results_with_coordinates.xlsx

Resume:
- Supported. Existing completed geocode rows are skipped.
- Error rows can be retried when RETRY_ERROR_GEOCODES is True.
"""


import os
import time
from pathlib import Path

import googlemaps
import pandas as pd

from pipeline_utils import (
    require_config_section,
    require_config_value,
    setup_logging,
    validate_columns,
    write_csv_safely,
    write_excel_safely,
)


# Set GOOGLE_MAPS_API_KEY in your environment before running this step.
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

COORDINATE_COLUMNS = [
    "Google Latitude",
    "Google Longitude",
    "Google Location Type",
    "Google Formatted Address",
    "Google Place ID",
    "Google Partial Match",
    "Google Result Types",
    "Google Geocode Status",
    "Final Project Address",
    "Final Address Source",
    "Address Needs Review",
]

_CONFIG = require_config_section("step5")
INPUT_EXCEL = require_config_value(_CONFIG, "input_excel", "step5")
OUTPUT_EXCEL = require_config_value(_CONFIG, "output_excel", "step5")
FALLBACK_CSV = require_config_value(_CONFIG, "fallback_csv", "step5")
FALLBACK_EXCEL = require_config_value(_CONFIG, "fallback_excel", "step5")
REQUEST_DELAY_SECONDS = float(_CONFIG.get("request_delay_seconds", 0.1))
SAVE_EVERY_N_ROWS = int(_CONFIG.get("save_every_n_rows", 20))
RESUME_FROM_EXISTING_OUTPUT = bool(_CONFIG.get("resume_from_existing_output", True))
RETRY_ERROR_GEOCODES = bool(_CONFIG.get("retry_error_geocodes", True))


def get_first_available(row, columns):
    for column in columns:
        if column not in row:
            continue
        value = row.get(column)
        if pd.notna(value) and str(value).strip() and str(value).strip() != "Not specified":
            return str(value).strip()
    return ""


def build_geocode_query(row):
    query = get_first_available(row, ["Google Maps Query", "Project Address"])
    if query:
        return query

    parts = [
        get_first_available(row, ["Generated Project Name", "Original Project Display Name"]),
        get_first_available(row, ["City"]),
        get_first_available(row, ["AddressState"]),
    ]
    return ", ".join(part for part in parts if part)


def geocode(gmaps_client, query):
    try:
        results = gmaps_client.geocode(query)
    except Exception as exc:
        return {
            "Google Latitude": None,
            "Google Longitude": None,
            "Google Location Type": "",
            "Google Formatted Address": "",
            "Google Place ID": "",
            "Google Partial Match": "",
            "Google Result Types": "",
            "Google Geocode Status": f"Error: {exc}",
        }

    if not results:
        return {
            "Google Latitude": None,
            "Google Longitude": None,
            "Google Location Type": "",
            "Google Formatted Address": "",
            "Google Place ID": "",
            "Google Partial Match": "",
            "Google Result Types": "",
            "Google Geocode Status": "No Result",
        }

    first = results[0]
    location = first.get("geometry", {}).get("location", {})
    return {
        "Google Latitude": location.get("lat"),
        "Google Longitude": location.get("lng"),
        "Google Location Type": first.get("geometry", {}).get("location_type", ""),
        "Google Formatted Address": first.get("formatted_address", ""),
        "Google Place ID": first.get("place_id", ""),
        "Google Partial Match": first.get("partial_match", False),
        "Google Result Types": ", ".join(first.get("types", [])),
        "Google Geocode Status": "Success",
    }


def build_final_address(row):
    google_address = get_first_available(row, ["Google Formatted Address"])
    project_address = get_first_available(row, ["Project Address"])
    query = get_first_available(row, ["Google Maps Query", "Geocode Query Used"])
    geocode_status = str(row.get("Google Geocode Status", "")).strip()
    location_type = str(row.get("Google Location Type", "")).strip()
    partial_match = str(row.get("Google Partial Match", "")).strip().lower()
    address_confidence = str(row.get("Address Confidence", "")).strip().lower()

    if google_address:
        final_address = google_address
        source = "google_formatted_address"
    elif project_address:
        final_address = project_address
        source = "gemini_project_address"
    else:
        final_address = query
        source = "geocode_query"

    needs_review = (
        geocode_status != "Success"
        or address_confidence == "low"
        or location_type in {"APPROXIMATE", "GEOMETRIC_CENTER"}
        or partial_match == "true"
    )

    return final_address, source, "Yes" if needs_review else "No"


def save_results(df):
    try:
        write_excel_safely(df, OUTPUT_EXCEL, index=False)
        print(f"  Saved to: {OUTPUT_EXCEL}")
    except PermissionError:
        write_csv_safely(df, FALLBACK_CSV, index=False, encoding="utf-8-sig")
        write_excel_safely(df, FALLBACK_EXCEL, index=False)
        print(f"  {OUTPUT_EXCEL} is open or locked.")
        print(f"  Saved recovery files to: {FALLBACK_CSV} and {FALLBACK_EXCEL}")


def load_existing_coordinate_rows():
    if not RESUME_FROM_EXISTING_OUTPUT:
        return {}

    output_path = Path(OUTPUT_EXCEL)
    fallback_path = Path(FALLBACK_EXCEL)
    fallback_csv_path = Path(FALLBACK_CSV)

    if output_path.exists():
        existing_df = pd.read_excel(output_path)
    elif fallback_path.exists():
        existing_df = pd.read_excel(fallback_path)
    elif fallback_csv_path.exists():
        existing_df = pd.read_csv(fallback_csv_path)
    else:
        return {}

    if existing_df.empty or "Project ID" not in existing_df.columns:
        return {}

    existing_rows = {}

    for _, row in existing_df.iterrows():
        project_id = str(row.get("Project ID", "")).strip()
        if not project_id:
            continue

        existing_rows[project_id] = row

    print(f"Loaded existing coordinate progress rows: {len(existing_rows)}")
    return existing_rows


def merge_existing_coordinate_results(df, existing_rows):
    if not existing_rows:
        return df

    resume_columns = COORDINATE_COLUMNS + ["Geocode Query Used"]

    for index, row in df.iterrows():
        project_id = str(row.get("Project ID", "")).strip()
        existing_row = existing_rows.get(project_id)

        if existing_row is None:
            continue

        for column in resume_columns:
            if column not in existing_row:
                continue

            value = existing_row.get(column)
            if pd.notna(value) and str(value).strip():
                df.at[index, column] = value

    return df


def is_geocode_complete(row):
    if pd.notna(row.get("Google Latitude")) and pd.notna(row.get("Google Longitude")):
        return True

    status = str(row.get("Google Geocode Status", "")).strip()

    if status in {"Success", "No Result"}:
        return True

    if status.startswith("Skipped:"):
        return True

    if status.startswith("Error:"):
        return not RETRY_ERROR_GEOCODES

    return False


def main():
    setup_logging("step5_fetch_coordinates")
    input_path = Path(INPUT_EXCEL)
    if not input_path.exists():
        print(f"Error: input Excel not found: {INPUT_EXCEL}")
        print("Run step4_ai_project_analysis.py first.")
        return

    if not GOOGLE_MAPS_API_KEY:
        print("Error: GOOGLE_MAPS_API_KEY is empty.")
        print("Set the GOOGLE_MAPS_API_KEY environment variable and run again.")
        return

    df = pd.read_excel(input_path)
    if df.empty:
        print("Input Excel has no rows.")
        return

    try:
        validate_columns(df, ["Project ID"], INPUT_EXCEL)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    for column in COORDINATE_COLUMNS:
        if column not in df.columns:
            df[column] = None

    if "Geocode Query Used" not in df.columns:
        df["Geocode Query Used"] = ""

    existing_rows = load_existing_coordinate_rows()
    df = merge_existing_coordinate_results(df, existing_rows)

    gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    processed = 0
    skipped = 0
    total = len(df)

    for index, row in df.iterrows():
        if is_geocode_complete(row):
            final_address, source, needs_review = build_final_address(row)
            df.at[index, "Final Project Address"] = final_address
            df.at[index, "Final Address Source"] = source
            df.at[index, "Address Needs Review"] = needs_review
            skipped += 1
            continue

        query = build_geocode_query(row)
        df.at[index, "Geocode Query Used"] = query

        if not query:
            df.at[index, "Google Geocode Status"] = "Skipped: empty query"
            final_address, source, needs_review = build_final_address(df.loc[index])
            df.at[index, "Final Project Address"] = final_address
            df.at[index, "Final Address Source"] = source
            df.at[index, "Address Needs Review"] = needs_review
            skipped += 1
            continue

        address_confidence = str(row.get("Address Confidence", "")).strip().lower()
        if address_confidence == "low":
            df.at[index, "Google Geocode Status"] = "Skipped: low address confidence"
            final_address, source, needs_review = build_final_address(df.loc[index])
            df.at[index, "Final Project Address"] = final_address
            df.at[index, "Final Address Source"] = source
            df.at[index, "Address Needs Review"] = needs_review
            skipped += 1
            continue

        print(f"Processing [{index + 1}/{total}]: {query}")
        result = geocode(gmaps, query)

        for column, value in result.items():
            df.at[index, column] = value

        final_address, source, needs_review = build_final_address(df.loc[index])
        df.at[index, "Final Project Address"] = final_address
        df.at[index, "Final Address Source"] = source
        df.at[index, "Address Needs Review"] = needs_review

        processed += 1

        if processed % SAVE_EVERY_N_ROWS == 0:
            print(f"  Saving progress after {processed} geocode requests...")
            save_results(df)

        time.sleep(REQUEST_DELAY_SECONDS)

    for index, row in df.iterrows():
        if not get_first_available(row, ["Final Project Address"]):
            final_address, source, needs_review = build_final_address(row)
            df.at[index, "Final Project Address"] = final_address
            df.at[index, "Final Address Source"] = source
            df.at[index, "Address Needs Review"] = needs_review

    save_results(df)

    success_count = (df["Google Geocode Status"] == "Success").sum()
    print("\nTask complete.")
    print(f"Rows: {total}")
    print(f"Geocode requests this run: {processed}")
    print(f"Skipped rows: {skipped}")
    print(f"Successful geocodes: {success_count}")
    print(f"Output saved to: {OUTPUT_EXCEL}")


if __name__ == "__main__":
    main()

