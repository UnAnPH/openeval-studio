#!/usr/bin/env python3
"""
compare_task1.py
Comparator for Task-1 (k-shortest).
Compares reference output_ref.json with student_output.json
and generates:
    - console diff summary
    - compare.html (visual overlay)
"""

import json
import folium
import argparse

def load_coords(graph_file):
    g = json.load(open(graph_file))
    return {n["id"]: (n["lat"], n["lon"]) for n in g["nodes"]}

def compare_paths(ref, stu):
    diffs = []
    for r in ref["results"]:
        qid = r["id"]
        rpaths = r.get("paths", [])
        spaths = next((x.get("paths", []) for x in stu["results"] if x["id"] == qid), None)
        if spaths is None:
            diffs.append((qid, "MISSING STUDENT ENTRY"))
            continue
        if len(rpaths) != len(spaths):
            diffs.append((qid, f"DIFFERENT COUNT ref={len(rpaths)} student={len(spaths)}"))
        # check each path length match (sequence mismatch also causes length mismatch)
        for i, (rp, sp) in enumerate(zip(rpaths, spaths)):
            if abs(rp["length"] - sp["length"]) > 1e-3:
                diffs.append((qid, f"path {i} length mismatch ref={rp['length']} vs stu={sp['length']}"))
    return diffs

def build_overlay(graph_file, ref_file, stu_file, out_html):
    coords = {n["id"]: (n["lat"], n["lon"]) for n in json.load(open(graph_file))["nodes"]}
    ref = json.load(open(ref_file))
    stu = json.load(open(stu_file))

    # choose center of map
    lat, lon = list(coords.values())[0]
    m = folium.Map(location=(lat, lon), zoom_start=13)

    ref_fg = folium.FeatureGroup(name="Reference paths (GREEN)", overlay=True, control=True)
    stu_fg = folium.FeatureGroup(name="Student paths (RED)", overlay=True, control=True)

    # reference green
    for r in ref["results"]:
        for p in r["paths"]:
            pts = [coords[n] for n in p["path"]]
            folium.PolyLine(pts, color="green", weight=4).add_to(ref_fg)

            # label start / end
            s = p["path"][0]; t = p["path"][-1]
            folium.Marker(coords[s], icon=folium.DivIcon(html=f"<b style='color:green'>S</b>")).add_to(ref_fg)
            folium.Marker(coords[t], icon=folium.DivIcon(html=f"<b style='color:green'>E</b>")).add_to(ref_fg)

    # student red
    for r in stu["results"]:
        for p in r["paths"]:
            pts = [coords[n] for n in p["path"]]
            folium.PolyLine(pts, color="red", weight=3).add_to(stu_fg)

            # label start / end
            s = p["path"][0]; t = p["path"][-1]
            folium.Marker(coords[s], icon=folium.DivIcon(html=f"<b style='color:red'>S</b>")).add_to(stu_fg)
            folium.Marker(coords[t], icon=folium.DivIcon(html=f"<b style='color:red'>E</b>")).add_to(stu_fg)

    ref_fg.add_to(m)
    stu_fg.add_to(m)
    folium.LayerControl().add_to(m)
    m.save(out_html)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", default="graph.json")
    parser.add_argument("--ref", default="output_ref.json")
    parser.add_argument("--stu", default="student_output.json")
    parser.add_argument("--html", default="compare.html")
    args = parser.parse_args()

    print(f"[+] Comparing {args.ref}  VS  {args.stu}")
    ref = json.load(open(args.ref))
    stu = json.load(open(args.stu))

    diffs = compare_paths(ref, stu)
    if not diffs:
        print("🎉 PERFECT MATCH — student output identical to reference")
    else:
        print("❌ MISMATCHES FOUND:")
        for qid, msg in diffs:
            print(f"  Query {qid}: {msg}")

    print("[+] Generating visual overlay compare map ...")
    build_overlay(args.graph, args.ref, args.stu, args.html)
    print(f"[✓] compare.html generated → {args.html}")
    print("ℹ️  Green = reference   |   Red = student output")

if __name__ == "__main__":
    main()
