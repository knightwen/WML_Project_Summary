"""Step 3: extract text from archived project files.

Purpose:
- Read 2_project_file_archive.jsonl from Step 2.
- Extract text from archived PDF and DOCX files.
- Keep proposal/report text broad enough for AI analysis.
- For authority files, extract only the Job Description section when present.

Input:
- data/interim/2_project_file_archive.jsonl

Outputs:
- data/cache/3_project_pdf_text_cache.jsonl
- reports/3_project_pdf_text_cache.xlsx
- logs/3_pdf_text_extraction_log.txt

Resume:
- Supported. Existing completed text records are skipped.
- No Text Extracted records can be retried when RETRY_TEXT_ISSUE_ROWS is True.
"""

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import fitz  # PyMuPDF
import pandas as pd

from pipeline_utils import (
    backup_existing_file,
    ensure_parent_dir,
    require_config_section,
    require_config_value,
    setup_logging,
    validate_records,
    write_excel_safely,
    write_text_safely,
)


ARCHIVE_SUCCESS_STATUSES = {"Success", "Partial Success"}
EXTRACTION_SUCCESS_STATUSES = {"Success", "Partial Success"}

AUTHORITY_KEYWORDS = ["authority", "autority"]
AUTHORITY_SUFFIXES = {".docx", ".pdf"}
EMAIL_SUFFIXES = {".msg"}
AUTHORITY_ONLY_STATUS = "Authority Only"
ARCHIVE_EXTRACTABLE_STATUSES = ARCHIVE_SUCCESS_STATUSES | {AUTHORITY_ONLY_STATUS}
TEXT_RETRY_STATUSES = {"No Text Extracted"}

SHOW_EXTRACTED_TEXT_IN_CONSOLE = False

ILLEGAL_EXCEL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_CONFIG = require_config_section("step3")
FILE_ARCHIVE_JSONL = require_config_value(_CONFIG, "file_archive_jsonl", "step3")
TEXT_CACHE_JSONL = require_config_value(_CONFIG, "text_cache_jsonl", "step3")
TEXT_CACHE_EXCEL = require_config_value(_CONFIG, "text_cache_excel", "step3")
TEXT_EXTRACTION_LOG = require_config_value(_CONFIG, "text_extraction_log", "step3")
MAX_PAGES_PER_PDF = int(_CONFIG.get("max_pages_per_pdf", 5))
RESUME_FROM_EXISTING_OUTPUT = bool(_CONFIG.get("resume_from_existing_output", True))
RETRY_TEXT_ISSUE_ROWS = bool(_CONFIG.get("retry_text_issue_rows", True))


def clean_excel_text(value):
    """Remove characters that Excel/openpyxl cannot write into worksheets."""
    if value is None:
        return ""
    return ILLEGAL_EXCEL_CHARACTERS_RE.sub("", str(value))


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


def write_jsonl(records, output_path):
    output_path = ensure_parent_dir(output_path)
    backup_existing_file(output_path)
    with open(output_path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_pdf_text(pdf_path, max_pages=MAX_PAGES_PER_PDF):
    """Extract plain text from the first N pages of a PDF."""
    text_parts = []

    try:
        with fitz.open(pdf_path) as document:
            page_count = min(len(document), max_pages)
            for page_index in range(page_count):
                page_text = document[page_index].get_text("text")
                if page_text:
                    text_parts.append(page_text.strip())
    except Exception as exc:
        return "", f"PDF read failed: {exc}"

    return "\n\n".join(part for part in text_parts if part), ""


def extract_docx_text(docx_path):
    """Extract plain text from a DOCX file without adding external dependencies."""
    try:
        with zipfile.ZipFile(docx_path) as document:
            xml_text = document.read("word/document.xml")
    except Exception as exc:
        return "", f"DOCX read failed: {exc}"

    try:
        root = ElementTree.fromstring(xml_text)
    except Exception as exc:
        return "", f"DOCX XML parsing failed: {exc}"

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []

    for paragraph in root.iter(f"{namespace}p"):
        text_parts = [
            text_node.text
            for text_node in paragraph.iter(f"{namespace}t")
            if text_node.text
        ]
        paragraph_text = "".join(text_parts).strip()
        if paragraph_text:
            paragraphs.append(paragraph_text)

    return "\n".join(paragraphs), ""


def clean_email_body(text):
    if not text:
        return ""

    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)

    split_patterns = [
        r"\nFrom:\s.+?\nSent:\s.+?\nTo:\s",
        r"\n-{2,}\s*Original Message\s*-{2,}",
        r"\n_{5,}\n",
    ]

    for pattern in split_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = text[:match.start()]
            break

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if re.match(r"^(from|sent|to|cc|subject):\s", stripped, re.IGNORECASE):
            continue
        lines.append(stripped)

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def extract_msg_text(msg_path):
    try:
        import extract_msg
    except ImportError:
        return "", "MSG read failed: install extract-msg to read Outlook .msg files"

    try:
        message = extract_msg.Message(str(msg_path))
        try:
            body = getattr(message, "body", "") or ""
        finally:
            message.close()
    except Exception as exc:
        return "", f"MSG read failed: {exc}"

    return clean_email_body(body), ""


def extract_authority_job_description(text):
    """
    Extract only the Job Description section from authority files.

    Expected structure:
    Job Description:
        <target text>

    Job Value Excluding GST ($):
    """
    if not text:
        return ""

    normalized_text = text.replace("\r", "")

    patterns = [
        r"Job\s*Description\s*:?\s*(.*?)\s*Job\s*Value\s*Excluding",
        r"Job\s*Description\s*:?\s*(.*?)\s*Job\s*Value",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r"\n{3,}", "\n\n", extracted)
            extracted = re.sub(r"[ \t]+", " ", extracted)
            return extracted.strip()

    return ""


def extract_text_from_file(file_path):
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(file_path)

    if suffix == ".docx":
        return extract_docx_text(file_path)

    if suffix == ".msg":
        return extract_msg_text(file_path)

    return "", f"Unsupported file type: {file_path.suffix}"


def is_authority_file(file_path):
    path = Path(file_path)
    name_lower = path.name.lower()
    return (
        path.suffix.lower() in AUTHORITY_SUFFIXES
        and any(keyword in name_lower for keyword in AUTHORITY_KEYWORDS)
    )


def is_email_file(file_path):
    return Path(file_path).suffix.lower() in EMAIL_SUFFIXES


def print_text_extraction_summary(file_name, text):
    if SHOW_EXTRACTED_TEXT_IN_CONSOLE:
        print(text)
        return

    text_length = len(text or "")
    print(f"  Extracted text from {file_name}: {text_length} characters")


def calculate_extraction_success_rate(records):
    total_count = len(records)
    if total_count == 0:
        return 0, 0, 0, 0.0

    full_success_count = sum(
        1
        for record in records
        if record["status"] == "Success"
        and record.get("primary_text_file_count", 0) > 0
    )

    partial_success_count = sum(
        1
        for record in records
        if record["status"] == "Partial Success"
        and record.get("primary_text_file_count", 0) > 0
    )

    usable_success_count = full_success_count + partial_success_count
    success_rate = usable_success_count / total_count * 100

    return usable_success_count, full_success_count, partial_success_count, success_rate


def get_failure_reason(record):
    status = record.get("status", "")
    errors = record.get("errors") or []

    if status in EXTRACTION_SUCCESS_STATUSES:
        return ""

    if status == AUTHORITY_ONLY_STATUS:
        return (
            "Only authority file Job Description text was extracted; "
            "no primary file text was extracted."
        )

    if errors:
        return " | ".join(str(error) for error in errors)

    if status == "Not Found":
        return "Project folder was not found in the configured source disks."

    if status == "Missing Target Files":
        return (
            "No proposal PDF, Financial Management proposal-folder project-ID-starting PDF, "
            "Financial Management project-ID-starting PDF, report PDF, or authority PDF/DOCX "
            "was found under the configured target subfolders."
        )

    if status == "Copy Failed":
        return "Target files were found, but none could be copied to the archive folder."

    if status == "No Text Extracted":
        return "Target files were archived, but no readable text was extracted."

    return "Unknown failure reason."


def count_statuses(records):
    status_counts = {}

    for record in records:
        status = record.get("status", "Unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return status_counts


def format_file_list(label, file_paths):
    lines = [f"{label}:"]

    if not file_paths:
        lines.append("  - None")
        return lines

    for file_path in file_paths:
        lines.append(f"  - {file_path}")

    return lines


def write_text_extraction_log(records, output_path):
    success_count, full_success_count, partial_success_count, success_rate = (
        calculate_extraction_success_rate(records)
    )
    status_counts = count_statuses(records)

    lines = [
        "File Text Extraction Log",
        f"Generated At: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Summary",
        f"Records processed: {len(records)}",
        "Authority PDF/DOCX files are not counted in text-extraction success rates.",
        "Authority files only extract Job Description text.",
        f"Successful primary text extractions: {success_count}",
        f"Full successful primary extractions: {full_success_count}",
        f"Partial successful primary extractions: {partial_success_count}",
        f"Extraction success rate: {success_rate:.2f}%",
        "",
        "Status Counts",
    ]

    for status, count in sorted(status_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "Project Details"])

    for index, record in enumerate(records, start=1):
        errors = record.get("errors") or []
        failure_reason = get_failure_reason(record)
        text_length = len(record.get("combined_text") or "")

        lines.extend(
            [
                "",
                "-" * 80,
                f"[{index}/{len(records)}] Project ID: {record.get('project_id', '')}",
                f"Project Name: {record.get('project_name', '')}",
                f"Project Description: {record.get('project_description', '')}",
                f"Status: {record.get('status', '')}",
                f"Text Length: {text_length}",
                f"Primary Text File Count: {record.get('primary_text_file_count', 0)}",
                f"Authority Text File Count: {record.get('authority_text_file_count', 0)}",
                f"Project Display Name: {record.get('project_display_name', '')}",
                f"Client: {record.get('client', '')}",
                f"Manager: {record.get('manager', '')}",
                f"Source Project Folder: {record.get('source_project_folder', '') or 'None'}",
            ]
        )

        if failure_reason:
            lines.append(f"Failure Reason: {failure_reason}")

        if errors:
            lines.append("Errors / Warnings:")
            for error in errors:
                lines.append(f"  - {error}")

        lines.extend(format_file_list("Source Files", record.get("source_files") or []))
        lines.extend(format_file_list("Archived Files", record.get("archived_files") or []))
        lines.append(f"Extracted At: {record.get('extracted_at', '')}")

    write_text_safely(output_path, "\n".join(lines) + "\n", encoding="utf-8")


def build_text_record(archive_record):
    return {
        "project_id": archive_record.get("project_id", ""),
        "project_name": archive_record.get("project_display_name", ""),
        "project_description": "",
        "status": archive_record.get("status", ""),
        "project_display_name": archive_record.get("project_display_name", ""),
        "project_display_name_raw": archive_record.get("project_display_name_raw", ""),
        "project_display_name_no_id": archive_record.get(
            "project_display_name_no_id", ""
        ),
        "client": archive_record.get("client", ""),
        "manager": archive_record.get("manager", ""),
        "start_date": archive_record.get("start_date", ""),
        "contract_amount": archive_record.get("contract_amount", ""),
        "address_state": archive_record.get("address_state", ""),
        "city": archive_record.get("city", ""),
        "source_project_folder": archive_record.get("source_project_folder", ""),
        "archived_files": archive_record.get("archived_files") or [],
        "source_files": archive_record.get("source_files") or [],
        "combined_text": "",
        "primary_text_file_count": 0,
        "authority_text_file_count": 0,
        "errors": list(archive_record.get("errors") or []),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }


def extract_record_text(archive_record):
    record = build_text_record(archive_record)

    if archive_record.get("status") not in ARCHIVE_EXTRACTABLE_STATUSES:
        return record

    combined_parts = []
    extraction_errors = []

    for archived_file_path in record["archived_files"]:
        archived_file = Path(archived_file_path)

        if not archived_file.exists():
            extraction_errors.append(f"Archived file does not exist: {archived_file}")
            continue

        extracted_text, error = extract_text_from_file(archived_file)

        if error:
            extraction_errors.append(error)
            print(f"  {error}")
            continue

        if is_authority_file(archived_file):
            authority_job_description = extract_authority_job_description(extracted_text)

            if authority_job_description:
                record["project_description"] = authority_job_description

                if not record.get("project_name"):
                    record["project_name"] = authority_job_description

                extracted_text = (
                    "Authority Job Description:\n"
                    f"{authority_job_description}"
                )

            else:
                extracted_text = ""
                extraction_errors.append(
                    f"Authority Job Description not found: {archived_file}"
                )

        if extracted_text:
            combined_parts.append(
                f"--- Content from {archived_file.name} ---\n{extracted_text}"
            )
            print_text_extraction_summary(archived_file.name, extracted_text)

            if is_authority_file(archived_file):
                record["authority_text_file_count"] += 1
            elif is_email_file(archived_file):
                record["primary_text_file_count"] += 1
            else:
                record["primary_text_file_count"] += 1

    record["combined_text"] = "\n\n".join(combined_parts).strip()
    record["errors"].extend(extraction_errors)

    if record["primary_text_file_count"] > 0 and extraction_errors:
        record["status"] = "Partial Success"

    elif record["primary_text_file_count"] > 0:
        record["status"] = "Success"

    elif record["combined_text"]:
        record["status"] = AUTHORITY_ONLY_STATUS

    else:
        record["status"] = "No Text Extracted"

    return record


def build_excel_rows(records):
    rows = []

    for record in records:
        combined_text = record.get("combined_text") or ""

        rows.append(
            {
                "Project ID": clean_excel_text(record.get("project_id", "")),
                "Project Name": clean_excel_text(record.get("project_name", "")),
                "Project Description": clean_excel_text(
                    record.get("project_description", "")
                ),
                "Status": clean_excel_text(record.get("status", "")),
                "Project Display Name": clean_excel_text(
                    record.get("project_display_name", "")
                ),
                "Project Display Name Raw": clean_excel_text(
                    record.get("project_display_name_raw", "")
                ),
                "Project Display Name No ID": clean_excel_text(
                    record.get("project_display_name_no_id", "")
                ),
                "Client": clean_excel_text(record.get("client", "")),
                "Manager": clean_excel_text(record.get("manager", "")),
                "Start Date": clean_excel_text(record.get("start_date", "")),
                "Contract Amount": clean_excel_text(record.get("contract_amount", "")),
                "AddressState": clean_excel_text(record.get("address_state", "")),
                "City": clean_excel_text(record.get("city", "")),
                "Source Project Folder": clean_excel_text(
                    record.get("source_project_folder", "")
                ),
                "Source Files": clean_excel_text(
                    "\n".join(record.get("source_files") or [])
                ),
                "Archived Files": clean_excel_text(
                    "\n".join(record.get("archived_files") or [])
                ),
                "Text Length": len(combined_text),
                "Primary Text File Count": record.get("primary_text_file_count", 0),
                "Authority Text File Count": record.get("authority_text_file_count", 0),
                "Combined Text Preview": clean_excel_text(combined_text[:30000]),
                "Errors": clean_excel_text("\n".join(record.get("errors") or [])),
                "Extracted At": clean_excel_text(record.get("extracted_at", "")),
            }
        )

    return rows


def load_existing_text_records():
    if not RESUME_FROM_EXISTING_OUTPUT:
        return {}

    cache_path = Path(TEXT_CACHE_JSONL)

    if not cache_path.exists():
        return {}

    records_by_project_id = {}

    for record in load_jsonl(cache_path):
        project_id = str(record.get("project_id", "")).strip()
        if project_id:
            records_by_project_id[project_id] = record

    print(f"Loaded existing text extraction progress rows: {len(records_by_project_id)}")
    return records_by_project_id


def is_completed_text_record(record):
    status = str(record.get("status", "")).strip()

    if RETRY_TEXT_ISSUE_ROWS and status in TEXT_RETRY_STATUSES:
        return False

    if status in EXTRACTION_SUCCESS_STATUSES | {AUTHORITY_ONLY_STATUS}:
        return True

    if status in {"Not Found", "Missing Target Files", "Copy Failed"}:
        return True

    return bool(status)


def build_ordered_text_records(archive_records, records_by_project_id):
    records = []

    for archive_record in archive_records:
        project_id = str(archive_record.get("project_id", "")).strip()
        if project_id in records_by_project_id:
            records.append(records_by_project_id[project_id])

    return records


def save_text_outputs(records):
    write_jsonl(records, TEXT_CACHE_JSONL)

    try:
        write_excel_safely(
            pd.DataFrame(build_excel_rows(records)),
            TEXT_CACHE_EXCEL,
            index=False,
        )
    except PermissionError:
        print(f"Warning: cannot save {TEXT_CACHE_EXCEL}. Please close it in Excel.")

    write_text_extraction_log(records, TEXT_EXTRACTION_LOG)


def main():
    setup_logging("step3_extract_project_text")
    archive_path = Path(FILE_ARCHIVE_JSONL)

    if not archive_path.exists():
        print(f"Error: file archive cache not found: {archive_path}")
        print("Run step2_archive_project_files.py first.")
        return

    archive_records = load_jsonl(archive_path)

    if not archive_records:
        print(f"No records found in {FILE_ARCHIVE_JSONL}.")
        return

    try:
        validate_records(
            archive_records,
            ["project_id", "status", "archived_files"],
            FILE_ARCHIVE_JSONL,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    records_by_project_id = load_existing_text_records()
    processed_this_run = 0
    skipped_existing = 0

    for index, archive_record in enumerate(archive_records, start=1):
        project_id = str(archive_record.get("project_id", "")).strip()
        existing_record = records_by_project_id.get(project_id)

        print(f"\nProcessing [{index}/{len(archive_records)}]: {project_id}")

        if existing_record and is_completed_text_record(existing_record):
            print("  Existing completed text result found. Skipping.")
            skipped_existing += 1
            continue

        records_by_project_id[project_id] = extract_record_text(archive_record)
        processed_this_run += 1

        save_text_outputs(
            build_ordered_text_records(archive_records, records_by_project_id)
        )

    records = build_ordered_text_records(archive_records, records_by_project_id)
    save_text_outputs(records)

    success_count, full_success_count, partial_success_count, success_rate = (
        calculate_extraction_success_rate(records)
    )

    print("\nTask complete.")
    print(f"Rows in output: {len(records)}")
    print(f"Rows processed this run: {processed_this_run}")
    print(f"Existing completed rows skipped: {skipped_existing}")
    print("Authority PDF/DOCX files are not counted in text-extraction success rates.")
    print("Authority files only extract Job Description text.")
    print(f"Successful primary text extractions: {success_count}")
    print(f"Full successful primary extractions: {full_success_count}")
    print(f"Partial successful primary extractions: {partial_success_count}")
    print(f"Extraction success rate: {success_rate:.2f}%")
    print(f"Full text cache: {TEXT_CACHE_JSONL}")
    print(f"Excel preview: {TEXT_CACHE_EXCEL}")
    print(f"Extraction log: {TEXT_EXTRACTION_LOG}")


if __name__ == "__main__":
    main()

