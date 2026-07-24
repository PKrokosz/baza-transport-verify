#!/usr/bin/env python3
"""
smtp_verifier.py — Async email verification (MX + SMTP).
FIX: Use dnspython instead of aiodns (API compatibility).
FIX: Handle multi-email fields (semicolon-separated).
FIX: Port 587 STARTTLS (port 25 blocked on GH Actions).
"""
import asyncio
import logging
import sys
from typing import Dict, Optional, List

import dns.resolver
import aiosmtplib

CONCURRENT_SMTP = 100
DNS_TIMEOUT = 5.0
SMTP_TIMEOUT = 8.0
SMTP_FROM = "verify@spedition-check.de"

log = logging.getLogger(__name__)


def split_emails(raw: str) -> List[str]:
    """Split multi-email field into individual valid emails."""
    if not raw or not raw.strip():
        return []
    parts = [e.strip().lower() for e in raw.replace(";", ",").split(",")]
    return [e for e in parts if "@" in e and "." in e.split("@")[1] and len(e) < 254]


def get_mx_host(domain: str) -> Optional[str]:
    """Get MX host for domain using dnspython."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT)
        if not answers:
            return None
        mx_records = sorted(answers, key=lambda r: r.preference)
        return str(mx_records[0].exchange).rstrip(".")
    except Exception as exc:
        log.debug(f"DNS MX lookup failed for {domain}: {exc}")
        return None


async def smtp_check(email: str) -> tuple:
    domain = email.split("@", 1)[1].lower()

    # Step 1: MX record lookup (async wrapper — dns.resolver is sync/blocking)
    mx_host = await asyncio.to_thread(get_mx_host, domain)
    if not mx_host:
        return "invalid", f"No MX records for {domain}"

    # Step 2: Try SMTP on port 587 (STARTTLS)
    smtp = None
    try:
        smtp = aiosmtplib.SMTP(hostname=mx_host, port=587, timeout=SMTP_TIMEOUT, use_tls=False)
        await smtp.connect()
        await smtp.starttls()
        await smtp.helo(hostname="spedition-check.de")
        await smtp.mail(SMTP_FROM)
        code, msg = await smtp.rcpt(email)
        msg_str = msg.decode() if isinstance(msg, bytes) else str(msg)
        if 200 <= code < 300:
            return "valid", f"SMTP {code} {msg_str}"
        else:
            return "invalid", f"SMTP {code} {msg_str}"
    except aiosmtplib.SMTPResponseException as exc:
        if exc.code == 550:
            return "invalid", f"SMTP {exc.code} {exc.message}"
        return "mx_valid", f"SMTP error {exc.code}: {exc.message}"
    except Exception as exc:
        return "mx_valid", f"MX OK ({mx_host}), SMTP unreachable: {type(exc).__name__}"
    finally:
        if smtp:
            try:
                await smtp.quit()
            except Exception:
                pass


async def verify_one_email(
    sem: asyncio.Semaphore,
    company: str,
    email: str,
) -> Dict[str, Optional[str]]:
    async with sem:
        status, message = await smtp_check(email)
        return {
            "company_name": company,
            "email": email,
            "smtp_status": status,
            "smtp_message": message,
        }


async def verify_all(csv_path: str, out_path: str) -> None:
    import pandas as pd
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    # Explode multi-email fields into individual rows
    all_tasks = []
    for _, row in df.iterrows():
        company = row.get("company_name", "")
        raw_email = row.get("email", "")
        emails = split_emails(raw_email)
        if emails:
            all_tasks.append({"company_name": company, "email": emails[0]})

    # Deduplicate by email
    seen = set()
    unique_tasks = []
    for t in all_tasks:
        key = t["email"].lower()
        if key not in seen:
            seen.add(key)
            unique_tasks.append(t)

    print(f"[INFO] {len(unique_tasks)} unique emails to verify (from {len(df)} rows)")

    sem = asyncio.Semaphore(CONCURRENT_SMTP)
    tasks = [verify_one_email(sem, t["company_name"], t["email"]) for t in unique_tasks]
    results = await asyncio.gather(*tasks)

    valid = sum(1 for r in results if r["smtp_status"] == "valid")
    mx_valid = sum(1 for r in results if r["smtp_status"] == "mx_valid")
    invalid = sum(1 for r in results if r["smtp_status"] == "invalid")
    unknown = sum(1 for r in results if r["smtp_status"] == "unknown")
    print(f"[INFO] Results: {valid} valid, {mx_valid} mx_valid, {invalid} invalid, {unknown} unknown")

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    print(f"[INFO] Verification finished -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python smtp_verifier.py <input_csv> <output_csv>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(verify_all(sys.argv[1], sys.argv[2]))
