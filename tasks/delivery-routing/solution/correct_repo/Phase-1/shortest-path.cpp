#include <set>
#include <algorithm>
#include <iostream>
#include <vector>
#include "graph.h"
#include<cmath>
// didnt include queue
#include <queue>

double Graph::heuristic(int u, int target) {
    double dx = nodes[u].lat - nodes[target].lat;
    double dy = nodes[u].lon - nodes[target].lon;
    return std::sqrt(dx*dx + dy*dy);  // euclidean
}

std::vector<int> Graph::shortest_path_dist(
    int src,
    int target,
    std::vector<int> forbidden_nodes,
    std::vector<std::string> forbidden_road_types,
    double &path_length)
{
  std::priority_queue<
      std::pair<double, int>,
      std::vector<std::pair<double, int>>, std::greater<>>
      pq;

  // std::set<int> forb_nodes(forbidden_nodes.begin(), forbidden_nodes.end());
  // std::set<std::string> forb_rtypes(forbidden_road_types.begin(), forbidden_road_types.end());


  bool* forb_nodes = new bool[vertices]{};
  for (int k : forbidden_nodes) forb_nodes[k] = true;

  // std::set<std::string> forb_rtypes(forbidden_road_types.begin(), forbidden_road_types.end());
  bool forb_rtypes[6] = {};
  for (std::string rtype : forbidden_road_types) forb_rtypes[road_types[rtype]] = true;

  if (forb_nodes[src] || forb_nodes[target]){delete []forb_nodes; return {}; }
     // source forbidden = no path

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
      if (forb_nodes[v_id] || forb_rtypes[edges[edge_id].roadtype] || edges[edge_id].blocked)
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
    delete []forb_nodes;
    return {};
  }

  // reconstruct
  std::vector<int> ans;
  for (int cur = target; cur != -1; cur = par[cur])
    ans.push_back(cur);

  std::reverse(ans.begin(), ans.end());
  path_length = dist[target];
  delete []forb_nodes;
  return ans;
}



std::vector<double> Graph::shortest_path_dist_vec(
    int src,
    std::vector<int> forbidden_nodes,
    std::vector<std::string> forbidden_road_types)
{
  std::priority_queue<std::pair<double, int>, std::vector<std::pair<double, int>>, std::greater<>> pq;


  std::vector<bool> forb_nodes(vertices, false);
  for (int k : forbidden_nodes) forb_nodes[k] = true;

  // std::set<std::string> forb_rtypes(forbidden_road_types.begin(), forbidden_road_types.end());
  std::vector<bool> forb_rtypes(6, false);
  for (std::string rtype : forbidden_road_types) forb_rtypes[road_types[rtype]] = true;

  if (forb_nodes[src])
    return {}; // source forbidden = no path

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
      if ( forb_nodes[v_id] || forb_rtypes[edges[edge_id].roadtype] || edges[edge_id].blocked) continue;

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


//! can any entry of speedpf=0 ? have not handled that



double seconds_of_day(double t){
  double flr=std::floor(t);
  double frac=t-flr; // time is flr+frac
  //one day is 86400 seconds
  long long sec=(long long)flr;
  long long crct_sec=((sec % 86400LL) + 86400LL) % 86400LL;
  return double(crct_sec)+frac;
}
double calc_edge_time(double dist, double start_time,std::vector<double>& speedpf){
    if(dist<=0.0)return 0.0;
    double t=start_time;
    double total=0;
    while(dist>1e-9){
      double currtime=seconds_of_day(t);
      int slot=int(currtime/900.0); //each slot is 900 seconds
      int nextslottime=(slot+1)*900.0;
      double left=nextslottime-currtime;
      double v=speedpf[slot];
      double reachable_dist=v*left;
      if(reachable_dist-dist>=1e-9) {//should we tighten this? to say 1e-12
        double timeneeded=dist/v;
        total+=timeneeded;
        break;
      }
      else{
        dist-=reachable_dist;
        total+=left;
        t+=left;
      }
    }
    return total;
}
  std::vector<int> Graph::shortest_path_time(int src, int target, std::vector<int>forbidden_nodes, std::vector<std::string>forbidden_road_types, double& path_time){
  std::priority_queue<std::pair<double, int>,std::vector<std::pair<double, int>>, std::greater<>>pq;
  std::vector<bool> forb_nodes(vertices, false);
  for (int k : forbidden_nodes) forb_nodes[k] = true;
  std::vector<bool> forb_rtypes(6, false);
  for (std::string rtype : forbidden_road_types) forb_rtypes[road_types[rtype]] = true;

  if (forb_nodes[src] || forb_nodes[target]) return {}; // source forbidden = no path
  std::vector<double> time(vertices + 1, __DBL_MAX__); //here dist will be total time elapsed so far
  std::vector<int> par(vertices + 1, -1);
  time[src]=0.0; //we start at time=0 from source in phase 1? (or always)
  pq.push({0.0,src});
  while(!pq.empty()){
    auto [currtime,u]=pq.top();
    pq.pop();
    if(currtime>time[u])continue;
    for(auto &[v_id, edge_id]:nodes[u].adj){
      auto &e=edges[edge_id];
      if(forb_nodes[v_id] || forb_rtypes[e.roadtype]|| e.blocked )continue;
      double travel=calc_edge_time(e.length,currtime,e.speed);
      double arrival=currtime+travel;
      if(arrival<time[v_id]){
        time[v_id]=arrival;
        par[v_id]=u;
        pq.push({arrival,v_id});
      }
    }
  }
    if (time[target] == __DBL_MAX__)
  {
    path_time = -1;
    return {};
  }

  std::vector<int> ans;
  for (int cur = target; cur != -1; cur = par[cur])
    ans.push_back(cur);

  std::reverse(ans.begin(), ans.end());
  path_time = time[target];
  return ans;
  }
