"""
Merge scraped_emails_round2.csv back into BAZA_FINAL_v1.csv.
Also generates pattern candidates for newly discovered domains.
"""
import csv
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
BAZA_FILE = os.path.join(BASE, 'BAZA_FINAL_v1.csv')
SCRAPED_FILE = os.path.join(BASE, 'scraped_emails_round2.csv')
PATTERNS_OUT = os.path.join(BASE, 'pattern_candidates_round2.csv')

PATTERNS = [
    'info', 'kontakt', 'office', 'service', 'vertrieb', 'support',
    'mail', 'post', 'sales', 'anfrage', 'buchhaltung', 'spedition',
    'transport', 'logistik', 'disposition', 'verkauf'
]


def main():
    print("=== Merging scraped results into BAZA_FINAL ===\n")

    scraped = {}
    domains_found = set()
    with open(SCRAPED_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = row['company_name'].strip()
            scraped[company] = {
                'emails': row['emails'].strip(),
                'website': row.get('website', '').strip(),
                'domain': row.get('domain', '').strip(),
            }
            if row.get('domain', '').strip():
                domains_found.add(row['domain'].strip())

    print(f"Scraped entries: {len(scraped)}")
    print(f"Unique domains found: {len(domains_found)}")

    updated = 0
    rows = []
    with open(BAZA_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            company = row['company_name'].strip()
            if company in scraped and not row.get('email', '').strip():
                row['email'] = scraped[company]['emails']
                row['verify_status'] = ''
                row['verify_method'] = ''
                row['catch_all'] = ''
                updated += 1
            rows.append(row)

    with open(BAZA_FILE, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} companies in BAZA_FINAL_v1.csv")

    existing_domains = set()
    if os.path.exists(os.path.join(BASE, 'mx_inference_cache.csv')):
        with open(os.path.join(BASE, 'mx_inference_cache.csv'), 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_domains.add(row.get('domain', '').strip())

    new_domains = domains_found - existing_domains
    print(f"\nNew domains for pattern generation: {len(new_domains)}")

    pattern_rows = []
    for domain in sorted(new_domains):
        emails = '; '.join(f'{p}@{domain}' for p in PATTERNS)
        pattern_rows.append({
            'domain': domain,
            'email': f'info@{domain}',
            'pattern': 'info',
            'source': 'scraped_round2',
            'mx_verified': ''
        })

    with open(PATTERNS_OUT, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['domain', 'email', 'pattern', 'source', 'mx_verified'])
        writer.writeheader()
        writer.writerows(pattern_rows)
    print(f"Pattern candidates: {pattern_rows.__len__()} -> {PATTERNS_OUT}")


if __name__ == '__main__':
    main()
