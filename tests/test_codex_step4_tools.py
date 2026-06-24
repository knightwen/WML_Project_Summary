import json

from codex_step4_tools import (
    build_codex_prompt,
    build_output_row_from_codex_json,
    build_shard_records,
    collect_error_log_rows,
    merge_output_rows,
    normalize_codex_result,
)


def make_record(project_id, status="Success", text="project text"):
    return {
        "project_id": project_id,
        "status": status,
        "project_display_name": f"{project_id} Sample Project",
        "project_display_name_raw": f"{project_id}: Sample Project",
        "project_display_name_no_id": "Sample Project",
        "project_name": "Sample Project",
        "project_description": "",
        "client": "Client A",
        "manager": "Manager A",
        "start_date": "2020-01-01",
        "contract_amount": "1000",
        "address_state": "WA",
        "city": "Perth",
        "source_project_folder": r"X:\\1000 - 3999\\1001 Sample",
        "archived_files": [r"D:\\archive\\sample.pdf"],
        "errors": [],
        "combined_text": text,
    }


def test_build_shard_records_keeps_only_analyzable_records_and_limits_text():
    records = [
        make_record("1001", text="A" * 50),
        make_record("1002", status="No Text Extracted", text=""),
        make_record("1003", text="B" * 50),
    ]

    shard_records = build_shard_records(
        records,
        source_batch="1_500",
        max_records=2,
        max_text_chars=10,
    )

    assert [record["project_id"] for record in shard_records] == ["1001", "1003"]
    assert shard_records[0]["source_batch"] == "1_500"
    assert shard_records[0]["source_line"] == 1
    assert shard_records[0]["analysis_input"]["document_excerpt"] == "A" * 10
    assert "generated_project_name" in shard_records[0]["required_json_keys"]


def test_build_codex_prompt_contains_simplified_requirements_and_project_context():
    shard_record = build_shard_records(
        [make_record("1001", text="Inspect balcony at 1 Sample Street.")],
        source_batch="1_500",
        max_records=1,
    )[0]

    prompt = build_codex_prompt(shard_record)

    assert "ONLY valid JSON" in prompt
    assert "Do not treat WML staff as the client" in prompt
    assert "Profile writing rules" in prompt
    assert "Write profile as a compact noun phrase" in prompt
    assert "Description rules" in prompt
    assert "Write 1-3 concise sentences" in prompt
    assert "Project ID: 1001" in prompt
    assert "Inspect balcony" in prompt


def test_normalize_codex_result_fills_defaults_and_stringifies_values():
    result = normalize_codex_result(
        {
            "generated_project_name": "Sample Inspection",
            "profile": None,
            "address_confidence": "HIGH",
            "extra": "ignored",
        }
    )

    assert result["generated_project_name"] == "Sample Inspection"
    assert result["profile"] == ""
    assert result["address"] == "Not specified"
    assert result["address_confidence"] == "HIGH"
    assert "extra" not in result


def test_build_output_row_from_codex_json_matches_step4_review_behavior():
    row = build_output_row_from_codex_json(
        make_record("1001"),
        {
            "generated_project_name": "Sample Inspection",
            "profile": "a structural inspection for a sample project",
            "description": "The project covers a structural inspection.",
            "job_type": "inspection reporting",
            "keywords": "structural inspection, reporting",
            "industry": "residential",
            "address": "Not specified",
            "google_maps_query": "",
            "address_confidence": "low",
            "address_source": "not_found",
            "client_name": "Client A",
            "client_contact": "Not specified",
        },
        analysis_method="codex-local",
        source_batch="1_500",
        source_line=1,
    )

    assert row["Status"] == "AI Review Needed"
    assert row["_analysis_method"] == "codex-local"
    assert row["_source_batch"] == "1_500"
    assert row["_source_line"] == 1
    assert "address_confidence is low" in row["Errors"]


def test_merge_output_rows_accepts_json_lines_and_orders_by_source_records():
    source_records = [make_record("1001"), make_record("1002", status="Not Found", text="")]
    lines = [
        json.dumps(
            {
                "project_id": "1001",
                "source_batch": "1_500",
                "source_line": 1,
                "analysis": {
                    "generated_project_name": "Sample Inspection",
                    "profile": "a structural inspection for a sample project",
                    "description": "The project covers a structural inspection.",
                    "job_type": "inspection reporting",
                    "keywords": "structural inspection, reporting",
                    "industry": "residential",
                    "address": "1 Sample Street, Perth WA",
                    "google_maps_query": "1 Sample Street, Perth WA, Australia",
                    "address_confidence": "high",
                    "address_source": "pdf",
                    "client_name": "Client A",
                    "client_contact": "Not specified",
                },
            }
        )
    ]

    rows = merge_output_rows(source_records, lines)

    assert [row["Project ID"] for row in rows] == ["1001", "1002"]
    assert rows[0]["Status"] == "Success"
    assert rows[1]["Status"] == "Not Found"


def test_merge_output_rows_marks_missing_analyzable_output_as_ai_error():
    rows = merge_output_rows([make_record("1001")], [])

    assert rows[0]["Project ID"] == "1001"
    assert rows[0]["Status"] == "AI Error"
    assert "Missing Codex analysis output" in rows[0]["Errors"]


def test_merge_output_rows_accepts_explicit_codex_error():
    lines = [
        json.dumps(
            {
                "project_id": "1001",
                "source_batch": "1_500",
                "source_line": 1,
                "analysis_method": "codex-local-step4-sample",
                "error": "Insufficient project-specific information",
                "review_notes": "Document only contains boilerplate.",
            }
        )
    ]

    rows = merge_output_rows([make_record("1001")], lines)

    assert rows[0]["Status"] == "AI Error"
    assert "Insufficient project-specific information" in rows[0]["Errors"]
    assert "Document only contains boilerplate." in rows[0]["Errors"]


def test_collect_error_log_rows_returns_rows_with_errors():
    rows = [
        {"Project ID": "1001", "Status": "Success", "Errors": ""},
        {
            "Project ID": "1002",
            "Status": "AI Error",
            "Errors": "Insufficient project-specific information",
        },
    ]

    error_rows = collect_error_log_rows(rows)

    assert error_rows == [
        {
            "Project ID": "1002",
            "Status": "AI Error",
            "Errors": "Insufficient project-specific information",
        }
    ]
