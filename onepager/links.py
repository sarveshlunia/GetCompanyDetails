"""Ready-made search/verification links per field, so the analyst can finish a gap in one click."""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import quote_plus


def _g(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


def search_links(name: str, person: Optional[str] = None, symbol: Optional[str] = None,
                 website: Optional[str] = None) -> Dict[str, str]:
    n = f'"{name}"'
    sym = (symbol or "").split(".")[0]
    L: Dict[str, str] = {
        "Official website": _g(f"{n} official website"),
        "About / address (Google)": _g(f"{n} about registered office address"),
        "Zauba Corp (capital, directors)": f"https://www.zaubacorp.com/companysearchresults/{quote_plus(name)}",
        "Tofler (financials, directors)": f"https://www.tofler.in/search?q={quote_plus(name)}",
        "Authorised / paid-up capital": _g(f"{n} authorised capital paid up capital"),
        "Directors": _g(f"{n} board of directors DIN"),
        "Credit rating - CRISIL": _g(f"{n} CRISIL rating rationale"),
        "Credit rating - ICRA": _g(f"{n} ICRA rating rationale"),
        "Credit rating - CARE": _g(f"{n} CARE ratings rationale"),
        "Credit rating - India Ratings / Acuite / Infomerics": _g(f"{n} India Ratings OR Acuite OR Infomerics OR Brickwork rating"),
        "Products & services": _g(f"{n} products and services"),
        "Factories / plants": _g(f"{n} manufacturing plants locations"),
        "Group companies / subsidiaries": _g(f"{n} subsidiaries group companies"),
        "Annual report": _g(f"{n} annual report filetype:pdf"),
        "MCA master data (official, needs captcha)": "https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html",
        "Wikipedia": "https://en.wikipedia.org/w/index.php?search=" + quote_plus(name),
        "Company on LinkedIn": "https://www.linkedin.com/search/results/companies/?keywords=" + quote_plus(name),
    }
    if sym:
        L["Screener (consolidated financials)"] = f"https://www.screener.in/company/{sym}/consolidated/"
        L["NSE quote & filings"] = f"https://www.nseindia.com/get-quotes/equity?symbol={sym}"
        L["BSE corporate filings"] = "https://www.bseindia.com/corporates/ann.html"
    else:
        L["Screener (if listed)"] = _g(f"{n} site:screener.in")
    if website:
        host = website.split("//")[-1].strip("/")
        L["Site search: board / leadership"] = _g(f"site:{host} board of directors OR leadership")
        L["Site search: subsidiaries"] = _g(f"site:{host} subsidiaries OR group companies")
        L["Site search: plants"] = _g(f"site:{host} plant OR manufacturing OR facilities")
        L["Site search: annual report"] = _g(f"site:{host} annual report")
    if person:
        L["Person on LinkedIn"] = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{person} {name}")
        L["Person (Google)"] = _g(f'"{person}" "{name}" LinkedIn')
    return L
