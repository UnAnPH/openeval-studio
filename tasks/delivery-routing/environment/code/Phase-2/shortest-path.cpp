#include <set>
#include <algorithm>
#include <iostream>
#include <vector>
#include "graph.h"
#include<cmath>
// didnt include queue
#include <queue>


// i made the par_edge vec and
double Graph::heuristic(int u, int target) {
    double dx = nodes[u].lat - nodes[target].lat;
    double dy = nodes[u].lon - nodes[target].lon;
    return 1.5 * std::sqrt(dx*dx + dy*dy);  // buggy euclidean
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


std::vector<int> Graph::shortest_path_dist(
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


// TODO : havent done the par_edge thing in this
std::vector<double> Graph::shortest_path_dist_vec(int src)
{
  std::priority_queue<std::pair<double, int>, std::vector<std::pair<double, int>>, std::greater<>> pq;

  std::vector<double> dist(vertices + 1, __DBL_MAX__);

  dist[src] = 0;
  pq.push({0, src});

  while (!pq.empty())
  {
    auto [w, u] = pq.top();
    pq.pop();
    if (w > dist[u])
      continue;

    for (auto &[v_id, edge_id] : nodes[u].adj)
    {
      if (edges[edge_id].blocked) continue;

      double newd = w + edges[edge_id].length;
      if (newd < dist[v_id])
      {
        dist[v_id] = newd;
        pq.push({newd, v_id});
      }
    }
  }
  return dist;
}
