import json
import os
import subprocess
import sys
import math

def load_graph_time(g_json, time=False):
    graph = {}
    nodes = set()
    for node in g_json.get("nodes", []):
        nid = node["id"]
        nodes.add(nid)
        graph.setdefault(nid, {})
    for e in g_json.get("edges", []):
        u, v = e["u"], e["v"]
        w = e.get("average_time") if time else e.get("length")
        if w is None:
            raise ValueError(f"Edge {e.get('id')} missing weight")
        graph.setdefault(u, {})[v] = float(w)
        if not e.get("oneway", False):
            graph.setdefault(v, {})[u] = float(w)
    for n in nodes:
        graph.setdefault(n, {})
    return graph, nodes

def verify_assignments(graph, nodes_set, event, result):
    if "orders" not in event:
        return False, "Input event missing 'orders'"
    orders = event["orders"]
    order_map = {}
    for o in orders:
        oid = o.get("order_id")
        if oid is None:
            return False, "An order is missing 'order_id'"
        if oid in order_map:
            return False, f"Duplicate order_id: {oid}"
        p, d = o.get("pickup"), o.get("dropoff")
        if p is None or d is None:
            return False, f"Order {oid} missing pickup/dropoff"
        order_map[oid] = (p, d)
    total_orders = set(order_map.keys())
    if "assignments" not in result:
        return False, "Output missing 'assignments'"
    assignments = result["assignments"]
    if not isinstance(assignments, list):
        return False, "'assignments' must be a list"
    coverage = {oid: 0 for oid in total_orders}
    total_penalty_time = 0.0
    for a in assignments:
        route = a.get("route")
        assigned_oids = a.get("order_ids", [])
        if route is None or not isinstance(route, list):
            return False, "Each assignment must have a 'route' list"
        if len(route) == 0 and len(assigned_oids) > 0:
            return False, "Cannot assign orders to an empty route"
        for n in route:
            if n not in nodes_set:
                return False, f"Unknown node in route: {n}"
        if len(route) > 0:
            for i in range(len(route) - 1):
                u, v = route[i], route[i + 1]
                if v not in graph.get(u, {}):
                    return False, f"Invalid edge: {u} -> {v}"
        for oid in assigned_oids:
            if oid not in order_map:
                return False, f"Unknown order_id in assignment: {oid}"
            coverage[oid] += 1

        if len(route) > 0:
            prefix = [0.0] * len(route)
            for i in range(1, len(route)):
                u, v = route[i - 1], route[i]
                prefix[i] = prefix[i - 1] + graph[u][v]
            node_positions = {}
            for idx, node in enumerate(route):
                node_positions.setdefault(node, []).append(idx)
            for oid in assigned_oids:
                pickup, dropoff = order_map[oid]
                if pickup not in node_positions:
                    return False, f"Pickup {pickup} for order {oid} not in route"
                if dropoff not in node_positions:
                    return False, f"Dropoff {dropoff} for order {oid} not in route"
                pickup_idx = node_positions[pickup][0]
                drop_indices = [idx for idx in node_positions[dropoff] if idx > pickup_idx]
                if not drop_indices:
                    return False, f"Dropoff for order {oid} occurs before pickup"
                drop_idx = drop_indices[0]
                total_penalty_time += prefix[drop_idx]

    missing = [oid for oid, cnt in coverage.items() if cnt == 0]
    multiple = [oid for oid, cnt in coverage.items() if cnt > 1]
    if missing:
        return False, f"Orders not assigned: {missing}"
    if multiple:
        return False, f"Orders assigned multiple times: {multiple}"
    return True, total_penalty_time

graph_files = {
    1: "graph-1-50.json",
    2: "graph-2-500.json",
    3: "graph-3-5000.json"
}

THRESHOLDS = {
    1: 7100,       # Naive: ~8000
    2: 75000,      # Naive: ~90000
    3: 1405000     # Naive: ~2000000
}

def run_test_for_scale(scale):
    graph_file = f"/tests/fixtures/Testcases/{graph_files[scale]}"
    input_file = f"/tests/fixtures/Testcases/phase3/task1/t{scale}.json"
    tmp_file = f"out_{scale}.json"
    if os.path.exists(tmp_file):
        os.remove(tmp_file)

    exe_path = "/app/phase3"
    if not os.path.exists(exe_path):
        print(f"Executable {exe_path} not found. Did you compile it?")
        sys.exit(1)
    cmd = [exe_path, graph_file, input_file, tmp_file]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except FileNotFoundError:
        print(f"Executable {exe_path} not found.")
        sys.exit(1)
    assert proc.returncode == 0, f"Execution failed: {proc.stderr}"

    with open(graph_file, 'r') as f:
        g_json = json.load(f)
    with open(input_file, 'r') as f:
        input_json = json.load(f)
    with open(tmp_file, 'r') as f:
        output_json = json.load(f)

    graph, nodes_set = load_graph_time(g_json, time=True)
    events = input_json.get("events", [])
    results = output_json.get("results", [])

    assert len(events) == len(results), "Mismatch in number of events and results."

    penalties = []
    for ei, (evt, res) in enumerate(zip(events, results), start=1):
        ok, penalty_or_err = verify_assignments(graph, nodes_set, evt, res)
        assert ok, f"Event {ei} failed: {penalty_or_err}"
        penalties.append(penalty_or_err)

    avg_penalty = sum(penalties) / len(penalties)
    max_allowed = THRESHOLDS[scale]

    print(f"Scale {scale} Average Penalty: {avg_penalty} (Max allowed: {max_allowed})")
    assert avg_penalty <= max_allowed, f"Delivery Time Penalty ({avg_penalty}) exceeds threshold ({max_allowed}). Your routing algorithm needs optimization."

def test_phase2_shortest_paths():
    """Verify Phase 2 A* and K-Shortest Path logic using reference output."""
    exe_path = "/app/phase2"
    graph_file = "/tests/fixtures/Testcases/P2/p2graph.json"
    query_file = "/tests/fixtures/Testcases/P2/p2queries.json"
    expected_file = "/tests/fixtures/p2output.json"
    tmp_out = "p2_out_temp.json"

    if os.path.exists(tmp_out):
        os.remove(tmp_out)

    if not os.path.exists(exe_path):
        print(f"Executable {exe_path} not found. Did you compile it?")
        sys.exit(1)

    cmd = [exe_path, graph_file, query_file, tmp_out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        print(f"Executable {exe_path} not found.")
        sys.exit(1)

    assert proc.returncode == 0, f"Phase 2 executable crashed or failed: {proc.stderr}"
    assert os.path.exists(tmp_out), "Phase 2 did not produce output file"

    with open(tmp_out, 'r') as f:
        student_data = json.load(f)
    with open(expected_file, 'r') as f:
        expected_data = json.load(f)

    student_results = {r["id"]: r for r in student_data.get("results", [])}
    expected_results = {r["id"]: r for r in expected_data.get("results", [])}

    for q_id, exp_r in expected_results.items():
        assert q_id in student_results, f"Missing query ID {q_id} in output"
        stu_r = student_results[q_id]

        exp_paths = exp_r.get("paths", [])
        stu_paths = stu_r.get("paths", [])

        if len(exp_paths) == 0:
            assert len(stu_paths) == 0, f"Query {q_id}: Expected 0 paths, got {len(stu_paths)}"
        else:
            assert len(stu_paths) > 0, f"Query {q_id}: Expected >0 paths, got 0"

        if len(exp_paths) > 0 and len(stu_paths) > 0:
            assert len(stu_paths) == len(exp_paths), f"Query {q_id}: Expected {len(exp_paths)} paths, got {len(stu_paths)}"
            for idx in range(len(exp_paths)):
                assert math.isclose(stu_paths[idx]["length"], exp_paths[idx]["length"], rel_tol=1e-3), f"Query {q_id}: Path {idx} length mismatch"

    if os.path.exists(tmp_out):
        os.remove(tmp_out)

if __name__ == "__main__":
    print("Running Phase 2 tests...")
    test_phase2_shortest_paths()
    print("Running Phase 3 Scale 1...")
    run_test_for_scale(1)
    print("Running Phase 3 Scale 2...")
    run_test_for_scale(2)
    print("Running Phase 3 Scale 3...")
    run_test_for_scale(3)
    print("All tests passed.")
    sys.exit(0)
