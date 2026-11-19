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
    kind: str = "loose"             # statement (read from Balance Sheet / P&L) | loose (highlights table) | api


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


def _label_nums(line: str):
    """Split 'Revenue from operations 24 1,53,670 1,46,767' into ('revenue from operations', [24, 153670, 146767])."""
    m = re.search(r"\s\(?-?\d[\d,]*(?:\.\d+)?\)?%?(?:\s|$)", " " + line)
    if not m:
        return None, []
    cut = max(m.start() - 1, 0)
    label = line[:cut].strip(" :-–—|")
    return label, numbers_after(line, cut)


_ENUM = re.compile(r"^(?:\(?[a-z]{1,2}\)|\(?[ivx]{1,4}\)|[ivx]{1,4}[\.\)]?|[a-z][\.\)]|\d{1,2}[\.\)])\s+", re.I)
_BORROW = re.compile(r"^(?:borrowings?|long[- ]term borrowings?|short[- ]term borrowings?|current maturities of long[- ]term (?:debt|borrowings?)|"
                     r"debt securities|subordinated liabilities|non[- ]current borrowings?|current borrowings?)\b", re.I)


def parse_statements(lines: List[str], source: str, url: str, page_label: str = "") -> List[Cand]:
    """Strict reader for the consolidated Balance Sheet and Statement of Profit & Loss.

    Needs the statement heading, a year header and a unit; tracks the section (BS / P&L / cash-flow) and the
    sub-block (non-current assets, current assets, liabilities, equity) so that 'Investments' and 'Borrowings'
    land in the right place. Builds:
      net worth = equity share capital + other equity   (else equity attributable to owners, else total equity)
      debt      = total borrowings, else the sum of the borrowing lines (non-current + current + current maturities)
      investments = 'Investments' under non-current assets / under current assets
      cash        = 'Cash and cash equivalents' under current assets;  revenue / PAT from the P&L.
    """
    section = ctx = None
    years: List[str] = []
    unit: Optional[float] = None
    basis = "Unspecified"
    rows: Dict[tuple, tuple] = {}          # (key, fy) -> (value_in_file_units, line)
    borrow: Dict[str, Dict[str, tuple]] = {}   # fy -> {label+ctx: (value, line)}

    def put(key, fy, val, line):
        rows.setdefault((key, fy), (val, line))

    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        low = line.lower()
        short = len(line) < 130
        if short:
            if "consolidated" in low:
                basis = "Consolidated"
            elif "standalone" in low or "stand-alone" in low:
                basis = "Standalone"
            if re.search(r"balance sheet", low) and not re.search(r"\bnote|\bnotes", low):
                section, ctx, years = "BS", None, []
            elif re.search(r"statement of profit|profit and loss|income statement|statement of income", low) and not re.search(r"\bnote", low):
                section, ctx, years = "PL", None, []
            elif "cash flow" in low:
                section = "CF"
            elif "changes in equity" in low:
                section = "OTHER"
            if section == "BS":
                if re.match(r"^non[- ]current assets", low): ctx = "NCA"
                elif re.match(r"^current assets", low): ctx = "CA"
                elif re.match(r"^non[- ]current liabilities", low): ctx = "NCL"
                elif re.match(r"^current liabilities", low): ctx = "CL"
                elif re.match(r"^(equity|shareholders|total assets)\b", low) and "liabilities" not in low[:12]: ctx = "EQ"
                elif re.match(r"^equity and liabilities|^liabilities$", low): ctx = "EQ"
        u = unit_in(line) if len(line) < 220 else None
        if u is not None and not re.search(r"\d[\d,]{3,}", line):
            unit = u
        ys = years_in(line)
        if ys and not re.search(r"\d[\d,]{4,}", re.sub(r"20\d\d", "", line)) and (len(ys) >= 2 or len(line) < 70):
            years = ys
            continue
        if section not in ("BS", "PL") or not years or unit is None:
            continue
        label, nums = _label_nums(line)
        if not label or not nums:
            continue
        lab = _ENUM.sub("", label).strip().lower()
        lab = re.sub(r"\s*\(refer.*$|\s*\(note.*$", "", lab).strip()
        if len(nums) == len(years) + 1 and abs(nums[0]) < 100 and float(nums[0]).is_integer():
            nums = nums[1:]
        n = min(len(nums), len(years))
        if n == 0:
            continue
        cols = list(zip(years[:n], nums[:n]))
        for fy, v in cols:
            if section == "BS":
                if re.match(r"^(equity )?share capital\b", lab) and ctx in ("EQ", None):
                    put("share_capital", fy, v, line)
                elif re.match(r"^other equity\b|^reserves (and|&) surplus\b", lab):
                    put("other_equity", fy, v, line)
                elif re.match(r"^equity attributable to", lab):
                    put("eq_attr", fy, v, line)
                elif re.match(r"^total equity\b", lab) and "liabilities" not in lab:
                    put("total_equity", fy, v, line)
                elif re.match(r"^total borrowings\b", lab):
                    put("total_borrowings", fy, v, line)
                elif _BORROW.match(lab) and "lease" not in lab and ctx not in ("NCA", "CA"):
                    borrow.setdefault(fy, {}).setdefault(f"{lab}|{ctx}", (v, line))
                elif re.match(r"^(non[- ]current )?investments?$", lab) and ctx in ("NCA", None):
                    put("inv_nc", fy, v, line)
                elif re.match(r"^current investments?$", lab) or (lab == "investments" and ctx == "CA"):
                    put("inv_c", fy, v, line)
                elif re.match(r"^cash and cash equivalents\b|^cash and bank balances\b", lab) and ctx in ("CA", None):
                    put("cash", fy, v, line)
            else:  # P&L
                if re.match(r"^revenue from operations\b", lab):
                    put("revenue", fy, v, line)
                elif re.match(r"^total revenue\b|^net sales\b", lab):
                    put("revenue2", fy, v, line)
                elif re.match(r"^(net )?profit\s*(/\s*\(loss\)\s*|\(loss\)\s*)?(for the (year|period)|after tax)\b", lab) and "before" not in lab:
                    put("pat", fy, v, line)

    out: List[Cand] = []
    if unit is None:
        return out
    fys = sorted({fy for (_, fy) in rows} | set(borrow))
    pl = page_label

    def emit(field, fy, val, rank, ev):
        out.append(Cand(field, fy, round(val * unit, 2), source, url, basis, rank, f"{pl}{ev}"[:260], "statement"))

    for fy in fys:
        g = lambda k: rows.get((k, fy))
        if g("share_capital") and g("other_equity"):
            emit("net_worth", fy, g("share_capital")[0] + g("other_equity")[0], 0,
                 f"Share capital + Other equity: [{g('share_capital')[1]}] + [{g('other_equity')[1]}]")
        elif g("eq_attr"):
            emit("net_worth", fy, g("eq_attr")[0], 1, g("eq_attr")[1])
        elif g("total_equity"):
            emit("net_worth", fy, g("total_equity")[0], 2, g("total_equity")[1])
        if g("total_borrowings"):
            emit("debt", fy, g("total_borrowings")[0], 0, g("total_borrowings")[1])
        elif borrow.get(fy):
            parts = borrow[fy].values()
            emit("debt", fy, sum(v for v, _ in parts), 1, f"Sum of {len(borrow[fy])} borrowing lines: " + " + ".join(f"[{l}]" for _, l in parts))
        for key, fld in (("inv_nc", "non_current_investments"), ("inv_c", "current_investments"), ("cash", "cash")):
            if g(key):
                emit(fld, fy, g(key)[0], 0, g(key)[1])
        if g("revenue"):
            emit("revenue", fy, g("revenue")[0], 0, g("revenue")[1])
        elif g("revenue2"):
            emit("revenue", fy, g("revenue2")[0], 1, g("revenue2")[1])
        if g("pat"):
            emit("pat", fy, g("pat")[0], 0, g("pat")[1])
    return out


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
                                    f"{page_label}{line[:200]}", "loose"))
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
    lines = html_lines(html)
    return parse_statements(lines, source, url) + parse_lines(lines, source, url)


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
        lines = text.splitlines()
        cands += parse_statements(lines, source, f"{url}#page={pno}", page_label=f"p.{pno}: ")
        cands += parse_lines(lines, source, f"{url}#page={pno}", page_label=f"p.{pno}: ")
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
