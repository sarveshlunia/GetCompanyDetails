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


def run_company(req: CompanyRequest, client=None) -> CompanyResult:
    res = CompanyResult(request=req)
    d = CompanyOnePager(company_name=req.name, confidence="Low")
    notes: List[str] = []
    try:
        sym = None
        try:
            sym = _resolve_ticker(req)
        except Exception as e:  # yfinance missing / network
            notes.append(f"Ticker lookup failed: {e}")
        if sym:
            try:
                _listed(sym, d, notes)
                d.confidence = "Medium"
            except Exception as e:
                d.data_gaps.append(f"Yahoo Finance fetch failed for {sym}: {e}")
        else:
            d.listed_status = "Unlisted / not found on NSE-BSE"
            d.data_gaps.append(
                "No NSE/BSE listing found, so no free structured financials. Look up: "
                f"Tofler https://www.tofler.in/search?q={quote_plus(req.name)} | "
                f"Zauba https://www.zaubacorp.com/companysearchresults/{quote_plus(req.name)} | "
                f"MCA https://www.mca.gov.in (CIN, directors, charges). If listed, retry with the ticker in the Hint column (e.g. XYZ.NS)."
            )

        lt, st, rsrc = _ratings(d.company_name)
        d.long_term_rating, d.short_term_rating = lt, st
        d.sources += rsrc
        if not lt.rating:
            d.data_gaps.append("No credit rating found in search snippets - check CRISIL/ICRA/CARE sites directly.")
        else:
            d.data_gaps.append("Ratings come from search snippets and may be stale or mismatched - verify on the agency site.")

        d.recent_news = [
            f"{r.get('date','')[:10]} {r.get('title','')} ({r.get('source','')})".strip()
            for r in _search(f'"{d.company_name}"', 5, news=True)
        ]
        flag_re = re.compile(r"downgrad|default|insolven|nclt|fraud|raid|qualif|resign|penalt|probe", re.I)
        d.red_flags = [n for n in d.recent_news if flag_re.search(n)]

        for key, q in (("factories", f'"{d.company_name}" manufacturing plants locations'),
                       ("group_companies", f'"{d.company_name}" subsidiaries group companies')):
            hits = _search(q, 3)
            setattr(d, key, [f"(search lead, verify) {h.get('title','')} - {h.get('href','')}" for h in hits])
        if not d.about:
            hits = _search(f'"{d.company_name}" company profile India', 3)
            if hits:
                d.about = f"(search snippet) {hits[0].get('body','')}"
        if req.meetup_person:
            d.meetup_person = _person(req.meetup_person, d.company_name)
            if not d.meetup_person.linkedin_url:
                d.data_gaps.append("LinkedIn profile not found via public search; search manually.")
            d.data_gaps.append("LinkedIn career history cannot be read without logging in; open the profile.")
        d.data_gaps += ["Products & services, factories, group companies: only search leads (no structured free source)."]
        res.data, res.notes = d, "\n".join(notes)
    except Exception as e:
        res.error = str(e)
    return res
