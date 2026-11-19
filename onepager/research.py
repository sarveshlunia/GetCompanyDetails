"""Research an Indian company with Claude + web search, then structure it.

Two steps per company:
  1. Research: Claude searches and reads the web (server-side web_search /
     web_fetch tools) and writes detailed notes with a source URL per figure.
  2. Extract: a second call turns those notes into a validated
     CompanyOnePager (structured output). Kept separate because web search
     answers carry citations, which can't be combined with structured output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import anthropic

from .schema import CompanyOnePager

MODEL = os.getenv("ONEPAGER_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ONEPAGER_EFFORT", "high")  # low | medium | high | xhigh | max
MAX_SEARCHES = int(os.getenv("ONEPAGER_MAX_SEARCHES", "15"))
MAX_FETCHES = int(os.getenv("ONEPAGER_MAX_FETCHES", "10"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"

RESEARCH_SYSTEM = """You are a corporate research analyst preparing a pre-meeting one-pager on an Indian company.
Today is {today}.

Find, for the company named by the user:
- Legal name, CIN, website, industry, incorporation year
- About (what they do, scale, key customers/markets)
- Registered / corporate office address
- Listed or unlisted (with NSE symbol / BSE code if listed), market cap with its date, promoter holding
- Net worth; non-current and current investments; cash and cash equivalents; total borrowings
- Revenue from operations and PAT for the latest 3 fiscal years
- Long-term and short-term credit ratings with agency and date
- Current directors (with designation, DIN if visible)
- Products & services; factory / plant locations; group companies (holding, subsidiaries, JVs)
- Material news from the last 12 months and any red flags (downgrades, defaults, NCLT, auditor qualifications)
{person_block}

Source priority (use the best available, say which you used):
1. Annual report, NSE/BSE filings and investor presentations
2. Credit rating rationales (CRISIL, ICRA, CARE, India Ratings, Acuite, Brickwork, Infomerics) - these are the best source of financials for UNLISTED companies
3. Screener.in, Moneycontrol, Tofler, Zauba Corp, The Company Check, company website
4. Reputable news (Economic Times, Business Standard, Mint, Reuters)

Rules:
- Indian fiscal years: FY2026 = April 2025 to March 2026. Label every figure with its fiscal year.
- Convert every amount to Rs. crore (1 crore = 10 million; 1 lakh crore = 100,000 crore). Prefer consolidated; state the basis.
- Put the source URL next to every number.
- If the name is ambiguous (several companies share it), pick the most likely one, state the CIN, and list the alternatives.
- Never invent a number, a person, or a LinkedIn URL. If you can't find something, write "NOT FOUND" and why
  (e.g. "FY2026 accounts not yet filed with MCA").
- Write plain notes, not a polished report."""

PERSON_BLOCK = """- The meetup person: {person}. Find their current designation, public LinkedIn profile URL
  (only if it appears in search results) and career history from public sources (LinkedIn snippets, company
  site, news, exchange filings). Do not guess."""

EXTRACT_SYSTEM = """Convert the research notes into the JSON schema. Copy values exactly; do not add facts that are not
in the notes. Use null for anything marked NOT FOUND and record it in data_gaps. Amounts in Rs. crore.
Revenue and PAT: newest fiscal year first, max 3 entries."""


@dataclass
class CompanyRequest:
    name: str
    meetup_person: Optional[str] = None
    hint: Optional[str] = None  # city / website / CIN to disambiguate


@dataclass
class CompanyResult:
    request: CompanyRequest
    data: Optional[CompanyOnePager] = None
    notes: str = ""
    error: Optional[str] = None


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4, timeout=900)


def _text(content) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text")


def research_notes(req: CompanyRequest, client: Optional[anthropic.Anthropic] = None) -> str:
    client = client or _client()
    person_block = PERSON_BLOCK.format(person=req.meetup_person) if req.meetup_person else ""
    system = RESEARCH_SYSTEM.format(today=date.today().isoformat(), person_block=person_block)
    ask = f"Company: {req.name}"
    if req.hint:
        ask += f"\nDisambiguation hint: {req.hint}"

    tools = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
            "user_location": {"type": "approximate", "country": "IN"},
        },
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": MAX_FETCHES},
    ]
    messages = [{"role": "user", "content": ask}]

    # A long server-tool turn can pause; send it back so the server continues.
    for _ in range(6):
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": EFFORT},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "refusal":
            raise RuntimeError("Model declined the research request.")
        return _text(response.content)

    raise RuntimeError("Research did not finish after several continuations.")


def extract(notes: str, req: CompanyRequest, client: Optional[anthropic.Anthropic] = None) -> CompanyOnePager:
    client = client or _client()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": f"Company requested: {req.name}\n\nResearch notes:\n{notes}"}],
        output_format=CompanyOnePager,
        output_config={"effort": "low"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"Extraction failed (stop_reason={response.stop_reason}).")
    data = response.parsed_output
    # The person we were asked about is authoritative even if research found nothing.
    if req.meetup_person and not data.meetup_person.name:
        data.meetup_person.name = req.meetup_person
    return data


def run_company(req: CompanyRequest, client: Optional[anthropic.Anthropic] = None) -> CompanyResult:
    client = client or _client()
    result = CompanyResult(request=req)
    try:
        result.notes = research_notes(req, client)
        result.data = extract(result.notes, req, client)
    except anthropic.AuthenticationError:
        result.error = "Invalid or missing ANTHROPIC_API_KEY."
    except anthropic.RateLimitError:
        result.error = "Rate limited by the API - retry later or lower concurrency."
    except anthropic.APIStatusError as e:
        result.error = f"API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        result.error = "Network error reaching the API."
    except Exception as e:  # keep the batch going; report per company
        result.error = str(e)
    return result
