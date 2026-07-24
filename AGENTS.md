# AGENTS.md — Kontekst projektu: Baza Firm Transportowych DE

## Cel projektu
Baza danych firm transportowych/spedycyjnych w Niemczech z potwierdzonymi adresami email
do celów outreachowych (marketing, sprzedaż, nawiązywanie kontaktów).

## Stan aktualny (2026-07-24)

### Główne statystyki
| Metryka | Wartość |
|---|---|
| Firm łącznie | 79,447 |
| Z adresem email | 16,723 (21%) |
| smtp_verified (potwierdzone SMTP) | 2,105 |
| catch_all (domena akceptuje każdy) | 1,337 |
| smtp_rejected (serwer odrzucił) | 5,094 |
| smtp_unreachable (serwer nie odpowiada) | 7,499 |
| Bez żadnego emaila | 62,666 (79%) |

### Kategorie w BAZA_STATUS_ASANA.xlsx
| Arkusz | Wierszy | Opis |
|---|---|---|
| 1-POTWIERDZONE | 3,290 | Email technicznie zweryfikowany (SMTP) |
| 2-PRAWDOPODOBNE | 152 | Potwierdzone ze źródeł, brak pełnej weryfikacji |
| 3-DO WERYFIKACJI | 76,005 | Brak emaila lub serwer nie odpowiada |
| 3b-PATTERNS-T2 | 15,195 | Domeny z wygenerowanymi kandydatami na email |
| 3c-MX-DOMAINY | 5,363 | Domeny z rekordami MX (odkryte przez inferencję) |

## Struktura danych

### BAZA_FINAL_v1.csv (główna baza)
18 kolumn: `company_name, email, website, phone, street, postcode, city, country,
founding_year, employee_count, distribution_area, description, source, source_detail,
verify_status, verify_code, verify_method, catch_all`

- Emaili wiele w jednym wierszu, rozdzielone średnikiem
- `verify_method`: smtp_verified / catch_all / smtp_rejected / smtp_unreachable / no_email
- `verify_status`: valid / partial_valid / all_invalid / unknown / missing

### verified_inferred_emails.csv (_verify_check/)
235,365 wierszy — zinferowane emaile dla 11,093 firm.
Kolumny: company_name, clean_name, domain, email, city, phone, verify_status, verify_code, verify_method, catch_all

### mx_inference_cache.csv
82,102 domen — wyniki lookup MX. 5,363 z MX=yes.

### pattern_emails_t2.csv
15,195 wzorcowych emaili (info@, kontakt@, office@ itd.) dla domen z MX.

## Pipeline na GitHub Actions

### Workflowy (repo: PKrokosz/baza-transport-verify)
1. **Infer + Verify Pipeline** — inferencja domen → split → 6x SMTP verify → merge
2. **Scrape + Verify Round 2** — scrape stron → split → 4x SMTP verify → merge
3. **Verify Emails (SMTP)** — weryfikacja pojedynczego pliku
4. **Re-Verify Unknown** — powtórna weryfikacja z 10 workerami

### Kluczowe skrypty
| Plik | Cel |
|---|---|
| `infer_domains.py` | Inferencja domen z nazw firm (DNS/MX) |
| `verify_smtp_bulk.py` | Weryfikacja SMTP (port 25, catch-all detection) |
| `split_shards.py` | Dzielenie CSV na N shardów |
| `merge_shards.py` | Scalanie zweryfikowanych shardów |
| `pattern_mx_t2.py` | Generowanie wzorcowych emaili dla domen |
| `scrape_websites_round2.py` | Scraping stron www dla brakujących emaili |
| `merge_scraped_round2.py` | Merge wyników scrapingu do bazy |
| `merge_inferred_to_baza.py` | Merge zinferowanych wyników do BAZA_FINAL |
| `rebuild_asana_excel.py` | Odbudowa BAZA_STATUS_ASANA.xlsx |

## Wyniki weryfikacji SMTP — co oznaczają

- **smtp_verified** — serwer potwierdził istnienie skrzynki (najlepszy wynik)
- **catch_all** — domena akceptuje każdy email (nie potwierdza istnienia konkretnej skrzynki)
- **smtp_rejected** — serwer odrzucił adres (skrzynka nie istnieje)
- **smtp_unreachable** — serwer nie odpowiada (timeout, firewall, blokada)
- **no_email** — firma nie ma żadnego emaila w bazie

### Uwaga o catch_all
Domeny catch-all akceptują każdy RCPT TO, więc nie da się potwierdzić
konkretnej skrzynki. Mimo to, emaile na takich domenach często docierają.

## Co zostało zrobione
1. ✅ Inferencja domen z nazw firm (13,845 domen, 235k emaili)
2. ✅ Weryfikacja SMTP na GitHub Actions (6 parallel shards)
3. ✅ Merge wyników do BAZA_FINAL_v1.csv
4. ✅ Odbudowa BAZA_STATUS_ASANA.xlsx z nowymi kategoriami
5. ✅ Scraping stron www (Round 2) — 233 strony, **0 emaili** (problem!)

## Co NIE działa
- **Scraper stron www** — znajduje 0 emaili z 233 stron. Możliwe przyczyny:
  - Strony blokują scrapery (Cloudflare, CAPTCHA)
  - Emaile w JavaScript (nie w HTML)
  - Zbyt agresywne filtry w `is_valid_email()`
  - Strony zwracają pustą treść

## Co dalej (priorytety)
1. **Naprawić scraper** lub użyć Apify/web search do znajdowania emaili
2. **Przetworzyć 62,666 firm bez emaila** — to główna praca
3. **Powtórzyć weryfikację smtp_unreachable** (7,499 firm) — serwery mogły być tymczasowo zablokowane
4. **Znaleźć alternatywne emaile** dla 5,094 firm z smtp_rejected
5. **Zweryfikować pattern T2** (15,195 emaili) i MX domainy (5,363 domen)

## Lokalne pliki (nie w git)
- `BAZA_STATUS_ASANA.xlsx` — raport statusu z arkuszami
- `_verify_check/verified_inferred_emails.csv` — wyniki weryfikacji inferencji

## GitHub
- Repo: `PKrokosz/baza-transport-verify`
- Branch: `master`
- Ostatni commit: `abb7c57` (Fix: add missing os import)
