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
    s.borrowings = last(bs.get("Borrowings", {}))
    s.investments = last(bs.get("Investments", {}))
    for h in soup.find_all(["h3", "h4", "h5"]):
        if "credit rating" in h.get_text(" ", strip=True).lower():
            ul = h.find_next("ul")
            if ul:
                s.rating_links = [("https://www.screener.in" + a["href"]) if a["href"].startswith("/") else a["href"]
                                  for a in ul.find_all("a", href=True)][:5]
            break
    return s


def fetch(symbol: str) -> Optional[ScreenerData]:
    url = URL.format(sym=symbol)
    html = get(url)
    if not html or not re.search(r'id="profit-loss"', html):
        return None
    return parse(html, url)
