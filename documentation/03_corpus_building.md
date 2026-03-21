# Step 2 — Corpus Building & XML Parsing

> **`2_build_corpus.py`** (78 lines) orchestrates parsing.  
> **`utils/xml_parser.py`** (628 lines) contains all XML parsers.  
> **`utils/normalizers.py`** (124 lines) provides abbreviation & citation normalization.

---

## Orchestrator Flow (`2_build_corpus.py`)

```mermaid
flowchart TD
    START(["main()"]) --> PARSE["build_smart_corpus()<br/><i>from utils/xml_parser.py</i>"]
    PARSE --> SAVE_CORPUS["💾 Save to<br/>legal_corpus_final.json"]
    SAVE_CORPUS --> ABBREV["build_abbreviation_table(corpus)<br/><i>from utils/normalizers.py</i>"]
    ABBREV --> IDMAP["build_id_to_title_map(corpus)<br/><i>from utils/normalizers.py</i>"]
    IDMAP --> STATS["📊 Print Summary:<br/>• Total chunks<br/>• Abbreviations found<br/>• Source documents<br/>• Type distribution<br/>• Chunks with notes"]
    STATS --> EFFECTS["load_effects_triples()<br/><i>from utils/xml_parser.py</i>"]
    EFFECTS -->|"effects found"| SAVE_EFF["💾 Save to<br/>effects_triples.json"]
    EFFECTS -->|"none"| DONE
    SAVE_EFF --> DONE(["✅ Done"])
```

---

## Smart Corpus Builder (`build_smart_corpus`)

```mermaid
flowchart TD
    START["build_smart_corpus()"] --> LEG

    subgraph "1️⃣ Primary Legislation"
        LEG["Scan raw_legislation/*.xml"] --> LEG_PARSE["parse_legislation_xml()<br/>for each file"]
    end

    LEG_PARSE --> SI

    subgraph "2️⃣ Statutory Instruments"
        SI["Scan raw_statutory_instruments/*.xml"] --> SI_PARSE["parse_legislation_xml()<br/><i>same CLML format</i>"]
    end

    SI_PARSE --> CASE

    subgraph "3️⃣ Case Law"
        CASE["Scan raw_caselaw/*.xml"] --> CASE_PARSE["parse_caselaw_xml()<br/><i>AKN format</i>"]
    end

    CASE_PARSE --> NOTES_CHECK{"Notes directory<br/>exists?"}

    subgraph "4️⃣ Notes Enrichment"
        NOTES_CHECK -->|yes| NOTES_PARSE["parse_notes_xml()<br/>for each notes file"]
        NOTES_PARSE --> BUILD_MAP["Build mapping:<br/>file_prefix → {section_id → text}"]
        BUILD_MAP --> ENRICH["Match notes to chunks<br/>by file prefix + section"]
        ENRICH --> SET["chunk['notes_text'] = note[:500]"]
    end

    NOTES_CHECK -->|no| RETURN
    SET --> RETURN["Return all_chunks<br/>list of dict"]
```

---

## XML Parsers — Detailed Flows

### `parse_legislation_xml()` — CLML Parser

Handles both primary legislation (Acts) and statutory instruments.

```mermaid
flowchart TD
    FILE["CLML XML file"] --> TREE["ET.parse(filepath)"]
    TREE --> TITLE["Extract doc_title from<br/>PrimaryPrelims/Title<br/>or first Title element"]
    TITLE --> YEAR["Extract year from<br/>Number element"]
    YEAR --> TERMS["extract_defined_terms(root)<br/>Find all Term definitions"]
    TERMS --> DATE["Extract enactment date<br/>from DateOfEnactment/DateText<br/>→ YYYY-MM-DD"]

    DATE --> SECTIONS["Iterate over P1 + P1group<br/>elements (sections)"]

    subgraph "🔄 Per Section"
        SECTIONS --> PNUM["Get section number<br/>from Pnumber element"]
        PNUM --> CONTENT["extract_text_recursive(section)<br/>→ plain text"]
        CONTENT --> LEN_CHECK{"len > 20?"}
        LEN_CHECK -->|no| SKIP["Skip"]
        LEN_CHECK -->|yes| HEADING["Find Title child<br/>→ heading text"]
        HEADING --> ATTRS["Get attributes:<br/>• RestrictExtent<br/>• RestrictStartDate"]
        ATTRS --> PARENT["get_parent_pblock_title()<br/>→ Part/Pblock ancestor title"]
        PARENT --> REFS["extract_internal_links()<br/>extract_inline_amendments()"]
        REFS --> BUILD["Build chunk dict with<br/>prefixed content string:<br/>ACT: ... | SECTION: ... | TEXT: ..."]
    end

    BUILD --> SCHEDULES["Iterate over Schedule<br/>elements (P1 within)"]

    subgraph "📋 Per Schedule Paragraph"
        SCHEDULES --> SCHED_CHUNK["Build schedule chunk<br/>with inherited extent/date"]
    end

    SCHED_CHUNK --> RETURN["Return list of all chunks"]
```

### `parse_caselaw_xml()` — AKN Parser

```mermaid
flowchart TD
    FILE["AKN XML file"] --> TREE["ET.parse(filepath)"]
    TREE --> NAME["Extract case name from<br/>dc:title or FRBRname"]
    NAME --> YEAR["Extract year from filename"]
    YEAR --> COURT["Determine court level:<br/>uksc → Supreme Court<br/>ewca → Court of Appeal<br/>ewhc → High Court"]
    COURT --> PARAS{"Structured<br/>paragraphs<br/>found?"}
    PARAS -->|no| FULL["Extract full text as<br/>single chunk"]
    PARAS -->|yes| LOOP["Iterate paragraphs"]

    subgraph "🔄 Per Paragraph"
        LOOP --> PARA_TEXT["extract_text_recursive(para)"]
        PARA_TEXT --> LEN{"len > 30?"}
        LEN -->|no| SKIP["Skip"]
        LEN -->|yes| CHUNK["Build chunk:<br/>CASE: ... | COURT: ...<br/>| PARA: ... | TEXT: ..."]
    end

    FULL --> RETURN["Return chunks"]
    CHUNK --> RETURN
```

### `parse_effects_xml()` — Effects Feed Parser

```mermaid
flowchart TD
    FILE["Effects Atom XML"] --> TREE["ET.parse(filepath)"]
    TREE --> ITER["Iterate over<br/>ukm:Effect elements"]

    subgraph "🔄 Per Effect"
        ITER --> TYPE["Get Type attribute<br/><i>e.g. 'words substituted'</i>"]
        TYPE --> MAP["_map_effect_type(type)<br/>→ canonical action"]
        MAP -->|None| SKIP["Skip"]
        MAP -->|action| TITLES["Find child elements:<br/>• ukm:AffectedTitle<br/>• ukm:AffectingTitle"]
        TITLES --> PROV["Get attributes:<br/>• AffectedProvisions<br/>• AffectingProvisions"]
        PROV --> DATE["Find InForceDates/InForce<br/>→ Date attribute"]
        DATE --> TRIPLE["Build ground-truth triple<br/>confidence=1.0<br/>provenance='effects_api'"]
    end

    TRIPLE --> RETURN["Return triples list"]
```

### `parse_notes_xml()` — Explanatory Notes Parser

Returns `dict[section_ref → commentary_text]` by scanning:
1. `<Comment>` elements
2. `<Commentary>` elements
3. `<P>` paragraph elements with parent section references

---

## Helper Functions (`utils/xml_parser.py`)

| Function | Input | Output | Purpose |
|----------|-------|--------|---------|
| `_leg(tag)` | Tag name | `{LEG_NS}tag` | Namespace prefix for legislation |
| `_akn(tag)` | Tag name | `{AKN_NS}tag` | Namespace prefix for AKN |
| `_meta(tag)` | Tag name | `{META_NS}tag` | Namespace prefix for metadata |
| `extract_text_recursive(elem)` | XML element | Plain text | Recursively extracts all text, strips tags |
| `extract_defined_terms(root)` | XML root | `{short: full}` | Finds `<Term>` definitions |
| `extract_internal_links(section)` | Section elem | `[ref_text, ...]` | All `<InternalLink>` refs |
| `extract_inline_amendments(section)` | Section elem | `[amend_text, ...]` | All `<InlineAmendment>` text |
| `get_parent_pblock_title(elem, root)` | Elem + root | Title string | Walks up tree for nearest `<Pblock>/<Part>` title |

---

## Normalization Functions (`utils/normalizers.py`)

### `normalize_citation(raw_citation, abbrev_table?) → str`

```mermaid
flowchart LR
    IN["raw citation"] --> ABBR{"abbreviation<br/>table?"}
    ABBR -->|yes| EXPAND["Expand:<br/>'LRA 1967' →<br/>'Leasehold Reform Act 1967'"]
    ABBR -->|no| FIND
    EXPAND --> FIND["Find year (4 digits)"]
    FIND --> SPLIT["Split at year:<br/>act_part + ref_part"]
    SPLIT --> REGEX["Regex on ref_part:<br/>section → s.<br/>sections → ss.<br/>Schedule → Sch.<br/>paragraph → para.<br/>regulation → reg.<br/>article → art."]
    REGEX --> OUT["normalized citation"]
```

### Other Functions

| Function | Purpose |
|----------|---------|
| `normalize_action(raw) → str \| None` | Maps LLM output to canonical action via `ACTION_NORMALIZER` + fuzzy substring fallback |
| `extract_act_name(citation) → str` | Strips section ref: `Housing Act 1996 s.122` → `Housing Act 1996` |
| `build_abbreviation_table(corpus) → dict` | Extracts abbreviations from `defined_terms` + text patterns like `'... Full Name Year ("abbrev")'` |
| `build_id_to_title_map(corpus) → dict` | Maps chunk ID prefixes → document titles |

---

## Output Data Structures

### Corpus Chunk (Legislation)

```json
{
  "id":                "ukpga_2024_3.xml_1",
  "source":            "legislation",
  "doc_title":         "Automated Vehicles Act 2024",
  "year":              "2024",
  "section":           "1",
  "part":              "Part 1 — Automated vehicles",
  "heading":           "Meaning of 'self-driving'",
  "extent":            "E+W+S+NI",
  "in_force_date":     "2024-05-20",
  "internal_refs":     ["section 3", "section 5"],
  "inline_amendments": ["for 'word X' substitute 'word Y'"],
  "defined_terms":     {"the 2018 Act": "Automated and Electric Vehicles Act 2018"},
  "notes_text":        "This section defines self-driving...",
  "content":           "ACT: Automated Vehicles Act 2024 (2024) | SECTION: 1 | PART: Part 1 | HEADING: Meaning of 'self-driving' | TEXT: ..."
}
```

### Corpus Chunk (Case Law)

```json
{
  "id":           "ewhc_admin_2023_5.xml_42",
  "source":       "judgment",
  "doc_title":    "R v Secretary of State for Transport",
  "year":         "2023",
  "section":      "42",
  "court_level":  "High Court",
  "content":      "CASE: R v Secretary of State ... | COURT: High Court | PARA: 42 | TEXT: ..."
}
```

### Effects Triple (Ground-Truth)

```json
{
  "source_id":        "effects_Railways Act 2005",
  "source_title":     "Railways Act 2005",
  "source_section":   "s. 12(1)",
  "action":           "SUBSTITUTES",
  "target_citation":  "Transport Act 2000 s. 5(1)",
  "target_act_name":  "Transport Act 2000",
  "detail_text":      "words substituted by Railways Act 2005 s. 12(1)",
  "effective_date":   "2024-01-01",
  "confidence":       1.0,
  "provenance":       "effects_api"
}
```

### Effect Type → Canonical Action Mapping

| API Effect Type | Canonical Action |
|----------------|-----------------|
| `inserted` | `INSERTS` |
| `substituted`, `words substituted`, `s. substituted` | `SUBSTITUTES` |
| `repealed`, `words repealed` | `REPEALS` |
| `amended`, `text amended` | `AMENDS` |
| `applied`, `applied (with modifications)` | `APPLIES` |
| `commenced`, `coming into force` | `COMMENCES` |
| `revoked` | `REVOKES` |
| `extended` | `EXTENDS` |
| `power to modify` | `EMPOWERS` |
| `restricted` | `PROHIBITS` |
