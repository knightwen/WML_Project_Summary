"""Sidecar helpers for Codex-assisted Step 4 project analysis.

These helpers do not call an AI API. They prepare bounded inputs for a Codex
worker/sub-agent and merge the worker's JSON output into the existing Step 4
row shape.
"""

import json
from pathlib import Path

import pandas as pd

from step4_ai_project_analysis import RESULT_FIELDS, build_output_row


OUTPUT_COLUMNS = [
    "Project ID",
    "Status",
    "Extraction Status",
    "Original Project Display Name",
    "Original Project Display Name Raw",
    "Original Project Display Name No ID",
    "Pre-extracted Project Name",
    "Pre-extracted Project Description",
    "Generated Project Name",
    "Original Client",
    "Client Name",
    "Client Contact",
    "Manager",
    "Start Date",
    "Contract Amount",
    "AddressState",
    "City",
    "Project Profile",
    "Job Type",
    "Project Address",
    "Google Maps Query",
    "Address Confidence",
    "Address Source",
    "Description",
    "Key Words",
    "Industry/Sector",
    "Text Length",
    "Source Project Folder",
    "Archived Files",
    "Errors",
]

ANALYZABLE_STATUSES = {"Success", "Partial Success"}
DEFAULT_ANALYSIS_METHOD = "codex-local-step4"

SIMPLIFIED_REQUIREMENTS = """You are an engineering project database analyst.

Return ONLY valid JSON with these exact keys:
generated_project_name, profile, description, job_type, keywords, industry,
address, google_maps_query, address_confidence, address_source, client_name,
client_contact.

If the document excerpt and metadata do not contain enough project-specific
information to produce a factual project profile and description, return ONLY:
{"error": "Insufficient project-specific information", "review_notes": "<brief reason>"}
Do not fill low-quality placeholder analysis fields in that case.

Rules:
- Use only the supplied project metadata and document excerpt. Do not invent facts.
- If uncertain, use Not specified, low, not_found, or an empty google_maps_query.
- Remove internal project ids and admin words from generated_project_name.
- Choose job_type from common engineering categories where possible.
- Choose industry from ports, rail, commercial, industrial, mining, residential,
  infrastructure, government, or a standard sector if clearly better.
- Prefer project-specific facts over generic company capability text.
- Use address clues in the document, project name, source folder, then city/state.
- Build google_maps_query for geocoding, WA/Australia first when ambiguous.
- Do not use WML's office address as the project address unless it is clearly the site.
- Do not treat WML staff as the client unless the text explicitly says WML is the client.

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
"""


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
                raise ValueError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
    return records


def write_jsonl(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_excel(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def is_analyzable_record(record):
    return (
        str(record.get("status", "")).strip() in ANALYZABLE_STATUSES
        and bool(str(record.get("combined_text") or "").strip())
    )


def build_analysis_input(record, max_text_chars=12000):
    text = str(record.get("combined_text") or "")
    return {
        "project_id": str(record.get("project_id", "")).strip(),
        "original_project_display_name": record.get("project_display_name", ""),
        "original_project_display_name_raw": record.get("project_display_name_raw", ""),
        "original_project_display_name_no_id": record.get(
            "project_display_name_no_id",
            "",
        ),
        "pre_extracted_project_name": record.get("project_name", ""),
        "pre_extracted_project_description": record.get("project_description", ""),
        "original_client": record.get("client", ""),
        "original_city": record.get("city", ""),
        "original_state": record.get("address_state", ""),
        "source_project_folder": record.get("source_project_folder", ""),
        "document_excerpt": text[:max_text_chars],
    }


def build_shard_records(records, source_batch, max_records=0, max_text_chars=12000):
    shard_records = []

    for line_number, record in enumerate(records, start=1):
        if not is_analyzable_record(record):
            continue

        shard_records.append(
            {
                "project_id": str(record.get("project_id", "")).strip(),
                "source_batch": source_batch,
                "source_line": line_number,
                "required_json_keys": list(RESULT_FIELDS.keys()),
                "requirements": SIMPLIFIED_REQUIREMENTS,
                "analysis_input": build_analysis_input(record, max_text_chars),
            }
        )

        if max_records and len(shard_records) >= max_records:
            break

    return shard_records


def build_codex_prompt(shard_record):
    analysis_input = shard_record["analysis_input"]
    lines = [
        SIMPLIFIED_REQUIREMENTS.strip(),
        "",
        f"Project ID: {analysis_input.get('project_id', '')}",
        (
            "Original project display name: "
            f"{analysis_input.get('original_project_display_name', '')}"
        ),
        (
            "Original project display name raw: "
            f"{analysis_input.get('original_project_display_name_raw', '')}"
        ),
        (
            "Original project display name without id: "
            f"{analysis_input.get('original_project_display_name_no_id', '')}"
        ),
        (
            "Pre-extracted project name: "
            f"{analysis_input.get('pre_extracted_project_name', '')}"
        ),
        (
            "Pre-extracted project description: "
            f"{analysis_input.get('pre_extracted_project_description', '')}"
        ),
        f"Original client: {analysis_input.get('original_client', '')}",
        f"Original city: {analysis_input.get('original_city', '')}",
        f"Original state: {analysis_input.get('original_state', '')}",
        f"Source project folder: {analysis_input.get('source_project_folder', '')}",
        "",
        "Document excerpt:",
        analysis_input.get("document_excerpt", ""),
    ]
    return "\n".join(lines)


def normalize_codex_result(data):
    normalized = RESULT_FIELDS.copy()
    if not isinstance(data, dict):
        return normalized

    for key in normalized:
        value = data.get(key, normalized[key])
        if value is None:
            value = normalized[key]
        normalized[key] = str(value).strip()
    return normalized


def build_output_row_from_codex_json(
    source_record,
    codex_json,
    analysis_method=DEFAULT_ANALYSIS_METHOD,
    source_batch="",
    source_line="",
):
    row = build_output_row(source_record, normalize_codex_result(codex_json), "")
    row["_analysis_method"] = analysis_method
    row["_source_batch"] = source_batch
    row["_source_line"] = source_line
    return row


def build_error_output_row(
    source_record,
    error,
    review_notes="",
    analysis_method=DEFAULT_ANALYSIS_METHOD,
    source_batch="",
    source_line="",
):
    message = str(error or "Codex analysis error").strip()
    notes = str(review_notes or "").strip()
    if notes:
        message = f"{message}; {notes}"
    row = build_output_row(source_record, RESULT_FIELDS.copy(), message)
    row["_analysis_method"] = analysis_method
    row["_source_batch"] = source_batch
    row["_source_line"] = source_line
    return row


def build_skipped_output_row(source_record, source_batch="", source_line=""):
    row = build_output_row(source_record, RESULT_FIELDS.copy(), "")
    row["_analysis_method"] = "codex-local-step4-skipped"
    row["_source_batch"] = source_batch
    row["_source_line"] = source_line
    return row


def build_missing_analysis_output_row(source_record, source_batch="", source_line=""):
    row = build_output_row(
        source_record,
        RESULT_FIELDS.copy(),
        "Missing Codex analysis output",
    )
    row["_analysis_method"] = "codex-local-step4-missing-output"
    row["_source_batch"] = source_batch
    row["_source_line"] = source_line
    return row


def parse_codex_output_lines(lines):
    outputs = {}
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid Codex output JSON line {line_number}: {exc}") from exc

        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            raise ValueError(f"Codex output line {line_number} is missing project_id")
        outputs[project_id] = payload
    return outputs


def merge_output_rows(source_records, codex_output_lines, source_batch=""):
    codex_outputs = parse_codex_output_lines(codex_output_lines)
    rows = []

    for source_line, source_record in enumerate(source_records, start=1):
        project_id = str(source_record.get("project_id", "")).strip()
        output = codex_outputs.get(project_id)

        if output:
            if output.get("error"):
                rows.append(
                    build_error_output_row(
                        source_record,
                        output.get("error", ""),
                        review_notes=output.get("review_notes", ""),
                        analysis_method=output.get(
                            "analysis_method",
                            DEFAULT_ANALYSIS_METHOD,
                        ),
                        source_batch=output.get("source_batch", source_batch),
                        source_line=output.get("source_line", source_line),
                    )
                )
                continue

            rows.append(
                build_output_row_from_codex_json(
                    source_record,
                    output.get("analysis", output),
                    analysis_method=output.get(
                        "analysis_method",
                        DEFAULT_ANALYSIS_METHOD,
                    ),
                    source_batch=output.get("source_batch", source_batch),
                    source_line=output.get("source_line", source_line),
                )
            )
            continue

        if is_analyzable_record(source_record):
            rows.append(
                build_missing_analysis_output_row(
                    source_record,
                    source_batch=source_batch,
                    source_line=source_line,
                )
            )
        else:
            rows.append(
                build_skipped_output_row(
                    source_record,
                    source_batch=source_batch,
                    source_line=source_line,
                )
            )

    return rows


def collect_error_log_rows(rows):
    error_rows = []
    for row in rows:
        if str(row.get("Status", "")).strip() == "Success":
            continue
        errors = str(row.get("Errors", "") or "").strip()
        if not errors:
            continue
        error_rows.append(
            {
                "Project ID": row.get("Project ID", ""),
                "Status": row.get("Status", ""),
                "Errors": errors,
            }
        )
    return error_rows
