"""Screener.in consolidated financials (listed companies only). Values are already Rs. crore."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from .http import get

URL = "https://www.screener.in/company/{sym}/consolidated/"


@dataclass
class ScreenerData:
    url: str
    sales: Dict[str, float] = field(default_factory=dict)      # 'FY2025' -> Cr
    profit: Dict[str, float] = field(default_factory=dict)
    net_worth: Optional[tuple] = None                           # (FY, Cr)
    borrowings: Optional[tuple] = None
    investments: Optional[tuple] = None
    rating_links: List[str] = field(default_factory=list)
    annual_reports: List[str] = field(default_factory=list)
    cash: Optional[tuple] = None
    basis: str = "Consolidated"
    warehouse_id: Optional[str] = None
    name: Optional[str] = None
    about: Optional[str] = None
    website: Optional[str] = None
    market_cap_cr: Optional[float] = None
    top_ratios: Dict[str, str] = field(default_factory=dict)
    tables: Dict[str, Dict[str, Dict[str, float]]] = field(default_factory=dict)   # section -> row -> FY -> value
    shareholding: Dict[str, Dict[str, float]] = field(default_factory=dict)        # row -> 'Dec 2024' -> %
    schedules: Dict[str, Dict[str, Dict[str, float]]] = field(default_factory=dict)
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    sector: Optional[str] = None
    industry: Optional[str] = None

    def to_dict(self, years: int = 5) -> dict:
        """JSON-friendly copy for the UI / Excel: only the latest `years` columns of each table."""
        def trim(tbl):
            return {row: dict(sorted(v.items())[-years:]) for row, v in tbl.items() if v}
        return {"url": self.url, "basis": self.basis, "name": self.name, "sector": self.sector, "industry": self.industry,
                "top_ratios": self.top_ratios, "tables": {k: trim(v) for k, v in self.tables.items()},
                "shareholding": {r: dict(list(v.items())[-4:]) for r, v in self.shareholding.items() if v},
                "schedules": {k: trim(v) for k, v in self.schedules.items()}, "pros": self.pros, "cons": self.cons}


def _f(s: str) -> Optional[float]:
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _read_table(t, raw: bool = False) -> Dict[str, Dict[str, float]]:
    thead = t.find("thead")
    heads = [th.get_text(strip=True) for th in (thead.find_all("th") if thead else t.find("tr").find_all(["th", "td"]))][1:]
    cols = [h if raw else (None if (not h or h.startswith("TTM")) else f"FY{h.split()[-1]}") for h in heads]  # 'Mar 2025' -> FY2025
    rows = {}
    body = t.find("tbody") or t
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = re.sub(r"[\s\u00a0+]+$", "", tds[0].get_text(" ", strip=True)).strip()
        vals = {}
        for y, td in zip(cols, tds[1:]):
            v = _f(td.get_text(strip=True))
            if y and v is not None:
                vals[y] = v
        if label:
            rows[label] = vals
    return rows


def _table(soup, section_id, raw: bool = False, container_id: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    sec = soup.find(id=container_id or section_id)
    t = sec.find("table") if sec else None
    return _read_table(t, raw) if t else {}


def parse(html: str, url: str = "") -> ScreenerData:
    soup = BeautifulSoup(html, "html.parser")
    s = ScreenerData(url=url)
    sec = soup.find(id="profit-loss")
    if sec and re.search(r"Standalone Figures", sec.get_text(" ")):
        s.basis = "Standalone"   # company has no consolidated accounts; Screener shows standalone
    pl, bs = _table(soup, "profit-loss"), _table(soup, "balance-sheet")
    s.sales = pl.get("Sales", pl.get("Revenue", {}))
    s.profit = pl.get("Net Profit", {})

    def last(row):
        if not row:
            return None
        y = max(row)
        return (y, row[y])

    eq, res = bs.get("Equity Capital", {}), bs.get("Reserves", {})
    common = sorted(set(eq) & set(res))
    if common:
        y = common[-1]
        s.net_worth = (y, round(eq[y] + res[y], 2))
    ci = soup.find(id="company-info")
    if ci:
        s.warehouse_id = ci.get("data-warehouse-id") or ci.get("data-company-id")
    h1 = soup.find("h1")
    s.name = h1.get_text(strip=True) if h1 else None
    ab = soup.select_one(".company-profile .about, .about")
    if ab:
        s.about = re.sub(r"\s+", " ", ab.get_text(" ", strip=True))[:900] or None
    for a in soup.select(".company-links a[href^=http]"):
        if not re.search(r"bseindia|nseindia|screener", a["href"]):
            s.website = a["href"]
            break
    s.tables = {"Profit & Loss": pl, "Balance Sheet": bs, "Cash Flow": _table(soup, "cash-flow"), "Ratios": _table(soup, "ratios"),
                "Quarterly results": _table(soup, "quarters")}
    s.shareholding = _table(soup, "shareholding", raw=True, container_id="quarterly-shp") or _table(soup, "shareholding", raw=True)
    for li in soup.select("#top-ratios li"):
        nm, vl = li.select_one(".name"), li.select_one(".value")
        if nm and vl:
            s.top_ratios[nm.get_text(" ", strip=True)] = re.sub(r"\s+", " ", vl.get_text(" ", strip=True)).replace("\u20b9", "Rs.")
    mc = s.top_ratios.get("Market Cap")
    if mc:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", mc)
        s.market_cap_cr = _f(m.group(0)) if m else None
    s.pros = [li.get_text(" ", strip=True) for li in soup.select(".pros li")][:8]
    s.cons = [li.get_text(" ", strip=True) for li in soup.select(".cons li")][:8]
    sec_a, ind_a = soup.select_one('a[title="Sector"]'), soup.select_one('a[title="Industry"]')
    s.sector = sec_a.get_text(strip=True) if sec_a else None
    s.industry = ind_a.get_text(strip=True) if ind_a else None
    s.borrowings = last(bs.get("Borrowings", {}))
    s.investments = last(bs.get("Investments", {}))
    for h in soup.find_all(["h3", "h4", "h5"]):
        if "credit rating" in h.get_text(" ", strip=True).lower():
            ul = h.find_next("ul")
            if ul:
                s.rating_links = [("https://www.screener.in" + a["href"]) if a["href"].startswith("/") else a["href"]
                                  for a in ul.find_all("a", href=True)][:5]
            break
    for h in soup.find_all(["h3", "h4", "h5"]):
        if "annual report" in h.get_text(" ", strip=True).lower():
            ul = h.find_next("ul")
            if ul:
                s.annual_reports = [("https://www.screener.in" + a["href"]) if a["href"].startswith("/") else a["href"]
                                    for a in ul.find_all("a", href=True)][:3]
            break
    return s


def fetch(symbol: str) -> Optional[ScreenerData]:
    url = URL.format(sym=symbol)
    html = get(url)
    if not html or not re.search(r'id="profit-loss"', html):
        return None
    sc = parse(html, url)
    if sc.warehouse_id:
        for parent in ("Other Assets", "Other Liabilities", "Borrowings"):
            try:
                sc.schedules[parent] = _schedule(sc.warehouse_id, parent, sc.basis == "Consolidated")
            except Exception:
                sc.schedules[parent] = {}
        sc.cash = _cash_from(sc.schedules)
    return sc


def _schedule(wid: str, parent: str, consolidated: bool) -> Dict[str, Dict[str, float]]:
    """Screener's expandable balance-sheet rows (Other Assets / Other Liabilities / Borrowings) as {row: {FY: value}}."""
    import json
    from urllib.parse import quote_plus
    raw = get(f"https://www.screener.in/api/company/{wid}/schedules/?parent={quote_plus(parent)}&section=balance-sheet"
              + ("&consolidated=" if consolidated else ""), accept_json=True)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for row, cols in (data.items() if isinstance(data, dict) else []):
        if not isinstance(cols, dict):
            continue
        vals = {}
        for k, v in cols.items():
            m = re.search(r"(\d{4})", k)
            f = _f(str(v))
            if m and f is not None:
                vals[f"FY{m.group(1)}"] = f
        if vals:
            out[row] = vals
    return out


def _cash_from(schedules: Dict[str, Dict[str, Dict[str, float]]]) -> Optional[tuple]:
    row = next((v for k, v in schedules.get("Other Assets", {}).items() if "cash" in k.lower()), None)
    return (max(row), row[max(row)]) if row else None


def search_all(name: str, limit: int = 6) -> List[tuple]:
    """Screener's public company search. Returns [(code, company_name), ...]; tries shorter variants for typos."""
    import json
    from urllib.parse import quote_plus
    words = re.sub(r"\b(limited|ltd|pvt|private)\b\.?", "", name, flags=re.I).split()
    variants = [name] + ([" ".join(words[:2])] if len(words) > 2 else []) + ([words[0]] if len(words) > 1 else [])
    out: List[tuple] = []
    for q in dict.fromkeys(v for v in variants if v.strip()):
        raw = get(f"https://www.screener.in/api/company/search/?q={quote_plus(q)}&v=3&fts=1", accept_json=True)
        try:
            hits = json.loads(raw) if raw else []
        except ValueError:
            hits = []
        for h in hits:
            m = re.search(r"/company/([^/]+)/", h.get("url", ""))
            if m and (m.group(1), h.get("name", "")) not in out:
                out.append((m.group(1), h.get("name", "")))
        if out:
            break
    return out[:limit]


def search(name: str) -> Optional[tuple]:
    hits = search_all(name)
    return hits[0] if hits else None
