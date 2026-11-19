"""Zauba Corp: authorised capital, paid-up capital, listing status, CIN, address, directors.

Zauba sits behind bot protection and its HTML changes; this is best-effort and returns None when
blocked. The parser is generic (label/value table rows + a DIN table) rather than tied to CSS classes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .http import get, inr_to_cr

SEARCH = "https://www.zaubacorp.com/companysearchresults/{q}"


@dataclass
class ZaubaData:
    url: str
    cin: Optional[str] = None
    listing_status: Optional[str] = None
    authorised_cr: Optional[float] = None
    paid_up_cr: Optional[float] = None
    address: Optional[str] = None
    directors: List[tuple] = field(default_factory=list)  # (name, designation, din)


def parse_company_page(html: str, url: str = "") -> ZaubaData:
    soup = BeautifulSoup(html, "html.parser")
    z = ZaubaData(url=url)
    kv = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) == 2:
            kv[re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).lower().rstrip(":")] = cells[1].get_text(" ", strip=True)
    for k, v in kv.items():
        if k in ("cin", "corporate identification number") and re.fullmatch(r"[A-Z]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}", v.strip()):
            z.cin = v.strip()
        elif k.startswith("authorised capital") or k.startswith("authorized capital"):
            z.authorised_cr = inr_to_cr(v)
        elif k.startswith("paid up capital") or k.startswith("paid-up capital"):
            z.paid_up_cr = inr_to_cr(v)
        elif "listing status" in k or k == "listed in stock exchange":
            z.listing_status = v.strip()
        elif k in ("registered address", "registered office") or k.startswith("registered address"):
            z.address = v.strip()
    # directors table: header contains DIN + Name
    for table in soup.find_all("table"):
        heads = [h.get_text(" ", strip=True).lower() for h in table.find_all("th")]
        if any("din" in h for h in heads) and any("name" in h for h in heads):
            idx = {h: i for i, h in enumerate(heads)}
            din_i = next(i for h, i in idx.items() if "din" in h)
            name_i = next(i for h, i in idx.items() if "name" in h)
            des_i = next((i for h, i in idx.items() if "designation" in h), None)
            for tr in table.find_all("tr")[1:]:
                td = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(td) > max(din_i, name_i):
                    z.directors.append((td[name_i], td[des_i] if des_i is not None and des_i < len(td) else None, td[din_i]))
            break
    return z


def fetch(name: str) -> Optional[ZaubaData]:
    html = get(SEARCH.format(q=quote_plus(name)))
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    link = next((a["href"] for a in soup.find_all("a", href=True) if "/company/" in a["href"]), None)
    if not link:
        return None
    url = link if link.startswith("http") else "https://www.zaubacorp.com" + link
    page = get(url)
    return parse_company_page(page, url) if page else None
