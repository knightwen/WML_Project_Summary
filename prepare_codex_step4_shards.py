"""Prepare sidecar JSONL shards for Codex-assisted Step 4 analysis."""

import argparse
from pathlib import Path

from codex_step4_tools import build_shard_records, load_jsonl, write_jsonl


DEFAULT_OUTPUT_DIR = Path("data/processed/codex_step4/shards")


def infer_batch_name(path):
    name = Path(path).stem
    prefix = "3_project_pdf_text_cache_"
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare Codex Step 4 shard inputs.")
    parser.add_argument("input_jsonl", help="Step 3 text cache JSONL file.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--batch", default="")
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--max-text-chars", type=int, default=12000)
    parser.add_argument("--shard-size", type=int, default=100)
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = Path(args.input_jsonl)
    batch = args.batch or infer_batch_name(input_path)
    records = load_jsonl(input_path)
    shard_records = build_shard_records(
        records,
        source_batch=batch,
        max_records=args.max_records,
        max_text_chars=args.max_text_chars,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not shard_records:
        print(f"No analyzable records found in {input_path}")
        return 0

    shard_size = max(1, args.shard_size)
    written = []
    for shard_index, start in enumerate(range(0, len(shard_records), shard_size), start=1):
        shard = shard_records[start:start + shard_size]
        output_path = output_dir / f"codex_step4_input_{batch}_shard_{shard_index:03d}.jsonl"
        write_jsonl(shard, output_path)
        written.append(output_path)

    print(f"Prepared {len(shard_records)} Codex Step 4 input records from {input_path}")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
