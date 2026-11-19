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


def get_browser(url: str, timeout_ms: int = 25000) -> Optional[str]:
    """Optional: real headless Chromium (passes some bot checks that plain requests can't).
    Only used if `playwright` + its browser are installed (run ENABLE_BROWSER_MODE)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            page = b.new_page(user_agent=UA, locale="en-IN")
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            html = page.content()
            b.close()
        if re.search(r"just a moment|verify you are human|attention required", html, re.I) and len(html) < 20000:
            log(f"FAIL browser challenge page {url}")
            return None
        log(f"OK   browser {url}")
        return html
    except Exception as e:
        log(f"FAIL browser {type(e).__name__} {url}")
        return None
