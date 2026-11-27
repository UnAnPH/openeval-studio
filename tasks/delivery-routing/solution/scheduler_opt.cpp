#include "scheduler.h"
#include <cmath>
#include <limits>
#include <string>
#include <queue>
#include <algorithm>
#include <map>
#include <random>

static double dist2(double x1, double y1, double x2, double y2) {
    double dx = x1 - x2;
    double dy = y1 - y2;
    return dx * dx + dy * dy;
}

double Graph::heuristic(int u, int target) {
    double dx = nodes[u].lat - nodes[target].lat;
    double dy = nodes[u].lon - nodes[target].lon;
    return std::sqrt(dx*dx + dy*dy);  // euclidean
}

std::vector<int> Graph::shortest_path(int src, int target, double &path_length) {
  std::priority_queue<std::pair<double, int>, std::vector<std::pair<double, int>>, std::greater<>> pq;
  std::vector<double> dist(vertices + 1, __DBL_MAX__);
  std::vector<int> par(vertices + 1, -1);

  dist[src] = 0;
  pq.push({heuristic(src,target), src});

  while (!pq.empty()) {
    auto [w, u] = pq.top();
    pq.pop();
    if (w > dist[u] + heuristic(u,target)) continue;
    if (u==target) break;

    for (auto &[v_id, edge_id] : nodes[u].adj) {
      if (edges[edge_id].blocked) continue;
      double newd = dist[u] + edges[edge_id].length;
      if (newd < dist[v_id]) {
        dist[v_id] = newd;
        par[v_id] = u;
        pq.push({newd + heuristic(v_id,target), v_id});
      }
    }
  }

  if (dist[target] == __DBL_MAX__) {
    path_length = -1;
    return {};
  }

  std::vector<int> ans;
  for (int cur = target; cur != -1; cur = par[cur]) ans.push_back(cur);
  std::reverse(ans.begin(), ans.end());
  path_length = dist[target];
  return ans;
}

std::vector<std::vector<int>> assign_orders_kmeans_pick_drop(
    const std::vector<Order> &orders, const std::vector<std::pair<double,double>> &node_coords, int num_drivers, double alpha) {
    int n = static_cast<int>(orders.size());
    int K = std::min(num_drivers, n);
    std::vector<std::vector<int>> driver_orders(num_drivers);
    if (n == 0 || num_drivers <= 0) return driver_orders;
    
    std::mt19937 rng(42);
    double best_assignment_eval = std::numeric_limits<double>::infinity();
    std::vector<int> best_assignment(n, 0);

    for (int restart = 0; restart < 5; restart++) {
        std::vector<std::pair<double, double>> centroids(K);
        for (int k = 0; k < K; k++) {
            int r_idx = rng() % n;
            centroids[k] = {(node_coords[orders[r_idx].pickup].first + node_coords[orders[r_idx].dropoff].first)/2.0,
                            (node_coords[orders[r_idx].pickup].second + node_coords[orders[r_idx].dropoff].second)/2.0};
        }

        std::vector<int> current_assignment(n, 0);
        bool changed = true;
        int max_iters = 100;
        int iter = 0;

        while (changed && iter < max_iters) {
            changed = false;
            iter++;
            std::vector<int> cluster_counts(K, 0);
            std::vector<std::pair<double, double>> new_centroids(K, {0.0, 0.0});

            for (int i = 0; i < n; i++) {
                double mx = (node_coords[orders[i].pickup].first + node_coords[orders[i].dropoff].first) / 2.0;
                double my = (node_coords[orders[i].pickup].second + node_coords[orders[i].dropoff].second) / 2.0;

                int best_cluster = 0;
                double best_dist = std::numeric_limits<double>::infinity();

                for (int k = 0; k < K; k++) {
                    double dist = std::hypot(mx - centroids[k].first, my - centroids[k].second);
                    if (dist < best_dist) {
                        best_dist = dist;
                        best_cluster = k;
                    }
                }

                if (current_assignment[i] != best_cluster) {
                    current_assignment[i] = best_cluster;
                    changed = true;
                }

                new_centroids[best_cluster].first += mx;
                new_centroids[best_cluster].second += my;
                cluster_counts[best_cluster]++;
            }

            for (int k = 0; k < K; k++) {
                if (cluster_counts[k] > 0) {
                    centroids[k].first = new_centroids[k].first / cluster_counts[k];
                    centroids[k].second = new_centroids[k].second / cluster_counts[k];
                }
            }
        }
        
        double current_eval = 0.0;
        for (int i = 0; i < n; i++) {
            int c = current_assignment[i];
            current_eval += std::hypot(centroids[c].first - (node_coords[orders[i].pickup].first + node_coords[orders[i].dropoff].first)/2.0, 
                                     centroids[c].second - (node_coords[orders[i].pickup].second + node_coords[orders[i].dropoff].second)/2.0);
        }
        if (current_eval < best_assignment_eval) {
            best_assignment_eval = current_eval;
            best_assignment = current_assignment;
        }
    }

    for (int i = 0; i < n; i++) {
        driver_orders[best_assignment[i]].push_back(i);
    }
    return driver_orders;
}

// Optimization using GRASP logic
void build_routes(Graph &g, int depot_node, const std::vector<Order> &orders, const std::vector<std::vector<int>> &driver_orders, std::vector<std::vector<int>> &driver_routes, double &total_time) {
    int num_drivers = static_cast<int>(driver_orders.size());
    driver_routes.assign(num_drivers, {});
    total_time = 0.0;

    std::map<std::pair<int, int>, std::pair<double, std::vector<int>>> cache;
    auto get_path = [&](int u, int v) -> std::pair<double, std::vector<int>> {
        if (u == v) return {0.0, {u}};
        auto key = std::make_pair(u, v);
        if (cache.count(key)) return cache[key];
        double len = 0;
        auto p = g.shortest_path(u, v, len);
        cache[key] = {len, p};
        return cache[key];
    };

    std::mt19937 rng(42);

    for (int d = 0; d < num_drivers; d++) {
        const auto &my_orders_idx = driver_orders[d];
        int m = static_cast<int>(my_orders_idx.size());
        if (m == 0) continue;

        double best_driver_time = std::numeric_limits<double>::infinity();
        std::vector<int> best_driver_route;

        int iterations = 10000; // Large number of iterations since we have cache

        for (int iter = 0; iter < iterations; iter++) {
            std::vector<bool> picked(m, false);
            std::vector<bool> delivered(m, false);
            int remaining = m;
            std::vector<int> route;
            int current = depot_node;
            route.push_back(depot_node);

            double time_so_far = 0.0;
            double current_delivery_time = 0.0;

            while (remaining > 0) {
                struct Cand {
                    int node; bool is_pickup; int local_idx; double len; std::vector<int> path;
                };
                std::vector<Cand> candidates;

                for (int j = 0; j < m; j++) {
                    int global_idx = my_orders_idx[j];
                    if (!picked[j]) {
                        auto p = get_path(current, orders[global_idx].pickup);
                        if (!p.second.empty()) candidates.push_back({orders[global_idx].pickup, true, j, p.first, p.second});
                    } else if (!delivered[j]) {
                        auto p = get_path(current, orders[global_idx].dropoff);
                        if (!p.second.empty()) candidates.push_back({orders[global_idx].dropoff, false, j, p.first, p.second});
                    }
                }

                if (candidates.empty()) break;

                std::sort(candidates.begin(), candidates.end(), [time_so_far](const Cand& a, const Cand& b) {
                    return (time_so_far + a.len) < (time_so_far + b.len);
                });

                int top_k = std::min((int)candidates.size(), 3);
                int chosen_idx = 0;
                if (iter > 0) chosen_idx = rng() % top_k; // randomization
                
                auto best_cand = candidates[chosen_idx];

                if (!best_cand.path.empty()) {
                    if (route.empty()) {
                        for (int node : best_cand.path) route.push_back(node);
                    } else {
                        for (size_t i = 1; i < best_cand.path.size(); i++) route.push_back(best_cand.path[i]);
                    }
                    current = best_cand.path.back();
                } else {
                    current = best_cand.node;
                }

                time_so_far += best_cand.len;
                int j = best_cand.local_idx;
                if (best_cand.is_pickup) {
                    picked[j] = true;
                } else {
                    delivered[j] = true;
                    remaining--;
                    current_delivery_time += time_so_far;
                }
            }

            if (current_delivery_time < best_driver_time) {
                best_driver_time = current_delivery_time;
                best_driver_route = route;
            }
        }
        
        driver_routes[d] = best_driver_route;
        total_time += best_driver_time;
    }
}
