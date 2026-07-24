#!/usr/bin/env python3
"""
scraper.py — Async website scraper for missing emails.
FIXES: jitter retry, User-Agent rotation, 429/503 backoff, dedup emails.
"""
import asyncio
import csv
import random
import re
import ssl
import sys
from typing import List, Dict, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from tenacity import (
    retry, stop_after_attempt, wait_exponential_jitter,
    retry_if_exception_type, retry_if_result,
)

CONCURRENT_REQUESTS = 50
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

JUNK_PATTERNS = [
    re.compile(r".*@example\."), re.compile(r".*@domain\."),
    re.compile(r".*@email\."), re.compile(r".*@placeholder\."),
    re.compile(r".*\.(png|jpg|gif|svg|css|js)$"),
    re.compile(r"^test@"), re.compile(r"^noreply@"),
    re.compile(r"^mailer-daemon@"), re.compile(r"^postmaster@"),
]


def is_junk_email(email: str) -> bool:
    email = email.lower().strip()
    if len(email) > 80 or len(email) < 5:
        return True
    for pat in JUNK_PATTERNS:
        if pat.match(email):
            return True
    local = email.split('@')[0]
    if '..' in local or local.startswith('.') or local.endswith('.'):
        return True
    return False


def extract_emails_from_html(html: str) -> List[str]:
    emails = set()
    # Regex on raw HTML
    for m in EMAIL_REGEX.findall(html):
        if not is_junk_email(m):
            emails.add(m.lower())
    # BeautifulSoup pass
    soup = BeautifulSoup(html, "lxml")
    # mailto links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            cand = href.split(":", 1)[1].split("?")[0].strip()
            if EMAIL_REGEX.fullmatch(cand) and not is_junk_email(cand):
                emails.add(cand.lower())
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = script.string
            if data:
                for m in EMAIL_REGEX.findall(data):
                    if not is_junk_email(m):
                        emails.add(m.lower())
        except Exception:
            pass
    # meta tags
    for meta in soup.find_all("meta", attrs={"content": True}):
        content = meta.get("content", "")
        if "@" in content:
            for m in EMAIL_REGEX.findall(content):
                if not is_junk_email(m):
                    emails.add(m.lower())
    # data-* attributes
    for tag in soup.find_all(True, attrs={"data-email": True}):
        val = tag.get("data-email", "")
        if EMAIL_REGEX.fullmatch(val) and not is_junk_email(val):
            emails.add(val.lower())
    return list(emails)


def is_rate_limited(exc: Exception) -> bool:
    """Check if exception indicates rate limiting (429/503)."""
    if hasattr(exc, 'status') and exc.status in (429, 503):
        return True
    if '429' in str(exc) or '503' in str(exc):
        return True
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential_jitter(initial=1, max=10, jitter=3),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }
    try:
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=False) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", "10"))
                log.warning(f"429 rate limited: {url}, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=429,
                    message="Rate limited",
                )
            if resp.status == 503:
                await asyncio.sleep(5)
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=503,
                    message="Service unavailable",
                )
            resp.raise_for_status()
            return await resp.text()
    except aiohttp.ClientSSLError:
        # HSTS site — retry with SSL verification enabled
        log.info(f"SSL error on {url}, retrying with verify=True")
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=True) as resp:
            resp.raise_for_status()
            return await resp.text()


import logging
log = logging.getLogger(__name__)


async def process_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    row: Dict[str, str],
) -> Dict[str, Optional[str]]:
    async with sem:
        nip = row.get("nip", "")
        website = row.get("website", "").strip()
        if not website:
            return {"nip": nip, "website": "", "scraped_email": None}
        if not website.startswith(("http://", "https://")):
            website = "https://" + website

        # German company contact pages
        pages_to_try = [
            "", "/kontakt", "/contact", "/impressum",
            "/ueber-uns", "/uber-uns", "/about", "/das-unternehmen",
            "/kontaktformular", "/anfrage",
        ]
        for page in pages_to_try:
            url = website.rstrip("/") + page
            try:
                html = await fetch(session, url)
                emails = extract_emails_from_html(html)
                if emails:
                    return {"nip": nip, "website": website, "scraped_email": emails[0]}
            except Exception as e:
                if is_rate_limited(e):
                    log.warning(f"Rate limited on {url}, skipping rest of domain")
                    break
                continue

        return {"nip": nip, "website": website, "scraped_email": None}


async def scrape_all(csv_path: str, out_path: str) -> None:
    import pandas as pd
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    mask = (
        df["website"].notna()
        & (df["website"].str.strip() != "")
        & (df["email"].isna() | (df["email"].str.strip() == ""))
    )
    targets = df[mask].to_dict(orient="records")
    print(f"[INFO] {len(targets)} companies to scrape (website + no email)")

    sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
    connector = aiohttp.TCPConnector(limit=0, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [process_one(session, sem, row) for row in targets]
        results = await asyncio.gather(*tasks)

    # Deduplicate emails across results
    seen_emails = set()
    deduped = []
    for r in results:
        email = r.get("scraped_email")
        if email and email in seen_emails:
            r["scraped_email"] = None  # skip duplicate
        elif email:
            seen_emails.add(email)
        deduped.append(r)

    found = sum(1 for r in deduped if r["scraped_email"])
    print(f"[INFO] Found {found} unique emails from {len(deduped)} sites")

    out_df = pd.DataFrame(deduped)
    out_df.to_csv(out_path, index=False)
    print(f"[INFO] Scraping finished -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scraper.py <input_csv> <output_csv>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(scrape_all(sys.argv[1], sys.argv[2]))
