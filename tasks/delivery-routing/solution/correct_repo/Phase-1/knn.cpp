#include <set>
#include <algorithm>
#include <iostream>
#include <vector>
#include "graph.h"
#include <queue>

double Graph::euclidean_distance(double lat1, double lat2, double lon1, double lon2){
    return sqrt((lat1-lat2)*(lat1-lat2) +(lon1-lon2)*(lon1-lon2));
    // fixed : put * bw )(
}

std::vector<int> Graph::knn(
    double lat, double lon, int k,
    std::string poi,
    std::vector<int> forbidden_nodes,
    std::vector<std::string> forbidden_road_types,
    std::string metric)
{
    // nearest graph node
    int src_id = -1;
    double bestd = 1e300;

    for (auto &p : nodes) {
        double dx = lat - p.lat;
        double dy = lon - p.lon;
        double sq = dx*dx + dy*dy;
        if (sq < bestd) {
            bestd = sq;
            src_id = p.id;
        }
    }
    if (src_id == -1) return {};

    std::vector<int> poi_candidates;
    int poi_idx = node_types[poi];

    // O(num of nodes) is ok right?!

    poi_candidates.reserve(nodes.size());
    for (auto &n : nodes) {
        if (n.poi[poi_idx])
            poi_candidates.push_back(n.id);
    }
    if (poi_candidates.empty()) return {};
    k = std::min(k, (int)poi_candidates.size());

    std::priority_queue<std::pair<double, int>> pq;

    // euclidean
    if (metric[0] == 'e') {
        for (int id : poi_candidates) {
            const Node &n = nodes[id];
            double dx = lat - n.lat;
            double dy = lon - n.lon;
            double dist_sq = dx*dx + dy*dy;

            if ((int)pq.size() < k) pq.push({dist_sq, id});
            else if (dist_sq < pq.top().first) {
                pq.pop();
                pq.push({dist_sq, id});
        }
    }
    }

    else if (metric[0] == 's') {
        std::vector<double> dist = shortest_path_dist_vec(src_id, forbidden_nodes, forbidden_road_types);

        for (int id : poi_candidates) {
            double d = dist[id];
            if (d >= __DBL_MAX__ - 1) continue; // unreachable

            if ((int)pq.size() < k) pq.push({d, id});
            else if (d < pq.top().first) {
                pq.pop();
                pq.push({d, id});
            }
        }
    }

    if (pq.empty()) return {};

    std::vector<int> ans;
    ans.reserve(k);

    while (!pq.empty()) {
        ans.push_back(pq.top().second);
        pq.pop();
    }

    std::reverse(ans.begin(), ans.end());
    return ans;
}
