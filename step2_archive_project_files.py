"""Step 2: archive selected project files.

Purpose:
- Read 1_source_projects.xlsx from Step 1.
- Find matching project folders using configured disk rules and cached folder indexes.
- Select proposal/report/authority files using the configured priority rules.
- Copy selected files into the local archive database.

Input:
- data/processed/1_source_projects.xlsx

Outputs:
- data/interim/2_project_file_archive.jsonl
- reports/2_project_file_archive.xlsx
- reports/2_project_file_archive_issues.xlsx

Resume:
- Supported. Existing completed archive records are skipped.
- Issue statuses can be retried when retry_archive_issue_rows is True.

Performance:
- Instead of scanning every configured source disk on every run, this script:
  1. chooses likely roots by Project ID range from pipeline_config.json;
  2. builds or loads a cached folder index per source root;
  3. reuses 2_disk_folder_index_cache.json for normal runs.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline_utils import (
    backup_existing_file,
    copy_file_safely,
    ensure_parent_dir,
    iter_files_safely,
    require_config_section,
    require_config_value,
    setup_logging,
    validate_columns,
    write_excel_safely,
)


_CONFIG = require_config_section("step2")
SOURCE_EXCEL = require_config_value(_CONFIG, "source_excel", "step2")
require_config_value(_CONFIG, "project_disk_rules", "step2")
FOLDER_INDEX_CACHE = require_config_value(_CONFIG, "folder_index_cache", "step2")
TARGET_DATABASE_DIR = Path(require_config_value(_CONFIG, "target_database_dir", "step2"))
FILE_ARCHIVE_JSONL = require_config_value(_CONFIG, "file_archive_jsonl", "step2")
FILE_ARCHIVE_EXCEL = require_config_value(_CONFIG, "file_archive_excel", "step2")
ISSUE_EXCEL = require_config_value(_CONFIG, "issue_excel", "step2")
RESUME_FROM_EXISTING_OUTPUT = bool(_CONFIG.get("resume_from_existing_output", True))
RETRY_ARCHIVE_ISSUE_ROWS = bool(_CONFIG.get("retry_archive_issue_rows", True))
TARGET_SUBFOLDERS = _CONFIG.get("target_subfolders", ["engineering", "Financial Management"])
AUTHORITY_KEYWORDS = _CONFIG.get("authority_keywords", ["authority", "autority"])
AUTHORITY_SUFFIXES = set(_CONFIG.get("authority_suffixes", [".docx", ".pdf"]))
TARGET_FILE_SUFFIXES = set(_CONFIG.get("target_file_suffixes", [".pdf", ".docx"]))
EMAIL_SUFFIXES = set(_CONFIG.get("email_suffixes", [".msg"]))
EMAIL_FOLDER_NAMES = {
    name.lower()
    for name in _CONFIG.get("email_folder_names", ["email", "emails"])
}
EMAIL_CONTACT_FIRST_NAMES = {
    name.lower()
    for name in _CONFIG.get(
        "email_contact_first_names",
        [
            "Greg",
            "Henk",
            "Anthony",
            "Nick",
            "Royce",
            "Imram",
            "Lobzang",
            "Falguni",
            "Cao",
            "Himanshu",
            "Kevin",
            "Robert",
            "Stephen",
            "Jing",
            "Jonathan",
            "Michael",
            "Rodgie",
            "Salman",
        ],
    )
}
SCORING_FALLBACK_MAX_FILES = int(_CONFIG.get("scoring_fallback_max_files", 2))
EMAIL_FALLBACK_MAX_FILES = int(_CONFIG.get("email_fallback_max_files", 2))
EMAIL_BODY_SCAN_NAME_LIMIT = int(_CONFIG.get("email_body_scan_name_limit", 3))
EMAIL_BODY_SCAN_MAX_FILES = int(_CONFIG.get("email_body_scan_max_files", 50))
AUTHORITY_ONLY_STATUS = "Authority Only"
ARCHIVE_RETRY_STATUSES = {
    "Not Found",
    "Source Root Missing",
    "Missing Target Files",
    AUTHORITY_ONLY_STATUS,
    "Copy Failed",
    "Partial Success",
}


def normalize_project_id(value):
    """Return clean project id."""
    if pd.isna(value):
        return ""

    text = str(value).strip()
    match = re.match(r"^\D*(\d+)", text)
    if match:
        return match.group(1)

    return text.replace(":", "").strip().split(".")[0]


RERUN_PROJECT_IDS = {
    normalize_project_id(project_id)
    for project_id in _CONFIG.get("rerun_project_ids", [])
}


def get_source_project_id(row):
    """Prefer Project ID Clean from Step 1, fallback to Project ID."""
    project_id = normalize_project_id(row.get("Project ID Clean", ""))

    if project_id:
        return project_id

    return normalize_project_id(row.get("Project ID", ""))


def project_id_to_int(project_id):
    try:
        return int(str(project_id).strip())
    except (TypeError, ValueError):
        return None


def get_candidate_roots_for_project(project_id, config):
    """
    Choose source roots from project_disk_rules, then fallback_roots.

    Fallback roots are used after any matching project range roots, or by
    themselves when no project range rule matches.
    """
    numeric_id = project_id_to_int(project_id)

    if numeric_id is None:
        return config.get("fallback_roots", [])

    roots = []
    for rule in config["project_disk_rules"]:
        if rule["min_id"] <= numeric_id <= rule["max_id"]:
            roots.extend(rule["roots"])
            break

    roots.extend(config.get("fallback_roots", []))
    return list(dict.fromkeys(roots))


def load_folder_index_cache(cache_path):
    path = Path(cache_path)

    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as input_file:
            return json.load(input_file)
    except Exception as exc:
        print(f"Warning: failed to load folder index cache: {exc}")
        return {}


def save_folder_index_cache(cache, cache_path):
    try:
        cache_path = ensure_parent_dir(cache_path)
        backup_existing_file(cache_path)
        with open(cache_path, "w", encoding="utf-8") as output_file:
            json.dump(cache, output_file, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"Warning: failed to save folder index cache: {exc}")


def build_single_root_index(root_path):
    """
    Scan one source root and build a project folder lookup:
    {
        "13184": "<source root>\\13184 ..."
    }
    """
    root = Path(root_path)
    folder_map = {}

    if not root.exists():
        print(f"Warning: source path does not exist: {root}")
        return folder_map

    print(f"  Scanning source root: {root}")

    try:
        children = list(root.iterdir())
    except Exception as exc:
        print(f"Warning: failed to scan source root {root}: {exc}")
        return folder_map

    for folder in children:
        try:
            if not folder.is_dir():
                continue
        except OSError as exc:
            print(f"Warning: cannot inspect folder {folder}: {exc}")
            continue

        project_id = normalize_project_id(folder.name.split()[0])

        if project_id and project_id not in folder_map:
            folder_map[project_id] = str(folder)

    print(f"  Indexed {len(folder_map)} project folders from {root}")
    return folder_map


def get_root_index(root_path, config, cache):
    root_key = str(Path(root_path))

    refresh_cache = config.get("refresh_folder_cache", False)

    if not refresh_cache and root_key in cache:
        return cache[root_key].get("folders", {})

    folder_map = build_single_root_index(root_path)

    cache[root_key] = {
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "folders": folder_map,
    }

    return folder_map


def find_project_folder(project_id, config, cache):
    """
    Find project folder using:
    1. Project ID range rules
    2. Cached folder index for matched roots

    If no rule matches, no disk is scanned.
    """
    candidate_roots = get_candidate_roots_for_project(project_id, config)

    if not candidate_roots:
        print(f"  No disk rule matched for project ID: {project_id}")
        return None, "No disk rule matched"

    accessible_roots = []
    missing_roots = []

    for root_path in candidate_roots:
        root = Path(root_path)
        if not root.exists():
            missing_roots.append(str(root))
            continue

        accessible_roots.append(str(root))
        folder_map = get_root_index(root_path, config, cache)
        folder_path = folder_map.get(project_id)

        if folder_path:
            return Path(folder_path), ""

    if missing_roots and not accessible_roots:
        return None, f"Source root missing or not mounted: {', '.join(missing_roots)}"

    return None, f"Project ID not found under: {', '.join(accessible_roots)}"


def get_target_search_roots(project_folder):
    """Find matching subfolders."""
    target_keywords = [subfolder.lower() for subfolder in TARGET_SUBFOLDERS]

    search_roots = []

    for child in project_folder.iterdir():
        if not child.is_dir():
            continue

        child_name = child.name.lower()

        if any(keyword in child_name for keyword in target_keywords):
            search_roots.append(child)

    return search_roots


def filename_starts_with_project_id(file_path, project_id):
    project_id = normalize_project_id(project_id).lower()
    file_stem = file_path.stem.strip().lower()

    return bool(project_id) and file_stem.startswith(project_id)


def is_under_proposal_folder(file_path, search_root):
    try:
        relative_parts = file_path.relative_to(search_root).parts[:-1]
    except ValueError:
        return False

    return any("proposal" in part.lower() for part in relative_parts)


def is_authority_file(file_path):
    path = Path(file_path)
    name_lower = path.name.lower()

    return (
        path.suffix.lower() in AUTHORITY_SUFFIXES
        and any(keyword in name_lower for keyword in AUTHORITY_KEYWORDS)
    )


def is_email_file(file_path):
    return Path(file_path).suffix.lower() in EMAIL_SUFFIXES


def is_under_email_folder(file_path):
    parts = [part.lower() for part in Path(file_path).parts]
    return any(part in EMAIL_FOLDER_NAMES for part in parts)


def get_first_name(value):
    match = re.search(r"[A-Za-z]+", str(value or ""))
    if not match:
        return ""
    return match.group(0).lower()


def iter_email_roots(project_folder):
    try:
        children = list(Path(project_folder).iterdir())
    except OSError as exc:
        print(f"  Warning: cannot scan project folder for emails: {exc}")
        return

    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue

        if child.name.lower() in EMAIL_FOLDER_NAMES:
            yield child


def find_contact_first_names(text, names=None):
    text = str(text or "").lower()
    names = names or EMAIL_CONTACT_FIRST_NAMES
    return [
        first_name
        for first_name in names
        if re.search(rf"\b{re.escape(first_name)}\b", text)
    ]


def contains_contact_first_name(text, names=None):
    return bool(find_contact_first_names(text, names=names))


def read_msg_search_text(msg_path):
    try:
        import extract_msg
    except ImportError:
        return ""

    try:
        message = extract_msg.Message(str(msg_path))
        try:
            parts = [
                getattr(message, "sender", "") or "",
                getattr(message, "to", "") or "",
                getattr(message, "cc", "") or "",
                getattr(message, "subject", "") or "",
                getattr(message, "body", "") or "",
            ]
            return "\n".join(parts)
        finally:
            message.close()
    except Exception:
        return ""


def get_latest_file(candidate_files):
    if not candidate_files:
        return None

    return max(candidate_files, key=lambda path: path.stat().st_mtime)


def get_latest_files(candidate_files, limit):
    return sorted(
        candidate_files,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]


def iter_subfolders(root):
    try:
        iterator = Path(root).rglob("*")
        for path in iterator:
            try:
                if path.is_dir():
                    yield path
            except OSError:
                continue
    except OSError as exc:
        print(f"  Warning: cannot scan email subfolders under {root}: {exc}")


def find_manager_email_folders(email_roots, manager):
    manager_first_name = get_first_name(manager)
    if not manager_first_name:
        return []

    matched_folders = []

    for email_root in email_roots:
        if contains_contact_first_name(email_root.name, names={manager_first_name}):
            matched_folders.append(email_root)

        for folder in iter_subfolders(email_root):
            if contains_contact_first_name(folder.name, names={manager_first_name}):
                matched_folders.append(folder)

    return add_unique_paths(matched_folders)


def find_email_folder_contact_names(email_roots):
    matched_names = []

    for email_root in email_roots:
        matched_names.extend(find_contact_first_names(email_root.name))

        for folder in iter_subfolders(email_root):
            matched_names.extend(find_contact_first_names(folder.name))

    return list(dict.fromkeys(matched_names))


def get_msg_files_under(root):
    msg_files = []

    for file_path in iter_files_safely(root):
        if is_email_file(file_path):
            msg_files.append(file_path)

    return msg_files


def get_preferred_authority_file(candidate_files):
    """
    Prefer DOCX authority files over PDF.
    Within same extension type, use latest modified file.
    """
    if not candidate_files:
        return None

    docx_files = [
        path for path in candidate_files
        if path.suffix.lower() == ".docx"
    ]

    if docx_files:
        return max(docx_files, key=lambda path: path.stat().st_mtime)

    pdf_files = [
        path for path in candidate_files
        if path.suffix.lower() == ".pdf"
    ]

    if pdf_files:
        return max(pdf_files, key=lambda path: path.stat().st_mtime)

    return None


def add_unique_paths(paths):
    unique_paths = []
    seen = set()

    for path in paths:
        path = Path(path)
        path_key = str(path).lower()
        if path_key in seen:
            continue
        seen.add(path_key)
        unique_paths.append(path)

    return unique_paths


def score_fallback_file(file_path, project_id):
    path = Path(file_path)
    name_lower = path.name.lower()
    path_lower = str(path).lower()
    score = 0

    if filename_starts_with_project_id(path, project_id):
        score += 100

    if re.search(rf"\b{re.escape(project_id)}\b", name_lower):
        score += 60

    positive_keywords = {
        "proposal": 90,
        "quote": 80,
        "quotation": 80,
        "fee": 70,
        "scope": 50,
        "submission": 50,
        "report": 70,
        "inspection": 60,
        "assessment": 60,
        "review": 55,
        "advice": 55,
        "letter": 45,
        "certificate": 55,
        "certification": 55,
        "form 15": 50,
        "form 16": 50,
        "design": 45,
        "calculation": 45,
        "calc": 35,
        "memo": 30,
        "issued": 35,
    }
    for keyword, weight in positive_keywords.items():
        if keyword in name_lower or keyword in path_lower:
            score += weight

    negative_keywords = {
        "invoice",
        "timesheet",
        "receipt",
        "photo",
        "image",
        "drawing register",
        "transmittal",
        "minutes",
    }
    if any(keyword in name_lower or keyword in path_lower for keyword in negative_keywords):
        score -= 120

    if path.suffix.lower() == ".pdf":
        score += 10
    elif path.suffix.lower() == ".docx":
        score += 5

    return score


def find_scoring_fallback_files(project_folder, project_id):
    scored_files = []

    for file_path in iter_files_safely(project_folder):
        if file_path.name.startswith("~$"):
            continue

        if is_authority_file(file_path):
            continue

        if file_path.suffix.lower() not in TARGET_FILE_SUFFIXES:
            continue

        score = score_fallback_file(file_path, project_id)
        if score > 0:
            scored_files.append((score, file_path.stat().st_mtime, str(file_path), file_path))

    scored_files.sort(reverse=True)
    return [
        file_path
        for _, _, _, file_path in scored_files[:SCORING_FALLBACK_MAX_FILES]
    ]


def find_relevant_email_files(project_folder, manager):
    email_files = []
    email_roots = list(iter_email_roots(project_folder))

    if not email_roots:
        return []

    print(f"  Email folders found: {len(email_roots)}")

    manager_folders = find_manager_email_folders(email_roots, manager)
    if manager_folders:
        print(f"  Manager email folders matched: {len(manager_folders)}")
        for manager_folder in manager_folders:
            email_files.extend(get_msg_files_under(manager_folder))
            if len(email_files) >= EMAIL_FALLBACK_MAX_FILES:
                return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)

        return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)

    matched_names = find_email_folder_contact_names(email_roots)
    msg_files = []

    for email_root in email_roots:
        for file_path in iter_files_safely(email_root):
            if not is_email_file(file_path):
                continue

            msg_files.append(file_path)
            path_text = str(file_path)
            path_names = find_contact_first_names(path_text)
            if path_names:
                matched_names.extend(path_names)
                email_files.append(file_path)
                if len(email_files) >= EMAIL_FALLBACK_MAX_FILES:
                    return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)

    if email_files:
        return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)

    matched_names = list(dict.fromkeys(matched_names))[:EMAIL_BODY_SCAN_NAME_LIMIT]
    if not matched_names:
        print("  No email path/name matches found. Skipping MSG body scan.")
        return []

    print(f"  Scanning MSG bodies for names: {', '.join(matched_names)}")

    for file_path in get_latest_files(msg_files, EMAIL_BODY_SCAN_MAX_FILES):
        msg_text = read_msg_search_text(file_path)
        if msg_text and contains_contact_first_name(msg_text, names=set(matched_names)):
            email_files.append(file_path)

        if len(email_files) >= EMAIL_FALLBACK_MAX_FILES:
            return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)

    return get_latest_files(email_files, EMAIL_FALLBACK_MAX_FILES)


def find_latest_project_files(project_folder, project_id, manager=""):
    """
    Selection priority:

    1. filename starts with project id + contains fee + proposal
    2. filename contains proposal
    3. filename starts with project id under Financial Management/Proposal
    4. filename starts with project id anywhere
    5. latest report file
    6. preferred authority file
    """

    project_id_proposal_files = []
    proposal_files = []
    financial_management_proposal_project_id_files = []
    project_id_files = []
    report_files = []
    authority_files = []

    for search_root in get_target_search_roots(project_folder):
        is_financial_management = (
            "financial management" in search_root.name.lower()
        )

        for file_path in iter_files_safely(search_root):
            if file_path.name.startswith("~$"):
                continue

            name_lower = file_path.name.lower()
            suffix_lower = file_path.suffix.lower()

            if is_authority_file(file_path):
                authority_files.append(file_path)
                continue

            if suffix_lower not in TARGET_FILE_SUFFIXES:
                continue

            starts_with_project_id = (
                filename_starts_with_project_id(file_path, project_id)
            )

            filename_contains_proposal = "proposal" in name_lower
            filename_contains_fee = "fee" in name_lower

            path_is_financial_management_proposal = (
                is_financial_management
                and is_under_proposal_folder(file_path, search_root)
            )

            if (
                starts_with_project_id
                and filename_contains_proposal
                and filename_contains_fee
            ):
                project_id_proposal_files.append(file_path)

            if filename_contains_proposal:
                proposal_files.append(file_path)

            if (
                starts_with_project_id
                and path_is_financial_management_proposal
            ):
                financial_management_proposal_project_id_files.append(file_path)

            if starts_with_project_id:
                project_id_files.append(file_path)

            if "report" in name_lower:
                report_files.append(file_path)

    selected = []

    latest_file = get_latest_file(project_id_proposal_files)

    if not latest_file:
        latest_file = get_latest_file(proposal_files)

    if not latest_file:
        latest_file = get_latest_file(
            financial_management_proposal_project_id_files
        )

    if not latest_file:
        latest_file = get_latest_file(project_id_files)

    if latest_file:
        selected.append(latest_file)

    latest_report = get_latest_file(report_files)

    if latest_report and latest_report not in selected:
        selected.append(latest_report)

    authority_file = get_preferred_authority_file(authority_files)

    if authority_file and authority_file not in selected:
        selected.append(authority_file)

    if not get_primary_files(selected):
        fallback_files = find_scoring_fallback_files(project_folder, project_id)
        if fallback_files:
            print(f"  Scoring fallback files found: {len(fallback_files)}")
            selected.extend(fallback_files)

    email_files = find_relevant_email_files(project_folder, manager)
    if email_files:
        print(f"  Email files found: {len(email_files)}")
        selected.extend(email_files)

    return add_unique_paths(selected)


def unique_destination_path(destination_folder, source_file):
    destination = destination_folder / source_file.name

    if not destination.exists():
        return destination

    if destination.stat().st_size == source_file.stat().st_size:
        return destination

    counter = 2

    while True:
        candidate = (
            destination_folder
            / f"{source_file.stem}_{counter}{source_file.suffix}"
        )

        if not candidate.exists():
            return candidate

        counter += 1


def copy_file_to_archive(source_file, project_id):
    destination_folder = TARGET_DATABASE_DIR / project_id
    destination_folder.mkdir(parents=True, exist_ok=True)

    destination = unique_destination_path(
        destination_folder,
        source_file,
    )

    if (
        not destination.exists()
        or destination.stat().st_size != source_file.stat().st_size
    ):
        copy_file_safely(source_file, destination)

    return destination


def write_jsonl(records, output_path):
    output_path = ensure_parent_dir(output_path)
    backup_existing_file(output_path)
    with open(output_path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )


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


def get_primary_files(file_paths):
    return [
        file_path
        for file_path in file_paths
        if not is_authority_file(file_path)
    ]


def build_excel_rows(records):
    rows = []

    for record in records:
        rows.append(
            {
                "Project ID": record["project_id"],
                "Status": record["status"],
                "Project Display Name": record["project_display_name"],
                "Project Display Name Raw": record.get("project_display_name_raw", ""),
                "Project Display Name No ID": record.get("project_display_name_no_id", ""),
                "Client": record["client"],
                "Manager": record["manager"],
                "Start Date": record["start_date"],
                "Contract Amount": record["contract_amount"],
                "AddressState": record["address_state"],
                "City": record["city"],
                "Source Project Folder": record["source_project_folder"],
                "Source Files": "\n".join(record["source_files"]),
                "Archived Files": "\n".join(record["archived_files"]),
                "Errors": "\n".join(record["errors"]),
                "Archived At": record["archived_at"],
            }
        )

    return rows


def write_issue_excel(records, output_path):
    """Write problem records to a separate Excel file."""

    issue_statuses = {
        "Not Found",
        "Source Root Missing",
        "Missing Target Files",
        "Authority Only",
        "Copy Failed",
        "Partial Success",
    }

    issue_records = [
        record
        for record in records
        if record.get("status") in issue_statuses
    ]

    if not issue_records:
        print("No issue records found.")
        return

    issue_rows = []

    for record in issue_records:
        issue_rows.append(
            {
                "Project ID": record.get("project_id", ""),
                "Status": record.get("status", ""),
                "Project Display Name": record.get(
                    "project_display_name",
                    "",
                ),
                "Project Display Name Raw": record.get(
                    "project_display_name_raw",
                    "",
                ),
                "Project Display Name No ID": record.get(
                    "project_display_name_no_id",
                    "",
                ),
                "Client": record.get("client", ""),
                "Manager": record.get("manager", ""),
                "Start Date": record.get("start_date", ""),
                "Contract Amount": record.get("contract_amount", ""),
                "AddressState": record.get("address_state", ""),
                "City": record.get("city", ""),
                "Source Project Folder": record.get(
                    "source_project_folder",
                    "",
                ),
                "Source Files": "\n".join(
                    record.get("source_files") or []
                ),
                "Archived Files": "\n".join(
                    record.get("archived_files") or []
                ),
                "Errors": "\n".join(
                    record.get("errors") or []
                ),
                "Archived At": record.get("archived_at", ""),
            }
        )

    write_excel_safely(pd.DataFrame(issue_rows), output_path, index=False)

    print(f"Issue records saved to: {output_path}")


def load_existing_archive_records():
    if not RESUME_FROM_EXISTING_OUTPUT:
        return {}

    archive_path = Path(FILE_ARCHIVE_JSONL)

    if not archive_path.exists():
        return {}

    records_by_project_id = {}

    for record in load_jsonl(archive_path):
        project_id = str(record.get("project_id", "")).strip()
        if project_id:
            records_by_project_id[project_id] = record

    print(f"Loaded existing archive progress rows: {len(records_by_project_id)}")
    return records_by_project_id


def is_completed_archive_record(record):
    status = str(record.get("status", "")).strip()

    if RETRY_ARCHIVE_ISSUE_ROWS and status in ARCHIVE_RETRY_STATUSES:
        return False

    return bool(status)


def build_ordered_archive_records(source_df, records_by_project_id):
    records = []

    for _, row in source_df.iterrows():
        project_id = get_source_project_id(row)
        if project_id in records_by_project_id:
            records.append(records_by_project_id[project_id])

    return records


def save_archive_outputs(records):
    write_jsonl(records, FILE_ARCHIVE_JSONL)

    try:
        write_excel_safely(
            pd.DataFrame(build_excel_rows(records)),
            FILE_ARCHIVE_EXCEL,
            index=False,
        )
    except PermissionError:
        print(f"Warning: cannot save {FILE_ARCHIVE_EXCEL}. Please close it in Excel.")

    try:
        write_issue_excel(records, ISSUE_EXCEL)
    except PermissionError:
        print(f"Warning: cannot save {ISSUE_EXCEL}. Please close it in Excel.")


def build_archive_record(row, project_id):
    return {
        "project_id": project_id,
        "status": "Not Found",
        "project_display_name": str(
            row.get("Project Display Name", "")
        ).strip(),
        "project_display_name_raw": str(
            row.get("Project Display Name Raw", "")
        ).strip(),
        "project_display_name_no_id": str(
            row.get("Project Display Name No ID", "")
        ).strip(),
        "client": str(row.get("Client", "")).strip(),
        "manager": str(row.get("Manager", "")).strip(),
        "start_date": str(row.get("Start Date", "")).strip(),
        "contract_amount": str(
            row.get("Contract Amount", "")
        ).strip(),
        "address_state": str(
            row.get("AddressState", "")
        ).strip(),
        "city": str(row.get("City", "")).strip(),
        "source_project_folder": "",
        "source_files": [],
        "archived_files": [],
        "errors": [],
        "archived_at": datetime.now().isoformat(
            timespec="seconds"
        ),
    }


def main():
    setup_logging("step2_archive_project_files")

    source_path = Path(SOURCE_EXCEL)

    if not source_path.exists():
        print(f"Error: source Excel not found: {source_path}")
        return

    folder_index_cache = load_folder_index_cache(FOLDER_INDEX_CACHE)

    try:
        df = pd.read_excel(source_path)

    except Exception as exc:
        print(f"Error: failed to read {SOURCE_EXCEL}: {exc}")
        return

    if df.empty:
        print("Source Excel has no rows.")
        return

    try:
        validate_columns(df, ["Project ID"], SOURCE_EXCEL)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    records_by_project_id = load_existing_archive_records()
    processed_this_run = 0
    skipped_existing = 0
    skipped_not_targeted = 0

    if RERUN_PROJECT_IDS:
        print(f"Rerun project IDs: {', '.join(sorted(RERUN_PROJECT_IDS))}")

    for index, row in df.iterrows():
        project_id = get_source_project_id(row)
        existing_record = records_by_project_id.get(project_id)

        if RERUN_PROJECT_IDS and project_id not in RERUN_PROJECT_IDS:
            skipped_not_targeted += 1
            continue

        print(f"\nProcessing [{index + 1}/{len(df)}]: {project_id}")

        if (
            existing_record
            and is_completed_archive_record(existing_record)
            and project_id not in RERUN_PROJECT_IDS
        ):
            print("  Existing completed archive result found. Skipping.")
            skipped_existing += 1
            continue

        record = build_archive_record(row, project_id)

        project_folder, lookup_error = find_project_folder(
            project_id,
            _CONFIG,
            folder_index_cache,
        )

        if not project_folder:
            if lookup_error.startswith("Source root missing"):
                record["status"] = "Source Root Missing"
            record["errors"].append(lookup_error)
            print(f"  Project folder not found: {lookup_error}")
            records_by_project_id[project_id] = record
            processed_this_run += 1
            save_folder_index_cache(folder_index_cache, FOLDER_INDEX_CACHE)
            save_archive_outputs(
                build_ordered_archive_records(df, records_by_project_id)
            )
            continue

        record["source_project_folder"] = str(project_folder)

        selected_files = find_latest_project_files(
            project_folder,
            project_id,
            record["manager"],
        )
        selected_files = add_unique_paths(selected_files)

        if not selected_files:
            record["status"] = "Missing Target Files"

            print("  No target files found.")

            records_by_project_id[project_id] = record
            processed_this_run += 1
            save_folder_index_cache(folder_index_cache, FOLDER_INDEX_CACHE)
            save_archive_outputs(
                build_ordered_archive_records(df, records_by_project_id)
            )
            continue

        for source_file in selected_files:
            if not source_file.exists():
                message = f"Source file not found: {source_file}"
                record["errors"].append(message)
                print(f"  {message}")
                continue

            record["source_files"].append(str(source_file))

            try:
                archived_file = copy_file_to_archive(
                    source_file,
                    project_id,
                )

                record["archived_files"].append(
                    str(archived_file)
                )

                print(f"  Archived: {archived_file.name}")

            except Exception as exc:
                message = (
                    f"Copy failed for {source_file}: {exc}"
                )

                record["errors"].append(message)

                print(f"  {message}")

        primary_archived_files = get_primary_files(
            record["archived_files"]
        )

        if primary_archived_files and record["errors"]:
            record["status"] = "Partial Success"

        elif primary_archived_files:
            record["status"] = "Success"

        elif record["archived_files"]:
            record["status"] = AUTHORITY_ONLY_STATUS

        else:
            record["status"] = "Copy Failed"

        records_by_project_id[project_id] = record
        processed_this_run += 1

        save_folder_index_cache(folder_index_cache, FOLDER_INDEX_CACHE)
        save_archive_outputs(
            build_ordered_archive_records(df, records_by_project_id)
        )

    records = build_ordered_archive_records(df, records_by_project_id)

    save_folder_index_cache(folder_index_cache, FOLDER_INDEX_CACHE)
    save_archive_outputs(records)

    print("\nTask complete.")
    print(f"Rows in output: {len(records)}")
    print(f"Rows processed this run: {processed_this_run}")
    print(f"Existing completed rows skipped: {skipped_existing}")
    if RERUN_PROJECT_IDS:
        print(f"Rows skipped outside rerun target: {skipped_not_targeted}")
    print(f"Folder index cache: {FOLDER_INDEX_CACHE}")
    print(f"JSONL: {FILE_ARCHIVE_JSONL}")
    print(f"Excel: {FILE_ARCHIVE_EXCEL}")
    print(f"Issue Excel: {ISSUE_EXCEL}")


if __name__ == "__main__":
    main()
