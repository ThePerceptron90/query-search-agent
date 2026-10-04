# QUERY — Query Understanding & Evidence Retrieval Yield

**[Try it live →](https://query-search-agent.streamlit.app)**

QUERY turns a plain-language research question into a validated,
PubMed-ready systematic review search strategy — the kind of thing a
student or researcher would otherwise need a medical librarian's help to
build by hand.

> Type: *"does metformin reduce cardiovascular risk in prediabetic
> adults?"* → get back a working PubMed Boolean search string with MeSH
> terms, synonyms, a live hit count, result filters, and exportable
> citations.

---

## Why this exists

Most systematic-review tooling (Covidence, DistillerSR, EPPI-Reviewer) is
enterprise software — paid, and built for teams running full reviews, not
for a student or researcher trying to get a defensible first-draft search
strategy without already knowing PubMed's query syntax or MeSH vocabulary.

QUERY is a focused slice of that problem: **drafting a search strategy**,
done well, with the methodology and reasoning visible rather than hidden
behind a black box.

---

## How it works

1. **Ask in plain English.** No PICO formatting required.
2. **Review the PICO breakdown.** An LLM (Groq-hosted Llama) splits the
   question into Population, Intervention, Comparator, and Outcome — shown
   in editable fields. Nothing else runs until you approve it.
3. **Term expansion.** Population and Intervention concepts are expanded
   into MeSH terms (looked up live against PubMed) and free-text synonyms.
   A vagueness check skips MeSH lookup for overly generic concepts (e.g.
   "clinical outcomes") rather than pulling in noise.
4. **Live search strategy.** Terms are assembled into a PubMed Boolean
   query, following the **Cochrane Handbook for Systematic Reviews of
   Interventions**: Population + Intervention only. Comparator/Outcome
   wording is too inconsistent across abstracts to search on reliably, so
   it's kept as reference material for your methods write-up, not forced
   into the live search.
5. **Transparent, not a black box.** An expandable panel shows exactly
   which MeSH terms and synonyms were used, and why (found / skipped /
   not found).
6. **Optional filters, free to use.** Year range, language, species, and
   study type — pure PubMed field-tag string construction, so narrowing
   results costs no extra API calls.
7. **Export citations** as RIS, BibTeX, MEDLINE, or CSV.

---

## Architecture

- **`backend.py`** — all logic: PubMed E-utilities helpers, Groq/LLM
  helpers, concept expansion, search-string assembly, filter construction,
  multi-format citation export
- **`app.py`** — Streamlit interface
- Deterministic PubMed calls (reproducible, no hallucination risk) are
  kept separate from the LLM layer (PICO breakdown, synonym generation,
  vagueness detection — the parts that need judgment), with a human
  approval checkpoint between them

**Built to survive real usage, not just a demo run:** every PubMed and
Groq call goes through shared retry-with-backoff logic; Streamlit's
session state is used to avoid silently re-running the LLM pipeline on
every unrelated UI interaction (the actual cause of early rate-limit
errors — not usage volume, but redundant re-runs of identical work); Groq
calls per question were cut from up to 9 to a typical 3 by merging
per-concept calls and making Comparator/Outcome processing lazy.

---

## A debugging lesson worth sharing

Converting the UI to a dark theme took five rounds of CSS fixes, and the
pattern behind it is worth more than the fix itself: each round assumed
something about Streamlit's internal markup (`data-testid`, `data-baseweb`
attributes) without checking it, and each assumption was wrong in a
slightly different way — right up until inspecting the actual rendered
DOM in the browser revealed the real cause (a newer Streamlit version had
quietly swapped the old widget library for a different one entirely, so
an entire category of CSS rules had been matching nothing). The fix that
actually stuck was architectural — reset every element to a known blank
state first, then deliberately repaint only what needs its own styling —
rather than chasing individual symptoms as they appeared.

---

## Limitations

- This is a **drafting assistant**, not a replacement for a trained
  information specialist — review any generated strategy against the
  [PRESS checklist](https://www.cadth.ca/resources/finding-evidence/press)
  before using it in a real systematic review.
- **PubMed only.** Embase has no public API (institutional paywall);
  Cochrane CENTRAL has no clean public API. Both are out of scope.
- Runs on Groq's free tier, which has its own rate limits — the app
  retries automatically, but very heavy concurrent use could still hit
  them.
- One question at a time — no saved search history yet (session-based).

---

## Running it locally

```bash
git clone https://github.com/ThePerceptron90/query-search-agent.git
cd query-search-agent
pip install -r requirements.txt
export GROQ_API_KEY=your_groq_key_here   # free tier: https://console.groq.com
streamlit run app.py
```

## Tech

- [Streamlit](https://streamlit.io) — web interface
- [PubMed E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) —
  search, MeSH lookup, citation data
- [Groq](https://groq.com) (free tier, `openai/gpt-oss-120b`) — PICO
  breakdown and term expansion
