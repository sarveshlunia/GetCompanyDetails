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


def _yahoo(sym: str):
    """Yahoo Finance profile + ticker object (lowest-priority 'other site')."""
    import yfinance as yf
    t = yf.Ticker(sym)
    return (t.info or {}), t


def _apply_screener(sc, d: CompanyOnePager, cands: List[Cand]) -> None:
    """Screener consolidated figures (already Rs. crore) become candidates; priority is decided later."""
    for field, m in (("revenue", sc.sales), ("pat", sc.profit)):
        for y in sorted(m, reverse=True)[:3]:
            cands.append(Cand(field, y, m[y], "Screener", sc.url, sc.basis, 0, f"Screener {sc.basis.lower()} table", "api"))
    for field, val in (("net_worth", sc.net_worth), ("debt", sc.borrowings), ("non_current_investments", sc.investments), ("cash", sc.cash)):
        if val:
            cands.append(Cand(field, val[0], val[1], "Screener", sc.url, sc.basis, 0, f"Screener {sc.basis.lower()} balance sheet", "api"))
    d.sources.append(sc.url)
    d.screener = sc.to_dict()
    if sc.market_cap_cr is not None:
        d.market_cap = Amount(value_cr=sc.market_cap_cr, period=date.today().isoformat(), basis="Current", source="Screener", source_url=sc.url)
    promo = next((v for k, v in sc.shareholding.items() if k.lower().startswith("promoter")), None)
    if promo:
        d.promoter_holding_pct = promo[sorted(promo)[-1]] if promo else None
    d.industry = d.industry or ", ".join(x for x in (sc.sector, sc.industry) if x) or None


FIELD_LABELS = {"listed_status": "Listed / Unlisted", "industry": "Industry", "cin": "CIN", "long_term_rating": "Long-term rating",
                "short_term_rating": "Short-term rating", "meetup_person": "Meetup person", "about": "About", "corporate_address": "Corporate address", "authorised_capital": "Authorised capital",
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


def _empty(v) -> bool:
    if v is None or v == "" or v == []:
        return True
    if isinstance(v, Amount):
        return v.value_cr is None
    if isinstance(v, Rating):
        return not v.rating
    if isinstance(v, MeetupPerson):
        return not v.name
    return False


NUM_FIELDS = ["revenue", "pat", "net_worth", "debt", "cash", "non_current_investments", "current_investments"]
ALLOW_PDF = True   # annual-report PDF numbers, used only for fields Screener/Zauba/website pages could not give


def run_company(req: CompanyRequest, client=None) -> CompanyResult:
    """ONE priority order for every field: 1 Screener -> 2 Zauba -> 3 company website -> 4 other sites
    (Yahoo, Wikipedia/Wikidata, search snippets). A source only fills what is still empty; its link is stored beside the value."""
    from . import fin_extract, http, numbers, screener, website, wiki, zauba
    from .links import search_links
    from .prompts import gap_prompt

    http.drain()
    http.drain_queries()
    cands: List[Cand] = []
    attempts: List[tuple] = []  # (fields, source, url, ok, note)
    res = CompanyResult(request=req)
    d = CompanyOnePager(company_name=req.name, confidence="Low")
    notes: List[str] = []

    def fill(field: str, value, source: str, url: str = "") -> bool:
        """First source wins: set the field only if it is still empty; remember where it came from."""
        if _empty(value) or not _empty(getattr(d, field)):
            return False
        setattr(d, field, value)
        d.field_sources[field], d.field_urls[field] = source, url or ""
        if url:
            d.sources.append(url)
        return True

    def tried(fields, source, url, ok, note):
        attempts.append((fields, source, url, ok, note))

    try:
        # ------------------------------------------------------------------ 1. SCREENER
        sym, sc, sc_url = None, None, ""
        if req.hint and re.fullmatch(r"[A-Za-z0-9&\-]+\.(NS|BO)", req.hint.strip()):
            sym = req.hint.strip().upper()
        else:
            hits = _try(d, "Screener search", screener.search_all, req.name, default=[]) or []
            if not hits:
                for r in _search(f'"{req.name}" site:screener.in', 5):
                    m = re.search(r"screener\.in/company/([A-Za-z0-9&\-]+)/", r.get("href", ""))
                    if m:
                        hits.append((m.group(1), re.sub(r"\s*share price.*$", "", r.get("title", ""), flags=re.I)))
            best = max(hits, key=lambda h: similar(req.name, h[1])) if hits else None
            if best:
                code, matched = best
                if similar(req.name, matched) < 0.5:
                    d.data_gaps.append(f"CHECK NAME: you typed '{req.name}' but the closest listed match is '{matched}' - ignore listed data if it is a different company.")
                sym = code + (".BO" if code.isdigit() else ".NS")
        if sym:
            sc = _try(d, "Screener page", screener.fetch, sym.split(".")[0])
            sc_url = sc.url if sc else f"https://www.screener.in/company/{sym.split('.')[0]}/consolidated/"
        tried(NUM_FIELDS + ["about", "listed_status", "industry"], "Screener", sc_url or f"https://www.google.com/search?q={quote_plus(req.name + ' site:screener.in')}",
              bool(sc), "page read" if sc else ("page not reachable" if sym else "no Screener page (unlisted?)"))
        if sc:
            exch = "BSE" if sym.endswith(".BO") else "NSE"
            fill("listed_status", f"Listed ({exch}: {sym.split('.')[0]})", "Screener", sc.url)
            fill("about", sc.about, "Screener", sc.url)
            fill("website", sc.website, "Screener", sc.url)
            fill("industry", ", ".join(x for x in (sc.sector, sc.industry) if x), "Screener", sc.url)
            _try(d, "Screener parse", _apply_screener, sc, d, cands)
            d.field_urls["market_cap"] = sc.url
            d.sources += sc.annual_reports + sc.rating_links
            if sc.annual_reports:
                d.field_urls["annual_report"] = sc.annual_reports[0]
            if sc.rating_links:
                d.data_gaps.append("Rating rationale documents on Screener (open for the exact rating): " + ", ".join(sc.rating_links))
        if sym and not sc:
            d.data_gaps.append("Screener page not reachable - numbers fall back to the next sources (unverified).")

        # ------------------------------------------------------------------ 2. ZAUBA
        z = _try(d, "Zauba", zauba.fetch, req.name)
        zurl = z.url if z else f"https://www.zaubacorp.com/companysearchresults/{quote_plus(req.name)}"
        tried(["cin", "authorised_capital", "paid_up_capital", "directors", "corporate_address", "listed_status"], "Zauba Corp", zurl, bool(z),
              "page read" if z else "blocked / not found (bot protection) - use the paste box in the app")
        if z:
            d.cin = d.cin or z.cin
            fill("listed_status", z.listing_status, "Zauba", z.url)
            fill("corporate_address", z.address, "Zauba", z.url)
            fill("directors", [Person(name=n, designation=des, din=din) for n, des, din in z.directors[:10]], "Zauba", z.url)
            fill("authorised_capital", Amount(value_cr=z.authorised_cr, basis="Registry", source="Zauba", source_url=z.url), "Zauba", z.url)
            fill("paid_up_capital", Amount(value_cr=z.paid_up_cr, basis="Registry", source="Zauba", source_url=z.url), "Zauba", z.url)

        # ------------------------------------------------------------------ 3. COMPANY WEBSITE
        site_url = d.website
        if not site_url and req.hint and re.search(r"\.[a-z]{2,}", req.hint) and ".NS" not in req.hint.upper() and ".BO" not in req.hint.upper():
            site_url = req.hint if req.hint.startswith("http") else f"https://{req.hint}/"
        if not site_url:
            site_url = website.pick_site(_search(f'"{req.name}" official website', 8))
        site = _try(d, "Company website", website.fetch, site_url) if site_url else None
        if site:
            d.website = d.website or site.url
            pu = site.page_urls
            for kind, flds, have in (("about", ["about"], site.about), ("contact", ["corporate_address"], site.address),
                                     ("products", ["products_and_services"], site.products), ("plants", ["factories"], site.factories),
                                     ("board", ["directors"], site.directors), ("group", ["group_companies"], site.group)):
                u = pu.get(kind) or (site.url if kind in ("about", "contact") else "")
                tried(flds, "Company website", u or site.url, bool(have),
                      "read from page" if have else ("page opened, nothing recognised" if pu.get(kind) else "no such page found on the site"))
            fill("about", site.about, "Company website", pu.get("about") or site.url)
            fill("corporate_address", site.address, "Company website", pu.get("contact") or site.url)
            fill("products_and_services", site.products, "Company website", pu.get("products") or site.url)
            fill("factories", site.factories, "Company website", pu.get("plants") or site.url)
            fill("directors", [Person(name=n, designation=des) for n, des in site.directors], "Company website (verify)", pu.get("board") or site.url)
            fill("group_companies", site.group, "Company website", pu.get("group") or site.url)
            d.sources += site.pages[:5] + site.annual_reports
            if site.annual_reports:
                d.field_urls.setdefault("annual_report", site.annual_reports[0])
        else:
            tried(["about", "corporate_address", "products_and_services", "factories", "directors", "group_companies"],
                  "Company website", site_url or "", False, "website not found or not reachable")

        # numbers still missing after Screener -> the company's own financial pages, then (optionally) its annual-report PDF
        have = {c.field for c in cands}
        missing = [f for f in NUM_FIELDS if f not in have]
        if missing and site:
            for url, html in site.fin_html:
                got = _try(d, "Website financial page", fin_extract.from_html, html, "Company website", url, default=[])
                cands += [c for c in got if c.field in missing]
                tried(missing, "Company website", url, bool(got), f"{len(got)} numbers read" if got else "no labelled numbers with a readable year+unit")
            have = {c.field for c in cands}
            missing = [f for f in NUM_FIELDS if f not in have]
        if missing and ALLOW_PDF:
            pdfs = list(dict.fromkeys((site.annual_reports if site else []) + (sc.annual_reports if sc else [])))
            for url in pdfs[:1]:
                got = _try(d, "Annual report PDF", fin_extract.from_pdf, url, default=[])
                cands += [c for c in got if c.field in missing]
                tried(missing, "Annual report PDF", url, bool(got), f"{len(got)} numbers read (unverified)" if got else "no readable numbers (scanned/odd layout)")
            have = {c.field for c in cands}
            missing = [f for f in NUM_FIELDS if f not in have]

        # ------------------------------------------------------------------ 4. OTHER SITES
        info, tick = {}, None
        if sym:
            got = _try(d, "Yahoo Finance", _yahoo, sym, default=None)
            if got:
                info, tick = got
        yurl = f"https://finance.yahoo.com/quote/{sym}" if sym else ""
        if info:
            addr = ", ".join(str(info[k]) for k in ("address1", "address2", "city", "state", "zip", "country") if info.get(k))
            fill("about", info.get("longBusinessSummary"), "Yahoo Finance", yurl)
            fill("corporate_address", addr, "Yahoo Finance", yurl)
            fill("website", info.get("website"), "Yahoo Finance", yurl)
            fill("industry", ", ".join(x for x in (info.get("sector"), info.get("industry")) if x), "Yahoo Finance", yurl)
            fill("directors", [Person(name=o["name"], designation=o.get("title")) for o in (info.get("companyOfficers") or [])[:10] if o.get("name")],
                 "Yahoo Finance (executives only)", yurl)
            mc = _cr(info.get("marketCap"))
            if mc is not None:
                fill("market_cap", Amount(value_cr=mc, period=date.today().isoformat(), basis="Current", source="Yahoo Finance", source_url=yurl), "Yahoo Finance", yurl)
        tried(["about", "corporate_address", "directors"], "Yahoo Finance", yurl, bool(info), "profile read" if info else "no data")
        if missing and tick is not None:
            got = _try(d, "Yahoo numbers", _yahoo_cands, tick, yurl + "/financials", default=[])
            cands += [c for c in got if c.field in missing]
            tried(missing, "Yahoo Finance", yurl + "/financials", bool(got), "statement rows read (unverified)" if got else "no data")
            have = {c.field for c in cands}
            missing = [f for f in NUM_FIELDS if f not in have]

        w = _try(d, "Wikipedia/Wikidata", wiki.fetch, d.company_name, lambda t: similar(d.company_name, t) >= 0.55)
        tried(["about", "group_companies"], "Wikipedia/Wikidata", w.url if w else "https://en.wikipedia.org/w/index.php?search=" + quote_plus(d.company_name),
              bool(w), "article found" if w else "no matching article")
        if w:
            fill("about", w.about, "Wikipedia", w.url)
            extra = [f"{x} (Wikidata: subsidiary)" for x in w.subsidiaries] + [f"{x} (Wikidata: parent)" for x in w.parent]
            if extra and _empty(d.group_companies):
                fill("group_companies", extra, "Wikidata", w.url)

        if _empty(d.authorised_capital) or _empty(d.paid_up_capital):
            auth, paid, src = _try(d, "Capital snippets", capital_from_snippets, req.name, default=(None, None, None))
            gq = "https://www.google.com/search?q=" + quote_plus(f'"{req.name}" authorised capital paid up capital')
            tried(["authorised_capital", "paid_up_capital"], "Search snippet", src or gq, auth is not None or paid is not None,
                  "quoted in a search result - verify" if (auth is not None or paid is not None) else "no snippet quoted the capital")
            fill("authorised_capital", Amount(value_cr=auth, basis="Search snippet - verify", source="Search snippet", source_url=src), "Search snippet", src or "")
            fill("paid_up_capital", Amount(value_cr=paid, basis="Search snippet - verify", source="Search snippet", source_url=src), "Search snippet", src or "")
        if _empty(d.directors):
            fill("directors", _try(d, "Director snippets", directors_from_snippets, req.name, default=[]), "Search snippet (verify)",
                 "https://www.google.com/search?q=" + quote_plus(f'"{req.name}" directors list DIN'))
        if _empty(d.about):
            for r in _search(f'"{d.company_name}" company profile India what does it do', 5):
                host = r.get("href", "")
                if not any(b in host for b in ("linkedin.", "zaubacorp.", "tofler.", "justdial.", "indiamart.")) and len(r.get("body", "")) > 60:
                    fill("about", r["body"] + "  (search snippet - verify)", "Search snippet", host)
                    break
        if missing:  # last resort for numbers: figures quoted in search results
            for r in _search(f'"{d.company_name}" revenue net profit crore FY', 8):
                cands += [c for c in fin_extract.from_snippet(f"{r.get('title','')}. {r.get('body','')}", r.get("href", "")) if c.field in missing]

        lt, st, rsrc = _try(d, "Ratings", _ratings, d.company_name, default=(Rating(), Rating(), []))
        d.long_term_rating, d.short_term_rating = lt, st
        for fld, rt in (("long_term_rating", lt), ("short_term_rating", st)):
            if rt.rating:
                d.field_sources[fld], d.field_urls[fld] = f"Search result ({rt.agency or 'agency'}) - verify", rt.source_url or ""
        d.sources += rsrc
        tried(["long_term_rating", "short_term_rating"], "Search snippet",
              (lt.source_url if lt.rating else "https://www.google.com/search?q=" + quote_plus(f'"{d.company_name}" credit rating CRISIL ICRA CARE')),
              bool(lt.rating), "rating found in a search result - verify" if lt.rating else "no rating in search results")

        d.recent_news = [f"{r.get('date','')[:10]} {r.get('title','')} ({r.get('source','')})".strip()
                         for r in _search(f'"{d.company_name}"', 5, news=True)]
        d.red_flags = [n for n in d.recent_news if re.search(r"downgrad|default|insolven|nclt|fraud|raid|qualif|resign|penalt|probe", n, re.I)]

        if req.meetup_person:
            d.meetup_person = _try(d, "LinkedIn search", _person, req.meetup_person, d.company_name, default=MeetupPerson(name=req.meetup_person))
            d.field_urls["meetup_person"] = d.meetup_person.linkedin_url or ("https://www.google.com/search?q=" + quote_plus(f'"{req.meetup_person}" "{d.company_name}" LinkedIn'))
            d.field_sources["meetup_person"] = "LinkedIn (public search)" if d.meetup_person.linkedin_url else "search"
            tried(["meetup_person"], "LinkedIn (public search)", d.field_urls["meetup_person"], bool(d.meetup_person.linkedin_url),
                  "profile found" if d.meetup_person.linkedin_url else "profile not found")

        # ------------------------------------------------------------------ resolve numbers, finish
        numbers.resolve(cands, d)
        if d.revenue:
            d.confidence = "High" if any(y.source == "Screener" for y in d.revenue) else "Medium"
        d.listed_status = d.listed_status or "Unlisted / not found on NSE-BSE"
        d.group_companies = d.group_companies or []
        d.search_links = search_links(d.company_name, req.meetup_person, sym, d.website)
        gap_fields = {"about": "About", "corporate_address": "Corporate address", "listed_status": "Listed / Unlisted",
                      "authorised_capital": "Authorised capital", "paid_up_capital": "Paid-up capital", "net_worth": "Net worth",
                      "non_current_investments": "Non-current investments", "current_investments": "Current investments",
                      "revenue": "Revenue", "pat": "PAT", "cash_and_equivalents": "Cash & equivalents", "debt": "Debt",
                      "long_term_rating": "Long-term rating", "short_term_rating": "Short-term rating", "directors": "Directors",
                      "products_and_services": "Products & services", "factories": "Factories", "group_companies": "Group companies"}
        for f, lab in gap_fields.items():
            if _empty(getattr(d, f, None)):
                d.data_gaps.append(f"{lab}: not found in Screener, Zauba, the company website or other sites - use the search link beside it.")
        if req.meetup_person:
            d.data_gaps.append("LinkedIn work history (company, from-to years) needs a logged-in view: open the profile or use the ChatGPT prompt.")
        d.chatgpt_prompt = gap_prompt(d, req.meetup_person or "")
        from urllib.parse import quote_plus as qp
        d.searches = [{"query": q, "url": "https://www.google.com/search?q=" + qp(q)} for q in http.drain_queries()]
        d.trail += _trail_from(d, attempts)
        d.source_log = http.drain()
        res.data, res.notes = d, "\n".join(notes)
    except Exception as e:
        res.error = str(e)
    return res
