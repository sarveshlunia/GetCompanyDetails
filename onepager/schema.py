"""Data model for one company's one-pager.

Field order follows the client's One_Pager_Format.docx, with a few extra
fields that make the output auditable (source, basis, as-of date, confidence).
All money values are in Rs. crore so they can be compared and loaded into BI.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Amount(BaseModel):
    """A single money figure, always in Rs. crore."""

    source: Optional[str] = Field(None, description="Which source supplied it, e.g. 'Company website', 'Screener'.")
    evidence: Optional[str] = Field(None, description="The raw text line the number was read from, if parsed from a document.")

    value_cr: Optional[float] = Field(
        None, description="Value in Rs. crore. null if not found. Never guess."
    )
    period: Optional[str] = Field(
        None, description="Period/date the figure belongs to, e.g. 'FY2025-26' or '31-Mar-2026'."
    )
    basis: Optional[str] = Field(
        None, description="'Consolidated' or 'Standalone'."
    )
    source_url: Optional[str] = Field(None, description="URL the figure was taken from.")


class YearFigure(BaseModel):
    source: Optional[str] = None
    evidence: Optional[str] = None
    fiscal_year: str = Field(
        description="Indian fiscal year label, e.g. 'FY2026' meaning Apr 2025 - Mar 2026."
    )
    value_cr: Optional[float] = Field(None, description="Rs. crore. null if not found.")
    basis: Optional[str] = Field(None, description="'Consolidated' or 'Standalone'.")
    source_url: Optional[str] = None


class Rating(BaseModel):
    rating: Optional[str] = Field(None, description="e.g. 'CRISIL AA+/Stable', 'ICRA A1+'.")
    agency: Optional[str] = Field(None, description="CRISIL, ICRA, CARE, India Ratings, Acuite, Brickwork, Infomerics.")
    date: Optional[str] = Field(None, description="Date of the rating action.")
    source_url: Optional[str] = None


class Person(BaseModel):
    name: str
    designation: Optional[str] = None
    din: Optional[str] = Field(None, description="Director Identification Number, if found.")


class MeetupPerson(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    linkedin_url: Optional[str] = Field(
        None, description="Public LinkedIn profile URL only if found in search results; otherwise null."
    )
    career_history: List[str] = Field(
        default_factory=list,
        description="Previous roles as 'Role, Company (years)', from public sources only.",
    )


class CompanyOnePager(BaseModel):
    # Identity
    company_name: str = Field(description="Full legal name, e.g. 'Tata Motors Limited'.")
    cin: Optional[str] = Field(None, description="21-character Corporate Identification Number from MCA.")
    website: Optional[str] = None
    industry: Optional[str] = None
    incorporation_year: Optional[str] = None

    # Template fields
    about: Optional[str] = Field(None, description="3-5 sentence business overview.")
    corporate_address: Optional[str] = Field(None, description="Registered / corporate office address.")
    listed_status: Optional[str] = Field(
        None, description="'Listed (NSE: XXX, BSE: 123456)' or 'Unlisted (Private)' / 'Unlisted (Public)'."
    )
    authorised_capital: Amount = Field(default_factory=Amount, description="Authorised share capital, Rs. crore (Zauba Corp).")
    paid_up_capital: Amount = Field(default_factory=Amount, description="Paid-up share capital, Rs. crore (Zauba Corp).")
    market_cap: Amount = Field(default_factory=Amount, description="Extra, listed companies only; period = date of price.")
    net_worth: Amount = Field(default_factory=Amount)
    non_current_investments: Amount = Field(default_factory=Amount)
    current_investments: Amount = Field(default_factory=Amount)
    revenue: List[YearFigure] = Field(
        default_factory=list, description="Revenue from operations, latest 3 fiscal years, newest first."
    )
    pat: List[YearFigure] = Field(
        default_factory=list, description="Profit after tax, latest 3 fiscal years, newest first."
    )
    cash_and_equivalents: Amount = Field(default_factory=Amount)
    debt: Amount = Field(default_factory=Amount, description="Total borrowings (long-term + short-term).")
    long_term_rating: Rating = Field(default_factory=Rating)
    short_term_rating: Rating = Field(default_factory=Rating)
    directors: List[Person] = Field(default_factory=list, description="Current board, max 10.")
    products_and_services: List[str] = Field(default_factory=list)
    meetup_person: MeetupPerson = Field(default_factory=MeetupPerson)
    factories: List[str] = Field(default_factory=list, description="Plant / manufacturing locations with city, state.")
    group_companies: List[str] = Field(
        default_factory=list, description="Holding company, key subsidiaries, associates, JVs."
    )

    # Extras that make the sheet trustworthy and useful in a meeting
    promoter_holding_pct: Optional[float] = Field(None, description="Listed companies only, latest quarter.")
    recent_news: List[str] = Field(
        default_factory=list, description="Up to 5 material items from the last 12 months, each with month/year."
    )
    red_flags: List[str] = Field(
        default_factory=list,
        description="Rating downgrades, defaults, NCLT/insolvency, auditor qualifications, big losses. Empty if none found.",
    )
    sources: List[str] = Field(default_factory=list, description="All URLs used.")
    field_sources: dict = Field(default_factory=dict, description="field name -> which source filled it.")
    data_gaps: List[str] = Field(
        default_factory=list, description="Fields that could not be found or verified, and why."
    )
    field_urls: dict = Field(default_factory=dict, description="field name -> URL it came from.")
    search_links: dict = Field(default_factory=dict, description="label -> ready-made search/verify URL.")
    trail: List[dict] = Field(default_factory=list, description="Per field: every source tried, its link and result.")
    searches: List[dict] = Field(default_factory=list, description="Web searches run: query + clickable Google link.")
    source_log: List[str] = Field(default_factory=list, description="What each web request returned (for debugging).")
    chatgpt_prompt: str = Field("", description="Copy-paste prompt for fields free sources could not fill.")
    confidence: str = Field(
        "Low", description="'High' if figures come from annual report/exchange filings, 'Medium' if from aggregators, 'Low' otherwise."
    )
