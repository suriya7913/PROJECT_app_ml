"""
LegalKGent — Phase 3+4: GraphRAG Hybrid Query Agent
====================================================
Upgrades the query agent from brittle string matching to a semantic + graph
hybrid (GraphRAG) architecture with strict hallucination guardrails.

TWO TOOLS instead of one:
  1. semantic_search(query, top_k)  — FAISS cosine search to find anchor nodes
  2. run_cypher(query)              — Deterministic Cypher traversal from anchors

WORKFLOW (ReAct loop):
  Step 1 → Agent calls semantic_search("...user question...")
  Step 2 → Agent uses returned node IDs as anchors in run_cypher(MATCH ...)
  Step 3 → Agent synthesizes ANSWER citing ONLY tool results

GUARDRAILS (Phase 4):
  - ZERO_RESULTS string returned on empty Cypher → agent must stop, not hallucinate
  - NO_MATCHES string returned on low-similarity search → agent must inform user
  - ANSWER blocked on step 1 (before any tool call)
  - Post-answer citation check warns when answer lacks grounded IDs

Usage:
  python3 6_graphrag_query.py
  (Requires: data/faiss_index/ built by 5_graphrag_embeddings.py)
"""

# ============================================================
# CELL 1: Dependencies
# ============================================================
# !pip install mistralai neo4j sentence-transformers faiss-cpu
# !pip install arize-phoenix opentelemetry-sdk opentelemetry-exporter-otlp openinference-instrumentation-mistralai

# ============================================================
# CELL 2: Imports & Config
# ============================================================
import json
import os
import re
import numpy as np
from collections import defaultdict

from neo4j import GraphDatabase
from mistralai import Mistral
from sentence_transformers import SentenceTransformer
import faiss

# --- Phoenix Tracing (official API) ---
from phoenix.otel import register
from opentelemetry import trace

# --- CONFIG ---
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "wTPmklMivGd5WB14RsLnms5DUw1pOeHh")
MISTRAL_MODEL   = "mistral-large-latest"

INDEX_FILE = "data/faiss_index/index.faiss"
IDMAP_FILE = "data/faiss_index/id_map.json"

EMBED_MODEL       = "all-MiniLM-L6-v2"
MIN_SIM_THRESHOLD = 0.25   # below this score, consider "no match"

# --- Register Phoenix tracer (connects to standalone server at localhost:6006) ---
tracer_provider = register(
    project_name="legalkgent-graphrag",
    auto_instrument=True,   # auto-instruments MistralAI based on installed OI packages
)
_tracer = trace.get_tracer("legalkgent.graphrag")
print("✅ Phoenix tracer registered & Mistral auto-instrumented")

# --- Connect ---
client = Mistral(api_key=MISTRAL_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connected to Neo4j and Mistral")

# ============================================================
# CELL 3: Load FAISS Index & Embedding Model
# ============================================================
if not os.path.exists(INDEX_FILE):
    raise FileNotFoundError(
        f"FAISS index not found at {INDEX_FILE}.\n"
        "Please run 5_graphrag_embeddings.py first to build the index."
    )

print(f"📦 Loading embedding model: {EMBED_MODEL} ...")
embed_model = SentenceTransformer(EMBED_MODEL)

print(f"🗂️  Loading FAISS index from {INDEX_FILE} ...")
faiss_index = faiss.read_index(INDEX_FILE)

print(f"📋 Loading ID map from {IDMAP_FILE} ...")
with open(IDMAP_FILE) as f:
    id_map = json.load(f)

print(f"✅ Index ready — {faiss_index.ntotal} vectors, {len(id_map)} records")

# ── Load corpus for lookup_corpus tool ────────────────────────────────────────
CORPUS_FILE = "data/legal_corpus_final.json"
print(f"📂 Loading corpus from {CORPUS_FILE} for content lookup ...")
with open(CORPUS_FILE, "r", encoding="utf-8") as f:
    _corpus_data = json.load(f)
corpus_lookup = {c["id"]: c for c in _corpus_data if c.get("id")}
print(f"   ✅ Corpus lookup ready — {len(corpus_lookup)} chunks")
del _corpus_data  # free memory


# ============================================================
# CELL 4: Tool 1 — semantic_search
# ============================================================
def _semantic_search_impl(query: str, top_k: int = 5) -> str:
    """
    Search the FAISS vector index for corpus chunks most semantically similar
    to 'query'. Returns the top-k matching node IDs with similarity scores
    and text snippets.

    Returns a JSON string or a GUARDRAIL string on failure.
    """
    print(f"\n🔧 [TOOL CALL] semantic_search")
    print(f"   Query : {query!r}")
    print(f"   Top-K : {top_k}")

    vec = embed_model.encode([query], normalize_embeddings=True).astype("float32")
    scores, indices = faiss_index.search(vec, k=top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(id_map):
            continue
        rec = id_map[idx]
        # Enrich with heading/part from corpus if available
        chunk_meta = corpus_lookup.get(rec["node_id"], {})
        heading = chunk_meta.get("heading", "")
        # Clean dot-placeholder headings
        if heading and re.match(r'^[\s.]+$', heading):
            heading = ""
        results.append({
            "node_id":    rec["node_id"],
            "doc_title":  rec["doc_title"],
            "section":    rec.get("section", ""),
            "heading":    heading,
            "part":       chunk_meta.get("part", ""),
            "similarity": round(float(score), 4),
            "snippet":    rec["text"][:600],
        })

    # Filter out low-similarity results
    relevant = [r for r in results if r["similarity"] >= MIN_SIM_THRESHOLD]

    if not relevant:
        msg = (
            "⚠️ NO_MATCHES: No corpus chunks found with similarity ≥ "
            f"{MIN_SIM_THRESHOLD} for this query. "
            "This topic may not be covered in the knowledge graph. "
            "You MUST inform the user: 'I do not have this information in my graph.'"
        )
        print(f"   ⚠️  {msg}")
        return msg

    output = json.dumps(relevant, indent=2)
    print(f"   ✅ Returned {len(relevant)} relevant matches")
    preview = output[:2000]
    print(f"   Results:\n{preview}{'...' if len(output) > 2000 else ''}")
    return output


def semantic_search(query: str, top_k: int = 5) -> str:
    """Traced wrapper for semantic_search."""
    with _tracer.start_as_current_span("semantic_search", attributes={"query": query, "top_k": top_k}):
        result = _semantic_search_impl(query, top_k)
        trace.get_current_span().set_attribute("result_preview", result[:500])
        return result


# ============================================================
# CELL 5: Tool 2 — run_cypher
# ============================================================
def _run_cypher_impl(query: str) -> str:
    """
    Execute a Cypher query against Neo4j and return results as JSON.
    Returns a GUARDRAIL string when 0 rows are returned.
    """
    print(f"\n🔧 [TOOL CALL] run_cypher")
    print(f"   Query: {query}")
    with driver.session() as session:
        try:
            result  = session.run(query)
            records = [dict(r) for r in result]

            if not records:
                msg = (
                    "⚠️ ZERO_RESULTS: This Cypher query returned 0 rows. "
                    "Do NOT fabricate data. Consider: "
                    "(1) Try searching the OTHER direction (source vs target). "
                    "(2) Try a broader WHERE clause (CONTAINS instead of STARTS WITH). "
                    "(3) If you have exhausted search strategies, respond with ANSWER "
                    "stating the graph does not contain this information."
                )
                print(f"   ⚠️  Zero results returned — guardrail triggered")
                return msg

            output  = json.dumps(records, indent=2, default=str)
            print(f"   ✅ Returned {len(records)} record(s)")
            preview = output[:2000]
            print(f"   Data: {preview}{'...' if len(output) > 2000 else ''}")
            return output

        except Exception as e:
            err = f"CYPHER ERROR: {e}"
            print(f"   ❌ {err}")
            return err


def run_cypher(query: str) -> str:
    """Traced wrapper for run_cypher."""
    with _tracer.start_as_current_span("run_cypher", attributes={"cypher_query": query}):
        result = _run_cypher_impl(query)
        trace.get_current_span().set_attribute("result_preview", result[:500])
        return result


# ============================================================
# CELL 6: Build Graph Schema Context
# ============================================================
def get_schema() -> dict:
    """Fetch graph schema for the system prompt."""
    with driver.session() as s:
        node_count  = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        edge_count  = s.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        actions     = {r["action"]: r["count"] for r in s.run(
            "MATCH ()-[r:LEGAL_RELATIONSHIP]->() "
            "RETURN r.action_type AS action, count(*) AS count ORDER BY count DESC"
        )}
        concepts    = [r["name"] for r in s.run(
            "MATCH (c:Concept) RETURN c.name AS name ORDER BY name"
        )]
        sample_edges = [dict(r) for r in s.run(
            "MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t) "
            "RETURN s.id AS source_id, r.action_type AS action, "
            "r.detail AS detail, t.citation AS target LIMIT 5"
        )]
    return {
        "node_count":   node_count,
        "edge_count":   edge_count,
        "action_types": actions,
        "concepts":     concepts,
        "sample_edges": sample_edges,
    }

schema = get_schema()
print(f"📊 Graph: {schema['node_count']} nodes, {schema['edge_count']} edges")
print(f"   Actions : {list(schema['action_types'].keys())}")
print(f"   Concepts: {schema['concepts'][:5]} ...")


# ============================================================
# CELL 6B: Tool 3 — lookup_corpus
# ============================================================
def _lookup_corpus_impl(node_ids_str: str) -> str:
    """
    Retrieve full content, heading, and part for the given node IDs
    from the corpus. Use this when you need the actual text of a
    section to explain what an inserted/amended section DOES.

    Input: comma-separated node IDs, e.g. "ukpga_2023_55.xml_63,ukpga_2023_55.xml_64"
    Returns JSON with full content for each found chunk.
    """
    print(f"\n🔧 [TOOL CALL] lookup_corpus")
    print(f"   Node IDs: {node_ids_str!r}")

    # Parse comma-separated IDs, strip whitespace
    raw_ids = [nid.strip().strip('"\' ') for nid in node_ids_str.split(",")]
    # Filter empty strings
    node_ids = [nid for nid in raw_ids if nid]
    if not node_ids:
        return "⚠️ NO_IDS: No valid node IDs provided."

    results = []
    for nid in node_ids[:10]:  # Limit to 10 lookups
        chunk = corpus_lookup.get(nid)
        if chunk:
            content = chunk.get("content", "")
            # Strip formatted prefix to get clean text
            text_body = content
            if "| TEXT:" in text_body:
                text_body = text_body.split("| TEXT:", 1)[-1].strip()
            heading = chunk.get("heading", "")
            if heading and re.match(r'^[\s.]+$', heading):
                heading = ""
            results.append({
                "node_id":   nid,
                "doc_title": chunk.get("doc_title", ""),
                "section":   chunk.get("section", ""),
                "heading":   heading,
                "part":      chunk.get("part", ""),
                "content":   text_body[:800],
            })

    if not results:
        msg = "⚠️ NO_CONTENT_FOUND: None of the provided node IDs were found in the corpus."
        print(f"   ⚠️ {msg}")
        return msg

    output = json.dumps(results, indent=2)
    print(f"   ✅ Found {len(results)} chunks")
    return output


def lookup_corpus(node_ids_str: str) -> str:
    """Traced wrapper for lookup_corpus."""
    with _tracer.start_as_current_span("lookup_corpus", attributes={"node_ids": node_ids_str}):
        result = _lookup_corpus_impl(node_ids_str)
        trace.get_current_span().set_attribute("result_preview", result[:500])
        return result


# ============================================================
# CELL 7: System Prompt (GraphRAG-aware, strict guardrails)
# ============================================================
CONCEPTS_LIST = "\n".join(f"  - {c}" for c in schema["concepts"])
ACTION_TYPES  = ", ".join(schema["action_types"].keys())

SYSTEM_PROMPT = f"""You are a UK Legal Knowledge Graph agent. You answer legal questions by using THREE tools in a hybrid (GraphRAG) workflow.

## Your Three Tools

### Tool 1 — semantic_search(query, top_k=5)
- Searches the vector index for corpus chunks semantically similar to your query
- Returns: node_id, doc_title, section, heading, part, similarity score, text snippet
- **Always call this FIRST** to find relevant anchor nodes
- Use natural language queries, not Cypher

### Tool 2 — run_cypher(query)
- Executes Cypher against Neo4j and returns JSON results
- **Call this SECOND** using node_id values from semantic_search as anchors
- Returns "⚠️ ZERO_RESULTS" if query matches nothing — do NOT fabricate data

### Tool 3 — lookup_corpus(node_ids)
- Retrieves full section text, heading, and part for given node IDs
- **Call this when you need to explain WHAT a section does** (e.g., for inserted sections)
- Input: comma-separated node IDs: lookup_corpus("id1,id2,id3")

---

## ⚠️ CRITICAL GRAPH SCHEMA

- Node label: `:LegalDoc` with properties:
  - `id`        — e.g., "ukpga_2023_36.xml_1" (source documents)
  - `citation`  — e.g., "Housing Act 1985 s.252" (target references)
  - `title`     — act title
  - `heading`   — section heading (when available)
  - `part`      — part/chapter name (when available)
  - `content_snippet` — brief extract of section text
- Edge type: `:LEGAL_RELATIONSHIP` with property `action_type`
- Valid action_type values: {ACTION_TYPES}
- Edge properties: `r.detail` (amendment wording), `r.confidence`, `r.date`
- Concept nodes: `:Concept` linked via `(n:LegalDoc)-[:REGULATES]->(c:Concept)`

## Legal Domain Concepts
{CONCEPTS_LIST}

---

## Graph Stats
- Nodes: {schema['node_count']} | Edges: {schema['edge_count']}
- Sample edge:
{json.dumps(schema['sample_edges'][:2], indent=2, default=str)}

---

## Recommended Cypher Patterns

```cypher
// Find what an anchor Act DOES (outgoing relationships)
MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
WHERE s.id STARTS WITH 'ukpga_2023_36'
RETURN s.id, s.heading, r.action_type, r.detail, t.citation
LIMIT 25
```

```cypher
// Find what AFFECTS an anchor Act (incoming)
MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
WHERE t.citation CONTAINS 'Housing Act 1985'
RETURN s.id, s.title, r.action_type, r.detail, t.citation
LIMIT 25
```

```cypher
// Browse a legal concept
MATCH (n:LegalDoc)-[:REGULATES]->(c:Concept {{name: 'Employment and Labour'}})
MATCH (n)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
RETURN n.id, r.action_type, t.citation LIMIT 30
```

```cypher
// Filter by action type
MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP {{action_type: 'AMENDS'}}]->(t:LegalDoc)
WHERE s.id STARTS WITH 'ukpga_2023'
RETURN s.id, r.detail, t.citation LIMIT 25
```

---

## ⚠️ OUTPUT FORMATTING — MANDATORY

1. **NEVER output raw node IDs** like "ukpga_2023_55.xml_63" in your ANSWER.
   Convert ALL references to standard UK legal citations:
   - "Section 63 of the Levelling-up and Regeneration Act 2023"
   - "Section 104 of the Local Democracy, Economic Development and Construction Act 2009,
     as amended by Section 61 of the Levelling-up and Regeneration Act 2023"
2. Use `doc_title` and `section` from tool results to build readable citations.
3. When listing amendments, use proper legal citation format throughout.

---

## ⚠️ HANDLING INCOMPLETE DATA

1. If structural retrieval covers only SOME of the user's question, answer ONLY
   the parts you have evidence for.
2. For any part without evidence, state clearly:
   "The specific textual amendments for [topic] are not available in the current
   knowledge graph."
3. **NEVER use hedging language**: "likely targeted", "suggests", "hinted",
   "probably", "may have". These indicate speculation. If you don't have the
   data, SAY SO directly.
4. Do NOT stitch together a narrative from vague semantic search snippets when
   exact structural amendments are missing.

---

## POLICY CONTEXT SYNTHESIS

When presenting amendments, go beyond listing changes — explain the "So What?":
1. **Group** related amendments by theme (e.g., "Devolution streamlining",
   "Consumer protection", "Tenant safety")
2. **Explain** practical impact: What changed → Why it matters → Who is affected
3. Use `r.detail` and corpus content to infer the purpose of each change
4. If a cluster of sections was amended/inserted together, explain the
   policy narrative (e.g., "These amendments collectively streamline the
   process for creating Combined Authorities")

---

## SECTION DESCRIPTIONS

When listing inserted or amended sections, ALWAYS include:
- The section heading (from `heading` field or lookup_corpus results)
- A one-sentence summary of the section's function from `r.detail` or content
- Do NOT list bare section numbers like "s.104C" without explaining what they do
- If heading is not available, use lookup_corpus("node_id") to retrieve it

---

## ⚠️ ABSOLUTE GUARDRAILS — NON-NEGOTIABLE

1. **NEVER answer from general knowledge.** Only cite data returned by tools.
2. **If a tool returns ZERO_RESULTS or NO_MATCHES**, inform the user:
   "The knowledge graph does not contain information about [topic]."
3. **NEVER invent section numbers, Act names, or statutory provisions.**
4. **You must call semantic_search at least once BEFORE answering.**
5. Your ANSWER must cite specific citations from tool results.

---

## Response Format (STRICT — one action per turn)

THOUGHT: <your reasoning>
ACTION: semantic_search("<natural language query>", top_k=5)

OR:

THOUGHT: <reasoning about which node IDs to traverse>
ACTION: run_cypher("<Cypher query using node IDs from step 1>")

OR:

THOUGHT: <I need the full text of these sections to explain what they do>
ACTION: lookup_corpus("node_id_1,node_id_2")

OR (only after at least one successful tool call with data):

THOUGHT: <interpretation of results>
ANSWER: <answer using standard UK legal citations, grouped by theme, with policy context>

Output ONLY ONE ACTION per turn.
"""


# ============================================================
# CELL 8: Answer Post-Processing & Agent Loop
# ============================================================
def _clean_answer(answer: str) -> str:
    """
    Post-process the final answer to remove leaked internals:
    1. THOUGHT blocks that bleed into the answer
    2. Raw node IDs like ukpga_2023_55.xml_63
    3. Internal debug lines
    """
    # Strip any THOUGHT block that leaked before or into the answer
    answer = re.sub(r'THOUGHT:.*?(?=\n\n|\Z)', '', answer, flags=re.DOTALL).strip()

    # Remove raw node IDs in parentheses: (ukpga_2023_55.xml_63)
    answer = re.sub(r'\((?:ukpga|uksi|ewca|uksc|ewhc|ukut)_\d{4}_\d+\.xml_\w+\)', '', answer)

    # Replace inline raw node IDs — but convert to "[internal reference]" not just remove
    # Pattern: ukpga_2023_55.xml_63 or ewca_civ_2023_1.xml_para_1
    answer = re.sub(
        r'(?:ukpga|uksi|ewca|uksc|ewhc|ukut)_[\w]+_\d{4}_?\d*\.xml_[\w]+',
        '',
        answer
    )

    # Clean up double spaces / orphan brackets left behind
    answer = re.sub(r'\(\s*\)', '', answer)       # empty parens
    answer = re.sub(r'  +', ' ', answer)           # double spaces
    answer = re.sub(r'\n{3,}', '\n\n', answer)     # triple+ newlines

    return answer.strip()


def _extract_arg(action_line: str, tool_name: str) -> str:
    """
    Extract the first string argument from a tool call like:
      semantic_search("query text", top_k=5)
      run_cypher("MATCH ...")
    Handles single/double/backtick quotes.
    """
    # Find the opening paren after the tool name
    start = action_line.index(tool_name + "(") + len(tool_name) + 1
    # Find first quote char
    for quote in ('"', "'", '`'):
        if action_line[start:].lstrip().startswith(quote):
            s = action_line.index(quote, start)
            e = action_line.index(quote, s + 1)
            return action_line[s + 1:e]
    # Fallback: everything to the last closing paren
    depth = 1; i = start
    while i < len(action_line) and depth > 0:
        if action_line[i] == '(':  depth += 1
        elif action_line[i] == ')': depth -= 1
        i += 1
    return action_line[start:i - 1].strip().strip('"\'`')


def agent_ask(question: str, max_steps: int = 10) -> str:
    """
    GraphRAG ReAct agent loop.

    Phase 3 hybrid workflow:
      1. semantic_search  → find anchor node IDs
      2. run_cypher       → traverse from anchors
      3. ANSWER           → cite only tool results

    Phase 4 guardrails built-in.
    """
    with _tracer.start_as_current_span("agent_ask", attributes={
        "question": question,
        "max_steps": max_steps,
    }):
        print(f"\n{'#'*60}")
        print(f"  🔍 USER QUESTION: {question}")
        print(f"{'#'*60}")

        conversation        = question
        has_nonempty_result = False   # True once any tool returns real data
        semantic_done       = False   # True once semantic_search has been called
        step                = 0

        for step in range(1, max_steps + 1):
            print(f"\n--- Agent Step {step} ---")
            print(f"📤 [LLM REQUEST] Sending to Mistral ({MISTRAL_MODEL}) ...")

            response = client.chat.complete(
                model=MISTRAL_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": conversation},
                ],
                temperature=0.1,
            )

            llm_output = response.choices[0].message.content.strip()
            print(f"📥 [LLM RESPONSE]\n{llm_output}")

            # ── Parse ANSWER ──────────────────────────────────────────────────────
            if "ANSWER:" in llm_output:
                # Guardrail: must call at least one tool before answering
                if not semantic_done:
                    print("⚠️  Blocked ANSWER — agent must call semantic_search first")
                    conversation += (
                        f"\n\n{llm_output}\n\n"
                        "You MUST call semantic_search before answering. "
                        "Use: ACTION: semantic_search(\"<your query>\", top_k=5)"
                    )
                    continue

                answer = llm_output.split("ANSWER:")[-1].strip()

                # ── Clean answer: remove leaked internals ──
                answer = _clean_answer(answer)

                # Grounding warning: answered without real data
                if not has_nonempty_result:
                    print("⚠️  WARNING: Answering without non-empty tool results")
                    answer = (
                        "⚠️ Note: The knowledge graph tools did not return relevant data. "
                        "The following answer is NOT grounded in graph evidence and may be incomplete.\n\n"
                        + answer
                    )

                trace.get_current_span().set_attribute("answer", answer[:1000])
                trace.get_current_span().set_attribute("steps_taken", step)

                print(f"\n{'='*60}")
                print(f"  ✅ FINAL ANSWER (after {step} steps)")
                print(f"{'='*60}")
                print(answer)
                return answer

            # ── Parse ACTION ──────────────────────────────────────────────────────
            if "ACTION:" not in llm_output:
                print("⚠️  No ACTION or ANSWER found — nudging agent ...")
                conversation += (
                    f"\n\n{llm_output}\n\n"
                    "You must respond with either:\n"
                    "  ACTION: semantic_search(\"<query>\", top_k=5)\n"
                    "  ACTION: run_cypher(\"<Cypher>\")\n"
                    "  ANSWER: <your answer>\n"
                    "Output ONLY ONE ACTION per turn."
                )
                continue

            action_line = llm_output.split("ACTION:")[1].strip()
            # If multiple ACTIONs on the same line, take only the first
            if "ACTION:" in action_line:
                action_line = action_line.split("ACTION:")[0].strip()

            tool_result = None

            # ── semantic_search ───────────────────────────────────────────────────
            if "semantic_search(" in action_line:
                try:
                    query_text = _extract_arg(action_line, "semantic_search")
                    # Check for optional top_k= argument
                    top_k = 5
                    m = re.search(r"top_k\s*=\s*(\d+)", action_line)
                    if m:
                        top_k = int(m.group(1))
                    tool_result  = semantic_search(query_text, top_k=top_k)
                    semantic_done = True
                except Exception as e:
                    tool_result = f"TOOL PARSE ERROR (semantic_search): {e}"

            # ── run_cypher ────────────────────────────────────────────────────────
            elif "run_cypher(" in action_line:
                try:
                    cypher      = _extract_arg(action_line, "run_cypher")
                    tool_result = run_cypher(cypher)
                except Exception as e:
                    tool_result = f"TOOL PARSE ERROR (run_cypher): {e}"

            # ── lookup_corpus ─────────────────────────────────────────────────────
            elif "lookup_corpus(" in action_line:
                try:
                    ids_arg     = _extract_arg(action_line, "lookup_corpus")
                    tool_result = lookup_corpus(ids_arg)
                except Exception as e:
                    tool_result = f"TOOL PARSE ERROR (lookup_corpus): {e}"

            else:
                tool_result = (
                    "UNKNOWN TOOL. Available tools:\n"
                    "  semantic_search(\"<query>\", top_k=5)\n"
                    "  run_cypher(\"<Cypher query>\")\n"
                    "  lookup_corpus(\"node_id_1,node_id_2\")"
                )

            # Track whether we got real data back
            if tool_result and not tool_result.startswith("⚠️"):
                try:
                    parsed = json.loads(tool_result)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        has_nonempty_result = True
                    elif isinstance(parsed, dict) and parsed:
                        has_nonempty_result = True
                except json.JSONDecodeError:
                    pass

            conversation += (
                f"\n\n{llm_output}"
                f"\n\nOBSERVATION (tool result):\n{tool_result}"
                f"\n\nContinue reasoning. Use another ACTION if you need more data, "
                f"or respond with ANSWER: when you have enough grounded evidence."
            )

        return f"❌ Agent did not reach an answer within {max_steps} steps."


# ============================================================
# CELL 9: Quick Index Self-Test  (only when run directly)
# ============================================================
def _test_semantic_search():
    """Sanity check the FAISS index before running the agent."""
    print("\n🔬 Self-test: semantic_search ...")
    result = semantic_search("employment rights redundancy 2023", top_k=3)
    if result.startswith("⚠️"):
        print("   ⚠️  Self-test returned no results — check the FAISS index")
    else:
        hits = json.loads(result)
        print(f"   ✅ Self-test OK — top hit: {hits[0]['doc_title']} (sim={hits[0]['similarity']})")

if __name__ == "__main__":
    _test_semantic_search()


# ============================================================
# CELL 10: Run Test Questions  (only when run directly)
# ============================================================
if __name__ == "__main__":
    TEST_QUESTIONS = [
        # --- Hybrid: needs semantic anchor + graph traversal ---
        "Which 2023 Acts modify the Employment Rights Act 1996, and what type of changes does each make?",

        # --- Concept-level domain question ---
        # "What legislation regulates autonomous vehicles and self-driving car data in the UK?",

        # --- Detail extraction ---
        # "What is the exact wording substituted into section 107 of the Housing and Regeneration Act 2008?",

        # --- Null-result test (should trigger guardrail) ---
        # "What are the rules for cryptocurrency regulation under UK law?",

        # --- Finance ---
        # "What changes did the Finance Act 2023 make to the dividend allowance under the Income Tax Act 2007?",
    ]

    for q in TEST_QUESTIONS:
        result = agent_ask(q)
        print()

    # driver.close()
    # print("✅ Neo4j connection closed")
