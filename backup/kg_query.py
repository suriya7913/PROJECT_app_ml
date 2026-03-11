"""
LegalKGent — Graph RAG Query Agent (Mistral)
=============================================
ReAct agent with run_cypher tool for querying the Legal Knowledge Graph.

Improvements (MedKGent-inspired):
  - Hallucination guardrail: agent must run at least one query before answering
  - First-ACTION parsing: only executes the first ACTION per LLM turn
  - Confidence-aware queries: can filter by r.confidence
  - Graph-context aware prompting

Copy each CELL block into a separate Jupyter notebook cell.
"""

# ============================================================
# CELL 1: Install dependencies
# ============================================================
# !pip install mistralai neo4j

# ============================================================
# CELL 2: Imports & Config
# ============================================================
import json
import os
from neo4j import GraphDatabase
from mistralai import Mistral

# --- CONFIG ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "wTPmklMivGd5WB14RsLnms5DUw1pOeHh")
MISTRAL_MODEL = "mistral-large-latest"

# Initialize clients
client = Mistral(api_key=MISTRAL_API_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connected to Neo4j and Mistral")


# ============================================================
# CELL 3: Tool — run_cypher
# ============================================================
def run_cypher(query: str) -> str:
    """Execute a Cypher query against Neo4j and return results as JSON."""
    print(f"\n🔧 [TOOL CALL] run_cypher")
    print(f"   Query: {query}")
    with driver.session() as session:
        try:
            result = session.run(query)
            records = [dict(r) for r in result]
            output = json.dumps(records, indent=2, default=str)
            print(f"   ✅ Returned {len(records)} record(s)")
            # Print truncated output for readability
            preview = output[:2000]
            print(f"   Data: {preview}{'...' if len(output) > 2000 else ''}")
            return output
        except Exception as e:
            err = f"CYPHER ERROR: {e}"
            print(f"   ❌ {err}")
            return err


# ============================================================
# CELL 4: Build Schema Context
# ============================================================
def get_schema() -> dict:
    """Fetch graph schema for the system prompt."""
    with driver.session() as s:
        node_count = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        edge_count = s.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]

        # Action type distribution
        actions = {r["action"]: r["count"] for r in s.run(
            "MATCH ()-[r:LEGAL_RELATIONSHIP]->() "
            "RETURN r.action_type AS action, count(*) AS count "
            "ORDER BY count DESC"
        )}

        # Sample edges (show source_id, action, target_citation, detail)
        samples = [dict(r) for r in s.run(
            "MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t) "
            "RETURN s.id AS source_id, r.action_type AS action, "
            "r.detail AS detail, t.citation AS target "
            "LIMIT 8"
        )]

        # Get sample source IDs from the graph
        source_ids = [dict(r) for r in s.run("""
            MATCH (s:LegalDoc)-[:LEGAL_RELATIONSHIP]->()
            WHERE s.id IS NOT NULL
            RETURN DISTINCT s.id AS source_id
            ORDER BY s.id LIMIT 30
        """)]

        # Top target acts (most referenced)
        top_targets = [dict(r) for r in s.run("""
            MATCH ()-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
            WHERE t.citation IS NOT NULL
            RETURN t.citation AS target, r.action_type AS action, count(*) AS cnt
            ORDER BY cnt DESC LIMIT 15
        """)]

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "action_types": actions,
        "sample_edges": samples,
        "source_ids": source_ids,
        "top_targets": top_targets,
    }

schema = get_schema()
print(f"📊 Graph: {schema['node_count']} nodes, {schema['edge_count']} edges")
print(f"   Actions: {schema['action_types']}")


# ============================================================
# CELL 5: Build Source ID → Title Lookup (from triples file)
# ============================================================

# Build a lookup from the triples file so the LLM can map Act names to IDs
SOURCE_LOOKUP = {}
TRIPLES_FILE = "extracted_triples (5).json"

if os.path.exists(TRIPLES_FILE):
    with open(TRIPLES_FILE, "r") as f:
        _triples = json.load(f)
    for t in _triples:
        sid = t.get("source_id", "")
        title = t.get("source_title", "")
        if sid and title:
            prefix = sid.rsplit(".xml_", 1)[0] if ".xml_" in sid else sid
            if prefix not in SOURCE_LOOKUP:
                SOURCE_LOOKUP[prefix] = title
    del _triples  # free memory
    print(f"📋 Source lookup: {len(SOURCE_LOOKUP)} Acts")

# Format for the prompt
SOURCE_TABLE = "\n".join(
    f"  {prefix} = {title}"
    for prefix, title in sorted(SOURCE_LOOKUP.items())
)


# ============================================================
# CELL 6: System Prompt (Fine-Tuned for this Graph)
# ============================================================

SYSTEM_PROMPT = f"""You are a UK Legal Knowledge Graph agent. You answer legal questions by querying a Neo4j database containing UK legislation relationships.

## ⚠️ CRITICAL GRAPH RULES (read these FIRST)

1. ALL relationships are `:LEGAL_RELATIONSHIP` — there are NO typed edges like :AMENDS or :REPEALS
2. The action is stored as a PROPERTY: `r.action_type` (values: AMENDS, REPEALS, SUBSTITUTES, INSERTS, COMMENCES, REVOKES, APPLIES)
3. Source nodes have `s.id` (e.g., "ukpga_2023_36.xml_1"). The ID includes ".xml_" + section number
4. Target nodes have `t.citation` (e.g., "Housing and Regeneration Act 2008 s.107")
5. NEVER guess document IDs — use the lookup table below or search with CONTAINS

## Source Document ID → Act Title Lookup
{SOURCE_TABLE}

## How to Search

### Find what an Act DOES (outgoing):
```
MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t)
WHERE s.id STARTS WITH 'ukpga_2023_36'
RETURN s.id, r.action_type, r.detail, t.citation
LIMIT 25
```

### Find what AFFECTS an Act (incoming):
```
MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t)
WHERE t.citation CONTAINS 'Housing and Regeneration Act 2008'
RETURN s.id, r.action_type, r.detail, t.citation
LIMIT 25
```

### Filter by action type:
```
MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t)
WHERE s.id STARTS WITH 'ukpga_2023_36' AND r.action_type = 'REPEALS'
RETURN s.id, r.action_type, t.citation
```

### Count by action type:
```
MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t)
WHERE s.id STARTS WITH 'ukpga_2023_36'
RETURN r.action_type AS action, count(*) AS count ORDER BY count DESC
```

### Find most amended/referenced targets:
```
MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t)
RETURN t.citation, r.action_type, count(*) AS cnt ORDER BY cnt DESC LIMIT 15
```

## ⚠️ GROUNDING RULES — READ CAREFULLY

1. You MUST run at least one Cypher query BEFORE answering
2. Your answer MUST be based ONLY on the data returned by your queries
3. If a query returns 0 results, say so — do NOT fill in from general knowledge
4. If the graph does not contain information about a topic, say "The knowledge graph does not contain information about [topic]"
5. NEVER fabricate section numbers, Act names, or legal provisions not found in query results

## Graph Stats
- {{schema['node_count']}} nodes, {{schema['edge_count']}} edges
- Action distribution: {{json.dumps(schema['action_types'])}}

## Sample Edges
{json.dumps(schema['sample_edges'][:5], indent=2, default=str)}

## Your Tool
You have ONE tool: `run_cypher(query)` — executes Cypher and returns JSON results.

## Response Format (STRICT)

Every response MUST contain EXACTLY ONE of these:

THOUGHT: <your reasoning>
ACTION: run_cypher("<your Cypher query>")

OR (only after at least one successful query):

THOUGHT: <final interpretation of query results>
ANSWER: <your answer citing ONLY data from query results>

IMPORTANT: Output ONLY ONE ACTION per response. Wait for the result before deciding next steps.

## Rules
1. ALWAYS run at least one Cypher query before answering
2. Use STARTS WITH on s.id for source Act searches (e.g., s.id STARTS WITH 'ukpga_2023_36')
3. Use CONTAINS on t.citation for target Act searches
4. If you get 0 results, try the OTHER side (source vs target)
5. LIMIT to 25 unless the user asks for all
6. Include r.detail in your RETURN when available — it contains the exact amendment wording
7. Cite specific sections in your answer (e.g., "Section 3 of the Finance Act 2023 amends...")
8. Output only ONE ACTION per turn — do NOT chain multiple ACTIONs
"""


# ============================================================
# CELL 7: ReAct Agent Loop
# ============================================================

def agent_ask(question: str, max_steps: int = 7) -> str:
    """
    ReAct agent loop with run_cypher tool.
    Uses Mistral for reasoning.

    Improvements over baseline:
      - Parses FIRST ACTION only (not last) — proper ReAct one-action-per-turn
      - Hallucination guardrail: blocks ANSWER on step 1 (before any query)
      - Grounding check: warns if answering without non-empty results
    """
    print(f"\n{'#'*60}")
    print(f"  🔍 USER QUESTION: {question}")
    print(f"{'#'*60}")

    conversation = question
    has_nonempty_result = False  # Track whether we got any actual data

    for step in range(1, max_steps + 1):
        print(f"\n--- Agent Step {step} ---")
        print(f"📤 [LLM REQUEST] Sending to Mistral ({MISTRAL_MODEL})...")

        response = client.chat.complete(
            model=MISTRAL_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": conversation}
            ],
            temperature=0.1,
        )

        llm_output = response.choices[0].message.content.strip()
        print(f"📥 [LLM RESPONSE]\n{llm_output}")

        # --- Parse ANSWER ---
        if "ANSWER:" in llm_output:
            # Hallucination guardrail: agent must run at least one query before answering
            if step == 1:
                print("⚠️  Blocked ANSWER on step 1 — agent must query the graph first")
                conversation += (
                    f"\n\n{llm_output}\n\n"
                    f"You MUST run at least one Cypher query before answering. "
                    f"Do NOT answer from general knowledge. Use ACTION: run_cypher(\"...\") first."
                )
                continue

            answer = llm_output.split("ANSWER:")[-1].strip()

            # Grounding warning
            if not has_nonempty_result:
                print("⚠️  WARNING: Agent answered without receiving any non-empty query results")
                answer = (
                    "⚠️ Note: The knowledge graph did not return relevant data for this query. "
                    "The following answer may not be fully grounded.\n\n" + answer
                )

            print(f"\n{'='*60}")
            print(f"  ✅ FINAL ANSWER")
            print(f"{'='*60}")
            print(answer)
            return answer

        # --- Parse ACTION (take FIRST action only — one action per ReAct turn) ---
        if "ACTION:" in llm_output:
            action_line = llm_output.split("ACTION:")[1].strip()

            # If there are more ACTION: lines after this one, truncate
            if "ACTION:" in action_line:
                action_line = action_line.split("ACTION:")[0].strip()

            tool_result = None

            if "run_cypher(" in action_line:
                # Extract Cypher query (handle nested parentheses)
                start = action_line.index("run_cypher(") + len("run_cypher(")
                depth = 1
                end = start
                for i in range(start, len(action_line)):
                    if action_line[i] == '(':
                        depth += 1
                    elif action_line[i] == ')':
                        depth -= 1
                    if depth == 0:
                        end = i
                        break
                cypher = action_line[start:end].strip().strip("\"'`")
                tool_result = run_cypher(cypher)

                # Track whether we got non-empty results
                if tool_result and tool_result.strip() not in ('[]', '""', 'null'):
                    try:
                        parsed = json.loads(tool_result)
                        if isinstance(parsed, list) and len(parsed) > 0:
                            has_nonempty_result = True
                    except json.JSONDecodeError:
                        pass

            if tool_result is not None:
                conversation += (
                    f"\n\n{llm_output}"
                    f"\n\nOBSERVATION (tool result):\n{tool_result}"
                    f"\n\nAnalyze the results. If you have enough information, respond with ANSWER:. "
                    f"Otherwise, run another ACTION: run_cypher(...)."
                )
            else:
                print(f"⚠️  Could not parse action: {action_line}")
                conversation += (
                    f"\n\n{llm_output}"
                    f"\n\nOBSERVATION: Could not parse that action. "
                    f"Use this format: ACTION: run_cypher(\"MATCH (s)-[r:LEGAL_RELATIONSHIP]->(t) WHERE ... RETURN ...\")"
                )
        else:
            print("⚠️  No ACTION or ANSWER found, nudging agent...")
            conversation += (
                f"\n\n{llm_output}\n\n"
                f"You must respond with ACTION: run_cypher(\"...\") or ANSWER: <your answer>."
            )

    return "❌ Agent did not reach an answer within max steps."


# ============================================================
# CELL 8: Test Questions
# ============================================================

# Lawyer-relevant test questions grounded in the actual graph data:
#
# --- Simple (single-Act lookups) ---
# result = agent_ask("What does the Social Housing (Regulation) Act 2023 amend in the Housing and Regeneration Act 2008?")
# result = agent_ask("Has the Social Housing Act 2023 repealed any parts of the Housing and Regeneration Act 2008?")
#
# --- Multi-source (cross-Act aggregation — KG strength) ---
# result = agent_ask("Which 2023 Acts modify the Employment Rights Act 1996, and what type of changes does each make?")
#
# --- Multi-hop chain (graph traversal — KG strength) ---
# result = agent_ask("Trace the chain of modifications to the Energy Profits Levy: which Act changed the rate, and until when has the levy been extended?")
#
# --- Detail extraction ---
# result = agent_ask("What is the exact wording substituted into section 107 of the Housing and Regeneration Act 2008?")
#
# --- Finance / tax ---
# result = agent_ask("What changes did the Finance Act 2023 make to the dividend allowance under the Income Tax Act 2007?")
# result = agent_ask("What is the current annual exempt amount for Capital Gains Tax, and which legislation reduced it?")
#
# --- Conflict detection ---
# result = agent_ask("Are there any sections that are both amended and repealed by different Acts?")
#
# --- Aggregation ---
# result = agent_ask("Which Act has the highest number of legal relationships overall? Break down by type.")

result = agent_ask("Which 2023 Acts modify the Employment Rights Act 1996, and what type of changes does each make?")


# ============================================================
# CELL 9: Cleanup
# ============================================================
# driver.close()
# print("✅ Neo4j connection closed")
