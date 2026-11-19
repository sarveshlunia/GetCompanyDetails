"""Render CompanyOnePager results into the client's Word template, and an Excel summary."""

from __future__ import annotations

import copy
import io
import re
import zipfile
from pathlib import Path
from typing import Iterable, List

import docx
from docx.document import Document as DocType
from openpyxl import Workbook

from .schema import Amount, CompanyOnePager, YearFigure

TEMPLATE = Path(__file__).parent / "assets" / "One_Pager_Format.docx"
NA = "Not available"


def _cr(a: Amount) -> str:
    if a.value_cr is None:
        return f"Rs. {NA}"
    extra = ", ".join(x for x in (a.basis, a.period) if x)
    return f"Rs. {a.value_cr:,.2f} Cr" + (f" ({extra})" if extra else "")


def _years(items: List[YearFigure], want: Iterable[str] = ()) -> List[str]:
    lines = []
    for y in sorted(items, key=lambda y: y.fiscal_year, reverse=True)[:3]:
        v = "Not available" if y.value_cr is None else f"Rs. {y.value_cr:,.2f} Cr"
        lines.append(f"{y.fiscal_year}: {v}" + (f" ({y.basis})" if y.basis else ""))
    return lines or [NA]


def _rating(r) -> str:
    if not r.rating:
        return NA
    return r.rating + (f" - {r.agency}" if r.agency else "") + (f" ({r.date})" if r.date else "")


def _set_cell(cell, lines: List[str]) -> None:
    """Replace cell text with one paragraph per line, keeping the first run's formatting."""
    lines = [l for l in lines if l is not None] or [NA]
    p0 = cell.paragraphs[0]
    base_run = next((r for r in p0.runs if r.text.strip()), p0.runs[0] if p0.runs else None)
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    for r in p0.runs:
        r._element.getparent().remove(r._element)

    def write(p, text):
        run = p.add_run(text)
        if base_run is not None and base_run._element.rPr is not None:
            run._element.insert(0, copy.deepcopy(base_run._element.rPr))

    write(p0, lines[0])
    for line in lines[1:]:
        write(cell.add_paragraph(), line)


def _fields(d: CompanyOnePager) -> dict:
    mp = d.meetup_person
    return {
        "about": [d.about or NA],
        "corporate address": [d.corporate_address or NA],
        "listed / unlisted": [d.listed_status or NA],
        "market cap": [f"Authorised Capital: {_cr(d.authorised_capital)}", f"Paid-up Capital: {_cr(d.paid_up_capital)}"]
                      + ([f"Market Cap (listed, extra): {_cr(d.market_cap)}"] if d.market_cap.value_cr is not None else []),
        "net worth": [_cr(d.net_worth)],
        "investment detail": [
            f"Non-Current Investments: {_cr(d.non_current_investments)}",
            f"Current Investments: {_cr(d.current_investments)}",
        ],
        "revenue (3 years)": _years(d.revenue),
        "pat (3 years)": _years(d.pat),
        "cash and cash equivalent": [_cr(d.cash_and_equivalents)],
        "debt": [_cr(d.debt)],
        "long term rating": [_rating(d.long_term_rating)],
        "short term rating": [_rating(d.short_term_rating)],
        "directors": [f"{p.name}" + (f" - {p.designation}" if p.designation else "") + (f" (DIN {p.din})" if p.din else "")
                      for p in d.directors[:10]] or [NA],
        "products & services": [f"• {x}" for x in d.products_and_services] or [NA],
        "meetup person name": [mp.name or NA],
        "designation": [mp.designation or NA],
        "linkedin": [mp.linkedin_url or NA],
        "linkedin history": [f"• {x}" for x in mp.career_history] or [NA],
        "factories": [f"• {x}" for x in d.factories] or [NA],
        "group companies": [f"• {x}" for x in d.group_companies] or [NA],
    }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("​", "")).strip().lower()


def build_docx(d: CompanyOnePager) -> bytes:
    doc: DocType = docx.Document(str(TEMPLATE))
    # Title paragraph "Company Name"
    for p in doc.paragraphs:
        if _norm(p.text) == "company name":
            for r in p.runs[1:]:
                r._element.getparent().remove(r._element)
            if p.runs:
                p.runs[0].text = d.company_name
            else:
                p.add_run(d.company_name)
            break
    fields = _fields(d)
    for table in doc.tables:
        for row in table.rows:
            cells = row.cells
            if len(cells) < 2:
                continue
            label = _norm(cells[0].text)
            if label in fields:
                _set_cell(cells[-1], fields[label])
    # Extras the template has no slot for: sources, flags, gaps on a second block
    doc.add_paragraph()
    for title, items in (
        ("Red flags", d.red_flags or ["None found"]),
        ("Recent news", d.recent_news),
        ("Data gaps", d.data_gaps),
        ("Sources", d.sources),
    ):
        if not items:
            continue
        p = doc.add_paragraph()
        p.add_run(title).bold = True
        for it in items:
            doc.add_paragraph(it, style="List Bullet") if "List Bullet" in [s.name for s in doc.styles] else doc.add_paragraph(f"• {it}")
    kr = (d.screener or {}).get("top_ratios") or {}
    if kr:
        p = doc.add_paragraph()
        p.add_run("Screener key ratios").bold = True
        doc.add_paragraph("; ".join(f"{k}: {v}" for k, v in kr.items()))
    p = doc.add_paragraph()
    p.add_run(f"Confidence: {d.confidence}. AI-researched from public sources - verify before relying on any figure.").italic = True
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:80] or "company"


def build_zip(items: List[CompanyOnePager]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in items:
            z.writestr(f"{_safe(d.company_name)}_OnePager.docx", build_docx(d))
    return buf.getvalue()


def build_xlsx(items: List[CompanyOnePager]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append([
        "Company", "CIN", "Listed/Unlisted", "Authorised Capital (Cr)", "Paid-up Capital (Cr)", "Market Cap (Cr)", "Net Worth (Cr)",
        "Revenue FY-1 (Cr)", "Revenue FY-2 (Cr)", "Revenue FY-3 (Cr)",
        "PAT FY-1 (Cr)", "PAT FY-2 (Cr)", "PAT FY-3 (Cr)",
        "Cash (Cr)", "Debt (Cr)", "Non-Current Inv (Cr)", "Current Inv (Cr)",
        "LT Rating", "ST Rating", "Meetup Person", "Designation", "LinkedIn",
        "Red Flags", "Confidence", "Data Gaps",
    ])

    def yv(lst, i):
        s = sorted(lst, key=lambda y: y.fiscal_year, reverse=True)
        return f"{s[i].fiscal_year}: {s[i].value_cr}" if i < len(s) else None

    for d in items:
        ws.append([
            d.company_name, d.cin, d.listed_status, d.authorised_capital.value_cr, d.paid_up_capital.value_cr, d.market_cap.value_cr, d.net_worth.value_cr,
            yv(d.revenue, 0), yv(d.revenue, 1), yv(d.revenue, 2),
            yv(d.pat, 0), yv(d.pat, 1), yv(d.pat, 2),
            d.cash_and_equivalents.value_cr, d.debt.value_cr,
            d.non_current_investments.value_cr, d.current_investments.value_cr,
            _rating(d.long_term_rating), _rating(d.short_term_rating),
            d.meetup_person.name, d.meetup_person.designation, d.meetup_person.linkedin_url,
            "; ".join(d.red_flags), d.confidence, "; ".join(d.data_gaps),
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 22
    ws.freeze_panes = "B2"
    long = wb.create_sheet("Screener data (long)")
    long.append(["Company", "Section", "Metric", "Period", "Value"])
    for d in items:
        sc = d.screener or {}
        for k, v in (sc.get("top_ratios") or {}).items():
            long.append([d.company_name, "Key ratios", k, "Current", v])
        for sect, rows in (sc.get("tables") or {}).items():
            for metric, cols in rows.items():
                for period, val in cols.items():
                    long.append([d.company_name, sect, metric, period, val])
        for metric, cols in (sc.get("shareholding") or {}).items():
            for period, val in cols.items():
                long.append([d.company_name, "Shareholding %", metric, period, val])
        for parent, rows in (sc.get("schedules") or {}).items():
            for metric, cols in rows.items():
                for period, val in cols.items():
                    long.append([d.company_name, f"Schedule: {parent}", metric, period, val])
    for col in long.columns:
        long.column_dimensions[col[0].column_letter].width = 26
    long.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
