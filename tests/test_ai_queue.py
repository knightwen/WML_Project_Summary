import pandas as pd

from step3_optional_ai_queue import build_queue_rows
from step4_ai_project_analysis import build_output_row, filter_records_for_ai_queue


def test_build_queue_rows_marks_text_success_ready_for_ai():
    records = [
        {
            "project_id": "1001",
            "project_name": "1001 Good",
            "status": "Success",
            "combined_text": "useful text",
            "address_state": "WA",
            "city": "Perth",
        },
        {
            "project_id": "1002",
            "project_name": "1002 No Text",
            "status": "No Text Extracted",
            "combined_text": "",
            "address_state": "WA",
            "city": "Bunbury",
        },
    ]

    rows = build_queue_rows(records)

    assert rows[0]["Ready For AI"] == "Yes"
    assert rows[0]["AI Priority"] == 1
    assert rows[0]["Text Length"] == 11
    assert rows[1]["Ready For AI"] == "No"
    assert rows[1]["Skip Reason"] == "No Text Extracted"


def test_filter_records_for_ai_queue_uses_ready_batch_and_limit():
    records = [
        {"project_id": "1001", "combined_text": "text 1"},
        {"project_id": "1002", "combined_text": "text 2"},
        {"project_id": "1003", "combined_text": "text 3"},
    ]
    queue_df = pd.DataFrame(
        [
            {"Project ID": "1001", "Ready For AI": "Yes", "AI Batch": "1", "AI Priority": 2},
            {"Project ID": "1002", "Ready For AI": "No", "AI Batch": "1", "AI Priority": 1},
            {"Project ID": "1003", "Ready For AI": "Yes", "AI Batch": "1", "AI Priority": 1},
        ]
    )

    selected = filter_records_for_ai_queue(
        records,
        queue_df,
        ai_batch="1",
        max_projects=1,
    )

    assert [record["project_id"] for record in selected] == ["1003"]


def test_build_output_row_marks_low_quality_ai_content_for_review():
    row = build_output_row(
        {
            "project_id": "1001",
            "status": "Success",
            "combined_text": "text was sent to AI",
            "project_display_name": "1001 Sample Project",
        },
        {
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
        },
        "",
    )

    assert row["Status"] == "AI Review Needed"
    assert "AI content quality issue" in row["Errors"]
    assert "google_maps_query is empty" in row["Errors"]
    assert row["Address Confidence"] == "low"
