from pathlib import Path

from step2_archive_project_files import (
    apply_existing_archive_files,
    build_archive_record,
    build_selection_reasons,
    find_existing_archive_files,
    find_scoring_fallback_files,
    score_fallback_file,
    should_exclude_low_value_file,
)


def test_low_value_admin_files_are_excluded_from_fallback(tmp_path):
    project_folder = tmp_path / "7589 Sample Project"
    proposal_folder = project_folder / "Financial Management" / "Proposal"
    proposal_folder.mkdir(parents=True)

    proposal = proposal_folder / "7589 WML Fee Proposal.pdf"
    invoice = proposal_folder / "7589 invoice.pdf"
    proposal.write_text("proposal", encoding="utf-8")
    invoice.write_text("invoice", encoding="utf-8")

    selected = find_scoring_fallback_files(project_folder, "7589")

    assert selected == [proposal]
    assert should_exclude_low_value_file(invoice) == (True, "invoice/admin")


def test_fallback_scoring_returns_reasons_for_high_value_file(tmp_path):
    file_path = (
        tmp_path
        / "7589 Sample Project"
        / "Engineering"
        / "Reporting"
        / "7589 Structural Inspection Report.pdf"
    )
    file_path.parent.mkdir(parents=True)
    file_path.write_text("report", encoding="utf-8")

    score, reasons = score_fallback_file(file_path, "7589")

    assert score >= 200
    assert "project id in file name" in reasons
    assert "engineering reporting path" in reasons
    assert "report keyword" in reasons


def test_archive_record_can_store_selection_reasons():
    record = build_archive_record(
        {
            "Project Display Name": "7589 Sample Project",
            "Project Display Name Raw": "7589: Sample Project",
            "Project Display Name No ID": "Sample Project",
            "Client": "Client A",
            "Manager": "Manager A",
            "Start Date": "2020-01-01",
            "Contract Amount": "1000",
            "AddressState": "WA",
            "City": "Perth",
        },
        "7589",
    )

    reasons = build_selection_reasons(
        [Path(r"C:\Projects\7589 Sample\Proposal\7589 WML Fee Proposal.pdf")],
        "7589",
    )
    record["selection_reasons"] = reasons

    assert record["selection_reasons"] == [
        "7589 WML Fee Proposal.pdf: project id in file name; proposal path; fee proposal; proposal keyword; supported PDF"
    ]


def test_existing_local_archive_files_can_populate_archive_record(tmp_path):
    archive_root = tmp_path / "local_archive"
    project_archive = archive_root / "7589"
    project_archive.mkdir(parents=True)
    manual_file = project_archive / "7589 Manual Structural Advice.pdf"
    ignored_file = project_archive / "7589 invoice.pdf"
    manual_file.write_text("advice", encoding="utf-8")
    ignored_file.write_text("invoice", encoding="utf-8")

    existing_files = find_existing_archive_files("7589", archive_root)
    record = build_archive_record(
        {
            "Project Display Name": "7589 Sample Project",
            "Client": "Client A",
            "Manager": "Manager A",
        },
        "7589",
    )

    applied = apply_existing_archive_files(record, "7589", existing_files)

    assert applied is True
    assert existing_files == [manual_file]
    assert record["status"] == "Success"
    assert record["source_project_folder"] == str(project_archive)
    assert record["source_files"] == []
    assert record["archived_files"] == [str(manual_file)]
    assert record["selection_reasons"] == [
        "7589 Manual Structural Advice.pdf: existing local archive file; project id in file name; advice keyword; supported PDF"
    ]
