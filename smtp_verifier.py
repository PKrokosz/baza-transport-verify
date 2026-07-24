#!/usr/bin/env python3
"""
smtp_verifier.py — Async SMTP email verification.
FIXES: jitter retry, proper cleanup, rate-limit awareness.
"""
import asyncio
import logging
import sys
from typing import Dict, Optional

import aiodns
import aiosmtplib
from tenacity import (
    retry, stop_after_attempt, wait_exponential_jitter,
    retry_if_exception_type,
)

CONCURRENT_SMTP = 200
DNS_TIMEOUT = 5.0
SMTP_TIMEOUT = 8.0
MAX_SMTP_RETRIES = 2
SMTP_FROM = "verify@spedition-check.de"

log = logging.getLogger(__name__)


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_SMTP_RETRIES),
    wait=wait_exponential_jitter(initial=1, max=5, jitter=2),
    retry=retry_if_exception_type((aiosmtplib.SMTPException, asyncio.TimeoutError)),
)
async def smtp_check(resolver: aiodns.DNSResolver, email: str) -> tuple:
    domain = email.split("@", 1)[1].lower()
    try:
        mx_records = await resolver.query(domain, "MX")
        if not mx_records:
            return "unknown", "No MX records"
        mx_host = str(sorted(mx_records, key=lambda r: r.priority)[0].host)
    except Exception as exc:
        return "unknown", f"DNS error: {exc}"

    smtp = None
    try:
        smtp = aiosmtplib.SMTP(hostname=mx_host, port=25, timeout=SMTP_TIMEOUT)
        await smtp.connect()
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
        raise  # retry on other SMTP errors
    except Exception as exc:
        return "invalid", f"SMTP error: {exc}"
    finally:
        if smtp:
            try:
                await smtp.quit()
            except Exception:
                pass


async def verify_one(
    sem: asyncio.Semaphore,
    resolver: aiodns.DNSResolver,
    row: Dict[str, str],
) -> Dict[str, Optional[str]]:
    async with sem:
        company = row.get("company_name", "")
        email = row.get("email", "").strip()
        if not email:
            return {"company_name": company, "email": "", "smtp_status": "unknown", "smtp_message": "empty"}

        status, message = await smtp_check(resolver, email)
        return {
            "company_name": company,
            "email": email,
            "smtp_status": status,
            "smtp_message": message,
        }


async def verify_all(csv_path: str, out_path: str) -> None:
    import pandas as pd
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    mask = df["email"].notna() & (df["email"].str.strip() != "")
    targets = df[mask].to_dict(orient="records")

    # Deduplicate emails before verifying
    seen = set()
    unique_targets = []
    for row in targets:
        email = row["email"].strip().lower()
        if email not in seen:
            seen.add(email)
            unique_targets.append(row)

    print(f"[INFO] {len(unique_targets)} unique emails to verify via SMTP (from {len(targets)} total)")

    resolver = aiodns.DNSResolver()
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_TIMEOUT

    sem = asyncio.Semaphore(CONCURRENT_SMTP)
    tasks = [verify_one(sem, resolver, row) for row in unique_targets]
    results = await asyncio.gather(*tasks)

    valid = sum(1 for r in results if r["smtp_status"] == "valid")
    invalid = sum(1 for r in results if r["smtp_status"] == "invalid")
    unknown = sum(1 for r in results if r["smtp_status"] == "unknown")
    print(f"[INFO] Results: {valid} valid, {invalid} invalid, {unknown} unknown")

    out_df = pd.DataFrame(results)
    out_df.to_csv(out_path, index=False)
    print(f"[INFO] Verification finished -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python smtp_verifier.py <input_csv> <output_csv>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(verify_all(sys.argv[1], sys.argv[2]))
