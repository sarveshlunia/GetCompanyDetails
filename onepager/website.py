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
}
PLANT_RE = re.compile(r"\b(plant|factory|manufacturing (?:unit|facility|plant)|facility|unit[- ]?[ivx\d]+|works)\b", re.I)


@dataclass
class SiteData:
    url: str
    about: Optional[str] = None
    address: Optional[str] = None
    products: List[str] = field(default_factory=list)
    factories: List[str] = field(default_factory=list)
    pages: List[str] = field(default_factory=list)


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
    if kind == "plants":
        for tag in soup.find_all(["h2", "h3", "h4", "li", "p"]):
            t = _clean(tag.get_text(" "))
            if 8 <= len(t) <= 200 and PLANT_RE.search(t) and t not in out.factories:
                out.factories.append(t)
        out.factories = out.factories[:12]


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
    home = get(site)
    if not home:
        return None
    out = SiteData(url=site, pages=[site])
    parse_page("home", home, site, out)
    for kind, url in _links(home, site).items():
        html = get(url)
        if html:
            out.pages.append(url)
            parse_page(kind, html, url, out)
    return out
