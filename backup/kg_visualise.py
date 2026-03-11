################################################################
# CELL 11 — Install Visualization Libraries
################################################################

!pip install pyvis networkx -q
print("✅ Cell 11 done — pyvis & networkx installed")


################################################################
# CELL 12 — Interactive Knowledge Graph (PyVis)
# Creates a beautiful interactive HTML graph
################################################################

import json
from pyvis.network import Network

with open("data/extracted_triples.json", "r") as f:
    triples = json.load(f)

# Color map for relationship types
ACTION_COLORS = {
    "REPEALS": "#e74c3c",      # Red
    "AMENDS": "#f39c12",       # Orange
    "SUBSTITUTES": "#9b59b6",  # Purple
    "INSERTS": "#2ecc71",      # Green
    "COMMENCES": "#3498db",    # Blue
    "REVOKES": "#e67e22",      # Dark orange
    "OVERRULES": "#c0392b",    # Dark red
    "APPLIES": "#1abc9c",      # Teal
    "CITES": "#95a5a6",        # Gray
}

# Create network
net = Network(
    height="700px",
    width="100%",
    bgcolor="#1a1a2e",        # Dark background
    font_color="#ffffff",
    directed=True,
    notebook=True,            # Required for Colab
    cdn_resources="remote"    # Use CDN for Colab compatibility
)

# Physics settings for nice layout
net.barnes_hut(
    gravity=-3000,
    central_gravity=0.3,
    spring_length=200,
    spring_strength=0.05
)

# Track unique nodes (filter out None values)
source_nodes = set()
target_nodes = set()

for t in triples:
    source_nodes.add(t["source_id"])
    if t.get("target_citation"):  # Skip None/null citations
        target_nodes.add(t["target_citation"])

# Add source nodes (legislation sections) — smaller, blue
for node in source_nodes:
    # Shorten label: "ukpga_2023_53.xml_10" -> "2023 c.53 s.10"
    label = node.replace("ukpga_", "").replace(".xml_", " s.").replace("uksc_", "UKSC ")
    net.add_node(
        node,
        label=label,
        color="#3498db",
        size=15,
        shape="dot",
        title=f"Source: {node}"  # Tooltip on hovertitle
    )

# Add target nodes (cited/affected laws) — larger, gold
for node in target_nodes:
    # Shorten long citations for display
    label = node[:50] + "..." if len(node) > 50 else node
    net.add_node(
        node,
        label=label,
        color="#f1c40f",
        size=25,
        shape="dot",
        title=f"Target: {node}"
    )

# Add edges with colors by action type
for t in triples:
    if not t.get("target_citation"):  # Skip null targets
        continue
    color = ACTION_COLORS.get(t["action"], "#ffffff")
    detail = t.get("detail_text", "")
    date = t.get("effective_date", "")
    tooltip = f"{t['action']}"
    if date: tooltip += f" | Date: {date}"
    if detail: tooltip += f" | Detail: {detail[:100]}"

    net.add_edge(
        t["source_id"],
        t["target_citation"],
        label=t["action"],
        color=color,
        width=2,
        title=tooltip,
        arrows="to"
    )

# Save and display
net.show("kg_visualization.html")
print(f"✅ Interactive graph saved to kg_visualization.html")
print(f"   {len(source_nodes)} source nodes")
print(f"   {len(target_nodes)} target nodes")
print(f"   {len(triples)} edges")

# On Colab, display inline:
from IPython.display import HTML, display
display(HTML("kg_visualization.html"))


################################################################
# CELL 13 — Static Network Graph (NetworkX + Matplotlib)
# Good for saving as PNG for your report/thesis
################################################################

import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

with open("data/extracted_triples.json", "r") as f:
    triples = json.load(f)

G = nx.DiGraph()

# Add edges
for t in triples:
    G.add_edge(
        t["source_id"],
        t["target_citation"],
        action=t["action"],
        detail=t.get("detail_text", ""),
        date=t.get("effective_date", "")
    )

# Node colors: source = blue, target = gold
node_colors = []
for node in G.nodes():
    if any(node == t["source_id"] for t in triples):
        node_colors.append("#3498db")
    else:
        node_colors.append("#f1c40f")

# Edge colors by action
edge_colors = []
for u, v, data in G.edges(data=True):
    edge_colors.append(ACTION_COLORS.get(data["action"], "#95a5a6"))

# Shorten labels
labels = {}
for node in G.nodes():
    short = node.replace("ukpga_", "").replace(".xml_", " s.")
    if len(short) > 30:
        short = short[:27] + "..."
    labels[node] = short

# Draw
fig, ax = plt.subplots(1, 1, figsize=(18, 12))
fig.patch.set_facecolor('#1a1a2e')
ax.set_facecolor('#1a1a2e')

pos = nx.spring_layout(G, k=2.5, iterations=50, seed=42)

# Draw edges
nx.draw_networkx_edges(
    G, pos, ax=ax,
    edge_color=edge_colors,
    width=2,
    alpha=0.7,
    arrows=True,
    arrowsize=20,
    connectionstyle="arc3,rad=0.1"
)

# Draw nodes
nx.draw_networkx_nodes(
    G, pos, ax=ax,
    node_color=node_colors,
    node_size=800,
    alpha=0.9,
    edgecolors='white',
    linewidths=1
)

# Draw labels
nx.draw_networkx_labels(
    G, pos, labels, ax=ax,
    font_size=7,
    font_color='white',
    font_weight='bold'
)

# Legend
legend_patches = []
for action, color in ACTION_COLORS.items():
    count = sum(1 for t in triples if t["action"] == action)
    if count > 0:
        legend_patches.append(mpatches.Patch(color=color, label=f"{action} ({count})"))

legend_patches.append(mpatches.Patch(color="#3498db", label="Source Section"))
legend_patches.append(mpatches.Patch(color="#f1c40f", label="Target Law"))

ax.legend(
    handles=legend_patches,
    loc='upper left',
    fontsize=9,
    facecolor='#16213e',
    edgecolor='white',
    labelcolor='white'
)

ax.set_title(
    "LegalKGent — UK Legal Knowledge Graph (50-chunk sample)",
    color='white',
    fontsize=16,
    fontweight='bold',
    pad=20
)
ax.axis('off')
plt.tight_layout()
plt.savefig("kg_graph.png", dpi=200, bbox_inches='tight', facecolor='#1a1a2e')
plt.show()
print("✅ Static graph saved to kg_graph.png")


################################################################
# CELL 14 — Download visualizations
################################################################

from google.colab import files
files.download("kg_visualization.html")   # Interactive
files.download("kg_graph.png")            # Static for report
print("✅ Downloads started")
