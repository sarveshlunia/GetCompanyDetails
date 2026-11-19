"""Pick each financial number by source priority and keep an audit trail.

Priority (as requested): 1 company's own documents (website pages, annual-report PDFs)
                         2 Screener (consolidated)
                         3 other sources (Yahoo Finance, search-snippet quotes)
Consolidated beats standalone whatever the source. Conflicts between sources are flagged.
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
CONFLICT = 0.15  # flag if two sources differ by more than 15%


def _key(c: Cand):
    return (1 if c.basis == "Standalone" else 0, SOURCE_RANK.get(c.source, 5), c.label_rank)


def _fy_num(fy: Optional[str]) -> int:
    try:
        return int(fy[2:])
    except Exception:
        return 0


def _basis(c: Cand) -> str:
    return c.basis if c.basis != "Unspecified" else "Basis not stated"


def _conflicts(field: str, fy: Optional[str], group: List[Cand], chosen: Cand, d: CompanyOnePager) -> None:
    for o in group:
        if o.source != chosen.source and chosen.value_cr and abs(o.value_cr - chosen.value_cr) / abs(chosen.value_cr) > CONFLICT:
            d.data_gaps.append(f"CHECK {LABEL[field]} {fy or ''}: {chosen.source} says Rs. {chosen.value_cr:,.2f} Cr "
                               f"but {o.source} says Rs. {o.value_cr:,.2f} Cr (using {chosen.source}).")
            return


def resolve(cands: List[Cand], d: CompanyOnePager) -> None:
    by_field: Dict[str, List[Cand]] = {}
    for c in cands:
        by_field.setdefault(c.field, []).append(c)
    for field, items in by_field.items():
        attr = FIELD_ATTR[field]
        if field in POINT_FIELDS:
            latest = max((_fy_num(c.fy) for c in items), default=0)
            pool = [c for c in items if _fy_num(c.fy) >= latest - 1] or items  # recent only
            best = min(pool, key=lambda c: (_key(c), -_fy_num(c.fy)))
            same = [c for c in pool if c.fy == best.fy]
            setattr(d, attr, Amount(value_cr=best.value_cr, period=best.fy, basis=_basis(best), source=best.source,
                                    source_url=best.url, evidence=best.evidence or None))
            _conflicts(field, best.fy, same, best, d)
            d.field_sources[attr], d.field_urls[attr] = best.source, best.url
        else:
            fys = sorted({c.fy for c in items if c.fy}, key=_fy_num, reverse=True)[:3]
            series = []
            for fy in fys:
                group = [c for c in items if c.fy == fy]
                best = min(group, key=_key)
                series.append(YearFigure(fiscal_year=fy, value_cr=best.value_cr, basis=_basis(best), source=best.source,
                                         source_url=best.url, evidence=best.evidence or None))
                _conflicts(field, fy, group, best, d)
            if series:
                setattr(d, attr, series)
                top = series[0]
                d.field_sources[attr] = "/".join(dict.fromkeys(y.source for y in series if y.source))
                d.field_urls[attr] = top.source_url
            if any(y.basis == "Standalone" for y in series):
                d.data_gaps.append(f"{LABEL[field]}: some years are STANDALONE only (no consolidated figure found) - verify.")
    # audit trail: every source that produced a number, and whether it was used
    for field, items in by_field.items():
        used = getattr(d, FIELD_ATTR[field])
        used_urls = {y.source_url for y in used} if isinstance(used, list) else {used.source_url}
        seen = set()
        for c in sorted(items, key=_key):
            k = (c.source, c.url.split("#")[0])
            if k in seen:
                continue
            seen.add(k)
            d.trail.append({"field": LABEL[field], "source": c.source, "url": c.url,
                            "result": ("USED: " if c.url in used_urls else "found, not used (lower priority): ")
                                      + f"{c.fy or ''} Rs. {c.value_cr:,.2f} Cr [{_basis(c)}]"
                                      + (f" - \"{c.evidence}\"" if c.evidence else "")})
