#!/usr/bin/env python3
"""
main.py — Orchestrator for the full email verification pipeline.
FIXES: Parallel scraper+SMTP execution via ThreadPoolExecutor.
"""
import os
import sys
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scraper import scrape_all
from smtp_verifier import verify_all
from merge_results import main as merge_main

ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

ORIGINAL = Path("BAZA_FINAL_v1.csv")
SCRAPED_CSV = ARTIFACTS / "scraped_emails.csv"
SMTP_CSV = ARTIFACTS / "smtp_results.csv"
MERGED_CSV = ARTIFACTS / "BAZA_FINAL_v2.csv"


def run_scraper_sync():
    asyncio.run(scrape_all(str(ORIGINAL), str(SCRAPED_CSV)))


def run_smtp_sync():
    asyncio.run(verify_all(str(ORIGINAL), str(SMTP_CSV)))


def run_merge():
    merge_main(str(ORIGINAL), str(SCRAPED_CSV), str(SMTP_CSV), str(MERGED_CSV))


def main_pipeline():
    print("\n=== PIPELINE: Parallel scraper + SMTP verification ===\n")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline") as executor:
        scraper_future = executor.submit(run_scraper_sync)
        smtp_future = executor.submit(run_smtp_sync)

        # Wait for both to complete
        scraper_future.result()
        print("[INFO] Scraper finished, waiting for SMTP...")
        smtp_future.result()

    print("\n=== STEP 3: Merge results ===")
    run_merge()

    print("\n=== PIPELINE FINISHED ===")
    print(f"Final enriched CSV: {MERGED_CSV}")


if __name__ == "__main__":
    try:
        main_pipeline()
    except Exception as exc:
        print(f"\n[FATAL] Pipeline failed: {exc}", file=sys.stderr)
        sys.exit(1)
