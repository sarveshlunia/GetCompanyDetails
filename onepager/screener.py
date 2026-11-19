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


def _f(s: str) -> Optional[float]:
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _table(soup, section_id) -> Dict[str, Dict[str, float]]:
    sec = soup.find(id=section_id)
    t = sec.find("table") if sec else None
    if not t:
        return {}
    heads = [th.get_text(strip=True) for th in t.find("thead").find_all("th")][1:]
    fy = [None if h.startswith("TTM") else f"FY{h.split()[-1]}" for h in heads]  # 'Mar 2025' -> FY2025
    rows = {}
    for tr in t.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = re.sub(r"[\s +]+$", "", tds[0].get_text(" ", strip=True)).strip()
        vals = {}
        for y, td in zip(fy, tds[1:]):
            v = _f(td.get_text(strip=True))
            if y and v is not None:
                vals[y] = v
        rows[label] = vals
    return rows


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
        sc.cash = _cash(sc.warehouse_id)
    return sc


def _cash(wid: str) -> Optional[tuple]:
    """Screener's expandable 'Other Assets' schedule has a 'Cash Equivalents' row (consolidated)."""
    import json
    raw = get(f"https://www.screener.in/api/company/{wid}/schedules/?parent=Other+Assets&section=balance-sheet&consolidated=",
              accept_json=True)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return None
    row = next((v for k, v in data.items() if "cash" in k.lower()), None)
    if not isinstance(row, dict):
        return None
    vals = {}
    for k, v in row.items():
        m = re.search(r"(\d{4})", k)
        f = _f(str(v))
        if m and f is not None:
            vals[f"FY{m.group(1)}"] = f
    return (max(vals), vals[max(vals)]) if vals else None


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
