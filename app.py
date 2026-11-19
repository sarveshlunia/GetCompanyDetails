"""Streamlit UI: type/upload Indian company names -> on-screen one-pager per company (editable) + downloads."""

import concurrent.futures as cf
import re

import pandas as pd
import streamlit as st

from onepager.export import build_docx, build_xlsx, build_zip
from onepager.parse_input import from_file, from_text
import onepager.research as research
from onepager.research import run_company
from onepager import zauba
from urllib.parse import quote_plus
from onepager.schema import Amount, Person, Rating, YearFigure

st.set_page_config(page_title="Company One-Pager", layout="wide")
st.title("Indian Company One-Pager Generator")

NF = ":red[Not found]"


def _src(source, url) -> str:
    if source and url:
        return f" · [{source} ↗]({url})"
    return f" · {source}" if source else ""


def fmt_amt(a: Amount) -> str:
    if a.value_cr is None:
        return NF
    extra = ", ".join(x for x in (a.basis, a.period) if x)
    return (f"Rs. {a.value_cr:,.2f} Cr" + (f"  ({extra})" if extra else "") + _src(a.source, a.source_url)
            + (f"  \n:grey[{a.verify}]" if a.verify else ""))


def fmt_years(items) -> str:
    if not items:
        return NF
    return "  \n".join(f"**{y.fiscal_year}**: Rs. {y.value_cr:,.2f} Cr ({y.basis or '-'})" + _src(y.source, y.source_url)
                       + (f"  \n:grey[{y.verify}]" if y.verify else "")
                       for y in sorted(items, key=lambda y: y.fiscal_year, reverse=True)[:3])


def fmt_rating(r: Rating) -> str:
    return NF if not r.rating else r.rating + (f" - {r.agency}" if r.agency else "") + (f" ({r.date})" if r.date else "")


def bullets(xs) -> str:
    return NF if not xs else "\n".join(f"- {x}" for x in xs)


def row(label: str, value: str, src: str = "", url: str = ""):
    c1, c2, c3 = st.columns([1.2, 4, 1.3])
    c1.markdown(f"**{label}**")
    c2.markdown(value or NF)
    if url:
        c3.markdown(f"[{src or 'source'} ↗]({url})")
    elif src:
        c3.caption(src)


def render(d, i: int):
    fs, fu = d.field_sources, d.field_urls
    if d.red_flags:
        st.error("Red flags: " + " | ".join(d.red_flags))
    row("About", d.about or "", fs.get("about", ""), fu.get("about", ""))
    row("Corporate Address", d.corporate_address or "", fs.get("corporate_address", ""), fu.get("corporate_address", ""))
    row("Listed / Unlisted", d.listed_status or "")
    row("Authorised Capital", fmt_amt(d.authorised_capital), fs.get("authorised_capital", ""), fu.get("authorised_capital", ""))
    row("Paid-up Capital", fmt_amt(d.paid_up_capital), fs.get("paid_up_capital", ""), fu.get("paid_up_capital", ""))
    if d.market_cap.value_cr is not None:
        row("Market Cap (extra)", fmt_amt(d.market_cap), "Yahoo")
    row("Net Worth", fmt_amt(d.net_worth), "", "")
    row("Investment Detail", f"Non-Current: {fmt_amt(d.non_current_investments)}  \nCurrent: {fmt_amt(d.current_investments)}")
    row("Revenue (3 yrs)", fmt_years(d.revenue), "", "")
    row("PAT (3 yrs)", fmt_years(d.pat), "", "")
    row("Cash & Equivalents", fmt_amt(d.cash_and_equivalents))
    row("Debt", fmt_amt(d.debt), "", "")
    row("Long Term Rating", fmt_rating(d.long_term_rating))
    row("Short Term Rating", fmt_rating(d.short_term_rating))
    row("Directors", bullets([f"{p.name}" + (f" - {p.designation}" if p.designation else "") + (f" (DIN {p.din})" if p.din else "")
                              for p in d.directors]), fs.get("directors", ""), fu.get("directors", ""))
    row("Products & Services", bullets(d.products_and_services), fs.get("products_and_services", ""), fu.get("products_and_services", ""))
    mp = d.meetup_person
    row("Meetup Person", mp.name or "")
    row("Designation", mp.designation or "")
    row("LinkedIn", f"[{mp.linkedin_url}]({mp.linkedin_url})" if mp.linkedin_url else "", "")
    row("LinkedIn History", bullets(mp.career_history))
    row("Factories", bullets(d.factories), fs.get("factories", ""), fu.get("factories", ""))
    row("Group Companies", bullets(d.group_companies), fs.get("group_companies", ""), fu.get("group_companies", ""))
    if d.recent_news:
        with st.expander("Recent news"):
            st.markdown(bullets(d.recent_news))


def _lines(t):
    return [x.strip(" -•\t") for x in t.splitlines() if x.strip()]


def _amt(text, old: Amount) -> Amount:
    try:
        v = float(text.replace(",", "").strip())
    except ValueError:
        return old
    return Amount(value_cr=v, period=old.period, basis="Entered manually", source_url=old.source_url)


def _years(text, old):
    out = []
    for ln in _lines(text):
        m = re.match(r"(FY\s?\d{4})\s*[:=-]\s*(\d[\d,\.]*)", ln, re.I)
        if m:
            try:
                out.append(YearFigure(fiscal_year=m.group(1).upper().replace(" ", ""),
                                      value_cr=float(m.group(2).replace(",", "").rstrip(".")), basis="Entered manually"))
            except ValueError:
                pass
    return out or old


def edit(d, i: int):
    """Fill/correct anything the tool missed. Edits flow into the Word/Excel downloads."""
    k = lambda n: f"{i}_{n}"
    d.about = st.text_area("About", d.about or "", key=k("about")) or None
    d.corporate_address = st.text_input("Corporate address", d.corporate_address or "", key=k("addr")) or None
    d.listed_status = st.text_input("Listed / Unlisted", d.listed_status or "", key=k("listed")) or None
    c = st.columns(3)
    d.authorised_capital = _amt(c[0].text_input("Authorised capital (Rs. Cr)", "" if d.authorised_capital.value_cr is None else str(d.authorised_capital.value_cr), key=k("auth")), d.authorised_capital)
    d.paid_up_capital = _amt(c[1].text_input("Paid-up capital (Rs. Cr)", "" if d.paid_up_capital.value_cr is None else str(d.paid_up_capital.value_cr), key=k("paid")), d.paid_up_capital)
    d.net_worth = _amt(c[2].text_input("Net worth (Rs. Cr)", "" if d.net_worth.value_cr is None else str(d.net_worth.value_cr), key=k("nw")), d.net_worth)
    c = st.columns(4)
    d.non_current_investments = _amt(c[0].text_input("Non-current inv. (Cr)", "" if d.non_current_investments.value_cr is None else str(d.non_current_investments.value_cr), key=k("nci")), d.non_current_investments)
    d.current_investments = _amt(c[1].text_input("Current inv. (Cr)", "" if d.current_investments.value_cr is None else str(d.current_investments.value_cr), key=k("ci")), d.current_investments)
    d.cash_and_equivalents = _amt(c[2].text_input("Cash (Cr)", "" if d.cash_and_equivalents.value_cr is None else str(d.cash_and_equivalents.value_cr), key=k("cash")), d.cash_and_equivalents)
    d.debt = _amt(c[3].text_input("Debt (Cr)", "" if d.debt.value_cr is None else str(d.debt.value_cr), key=k("debt")), d.debt)
    c = st.columns(2)
    d.revenue = _years(c[0].text_area("Revenue, one per line: `FY2025: 1200`", "\n".join(f"{y.fiscal_year}: {y.value_cr}" for y in d.revenue), key=k("rev")), d.revenue)
    d.pat = _years(c[1].text_area("PAT, one per line: `FY2025: 150`", "\n".join(f"{y.fiscal_year}: {y.value_cr}" for y in d.pat), key=k("pat")), d.pat)
    c = st.columns(2)
    lt = c[0].text_input("Long-term rating", d.long_term_rating.rating or "", key=k("lt"))
    stt = c[1].text_input("Short-term rating", d.short_term_rating.rating or "", key=k("st"))
    d.long_term_rating = Rating(rating=lt or None, agency=d.long_term_rating.agency, date=d.long_term_rating.date)
    d.short_term_rating = Rating(rating=stt or None, agency=d.short_term_rating.agency, date=d.short_term_rating.date)
    dirs = st.text_area("Directors (one per line: `Name - Designation`)", "\n".join(p.name + (f" - {p.designation}" if p.designation else "") for p in d.directors), key=k("dirs"))
    d.directors = [Person(name=n.strip(), designation=(r or None) and r.strip()) for n, _, r in (ln.partition(" - ") for ln in _lines(dirs))]
    d.products_and_services = _lines(st.text_area("Products & services (one per line)", "\n".join(d.products_and_services), key=k("prod")))
    d.factories = _lines(st.text_area("Factories (one per line)", "\n".join(d.factories), key=k("fac")))
    d.group_companies = _lines(st.text_area("Group companies (one per line)", "\n".join(d.group_companies), key=k("grp")))
    c = st.columns(3)
    d.meetup_person.name = c[0].text_input("Meetup person", d.meetup_person.name or "", key=k("pn")) or None
    d.meetup_person.designation = c[1].text_input("Designation", d.meetup_person.designation or "", key=k("pd")) or None
    d.meetup_person.linkedin_url = c[2].text_input("LinkedIn URL", d.meetup_person.linkedin_url or "", key=k("pl")) or None
    d.meetup_person.career_history = _lines(st.text_area("LinkedIn history (`Role, Company (from - to)`, one per line)", "\n".join(d.meetup_person.career_history), key=k("ph")))


typed = st.text_area(
    "Type company names (one per line). Optional: `Company | Meetup person | Ticker e.g. XYZ.NS or website`",
    height=140, placeholder="Tata Motors Limited\nSupreme Industries | Ramesh Kumar | Mumbai")
up = st.file_uploader("...or upload CSV / XLSX / TXT (columns: Company, Meetup Person, Hint)", type=["csv", "xlsx", "txt"])
read_pdfs = st.checkbox("Read annual-report PDFs for numbers (slower, more complete)", value=True)
research.READ_PDFS = read_pdfs
workers = st.slider("Parallel companies", 1, 5, 2, help="Lower = fewer blocked requests.")

reqs = from_text(typed)
if up:
    reqs += from_file(up.name, up.getvalue())
st.caption(f"{len(reqs)} companies queued")

if st.button("Generate", type="primary", disabled=not reqs):
    results, bar = [], st.progress(0.0)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_company, r) for r in reqs]
        for n, f in enumerate(futs, 1):  # keep input order
            results.append(f.result())
            bar.progress(n / len(reqs), text=f"{n}/{len(reqs)} done")
    st.session_state["results"] = results

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))
results = st.session_state.get("results", [])
for r in results:
    if r.error:
        st.error(f"{r.request.name}: {r.error}")
ok = [(i, r) for i, r in enumerate(results) if r.data]
if ok:
    st.divider()
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("All one-pagers (ZIP)", build_zip([r.data for _, r in ok]), "OnePagers.zip")
    c2.download_button("Summary (.xlsx)", build_xlsx([r.data for _, r in ok]), "Summary.xlsx")
    tabs = st.tabs([r.data.company_name[:28] for _, r in ok])
    for tab, (i, r) in zip(tabs, ok):
        d = r.data
        with tab:
            st.subheader(d.company_name)
            st.caption(f"Confidence: {d.confidence} - AI-free web research; verify before relying on any figure. "
                       f"Grey tag on the right = where each value came from.")
            render(d, i)
            st.download_button("Download this one-pager (.docx)", build_docx(d), f"{d.company_name}.docx", key=f"dl{i}")
            with st.expander("Fill / correct missing fields (flows into the download)"):
                edit(d, i)
            with st.expander("Search it yourself - links for every field", expanded=bool(d.data_gaps)):
                for label, url in d.search_links.items():
                    st.markdown(f"- [{label}]({url})")
                if d.field_urls.get("annual_report"):
                    st.markdown(f"- [Annual report (found)]({d.field_urls['annual_report']})")
            if d.verification:
                with st.expander(f"Cross-check vs Screener ({len(d.verification)} comparisons)", expanded=any(v["status"] == "MISMATCH" for v in d.verification)):
                    vdf = pd.DataFrame(d.verification)
                    st.dataframe(vdf, hide_index=True, column_config={"evidence": st.column_config.TextColumn("raw line read", width="large")})
                    st.caption("MATCH = company statement and Screener agree, statement figure shown. MISMATCH = Screener figure shown; "
                               "open the annual report page in the evidence to see which is right.")
            if d.trail:
                with st.expander(f"How each field was searched ({len(d.trail)} attempts) - source, link, result", expanded=False):
                    df = pd.DataFrame(d.trail)[["field", "source", "url", "result"]].sort_values(["field", "source"])
                    st.dataframe(df, hide_index=True,
                                 column_config={"url": st.column_config.LinkColumn("link", display_text="open ↗"),
                                                "result": st.column_config.TextColumn("result", width="large")})
            if d.searches:
                with st.expander(f"Web searches run ({len(d.searches)}) - click to repeat any in Google"):
                    for q in d.searches:
                        st.markdown(f"- [{q['query']}]({q['url']})")
            with st.expander("Every source page used"):
                for u in dict.fromkeys(x for x in d.sources if x):
                    st.markdown(f"- {u}")
            if any("Zauba" in g for g in d.data_gaps) or d.authorised_capital.value_cr is None:
                with st.expander("Zauba blocked? Paste the page here (fixes capital + directors in 30 seconds)", expanded=True):
                    st.markdown(f"1. Open [Zauba search for {d.company_name}](https://www.zaubacorp.com/companysearchresults/{quote_plus(d.company_name)}) "
                                "in your normal browser and click the company.  \n2. Press **Ctrl+A**, then **Ctrl+C** on the company page.  \n"
                                "3. Paste below and click Apply.")
                    pasted = st.text_area("Zauba page text", key=f"zp{i}", height=120)
                    if st.button("Apply pasted Zauba data", key=f"za{i}") and pasted.strip():
                        z = zauba.parse_text(pasted)
                        got = []
                        if z.authorised_cr is not None:
                            d.authorised_capital = Amount(value_cr=z.authorised_cr, basis="Zauba (pasted)"); got.append("authorised capital")
                        if z.paid_up_cr is not None:
                            d.paid_up_capital = Amount(value_cr=z.paid_up_cr, basis="Zauba (pasted)"); got.append("paid-up capital")
                        if z.cin:
                            d.cin = z.cin; got.append("CIN")
                        if z.listing_status:
                            d.listed_status = z.listing_status if not (d.listed_status or "").startswith("Listed") else d.listed_status
                        if z.directors:
                            d.directors = [Person(name=n, designation=des, din=din) for n, des, din in z.directors[:10]]; got.append(f"{len(z.directors)} directors")
                        if z.address and not d.corporate_address:
                            d.corporate_address = z.address
                        for k in ("authorised_capital", "paid_up_capital", "directors"):
                            d.field_sources[k] = "Zauba (pasted)"
                        d.data_gaps = [g for g in d.data_gaps if "Zauba" not in g]
                        for key in [k for k in st.session_state.keys() if str(k).startswith(f"{i}_")]:
                            del st.session_state[key]  # let the edit boxes below pick up the new values
                        st.session_state["flash"] = f"{d.company_name}: found {', '.join(got) or 'nothing recognisable - check you copied the company page'}"
                        st.rerun()
            if d.data_gaps:
                with st.expander(f"Gaps ({len(d.data_gaps)}) - what was not found and why"):
                    for g in d.data_gaps:
                        st.markdown(f"- {g}")
            if d.chatgpt_prompt:
                with st.expander("ChatGPT prompt for the gaps (copy icon at top-right)"):
                    st.code(d.chatgpt_prompt, language=None)
            with st.expander("Debug log - send this to the developer if fields are wrong/empty"):
                st.code("\n".join(d.source_log) or "(empty)", language=None)
