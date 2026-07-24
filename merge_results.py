#!/usr/bin/env python3
"""
merge_results.py — Merge scraped & verified data back into source CSV.
FIXES: deduplication before merge to prevent Cartesian products.
"""
import pandas as pd
import sys


def main(original_path: str, scraped_path: str, smtp_path: str, output_path: str) -> None:
    orig = pd.read_csv(original_path, dtype=str).fillna("")
    scraped = pd.read_csv(scraped_path, dtype=str).fillna("")
    smtp = pd.read_csv(smtp_path, dtype=str).fillna("")

    # 1. Deduplicate scraped emails (keep first non-empty per nip)
    scraped_dedup = scraped[scraped["scraped_email"].str.strip() != ""].drop_duplicates(
        subset=["nip"], keep="first"
    )
    scraped_map = scraped_dedup.set_index("nip")["scraped_email"]

    def fill_email(row):
        if not row["email"] and row["nip"] in scraped_map.index:
            val = scraped_map[row["nip"]]
            if val and val.strip():
                return val
        return row["email"]

    orig["email"] = orig.apply(fill_email, axis=1)

    # 2. Deduplicate SMTP results (keep first per nip)
    smtp_dedup = smtp.drop_duplicates(subset=["nip"], keep="first")
    smtp_map = smtp_dedup.set_index("nip")[["smtp_status", "smtp_message"]]

    def get_status(row):
        nip = row["nip"]
        if nip in smtp_map.index:
            return pd.Series(smtp_map.loc[nip])
        return pd.Series({"smtp_status": "not_checked", "smtp_message": ""})

    status_df = orig.apply(get_status, axis=1)
    orig["email_status"] = status_df["smtp_status"]
    orig["email_smtp_msg"] = status_df["smtp_message"]

    # 3. Column ordering
    cols = [c for c in orig.columns if c not in ["email_status", "email_smtp_msg"]]
    cols += ["email_status", "email_smtp_msg"]
    orig = orig[cols]

    orig.to_csv(output_path, index=False)
    print(f"[INFO] Merged file written -> {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python merge_results.py <original> <scraped> <smtp> <output>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
