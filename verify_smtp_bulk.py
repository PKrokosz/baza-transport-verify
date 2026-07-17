#!/usr/bin/env python3
"""
Bulk SMTP Email Verifier for GitHub Actions.
Verifies emails via MX lookup + SMTP RCPT TO check on port 25.
Detects catch-all domains. Designed for GitHub Actions runners (port 25 open).

Usage: python verify_smtp_bulk.py input.csv output.csv [--max-workers 20]
"""

import csv
import sys
import os
import time
import random
import string
import socket
import smtplib
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import dns.resolver
except ImportError:
    print("ERROR: pip install dnspython")
    sys.exit(1)

MX_CACHE: dict[str, list[str]] = {}
MX_CACHE_LOCK = threading.Lock()
DOMAIN_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
DOMAIN_LOCKS_LOCK = threading.Lock()

STATS: dict[str, int] = defaultdict(int)
STATS_LOCK = threading.Lock()
PROGRESS = {"done": 0, "total": 0}
PROGRESS_LOCK = threading.Lock()

MIN_DELAY = 0.5
FROM_DOMAIN = "verify.lan"


def get_domain_lock(domain: str) -> threading.Lock:
    with DOMAIN_LOCKS_LOCK:
        return DOMAIN_LOCKS[domain]


def get_mx(domain: str) -> list[str]:
    with MX_CACHE_LOCK:
        if domain in MX_CACHE:
            return MX_CACHE[domain]

    for attempt in range(2):
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5)
            result = []
            for r in sorted(answers, key=lambda r: r.preference):
                result.append(str(r.exchange).rstrip("."))
            if result:
                with MX_CACHE_LOCK:
                    MX_CACHE[domain] = result
                return result
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            try:
                a = dns.resolver.resolve(domain, "A", lifetime=4)
                result = [str(aa) for aa in a]
                with MX_CACHE_LOCK:
                    MX_CACHE[domain] = result
                return result
            except Exception:
                with MX_CACHE_LOCK:
                    MX_CACHE[domain] = []
                return []
        except Exception:
            if attempt == 1:
                with MX_CACHE_LOCK:
                    MX_CACHE[domain] = []
                return []
            time.sleep(1)

    with MX_CACHE_LOCK:
        MX_CACHE[domain] = []
    return []


def verify_one(email: str) -> dict[str, str | int | bool]:
    """Returns: {status, code, method, catch_all}"""
    if not email or "@" not in email:
        return {"status": "invalid", "code": -1, "method": "bad_format", "catch_all": False}

    try:
        local, domain = email.lower().rsplit("@", 1)
    except ValueError:
        return {"status": "invalid", "code": -1, "method": "bad_format", "catch_all": False}

    mx_list = get_mx(domain)
    if not mx_list:
        return {"status": "invalid", "code": -1, "method": "no_mx", "catch_all": False}

    lock = get_domain_lock(domain)

    with lock:
        time.sleep(random.uniform(0.1, 0.4))

        for mx_host in mx_list:
            try:
                smtp = smtplib.SMTP(timeout=10)
                smtp.connect(mx_host, 25)
                code, _ = smtp.ehlo()
                if code >= 400:
                    try:
                        smtp.quit()
                    except Exception:
                        pass
                    continue

                code, _ = smtp.mail(f"noreply@{FROM_DOMAIN}")
                if code >= 400:
                    try:
                        smtp.quit()
                    except Exception:
                        pass
                    continue

                code, msg = smtp.rcpt(email)
                try:
                    smtp.quit()
                except Exception:
                    pass

                msg_lower = (msg or b"").decode("utf-8", errors="ignore").lower() if isinstance(msg, bytes) else str(msg or "").lower()

                if code in (250, 251):
                    catch_all = _check_catch_all(mx_host, domain, lock)
                    return {
                        "status": "valid",
                        "code": code,
                        "method": "smtp_catch_all" if catch_all else "smtp_verified",
                        "catch_all": catch_all,
                    }
                elif code >= 500:
                    return {
                        "status": "invalid",
                        "code": code,
                        "method": "smtp_rejected",
                        "catch_all": False,
                    }
                else:
                    continue

            except (socket.timeout, TimeoutError, ConnectionRefusedError,
                    ConnectionResetError, OSError, smtplib.SMTPException):
                continue

        return {"status": "unknown", "code": -1, "method": "smtp_unreachable", "catch_all": False}


def _check_catch_all(mx_host: str, domain: str, lock: threading.Lock) -> bool:
    """Send random RCPT TO to same MX to detect catch-all."""
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
    try:
        time.sleep(0.3)
        smtp2 = smtplib.SMTP(timeout=8)
        smtp2.connect(mx_host, 25)
        smtp2.ehlo()
        smtp2.mail(f"noreply@{FROM_DOMAIN}")
        rc, _ = smtp2.rcpt(f"{rand}@{domain}")
        smtp2.quit()
        return rc in (250, 251)
    except Exception:
        return False


def worker(job: dict) -> dict:
    email = job["email"]
    result = verify_one(email)
    with STATS_LOCK:
        STATS[result["status"]] += 1
    with PROGRESS_LOCK:
        PROGRESS["done"] += 1
        done = PROGRESS["done"]
        total = PROGRESS["total"]
    if done % 200 == 0 or done == total:
        elapsed = job.get("_start_time", time.time())
        rate = done / max(time.time() - elapsed, 0.01)
        print(
            f"  [{done}/{total}] {rate:.1f}/s | "
            f"ok={STATS.get('valid',0)} bad={STATS.get('invalid',0)} "
            f"unk={STATS.get('unknown',0)} catch={STATS.get('catch_all_cnt',0)}",
            flush=True,
        )
    return {"email": email, **result}


def main():
    input_csv = sys.argv[1] if len(sys.argv) > 1 else "transport_final_merged_v12.csv"
    output_csv = sys.argv[2] if len(sys.argv) > 2 else "verified_results.csv"
    max_workers = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    if not os.path.exists(input_csv):
        print(f"ERROR: {input_csv} not found in {os.getcwd()}")
        print(f"Files: {os.listdir('.')[:20]}")
        sys.exit(1)

    print(f"Input:  {input_csv}")
    print(f"Output: {output_csv}")
    print(f"Workers: {max_workers}")
    print()

    rows = []
    with open(input_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        in_fields = list(reader.fieldnames or [])
        for row in reader:
            rows.append(dict(row))

    print(f"Read {len(rows)} rows from CSV")
    print(f"Fields: {in_fields}")

    email_field = next((k for k in in_fields if k.lower() == "email"), "email")
    if email_field not in in_fields:
        print(f"WARN: 'email' field not found in {in_fields}, using first match")
        email_field = in_fields[0] if in_fields else "email"

    unique_emails: dict[str, str] = {}
    rows_with_email = 0
    for row in rows:
        raw = (row.get(email_field) or "").strip()
        if not raw:
            continue
        rows_with_email += 1
        for part in raw.split(";"):
            e = part.strip().lower()
            if e and "@" in e:
                unique_emails[e] = e

    print(f"Rows with email field: {rows_with_email}")
    print(f"Unique emails to verify: {len(unique_emails)}")
    print()

    domains = list(set(e.rsplit("@", 1)[-1] for e in unique_emails))
    print(f"Unique domains: {len(domains)}")
    print(f"Prefilling MX cache for {len(domains)} domains...")
    dns_ok = 0
    for d in domains:
        mx = get_mx(d)
        if mx:
            dns_ok += 1
    print(f"Domains with MX: {dns_ok}/{len(domains)}")
    print()
    print("Starting verification...")
    print()

    start_time = time.time()
    PROGRESS["total"] = len(unique_emails)

    verification_results: dict[str, dict] = {}

    jobs = [{"email": e, "_start_time": start_time} for e in sorted(unique_emails)]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, job): job["email"] for job in jobs}
        for future in as_completed(futures):
            try:
                result = future.result()
                verification_results[result["email"]] = result
            except Exception as exc:
                email = futures[future]
                verification_results[email] = {
                    "email": email, "status": "error", "code": -1,
                    "method": f"exception:{exc}", "catch_all": False,
                }

    elapsed = time.time() - start_time
    print()
    print(f"Done in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Rate: {len(unique_emails)/elapsed:.1f} emails/s")
    print()

    out_fields = list(in_fields) + ["verify_status", "verify_code", "verify_method", "catch_all"]
    for col in out_fields:
        if col not in in_fields and col not in out_fields:
            out_fields.append(col)

    written = 0
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            raw_emails = (row.get(email_field) or "").strip()
            if not raw_emails:
                row["verify_status"] = "missing"
                row["verify_code"] = ""
                row["verify_method"] = "no_email"
                row["catch_all"] = "false"
                writer.writerow(row)
                written += 1
                continue

            parts = [p.strip() for p in raw_emails.split(";")]
            verified_parts = []
            all_valid = True
            has_catch_all = False
            best_method = "no_email"

            for part in parts:
                e = part.lower() if part else ""
                if not e or "@" not in e:
                    continue
                vr = verification_results.get(e)
                if vr is None:
                    verified_parts.append(part)
                    continue
                status = str(vr.get("status", "unknown"))
                if status == "valid":
                    verified_parts.append(part)
                    if vr.get("catch_all"):
                        has_catch_all = True
                    best_method = vr.get("method", best_method)
                elif status == "invalid":
                    all_valid = False
                else:
                    verified_parts.append(part)

            if not verified_parts:
                row["verify_status"] = "all_invalid"
                row["verify_code"] = ""
                row["verify_method"] = "smtp_rejected"
                row["catch_all"] = str(has_catch_all).lower()
            else:
                row["verify_status"] = "valid" if all_valid else "partial_valid"
                row["verify_code"] = ""
                row["verify_method"] = f"catch_all" if has_catch_all else str(best_method)
                row["catch_all"] = str(has_catch_all).lower()
                if verified_parts:
                    row[email_field] = "; ".join(verified_parts)

            row["verify_status"] = str(row.get("verify_status", ""))
            row["verify_code"] = str(row.get("verify_code", ""))
            row["verify_method"] = str(row.get("verify_method", ""))
            row["catch_all"] = str(row.get("catch_all", "false"))

            writer.writerow(row)
            written += 1

    final_stats = dict(STATS)
    print(f"Output rows: {written} -> {output_csv}")
    print(f"Stats: {final_stats}")
    print(f"\nFULLY DONE.")


if __name__ == "__main__":
    main()
