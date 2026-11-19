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
    try:
        from ddgs import DDGS

        with DDGS() as d:
            res = d.news(query, region="in-en", max_results=n) if news else d.text(query, region="in-en", max_results=n)
            return list(res or [])
    except Exception:
        return []


_RATING_RE = re.compile(
    r"(CRISIL|ICRA|CARE|India Ratings|IND|Acuite|ACUITE|Brickwork|BWR|Infomerics)"
    r"[^.\n]{0,60}?\b((?:AAA|AA\+|AA-|AA|A\+|A-|A(?=\s*[(/]|\s*(?:Stable|Positive|Negative))|BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|D)"
    r"(?:\s*\(?\s*(?:Stable|Positive|Negative|CE)\s*\)?)?)(?![\w+])"
)
_SHORT_RE = re.compile(r"\b(A[1-4]\+?)(?![\w+])")


def _ratings(name: str):
    lt, st, sources = Rating(), Rating(), []
    for r in _search(f'"{name}" credit rating rationale CRISIL ICRA CARE long term short term', 8):
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
def _listed(sym: str, d: CompanyOnePager, notes: List[str]) -> None:
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

    inc, bal = t.income_stmt, t.balance_sheet
    d.revenue = _years(_row(inc, ["Total Revenue", "Operating Revenue"]))
    d.pat = _years(_row(inc, ["Net Income", "Net Income Common Stockholders"]))
    d.net_worth = _latest(_row(bal, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"]))
    d.non_current_investments = _latest(_row(bal, ["Long Term Equity Investment", "Investments And Advances", "Investmentin Financial Assets"]))
    d.current_investments = _latest(_row(bal, ["Other Short Term Investments", "Available For Sale Securities", "Short Term Investments"]))
    d.cash_and_equivalents = _latest(_row(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]))
    d.debt = _latest(_row(bal, ["Total Debt"]))
    pct = _num(info.get("heldPercentInsiders"))
    d.promoter_holding_pct = None if pct is None else round(pct * 100, 2)
    d.sources.append(f"https://finance.yahoo.com/quote/{sym}")
    d.sources.append(f"https://www.screener.in/company/{sym.split('.')[0]}/consolidated/")
    notes.append(f"Yahoo Finance ticker {sym}; statements in INR, converted to Rs. crore.")


def _apply_screener(sc, d: CompanyOnePager) -> None:
    """Screener consolidated figures (already Rs. crore) override Yahoo's."""
    def yf(m):
        return [YearFigure(fiscal_year=y, value_cr=m[y], basis="Consolidated", source_url=sc.url)
                for y in sorted(m, reverse=True)[:3]]
    if sc.sales:
        d.revenue = yf(sc.sales)
    if sc.profit:
        d.pat = yf(sc.profit)
    for attr, val in (("net_worth", sc.net_worth), ("debt", sc.borrowings), ("non_current_investments", sc.investments)):
        if val:
            setattr(d, attr, Amount(value_cr=val[1], period=val[0], basis="Consolidated", source_url=sc.url))
    d.sources.append(sc.url)
    d.field_sources.update({k: "Screener (consolidated)" for k in ("revenue", "pat", "net_worth", "debt", "non_current_investments")
                            if getattr(d, k)})


def run_company(req: CompanyRequest, client=None) -> CompanyResult:
    """Order follows the analyst's manual process:
    Zauba (capital, directors) -> company website (about, address, products, factories) ->
    Screener/Yahoo consolidated financials -> ratings search -> LinkedIn person -> ChatGPT gap prompt."""
    from . import screener, website, zauba
    from .prompts import gap_prompt

    res = CompanyResult(request=req)
    d = CompanyOnePager(company_name=req.name, confidence="Low")
    notes: List[str] = []
    try:
        # 1. Zauba: authorised + paid-up capital, CIN, listing status, directors
        z = zauba.fetch(req.name)
        if z:
            d.cin = z.cin
            d.authorised_capital = Amount(value_cr=z.authorised_cr, basis="Zauba Corp", source_url=z.url)
            d.paid_up_capital = Amount(value_cr=z.paid_up_cr, basis="Zauba Corp", source_url=z.url)
            d.directors = [Person(name=n, designation=des, din=din) for n, des, din in z.directors[:10]]
            d.corporate_address = z.address
            d.listed_status = z.listing_status
            d.sources.append(z.url)
            d.field_sources.update({"authorised_capital": "Zauba", "paid_up_capital": "Zauba", "directors": "Zauba"})
        else:
            d.data_gaps.append(
                "Zauba Corp unreachable/blocked or company not found: get authorised & paid-up capital and directors manually: "
                f"https://www.zaubacorp.com/companysearchresults/{quote_plus(req.name)}")

        # 2. Listed? Yahoo (profile + cash + fallback financials), then Screener (consolidated) overrides
        sym = None
        try:
            sym = _resolve_ticker(req)
        except Exception as e:
            notes.append(f"Ticker lookup failed: {e}")
        if sym:
            try:
                _listed(sym, d, notes)
                d.confidence = "Medium"
            except Exception as e:
                d.data_gaps.append(f"Yahoo Finance fetch failed for {sym}: {e}")
            sc = screener.fetch(sym.split(".")[0])
            if sc:
                _apply_screener(sc, d)
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
        site = website.fetch(site_url) if site_url else None
        if site:
            d.website = site.url
            d.sources += site.pages[:5]
            for attr, val in (("about", site.about), ("corporate_address", site.address),
                              ("products_and_services", site.products), ("factories", site.factories)):
                if val:
                    setattr(d, attr, val)
                    d.field_sources[attr] = "Company website"
            if not d.directors:
                d.data_gaps.append("Directors: not on Zauba; check the website's Board/Leadership page, else ChatGPT prompt below.")
        else:
            d.data_gaps.append(f"Company website {'not reachable' if site_url else 'not found'}: about/address/products/factories need manual entry.")
        for attr, label in (("about", "About"), ("corporate_address", "Corporate address"),
                            ("products_and_services", "Products & services"), ("factories", "Factories")):
            if not getattr(d, attr):
                d.data_gaps.append(f"{label}: not found on website - use ChatGPT prompt below.")

        # 4. Ratings (search snippets) - verify on agency site
        lt, st, rsrc = _ratings(d.company_name)
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
            d.meetup_person = _person(req.meetup_person, d.company_name)
            if not d.meetup_person.linkedin_url:
                d.data_gaps.append("LinkedIn profile not found via public search; search manually.")
            d.data_gaps.append("LinkedIn work history (company, from-to years) needs a logged-in view: open the profile or use the ChatGPT prompt.")

        d.group_companies = d.group_companies or []
        d.data_gaps.append("Group companies: no structured free source.")
        d.chatgpt_prompt = gap_prompt(d, req.meetup_person or "")
        res.data, res.notes = d, "\n".join(notes)
    except Exception as e:
        res.error = str(e)
    return res
