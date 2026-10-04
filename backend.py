
"""
Search Strategy Agent — backend logic.

Pipeline: plain-language question -> PICO breakdown (Groq/Llama) ->
MeSH term + synonym expansion per concept -> PubMed search string built
per Cochrane Handbook search methodology (Population + Intervention only,
live; Comparator/Outcome kept as reference, since restricting a search by
them risks missing relevant studies that don't state them in the abstract)
-> live hit count -> optional citation export for reference managers.
"""

import os
import requests
import json
import re
import time
import csv
import io
import datetime

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "PASTE_YOUR_KEY_HERE")

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"


# ---------------------------------------------------------------------------
# PubMed helpers
# ---------------------------------------------------------------------------

def _pubmed_request(url: str, params: dict):
    """POST to a PubMed E-utilities endpoint, retrying with backoff on rate
    limit (429) errors. Every PubMed call in this file goes through this —
    without a registered NCBI API key, the limit is 3 requests/second, and
    a single search strategy build fires several PubMed calls in a row
    (MeSH lookups for Population + Intervention, then the live hit count),
    so this needs to be shared, not just applied to the citation export.
    """
    max_retries = 4
    for attempt in range(max_retries):
        response = requests.post(url, data=params)
        if response.status_code == 429:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s
            continue
        response.raise_for_status()
        return response
    raise Exception("PubMed rate limit hit repeatedly — please wait a minute and try again.")


def search_pubmed(search_term: str):
    """Search PubMed and return the number of matching papers."""
    params = {"db": "pubmed", "term": search_term, "retmode": "json"}
    response = _pubmed_request(ESEARCH_URL, params)
    return int(response.json()["esearchresult"]["count"])


def find_mesh_terms(word: str):
    """Given an everyday word, return matching official MeSH term names.
    NOTE: each item returned may itself be a list of synonym terms for one
    MeSH record — handle that when formatting (see format_concept_as_pubmed_string).
    """
    search_params = {"db": "mesh", "term": word, "retmode": "json"}
    search_response = _pubmed_request(ESEARCH_URL, search_params)
    id_list = search_response.json()["esearchresult"]["idlist"]
    if not id_list:
        return []

    summary_params = {"db": "mesh", "id": ",".join(id_list), "retmode": "json"}
    summary_response = _pubmed_request(ESUMMARY_URL, summary_params)
    summary_data = summary_response.json()["result"]
    return [summary_data[uid]["ds_meshterms"] for uid in id_list]


def get_abstract(pmid: str):
    """Fetch the title + abstract text for a given PMID — useful for
    diagnosing why a search string missed a known paper."""
    params = {"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"}
    response = _pubmed_request(EFETCH_URL, params)
    return response.text


# ---------------------------------------------------------------------------
# Groq / LLM helpers
# ---------------------------------------------------------------------------

def _call_groq(system_instructions: str, user_message: str):
    """Shared helper: send a system+user message to Groq, return parsed JSON
    reply. Retries with backoff on 429 (rate limit) — Groq's free tier has
    its own per-minute limit, and a search-strategy build fires several Groq
    calls in a row (one per PICO concept processed), same shape of problem
    as the PubMed rate limiting.
    """
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": user_message},
        ],
    }

    max_retries = 4
    for attempt in range(max_retries):
        response = requests.post(GROQ_URL, headers=headers, json=payload)
        if response.status_code == 429:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s
            continue
        response.raise_for_status()
        reply_text = response.json()["choices"][0]["message"]["content"]
        return json.loads(reply_text)

    raise Exception("Groq rate limit hit repeatedly — please wait a minute and try again.")


def break_into_pico(research_question: str):
    """Break a plain-language research question into PICO format."""
    instructions = """You are a medical research assistant helping design a systematic review search strategy.

Given a research question, break it into PICO format:
- Population: who is being studied
- Intervention: the treatment, exposure, or main factor being studied
- Comparator: what it's being compared against
- Outcome: what result is being measured

IMPORTANT rules for the Comparator field specifically:
1. If the question explicitly states a comparator, use exactly what was stated.
2. If no comparator is stated, but clinical convention makes one strongly implied
   (e.g. a new drug being tested is almost always implicitly compared against
   standard/usual care or placebo), you may suggest one — but you MUST prefix it
   with "ASSUMED:" so it is clearly flagged as your inference.
3. If you cannot reasonably infer one at all, write exactly "none specified".

Respond ONLY with valid JSON in this exact format, nothing else:
{"population": "...", "intervention": "...", "comparator": "...", "outcome": "..."}

---
Worked example:

Question: "can sotatercept improve outcomes in patients with pulmonary hypertension"

Correct response:
{"population": "patients with pulmonary hypertension", "intervention": "sotatercept", "comparator": "ASSUMED: standard/usual care", "outcome": "clinical outcomes"}
---
"""
    return _call_groq(instructions, research_question)


def analyze_concept(concept_text: str, concept_type: str):
    """Ask the model everything we need about one PICO concept, IN ONE CALL:
    1. Likely free-text synonyms/abbreviations/spelling variants.
    2. Whether the concept is too vague/generic for a meaningful MeSH lookup.
    3. If not, the BARE clinical term to actually search MeSH with.

    This replaces what used to be two separate calls (get_synonyms +
    analyze_concept_for_mesh) — they were asking the model about the same
    single concept twice, which doubled Groq usage for no real benefit.
    Halving the call count here matters because a full search-strategy
    build processes up to 4 concepts (Population, Intervention, and
    optionally Comparator/Outcome), so this call is made several times
    per question.

    Why #3 matters: PubMed's MeSH database search requires the query words
    to appear together in a MeSH record's own text. A PICO phrase like
    "patients with pulmonary hypertension" fails to match anything, even
    though "pulmonary hypertension" alone is a well-established MeSH term —
    wrapper words like "patients with" are never part of a MeSH record's own
    name or synonyms. The full original phrase is still used everywhere else
    (free-text synonyms, the final search string) — only the MeSH lookup
    itself uses the cleaned-up version.
    """
    instructions = f"""You are helping build a precise PubMed search strategy.

Given a single concept (a {concept_type}), do three things:

1. List likely free-text synonyms, alternate names, abbreviations, or
   spelling variants that researchers might use in a paper's title or
   abstract when referring to this concept. PubMed's phrase search requires
   EXACT word-for-word matching, and authors often insert abbreviations
   mid-phrase (e.g. "post-myocardial infarction (MI) heart failure" — note
   "(MI)" splits the phrase), which breaks long exact phrases. Your list
   MUST include the BARE foundational term with ALL qualifiers stripped,
   AND the bare standard abbreviation on its own — not just longer
   descriptive phrases. Keep the list focused and realistic (5 to 8 terms).
   Do not include the original concept text itself in the list.

2. Decide whether the concept is too VAGUE/GENERIC for a meaningful MeSH
   lookup (common vague examples: "clinical outcomes", "effectiveness",
   "improved health", "better results").

3. If it is NOT too vague, extract the BARE clinical/scientific term from
   it for a MeSH lookup — strip away wrapper phrases like "patients with",
   "people with", "individuals with", "adults diagnosed with", "in the
   setting of", etc. These wrapper words never appear in a MeSH record's
   own name or synonyms, so they must be removed for the MeSH lookup to
   succeed, even though the original full phrase is still used everywhere
   else. If too_vague is true, mesh_search_term can just repeat the input.

Respond ONLY with valid JSON in this exact format, nothing else:
{{"synonyms": ["term1", "term2"], "too_vague": true/false, "mesh_search_term": "..."}}

---
Worked example 1 (bare-term/abbreviation rule for synonyms):

Concept: "post-MI heart failure"

Correct response:
{{"synonyms": ["post-MI heart failure", "heart failure after myocardial infarction", "ischemic cardiomyopathy", "heart failure", "HF", "post-infarction HF"], "too_vague": false, "mesh_search_term": "heart failure"}}

Notice "heart failure" and "HF" appear BARE in the synonyms list, not just
inside longer compound phrases — this is required, not optional.

---
Worked example 2 (wrapper-stripping rule for the MeSH term):

Concept: "patients with pulmonary hypertension"

Correct response:
{{"synonyms": ["pulmonary arterial hypertension", "PAH", "PH", "pulmonary artery hypertension", "pulmonary hypertension"], "too_vague": false, "mesh_search_term": "pulmonary hypertension"}}

Notice the wrapper phrase "patients with" is stripped from mesh_search_term —
only the bare clinical term remains.

---
Worked example 3 (too-vague case):

Concept: "clinical outcomes"

Correct response:
{{"synonyms": ["clinical endpoints", "health outcomes", "patient outcomes", "treatment outcomes", "efficacy outcomes"], "too_vague": true, "mesh_search_term": "clinical outcomes"}}
---
"""
    return _call_groq(instructions, concept_text)


# ---------------------------------------------------------------------------
# Concept building and search string assembly
# ---------------------------------------------------------------------------

def build_concept_block(concept_text: str, concept_type: str):
    """Gather MeSH term(s) + synonyms for one PICO concept.

    mesh_status distinguishes WHY mesh_terms might be empty, since these mean
    different things to someone reviewing the search strategy:
      - "found"       -> MeSH terms were located and are being used
      - "skipped"     -> the concept was judged too vague/generic, so MeSH
                         lookup was not attempted at all
      - "not_found"   -> MeSH lookup was attempted, but PubMed has no official
                         MeSH entry for this term (e.g. a very new drug name)
    """
    analysis = analyze_concept(concept_text, concept_type)
    synonyms = analysis["synonyms"]

    if analysis["too_vague"]:
        mesh_terms = []
        mesh_status = "skipped"
    else:
        # Use the cleaned, bare clinical term for the MeSH lookup itself —
        # not the full PICO phrase, which often includes wrapper words
        # ("patients with", etc.) that MeSH's own records never contain.
        mesh_terms = find_mesh_terms(analysis["mesh_search_term"])
        mesh_status = "found" if mesh_terms else "not_found"

    return {
        "original_term": concept_text,
        "mesh_terms": mesh_terms,
        "mesh_status": mesh_status,
        "synonyms": synonyms,
    }


def format_concept_as_pubmed_string(block: dict):
    """Turn a concept block into a PubMed search fragment, e.g.
    ("Term"[mesh] OR "synonym"[tiab] OR ...). Flattens mesh_terms since each
    item may itself be a list of synonym terms for one MeSH record.
    """
    pieces = []
    for mesh_entry in block["mesh_terms"]:
        if isinstance(mesh_entry, list):
            for term in mesh_entry:
                pieces.append(f'"{term}"[mesh]')
        else:
            pieces.append(f'"{mesh_entry}"[mesh]')

    pieces.append(f'"{block["original_term"]}"[tiab]')
    for synonym in block["synonyms"]:
        pieces.append(f'"{synonym}"[tiab]')

    return "(" + " OR ".join(pieces) + ")"


def build_full_search_string(pico: dict):
    """Build the LIVE PubMed search string using Population + Intervention only.
    This follows standard systematic-review search methodology as described
    in the Cochrane Handbook for Systematic Reviews of Interventions, which
    recommends against restricting searches by Outcome (often not mentioned
    in a title/abstract even when reported in the full text, so it risks
    missing relevant studies) and frequently omits Comparator too (described
    too inconsistently across abstracts to search on reliably). This is the
    only part needed for the actual search, so it's the only part that costs
    Groq calls by default.

    Comparator/Outcome are reference-only (for the methods write-up and
    manual screening, never forced into the live search) and many users
    will never look at them — so they are NOT processed here. Call
    build_reference_fragments() separately, only if/when the user actually
    asks to see them, so that Groq usage isn't spent on something that
    might never be viewed.
    """
    pop_block = build_concept_block(pico["population"], "population/condition")
    pop_fragment = format_concept_as_pubmed_string(pop_block)

    interv_block = build_concept_block(pico["intervention"], "drug/intervention")
    interv_fragment = format_concept_as_pubmed_string(interv_block)

    live_search_string = f"{pop_fragment} AND {interv_fragment}"

    return {
        "live_search_string": live_search_string,
        "population_block": pop_block,
        "intervention_block": interv_block,
    }


def build_reference_fragments(pico: dict):
    """Process Comparator/Outcome into PubMed-style fragments, for display
    only (methods write-up / manual screening) — never used in the live
    search. Separate from build_full_search_string so these optional Groq
    calls only happen if/when a user actually asks to see them.
    """
    reference_blocks = {}
    for field, label in [("comparator", "comparator"), ("outcome", "outcome")]:
        concept_text = pico[field]
        if concept_text.lower().strip() in ["none specified", ""]:
            reference_blocks[field] = None
            continue
        clean_text = concept_text.replace("ASSUMED:", "").strip()
        block = build_concept_block(clean_text, label)
        reference_blocks[field] = format_concept_as_pubmed_string(block)

    return {
        "reference_comparator": reference_blocks["comparator"],
        "reference_outcome": reference_blocks["outcome"],
    }


# ---------------------------------------------------------------------------
# Result filters (year / language / species / study type)
#
# These map directly onto PubMed's own field tags, so building them costs
# NO Groq calls and NO extra judgment — pure deterministic string
# construction. They can be freely recomputed on every UI interaction
# without any rate-limit concern; only the resulting PubMed hit-count
# check costs an API call, and that's PubMed (cheap, already retry-
# protected), not Groq.
# ---------------------------------------------------------------------------

# Dropdown label -> the exact value PubMed expects in the [la] field tag.
LANGUAGE_OPTIONS = {
    "English": "english",
    "French": "french",
    "German": "german",
    "Spanish": "spanish",
    "Chinese": "chinese",
    "Japanese": "japanese",
    "Russian": "russian",
    "Portuguese": "portuguese",
}

# Dropdown label -> the exact value PubMed expects in the [pt] (publication
# type) field tag.
STUDY_TYPE_OPTIONS = {
    "Randomized Controlled Trial": "randomized controlled trial",
    "Systematic Review": "systematic review",
    "Meta-Analysis": "meta-analysis",
    "Clinical Trial": "clinical trial",
    "Observational Study": "observational study",
    "Review": "review",
}


def build_filters_clause(year_from: str, year_to: str, languages: list, species: str, study_types: list):
    """Build a PubMed query fragment from optional result filters, meant to
    be AND-ed onto the live search string. Returns "" if nothing is
    selected. year_from/year_to are either "Any" or a 4-digit year string
    (as given by the UI's dropdowns); languages and study_types are lists
    of dropdown labels (keys of LANGUAGE_OPTIONS / STUDY_TYPE_OPTIONS).
    """
    clauses = []

    if year_from != "Any" or year_to != "Any":
        start = year_from if year_from != "Any" else "1900"
        end = year_to if year_to != "Any" else str(datetime.date.today().year)
        clauses.append(f'("{start}"[dp] : "{end}"[dp])')

    if languages:
        lang_pieces = [f'"{LANGUAGE_OPTIONS[lang]}"[la]' for lang in languages]
        clauses.append("(" + " OR ".join(lang_pieces) + ")")

    if species == "Humans only":
        clauses.append('"Humans"[mh]')
    elif species == "Animals only":
        clauses.append('"Animals"[mh]')

    if study_types:
        type_pieces = [f'"{STUDY_TYPE_OPTIONS[t]}"[pt]' for t in study_types]
        clauses.append("(" + " OR ".join(type_pieces) + ")")

    return " AND ".join(clauses)


# ---------------------------------------------------------------------------
# Validation (known-item / gold-standard test)
# ---------------------------------------------------------------------------

def check_paper_is_found(search_string: str, target_pmid: str):
    """Checks whether a specific known PMID appears in our search results."""
    validation_string = f"({search_string}) AND {target_pmid}[pmid]"
    count = search_pubmed(validation_string)
    return count > 0


# ---------------------------------------------------------------------------
# RIS export (Mendeley / EndNote)
# ---------------------------------------------------------------------------

PUBMED_MAX_RETMAX = 10000  # PubMed's hard ceiling for a single esearch call


def fetch_citations_medline(search_string: str, max_results: int = PUBMED_MAX_RETMAX, progress_callback=None):
    """Fetch full citation data for up to max_results matching papers, in
    MEDLINE format — batched in groups of 200 (per PubMed's usage
    guidelines), with retry-with-backoff on rate-limit (429) errors.

    max_results defaults to PubMed's own hard ceiling (10,000) — there is no
    artificial lower cap here; PubMed itself does not support fetching more
    than 10,000 results from a single search without additional pagination
    machinery (its "history server"), which this tool does not implement,
    since realistic search strategies here are expected to stay well under it.

    progress_callback, if given, is called after each batch as
    progress_callback(batches_done, total_batches) — lets a caller (e.g. a
    Streamlit UI) show live progress on larger exports.
    """
    search_params = {"db": "pubmed", "term": search_string, "retmode": "json", "retmax": max_results}
    search_response = _pubmed_request(ESEARCH_URL, search_params)
    pmids = search_response.json()["esearchresult"]["idlist"]
    if not pmids:
        return []

    batch_size = 200
    total_batches = (len(pmids) + batch_size - 1) // batch_size
    all_records = []

    for batch_num, i in enumerate(range(0, len(pmids), batch_size), start=1):
        batch_pmids = pmids[i:i + batch_size]
        fetch_params = {"db": "pubmed", "id": ",".join(batch_pmids), "rettype": "medline", "retmode": "text"}
        fetch_response = _pubmed_request(EFETCH_URL, fetch_params)

        all_records.extend(fetch_response.text.strip().split("\n\n"))

        if progress_callback:
            progress_callback(batch_num, total_batches)

        if i + batch_size < len(pmids):
            time.sleep(0.5)

    return all_records


def parse_medline_record(record_text: str):
    """Parse one raw MEDLINE-format record into a dictionary of fields.
    Handles multi-line fields (title, abstract) and repeated fields (authors).
    """
    fields = {}
    current_tag = None

    for line in record_text.split("\n"):
        match = re.match(r"^([A-Z]{2,4})\s*- (.*)$", line)
        if match:
            tag, value = match.group(1), match.group(2)
            current_tag = tag
            if tag == "FAU":
                fields.setdefault("FAU", []).append(value)
            else:
                if tag not in fields:
                    fields[tag] = value
        else:
            if current_tag and line.strip():
                if current_tag == "FAU":
                    fields["FAU"][-1] += " " + line.strip()
                else:
                    fields[current_tag] = fields.get(current_tag, "") + " " + line.strip()

    return fields


def medline_to_ris(fields: dict):
    """Convert one parsed MEDLINE record into an RIS-format citation block."""
    lines = ["TY  - JOUR"]

    if "TI" in fields:
        lines.append(f"TI  - {fields['TI']}")
    for author in fields.get("FAU", []):
        lines.append(f"AU  - {author}")
    if "AB" in fields:
        lines.append(f"AB  - {fields['AB']}")
    if "TA" in fields:
        lines.append(f"JO  - {fields['TA']}")
    elif "JT" in fields:
        lines.append(f"JO  - {fields['JT']}")
    if "DP" in fields:
        lines.append(f"PY  - {fields['DP'].split()[0]}")
    if "AID" in fields and "[doi]" in fields["AID"]:
        lines.append(f"DO  - {fields['AID'].replace('[doi]', '').strip()}")
    if "PMID" in fields:
        lines.append(f"AN  - {fields['PMID']}")

    lines.append("ER  - ")
    return "\n".join(lines)


def make_bibtex_key(fields: dict):
    """Build a readable citation key like 'smith2021' from first author + year."""
    first_author = fields.get("FAU", ["unknown"])[0] if fields.get("FAU") else "unknown"
    last_name = first_author.split(",")[0].strip().lower()
    last_name = re.sub(r"[^a-z]", "", last_name) or "unknown"
    year = fields.get("DP", "").split()[0] if fields.get("DP") else "nd"
    return f"{last_name}{year}"


def medline_to_bibtex(fields: dict, cite_key: str):
    """Convert one parsed MEDLINE record into a BibTeX @article block."""
    def escape(text):
        return text.replace("{", "").replace("}", "") if text else ""

    lines = [f"@article{{{cite_key},"]
    if "TI" in fields:
        lines.append(f"  title = {{{escape(fields['TI'])}}},")
    if fields.get("FAU"):
        lines.append(f"  author = {{{escape(' and '.join(fields['FAU']))}}},")
    if "TA" in fields:
        lines.append(f"  journal = {{{escape(fields['TA'])}}},")
    elif "JT" in fields:
        lines.append(f"  journal = {{{escape(fields['JT'])}}},")
    if "DP" in fields:
        lines.append(f"  year = {{{fields['DP'].split()[0]}}},")
    if "AID" in fields and "[doi]" in fields["AID"]:
        lines.append(f"  doi = {{{fields['AID'].replace('[doi]', '').strip()}}},")
    if "PMID" in fields:
        lines.append(f"  pmid = {{{fields['PMID']}}},")
    lines.append("}")
    return "\n".join(lines)


CSV_COLUMNS = ["PMID", "Title", "Authors", "Journal", "Year", "DOI"]


def medline_to_csv_row(fields: dict):
    """Turn one parsed MEDLINE record into a row (list) matching CSV_COLUMNS."""
    authors = "; ".join(fields.get("FAU", []))
    journal = fields.get("TA", fields.get("JT", ""))
    year = fields.get("DP", "").split()[0] if fields.get("DP") else ""
    aid = fields.get("AID", "")
    doi = aid.replace("[doi]", "").strip() if "[doi]" in aid else ""
    return [fields.get("PMID", ""), fields.get("TI", ""), authors, journal, year, doi]


# Each export format's human-readable label -> the file extension and MIME
# type Streamlit's download button needs to offer it correctly.
EXPORT_FORMATS = {
    "RIS (Mendeley / EndNote)": {"extension": "ris", "mime": "application/x-research-info-systems"},
    "BibTeX (LaTeX / Zotero)": {"extension": "bib", "mime": "application/x-bibtex"},
    "MEDLINE (.txt)": {"extension": "txt", "mime": "text/plain"},
    "CSV (spreadsheet)": {"extension": "csv", "mime": "text/csv"},
}


def export_citations(search_string: str, output_format: str, max_results: int = PUBMED_MAX_RETMAX, progress_callback=None):
    """Full export pipeline: search -> fetch citations -> convert to the
    chosen format. output_format must be one of EXPORT_FORMATS's keys.
    Returns (file_text, exported_count, total_hits) so the caller (e.g. a
    Streamlit download button) can warn if results were capped.
    """
    total_hits = search_pubmed(search_string)
    raw_records = fetch_citations_medline(search_string, max_results=max_results, progress_callback=progress_callback)

    if not raw_records:
        return "", 0, total_hits

    if output_format == "MEDLINE (.txt)":
        # Raw records are already in exactly this format — nothing to convert.
        return "\n\n".join(raw_records), len(raw_records), total_hits

    parsed_records = []
    for raw_record in raw_records:
        parsed = parse_medline_record(raw_record)
        if "TI" in parsed:
            parsed["TI"] = re.sub(r"\s+", " ", parsed["TI"]).strip()
        if "AB" in parsed:
            parsed["AB"] = re.sub(r"\s+", " ", parsed["AB"]).strip()
        parsed_records.append(parsed)

    if output_format == "RIS (Mendeley / EndNote)":
        blocks = [medline_to_ris(fields) for fields in parsed_records]
        return "\n\n".join(blocks), len(blocks), total_hits

    if output_format == "BibTeX (LaTeX / Zotero)":
        used_keys = {}
        blocks = []
        for fields in parsed_records:
            base_key = make_bibtex_key(fields)
            count = used_keys.get(base_key, 0)
            used_keys[base_key] = count + 1
            cite_key = base_key if count == 0 else f"{base_key}{chr(ord('a') + count)}"
            blocks.append(medline_to_bibtex(fields, cite_key))
        return "\n\n".join(blocks), len(blocks), total_hits

    if output_format == "CSV (spreadsheet)":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_COLUMNS)
        for fields in parsed_records:
            writer.writerow(medline_to_csv_row(fields))
        return output.getvalue(), len(parsed_records), total_hits

    raise ValueError(f"Unknown output format: {output_format}")
