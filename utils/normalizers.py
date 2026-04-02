"""
LegalKGent — Normalization Utilities
=====================================
Shared functions for normalizing actions, citations, and abbreviations.
Extracted from 2_kg_creation.py and 2b_kg_creation_caselaw.py to eliminate duplication.
"""

import re
from config import CANONICAL_ACTIONS, ACTION_NORMALIZER


def normalize_action(raw_action: str) -> str | None:
    """Map any LLM action string to canonical form. Returns None if unrecognized."""
    if not raw_action:
        return None
    cleaned = raw_action.upper().strip()
    # Direct lookup
    normalized = ACTION_NORMALIZER.get(cleaned)
    if normalized:
        return normalized
    # Fuzzy fallback: check if any canonical action is a substring match
    for canon in CANONICAL_ACTIONS:
        if canon in cleaned or cleaned in canon:
            return canon
    return None


def normalize_citation(raw_citation: str, abbrev_table: dict | None = None) -> str | None:
    """
    Normalize a citation to consistent format.
    - Expand abbreviations (LRA 1967 → Leasehold Reform Act 1967)
    - Standardize section refs: 'section 5' → 's.5', 'Schedule 2' → 'Sch.2'
    """
    if not raw_citation:
        return None
    citation = raw_citation.strip()

    # 1. Expand known abbreviations
    if abbrev_table:
        for short, full in abbrev_table.items():
            if short in citation:
                citation = citation.replace(short, full)

    # (Regex fallbacks removed as requested. The new Glossary RAG architecture 
    # natively prevents the LLM from substituting overlong definitions into citations)
    
    # 3. Normalize section references after the year
    year_match = re.search(r'\d{4}', citation)
    if year_match:
        pos = year_match.end()
        act_part = citation[:pos]
        ref_part = citation[pos:]
        ref_part = re.sub(r'\bsection\s+',     's.',     ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bsections\s+',    'ss.',    ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSchedule\s+',    'Sch.',   ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSCHEDULE\s+',   'Sch.',   ref_part)
        ref_part = re.sub(r'\bparagraph\s+',   'para.',  ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bparagraphs\s+',  'paras.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulation\s+',  'reg.',   ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulations\s+', 'regs.',  ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\barticle\s+',     'art.',   ref_part, flags=re.IGNORECASE)
        citation = act_part + ref_part

    return citation.strip()


def extract_act_name(citation: str) -> str | None:
    """
    Pull out just the Act/Case name without section reference.
    'Housing Act 1996 s.122' → 'Housing Act 1996'
    '[2023] UKSC 1' → '[2023] UKSC 1'
    """
    if not citation:
        return None
    # Match standard Act pattern
    match = re.match(r'(.*?(?:Act|Bill|Order|Regulations?|Rules?)\s+\d{4})', citation)
    if match:
        return match.group(1).strip()
    # Match case citation: [YYYY] COURT NUM
    case_match = re.match(r'(\[\d{4}\]\s+\w+\s+\d+)', citation)
    if case_match:
        return case_match.group(1).strip()
    return citation.strip()


def build_abbreviation_table(corpus: list[dict]) -> dict:
    """
    Auto-extract abbreviation definitions from corpus text.
    Looks for patterns like: 'the Landlord and Tenant Act 1985 ("the LTA 1985")'
    Also uses <Term> definitions extracted during XML parsing.
    """
    abbrev_table = {}

    # From text patterns: '... Full Act Name Year ("abbreviation")'
    pattern = re.compile(
        r'((?:the\s+)?[A-Z][A-Za-z\s,\'-]+?Act\s+\d{4})\s*'
        r'\(\s*["\u201c]\s*((?:the\s+)?[A-Z][A-Za-z\s]+?\d{4})\s*["\u201d]\s*\)'
    )
    for chunk in corpus:
        content = chunk.get("content", "")
        # Fast-fail string matching to prevent catastrophic regex backtracking
        if "Act " not in content or "(" not in content:
            continue
            
        for match in pattern.finditer(content):
            full_name = match.group(1).strip()
            abbreviation = match.group(2).strip()
            if abbreviation and full_name and len(abbreviation) < len(full_name):
                abbrev_table[abbreviation] = full_name

    return abbrev_table


def build_id_to_title_map(corpus: list[dict]) -> dict:
    """Map source ID prefixes to readable Act titles."""
    id_to_title = {}
    for c in corpus:
        chunk_id = c.get("chunk_id", "")
        prefix = chunk_id.rsplit(".xml_", 1)[0] if ".xml_" in chunk_id else chunk_id
        if prefix and prefix not in id_to_title:
            id_to_title[prefix] = c.get("doc_title", prefix)
    return id_to_title


def extract_matched_glossary(text: str, act_glossary: dict) -> dict:
    """
    Given a chunk's text and the full glossary for its parent Act,
    return only the {term: summary} pairs that actually appear in the text.
    Uses fast regex word-boundary matching.
    """
    if not act_glossary or not text:
        return {}
        
    matched = {}
    # Sort terms by length descending, so we match longer terms first
    sorted_terms = sorted(act_glossary.keys(), key=len, reverse=True)
    
    for term in sorted_terms:
        # Build safe word-boundary pattern, allowing basic plurals
        pattern = r'\b' + re.escape(term) + r'(s|es)?\b' 
        if re.search(pattern, text, flags=re.IGNORECASE):
            matched[term] = act_glossary[term]
            
    return matched
