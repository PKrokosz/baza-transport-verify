#!/usr/bin/env python3
"""
smtp_verifier.py — Async email verification (MX + SMTP).
FIX: GitHub Actions blocks port 25. Use port 587 (STARTTLS) fallback.
"""
import asyncio
import logging
import sys
from typing import Dict, Optional

import aiodns
import aiosmtplib
from email.utils import parseaddr

CONCURRENT_SMTP = 100
DNS_TIMEOUT = 5.0
SMTP_TIMEOUT = 10.0
SMTP_FROM = "verify@spedition-check.de"

log = logging.getLogger(__name__)


async def smtp_check(resolver: aiodns.DNSResolver, email: str) -> tuple:
    domain = email.split("@", 1)[1].lower()

    # Step 1: MX record lookup
    try:
        mx_records = await resolver.query(domain, "MX")
        if not mx_records:
            return "invalid", "No MX records"
        mx_host = str(sorted(mx_records, key=lambda r: r.priority)[0].host)
    except Exception as exc:
        return "unknown", f"DNS error: {exc}"

    # Step 2: Try SMTP on port 587 (STARTTLS, not blocked by GitHub Actions)
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
        # Other SMTP errors - email might still be valid, MX exists
        return "mx_valid", f"SMTP error {exc.code}: {exc.message}"
    except Exception as exc:
        # Can't connect via SMTP but MX exists - likely valid domain
        return "mx_valid", f"MX exists ({mx_host}), SMTP unreachable: {type(exc).__name__}"
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

    # Deduplicate emails
    seen = set()
    unique_targets = []
    for row in targets:
        email = row["email"].strip().lower()
        if email not in seen:
            seen.add(email)
            unique_targets.append(row)

    print(f"[INFO] {len(unique_targets)} unique emails to verify (from {len(targets)} total)")

    resolver = aiodns.DNSResolver()
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_TIMEOUT

    sem = asyncio.Semaphore(CONCURRENT_SMTP)
    tasks = [verify_one(sem, resolver, row) for row in unique_targets]
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
