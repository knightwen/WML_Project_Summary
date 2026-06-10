import pandas as pd

from step6_export_google_earth import (
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
