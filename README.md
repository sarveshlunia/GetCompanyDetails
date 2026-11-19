# Indian Company One-Pager Generator (free, no API key)

Type or upload Indian company names -> get a filled Word one-pager per company (your
`One_Pager_Format.docx` layout) + one Excel summary. No paid API, no LLM.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Input: one name per line, or CSV/XLSX/TXT with columns `Company`, `Meetup Person`, `Hint`.
Inline form: `Acme Ltd | Rahul Sharma | ACME.NS`. If the name lookup picks the wrong stock, put the
Yahoo ticker (`XYZ.NS` / `XYZ.BO`) in the hint.

## Where data comes from (all free)
- **Listed companies** - Yahoo Finance (`yfinance`): about, address, website, market cap, 3 yrs revenue & PAT,
  net worth, investments, cash, debt, key executives. Converted to Rs. crore, FY labelled Apr-Mar.
- **Ratings, news, LinkedIn URL, plants, group companies** - DuckDuckGo search snippets (`ddgs`).
  These are *leads*, flagged "verify". LinkedIn is never scraped; only the profile URL/headline from search.

## Blunt limits
- **Unlisted companies get almost nothing** structured. Yahoo has no data on them; the sheet marks gaps and gives
  Tofler / Zauba / MCA lookup links. Free sources simply don't cover them well. That's the real constraint.
- Directors = key executives from Yahoo, not the full board. Products, factories, group companies = search leads only.
- LinkedIn career history needs a login - not retrievable here. You get the profile link + headline if search finds it.
- Yahoo data for Indian small caps can be missing/lagging; DuckDuckGo may rate-limit big batches. Keep batches small.
- Ratings regex-parsed from snippets can be wrong or stale - confirm on CRISIL/ICRA/CARE.

## Upgrades if free isn't enough
Screener.in export / Prowess / Tofler paid API for unlisted financials; Apollo/Lusha for the person.
A free local LLM (Ollama) could summarise "About" from search results - not included.
