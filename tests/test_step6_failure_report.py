import pandas as pd

from step6_export_google_earth import (
    build_all_projects_table,
    build_export_table,
    build_failure_detail_table,
    build_failure_summary_table,
)


def test_failure_report_tables_include_projects_without_exportable_coordinates():
    df = pd.DataFrame(
        [
            {
                "Project ID": "1001",
                "Status": "Success",
                "Extraction Status": "Success",
                "Original Project Display Name": "1001 Good Project",
                "Google Latitude": -31.95,
                "Google Longitude": 115.86,
                "Google Geocode Status": "Success",
                "Address Confidence": "high",
            },
            {
                "Project ID": "1002",
                "Status": "Missing Target Files",
                "Extraction Status": "Missing Target Files",
                "Original Project Display Name": "1002 No Files",
                "Google Latitude": "",
                "Google Longitude": "",
                "Google Geocode Status": "",
                "Address Confidence": "low",
            },
            {
                "Project ID": "1003",
                "Status": "No Text Extracted",
                "Extraction Status": "No Text Extracted",
                "Original Project Display Name": "1003 No Text",
                "Google Latitude": "",
                "Google Longitude": "",
                "Google Geocode Status": "",
                "Address Confidence": "low",
            },
            {
                "Project ID": "1004",
                "Status": "Success",
                "Extraction Status": "Success",
                "Original Project Display Name": "1004 Low Address",
                "Project Address": "Not specified",
                "Google Latitude": "",
                "Google Longitude": "",
                "Google Geocode Status": "Skipped: low address confidence",
                "Address Confidence": "low",
                "Address Source": "not_found",
            },
        ]
    )

    detail_df = build_failure_detail_table(df)
    summary_df = build_failure_summary_table(df)

    assert len(build_export_table(df)) == 1
    assert list(detail_df["Project ID"]) == ["1002", "1003", "1004"]
    assert list(detail_df["Failure Stage"]) == [
        "Step 2 Archive",
        "Step 3 Text Extraction",
        "Step 5 Geocode",
    ]
    assert summary_df.set_index("Failure Stage").loc["Step 2 Archive", "Project Count"] == 1
    assert summary_df.set_index("Failure Stage").loc["Step 3 Text Extraction", "Project Count"] == 1
    assert summary_df.set_index("Failure Stage").loc["Step 5 Geocode", "Project Count"] == 1


def test_export_table_keeps_all_valid_project_columns():
    df = pd.DataFrame(
        [
            {
                "Project ID": "1001",
                "Original Project Display Name": "1001 Good Project",
                "Google Latitude": -31.95,
                "Google Longitude": 115.86,
                "Source Project Folder": r"X:\\1000 - 3999\\1001 Good Project",
                "Archived Files": r"D:\\archive\\1001\\report.pdf",
                "Final Project Address": "Bunbury WA, Australia",
            },
            {
                "Project ID": "1002",
                "Original Project Display Name": "1002 No Coordinates",
                "Google Latitude": "",
                "Google Longitude": "",
                "Source Project Folder": r"X:\\1000 - 3999\\1002 No Coordinates",
                "Archived Files": r"D:\\archive\\1002\\report.pdf",
                "Final Project Address": "",
            },
        ]
    )

    export_df = build_export_table(df)

    assert len(export_df) == 1
    assert export_df.loc[0, "Project ID"] == "1001"
    assert export_df.loc[0, "Source Project Folder"] == r"X:\\1000 - 3999\\1001 Good Project"
    assert export_df.loc[0, "Archived Files"] == r"D:\\archive\\1001\\report.pdf"
    assert export_df.loc[0, "Final Project Address"] == "Bunbury WA, Australia"
    assert export_df.loc[0, "Google Latitude"] == -31.95


def test_all_projects_table_keeps_every_row_with_export_status_and_failure_reason():
    df = pd.DataFrame(
        [
            {
                "Project ID": "1001",
                "Status": "Success",
                "Extraction Status": "Success",
                "Original Project Display Name": "1001 Good Project",
                "Google Latitude": -31.95,
                "Google Longitude": 115.86,
                "Source Project Folder": r"X:\\1000 - 3999\\1001 Good Project",
                "Archived Files": r"D:\\archive\\1001\\report.pdf",
                "Final Project Address": "Bunbury WA, Australia",
            },
            {
                "Project ID": "1002",
                "Status": "Success",
                "Extraction Status": "Success",
                "Original Project Display Name": "1002 Low Address",
                "Google Latitude": "",
                "Google Longitude": "",
                "Google Geocode Status": "Skipped: low address confidence",
                "Address Confidence": "low",
                "Address Source": "not_found",
                "Project Address": "Not specified",
                "Source Project Folder": r"X:\\1000 - 3999\\1002 Low Address",
                "Archived Files": r"D:\\archive\\1002\\report.pdf",
            },
        ]
    )

    all_projects_df = build_all_projects_table(df)

    assert len(all_projects_df) == 2
    assert list(all_projects_df["Export Status"]) == ["Exported", "Not Exported"]
    assert all_projects_df.loc[0, "Failure Reason"] == ""
    assert "low address confidence" in all_projects_df.loc[1, "Failure Reason"]
    assert all_projects_df.loc[1, "Source Project Folder"] == r"X:\\1000 - 3999\\1002 Low Address"
