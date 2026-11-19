"""Build copy-paste ChatGPT prompts for fields the free sources could not fill (matches the manual workflow)."""

from __future__ import annotations

from typing import List

from .schema import CompanyOnePager


def gap_prompt(d: CompanyOnePager, person: str = "") -> str:
    asks: List[str] = []
    if not d.long_term_rating.rating or not d.short_term_rating.rating:
        asks.append("Latest long-term and short-term credit rating (agency, rating, outlook, date, link to rationale).")
    if len(d.directors) < 3:
        asks.append("Current directors / board members with designation (and DIN if known).")
    if not d.products_and_services:
        asks.append("Products and services (consolidated, group level) as a bullet list.")
    if not d.factories:
        asks.append("Manufacturing plants / factories with city and state.")
    if not d.group_companies:
        asks.append("Group companies: parent, subsidiaries, associates, JVs.")
    if not d.revenue or not d.pat or d.net_worth.value_cr is None:
        asks.append("Consolidated revenue and PAT for the last 3 fiscal years, net worth, total debt, cash & cash equivalents, "
                    "current and non-current investments (all in Rs. crore, with FY and source).")
    if d.authorised_capital.value_cr is None or d.paid_up_capital.value_cr is None:
        asks.append("Authorised share capital and paid-up share capital (Rs. crore).")
    if person:
        asks.append(f"{person}: current designation at the company, and previous employers with role and from-to years.")
    if not asks:
        return ""
    body = "\n".join(f"{i}. {a}" for i, a in enumerate(asks, 1))
    return (f"For the Indian company \"{d.company_name}\"" + (f" (CIN {d.cin})" if d.cin else "") +
            ", give the following from public sources. Give a source link for every item and write 'not found' instead of guessing:\n" + body)
