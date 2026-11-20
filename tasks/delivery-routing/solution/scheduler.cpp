#include "scheduler.h"
#include <cmath>
#include <limits>
#include <string>
#include<queue>

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

std::vector<int> Graph::shortest_path(
    int src,
    int target,
    double &path_length)
{
  std::priority_queue<
      std::pair<double, int>,
      std::vector<std::pair<double, int>>, std::greater<>>
      pq;

  std::vector<double> dist(vertices + 1, __DBL_MAX__);
  std::vector<int> par(vertices + 1, -1);

  dist[src] = 0;
  pq.push({heuristic(src,target), src});

  while (!pq.empty())
  {
    auto [w, u] = pq.top();
    pq.pop();
    if (w > dist[u] + heuristic(u,target))
      continue;

    if (u==target) break;

    for (auto &[v_id, edge_id] : nodes[u].adj)
    {
      if (edges[edge_id].blocked)
        continue;


      double newd = dist[u] + edges[edge_id].length;
      if (newd < dist[v_id])
      {
        dist[v_id] = newd;
        par[v_id] = u;
        pq.push({newd + heuristic(v_id,target), v_id});
      }
    }
  }

  if (dist[target] == __DBL_MAX__)
  {
    path_length = -1;
    return {};
  }

  // reconstruct
  std::vector<int> ans;
  for (int cur = target; cur != -1; cur = par[cur])
    ans.push_back(cur);

  std::reverse(ans.begin(), ans.end());
  path_length = dist[target];
  return ans;
}

std::vector<std::vector<int>> assign_orders_kmeans_pick_drop(
    const std::vector<Order> &orders,
    const std::vector<std::pair<double,double>> &node_coords,
    int num_drivers,
    double alpha
) {
    int n = static_cast<int>(orders.size());
    std::vector<std::vector<int>> driver_orders(num_drivers);

    if (n == 0 || num_drivers <= 0) return driver_orders;

    int k = std::min(num_drivers, n);

    std::vector<std::pair<double,double>> p(n), d(n);
    for (int i = 0; i < n; i++) {
        int pu = orders[i].pickup;
        int dr = orders[i].dropoff;
        p[i] = node_coords[pu];
        d[i] = node_coords[dr];
    }

    std::vector<std::pair<double,double>> Pc(k), Dc(k);

    for (int c = 0; c < k; c++) {
        Pc[c] = p[c];
        Dc[c] = d[c];
    }

    std::vector<std::vector<int>> clusters(k);
    const int mx_itr = 10;

    for (int it = 0; it < mx_itr; it++) {
        for (int c = 0; c < k; c++) clusters[c].clear();

        for (int i = 0; i < n; i++) {
            double best_score = std::numeric_limits<double>::infinity();
            int best_c = 0;
            for (int c = 0; c < k; c++) {
                double dp = dist2(p[i].first, p[i].second, Pc[c].first, Pc[c].second);
                double dd = dist2(d[i].first, d[i].second, Dc[c].first, Dc[c].second);
                double score = alpha * dp + (1.0 - alpha) * dd;
                if (score < best_score) {
                    best_score = score;
                    best_c = c;
                }
            }
            clusters[best_c].push_back(i);  // i is index into order
        }

        for (int c = 0; c < k; c++) {
            if (clusters[c].empty()) continue;
            double spx = 0.0, spy = 0.0;
            double sdx = 0.0, sdy = 0.0;
            for (int idx : clusters[c]) {
                spx += p[idx].first;
                spy += p[idx].second;
                sdx += d[idx].first;
                sdy += d[idx].second;
            }
            double inv = 1.0 / clusters[c].size();
            Pc[c] = { spx * inv, spy * inv };
            Dc[c] = { sdx * inv, sdy * inv };
        }
    }

    for (int dri = 0; dri < num_drivers; dri++) {
        if (dri < k) driver_orders[dri] = clusters[dri];
    }

    return driver_orders;
}

void build_routes(
    Graph &g,
    int depot_node,
    const std::vector<Order> &orders,
    const std::vector<std::vector<int>> &driver_orders,
    std::vector<std::vector<int>> &driver_routes,
    double &total_time
) {
    int num_drivers = static_cast<int>(driver_orders.size()); // recommended by chatgpt
    driver_routes.assign(num_drivers, {});
    total_time = 0.0;

    for (int d = 0; d < num_drivers; d++) {
        const auto &my_orders_idx = driver_orders[d];
        int m = static_cast<int>(my_orders_idx.size());
        if (m == 0) {
            continue;
        }

        std::vector<bool> picked(m, false);
        std::vector<bool> delivered(m, false);
        int remaining = m;

        std::vector<int> &route = driver_routes[d];
        route.clear();
        int current = depot_node;
        route.push_back(depot_node);

        double time_so_far = 0.0;

        while (remaining > 0) {
            struct Cand {
                int node;
                bool is_pickup;
                int local_idx;
            };
            std::vector<Cand> candidates;

            for (int j = 0; j < m; j++) {
                int global_idx = my_orders_idx[j];
                const Order &ord = orders[global_idx];
                if (!picked[j]) {
                    // pickup candidate
                    candidates.push_back({ ord.pickup, true, j });
                } else if (!delivered[j]) {
                    // dropoff candidate
                    candidates.push_back({ ord.dropoff, false, j });
                }
            }

            if (candidates.empty()) break; // safety

            double best_seg = std::numeric_limits<double>::infinity();
            std::vector<int> best_path;
            Cand best_cand{};
            bool found = false;

            for (const auto &cand : candidates) {
                if (cand.node == current) {
                    best_seg = 0.0;
                    best_path.clear();
                    best_cand = cand;
                    found = true;
                    break;
                }

                double seg_len = 0.0;
                std::vector<int> path = g.shortest_path(current, cand.node, seg_len);

                if (path.empty()) continue; // unreachable

                if (seg_len < best_seg) {
                    best_seg = seg_len;
                    best_path = std::move(path);
                    best_cand = cand;
                    found = true;
                }
            }

            if (!found) {
                break;
            }

            if (!best_path.empty()) {
                if (route.empty()) {
                    for (int node : best_path) route.push_back(node);
                } else {
                    for (size_t i = 1; i < best_path.size(); i++)
                        route.push_back(best_path[i]);
                }
                current = best_path.back();
            } else {
                current = best_cand.node;
            }

            time_so_far += best_seg;

            int j = best_cand.local_idx;
            int global_idx = my_orders_idx[j];
            const Order &ord = orders[global_idx];

            if (best_cand.is_pickup) {
                picked[j] = true;
            } else {
                delivered[j] = true;
                remaining--;
                total_time += time_so_far;
            }
        }
    }
}
