# Indian Company One-Pager Generator

Type or upload Indian company names -> Claude researches the web -> you get a filled
Word one-pager per company (your `One_Pager_Format.docx` layout) + one Excel summary.

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

Input: one name per line, or CSV/XLSX/TXT with columns `Company`, `Meetup Person`, `Hint`
(city/website/CIN, to disambiguate common names). Inline form: `Acme Ltd | Rahul Sharma | Pune`.

Config (env): `ONEPAGER_MODEL` (default claude-opus-5-5), `ONEPAGER_EFFORT` (default high),
`ONEPAGER_MAX_SEARCHES`, `ONEPAGER_MAX_FETCHES`.

## Limits (read this)
- Data is whatever is publicly findable. Listed companies: good. Unlisted: financials often only
  come from credit rating rationales or MCA-derived sites, and may be missing or a year old.
- LinkedIn is not scraped. LinkedIn URL / career history appear only if public search surfaces them.
  Expect gaps; the sheet says "Not available" rather than guessing.
- Every figure is in Rs. crore with basis (consolidated/standalone) and source; gaps and red flags are listed.
  Treat output as a draft and spot-check numbers before a meeting.
- Cost: each company is a multi-search research run (minutes, real API spend). Test with 2-3 first.

## Better data sources (if you want accuracy over free web search)
MCA/Tofler/Zauba, Screener.in or Prowess/CMIE for financials, CRISIL/ICRA/CARE rating pages,
Apollo/Lusha/LinkedIn Sales Navigator for the person. Plug any in as extra tools in `research.py`.
