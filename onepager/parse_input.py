"""Read company names from typed text or an uploaded CSV / XLSX / TXT file."""

from __future__ import annotations

import io
import re
from typing import List

import pandas as pd

from .research import CompanyRequest

NAME_COLS = ("company", "company name", "name", "company_name")
PERSON_COLS = ("meetup person", "meetup person name", "person", "contact", "meetup_person")
HINT_COLS = ("hint", "city", "website", "cin", "location")


def from_text(text: str) -> List[CompanyRequest]:
    """One company per line. Optional ' | person' and ' | hint' parts: 'Acme Ltd | Rahul Sharma | Pune'."""
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in re.split(r"\s*[|;\t]\s*", line.strip()) if p.strip()]
        if parts:
            out.append(CompanyRequest(parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None))
    return out


def from_file(name: str, data: bytes) -> List[CompanyRequest]:
    low = name.lower()
    if low.endswith((".txt", ".text")):
        return from_text(data.decode("utf-8", errors="ignore"))
    df = pd.read_csv(io.BytesIO(data)) if low.endswith(".csv") else pd.read_excel(io.BytesIO(data))
    cols = {str(c).strip().lower(): c for c in df.columns}
    name_col = next((cols[c] for c in NAME_COLS if c in cols), df.columns[0])
    person_col = next((cols[c] for c in PERSON_COLS if c in cols), None)
    hint_cols = [cols[c] for c in HINT_COLS if c in cols]
    out = []
    for _, r in df.iterrows():
        n = str(r[name_col]).strip()
        if not n or n.lower() == "nan":
            continue
        person = str(r[person_col]).strip() if person_col is not None and pd.notna(r[person_col]) else None
        hint = ", ".join(str(r[c]) for c in hint_cols if pd.notna(r[c])) or None
        out.append(CompanyRequest(n, person, hint))
    return out
