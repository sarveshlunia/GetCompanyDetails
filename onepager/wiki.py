"""Wikipedia + Wikidata (open APIs, no bot wall): description, parent company, subsidiaries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

from .http import get

WP = "https://en.wikipedia.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"


@dataclass
class WikiData:
    title: str
    url: str
    about: Optional[str] = None
    parent: List[str] = field(default_factory=list)
    subsidiaries: List[str] = field(default_factory=list)


def _json(url: str):
    raw = get(url, accept_json=True)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _ids(claims: dict, prop: str, limit: int = 25) -> List[str]:
    out = []
    for c in claims.get(prop, []):
        v = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
    return out[:limit]


def fetch(name: str, accept_title=None) -> Optional[WikiData]:
    q = _json(f"{WP}?action=query&list=search&srsearch={quote_plus(name + ' India company')}&srlimit=3&format=json")
    hits = (q or {}).get("query", {}).get("search", [])
    title = next((h["title"] for h in hits if accept_title is None or accept_title(h["title"])), None)
    if not title:
        return None
    p = _json(f"{WP}?action=query&prop=extracts|pageprops&exintro=1&explaintext=1&redirects=1&titles={quote_plus(title)}&format=json")
    pages = list(((p or {}).get("query", {}).get("pages", {})).values())
    if not pages:
        return None
    pg = pages[0]
    w = WikiData(title=title, url="https://en.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_")),
                 about=(pg.get("extract") or "").strip()[:900] or None)
    qid = (pg.get("pageprops") or {}).get("wikibase_item")
    if qid:
        ent = _json(f"{WD}?action=wbgetentities&ids={qid}&props=claims&format=json")
        claims = ((ent or {}).get("entities", {}).get(qid, {}) or {}).get("claims", {})
        parent_ids, sub_ids = _ids(claims, "P749"), _ids(claims, "P355")
        ids = parent_ids + sub_ids
        if ids:
            lab = _json(f"{WD}?action=wbgetentities&ids={'|'.join(ids)}&props=labels&languages=en&format=json")
            ents = (lab or {}).get("entities", {})
            name_of = lambda i: ((ents.get(i, {}).get("labels", {}) or {}).get("en", {}) or {}).get("value")
            w.parent = [n for n in map(name_of, parent_ids) if n]
            w.subsidiaries = [n for n in map(name_of, sub_ids) if n]
    return w
