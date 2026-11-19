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

## How it follows your manual process (from the voice note)
| Field | Source, in order |
|---|---|
| About, corporate address | Company website (Yahoo/Zauba only as fallback) |
| Listed/Unlisted, Authorised + Paid-up capital ("market capital") | Zauba Corp |
| Net worth, investments, revenue (3 yrs), PAT, debt | Screener **consolidated** (Yahoo fallback), Rs. crore |
| Cash & equivalents | Yahoo balance sheet |
| Long/short-term rating | Search snippets + Screener "Credit ratings" links -> else ChatGPT prompt |
| Directors | Zauba -> else ChatGPT prompt (website directors not auto-parsed) |
| Products & services, factories | Company website -> else ChatGPT prompt |
| Meetup person: designation, LinkedIn link | Public search result (never scraped) |
| LinkedIn work history (company, from-to) | **Manual** - needs login; ChatGPT prompt provided |

Anything the free sources miss is listed under "Gaps" with a ready-to-paste ChatGPT prompt per company
(your "chart GBD" step), so you only do the manual part for what's missing.

## Screen view
Results show on screen per company (tabs), each value tagged with its source; red "Not found" marks gaps. Use the
"Fill / correct" box to type missing values - they flow into the Word/Excel download. The "Debug log" shows what every
web request returned; send it to the developer when fields come back empty.

## Deeper sources and links
- Company website is crawled two levels deep (About, Investors, Board/Leadership, Subsidiaries, Plants, Annual report).
- Wikipedia + Wikidata supply description, parent company and subsidiaries for notable companies.
- Screener supplies annual-report and rating-document links, plus all financial numbers.
- Every value shows a clickable source link; each company has a "Search it yourself" list of ready-made links
  (Zauba, Tofler, MCA, NSE/BSE, rating agencies, LinkedIn, site-specific searches).

## Numbers: Screener only
Every financial number (revenue, PAT, net worth, debt, investments, cash, market cap, ratios, cash flow, shareholding,
promoter holding) comes from **Screener** only - consolidated, or standalone if that is all Screener has (labelled).
It is shown on screen (ratios, P&L, balance sheet, cash flow, ratios, shareholding, Other Assets/Liabilities/Borrowings
schedules, pros/cons) and exported to Excel ("Screener data (long)" sheet, ready for pivots/Power BI).
No PDFs, Yahoo or search snippets are used for numbers. Unlisted companies have no Screener page, so their numbers are
left blank (type them into "Fill / correct"). Authorised/paid-up capital are registry data and still come from Zauba.
All non-numeric fields (about, address, directors, products, factories, group companies, ratings, news, person) are
gathered from whichever source is relevant.

## Blunt limits
- Zauba is bot-protected and Screener/company sites change HTML; scrapers are best-effort and written against
  fixtures, not verified live. If one is blocked you get a gap + manual link, not a crash.
- Unlisted companies: no free consolidated financials. Expect to fill those by hand (MCA/Tofler/rating rationale).
- Website heuristics (about/products/factories) work on standard sites; JS-heavy sites return nothing.
- LinkedIn history cannot be automated. Ratings from snippets can be stale - verify.

## Upgrades if free isn't enough
Screener.in export / Prowess / Tofler paid API for unlisted financials; Apollo/Lusha for the person.
A free local LLM (Ollama) could summarise "About" from search results - not included.
