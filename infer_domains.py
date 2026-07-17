"""
Domain Inference + MX Check for firms without email/website.
Generates domain candidates from company names, checks MX records,
produces pattern emails for valid domains.

Runs locally or on GitHub Actions (DNS only, no port 25 needed).
Output feeds into GitHub Actions SMTP verification.
"""
import csv
import os
import re
import sys
import time
import string
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import dns.resolver
except ImportError:
    print("pip install dnspython")
    sys.exit(1)

BASE = os.environ.get("WORKSPACE_DIR", os.path.dirname(os.path.abspath(__file__)))
INPUT = os.path.join(BASE, os.environ.get("INPUT_CSV", "BAZA_FINAL_v1.csv"))
OUTPUT = os.path.join(BASE, os.environ.get("OUTPUT_CSV", "inferred_emails_for_smtp.csv"))

LEGAL_FORMS = [
    "gmbh & co. kg", "gmbh & co kg", "gmbh", "gesellschaft mit beschränkter haftung",
    "aktiengesellschaft", "ag & co. kg", "ag & co kg", "ag",
    "e.k.", "e.kfr.", "e. k.", "e. kfr.", "eingetragener kaufmann",
    "ohg", "offene handelsgesellschaft", "kg", "kommanditgesellschaft",
    "gbr", "gesellschaft bürgerlichen rechts",
    "ug", "unternehmergesellschaft", "haftungsbeschränkt",
    "partg", "partnerschaftsgesellschaft", "mbb",
    "ltd", "limited", "llc", "inc", "incorporated",
    "sarl", "sas", "bv", "nv", "sp. z o.o.", "s.r.o.",
    "& co.", "& co", "co.", "und co.", "u. co.",
]

ROLE_PREFIXES = [
    "info", "kontakt", "office", "service", "vertrieb", "support",
    "mail", "post", "anfrage", "buchhaltung", "verkauf", "sales",
    "logistik", "spedition", "transport", "disposition", "dispatch",
]

TRANSPORT_SUFFIXES = [
    "-spedition", "-transport", "-logistik", "-transporte", "-logistic",
    "-transportlogistik", "-speditionen",
]

CACHE_FILE = os.path.join(BASE, "mx_inference_cache.csv")

MX_CACHE = {}
MX_CACHE_LOCK = threading.Lock()
STATS = {"checked": 0, "found": 0, "domains": 0, "emails": 0}
STATS_LOCK = threading.Lock()


def clean_name(name):
    if not name:
        return ""
    n = name.lower().strip()
    for lf in sorted(LEGAL_FORMS, key=len, reverse=True):
        idx = n.find(lf)
        if idx > 0:
            n = n[:idx].strip()
        elif idx == 0:
            n = n[len(lf):].strip()
    n = n.strip().rstrip(",").rstrip(".").rstrip("&").strip()
    rep = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", " - ": "-", " – ": "-"}
    for k, v in rep.items():
        n = n.replace(k, v)
    n = re.sub(r"['`\"]", "", n)
    n = re.sub(r"[^a-z0-9&\s\-]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    if len(n) < 3:
        return ""
    return n


def generate_domains(clean):
    """Generate 3-5 domain candidates from a cleaned company name."""
    domains = []
    words = [w for w in clean.split() if len(w) > 1]

    if not words:
        return []

    if len(words) == 1:
        w = words[0]
        domains.append(f"{w}.de")
        domains.append(f"{w}-spedition.de")
        domains.append(f"{w}-transport.de")

    elif len(words) == 2:
        a, b = words
        domains.append(f"{a}-{b}.de")
        domains.append(f"{a}{b}.de")
        domains.append(f"{a}-{b}-spedition.de")
        domains.append(f"{a}-{b}-transport.de")

    else:
        a, b, c = words[0], words[1], words[-1]
        domains.append(f"{a}-{b}.de")
        domains.append(f"{a}{b}.de")
        domains.append(f"{a}-{b}-{c}.de")
        domains.append(f"{a}-{b}-spedition.de")
        domains.append(f"{a}{b}{c}.de")

    return [d for d in domains if 5 < len(d) < 64]


def load_mx_cache():
    global MX_CACHE
    if not os.path.exists(CACHE_FILE):
        return
    with open(CACHE_FILE, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            domain = row.get("domain", "").strip()
            has_mx = row.get("has_mx", "").strip()
            if domain:
                MX_CACHE[domain] = has_mx == "yes"


def save_mx_cache(domain, has_mx):
    with MX_CACHE_LOCK:
        MX_CACHE[domain] = has_mx


def check_mx(domain):
    with MX_CACHE_LOCK:
        if domain in MX_CACHE:
            return MX_CACHE[domain]

    for attempt in range(2):
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=8)
            if answers:
                result = True
                save_mx_cache(domain, True)
                return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            save_mx_cache(domain, False)
            return False
        except dns.resolver.Timeout:
            if attempt == 1:
                save_mx_cache(domain, False)
                return False
            time.sleep(0.5)
        except Exception:
            save_mx_cache(domain, False)
            return False

    save_mx_cache(domain, False)
    return False


def flush_cache():
    with open(CACHE_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "has_mx"])
        w.writeheader()
        with MX_CACHE_LOCK:
            for domain, has_mx in MX_CACHE.items():
                w.writerow({"domain": domain, "has_mx": "yes" if has_mx else "no"})


def process_firm(row, idx, total):
    name = row.get("company_name", "")
    clean = clean_name(name)
    if not clean:
        return None

    candidates = generate_domains(clean)
    if not candidates:
        return None

    results = []
    for domain in candidates:
        if check_mx(domain):
            for role in ROLE_PREFIXES:
                results.append({
                    "company_name": name,
                    "clean_name": clean,
                    "domain": domain,
                    "email": f"{role}@{domain}",
                    "city": row.get("city", ""),
                    "phone": row.get("phone", ""),
                })

    with STATS_LOCK:
        STATS["checked"] += 1
        if results:
            STATS["found"] += 1
            STATS["domains"] += len(set(r["domain"] for r in results))
            STATS["emails"] += len(results)

    if idx % 500 == 0:
        with STATS_LOCK:
            pct = (STATS["checked"] / total * 100) if total else 0
            print(f"  [{idx}/{total}] {pct:.1f}% | checked={STATS['checked']} domains={STATS['domains']} emails={STATS['emails']} cache={len(MX_CACHE)}", flush=True)
        flush_cache()

    return results


def main():
    print(f"Input:  {INPUT}", flush=True)
    print(f"Output: {OUTPUT}", flush=True)
    print(flush=True)

    print("Loading firms without email + website...", flush=True)
    firms = []
    with open(INPUT, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not (row.get("email") or "").strip() and not (row.get("website") or "").strip():
                firms.append(row)

    total = len(firms)
    print(f"  {total} firms to process", flush=True)
    print(f"  Generating {len(ROLE_PREFIXES)} patterns per domain", flush=True)
    print(flush=True)

    load_mx_cache()
    print(f"  Loaded {len(MX_CACHE)} cached MX records", flush=True)
    print(flush=True)

    all_results = []
    workers = 25
    print(f"Starting with {workers} workers...", flush=True)
    print(flush=True)

    start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, firm in enumerate(firms):
            future = executor.submit(process_firm, firm, i, total)
            futures[future] = i

        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    all_results.extend(result)
            except Exception as e:
                pass

    flush_cache()
    elapsed = time.time() - start

    print(flush=True)
    print(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)", flush=True)
    print(f"  Firms checked: {STATS['checked']}/{total}", flush=True)
    print(f"  Domains with MX found: {STATS['domains']}", flush=True)
    print(f"  Pattern emails generated: {STATS['emails']}", flush=True)
    print(f"  MX cache size: {len(MX_CACHE)}", flush=True)

    if all_results:
        seen = set()
        unique = []
        for r in all_results:
            e = r["email"]
            if e not in seen:
                seen.add(e)
                unique.append(r)

        print(f"  Unique emails (deduped): {len(unique)}", flush=True)

        with open(OUTPUT, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["company_name", "clean_name", "domain", "email", "city", "phone"])
            w.writeheader()
            w.writerows(unique)

        print(f"  Output: {OUTPUT}", flush=True)

        domain_count = defaultdict(int)
        for r in unique:
            domain_count[r["domain"]] += 1
        print(f"  Unique domains with MX: {len(domain_count)}", flush=True)
        print(f"  Top 10 domains (most emails):", flush=True)
        for d, c in sorted(domain_count.items(), key=lambda x: -x[1])[:10]:
            print(f"    {d}: {c}", flush=True)
    else:
        print("  No results generated!", flush=True)


if __name__ == "__main__":
    main()
