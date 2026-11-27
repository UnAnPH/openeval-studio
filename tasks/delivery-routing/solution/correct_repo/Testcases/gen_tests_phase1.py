#!/usr/bin/env python3
"""
gen_test_phase1.py

Generates:
 - graph.json (nodes + edges)
 - queries.json (events)

Defaults:
 - nodes: 5000
 - edges: 50000
 - queries: 1000

Usage:
  python gen_test_phase1.py
  python gen_test_phase1.py --nodes 1000 --edges 5000 --queries 500 --out_dir ./sample

Notes:
 - speed_profile contains 96 slots (phase1 requirement)
 - average_time is computed from length (meters) and mean speed (km/h)
 - ensures graph is connected by creating a spanning tree first, then adds remaining random edges
"""
import json
import random
import math
import argparse
import os
from typing import List, Dict

# ---- utils ----
def haversine_m(lat1, lon1, lat2, lon2):
    # return distance in meters between two lat/lon
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def clamp(x, lo, hi): return max(lo, min(hi, x))

# ---- generation functions ----
def gen_nodes(n_nodes: int, bbox=None, poi_list=None, poi_prob=0.35, seed=None):
    """
    bbox: (lat_min, lat_max, lon_min, lon_max)
    poi_prob: probability a node has at least one POI
    """
    if seed is not None: random.seed(seed)
    if bbox is None:
    
        bbox = (40.0, 42.5, -74.5, -72.0)
    lat_min, lat_max, lon_min, lon_max = bbox
    if poi_list is None:
        poi_list = ["petrol station", "atm", "pharmacy", "hospital", "restaurant"]

    nodes = []
    for i in range(n_nodes):
        lat = random.uniform(lat_min, lat_max)
        lon = random.uniform(lon_min, lon_max)
        pois = []
        if random.random() < poi_prob:
            # choose 1..3 POIs
            k = random.randint(1, min(3, len(poi_list)))
            pois = random.sample(poi_list, k)
        nodes.append({
            "id": i,
            "lat": lat,
            "lon": lon,
            "pois": pois
        })
    return nodes

def gen_speed_profile(slots=96, mean=22.0, sigma=3.0, min_v=5.0, max_v=40.0):
    prof = [clamp(random.gauss(mean, sigma), min_v, max_v) for _ in range(slots)]
    # round to 6 decimals like sample
    return [float(round(x, 12)) for x in prof]

def gen_edges(n_nodes: int, n_edges: int, nodes: List[Dict], seed=None, start_edge_id=1000):
    if seed is not None: random.seed(seed + 1)
    if n_edges < n_nodes - 1:
        raise ValueError("n_edges must be at least n_nodes-1 to make graph connected")

    road_types = ["local", "secondary", "tertiary", "primary", "expressway"]
    edges = []
    edge_id = start_edge_id

    # Helper to create an edge dict
    def make_edge(u, v, eid):
        # compute length in meters via haversine
        a = nodes[u]
        b = nodes[v]
        length = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        speed_profile = gen_speed_profile()
        mean_speed = sum(speed_profile)/len(speed_profile)  # km/h
        # average_time in seconds = length(m) / (mean_speed(km/h) * 1000/3600)
        # = length * 3.6 / mean_speed
        average_time = length * 3.6 / mean_speed if mean_speed > 0 else length
        return {
            "id": eid,
            "u": u,
            "v": v,
            "length": float(round(length, 6)),
            "average_time": float(round(average_time, 6)),
            "speed_profile": [float(round(s, 12)) for s in speed_profile],
            "oneway": random.choice([False, False, False, True]),  # mostly two-way
            "road_type": random.choice(road_types)
        }

    # 1) Build a spanning tree (chain) to ensure connectivity
    available_nodes = list(range(n_nodes))
    random.shuffle(available_nodes)
    for i in range(1, n_nodes):
        u = available_nodes[i-1]
        v = available_nodes[i]
        edges.append(make_edge(u, v, edge_id))
        edge_id += 1

    # 2) Add remaining random edges
    needed = n_edges - (n_nodes - 1)
    seen_pairs = set((min(e["u"], e["v"]), max(e["u"], e["v"])) for e in edges)
    attempts = 0
    max_attempts = needed * 10 + 1000
    while needed > 0 and attempts < max_attempts:
        u = random.randrange(0, n_nodes)
        v = random.randrange(0, n_nodes)
        if u == v:
            attempts += 1
            continue
        pair = (min(u,v), max(u,v))
        if pair in seen_pairs:
            attempts += 1
            continue
        edges.append(make_edge(u, v, edge_id))
        seen_pairs.add(pair)
        edge_id += 1
        needed -= 1
    if needed > 0:
        raise RuntimeError(f"Could not generate enough unique edges; still need {needed}")

    return edges

# ---- queries generator ----
def gen_queries(n_nodes: int, edges: List[Dict], n_queries: int, seed=None):
    if seed is not None: random.seed(seed + 2)
    events = []
    edge_ids = [e["id"] for e in edges]

    poi_choices = ["pharmacy", "restaurant", "petrol station", "hospital", "atm"]
    road_types = ["local", "secondary", "tertiary", "primary", "expressway"]
    next_qid = 1

    for _ in range(n_queries):
        t = random.random()
        if t < 0.30:
            # knn
            poi = random.choice(poi_choices)
            # pick a random point inside the nodes bbox (approx)
            sample_node = random.choice(range(n_nodes))
            qlat = round((random.uniform(-0.999, 0.999) + 0) + 0, 12)  # we'll actually use node coords below
            # but better to use a node's lat/lon:
            # for realism, use a random node coordinate
            # (we'll override qlat, qlon properly)
            # fill query_point from actual node to be consistent
            src_node = random.randrange(0, n_nodes)
            qlat = nodes_global[src_node]["lat"]
            qlon = nodes_global[src_node]["lon"]
            k = random.randint(1, 10)
            metric = random.choice(["euclidean", "shortest_path"])
            ev = {
                "type": "knn",
                "id": next_qid,
                "poi": poi,
                "query_point": {"lat": float(round(qlat, 12)), "lon": float(round(qlon, 12))},
                "k": k,
                "metric": metric
            }
        elif t < 0.75:
            # shortest_path
            s = random.randrange(0, n_nodes)
            tt = random.randrange(0, n_nodes)
            while tt == s:
                tt = random.randrange(0, n_nodes)
            mode = random.choice(["time", "distance"])
            constraints = {}
            # randomly add constraints
            if random.random() < 0.25:
                # forbidden_nodes: up to 4 nodes
                fn = random.sample(range(n_nodes), k=min(4, max(1, n_nodes//1000)))
                constraints["forbidden_nodes"] = fn
            if random.random() < 0.25:
                frt = random.sample(road_types, k=random.randint(1,2))
                constraints["forbidden_road_types"] = frt
            ev = {
                "type": "shortest_path",
                "id": next_qid,
                "source": s,
                "target": tt,
                "mode": mode,
                "constraints": constraints
            }
        else:
            # dynamic update: remove_edge or modify_edge (50/50)
            if random.random() < 0.5:
                # remove_edge
                chosen = random.choice(edge_ids)
                ev = {"id": next_qid, "type": "remove_edge", "edge_id": chosen}
            else:
                chosen = random.choice(edge_ids)
                patch = {}
                # choose which fields to patch
                if random.random() < 0.6:
                    patch["length"] = float(round(random.uniform(50.0, 5000.0), 6))
                if random.random() < 0.6:
                    patch["average_time"] = float(round(random.uniform(10.0, 10000.0), 6))
                if random.random() < 0.4:
                    # full new speed_profile sometimes
                    patch["speed_profile"] = gen_speed_profile()
                if random.random() < 0.3:
                    patch["road_type"] = random.choice(road_types)
                ev = {"id": next_qid, "type": "modify_edge", "edge_id": chosen, "patch": patch}
        events.append(ev)
        next_qid += 1
    return events

# ---- main ----
def main():
    parser = argparse.ArgumentParser(description="Generate graph.json and queries.json for Phase 1 testing")
    parser.add_argument("--nodes", type=int, default=5000, help="number of nodes (default 5000)")
    parser.add_argument("--edges", type=int, default=50000, help="number of edges (default 50000)")
    parser.add_argument("--queries", type=int, default=1000, help="number of queries/events (default 1000)")
    parser.add_argument("--out_dir", type=str, default=".", help="output directory (default current dir)")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--graph_name", type=str, default="generated_graph", help="prefix for graph id/filename")
    parser.add_argument("--queries_name", type=str, default="generated_queries", help="prefix for queries id/filename")
    args = parser.parse_args()

    n_nodes = args.nodes
    n_edges = args.edges
    n_queries = args.queries
    out_dir = args.out_dir
    seed = args.seed

    os.makedirs(out_dir, exist_ok=True)

    # generate nodes
    global nodes_global
    nodes_global = gen_nodes(n_nodes, seed=seed)

    # generate edges (ensures connectivity)
    edges = gen_edges(n_nodes, n_edges, nodes_global, seed=seed)

    # assemble graph object
    graph_obj = {
        "meta": {
            "id": f"{args.graph_name}_{n_nodes}n_{n_edges}e",
            "nodes": n_nodes,
            "description": "Auto-generated complex graph for testing"
        },
        "nodes": nodes_global,
        "edges": edges
    }

    graph_path = os.path.join(out_dir, f"{graph_obj['meta']['id']}_graph.json")
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph_obj, f, indent=4)
    print(f"Wrote graph -> {graph_path}")

    # generate queries
    events = gen_queries(n_nodes, edges, n_queries, seed=seed)
    queries_obj = {
        "meta": {"id": f"{args.queries_name}_{n_queries}"},
        "events": events
    }
    queries_path = os.path.join(out_dir, f"{queries_obj['meta']['id']}_queries.json")
    with open(queries_path, "w", encoding="utf-8") as f:
        json.dump(queries_obj, f, indent=4)
    print(f"Wrote queries -> {queries_path}")

if __name__ == "__main__":
    # nodes_global is referenced in gen_queries; initialize here to silence linters.
    nodes_global = []
    main()
