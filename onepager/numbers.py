"""Pick each financial number and cross-verify it against Screener.

Method (as requested):
  1. Take the figure from the company's own Balance Sheet / P&L (annual report PDF or website statements),
     consolidated preferred. Looser "highlights" tables only if no statement figure exists for that year.
  2. Cross-verify with Screener (consolidated). If they agree within a tolerance the statement figure is
     shown (exact, from the filing) and marked verified. If they disagree, Screener's figure is shown,
     the statement figure is kept in the audit table, and the metric is flagged.
  3. If only one source has the number it is shown and marked 'not cross-checked'.
Priority when sources are not Screener: company website / annual report, then Yahoo, then search snippets.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .fin_extract import Cand, POINT_FIELDS
from .schema import Amount, CompanyOnePager, YearFigure

SOURCE_RANK = {"Company website": 1, "Annual report PDF": 1, "Screener": 2, "Yahoo Finance": 3, "Search snippet": 4}
FIELD_ATTR = {"revenue": "revenue", "pat": "pat", "net_worth": "net_worth", "debt": "debt", "cash": "cash_and_equivalents",
              "non_current_investments": "non_current_investments", "current_investments": "current_investments"}
LABEL = {"revenue": "Revenue", "pat": "PAT", "net_worth": "Net worth", "debt": "Debt", "cash": "Cash & equivalents",
         "non_current_investments": "Non-current investments", "current_investments": "Current investments"}
# how far two sources may differ before we call it a mismatch (definitions differ a little: NCI, lease liabilities...)
TOL = {"revenue": 0.03, "pat": 0.10, "net_worth": 0.10, "debt": 0.12, "cash": 0.15,
       "non_current_investments": 0.15, "current_investments": 0.15}


def _key(c: Cand):
    return (1 if c.basis == "Standalone" else 0, SOURCE_RANK.get(c.source, 5), 0 if c.kind == "statement" else 1, c.label_rank)


def _fy_num(fy: Optional[str]) -> int:
    try:
        return int(fy[2:])
    except Exception:
        return 0


def _basis(c: Cand) -> str:
    return c.basis if c.basis != "Unspecified" else "Basis not stated"


def _decide(field: str, other: Optional[Cand], scr: Optional[Cand], d: CompanyOnePager):
    """Return (chosen candidate, verify text)."""
    if other is None and scr is None:
        return None, None
    if other is None:
        return scr, "Screener only (no company-statement figure found)"
    if scr is None:
        note = "not cross-checked (Screener has no figure for this)" if other.kind == "statement" else "NOT verified (loose table, no Screener figure)"
        return other, note
    diff = abs(other.value_cr - scr.value_cr) / max(abs(scr.value_cr), 1e-9)
    ok = diff <= TOL[field] or abs(other.value_cr - scr.value_cr) < 1.0
    d.verification.append({"metric": LABEL[field], "FY": other.fy, "statement / company": other.value_cr, "company source": other.source,
                           "Screener": scr.value_cr, "difference %": round(diff * 100, 1),
                           "status": "MATCH" if ok else "MISMATCH", "shown": "company" if ok else "Screener",
                           "evidence": other.evidence})
    if ok:
        return other, f"✔ matches Screener ({diff:.1%} apart)"
    d.data_gaps.append(f"MISMATCH {LABEL[field]} {other.fy or ''}: {other.source} reads Rs. {other.value_cr:,.2f} Cr but Screener has "
                       f"Rs. {scr.value_cr:,.2f} Cr ({diff:.0%} apart) - showing Screener; check the filing.")
    return scr, f"⚠ {other.source} read Rs. {other.value_cr:,.2f} Cr ({diff:.0%} apart) - Screener shown"


def _best(items: List[Cand]) -> Optional[Cand]:
    return min(items, key=_key) if items else None


def resolve(cands: List[Cand], d: CompanyOnePager) -> None:
    d.verification = []
    by_field: Dict[str, List[Cand]] = {}
    for c in cands:
        by_field.setdefault(c.field, []).append(c)
    chosen_all: Dict[str, List[Cand]] = {}
    for field, items in by_field.items():
        attr = FIELD_ATTR[field]
        stmt_fy = {c.fy for c in items if c.kind == "statement"}
        items = [c for c in items if not (c.kind == "loose" and c.source != "Screener" and c.fy in stmt_fy)]   # statements beat highlight tables
        if field in POINT_FIELDS:
            latest = max((_fy_num(c.fy) for c in items), default=0)
            fy = next((c.fy for c in sorted(items, key=lambda c: -_fy_num(c.fy)) if _fy_num(c.fy) >= latest - 1 and
                       any(o.fy == c.fy and o.source != "Screener" for o in items)), None) or \
                 max((c.fy for c in items), key=_fy_num, default=None)
            group = [c for c in items if c.fy == fy] or items
            other = _best([c for c in group if c.source != "Screener"])
            scr = _best([c for c in group if c.source == "Screener"])
            if scr is None and other is not None:  # Screener may have a different latest FY; compare same FY only
                scr = next((c for c in items if c.source == "Screener" and c.fy == other.fy), None)
            best, verify = _decide(field, other, scr, d)
            if best:
                setattr(d, attr, Amount(value_cr=best.value_cr, period=best.fy, basis=_basis(best), source=best.source,
                                        source_url=best.url, evidence=best.evidence or None, verify=verify))
                d.field_sources[attr], d.field_urls[attr] = best.source, best.url
                chosen_all[field] = [best]
        else:
            fys = sorted({c.fy for c in items if c.fy}, key=_fy_num, reverse=True)[:3]
            series, chosen = [], []
            for fy in fys:
                group = [c for c in items if c.fy == fy]
                other = _best([c for c in group if c.source != "Screener"])
                scr = _best([c for c in group if c.source == "Screener"])
                best, verify = _decide(field, other, scr, d)
                if best:
                    chosen.append(best)
                    series.append(YearFigure(fiscal_year=fy, value_cr=best.value_cr, basis=_basis(best), source=best.source,
                                             source_url=best.url, evidence=best.evidence or None, verify=verify))
            if series:
                setattr(d, attr, series)
                d.field_sources[attr] = "/".join(dict.fromkeys(y.source for y in series if y.source))
                d.field_urls[attr] = series[0].source_url
                chosen_all[field] = chosen
            if any(y.basis == "Standalone" for y in series):
                d.data_gaps.append(f"{LABEL[field]}: some years are STANDALONE only (no consolidated figure found) - verify.")
    # audit trail
    for field, items in by_field.items():
        used = {c.url for c in chosen_all.get(field, [])}
        seen = set()
        for c in sorted(items, key=_key):
            k = (c.source, c.url.split("#")[0], c.fy)
            if k in seen:
                continue
            seen.add(k)
            d.trail.append({"field": LABEL[field], "source": c.source, "url": c.url,
                            "result": ("USED: " if c.url in used else "found, not used: ")
                                      + f"{c.fy or ''} Rs. {c.value_cr:,.2f} Cr [{_basis(c)}]"
                                      + (f" - \"{c.evidence}\"" if c.evidence else "")})
