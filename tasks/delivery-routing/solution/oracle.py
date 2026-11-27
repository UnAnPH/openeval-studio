import json
import sys
import random
import math

def load_graph(g_json):
    graph = {}
    nodes = set()
    for node in g_json.get("nodes", []):
        nodes.add(node["id"])
        graph[node["id"]] = {}
    for e in g_json.get("edges", []):
        u, v = e["u"], e["v"]
        w = e.get("average_time", e.get("length", 1.0))
        if v not in graph[u] or w < graph[u][v]:
            graph[u][v] = float(w)
        if not e.get("oneway", False):
            if u not in graph[v] or w < graph[v][u]:
                graph[v][u] = float(w)
    return graph, nodes

def dijkstra(graph, nodes, start):
    import heapq
    dist = {u: float('inf') for u in nodes}
    nxt = {u: -1 for u in nodes}
    dist[start] = 0
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]: continue
        for v, w in graph[u].items():
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                nxt[v] = u
                heapq.heappush(pq, (dist[v], v))
    return dist, nxt

def solve(graph_file, input_file, output_file):
    with open(graph_file) as f:
        g_json = json.load(f)
    with open(input_file) as f:
        t_json = json.load(f)
        
    graph, nodes = load_graph(g_json)
    events = t_json.get('events', [t_json])
    final_output = {"meta": t_json.get("meta", {})}
    results = []
    
    for ev in events:
        num_drivers = ev['fleet']['num_delivery_guys']
        if ev['fleet'].get('num_delievery_guys'):
            num_drivers = ev['fleet']['num_delievery_guys']
        depot = ev['fleet']['depot_node']
        orders = ev['orders']
        
        m = len(orders)
        if m == 0:
            results.append({"assignments": [{"driver_id": i, "route": [depot], "order_ids": []} for i in range(num_drivers)], "metrics": {"total_delivery_time_s": 0}})
            continue

        poi = set([depot])
        for o in orders:
            poi.add(o['pickup'])
            poi.add(o['dropoff'])
            
        dist_cache = {}
        prev_cache = {}
        for p in poi:
            dist, prev = dijkstra(graph, nodes, p)
            dist_cache[p] = dist
            prev_cache[p] = prev
            
        def get_path(u, v):
            if u == v: return []
            path = []
            curr = v
            prev = prev_cache[u]
            while curr != u and curr != -1:
                path.append(curr)
                curr = prev[curr]
            path.reverse()
            return path

        def eval_driver(driver_orders):
            if not driver_orders:
                return 0.0, [depot]
            loc = depot
            time_elapsed = 0.0
            route = [depot]
            picked_up = set()
            delivered = set()
            carrying = set()
            total_penalty = 0.0
            
            while len(delivered) < len(driver_orders):
                best_action = None
                best_cost = float('inf')
                for i in driver_orders:
                    if i not in picked_up:
                        arr_time = time_elapsed + dist_cache[loc][orders[i]['pickup']]
                        if arr_time < best_cost:
                            best_cost = arr_time
                            best_action = (True, i)
                    if i in carrying:
                        arr_time = time_elapsed + dist_cache[loc][orders[i]['dropoff']]
                        if arr_time < best_cost:
                            best_cost = arr_time
                            best_action = (False, i)
                if not best_action: break
                is_pickup, idx = best_action
                target_node = orders[idx]['pickup'] if is_pickup else orders[idx]['dropoff']
                time_spent = dist_cache[loc][target_node]
                path = get_path(loc, target_node)
                loc = target_node
                time_elapsed += time_spent
                route.extend(path)
                if is_pickup:
                    picked_up.add(idx)
                    carrying.add(idx)
                else:
                    delivered.add(idx)
                    carrying.remove(idx)
                    total_penalty += time_elapsed
            return total_penalty, route

        def eval_state(assignment):
            tot = 0.0
            routes = []
            for d in range(num_drivers):
                orders_for_d = [i for i, driver in enumerate(assignment) if driver == d]
                pen, r = eval_driver(orders_for_d)
                tot += pen
                routes.append(r)
            return tot, routes

        # Initialize with simple spatial assignment (closest depot-pickup)
        # Actually random starts to avoid bias
        best_overall_penalty = float('inf')
        best_assignment = []
        best_routes = []
        
        # 10 restarts
        for restart in range(10):
            curr_assignment = [random.randint(0, num_drivers - 1) for _ in range(m)]
            curr_pen, curr_routes = eval_state(curr_assignment)
            
            # SA params
            T = 10000.0
            cooling = 0.95
            
            for step in range(500): # 500 steps per restart
                i = random.randint(0, m - 1)
                old_d = curr_assignment[i]
                new_d = random.randint(0, num_drivers - 1)
                if old_d == new_d: continue
                
                new_assignment = list(curr_assignment)
                new_assignment[i] = new_d
                
                new_pen, new_routes = eval_state(new_assignment)
                
                if new_pen < curr_pen or random.random() < math.exp((curr_pen - new_pen) / T):
                    curr_assignment = new_assignment
                    curr_pen = new_pen
                    curr_routes = new_routes
                    
                T *= cooling
                
            if curr_pen < best_overall_penalty:
                best_overall_penalty = curr_pen
                best_assignment = list(curr_assignment)
                best_routes = list(curr_routes)
                
        assignments = []
        for d in range(num_drivers):
            orders_for_d = [orders[i]['order_id'] for i, driver in enumerate(best_assignment) if driver == d]
            assignments.append({
                "driver_id": d,
                "route": best_routes[d],
                "order_ids": orders_for_d
            })
                
        results.append({"assignments": assignments, "metrics": {"total_delivery_time_s": 0}, "processing_time": 100})
        
    final_output["results"] = results
    
    with open(output_file, 'w') as f:
        json.dump(final_output, f, indent=4)

if __name__ == '__main__':
    if len(sys.argv) >= 4:
        solve(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("Usage: python oracle.py <graph> <input> <output>")
