#include "graph.h"
#include <random>
#include <algorithm>
#include <queue>
#include <vector>
#include <iostream>

//this will run dijsktra for landmarks
void run_dijkstra_for_landmark(int start_node, int V, const std::vector<Node>& nodes,const std::unordered_map<int, Edge>& edges,std::vector<double>& dist,bool reverse_graph) {
    std::vector<std::vector<std::pair<int, double>>> rev_adj; 
    if (reverse_graph) {
        rev_adj.resize(V);
        for (const auto& itr : edges) {
            const Edge& e = itr.second;
            if (e.blocked) continue;
            //storing reversed adjacency list
            rev_adj[e.node2].push_back({e.node1, e.length});
            if (!e.oneway) {
                rev_adj[e.node1].push_back({e.node2, e.length});
            }
        }
    }
    std::priority_queue<std::pair<double, int>, std::vector<std::pair<double, int>>, std::greater<>> pq;
    dist.assign(V, __DBL_MAX__);
    dist[start_node] = 0;
    pq.push({0, start_node});

    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();
        if (d > dist[u]) continue;
        if (reverse_graph) {
            for (auto& eid : rev_adj[u]) {
                int v = eid.first;
                double weight = eid.second;
                if (dist[u] + weight < dist[v]) {
                    dist[v] = dist[u] + weight;
                    pq.push({dist[v], v});
                }
            }
        } else {
            for (auto& eid : nodes[u].adj) {
                int v = eid.first;
                int edge_id = eid.second;
                //if there are any blocked edges we skip them as ususl
                auto e = edges.find(edge_id);
                if (e == edges.end() || e->second.blocked) continue;
                double weight = e->second.length;
                if (dist[u] + weight < dist[v]) {
                    dist[v] = dist[u] + weight;
                    pq.push({dist[v], v});
                }
            }
        }
    }
}

void Graph::precompute_landmarks(int L) {
    if (vertices == 0) return;
    landmarks.clear();
    dist_from_landmark.assign(L, std::vector<double>(vertices));
    dist_to_landmark.assign(L, std::vector<double>(vertices));

    std::vector<double> mintolandm(vertices, __DBL_MAX__);
    std::mt19937 rng(42); 
    std::uniform_int_distribution<int> dist(0, vertices - 1);
    //randomly picked first landmark, then implement avoid as the 
    int first = dist(rng);
    landmarks.push_back(first);

    //iteratively pick using avoid strategy
    for (int i = 0; i < L; ++i) {
        int current_landmark = landmarks[i];
        //fwd run
        run_dijkstra_for_landmark(current_landmark, vertices, nodes, edges, dist_from_landmark[i], false);
        //backward run
        run_dijkstra_for_landmark(current_landmark, vertices, nodes, edges, dist_to_landmark[i], true);
        if (i == L - 1) break; 
        //pick farthest
        int best_candidate = -1;
        double max_dist = -1.0;
        for (int v = 0; v < vertices; ++v) {
            double d = dist_from_landmark[i][v];
            if (d < mintolandm[v]) {
                mintolandm[v] = d;
            }
            // we want to pick the node where this minimum distance is maximized
            if (mintolandm[v] != __DBL_MAX__ && mintolandm[v] > max_dist) {
                max_dist = mintolandm[v];
                best_candidate = v;
            }
        }
        if (best_candidate == -1) {
            do { best_candidate = dist(rng); } 
            while(std::find(landmarks.begin(), landmarks.end(), best_candidate) != landmarks.end());
        }
        landmarks.push_back(best_candidate);
    }
}

double Graph::approx_shortest_distance_alt(int src, int target, double w, int use_landmarks) {
    if (src == target) return 0.0;
    std::priority_queue<std::pair<double, int>,std::vector<std::pair<double, int>>,std::greater<>> pq;
    std::vector<double> g_score(vertices, __DBL_MAX__);
    std::vector<bool> visited(vertices, false); 
    g_score[src] = 0;
    double start_h = heuristic(src, target); 
    pq.push({start_h * w, src}); 
    int l_cnt = landmarks.size();
    if (use_landmarks==0||dist_from_landmark.empty()) l_cnt = 0; 
    while (!pq.empty()) {
        auto [f, u] = pq.top();
        pq.pop();
        //skip
        if (visited[u]) continue;
        visited[u] = true;
        if (u == target) return g_score[target];
        for (auto& eid : nodes[u].adj) {
            int v = eid.first;
            int edge_id = eid.second;

            auto e = edges.find(edge_id);
            if (e == edges.end() || e->second.blocked) continue;
            double weight = e->second.length;
            double new_g = g_score[u] + weight;
            if (new_g < g_score[v]) {
                g_score[v] = new_g;

                //ALT heurestic
                double h_val = heuristic(v, target); 
                if (l_cnt > 0) {
                    for(int i = 0; i < l_cnt; ++i) {
                        // dist(v, t) >= dist(v, L) - dist(t, L)
                        double dvl = dist_to_landmark[i][v];
                        double dtl = dist_to_landmark[i][target];
                        if (dvl != __DBL_MAX__ && dtl != __DBL_MAX__) 
                            h_val = std::max(h_val, dvl - dtl);

                        // dist(v, t) >= dist(L, t) - dist(L, v)
                        double dlt = dist_from_landmark[i][target];
                        double dlv = dist_from_landmark[i][v];
                        if (dlt != __DBL_MAX__ && dlv != __DBL_MAX__) 
                            h_val = std::max(h_val, dlt - dlv);
                    }
                }
                //calculated huerestic is pushed into the heap
                double new_f = new_g + (h_val * w);
                pq.push({new_f, v});
            }
        }
    }
    return -1.0; 
}