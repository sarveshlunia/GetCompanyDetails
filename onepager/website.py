"""Company website: about, address, products/services, factories. Heuristic, no LLM.

Quality depends on how standard the site is. Whatever can't be found is returned empty and
reported as a gap rather than invented.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http import get

BLOCKED = ("linkedin.", "wikipedia.", "zaubacorp.", "tofler.", "screener.", "moneycontrol.", "economictimes.",
           "facebook.", "twitter.", "instagram.", "indiamart.", "justdial.", "glassdoor.", "ambitionbox.",
           "crunchbase.", "youtube.", "bloomberg.", "reuters.", "business-standard.", "livemint.", "naukri.",
           "thecompanycheck.", "dnb.", "mca.gov.in", "bseindia.", "nseindia.", "cleartax.", "quickcompany.")
PAGE_KEYS = {
    "about": ("about", "who-we-are", "company", "overview", "profile"),
    "contact": ("contact", "locat", "reach-us", "address"),
    "products": ("product", "service", "solution", "offering", "business", "portfolio"),
    "plants": ("plant", "factory", "factories", "manufactur", "facilit", "infrastructure", "operations"),
    "board": ("board-of-director", "board_of_director", "board of director", "directors", "leadership", "governance", "management-team", "our-team"),
    "group": ("subsidiar", "group-compan", "group compan", "our-group", "associate", "joint-venture", "group-structure"),
    "investor": ("investor", "annual-report", "annual report", "shareholder"),
    "financials": ("financial", "results", "highlights", "key-figures", "at-a-glance", "performance"),
}
TITLE_RE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr|Prof|Shri|Smt|Sri)\.?\s+([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,3})")
ENTITY_RE = re.compile(r"\b(Ltd|Limited|Pvt|Private|LLP|Inc|Corporation|Corp|GmbH|PLC|Industries|Enterprises)\b\.?", re.I)
PLANT_RE = re.compile(r"\b(plant|factory|manufacturing (?:unit|facility|plant)|facility|unit[- ]?[ivx\d]+|works)\b", re.I)


@dataclass
class SiteData:
    url: str
    about: Optional[str] = None
    address: Optional[str] = None
    products: List[str] = field(default_factory=list)
    factories: List[str] = field(default_factory=list)
    pages: List[str] = field(default_factory=list)
    directors: List[tuple] = field(default_factory=list)   # (name, designation)
    group: List[str] = field(default_factory=list)
    annual_reports: List[str] = field(default_factory=list)
    page_urls: dict = field(default_factory=dict)          # kind -> url
    fin_html: List[tuple] = field(default_factory=list)    # (url, html) of investor/financial pages


def pick_site(results: List[dict]) -> Optional[str]:
    for r in results:
        u = r.get("href", "")
        host = urlparse(u).netloc.lower()
        if host and not any(b in host for b in BLOCKED):
            return f"{urlparse(u).scheme}://{host}/"
    return None


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _address_from(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            a = item.get("address") if isinstance(item, dict) else None
            if isinstance(a, dict):
                parts = [a.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")]
                s = ", ".join(str(p) for p in parts if p and not isinstance(p, dict))
                if s:
                    return s
    text = _clean(soup.get_text(" "))
    m = re.search(r"(?:registered|corporate|head|regd\.?)\s*(?:&\s*corporate\s*)?(?:office|address)\s*[:\-–]?\s*(.{30,260}?\b\d{6}\b)", text, re.I)
    if m:
        return _clean(m.group(1))
    m = re.search(r"([A-Z][^.|]{20,200}?\b\d{3}\s?\d{3}\b)", text)  # any line ending in a 6-digit PIN
    return _clean(m.group(1)) if m else None


def parse_page(kind: str, html: str, base: str, out: SiteData) -> None:
    soup = BeautifulSoup(html, "html.parser")
    if kind in ("home", "about") and not out.about:
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        paras = [_clean(p.get_text(" ")) for p in soup.find_all("p")]
        paras = [p for p in paras if len(p) > 80]
        text = " ".join(paras[:3]) if kind == "about" and paras else (meta.get("content", "") if meta else "")
        if len(text) < 60 and paras:
            text = " ".join(paras[:3])
        out.about = text[:900] or None
    if kind in ("home", "contact") and not out.address:
        out.address = _address_from(soup)
    if kind == "products":
        for tag in soup.find_all(["h2", "h3", "h4", "li"]):
            t = _clean(tag.get_text(" "))
            if 3 <= len(t) <= 60 and not re.search(r"home|contact|about|career|login|privacy|read more|menu|search|©", t, re.I) \
                    and t not in out.products:
                out.products.append(t)
        out.products = out.products[:25]
    if kind == "board":
        _board(soup, out)
    if kind == "group":
        _group(soup, out)
    if kind in ("investor", "about", "home"):
        _annual(soup, base, out)
    if kind == "plants":
        for tag in soup.find_all(["h2", "h3", "h4", "li", "p"]):
            t = _clean(tag.get_text(" "))
            if 8 <= len(t) <= 200 and PLANT_RE.search(t) and t not in out.factories:
                out.factories.append(t)
        out.factories = out.factories[:12]


def _board(soup: BeautifulSoup, out: SiteData) -> None:
    seen = {n for n, _ in out.directors}
    for tag in soup.find_all(["h2", "h3", "h4", "h5", "strong", "b", "p", "li", "span", "div"]):
        if tag.find(["h2", "h3", "h4", "h5", "li", "p", "div"]):
            continue  # only leaf-ish elements
        t = _clean(tag.get_text(" "))
        if not (6 <= len(t) <= 160):
            continue
        m = TITLE_RE.search(t)
        if not m:
            continue
        name = m.group(1).strip(" .,-")
        if name in seen:
            continue
        rest = _clean(t[m.end():].strip(" -,:|"))
        if not rest:
            sib = tag.find_next(string=lambda x: x and x.strip() and x.strip() != t)
            rest = _clean(sib)[:80] if sib else ""
        des = rest if 2 <= len(rest) <= 80 and not TITLE_RE.search(rest) else None
        seen.add(name)
        out.directors.append((name, des))
    out.directors = out.directors[:15]


def _group(soup: BeautifulSoup, out: SiteData) -> None:
    for tag in soup.find_all(["li", "td", "h4", "h5", "p"]):
        t = _clean(tag.get_text(" "))
        if 4 <= len(t) <= 90 and ENTITY_RE.search(t) and t not in out.group:
            out.group.append(t)
    out.group = out.group[:30]


def _annual(soup: BeautifulSoup, base: str, out: SiteData) -> None:
    for a in soup.find_all("a", href=True):
        txt = (a.get_text(" ") + " " + a["href"]).lower()
        if "annual" in txt and "report" in txt:
            u = urljoin(base, a["href"])
            if u not in out.annual_reports:
                out.annual_reports.append(u)
    out.annual_reports = out.annual_reports[:4]


def _links(html: str, base: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    host = urlparse(base).netloc
    for a in soup.find_all("a", href=True):
        u = urljoin(base, a["href"]).split("#")[0]
        if urlparse(u).netloc != host:
            continue
        key = (u + " " + a.get_text(" ")).lower()
        for kind, words in PAGE_KEYS.items():
            if kind not in found and any(w in key for w in words):
                found[kind] = u
    return found


def fetch(site: str) -> Optional[SiteData]:
    if urlparse(site).path == "":
        site += "/"
    home = get(site)
    if not home:
        return None
    out = SiteData(url=site, pages=[site])
    parse_page("home", home, site, out)
    out.page_urls["home"] = site
    queue = list(_links(home, site).items())
    done = set()
    fetched = 0
    while queue and fetched < 10:
        kind, url = queue.pop(0)
        if kind in done or url == site:
            continue
        done.add(kind)
        html = get(url)
        if not html:
            continue
        fetched += 1
        out.pages.append(url)
        out.page_urls[kind] = url
        if kind in ("investor", "financials"):
            out.fin_html.append((url, html))
        parse_page(kind, html, url, out)
        # second level: pages like About / Investors often link to Board, Subsidiaries, Plants
        for k2, u2 in _links(html, url).items():
            if k2 not in done and k2 not in dict(queue):
                queue.append((k2, u2))
    return out
