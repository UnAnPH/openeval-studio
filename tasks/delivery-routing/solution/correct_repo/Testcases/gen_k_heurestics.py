#!/usr/bin/env python3
"""
gen_kheur_only.py

Generates:
 - graph.json                      (PDF schema, node ids 0..N-1)
 - <out_prefix>_queries.json       (meta + events[] of ONLY k_shortest_paths_heuristic)
 - <out_prefix>_reference.json     (exact k-shortest reference; convenience)

Usage:
  pip install osmnx networkx tqdm
  python gen_kheur_only.py --place "Bandra, Mumbai, India" --num_events 6 --k_max 4 --out_prefix bandra_kheur --seed 42

Notes:
 - Each event in events[] has shape:
   {
     "id": 0,
     "type": "k_shortest_paths_heuristic",
     "source": 410,
     "target": 58,
     "k": 2,
     "overlap_threshold": 20
   }
 - overlap_threshold is derived from chosen heuristic params (or default 20).
 - Reference PDF (schema) available at: sandbox:/mnt/data/CS293 2025 Lab Project (1).pdf
"""
import argparse, json, random, datetime
from tqdm import tqdm

import osmnx as ox
import networkx as nx

def safe_add_speeds_travel(G):
    if hasattr(ox, "routing") and hasattr(ox.routing, "add_edge_speeds"):
        G = ox.routing.add_edge_speeds(G)
        G = ox.routing.add_edge_travel_times(G)
    elif hasattr(ox, "add_edge_speeds"):
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    else:
        for u,v,k,d in G.edges(keys=True, data=True):
            if "length" not in d: d["length"]=1.0
            if "travel_time" not in d: d["travel_time"]=d["length"]/10.0
    return G

def map_highway(hw):
    if not hw: return "local"
    if isinstance(hw,(list,tuple)): hw = hw[0]
    hw = str(hw).lower()
    if "motorway" in hw or "primary" in hw: return "primary"
    if "secondary" in hw: return "secondary"
    if "tertiary" in hw: return "tertiary"
    if "trunk" in hw: return "expressway"
    return "local"

def build_project_graph(G_ox):
    osm_to_idx = {}
    nodes = []
    for new_id,(osm_id,data) in enumerate(G_ox.nodes(data=True)):
        osm_to_idx[osm_id] = new_id
        lat = data.get("y", data.get("lat"))
        lon = data.get("x", data.get("lon"))
        nodes.append({"id": new_id, "lat": float(lat), "lon": float(lon), "pois": []})
    edge_best = {}
    for u_osm,v_osm,key,data in G_ox.edges(keys=True, data=True):
        if u_osm not in osm_to_idx or v_osm not in osm_to_idx: continue
        u = osm_to_idx[u_osm]; v = osm_to_idx[v_osm]
        length = float(data.get("length", 0.0))
        travel_time = float(data.get("travel_time", length/10.0))
        oneway_raw = data.get("oneway", False)
        if isinstance(oneway_raw, str):
            oneway = oneway_raw.lower() in ("yes","true","1","y")
        else:
            oneway = bool(oneway_raw)
        road_type = map_highway(data.get("highway"))
        key2 = (u, v)
        if key2 not in edge_best or length < edge_best[key2]["length"]:
            edge_best[key2] = {"length": length, "travel_time": travel_time, "oneway": oneway, "road_type": road_type}
    edges=[]
    eid = 1001
    for (u,v),info in edge_best.items():
        edges.append({"id":eid,"u":int(u),"v":int(v),
                      "length":float(info["length"]),
                      "average_time":float(info["travel_time"]),
                      "speed_profile":[], "oneway":bool(info["oneway"]), "road_type":info["road_type"]})
        eid += 1
    G = nx.DiGraph()
    for n in nodes: G.add_node(n["id"], lat=n["lat"], lon=n["lon"])
    for e in edges: G.add_edge(e["u"], e["v"], length=e["length"], travel_time=e["average_time"])
    project = {"meta":{"id":"osm_kheur_only","nodes":len(nodes),"edges":len(edges),
                       "description":"OSM-generated graph (PDF schema)"},
               "nodes": nodes, "edges": edges}
    return G, project

def exact_k_shortest(G, s, t, k, max_search=2000):
    out=[]
    try:
        gen = nx.shortest_simple_paths(G, s, t, weight="length")
        for i,p in enumerate(gen):
            if i >= max_search: break
            length = sum(G[u][v].get("length",0.0) for u,v in zip(p[:-1], p[1:]))
            out.append({"path":[int(x) for x in p], "length": float(length)})
            if len(out) >= k: break
    except Exception:
        pass
    return out

def sample_events_only_heur(G, num_events, k_max, seed=42):
    random.seed(seed)
    nodes = list(G.nodes())
    events = []
    attempts = 0
    while len(events) < num_events and attempts < num_events * 200:
        s = random.choice(nodes); t = random.choice(nodes)
        if s == t:
            attempts += 1; continue
        if nx.has_path(G, s, t):
            k = random.randint(2, k_max)
            # choose heuristic variant and map to overlap_threshold
            variant = random.choice(["approx_error","time_budget","diversity"])
            if variant == "approx_error":
                # map acceptable_error to overlap_threshold default 20
                overlap_threshold = 20
            elif variant == "time_budget":
                overlap_threshold = 20
            else:
                lam = random.choice([10.0,50.0,150.0])
                overlap_threshold = int(max(1, min(100, round(lam/5.0))))
            events.append({
                "id": len(events),
                "type": "k_shortest_paths_heuristic",
                "source": int(s),
                "target": int(t),
                "k": int(k),
                "overlap_threshold": int(overlap_threshold)
            })
        attempts += 1
    return events

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--place", required=True)
    parser.add_argument("--num_events", type=int, default=6)
    parser.add_argument("--k_max", type=int, default=4)
    parser.add_argument("--out_prefix", default="kheur_only")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("[*] Downloading OSM graph for:", args.place)
    G_ox = ox.graph_from_place(args.place, network_type="drive")
    G_ox = safe_add_speeds_travel(G_ox)

    print("[*] Building project graph (PDF schema)")
    G, project_graph = build_project_graph(G_ox)
    graph_file = f"{args.out_prefix}_graph.json"
    with open(graph_file,"w") as f: json.dump(project_graph, f, indent=2)
    print(f"[✓] Wrote {graph_file} (nodes={len(project_graph['nodes'])} edges={len(project_graph['edges'])})")

    # sample only heuristic events
    events = sample_events_only_heur(G, args.num_events, args.k_max, seed=args.seed)
    meta = {
        "id": f"{args.out_prefix}_queries",
        "nodes": len(project_graph["nodes"]),
        "edges": len(project_graph["edges"]),
        "description": f"OSM heuristic-only testcases generated for {args.place}",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "constraints": {"nodes_max": 100000, "edges_max": 100000}
    }
    queries = {"meta": meta, "events": events}
    queries_file = f"{args.out_prefix}_queries.json"
    with open(queries_file,"w") as f: json.dump(queries, f, indent=2)
    print(f"[✓] Wrote {queries_file} ({len(events)} events)")

    # convenience: reference exact k-shortest
    print("[*] Computing reference exact k-shortest (convenience)")
    ref_results = {"results": []}
    for ev in tqdm(events):
        paths = exact_k_shortest(G, ev["source"], ev["target"], ev["k"])
        ref_results["results"].append({"id": ev["id"], "paths": paths})
    ref_file = f"{args.out_prefix}_reference.json"
    with open(ref_file,"w") as f: json.dump(ref_results, f, indent=2)
    print(f"[✓] Wrote {ref_file}")

    print("\nDone. Files:")
    print(" -", graph_file)
    print(" -", queries_file)
    print(" -", ref_file)
    print("\nReference PDF (schema): sandbox:/mnt/data/CS293 2025 Lab Project (1).pdf")

if __name__ == "__main__":
    main()
