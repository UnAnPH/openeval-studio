#!/usr/bin/env python3
"""
compare_heuristic.py

Overlay:
 - Reference (black)
 - Expected heuristic (blue) [optional]
 - Student output (red)

Usage:
  pip install folium
  python compare_heuristic.py --graph bandra_kheur_graph.json --ref bandra_kheur_reference.json \
      --student student_output.json --expected bandra_kheur_expected.json --out compare.html
"""
import argparse, json, folium

def load_nodes(graph_file):
    pj = json.load(open(graph_file))
    return {n["id"]:(n["lat"], n["lon"]) for n in pj["nodes"]}

def add_layer(m, results, node_map, color, name, show=True):
    fg = folium.FeatureGroup(name=name, show=show)
    for r in results:
        qid = r.get("id")
        for idx, p in enumerate(r.get("paths", [])):
            pts = [node_map[n] for n in p["path"] if n in node_map]
            if not pts: continue
            folium.PolyLine(pts, color=color, weight=4, opacity=0.9, tooltip=f"{name} q{qid} p{idx} len={p.get('length',0):.1f}").add_to(fg)
            s = p["path"][0]; t = p["path"][-1]
            if s in node_map:
                folium.Marker(location=node_map[s], popup=f"{name} q{qid} p{idx} START", icon=folium.DivIcon(html=f"<div style='font-weight:bold;color:{color}'>S</div>")).add_to(fg)
            if t in node_map:
                folium.Marker(location=node_map[t], popup=f"{name} q{qid} p{idx} END", icon=folium.DivIcon(html=f"<div style='font-weight:bold;color:{color}'>E</div>")).add_to(fg)
    fg.add_to(m)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--student", required=True)
    parser.add_argument("--expected", default=None)
    parser.add_argument("--out", default="compare.html")
    args = parser.parse_args()

    node_map = load_nodes(args.graph)
    center = list(node_map.values())[0] if node_map else (0,0)
    m = folium.Map(location=center, zoom_start=13)

    # reference (black)
    ref = json.load(open(args.ref))["results"]
    add_layer(m, ref, node_map, color="#000000", name="Reference (black)")

    # expected heuristic (blue)
    if args.expected:
        exp = json.load(open(args.expected))["results"]
        add_layer(m, exp, node_map, color="#0000ff", name="Expected Heuristic (blue)")

    # student (red)
    stu = json.load(open(args.student))["results"]
    add_layer(m, stu, node_map, color="#ff0000", name="Student (red)")

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(args.out)
    print("[✓] wrote", args.out)

if __name__ == "__main__":
    main()
