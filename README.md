# QUERY — Query Understanding & Evidence Retrieval Yield

Turns a plain-language research question into a PRISMA/Cochrane-aligned
PubMed systematic-review search strategy.

> **Status:** core functionality complete and working; README still being
> expanded with full usage, validation results, and design notes.

## What it does

1. Type a research question in plain English.
2. The app breaks it into PICO (Population, Intervention, Comparator,
   Outcome) and lets you review/edit each field before anything runs.
3. It expands the Population and Intervention concepts into MeSH terms and
   synonyms, builds a PubMed-ready Boolean search string, and shows the
   live hit count.
4. Optional filters (year range, language, species, study type) narrow
   results at no extra API cost.
5. Export matching citations as RIS, BibTeX, MEDLINE, or CSV.

The search methodology (Population + Intervention only, Comparator/Outcome
kept as reference terms rather than forced into the live search) follows
the **Cochrane Handbook for Systematic Reviews of Interventions** — not a
substitute for a trained information specialist, and strategies should
still be checked against the PRESS checklist before use in a real review.

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

You'll need a free [Groq](https://console.groq.com/) API key — set it as
the `GROQ_API_KEY` environment variable for local use, or via Streamlit
Secrets if deploying.

## Tech

- **Streamlit** for the interface
- **PubMed E-utilities** for search, MeSH lookup, and citation data
- **Groq** (free tier, `openai/gpt-oss-120b`) for PICO breakdown and term
  expansion

---

*Full design decisions, validation results, and lessons learned write-up
coming in a follow-up README pass.*
