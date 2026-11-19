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

## One source order for every field
**1 Screener -> 2 Zauba -> 3 company website -> 4 other sites** (Yahoo, Wikipedia/Wikidata, search results).
The first source that has a value fills it; later sources only fill what is still empty. The source link is shown to the
right of every value; an empty field shows a ready "search" link instead.

| Field | Where it can come from (in order) |
|---|---|
| Revenue, PAT, net worth, debt, investments, cash, market cap | Screener -> company website financial pages -> annual-report PDF (gaps only) -> Yahoo -> search quotes |
| Authorised / paid-up capital, CIN | Zauba (or pasted Zauba page) -> search quotes |
| About | Screener -> Company website -> Yahoo -> Wikipedia -> search |
| Address | Zauba -> website -> Yahoo |
| Directors | Zauba -> website board page -> Yahoo executives -> search |
| Products, factories | Company website |
| Group companies | Company website -> Wikidata |
| Ratings | Search results (agency sites) - always verify |

Numbers read from the website/PDF/Yahoo/search are flagged "NOT verified". A Screener figure is never overridden by them.
Screener data (ratios, P&L, balance sheet, cash flow, shareholding, schedules, pros/cons) is shown on screen and in Excel.

## Blunt limits
- Zauba is bot-protected and Screener/company sites change HTML; scrapers are best-effort and written against
  fixtures, not verified live. If one is blocked you get a gap + manual link, not a crash.
- Unlisted companies: no free consolidated financials. Expect to fill those by hand (MCA/Tofler/rating rationale).
- Website heuristics (about/products/factories) work on standard sites; JS-heavy sites return nothing.
- LinkedIn history cannot be automated. Ratings from snippets can be stale - verify.

## Upgrades if free isn't enough
Screener.in export / Prowess / Tofler paid API for unlisted financials; Apollo/Lusha for the person.
A free local LLM (Ollama) could summarise "About" from search results - not included.
