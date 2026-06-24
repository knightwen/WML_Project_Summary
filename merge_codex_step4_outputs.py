"""Merge Codex sidecar Step 4 JSONL outputs into Step 4-shaped files."""

import argparse
from pathlib import Path

from codex_step4_tools import (
    collect_error_log_rows,
    load_jsonl,
    merge_output_rows,
    write_excel,
    write_jsonl,
)


DEFAULT_OUTPUT_DIR = Path("data/processed/codex_step4/merged")


def parse_args():
    parser = argparse.ArgumentParser(description="Merge Codex Step 4 outputs.")
    parser.add_argument("source_jsonl", help="Step 3 text cache JSONL used as source.")
    parser.add_argument("codex_output_jsonl", help="Codex output JSONL lines.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--batch", default="")
    parser.add_argument("--stem", default="")
    return parser.parse_args()


def infer_batch_name(path):
    name = Path(path).stem
    prefix = "3_project_pdf_text_cache_"
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def main():
    args = parse_args()
    source_path = Path(args.source_jsonl)
    output_path = Path(args.codex_output_jsonl)
    batch = args.batch or infer_batch_name(source_path)
    stem = args.stem or f"4_project_analysis_results_codex_{batch}"

    source_records = load_jsonl(source_path)
    output_lines = output_path.read_text(encoding="utf-8").splitlines()
    rows = merge_output_rows(source_records, output_lines, source_batch=batch)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{stem}.jsonl"
    xlsx_path = output_dir / f"{stem}.xlsx"
    log_path = output_dir / f"{stem}_errors.jsonl"

    write_jsonl(rows, jsonl_path)
    write_excel(rows, xlsx_path)
    error_rows = collect_error_log_rows(rows)
    write_jsonl(error_rows, log_path)

    success_count = sum(1 for row in rows if row.get("Status") == "Success")
    review_count = sum(1 for row in rows if row.get("Status") == "AI Review Needed")
    print(f"Merged {len(rows)} rows")
    print(f"  Success: {success_count}")
    print(f"  AI Review Needed: {review_count}")
    print(f"  JSONL: {jsonl_path}")
    print(f"  Excel: {xlsx_path}")
    print(f"  Error log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
