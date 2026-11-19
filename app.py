"""Streamlit UI: type or upload Indian company names, get one-pagers (Word) + summary (Excel)."""

import concurrent.futures as cf
import os

import streamlit as st

from onepager.export import build_docx, build_xlsx, build_zip
from onepager.parse_input import from_file, from_text
from onepager.research import run_company

st.set_page_config(page_title="Company One-Pager", layout="wide")
st.title("Indian Company One-Pager Generator")

if not os.getenv("ANTHROPIC_API_KEY"):
    st.error("Set ANTHROPIC_API_KEY before starting the app.")
    st.stop()

typed = st.text_area(
    "Type company names (one per line). Optional: `Company | Meetup person | City/website/CIN`",
    height=140,
    placeholder="Tata Motors Limited\nSupreme Industries | Ramesh Kumar | Mumbai",
)
up = st.file_uploader("...or upload CSV / XLSX / TXT (columns: Company, Meetup Person, Hint)", type=["csv", "xlsx", "txt"])
workers = st.slider("Parallel companies", 1, 5, 3, help="Each company takes ~2-5 minutes and uses real API credits.")

reqs = from_text(typed)
if up:
    reqs += from_file(up.name, up.getvalue())
st.caption(f"{len(reqs)} companies queued")

if st.button("Generate", type="primary", disabled=not reqs):
    results, bar = [], st.progress(0.0)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_company, r): r for r in reqs}
        for i, f in enumerate(cf.as_completed(futs), 1):
            results.append(f.result())
            bar.progress(i / len(reqs), text=f"{i}/{len(reqs)} done")
    st.session_state["results"] = results

results = st.session_state.get("results", [])
ok = [r for r in results if r.data]
for r in results:
    if r.error:
        st.error(f"{r.request.name}: {r.error}")
if ok:
    st.download_button("Download all (ZIP of .docx)", build_zip([r.data for r in ok]), "OnePagers.zip")
    st.download_button("Download summary (.xlsx)", build_xlsx([r.data for r in ok]), "Summary.xlsx")
    for r in ok:
        d = r.data
        with st.expander(f"{d.company_name} - confidence {d.confidence}"):
            st.download_button("One-pager (.docx)", build_docx(d), f"{d.company_name}.docx", key=d.company_name)
            if d.data_gaps:
                st.warning("Gaps: " + "; ".join(d.data_gaps))
            if d.red_flags:
                st.error("Red flags: " + "; ".join(d.red_flags))
            st.json(d.model_dump())
            st.text_area("Raw research notes", r.notes, height=200, key="n" + d.company_name)
