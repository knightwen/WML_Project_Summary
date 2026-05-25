# Python DataClean Pipeline

This project prepares WML project records, archives relevant project files,
extracts text, enriches records with AI, geocodes project locations, and exports
Google Earth files.

## Project Structure

```text
.
├── data/
│   ├── raw/          # Optional local raw inputs
│   ├── interim/      # Intermediate machine-readable outputs
│   ├── processed/    # Main step outputs used by later steps
│   └── cache/        # Reusable caches
├── exports/
│   └── google_earth/ # KML/CSV/XLSX exports for mapping
├── logs/             # Step logs and extraction logs
├── reports/          # Human-readable Excel reports
├── pipeline_config.json
├── pipeline_utils.py
└── step*.py
```

## Setup

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Step 4 requires `GEMINI_API_KEY`.
Command Bash: export GEMINI_API_KEY='your-gemini-api-key'

Step 5 requires `GOOGLE_MAPS_API_KEY`.
Command Bash: export GOOGLE_MAPS_API_KEY='your-google-maps-api-key'

Set them in PowerShell before running those steps:

```powershell
$env:GEMINI_API_KEY = "your-gemini-api-key"
$env:GOOGLE_MAPS_API_KEY = "your-google-maps-api-key"
```

## Configuration

Edit `pipeline_config.json` before running.

External folders are centralized in the top-level `paths` section:

```json
{
  "paths": {
    "source_database_dir": "...",
    "archive_database_dir": "...",
    "disk_1000_3999": "..."
  }
}
```

Other sections can reference these values with placeholders such as:

```json
"input_excel": "{source_database_dir}\\job_sum.xlsx"
```

The output file names also use `{batch_range}`, which is generated from
`step1.row_start` and `step1.row_end`. For example, `row_start: 1` and
`row_end: 200` becomes `1_200`.

To rerun only specific projects in Step 2, set `step2.rerun_project_ids`:

```json
"rerun_project_ids": ["10966"]
```

When this list is not empty, Step 2 only scans those project IDs and updates
their records in the existing batch output. Clear the list after the targeted
rerun to return to normal batch processing.

Step 2 searches proposal/report files using `step2.target_file_suffixes`. The
default includes both PDF and DOCX:

```json
"target_file_suffixes": [".pdf", ".docx"]
```

When the normal Step 2 search only finds authority files or no target files, a
scoring fallback searches PDF/DOCX files across the project folder and keeps the
highest scoring candidates:

```json
"scoring_fallback_max_files": 2
```

Step 2 also scans Outlook `.msg` files from `Email` or `Emails` folders for
every project, regardless of whether proposal/report files were found. Only
messages whose path or content contains one of the configured first names are
selected:

```json
"email_suffixes": [".msg"],
"email_folder_names": ["email", "emails"],
"email_fallback_max_files": 5,
"email_body_scan_name_limit": 3,
"email_body_scan_max_files": 50
```

Step 3 reads `.msg` files with `extract-msg` and keeps the cleaned email body.

## Running The Pipeline

Run each step in order:

```powershell
python step1_prepare_source_projects.py
python step2_archive_project_files.py
python step3_extract_project_text.py
python step4_ai_project_analysis.py
python step5_fetch_coordinates.py
python step6_export_google_earth.py
```

## Step Outputs

| Step | Main output |
| --- | --- |
| 1 | `data/processed/1_source_projects_{batch_range}.xlsx` |
| 2 | `data/interim/2_project_file_archive_{batch_range}.jsonl` and `reports/2_project_file_archive_{batch_range}.xlsx` |
| 3 | `data/cache/3_project_pdf_text_cache_{batch_range}.jsonl` and `reports/3_project_pdf_text_cache_{batch_range}.xlsx` |
| 4 | `data/processed/4_project_analysis_results_{batch_range}.xlsx` |
| 5 | `data/processed/5_final_project_results_with_coordinates_{batch_range}.xlsx` |
| 6 | `exports/google_earth/6_project_locations_google_earth_{batch_range}.kml` |

Existing outputs are backed up into an `_backups` folder beside the overwritten
file when `safety.backup_existing_outputs` is enabled.
