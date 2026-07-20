#!/usr/bin/env python3
"""
Merge N verified shard CSVs into a single results file.
Usage: python merge_shards.py output.csv shard_0.csv shard_1.csv ...
"""
import csv
import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: python merge_shards.py output.csv shard_0.csv shard_1.csv ...")
        sys.exit(1)

    output_csv = sys.argv[1]
    shard_paths = sys.argv[2:]

    fields = None
    merged = 0
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as out:
        for i, path in enumerate(shard_paths):
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if fields is None:
                    fields = reader.fieldnames
                    writer = csv.DictWriter(out, fieldnames=fields)
                    writer.writeheader()
                writer.writerows(reader)
                count = sum(1 for _ in open(path, encoding="utf-8-sig")) - 1
                merged += count
                print(f"  {path}: {count} rows")

    print(f"Merged {merged} rows -> {output_csv}")


if __name__ == "__main__":
    main()
