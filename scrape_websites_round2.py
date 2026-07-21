"""
Scrape websites of companies that have a website but no email address.
Reads BAZA_FINAL_v1.csv, outputs scraped_emails_round2.csv.
Designed for GitHub Actions (headless, no display).
"""
import csv
import re
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))

MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
MAX_PAGES_PER_SITE = 5
DELAY = 1.5
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
BAD_SUFFIXES = ('.png', '.jpg', '.gif', '.svg', '.css', '.js', '.ico')
BAD_PREFIXES = ('example', 'test', 'dummy', 'sentry', 'wix', 'shopify', 'noreply', 'no-reply', 'mailer-daemon')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
}

CONTACT_PATHS = [
    '', '/kontakt', '/kontakt/', '/contact', '/contact/',
    '/impressum', '/impressum/', '/imprint', '/imprint/',
    '/about', '/ueber-uns', '/uber-uns', '/impressum-kontakt',
]

session = requests.Session()
session.headers.update(HEADERS)

write_lock = threading.Lock()
results = []


def normalize_url(url):
    url = url.strip()
    if not url:
        return None
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_valid_email(email):
    email = email.lower().strip()
    if len(email) > 80 or len(email) < 5:
        return False
    if any(email.endswith(s) for s in BAD_SUFFIXES):
        return False
    if any(email.startswith(p) for p in BAD_PREFIXES):
        return False
    local = email.split('@')[0]
    if local in ('info', 'kontakt', 'office', 'mail', 'post', 'service', 'support',
                 'vertrieb', 'sales', 'anfrage', 'webmaster', 'admin', 'marketing'):
        return True
    if '.' in local or '-' in local or '_' in local:
        return True
    if re.match(r'^[a-z]+\.[a-z]+$', local) or re.match(r'^[a-z]+-[a-z]+$', local):
        return True
    return False


def extract_emails(html):
    emails = set()
    soup = BeautifulSoup(html, 'lxml')

    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('mailto:'):
            email = href[7:].split('?')[0].strip().lower()
            if '@' in email and is_valid_email(email):
                emails.add(email)

    text = soup.get_text()
    for match in EMAIL_REGEX.finditer(text):
        email = match.group(0).lower()
        if is_valid_email(email):
            emails.add(email)

    for tag in soup.find_all(attrs={"data-email": True}):
        val = tag['data-email'].strip().lower()
        if '@' in val and is_valid_email(val):
            emails.add(val)

    for tag in soup.find_all(attrs={"data-mailto": True}):
        val = tag['data-mailto'].strip().lower()
        if '@' in val and is_valid_email(val):
            emails.add(val)

    return emails


def find_contact_links(html, base_url):
    soup = BeautifulSoup(html, 'lxml')
    urls = set()
    base_domain = urlparse(base_url).netloc

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc != base_domain:
            continue
        path_lower = parsed.path.lower()
        if any(kw in path_lower for kw in ['kontakt', 'contact', 'impressum', 'imprint', 'about', 'firma']):
            urls.add(full)

    return list(urls)[:MAX_PAGES_PER_SITE]


def scrape_site(url):
    base = normalize_url(url)
    if not base:
        return set(), None

    all_emails = set()
    domain = urlparse(base).netloc

    try:
        resp = session.get(base, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return set(), domain
        all_emails.update(extract_emails(resp.text))
        contact_links = find_contact_links(resp.text, base)
    except Exception:
        return set(), domain

    for link in contact_links:
        try:
            time.sleep(DELAY)
            resp = session.get(link, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200:
                all_emails.update(extract_emails(resp.text))
        except Exception:
            continue

    return all_emails, domain


def worker(args):
    company, website = args
    emails, domain = scrape_site(website)
    if emails:
        with write_lock:
            results.append({
                'company_name': company,
                'website': website,
                'domain': domain or '',
                'emails': '; '.join(sorted(emails)),
                'email_count': len(emails),
                'source': 'scraped_round2'
            })
    return len(emails)


def main():
    print("=== Scrape websites for missing emails (Round 2) ===\n")

    baza_path = os.path.join(BASE, 'BAZA_FINAL_v1.csv')
    candidates = []
    seen_domains = set()

    with open(baza_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get('email', '').strip()
            website = row.get('website', '').strip()
            company = row.get('company_name', '').strip()
            if not email and website and company:
                norm = normalize_url(website)
                if norm and norm not in seen_domains:
                    seen_domains.add(norm)
                    candidates.append((company, website))

    print(f"Companies with website but no email: {len(candidates)}")

    if not candidates:
        print("Nothing to scrape.")
        return

    MAX_BATCH = 5000
    if len(candidates) > MAX_BATCH:
        print(f"Limiting to {MAX_BATCH}")
        candidates = candidates[:MAX_BATCH]

    total_found = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(worker, c): c for c in candidates}
        done = 0
        for fut in as_completed(futures):
            total_found += fut.result()
            done += 1
            if done % 100 == 0:
                print(f"  Progress: {done}/{len(candidates)} ({total_found} emails found)")

    print(f"\n=== RESULTS ===")
    print(f"Scraped: {len(candidates)} websites")
    print(f"Emails found: {total_found}")
    print(f"Companies with emails: {len(results)}")

    out_path = os.path.join(BASE, 'scraped_emails_round2.csv')
    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['company_name', 'website', 'domain', 'emails', 'email_count', 'source'])
        writer.writeheader()
        writer.writerows(results)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
