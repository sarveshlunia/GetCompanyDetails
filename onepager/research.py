"""Free, key-less company research for Indian companies.

Sources (all free, no API key, no LLM):
  * Yahoo Finance via `yfinance`  -> listed companies: profile, market cap, 3-4y revenue/PAT,
                                     net worth, investments, cash, debt, executives
  * DuckDuckGo via `ddgs`         -> credit ratings (snippets), LinkedIn profile URL (search result
                                     only, never scraped), news, plant/group-company leads
Unlisted companies get far less: Yahoo has nothing on them. The tool says so and gives lookup links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional
from urllib.parse import quote_plus

from .fin_extract import Cand
from .schema import Amount, CompanyOnePager, MeetupPerson, Person, Rating, YearFigure

CR = 1e7  # 1 crore = 10 million
READ_PDFS = True  # read annual-report PDFs for numbers (slower, more complete); the UI toggles this


@dataclass
class CompanyRequest:
    name: str
    meetup_person: Optional[str] = None
    hint: Optional[str] = None  # ticker (e.g. RELIANCE.NS), city or website


@dataclass
class CompanyResult:
    request: CompanyRequest
    data: Optional[CompanyOnePager] = None
    notes: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------- helpers
def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # NaN -> None


def _cr(x: Any) -> Optional[float]:
    v = _num(x)
    return None if v is None else round(v / CR, 2)


def _row(df, names: List[str]):
    """First matching row of a yfinance statement as {Timestamp: value}."""
    if df is None or getattr(df, "empty", True):
        return {}
    for n in names:
        if n in df.index:
            return {c: df.loc[n, c] for c in df.columns}
    return {}


def _fy(ts) -> str:
    """Indian FY label: year-end 31-Mar-2025 -> FY2025."""
    return f"FY{ts.year + (1 if ts.month > 3 else 0)}"


def _years(series: dict, basis: str = "Consolidated") -> List[YearFigure]:
    out = []
    for ts in sorted(series, reverse=True)[:3]:
        v = _cr(series[ts])
        if v is not None:
            out.append(YearFigure(fiscal_year=_fy(ts), value_cr=v, basis=basis, source_url="Yahoo Finance"))
    return out


def _latest(series: dict, period_note: str = "") -> Amount:
    for ts in sorted(series, reverse=True):
        v = _cr(series[ts])
        if v is not None:
            return Amount(value_cr=v, period=_fy(ts), basis="Consolidated", source_url="Yahoo Finance")
    return Amount()


# ---------------------------------------------------------------- web search
def _search(query: str, n: int = 6, news: bool = False) -> List[dict]:
    """DuckDuckGo with retries and backend fallback - it fails/rate-limits often."""
    import time
    from . import http
    http.record_query(query)
    try:
        from ddgs import DDGS
    except Exception:
        http.log("FAIL ddgs not installed")
        return []
    for attempt, backend in enumerate(("auto", "duckduckgo", "bing")):
        try:
            with DDGS() as d:
                res = (d.news(query, region="in-en", max_results=n) if news
                       else d.text(query, region="in-en", max_results=n, backend=backend))
            res = list(res or [])
            if res:
                http.log(f"OK   search[{backend}] {query[:60]} -> {len(res)} results")
                return res
        except Exception as e:
            http.log(f"FAIL search[{backend}] {type(e).__name__}: {str(e)[:80]}")
            time.sleep(0.5 * (attempt + 1))
            if "ConnectError" in type(e).__name__ or "Timeout" in str(e)[:40] and attempt >= 1:
                break
    return []


def _name_core(n: str) -> str:
    n = re.sub(r"\b(limited|ltd|pvt|private|public|co|company|corporation|corp|inc|llp|india|industries|industry|enterprises|group|holdings)\b\.?", " ", n.lower())
    return re.sub(r"[^a-z0-9 ]", " ", n).split()


def similar(a: str, b: str) -> float:
    import difflib
    x, y = " ".join(_name_core(a)) or a.lower(), " ".join(_name_core(b)) or b.lower()
    return difflib.SequenceMatcher(None, x, y).ratio()


_AUTH_RE = re.compile(r"authori[sz]ed\s+(?:share\s+)?capital[^\d]{0,40}((?:rs\.?|inr|\u20b9)?\s*\d[\d,\.]*\s*(?:crore|cr|lakh|lac)?)", re.I)
_PAID_RE = re.compile(r"paid[\s-]*up\s+(?:share\s+)?capital[^\d]{0,40}((?:rs\.?|inr|\u20b9)?\s*\d[\d,\.]*\s*(?:crore|cr|lakh|lac)?)", re.I)
_DIRS_RE = re.compile(r"directors?\s+(?:of\s+[^.]{0,80}?\s+)?(?:are|is|include)\s+([A-Z][^.]{5,200})\.", re.I)


def capital_from_snippets(name: str):
    """Fallback when Zauba blocks us: Zauba/Tofler/Falcon snippets usually quote both capitals in plain text."""
    from .http import inr_to_cr
    auth = paid = src = None
    first = (_name_core(name) or [""])[0]
    for r in _search(f'"{name}" authorised capital paid up capital', 8):
        text = f"{r.get('title','')} {r.get('body','')}"
        if first and first not in text.lower():
            continue
        a, p = _AUTH_RE.search(text), _PAID_RE.search(text)
        if a and auth is None:
            auth, src = inr_to_cr(a.group(1)), r.get("href")
        if p and paid is None:
            paid, src = inr_to_cr(p.group(1)), src or r.get("href")
        if auth is not None and paid is not None:
            break
    return auth, paid, src


def directors_from_snippets(name: str) -> List[Person]:
    first = (_name_core(name) or [""])[0]
    for r in _search(f'"{name}" directors list DIN', 6):
        text = f"{r.get('title','')} {r.get('body','')}"
        if first and first not in text.lower():
            continue
        m = _DIRS_RE.search(text)
        if m:
            names = [x.strip() for x in re.split(r",|\band\b", m.group(1)) if 2 <= len(x.split()) <= 4]
            if names:
                return [Person(name=n) for n in names[:10]]
    return []


_RATING_RE = re.compile(
    r"(CRISIL|ICRA|CARE|India Ratings|IND|Acuite|ACUITE|Brickwork|BWR|Infomerics)"
    r"[^.\n]{0,60}?\b((?:AAA|AA\+|AA-|AA|A\+|A-|A(?=\s*[(/]|\s*(?:Stable|Positive|Negative))|BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|D)"
    r"(?:\s*\(?\s*(?:Stable|Positive|Negative|CE)\s*\)?)?)(?![\w+])"
)
_SHORT_RE = re.compile(r"\b(A[1-4]\+?)(?![\w+])")


def _ratings(name: str):
    lt, st, sources = Rating(), Rating(), []
    results = _search(f'"{name}" credit rating rationale CRISIL ICRA CARE long term short term', 8)
    results += _search(f'"{name}" rating site:crisilratings.com OR site:icra.in OR site:careratings.com OR site:indiaratings.co.in', 6)
    for r in results:
        text = f"{r.get('title','')} {r.get('body','')}"
        url = r.get("href")
        if not lt.rating and (m := _RATING_RE.search(text)):
            lt = Rating(rating=m.group(2).strip(), agency=m.group(1), source_url=url)
        if not st.rating and (m := _SHORT_RE.search(text)):
            st = Rating(rating=m.group(1), source_url=url)
        if url:
            sources.append(url)
    return lt, st, sources[:3]


def _person(name: str, company: str, designation_hint: bool = True) -> MeetupPerson:
    mp = MeetupPerson(name=name)
    for r in _search(f'site:linkedin.com/in "{name}" "{company}"', 5):
        url = r.get("href", "")
        if "linkedin.com/in/" in url:
            mp.linkedin_url = url
            # Typical title: "Name - Designation - Company | LinkedIn"
            parts = [p.strip() for p in re.split(r"\s[-–|]\s", r.get("title", ""))]
            if len(parts) >= 2 and parts[1].lower() != "linkedin":
                mp.designation = parts[1]
            if r.get("body"):
                mp.career_history = [r["body"][:300] + "  (LinkedIn search snippet - open profile for full history)"]
            break
    return mp


# ---------------------------------------------------------------- ticker
def _resolve_ticker(req: CompanyRequest) -> Optional[str]:
    import yfinance as yf

    if req.hint and re.fullmatch(r"[A-Za-z0-9&\-]+\.(NS|BO)", req.hint.strip()):
        return req.hint.strip().upper()
    try:
        quotes = yf.Search(req.name, max_results=10).quotes
    except Exception:
        return None
    cands = [q for q in quotes if str(q.get("symbol", "")).endswith((".NS", ".BO"))]
    cands.sort(key=lambda q: 0 if q["symbol"].endswith(".NS") else 1)
    return cands[0]["symbol"] if cands else None


# ---------------------------------------------------------------- main
def _yahoo_cands(t, url: str) -> List[Cand]:
    inc, bal = t.income_stmt, t.balance_sheet
    out: List[Cand] = []

    def add(field, df, names, n):
        row = _row(df, names)
        for ts in sorted(row, reverse=True)[:n]:
            cr = _cr(row[ts])
            if cr is not None:
                out.append(Cand(field, _fy(ts), cr, "Yahoo Finance", url, "Unspecified", 0, "Yahoo Finance statement row", "api"))

    add("revenue", inc, ["Total Revenue", "Operating Revenue"], 3)
    add("pat", inc, ["Net Income", "Net Income Common Stockholders"], 3)
    add("net_worth", bal, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"], 1)
    add("non_current_investments", bal, ["Long Term Equity Investment", "Investments And Advances", "Investmentin Financial Assets"], 1)
    add("current_investments", bal, ["Other Short Term Investments", "Available For Sale Securities", "Short Term Investments"], 1)
    add("cash", bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], 1)
    add("debt", bal, ["Total Debt"], 1)
    return out


def _listed(sym: str, d: CompanyOnePager, notes: List[str], cands: Optional[List[Cand]] = None) -> None:
    import yfinance as yf

    t = yf.Ticker(sym)
    info = t.info or {}
    exch = "NSE" if sym.endswith(".NS") else "BSE"
    d.company_name = info.get("longName") or d.company_name
    d.listed_status = f"Listed ({exch}: {sym.split('.')[0]})"
    d.website = info.get("website")
    d.industry = ", ".join(x for x in (info.get("sector"), info.get("industry")) if x) or None
    d.about = info.get("longBusinessSummary")
    addr = [info.get(k) for k in ("address1", "address2", "city", "state", "zip", "country")]
    d.corporate_address = ", ".join(str(a) for a in addr if a) or None
    mc = _cr(info.get("marketCap"))
    if mc is not None:
        d.market_cap = Amount(value_cr=mc, period=date.today().isoformat(), source_url="Yahoo Finance")
    d.directors = [
        Person(name=o["name"], designation=o.get("title"))
        for o in (info.get("companyOfficers") or [])[:10] if o.get("name")
    ]
    if d.directors:
        d.data_gaps.append("'Directors' lists key executives from Yahoo; full board (with DINs) is on MCA / BSE-NSE filings.")

    if cands is not None:
        cands += _yahoo_cands(t, f"https://finance.yahoo.com/quote/{sym}/financials")
    pct = _num(info.get("heldPercentInsiders"))
    d.promoter_holding_pct = None if pct is None else round(pct * 100, 2)
    d.sources.append(f"https://finance.yahoo.com/quote/{sym}")
    d.sources.append(f"https://www.screener.in/company/{sym.split('.')[0]}/consolidated/")
    notes.append(f"Yahoo Finance ticker {sym}; statements in INR, converted to Rs. crore.")


def _apply_screener(sc, d: CompanyOnePager, cands: List[Cand]) -> None:
    """Screener consolidated figures (already Rs. crore) become candidates; priority is decided later."""
    for field, m in (("revenue", sc.sales), ("pat", sc.profit)):
        for y in sorted(m, reverse=True)[:3]:
            cands.append(Cand(field, y, m[y], "Screener", sc.url, "Consolidated", 0, "Screener consolidated table", "api"))
    for field, val in (("net_worth", sc.net_worth), ("debt", sc.borrowings), ("non_current_investments", sc.investments), ("cash", sc.cash)):
        if val:
            cands.append(Cand(field, val[0], val[1], "Screener", sc.url, "Consolidated", 0, "Screener consolidated balance sheet", "api"))
    d.sources.append(sc.url)


FIELD_LABELS = {"about": "About", "corporate_address": "Corporate address", "authorised_capital": "Authorised capital",
                "paid_up_capital": "Paid-up capital", "directors": "Directors", "products_and_services": "Products & services",
                "factories": "Factories", "group_companies": "Group companies", "revenue": "Revenue", "pat": "PAT",
                "net_worth": "Net worth", "debt": "Debt", "cash": "Cash & equivalents", "non_current_investments": "Non-current investments",
                "current_investments": "Current investments"}


def _trail_from(d: CompanyOnePager, attempts: List[tuple]) -> List[dict]:
    rows = []
    for fields, source, url, ok, note in attempts:
        for f in fields:
            rows.append({"field": FIELD_LABELS.get(f, f), "source": source, "url": url,
                         "result": ("FOUND: " if ok else "NOT FOUND: ") + note})
    return rows


def _try(d: CompanyOnePager, label: str, fn, *args, default=None):
    """Run one source; on any error log it and carry on with the other sources."""
    import traceback
    from . import http
    try:
        return fn(*args)
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)[-1]
        http.log(f"ERROR {label}: {type(e).__name__}: {e} (at {tb.filename.split('/')[-1]}:{tb.lineno})")
        d.data_gaps.append(f"{label} failed ({type(e).__name__}) - see Debug log; fill manually.")
        return default


def run_company(req: CompanyRequest, client=None) -> CompanyResult:
    """Order follows the analyst's manual process:
    Zauba (capital, directors) -> company website (about, address, products, factories) ->
    Screener/Yahoo consolidated financials -> ratings search -> LinkedIn person -> ChatGPT gap prompt."""
    from . import screener, website, zauba
    from .prompts import gap_prompt

    from . import http as _h
    _h.drain()
    _h.drain_queries()
    cands: List[Cand] = []
    attempts: List[tuple] = []  # (fields, source, url, ok, note)
    res = CompanyResult(request=req)
    d = CompanyOnePager(company_name=req.name, confidence="Low")
    notes: List[str] = []
    try:
        # 1. Zauba: authorised + paid-up capital, CIN, listing status, directors
        z = _try(d, "Zauba", zauba.fetch, req.name)
        attempts.append((["authorised_capital", "paid_up_capital", "directors"], "Zauba Corp",
                         z.url if z else f"https://www.zaubacorp.com/companysearchresults/{quote_plus(req.name)}", bool(z),
                         "page read" if z else "blocked / not found (bot protection) - paste the page in the app"))
        if z:
            d.cin = z.cin
            d.authorised_capital = Amount(value_cr=z.authorised_cr, basis="Zauba Corp", source_url=z.url)
            d.paid_up_capital = Amount(value_cr=z.paid_up_cr, basis="Zauba Corp", source_url=z.url)
            d.directors = [Person(name=n, designation=des, din=din) for n, des, din in z.directors[:10]]
            d.corporate_address = z.address
            d.listed_status = z.listing_status
            d.sources.append(z.url)
            d.field_sources.update({"authorised_capital": "Zauba", "paid_up_capital": "Zauba", "directors": "Zauba"})
            d.field_urls.update({k: z.url for k in ("authorised_capital", "paid_up_capital", "directors")})
        else:
            auth, paid, src = _try(d, "Capital snippets", capital_from_snippets, req.name, default=(None, None, None))
            if auth is not None or paid is not None:
                d.authorised_capital = Amount(value_cr=auth, basis="Search snippet - verify", source_url=src)
                d.paid_up_capital = Amount(value_cr=paid, basis="Search snippet - verify", source_url=src)
                d.field_sources.update({"authorised_capital": "Search snippet", "paid_up_capital": "Search snippet"})
                if src:
                    d.field_urls.update({"authorised_capital": src, "paid_up_capital": src})
                d.sources.append(src or "")
            d.directors = _try(d, "Director snippets", directors_from_snippets, req.name, default=[])
            gq = "https://www.google.com/search?q=" + quote_plus(f'"{req.name}" authorised capital paid up capital')
            attempts.append((["authorised_capital", "paid_up_capital"], "Search snippet", gq, auth is not None or paid is not None,
                             "quoted in a search result - verify" if (auth is not None or paid is not None) else "no snippet quoted the capital"))
            if d.directors:
                d.field_sources["directors"] = "Search snippet"
            d.data_gaps.append("Zauba blocked/not found - capital and directors came from search snippets (verify) or are missing: "
                               f"https://www.zaubacorp.com/companysearchresults/{quote_plus(req.name)}")

        # 2. Listed? Yahoo (profile + cash + fallback financials), then Screener (consolidated) overrides
        sym, sc_hit = None, None
        if req.hint and re.fullmatch(r"[A-Za-z0-9&\-]+\.(NS|BO)", req.hint.strip()):
            sym = req.hint.strip().upper()
        else:
            sc_hit = _try(d, "Screener search", screener.search, req.name)
            if sc_hit:
                code, matched = sc_hit
                if similar(req.name, matched) < 0.5:
                    d.data_gaps.append(f"CHECK NAME: you typed '{req.name}' but the closest listed match is '{matched}' - ignore listed data if it is a different company.")
                sym = code + (".BO" if code.isdigit() else ".NS")
        if not sym:
            try:
                sym = _resolve_ticker(req)
            except Exception as e:
                notes.append(f"Ticker lookup failed: {e}")
        if sym:
            try:
                _try(d, "Yahoo Finance", _listed, sym, d, notes, cands)
                d.confidence = "Medium" if d.revenue else d.confidence
            except Exception as e:
                d.data_gaps.append(f"Yahoo Finance fetch failed for {sym}: {e}")
            sc = _try(d, "Screener page", screener.fetch, sym.split(".")[0])
            attempts.append((["revenue", "pat", "net_worth", "debt", "non_current_investments"], "Screener",
                             f"https://www.screener.in/company/{sym.split('.')[0]}/consolidated/", bool(sc),
                             "consolidated tables read" if sc else "page not reachable"))
            if sc:
                d.sources += sc.annual_reports
                if sc.annual_reports:
                    d.field_urls["annual_report"] = sc.annual_reports[0]
                if sc.about and not d.about:
                    d.about = sc.about
                    d.field_sources["about"] = "Screener"
                if sc.website and not d.website:
                    d.website = sc.website
                _try(d, "Screener parse", _apply_screener, sc, d, cands)
                d.confidence = "High" if d.revenue else d.confidence
                if sc.rating_links:
                    d.sources += sc.rating_links
                    d.data_gaps.append("Rating rationale PDFs on Screener (open for the exact rating): " + ", ".join(sc.rating_links))
            else:
                d.data_gaps.append("Screener consolidated page not reachable; financials are Yahoo's - confirm they are consolidated.")
        else:
            d.listed_status = d.listed_status or "Unlisted / not found on NSE-BSE"
            d.data_gaps.append("Unlisted: net worth, investments, revenue, PAT, cash, debt need the consolidated balance sheet "
                               f"(MCA filing / Tofler https://www.tofler.in/search?q={quote_plus(req.name)} / rating rationale).")

        # 3. Company website: about, address, products, factories (website wins over Yahoo/Zauba text)
        site_url = d.website
        if not site_url and req.hint and re.search(r"\.[a-z]{2,}", req.hint) and ".NS" not in req.hint.upper() and ".BO" not in req.hint.upper():
            site_url = req.hint if req.hint.startswith("http") else f"https://{req.hint}/"
        if not site_url:
            site_url = website.pick_site(_search(f'"{req.name}" official website', 8))
        site = _try(d, "Company website", website.fetch, site_url) if site_url else None
        if site:
            d.website = site.url
            for kind, flds in (("about", ["about"]), ("contact", ["corporate_address"]), ("products", ["products_and_services"]),
                               ("plants", ["factories"]), ("board", ["directors"]), ("group", ["group_companies"])):
                u = site.page_urls.get(kind) or (site.url if kind in ("about", "contact") else "")
                have = {"about": site.about, "contact": site.address, "products": site.products, "plants": site.factories,
                        "board": site.directors, "group": site.group}[kind]
                attempts.append((flds, "Company website", u or site.url, bool(have),
                                 "read from page" if have else ("page opened, nothing recognised" if site.page_urls.get(kind) else "no such page found on the site")))
            d.sources += site.pages[:5]
            for attr, val in (("about", site.about), ("corporate_address", site.address),
                              ("products_and_services", site.products), ("factories", site.factories)):
                if val:
                    setattr(d, attr, val)
                    d.field_sources[attr] = "Company website"
            pu = site.page_urls
            for attr, kind in (("about", "about"), ("corporate_address", "contact"), ("products_and_services", "products"), ("factories", "plants")):
                if d.field_sources.get(attr) == "Company website":
                    d.field_urls[attr] = pu.get(kind) or pu.get("home") or site.url
            if site.directors and not d.directors:
                d.directors = [Person(name=n, designation=des) for n, des in site.directors]
                d.field_sources["directors"] = "Company website (verify)"
                d.field_urls["directors"] = pu.get("board", site.url)
            if site.group:
                d.group_companies = site.group
                d.field_sources["group_companies"] = "Company website"
                d.field_urls["group_companies"] = pu.get("group", site.url)
            if site.annual_reports:
                d.sources += site.annual_reports
                d.field_urls.setdefault("annual_report", site.annual_reports[0])
            if not d.directors:
                d.data_gaps.append("Directors: not on Zauba or website Board page; use the search links below or ChatGPT prompt.")
        else:
            attempts.append((["about", "corporate_address", "products_and_services", "factories", "directors", "group_companies"],
                             "Company website", site_url or "", False, "website not found or not reachable"))
            d.data_gaps.append(f"Company website {'not reachable' if site_url else 'not found'}: about/address/products/factories need manual entry.")
        # 3b. Numbers, priority 1: company's own pages and annual reports (then Screener already collected, then others)
        from . import fin_extract, numbers
        nfields = ["revenue", "pat", "net_worth", "debt", "cash", "non_current_investments", "current_investments"]
        if site:
            for url, html in site.fin_html:
                got = _try(d, "Website financial page", fin_extract.from_html, html, "Company website", url, default=[])
                cands += got
                attempts.append((nfields, "Company website", url, bool(got), f"{len(got)} numbers read" if got else "page read, no labelled numbers with a readable year+unit"))
            if not site.fin_html:
                attempts.append((nfields, "Company website", site.page_urls.get("investor") or site.url, False, "no investor/financial-results page found"))
        pdfs = list(dict.fromkeys((site.annual_reports if site else []) + ((sc.annual_reports if sc else []) if sym else [])))
        if READ_PDFS and pdfs:
            for url in pdfs[:2]:
                got = _try(d, "Annual report PDF", fin_extract.from_pdf, url, default=[])
                cands += got
                attempts.append((nfields, "Annual report PDF", url, bool(got), f"{len(got)} numbers read" if got else "no readable numbers (scanned/odd layout/too big)"))
        elif pdfs:
            attempts.append((nfields, "Annual report PDF", pdfs[0], False, "skipped (PDF reading switched off)"))
        else:
            attempts.append((nfields, "Annual report PDF", "", False, "no annual-report link found on the website or Screener"))
        if not any(c.field == "revenue" for c in cands):  # nothing structured: try quoted figures in search results
            for r in _search(f'"{d.company_name}" revenue net profit crore FY', 8):
                cands += fin_extract.from_snippet(f"{r.get('title','')}. {r.get('body','')}", r.get("href", ""))
        numbers.resolve(cands, d)
        if cands and d.revenue and d.confidence != "High":
            d.confidence = "High" if any(y.source in ("Company website", "Annual report PDF", "Screener") for y in d.revenue) else "Medium"
        if not d.revenue:
            d.data_gaps.append("Revenue/PAT not found from website, annual report, Screener or other sources - use the links below.")
        from . import wiki
        w = _try(d, "Wikipedia/Wikidata", wiki.fetch, d.company_name, lambda t: similar(d.company_name, t) >= 0.55)
        attempts.append((["about", "group_companies"], "Wikipedia/Wikidata", w.url if w else "https://en.wikipedia.org/w/index.php?search=" + quote_plus(d.company_name), bool(w), "article found" if w else "no matching article"))
        if w:
            d.sources.append(w.url)
            if w.about and not d.about:
                d.about, d.field_sources["about"], d.field_urls["about"] = w.about, "Wikipedia", w.url
            extra = [f"{x} (Wikidata: subsidiary)" for x in w.subsidiaries] + [f"{x} (Wikidata: parent)" for x in w.parent]
            if extra:
                d.group_companies = (d.group_companies or []) + [e for e in extra if e not in (d.group_companies or [])]
                d.field_sources.setdefault("group_companies", "Wikidata")
                d.field_urls.setdefault("group_companies", w.url)
        if not d.about:
            for r in _search(f'"{d.company_name}" company profile India what does it do', 5):
                host = r.get("href", "")
                if not any(b in host for b in ("linkedin.", "zaubacorp.", "tofler.", "justdial.", "indiamart.")) and len(r.get("body", "")) > 60:
                    d.about = r["body"] + "  (search snippet - verify)"
                    d.field_sources["about"] = "Search snippet"
                    break
        for attr, label in (("about", "About"), ("corporate_address", "Corporate address"),
                            ("products_and_services", "Products & services"), ("factories", "Factories")):
            if not getattr(d, attr):
                d.data_gaps.append(f"{label}: not found on website - use ChatGPT prompt below.")

        # 4. Ratings (search snippets) - verify on agency site
        lt, st, rsrc = _try(d, "Ratings", _ratings, d.company_name, default=(Rating(), Rating(), []))
        d.long_term_rating, d.short_term_rating = lt, st
        d.sources += rsrc
        d.data_gaps.append("Ratings parsed from search snippets - verify on CRISIL/ICRA/CARE/Screener." if lt.rating
                           else "No rating found in search snippets - check Screener's 'Credit ratings' / agency site.")

        # 5. News + red flags
        d.recent_news = [f"{r.get('date','')[:10]} {r.get('title','')} ({r.get('source','')})".strip()
                         for r in _search(f'"{d.company_name}"', 5, news=True)]
        d.red_flags = [n for n in d.recent_news
                       if re.search(r"downgrad|default|insolven|nclt|fraud|raid|qualif|resign|penalt|probe", n, re.I)]

        # 6. Meetup person: LinkedIn link + headline; history needs manual/ChatGPT
        if req.meetup_person:
            d.meetup_person = _try(d, "LinkedIn search", _person, req.meetup_person, d.company_name, default=MeetupPerson(name=req.meetup_person))
            if not d.meetup_person.linkedin_url:
                d.data_gaps.append("LinkedIn profile not found via public search; search manually.")
            d.data_gaps.append("LinkedIn work history (company, from-to years) needs a logged-in view: open the profile or use the ChatGPT prompt.")

        d.group_companies = d.group_companies or []
        d.data_gaps.append("Group companies: no structured free source.")
        from .links import search_links
        d.search_links = search_links(d.company_name, req.meetup_person, sym, d.website)
        if d.meetup_person.linkedin_url:
            d.field_urls["linkedin"] = d.meetup_person.linkedin_url
        d.chatgpt_prompt = gap_prompt(d, req.meetup_person or "")
        from . import http
        from urllib.parse import quote_plus as qp
        d.searches = [{"query": q, "url": "https://www.google.com/search?q=" + qp(q)} for q in http.drain_queries()]
        d.trail += _trail_from(d, attempts)
        d.source_log = http.drain()
        res.data, res.notes = d, "\n".join(notes)
    except Exception as e:
        res.error = str(e)
    return res
