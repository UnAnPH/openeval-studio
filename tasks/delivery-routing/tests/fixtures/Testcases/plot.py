import argparse
import matplotlib.pyplot as plt
import networkx as nx
import math
from collections import defaultdict
import json

"""
plot_graph.py

Usage:
    pip install networkx matplotlib
    python plot_graph.py

Reads graph.json (set FILENAME) and writes graph_plot.png
"""


argparser = argparse.ArgumentParser()
argparser.add_argument("--filename", type=str, default="graph.json",
                       help="Path to graph.json file to plot")
args = argparser.parse_args()
FILENAME = args.filename

# --- config: colors for road types (add more if needed) ---
ROAD_COLORS = {
    "expressway": "#d62728",   # red
    "local": "#7f7f7f",        # gray
    "secondary": "#1f77b4",    # blue
    "primary": "#2ca02c",      # green
    "tertiary": "#ff7f0e",     # orange
}
FALLBACK_COLOR = "#8c564b"     # brown

# --- helper to scale widths visually ---


def edge_width_from_length(length, min_w=0.6, max_w=6.0):
    # length in meters (in sample). map to width logarithmically for nicer visuals
    if length <= 0:
        return min_w
    w = math.log1p(length) / 3.0  # tweak denominator to taste
    return max(min_w, min(max_w, w))


# --- load graph.json ---
with open(FILENAME, "r", encoding="utf-8") as f:
    data = json.load(f)

nodes = data.get("nodes", [])
edges = data.get("edges", [])

# --- build a directed graph so one-way edges can be drawn with arrowheads ---
G = nx.DiGraph()

# Add nodes with attributes and store positions (x=lon, y=lat)
pos = {}
for n in nodes:
    nid = n["id"]
    lat = n.get("lat")
    lon = n.get("lon")
    G.add_node(nid, **n)   # keep all attributes (pois etc.)
    if lat is not None and lon is not None:
        pos[nid] = (lon, lat)   # matplotlib x=longitude, y=latitude

# Add edges.
for e in edges:
    u = e["u"]
    v = e["v"]
    attrs = e.copy()
    G.add_edge(u, v, **attrs)

# --- group edges by road_type to draw them in batches (for legend/colors) ---
edges_by_type = defaultdict(list)
edge_styles = {}  # map (u,v) -> style attrs like color, width, arrow
for u, v, dat in G.edges(data=True):
    rtype = dat.get("road_type", None) or "unknown"
    color = ROAD_COLORS.get(rtype, FALLBACK_COLOR)
    width = edge_width_from_length(dat.get("length", 0))
    oneway = bool(dat.get("oneway", False))
    edges_by_type[rtype].append((u, v))
    edge_styles[(u, v)] = {"color": color, "width": width, "oneway": oneway}

# --- setup plot ---
plt.figure(figsize=(10, 9))
ax = plt.gca()
ax.set_aspect("equal", adjustable="box")

# Draw edges by groups
for rtype, edgelist in edges_by_type.items():
    # draw non-arrowed (oneway==False) and arrowed (oneway==True) separately for this road_type
    undirected_edges = [(u, v) for (
        u, v) in edgelist if not edge_styles[(u, v)]["oneway"]]
    directed_edges = [(u, v)
                      for (u, v) in edgelist if edge_styles[(u, v)]["oneway"]]

    # draw undirected-style as simple lines (arrows=False)
    if undirected_edges:
        widths_ud = [edge_styles[(u, v)]["width"]
                     for (u, v) in undirected_edges]
        colors_ud = [edge_styles[(u, v)]["color"]
                     for (u, v) in undirected_edges]
        nx.draw_networkx_edges(
            G, pos,
            edgelist=undirected_edges,
            width=widths_ud,
            edge_color=colors_ud,
            arrows=True,
            alpha=0.9,
            connectionstyle="arc3,rad=0.0",
            ax=ax
        )

    # draw directed edges with arrowheads
    if directed_edges:
        for (u, v) in directed_edges:
            w = edge_styles[(u, v)]["width"]
            c = edge_styles[(u, v)]["color"]
            nx.draw_networkx_edges(
                G, pos,
                edgelist=[(u, v)],
                width=w,
                edge_color=c,
                arrowsize=max(8, int(w * 3)),
                arrowstyle="-|>",
                connectionstyle="arc3,rad=0.0",
                ax=ax
            )

# Draw nodes (uniform styling, no POI labels)
# all_nodes = list(G.nodes())
# nx.draw_networkx_nodes(G, pos, nodelist=all_nodes, node_size=80, node_color="#4b8bbe", label="node")

# (No node labels, no POI annotations)

# final touches
plt.title("Graph plot from graph.json (lon=x, lat=y)")
plt.xlabel("Longitude")
plt.ylabel("Latitude")

# adjust margins to make sure labels are visible
plt.tight_layout()

# save & show
plt.savefig("graph_plot.png", dpi=300)
print("Saved graph_plot.png")
plt.show()
