"""Tiny polite HTTP helper shared by the scrapers. Returns None on any failure (blocked, timeout, 4xx/5xx)."""

from __future__ import annotations

import re
import threading
from typing import List, Optional

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


_tl = threading.local()


def log(msg: str) -> None:
    if not hasattr(_tl, "lines"):
        _tl.lines = []
    _tl.lines.append(msg)


def drain() -> List[str]:
    """Return and clear this thread's log (one company = one thread of work)."""
    lines, _tl.lines = getattr(_tl, "lines", []), []
    return lines


def get(url: str, timeout: int = 15, accept_json: bool = False) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
                                       **({"Accept": "application/json"} if accept_json else {})}, timeout=timeout)
        if r.status_code == 200 and r.text:
            log(f"OK   200 {url}")
            return r.text
        log(f"FAIL {r.status_code} {url}" + (" (blocked by site?)" if r.status_code in (403, 429, 503) else ""))
    except requests.RequestException as e:
        log(f"FAIL {type(e).__name__} {url}")
    return None


def inr_to_cr(text: str) -> Optional[float]:
    """'₹ 5,00,00,000' -> 5.0 ; 'Rs. 10 Crore' -> 10.0 ; '25 Lakh' -> 0.25"""
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    t = text.lower()
    if "crore" in t or re.search(r"\bcr\b", t):
        return round(v, 2)
    if "lakh" in t or "lac" in t:
        return round(v / 100, 4)
    return round(v / 1e7, 4)
