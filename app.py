
"""
Search Strategy Agent — Streamlit interface.

Run with: streamlit run app.py
Requires backend.py in the same directory, with a valid GROQ_API_KEY set.
"""

import datetime
import streamlit as st
from backend import (
    break_into_pico,
    build_full_search_string,
    build_reference_fragments,
    build_filters_clause,
    LANGUAGE_OPTIONS,
    STUDY_TYPE_OPTIONS,
    search_pubmed,
    export_citations,
    EXPORT_FORMATS,
)

CURRENT_YEAR = datetime.date.today().year
YEAR_OPTIONS = ["Any"] + [str(y) for y in range(CURRENT_YEAR, 1945, -1)]

st.set_page_config(page_title="QUERY", page_icon="🔬", layout="wide")

# ---------------------------------------------------------------------------
# Visual design: "Evidence Lab" — clinical/journal precision rather than a
# generic SaaS look. Ink-navy text on cool paper-white, a single teal accent
# reserved for actions and validated results, IBM Plex Serif for headings,
# Plex Sans for body text, Plex Mono for the PubMed query strings (the one
# piece of content that is genuinely code-like and should read that way).
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {
        --ink: #E8ECEF;
        --paper: #121A22;
        --teal: #2BB8A3;
        --slate: #7FA0BE;
        --border: #2A3744;
        --amber: #E0A046;
    }

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
        color: var(--ink);
    }

    /* ---------------------------------------------------------------------
       THE FIX, DONE PROPERLY: rather than hunting down every individual
       Streamlit/BaseWeb element that happens to ship a white background
       (expander headers, inline code spans, select boxes, etc. — a list
       that keeps growing because the exact internal class/testid names
       differ by Streamlit version and are not a stable public API), strip
       EVERY element inside the app down to a transparent background and
       the light ink text color first. That alone fixes any white box
       anywhere, because there is no more white background left to fight.
       Afterwards, specific areas that genuinely need their own background
       (page frame, sidebar, inputs, code, buttons, badges, tags, popover
       menus) repaint themselves with their own rule, which wins over this
       one because it is written later in the stylesheet (same selector
       specificity, so source order decides the tie). */
    .stApp *, .stApp *::before, .stApp *::after {
        background-color: transparent !important;
        color: var(--ink) !important;
        border-color: var(--border) !important;
    }

    [data-testid="stAppViewContainer"] { background-color: var(--paper) !important; }
    [data-testid="stSidebar"] { background-color: #16202A !important; border-right: 1px solid var(--border); }

    /* Streamlit's top toolbar sits outside .stApp, so it keeps its default
       white background unless targeted separately. */
    [data-testid="stHeader"] { background-color: var(--paper) !important; }

    h1, h2, h3 { font-family: 'IBM Plex Serif', serif; font-weight: 600; color: var(--ink); }
    h1 { font-size: 3.4rem !important; }

    [data-testid="stCaptionContainer"] {
        font-family: 'IBM Plex Sans', sans-serif; color: var(--slate) !important;
        font-size: 1.2rem !important;
    }

    .step-header { display: flex; align-items: center; gap: 0.7rem; margin-top: 0.3rem; margin-bottom: 0.8rem; }
    .step-badge {
        display: inline-flex; align-items: center; justify-content: center;
        width: 2rem; height: 2rem; border-radius: 50%;
        background-color: var(--teal) !important; color: #0F171E !important;
        font-family: 'IBM Plex Sans', sans-serif; font-weight: 600; font-size: 1rem; flex-shrink: 0;
    }
    .step-title { font-family: 'IBM Plex Serif', serif; font-weight: 600; font-size: 1.4rem; color: var(--ink) !important; }

    /* NOTE: every custom background-color below needs !important — the
       blanket ".stApp *" reset above also carries !important, and a
       non-!important declaration never beats an !important one regardless
       of selector specificity or source order. (This is exactly what made
       the step-number badges invisible: var(--teal) without !important lost
       to the reset's transparent, leaving dark-on-dark text.) */
    .callout-note {
        border-left: 3px solid var(--amber); background-color: #2A2113 !important;
        padding: 0.6rem 0.8rem; font-size: 0.9rem; color: var(--ink);
        border-radius: 2px; margin-top: 0.6rem;
    }

    /* Code block background: the data-testid wrapper alone doesn't match in
       every Streamlit version, so also force the background directly on the
       <pre>/<code> tags themselves — belt-and-suspenders, same as buttons. */
    code, pre, [data-testid="stCodeBlock"] { font-family: 'IBM Plex Mono', monospace !important; }
    [data-testid="stCodeBlock"],
    [data-testid="stCodeBlock"] pre,
    [data-testid="stCodeBlock"] code,
    pre, pre > div, pre code {
        border: 1px solid var(--border);
        background-color: #0C1319 !important;
        color: var(--ink) !important;
    }
    [data-testid="stCodeBlock"] { border-left: 3px solid var(--slate) !important; border-radius: 4px; }

    /* Expander headers/bodies have no background rule of their own — they
       just inherit transparent from the reset above (correct: they sit on
       the page background), but give them a visible border so the
       clickable header still reads as a distinct control. */
    [data-testid="stExpander"] { border: 1px solid var(--border) !important; border-radius: 4px; }
    details, summary { background-color: transparent !important; }

    [data-testid="stMetricValue"] { font-family: 'IBM Plex Serif', serif; color: var(--teal) !important; }
    [data-testid="stMetricLabel"] { font-family: 'IBM Plex Sans', sans-serif; color: var(--slate) !important; }

    /* Button styling: target both the data-testid (newer Streamlit) and the
       "kind" attribute Streamlit puts directly on the <button> element
       (more stable across versions), as a belt-and-suspenders fix after the
       testid-only rules didn't match this installed version. */
    [data-testid="baseButton-primary"], button[kind="primary"] {
        background-color: var(--teal) !important; color: #0F171E !important; border: none !important;
        border-radius: 4px !important; font-family: 'IBM Plex Sans', sans-serif !important; font-weight: 500 !important;
    }
    [data-testid="baseButton-primary"]:hover, button[kind="primary"]:hover { background-color: #45CDB9 !important; color: #0F171E !important; }
    [data-testid="baseButton-secondary"], button[kind="secondary"] {
        background-color: transparent !important; color: var(--teal) !important; border: 1px solid var(--teal) !important;
        border-radius: 4px !important; font-family: 'IBM Plex Sans', sans-serif !important; font-weight: 500 !important;
    }
    [data-testid="baseButton-secondary"]:hover, button[kind="secondary"]:hover { background-color: #1B2B29 !important; color: var(--teal) !important; }

    hr { border-color: var(--border) !important; }

    /* Text inputs, selectboxes and multiselects don't pick up the dark
       background from config.toml on their own — give them one explicitly,
       since forcing light text (above) without this would make them
       unreadable (light text on their default white field background).
       The "> div" / specific child selectors from the first pass didn't
       match this Streamlit version's actual DOM, so this brute-forces every
       element nested inside a select/multiselect/input wrapper as well. */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-baseweb="select"],
    [data-baseweb="select"] *,
    [data-baseweb="base-input"],
    [data-baseweb="base-input"] *,
    [data-baseweb="input"],
    [data-baseweb="input"] * {
        background-color: #0C1319 !important;
        color: var(--ink) !important;
        /* The multiselect's empty-state "Choose options" text isn't dim
           because of its color — color is already forced to var(--ink)
           above — it's dim because BaseWeb renders it at reduced opacity
           (its own placeholder styling). Force full opacity too, or the
           text stays a washed-out grey no matter what color it's painted. */
        opacity: 1 !important;
    }
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-baseweb="select"],
    [data-baseweb="base-input"],
    [data-baseweb="input"] {
        border: 1px solid var(--border) !important;
    }
    /* The little dropdown caret / clear-icon svgs inside selects should stay
       visible against the dark background rather than inheriting black fill. */
    [data-baseweb="select"] svg { fill: var(--slate) !important; }

    /* The multiselect placeholder dimming turned out to come from further
       out than [data-baseweb="select"] itself — Streamlit's own widget
       wrapper (stMultiSelect / stSelectbox) applies reduced opacity at the
       container level when a widget has no value, which is outside
       everything targeted above (that rule only reaches [data-baseweb=
       "select"] and its children, not its ancestors). Force it back to
       full opacity from the wrapper down. */
    [data-testid="stMultiSelect"],
    [data-testid="stMultiSelect"] *,
    [data-testid="stSelectbox"],
    [data-testid="stSelectbox"] * {
        opacity: 1 !important;
    }

    /* Placeholder text lives in a ::placeholder pseudo-element on a hidden
       <input>, which the "select every nested element" rule above can't
       reach — pseudo-elements aren't matched by '*'. BaseWeb also ships its
       own placeholder color with !important in some versions, so this needs
       its own explicit, forceful rule rather than relying on inheritance.

       Two different cases, two different colors:
       - A free-text field's placeholder (research question) is an example
         of what to type, not a value — keep it muted slate so it still
         reads as a hint, same as any ordinary form field.
       - A select/multiselect's "Choose an option(s)" text is standing in
         for an actual value ("Any" is shown the same way, in solid white,
         once chosen) — so it should match that solid ink color, not look
         like a dimmer, different kind of control. */
    [data-testid="stTextInput"] input::placeholder,
    [data-testid="stTextArea"] textarea::placeholder {
        color: var(--slate) !important;
        opacity: 1 !important;
        -webkit-text-fill-color: var(--slate) !important;
    }
    /* Confirmed via inspecting the live page: this Streamlit version's
       selectbox/multiselect is NOT the old BaseWeb widget (no
       data-baseweb attribute anywhere on it) — it's a newer React-Aria
       combobox, e.g.:
           <input role="combobox" placeholder="Any"
                  class="st-emotion-cache-161p2tj e1kig3hy6" ...>
       Every [data-baseweb="select"] rule above has therefore been
       matching nothing for this placeholder. role="combobox" is a stable
       ARIA attribute (not a generated class hash), so target that
       directly instead of guessing at version-specific class names. */
    input[role="combobox"]::placeholder,
    [data-testid="stMultiSelect"] input::placeholder,
    [data-testid="stSelectbox"] input::placeholder {
        color: var(--ink) !important;
        opacity: 1 !important;
        -webkit-text-fill-color: var(--ink) !important;
    }

    /* The (?) help-tooltip icon next to a widget label (e.g. "Export
       format") renders as its own small svg icon button, separate from the
       field it annotates, and needs the same treatment. */
    [data-testid="stTooltipIcon"],
    [data-testid="stTooltipIcon"] svg,
    [data-testid="stTooltipHoverTarget"],
    [data-testid="stTooltipHoverTarget"] svg {
        color: var(--slate) !important;
        fill: var(--slate) !important;
        opacity: 1 !important;
    }

    /* Multiselect chips (e.g. selected languages/study types) */
    [data-baseweb="tag"] { background-color: var(--teal) !important; color: #0F171E !important; }
    [data-baseweb="tag"] svg { fill: #0F171E !important; }

    /* Dropdown/multiselect option menus render in a portal outside .stApp,
       so they need their own background + text rules too. */
    [data-baseweb="popover"] [data-baseweb="menu"],
    [data-baseweb="popover"] ul,
    [role="listbox"] {
        background-color: #16202A !important;
    }
    [data-baseweb="popover"] li,
    [role="option"] {
        color: var(--ink) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def step_header(number, title):
    """Render a step heading with a numbered badge — this really is a
    sequential pipeline (PICO -> search -> filters -> export), so the
    numbering earns its place rather than decorating generic sections."""
    st.markdown(
        f'<div class="step-header"><div class="step-badge">{number}</div>'
        f'<div class="step-title">{title}</div></div>',
        unsafe_allow_html=True,
    )


with st.sidebar:
    st.header("About")
    st.write(
        "This tool turns a plain-language research question into a "
        "PubMed-ready systematic review search strategy — built on PICO "
        "breakdown, MeSH term lookup, and search logic following Cochrane "
        "Handbook methodology."
    )
    st.markdown(
        '<div class="callout-note">This is a drafting assistant, not a replacement '
        "for a trained information specialist. Review the final strategy against "
        "the PRESS checklist before using it in a real review.</div>",
        unsafe_allow_html=True,
    )

    st.divider()
    if st.button("Reset everything"):
        st.session_state.clear()
        st.rerun()

st.title("QUERY")
st.caption("Query Understanding & Evidence Retrieval Yield — plain-language question to PubMed-ready search strategy.")

research_question = st.text_input(
    "Research question:",
    placeholder="e.g. does metformin reduce cardiovascular risk in prediabetic adults?",
)

if research_question:
    # Only call the PICO breakdown once per question — store it so editing
    # the fields below doesn't trigger a brand new LLM call every time.
    if st.session_state.get("last_question") != research_question:
        with st.spinner("Breaking your question into PICO..."):
            try:
                st.session_state["pico"] = break_into_pico(research_question)
            except Exception as e:
                st.error(f"⚠️ {e}")
                st.stop()
        st.session_state["last_question"] = research_question
        st.session_state["approved"] = False

    st.divider()
    step_header(1, "Review PICO breakdown")
    st.write("Edit any field below if it doesn't look right, then approve to build the search strategy.")

    col1, col2 = st.columns(2)
    with col1:
        population = st.text_input("Population", value=st.session_state["pico"]["population"])
        comparator = st.text_input("Comparator", value=st.session_state["pico"]["comparator"])
    with col2:
        intervention = st.text_input("Intervention", value=st.session_state["pico"]["intervention"])
        outcome = st.text_input("Outcome", value=st.session_state["pico"]["outcome"])

    if st.button("Approve and build search strategy", type="primary"):
        st.session_state["approved"] = True
        st.session_state["approved_pico"] = {
            "population": population,
            "intervention": intervention,
            "comparator": comparator,
            "outcome": outcome,
        }

    if st.session_state.get("approved"):
        pico = st.session_state["approved_pico"]

        # Streamlit reruns this whole script on every interaction (changing
        # the export format dropdown, clicking the export button, etc.) —
        # without this check, build_full_search_string (2 Groq calls, one
        # per concept) and search_pubmed would re-run every single time,
        # burning through the Groq/PubMed rate limit for no reason, even
        # though the PICO hasn't changed. Only redo the work when the
        # approved PICO actually changes.
        if st.session_state.get("last_approved_pico") != pico:
            with st.spinner("Building search strategy (this takes a moment)..."):
                try:
                    st.session_state["result"] = build_full_search_string(pico)
                except Exception as e:
                    st.error(f"⚠️ {e}")
                    st.stop()

            with st.spinner("Checking live hit count on PubMed..."):
                try:
                    st.session_state["hit_count"] = search_pubmed(
                        st.session_state["result"]["live_search_string"]
                    )
                except Exception as e:
                    st.error(f"⚠️ {e}")
                    st.stop()

            st.session_state["last_approved_pico"] = pico
            st.session_state.pop("export_text", None)  # stale — strategy changed
            st.session_state.pop("reference_fragments", None)  # stale — strategy changed

        result = st.session_state["result"]
        hit_count = st.session_state["hit_count"]

        st.divider()
        step_header(2, "Your PubMed search strategy")
        st.code(result["live_search_string"], language=None)

        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Papers found on PubMed", hit_count)

        def describe_mesh(block):
            """Return a clear, honest description of the MeSH lookup outcome."""
            if block["mesh_status"] == "found":
                return str(block["mesh_terms"])
            elif block["mesh_status"] == "skipped":
                return "_not looked up — this concept was judged too generic for a reliable MeSH match_"
            else:  # not_found
                return "_looked up, but PubMed has no official MeSH term for this (may be too new or too specific)_"

        with st.expander("Show term details (MeSH terms and synonyms used)"):
            st.write("**Population terms:**")
            pop_block = result["population_block"]
            st.write(f"- Original: {pop_block['original_term']}")
            st.write(f"- MeSH terms: {describe_mesh(pop_block)}")
            st.write(f"- Synonyms: {pop_block['synonyms']}")

            st.write("**Intervention terms:**")
            interv_block = result["intervention_block"]
            st.write(f"- Original: {interv_block['original_term']}")
            st.write(f"- MeSH terms: {describe_mesh(interv_block)}")
            st.write(f"- Synonyms: {interv_block['synonyms']}")

        with st.expander("Show Comparator/Outcome reference terms (optional, not used in the live search)"):
            st.write(
                "These aren't part of the search above. Per standard systematic-review "
                "search methodology (Cochrane Handbook for Systematic Reviews of "
                "Interventions), searches are generally restricted to Population + "
                "Intervention only — adding Outcome terms risks missing studies that "
                "don't state the outcome in their title/abstract even when they report "
                "it, and Comparator wording (e.g. \"usual care\", \"placebo\") is too "
                "inconsistent across abstracts to search on reliably. This is just for "
                "your methods write-up or manual screening."
            )
            if "reference_fragments" not in st.session_state:
                if st.button("Generate reference terms"):
                    with st.spinner("Processing comparator/outcome..."):
                        try:
                            st.session_state["reference_fragments"] = build_reference_fragments(pico)
                        except Exception as e:
                            st.error(f"⚠️ {e}")

            if "reference_fragments" in st.session_state:
                refs = st.session_state["reference_fragments"]
                st.write(f"**Comparator:** `{refs['reference_comparator']}`" if refs["reference_comparator"] else "**Comparator:** none specified")
                st.write(f"**Outcome:** `{refs['reference_outcome']}`" if refs["reference_outcome"] else "**Outcome:** none specified")

        st.divider()
        step_header(3, "Refine with filters (optional)")
        st.write("These add extra conditions to the search above (combined with AND).")

        filt_col1, filt_col2 = st.columns(2)
        with filt_col1:
            year_from = st.selectbox("From year", options=YEAR_OPTIONS, index=0)
            languages = st.multiselect("Language(s)", options=list(LANGUAGE_OPTIONS.keys()), placeholder="Any")
        with filt_col2:
            year_to = st.selectbox("To year", options=YEAR_OPTIONS, index=0)
            species = st.selectbox("Species", options=["Any", "Humans only", "Animals only"])

        study_types = st.multiselect("Study type(s)", options=list(STUDY_TYPE_OPTIONS.keys()), placeholder="Any")

        filters_clause = build_filters_clause(year_from, year_to, languages, species, study_types)
        final_search_string = result["live_search_string"]
        if filters_clause:
            final_search_string = f"({final_search_string}) AND {filters_clause}"

        # Cheap to recompute (one PubMed call, no Groq) — but still cache so
        # re-rendering the page for an unrelated reason doesn't re-fetch.
        if st.session_state.get("last_final_search_string") != final_search_string:
            with st.spinner("Checking filtered hit count..."):
                try:
                    st.session_state["filtered_hit_count"] = search_pubmed(final_search_string)
                except Exception as e:
                    st.error(f"⚠️ {e}")
                    st.stop()
            st.session_state["last_final_search_string"] = final_search_string
            st.session_state.pop("export_text", None)  # stale — search changed

        st.code(final_search_string, language=None)
        st.metric("Papers found (with filters)", st.session_state["filtered_hit_count"])

        st.divider()
        step_header(4, "Export citations")

        export_format = st.selectbox(
            "Export format",
            options=list(EXPORT_FORMATS.keys()),
            help=(
                "RIS and BibTeX import directly into reference managers "
                "(Mendeley/EndNote/Zotero for RIS; Zotero/JabRef/LaTeX for BibTeX). "
                "MEDLINE is PubMed's own native format. CSV is a plain spreadsheet."
            ),
        )

        if st.button("Prepare citations for download"):
            progress_bar = st.progress(0.0)
            progress_text = st.empty()

            def update_progress(batches_done, total_batches):
                progress_bar.progress(batches_done / total_batches)
                progress_text.write(f"Fetching batch {batches_done} of {total_batches}...")

            try:
                file_text, exported_count, total_hits = export_citations(
                    final_search_string, export_format, progress_callback=update_progress
                )
                st.session_state["export_text"] = file_text
                st.session_state["export_count"] = exported_count
                st.session_state["export_total"] = total_hits
                st.session_state["export_format"] = export_format
            except Exception as e:
                st.error(f"⚠️ {e}")
            finally:
                progress_bar.empty()
                progress_text.empty()

        if "export_text" in st.session_state:
            if st.session_state["export_total"] > st.session_state["export_count"]:
                st.warning(f"Found {st.session_state['export_total']} papers — exported the first {st.session_state['export_count']}.")
            else:
                st.success(f"Exported all {st.session_state['export_count']} papers.")

            format_info = EXPORT_FORMATS[st.session_state["export_format"]]
            st.download_button(
                label=f"Download .{format_info['extension']} file",
                data=st.session_state["export_text"],
                file_name=f"search_results.{format_info['extension']}",
                mime=format_info["mime"],
            )
