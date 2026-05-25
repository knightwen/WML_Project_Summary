"""Step 4: generate structured project analysis with AI.

Purpose:
- Read 3_project_pdf_text_cache.jsonl from Step 3.
- Send one project at a time to Gemini.
- Generate structured project name, profile, description, job type, industry,
  address, Google Maps query, and client information.

Input:
- data/cache/3_project_pdf_text_cache.jsonl

Output:
- data/processed/4_project_analysis_results.xlsx

Resume:
- Supported. Existing successful AI rows are skipped.
- AI Error rows can be retried when RETRY_AI_ERROR_ROWS is True.
"""

import json
import os
import time
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import types

from pipeline_utils import (
    require_config_section,
    require_config_value,
    setup_logging,
    validate_records,
    write_csv_safely,
    write_excel_safely,
)


# Set GEMINI_API_KEY in your environment before running this step.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

QUOTA_ERROR_PATTERNS = [
    "429",
    "quota",
    "resource_exhausted",
    "rate limit",
    "exceeded",
]

RESULT_FIELDS = {
    "generated_project_name": "",
    "profile": "",
    "description": "",
    "job_type": "",
    "keywords": "",
    "industry": "",
    "address": "Not specified",
    "google_maps_query": "",
    "address_confidence": "low",
    "address_source": "not_found",
    "client_name": "Not specified",
    "client_contact": "Not specified",
}

_CONFIG = require_config_section("step4")
TEXT_CACHE_JSONL = require_config_value(_CONFIG, "text_cache_jsonl", "step4")
OUTPUT_EXCEL = require_config_value(_CONFIG, "output_excel", "step4")
FALLBACK_CSV = require_config_value(_CONFIG, "fallback_csv", "step4")
MODEL_NAME = _CONFIG.get("model_name", "gemini-3.1-flash-lite")
MAX_TEXT_CHARS = int(_CONFIG.get("max_text_chars", 12000))
REQUEST_DELAY_SECONDS = float(_CONFIG.get("request_delay_seconds", 2))
SAVE_EVERY_N_ROWS = int(_CONFIG.get("save_every_n_rows", 1))
STOP_ON_QUOTA_ERROR = bool(_CONFIG.get("stop_on_quota_error", True))
RESUME_FROM_EXISTING_OUTPUT = bool(_CONFIG.get("resume_from_existing_output", True))
RETRY_AI_ERROR_ROWS = bool(_CONFIG.get("retry_ai_error_rows", True))


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
                print(f"Skipping invalid JSON on line {line_number}: {exc}")

    return records


def clean_text_for_prompt(text):
    if not text:
        return ""

    cleaned = "".join(
        char if char == "\n" or char == "\t" or ord(char) >= 32 else " "
        for char in text
    )
    cleaned = "\n".join(line.strip() for line in cleaned.splitlines())

    return cleaned[:MAX_TEXT_CHARS]


def build_prompt(record, text):
    project_id = record.get("project_id", "")
    original_name = record.get("project_display_name", "")
    original_name_raw = record.get("project_display_name_raw", "")
    original_name_no_id = record.get("project_display_name_no_id", "")
    project_name = record.get("project_name", "")
    project_description = record.get("project_description", "")
    client = record.get("client", "")
    city = record.get("city", "")
    address_state = record.get("address_state", "")
    source_folder = record.get("source_project_folder", "")

    return f"""
You are an expert engineering project analyst.

Analyze the following engineering project document excerpt and return ONLY a valid JSON object with these exact keys:

{{
  "generated_project_name": "A clean human-readable project name without internal project id, fee, quotation, proposal, report, or admin wording.",
  "profile": "A compact job profile written in the style: 'a geotechnical investigation for site classification for 3 buildings, 9 boreholes'.",
  "description": "A concise but useful project description based on the actual scope.",
  "job_type": "One or two controlled job type categories.",
  "keywords": "3-5 core keywords separated by commas.",
  "industry": "One controlled industry/sector category.",
  "address": "Physical site address, location, city, or region. Use Not specified if absent.",
  "google_maps_query": "The best concise query for Google Maps Geocoding API. Include street/site address, suburb/city, state, and Australia where possible. Use an empty string if no reliable location is available.",
  "address_confidence": "high, medium, or low.",
  "address_source": "pdf, original_project_name, source_folder, city_state_only, or not_found.",
  "client_name": "Client organization or company commissioning the work. Use Not specified if absent.",
  "client_contact": "Client-side contact details, including contact person name, email address, phone/mobile number, company, and role if available. Use Not specified if absent."
}}

General rules:
- Prefer project-specific facts over generic company capability text.
- Do not include WML's own office address as the project address unless it is clearly the project site.
- Do not list WML staff as client_contact unless they are explicitly the client representative.
- Prefer external client details from email headers, letter recipients, proposal requests, briefs, or issued-to sections.
- Include client email and phone/mobile numbers when present.
- If the document is only a fee proposal, infer the scope from the proposal and keep uncertainty out of the wording.
- Keep all outputs factual, compact, and suitable for a project database.
- Do not include uncertainty words such as likely, appears, seems, may, probably, or possibly.
- Do not mention internal project IDs in generated_project_name unless the ID is part of a public asset name.

Generated project name rules:
- Create a clean project title.
- Remove words such as fee proposal, quotation, proposal, report, authority form, consultancy authority, email, FW, RE, and internal references.
- Prefer a title based on site, asset, and engineering task.
- Example: "568 Caves Road Structural Inspection"
- Example: "Paddington New Crusher Structural Design"
- Example: "Preston River Path Staircase Verification"

Profile writing rules:
- Write profile as a compact noun phrase, not a full marketing sentence.
- Use this style: "a geotechnical investigation for site classification for 3 buildings, 9 boreholes".
- Start with "a" or "an" where natural.
- Mention the engineering activity first, then the purpose, then quantity/location/context if available.
- Keep it under 35 words.
- Do not mention fee proposal, quotation, internal admin, or document formatting.
- Good examples:
  - "a structural inspection and independent expert report for a residential balcony"
  - "a structural design for a 250 kg davit arm footing"
  - "a design verification for a temporary works access platform"
  - "a geotechnical investigation for site classification for 3 buildings, 9 boreholes"
  - "a condition assessment and reporting for a pedestrian staircase"

Description rules:
- Write 1-3 concise sentences.
- Include the actual engineering scope, asset, site, and deliverables where available.
- Include inspections, calculations, design, report, certification, verification, review, or expert evidence only if supported by the text.
- Avoid marketing language.

Job type rules:
- Select from this controlled vocabulary where possible:
  inspection reporting, design, verification, temporary works, structural assessment, certification, forensic engineering, site investigation, construction support, peer review, condition assessment, remedial works, geotechnical investigation
- Use one primary category, or at most two if genuinely needed.
- If none fit, use a short typical engineering category, but do not invent overly specific or casual labels.
- Prefer "inspection reporting" for inspections, expert reports, site inspections, condition reports, and investigation reports.
- Prefer "design" for structural design, footing design, member design, slab design, retaining wall design, crane/lifting support design, and calculations.
- Prefer "verification" for third-party checking, design verification, certification review, compliance review, and independent check.
- Prefer "temporary works" for propping, temporary access, construction-stage support, lifting/rigging temporary structures, temporary platforms, and temporary bracing.
- Prefer "forensic engineering" for dispute, arbitration, expert witness, litigation, failure investigation, and insurance-related engineering opinion.
- Prefer "certification" for certification letters, compliance certification, Form 15/Form 16 style work, and sign-off.
- Prefer "condition assessment" for asset condition review, dilapidation, degradation, corrosion, or defect assessment.
- Prefer "geotechnical investigation" for boreholes, site classification, soil testing, footings founding conditions, and geotechnical reports.

Industry / sector rules:
- Select from this controlled vocabulary where possible:
  ports, rail, commercial, industrial, mining, residential, infrastructure, government
- Use one primary category only.
- If none fit, use another standard sector such as education, healthcare, utilities, energy, transport, marine, agriculture, or legal.
- Do not create overly specific sectors.
- For houses, apartments, balconies, residential subdivisions, private residences, or strata buildings, use residential.
- For mines, processing plants, crushers, conveyors, workshops on mine sites, or Rio Tinto/BHP/FMG-type projects, use mining.
- For bridges, roads, paths, drainage, public assets, civil networks, pedestrian paths, and public staircases, use infrastructure.
- For warehouses, factories, workshops, plants, and manufacturing facilities, use industrial.
- For offices, shops, retail, hospitality, and mixed-use business premises, use commercial.
- For port terminals, wharves, marine loading facilities, and harbour assets, use ports.
- For rail lines, rail bridges, rail stations, rail depots, and rail infrastructure, use rail.
- For council, state government, public agencies, schools, public facilities, and government-owned assets, use government unless a more specific sector is clearly stronger.

Address and maps rules:
- If a precise street/site address is present, use it in address and google_maps_query.
- Build google_maps_query for geocoding, not for human display.
- Include street/site address, suburb/city, state, and Australia where possible.
- If only a regional location is present, include the region/city/state and set address_confidence to medium or low.
- If the only location is WML's office address, do not use it as the project address.
- address_source must be one of: pdf, original_project_name, source_folder, city_state_only, not_found.

Client rules:
- client_name should be the external client organization commissioning the work.
- client_contact should include contact person, email, phone/mobile, company, and role if available.
- If only an email recipient is available, include it.
- Do not treat WML staff as the client unless the text explicitly says WML is the client.

Project ID: {project_id}
Original project display name: {original_name}
Original project display name raw: {original_name_raw}
Original project display name without id: {original_name_no_id}
Pre-extracted project name: {project_name}
Pre-extracted project description: {project_description}
Original client: {client}
Original city: {city}
Original state: {address_state}
Source project folder: {source_folder}

Document excerpt:
{text}
""".strip()


def normalize_ai_data(data):
    normalized = RESULT_FIELDS.copy()

    if not isinstance(data, dict):
        return normalized

    for key in normalized:
        value = data.get(key, normalized[key])

        if value is None:
            value = normalized[key]

        normalized[key] = str(value).strip()

    return normalized


def is_quota_error(error_message):
    if not error_message:
        return False

    message = error_message.lower()

    return any(pattern in message for pattern in QUOTA_ERROR_PATTERNS)


def save_output_rows(output_rows):
    if not output_rows:
        return

    output_df = pd.DataFrame(output_rows)

    try:
        write_excel_safely(output_df, OUTPUT_EXCEL, index=False)
        print(f"  Progress saved to: {OUTPUT_EXCEL}")
    except PermissionError:
        write_csv_safely(output_df, FALLBACK_CSV, index=False, encoding="utf-8-sig")
        print(f"  Excel file is open. Progress saved to: {FALLBACK_CSV}")


def load_existing_output_rows():
    if not RESUME_FROM_EXISTING_OUTPUT:
        return {}

    output_path = Path(OUTPUT_EXCEL)
    fallback_path = Path(FALLBACK_CSV)

    if output_path.exists():
        existing_df = pd.read_excel(output_path)
    elif fallback_path.exists():
        existing_df = pd.read_csv(fallback_path)
    else:
        return {}

    if existing_df.empty or "Project ID" not in existing_df.columns:
        return {}

    existing_rows = {}

    for _, row in existing_df.iterrows():
        project_id = str(row.get("Project ID", "")).strip()
        if not project_id:
            continue

        existing_rows[project_id] = {
            column: "" if pd.isna(value) else value
            for column, value in row.to_dict().items()
        }

    print(f"Loaded existing progress rows: {len(existing_rows)}")
    return existing_rows


def is_completed_output_row(row):
    status = str(row.get("Status", "")).strip()
    extraction_status = str(row.get("Extraction Status", "")).strip()

    if status == "Success":
        return True

    if RETRY_AI_ERROR_ROWS and status == "AI Error":
        return False

    if extraction_status not in {"Success", "Partial Success"}:
        return True

    return bool(status)


def build_ordered_output_rows(records, rows_by_project_id):
    output_rows = []

    for record in records:
        project_id = str(record.get("project_id", "")).strip()
        if project_id in rows_by_project_id:
            output_rows.append(rows_by_project_id[project_id])

    return output_rows


def get_ai_analysis(client, record, text):
    text = clean_text_for_prompt(text)

    if not text:
        return RESULT_FIELDS.copy(), "No text available for AI analysis"

    prompt = build_prompt(record, text)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        data = json.loads(response.text)

        return normalize_ai_data(data), ""

    except Exception as exc:
        return RESULT_FIELDS.copy(), f"AI parsing error: {exc}"


def build_output_row(record, ai_data, ai_error):
    extraction_status = record.get("status", "")
    status = extraction_status

    if extraction_status in {"Success", "Partial Success"}:
        status = "Success" if not ai_error else "AI Error"

    errors = list(record.get("errors") or [])

    if ai_error:
        errors.append(ai_error)

    return {
        "Project ID": record.get("project_id", ""),
        "Status": status,
        "Extraction Status": extraction_status,
        "Original Project Display Name": record.get("project_display_name", ""),
        "Original Project Display Name Raw": record.get(
            "project_display_name_raw", ""
        ),
        "Original Project Display Name No ID": record.get(
            "project_display_name_no_id", ""
        ),
        "Pre-extracted Project Name": record.get("project_name", ""),
        "Pre-extracted Project Description": record.get("project_description", ""),
        "Generated Project Name": ai_data.get("generated_project_name", ""),
        "Original Client": record.get("client", ""),
        "Client Name": ai_data.get("client_name", "Not specified"),
        "Client Contact": ai_data.get("client_contact", "Not specified"),
        "Manager": record.get("manager", ""),
        "Start Date": record.get("start_date", ""),
        "Contract Amount": record.get("contract_amount", ""),
        "AddressState": record.get("address_state", ""),
        "City": record.get("city", ""),
        "Project Profile": ai_data.get("profile", ""),
        "Job Type": ai_data.get("job_type", ""),
        "Project Address": ai_data.get("address", "Not specified"),
        "Google Maps Query": ai_data.get("google_maps_query", ""),
        "Address Confidence": ai_data.get("address_confidence", "low"),
        "Address Source": ai_data.get("address_source", "not_found"),
        "Description": ai_data.get("description", ""),
        "Key Words": ai_data.get("keywords", ""),
        "Industry/Sector": ai_data.get("industry", ""),
        "Text Length": len(record.get("combined_text") or ""),
        "Source Project Folder": record.get("source_project_folder", ""),
        "Archived Files": "\n".join(record.get("archived_files") or []),
        "Errors": "\n".join(errors),
    }


def main():
    setup_logging("step4_ai_project_analysis")
    cache_path = Path(TEXT_CACHE_JSONL)

    if not cache_path.exists():
        print(f"Error: text cache not found: {cache_path}")
        print("Run step3_extract_project_text.py first.")
        return

    if not GEMINI_API_KEY:
        print("Error: Gemini API key is not set.")
        print("Set the GEMINI_API_KEY environment variable and run again.")
        return

    records = load_jsonl(cache_path)

    if not records:
        print(f"No records found in {cache_path}.")
        return

    try:
        validate_records(
            records,
            ["project_id", "status", "combined_text"],
            TEXT_CACHE_JSONL,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)
    output_rows_by_project_id = load_existing_output_rows()
    processed_this_run = 0
    skipped_existing = 0

    for index, record in enumerate(records, start=1):
        project_id = str(record.get("project_id", "")).strip()
        extraction_status = record.get("status", "")
        existing_row = output_rows_by_project_id.get(project_id)

        print(f"\nProcessing [{index}/{len(records)}]: {project_id}")

        if existing_row and is_completed_output_row(existing_row):
            print("  Existing completed result found. Skipping.")
            skipped_existing += 1
            continue

        if extraction_status not in {"Success", "Partial Success"}:
            print(f"  Skipping AI: extraction status is {extraction_status}")

            output_rows_by_project_id[project_id] = build_output_row(
                record,
                RESULT_FIELDS.copy(),
                "",
            )

            processed_this_run += 1

            if processed_this_run % SAVE_EVERY_N_ROWS == 0:
                save_output_rows(
                    build_ordered_output_rows(records, output_rows_by_project_id)
                )

            continue

        ai_data, ai_error = get_ai_analysis(
            client,
            record,
            record.get("combined_text", ""),
        )

        if ai_error:
            print(f"  {ai_error}")
        else:
            print("  AI analysis complete.")

        output_rows_by_project_id[project_id] = build_output_row(
            record,
            ai_data,
            ai_error,
        )

        processed_this_run += 1

        if processed_this_run % SAVE_EVERY_N_ROWS == 0:
            save_output_rows(
                build_ordered_output_rows(records, output_rows_by_project_id)
            )

        if STOP_ON_QUOTA_ERROR and is_quota_error(ai_error):
            print("  Gemini quota/rate limit reached. Stopping now and keeping saved progress.")
            break

        time.sleep(REQUEST_DELAY_SECONDS)

    output_rows = build_ordered_output_rows(records, output_rows_by_project_id)
    save_output_rows(output_rows)

    success_count = sum(1 for row in output_rows if row["Status"] == "Success")
    ai_error_count = sum(1 for row in output_rows if row["Status"] == "AI Error")

    print("\nTask complete.")
    print(f"Rows in output: {len(output_rows)}")
    print(f"Rows processed this run: {processed_this_run}")
    print(f"Existing completed rows skipped: {skipped_existing}")
    print(f"Successful AI analyses: {success_count}")
    print(f"AI errors: {ai_error_count}")
    print(f"Output saved to: {OUTPUT_EXCEL}")


if __name__ == "__main__":
    main()
