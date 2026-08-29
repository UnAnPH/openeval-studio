#!/usr/bin/env python3
"""
osm_testcase_generator_task1.py
Task-1 test case generator (k-shortest) — produces graph.json conforming to the PDF schema,
queries.json (k-shortest), output_ref.json (exact k-shortest reference), and paths.html.

Usage:
  pip install osmnx networkx folium tqdm
  python osm_testcase_generator_task1.py --place "Bandra, Mumbai, India" --num_queries 6 --k 4

Outputs:
  - graph.json                # PDF schema (meta,nodes,edges)
  - queries.json              # k-shortest queries
  - output_ref.json           # reference results (exact k-shortest)
  - paths.html                # interactive map of reference paths

Reference PDF (runtime): /mnt/data/CS293 2025 Lab Project (1).pdf
"""
import argparse
import json
import random
from tqdm import tqdm

import osmnx as ox
import networkx as nx
import folium

# Allowed road types in the PDF (basic normalization)
ALLOWED_ROAD_TYPES = {"primary", "secondary", "tertiary", "local", "expressway"}


def safe_add_speeds_travel(G):
    """
    Add edge speeds and travel times using modern OSMnx APIs if available.
    Fallback: set travel_time = length / 10.0 (10 m/s) if not present.
    """
    if hasattr(ox, "routing") and hasattr(ox.routing, "add_edge_speeds"):
        G = ox.routing.add_edge_speeds(G)
        G = ox.routing.add_edge_travel_times(G)
    elif hasattr(ox, "add_edge_speeds"):
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    else:
        # best-effort fallback
        for u, v, k, d in G.edges(keys=True, data=True):
            if "length" not in d:
                d["length"] = 1.0
            if "travel_time" not in d:
                d["travel_time"] = d["length"] / 10.0
    return G


def map_highway_to_allowed(highway):
    """
    Normalize OSM 'highway' tag into one of the allowed road types.
    If unknown, return 'local'.
    """
    if not highway:
        return "local"
    if isinstance(highway, (list, tuple)):
        hw = highway[0]
    else:
        hw = str(highway)
    hw = hw.lower()

    if "motorway" in hw or "trunk" in hw or "primary" in hw:
        return "primary"
    if "secondary" in hw:
        return "secondary"
    if "tertiary" in hw:
        return "tertiary"
    if "service" in hw or "residential" in hw or "unclassified" in hw or "road" in hw:
        return "local"
    if "trunk" in hw or "trunk_link" in hw:
        return "expressway"
    for cand in ALLOWED_ROAD_TYPES:
        if cand in hw:
            return cand
    return "local"


def build_project_graph_from_osm(G_ox):
    """
    Convert OSMnx MultiDiGraph -> project graph structure (PDF schema)
    and return a NetworkX DiGraph (simple, collapsed parallel edges).
    """
    osm_to_idx = {}
    nodes = []
    # enumerate nodes -> 0..N-1
    for new_id, (osm_id, data) in enumerate(G_ox.nodes(data=True)):
        osm_to_idx[osm_id] = new_id
        lat = data.get("y", data.get("lat"))
        lon = data.get("x", data.get("lon"))
        nodes.append({
            "id": new_id,
            "lat": float(lat),
            "lon": float(lon),
            "pois": []
        })

    # collapse parallel edges by taking smallest-length edge between same u->v
    edge_best = {}  # (u_idx, v_idx) -> dict(length, travel_time, oneway, road_type)
    for u_osm, v_osm, key, data in G_ox.edges(keys=True, data=True):
        if u_osm not in osm_to_idx or v_osm not in osm_to_idx:
            continue
        u = osm_to_idx[u_osm]; v = osm_to_idx[v_osm]
        length = float(data.get("length", 0.0))
        travel_time = float(data.get("travel_time", length / 10.0))
        # respect oneway: OSM 'oneway' can be 'yes'/'no' or boolean
        oneway_raw = data.get("oneway", False)
        if isinstance(oneway_raw, str):
            oneway = oneway_raw.lower() in ("yes", "true", "1", "y")
        else:
            oneway = bool(oneway_raw)
        road_type = map_highway_to_allowed(data.get("highway"))
        key2 = (u, v)
        if key2 not in edge_best or length < edge_best[key2]["length"]:
            edge_best[key2] = {
                "length": length,
                "travel_time": travel_time,
                "oneway": oneway,
                "road_type": road_type
            }

    edges = []
    eid = 1001
    for (u, v), info in edge_best.items():
        edges.append({
            "id": eid,
            "u": int(u),
            "v": int(v),
            "length": float(info["length"]),
            "average_time": float(info["travel_time"]),
            "speed_profile": [],
            "oneway": bool(info["oneway"]),
            "road_type": str(info["road_type"])
        })
        eid += 1

    # build simple DiGraph for algorithmic work
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], lat=n["lat"], lon=n["lon"])
    for e in edges:
        # store both length and travel_time for potential future use
        G.add_edge(e["u"], e["v"], length=e["length"], travel_time=e["average_time"])
    project_graph = {
        "meta": {
            "id": "osm_generated",
            "nodes": len(nodes),
            "description": "OSM-extracted graph (generated) — conforms to CS293 PDF schema"
        },
        "nodes": nodes,
        "edges": edges
    }
    return G, project_graph


def sample_k_queries(G, num_queries, k_min=2, k_max=5, seed=42):
    """
    Sample queries that are connected (nx.has_path). Returns list of events in PDF format.
    """
    random.seed(seed)
    nodes = list(G.nodes())
    events = []
    attempts = 0
    while len(events) < num_queries and attempts < num_queries * 200:
        s = random.choice(nodes); t = random.choice(nodes)
        if s == t:
            attempts += 1
            continue
        if nx.has_path(G, s, t):
            k = random.randint(k_min, k_max)
            events.append({
                "type": "k_shortest_paths",
                "id": 1000 + len(events),
                "source": int(s),
                "target": int(t),
                "k": k,
                "mode": "distance"
            })
        attempts += 1
    return events


def compute_exact_k_shortest(G, s, t, k, max_search=2000):
    """
    Use networkx.shortest_simple_paths to return up to k simple paths ordered by distance.
    """
    res = []
    try:
        gen = nx.shortest_simple_paths(G, s, t, weight="length")
        for i, p in enumerate(gen):
            if i >= max_search:
                break
            # compute length
            length = 0.0
            for u, v in zip(p[:-1], p[1:]):
                length += float(G[u][v].get("length", 0.0))
            res.append({"path": [int(x) for x in p], "length": float(length)})
            if len(res) >= k:
                break
    except nx.NetworkXNoPath:
        pass
    except Exception:
        pass
    return res


def visualize_paths(project_graph, output_file, out_html):
    node_map = {n["id"]:(n["lat"], n["lon"]) for n in project_graph["nodes"]}
    data = json.load(open(output_file))
    results = data.get("results", [])
    coords = list(node_map.values())
    if not coords:
        center = (0, 0)
    else:
        center = (sum([c[0] for c in coords]) / len(coords), sum([c[1] for c in coords]) / len(coords))
    m = folium.Map(location=center, zoom_start=13)
    colors = ["green","red","blue","purple","orange","black","cadetblue","pink"]
    for r in results:
        qid = r.get("id")
        for i, p in enumerate(r.get("paths", [])):
            pts = []
            for nid in p.get("path", []):
                if nid in node_map:
                    pts.append(node_map[nid])
            if not pts:
                continue
            folium.PolyLine(pts, color=colors[i % len(colors)], weight=4, tooltip=f"q{qid} p{i} len={p.get('length',0):.1f}").add_to(m)
    m.save(out_html)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--place", required=True, help="Place name for OSM (e.g. 'Bandra, Mumbai, India')")
    parser.add_argument("--num_queries", type=int, default=6, help="Number of k-shortest queries to generate")
    parser.add_argument("--out_graph", default="graph.json", help="Output graph filename (JSON, PDF schema)")
    parser.add_argument("--out_queries", default="queries.json", help="Output queries filename")
    parser.add_argument("--out_ref", default="output_ref.json", help="Reference output filename (exact k-shortest)")
    parser.add_argument("--out_html", default="paths.html", help="Visualization HTML file")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("[+] Downloading OSM graph for:", args.place)
    G_ox = ox.graph_from_place(args.place, network_type="drive")
    G_ox = safe_add_speeds_travel(G_ox)

    print("[+] Converting to project graph (PDF schema).")
    G, project_graph = build_project_graph_from_osm(G_ox)

    # write graph.json (PDF schema)
    with open(args.out_graph, "w") as f:
        json.dump(project_graph, f, indent=2)
    print(f"[✓] Wrote graph: {args.out_graph}  (nodes={project_graph['meta']['nodes']}, edges={len(project_graph['edges'])})")

    # sample queries
    events = sample_k_queries(G, args.num_queries, seed=args.seed)
    queries_json = {"meta": {"id": "ksp_queries"}, "events": events}
    with open(args.out_queries, "w") as f:
        json.dump(queries_json, f, indent=2)
    print(f"[✓] Wrote queries: {args.out_queries} ({len(events)} events)")

    # compute exact k-shortest reference
    print("[+] Computing exact k-shortest (reference)...")
    results = []
    for ev in tqdm(events):
        paths = compute_exact_k_shortest(G, ev["source"], ev["target"], ev["k"])
        results.append({"id": ev["id"], "paths": paths})
    with open(args.out_ref, "w") as f:
        json.dump({"results": results}, f, indent=2)
    print(f"[✓] Wrote reference output: {args.out_ref}")

    # visualization
    visualize_paths(project_graph, args.out_ref, args.out_html)
    print(f"[✓] Wrote visualization: {args.out_html}")

    print("\nDone. Files produced:")
    print(" -", args.out_graph)
    print(" -", args.out_queries)
    print(" -", args.out_ref)
    print(" -", args.out_html)
    print("\nReference PDF (for schema): /mnt/data/CS293 2025 Lab Project (1).pdf")


if __name__ == "__main__":
    main()
