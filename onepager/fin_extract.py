"""Read financial numbers out of the company's own pages and annual-report PDFs.

No LLM: a line-based reader that needs, for every number it accepts,
  (1) a recognised label (e.g. "Revenue from operations"),
  (2) a column-year header it can read (it never guesses which year a column is), and
  (3) a unit phrase (crore / lakh / million / billion) near the table (it never guesses the unit).
Every accepted number keeps the raw line as evidence so the analyst can check it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString

from .http import get_bytes, log


@dataclass
class Cand:
    field: str
    fy: Optional[str]
    value_cr: float
    source: str
    url: str
    basis: str = "Unspecified"      # Consolidated | Standalone | Unspecified
    label_rank: int = 0
    evidence: str = ""


# ---- labels (rank = preference, lower first) ---------------------------------------------------
LABELS: Dict[str, List[str]] = {
    "revenue": [r"revenue from operations", r"total revenue", r"net sales", r"total income", r"turnover"],
    "pat": [r"profit after tax", r"profit for the (?:year|period)", r"net profit", r"\bPAT\b"],
    "net_worth": [r"net worth", r"total equity", r"shareholders[’']? (?:funds|equity)"],
    "debt": [r"total borrowings", r"total debt", r"gross debt"],
    "cash": [r"cash and cash equivalents"],
    "non_current_investments": [r"non[- ]current investments"],
    "current_investments": [r"(?<!non[- ])(?<!non )current investments"],
}
_LABEL_RE = {f: [re.compile(p, re.I) for p in ps] for f, ps in LABELS.items()}
POINT_FIELDS = {"net_worth", "debt", "cash", "non_current_investments", "current_investments"}

_UNITS = [(re.compile(r"crores?|\bcr\b\.?", re.I), 1.0), (re.compile(r"lakhs?|lacs?", re.I), 0.01),
          (re.compile(r"millions?|\bmn\b", re.I), 0.1), (re.compile(r"billions?|\bbn\b", re.I), 100.0)]
_NUM = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?")


def unit_in(line: str) -> Optional[float]:
    best = None
    for rx, f in _UNITS:
        m = rx.search(line)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), f)
    return best[1] if best else None


def years_in(line: str) -> List[str]:
    """Column headers -> ['FY2025', 'FY2024']. Only returns when it can read at least one clear fiscal year."""
    out: List[str] = []
    for m in re.finditer(r"(?:FY|F\.Y\.?)\s?(\d{2,4})\s*[-–/]\s*(\d{2,4})|(?:FY|F\.Y\.?)\s?(\d{2,4})|\b(20\d{2})\s*[-–]\s*(\d{2,4})\b|"
                         r"(?:march|mar)[\s\-,]*(?:31(?:st)?[\s,]*)?'?(\d{2,4})|31(?:st)?\s*(?:march|mar)[\s,]*(\d{2,4})", line, re.I):
        g = m.groups()
        if g[1]:
            y = int(g[1]); y = y + 2000 if y < 100 else y
        elif g[2]:
            y = int(g[2]); y = y + 2000 if y < 100 else y
        elif g[4]:
            y = int(g[4]); y = y + 2000 if y < 100 else y
        elif g[5]:
            y = int(g[5]); y = y + 2000 if y < 100 else y
        elif g[6]:
            y = int(g[6]); y = y + 2000 if y < 100 else y
        else:
            continue
        if 2005 <= y <= 2035:
            out.append(f"FY{y}")
    if not out:  # bare years only when several appear together: "2025 2024 2023"
        bare = [int(x) for x in re.findall(r"\b(20[12]\d)\b", line)]
        if len(bare) >= 2 and len(set(bare)) == len(bare):
            out = [f"FY{y}" for y in bare]
    return out


def numbers_after(line: str, label_end: int) -> List[float]:
    vals = []
    for tok in _NUM.findall(line[label_end:]):
        if tok.endswith("%"):
            continue
        neg = tok.startswith("(") or tok.startswith("-")
        t = tok.strip("()-").replace(",", "")
        try:
            v = float(t)
        except ValueError:
            continue
        vals.append(-v if neg else v)
    return vals


def parse_lines(lines: List[str], source: str, url: str, page_label: str = "") -> List[Cand]:
    out: List[Cand] = []
    cur_years: List[str] = []
    years_at = -999
    cur_unit: Optional[float] = None
    basis = "Unspecified"
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        low = line.lower()
        if len(line) < 140:
            if "consolidated" in low:
                basis = "Consolidated"
            elif "standalone" in low or "stand-alone" in low or "separate financial" in low:
                basis = "Standalone"
        u = unit_in(line) if len(line) < 220 else None
        if u is not None and not re.search(r"\d[\d,]{3,}", line):  # a unit note, not a data row
            cur_unit = u
        ys = years_in(line)
        if len(ys) >= 2 or (ys and len(line) < 60 and not re.search(r"\d[\d,]{4,}", line)):
            cur_years, years_at = ys, i
            continue
        for fld, regs in _LABEL_RE.items():
            for rank, rx in enumerate(regs):
                m = rx.search(line)
                if not m or m.start() > 60:
                    continue
                if fld == "pat" and re.search(r"before\s+tax", low[m.start():m.end() + 25]):
                    continue
                nums = numbers_after(line, m.end())
                if not nums or not cur_years or i - years_at > 120 or cur_unit is None:
                    continue
                if len(nums) == len(cur_years) + 1 and abs(nums[0]) < 100 and float(nums[0]).is_integer():
                    nums = nums[1:]  # note-number column
                n = min(len(nums), len(cur_years))
                if n == 0:
                    continue
                cols = list(zip(cur_years[:n], nums[:n]))
                if fld in POINT_FIELDS:
                    cols = cols[:1] if cur_years[0] >= cur_years[-1] else cols[-1:]
                for fy, v in cols:
                    out.append(Cand(fld, fy, round(v * cur_unit, 2), source, url, basis, rank,
                                    f"{page_label}{line[:200]}"))
                break
    return out


# ---- inputs ------------------------------------------------------------------------------------
def html_lines(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        tr.replace_with(NavigableString("\n" + " ".join(c for c in cells if c) + "\n"))
    return soup.get_text("\n").splitlines()


def from_html(html: str, source: str, url: str) -> List[Cand]:
    return parse_lines(html_lines(html), source, url)


_PAGE_CUES = re.compile(r"balance sheet|statement of profit|profit and loss|financial highlights|key financial|five[- ]year|ten[- ]year|"
                        r"financial performance|financial summary|financial snapshot|at a glance", re.I)


def from_pdf(url: str, source: str = "Annual report PDF", max_pages: int = 450, max_mb: int = 45) -> List[Cand]:
    data = get_bytes(url, max_mb=max_mb)
    if not data:
        return []
    if data[:4] != b"%PDF":  # a landing page, not the file: follow its first annual-report PDF link
        try:
            soup = BeautifulSoup(data.decode("utf-8", "ignore"), "html.parser")
            from urllib.parse import urljoin
            link = next((urljoin(url, a["href"]) for a in soup.find_all("a", href=True) if ".pdf" in a["href"].lower()), None)
        except Exception:
            link = None
        if not link or link == url:
            log(f"SKIP not a PDF and no PDF link inside {url}")
            return []
        url = link
        data = get_bytes(url, max_mb=max_mb)
        if not data or data[:4] != b"%PDF":
            return []
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        log(f"FAIL pdf open {type(e).__name__} {url}")
        return []
    cands: List[Cand] = []
    scanned = 0
    for pno, page in enumerate(reader.pages[:max_pages], 1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if not _PAGE_CUES.search(text):
            continue
        scanned += 1
        cands += parse_lines(text.splitlines(), source, f"{url}#page={pno}", page_label=f"p.{pno}: ")
    log(f"PDF  {url}: {len(reader.pages)} pages, {scanned} financial pages scanned, {len(cands)} candidate numbers")
    return cands


_SNIP_AMT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lac|million|billion|bn|mn)"
_SNIP = {
    "revenue": re.compile(r"(?:revenue|turnover|total income|sales)[^.]{0,80}?" + _SNIP_AMT, re.I),
    "pat": re.compile(r"(?:net profit|profit after tax|PAT|net income)[^.]{0,80}?" + _SNIP_AMT, re.I),
}


def from_snippet(text: str, url: str, source: str = "Search snippet") -> List[Cand]:
    out = []
    fys = years_in(text)
    for fld, rx in _SNIP.items():
        m = rx.search(text)
        if not m:
            continue
        unit = {"crore": 1.0, "cr": 1.0, "lakh": 0.01, "lac": 0.01, "million": 0.1, "mn": 0.1, "billion": 100.0, "bn": 100.0}[m.group(2).lower()]
        try:
            out.append(Cand(fld, fys[0] if fys else None, round(float(m.group(1).replace(",", "")) * unit, 2),
                            source, url, "Unspecified", 9, m.group(0)[:200]))
        except ValueError:
            pass
    return out
