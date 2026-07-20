#!/usr/bin/env python3
"""
Split a CSV with inferred emails into N shards for parallel SMTP verification.
Usage: python split_shards.py input.csv output_prefix N
  -> output_prefix_0.csv, output_prefix_1.csv, ..., output_prefix_(N-1).csv
"""
import csv
import sys
import os


def main():
    if len(sys.argv) < 4:
        print("Usage: python split_shards.py input.csv output_prefix N")
        sys.exit(1)

    input_csv = sys.argv[1]
    out_prefix = sys.argv[2]
    n_shards = int(sys.argv[3])

    if not os.path.exists(input_csv):
        print(f"ERROR: {input_csv} not found")
        sys.exit(1)

    with open(input_csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    total = len(rows)
    shard_size = (total + n_shards - 1) // n_shards
    print(f"Splitting {total} rows into {n_shards} shards of ~{shard_size} each")

    for i in range(n_shards):
        start = i * shard_size
        end = min(start + shard_size, total)
        shard_rows = rows[start:end]
        out_path = f"{out_prefix}_{i}.csv"
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(shard_rows)
        print(f"  shard {i}: {len(shard_rows)} rows -> {out_path}")

    print(f"GITHUB_ENV_SHARD_COUNT={n_shards}")


if __name__ == "__main__":
    main()
