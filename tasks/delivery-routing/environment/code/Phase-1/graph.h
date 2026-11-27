#include <vector>
#include <string>
#include <map>
#include <unordered_map>
#include "../nlohmann/json.hpp"
using json = nlohmann::json;

// only declarations here !
struct Edge{
  int id, node1, node2; // edge id, u, v
  bool oneway = false;
  bool blocked = false;
  double length, avg_time;
  std::vector<double> speed;  // is it gonna be double or int guys // !!it must be double
  int roadtype;
};

struct Node{
  int id; // label of node
  double lat, lon; // latitude, longitude
  std::vector<bool> poi; // points of interest
  std::vector<std::pair<int,int>> adj; // adjacent {node id,edge id} pairs

  Node(){
    poi.resize(6);
  }
};

#pragma once
class Graph{
  int vertices; // number of nodes
  std::vector<Node> nodes; // maps label to nodes
  std::unordered_map<int, Edge> edges; // maps edge id to edges
  std::unordered_map<std::string, int> road_types;
  std::unordered_map<std::string, int> node_types;

  public :
  Graph(int V);
  int size();
  double heuristic(int u, int target);
  bool addNode(const Node& node);
  bool addEdge(const Edge& edge);
  bool removeEdge(int edgeid);
  bool deleted(int edgeid);
  bool modifyEdgeLength(int edgeid, double new_length);
  bool modifyEdgeAvg(int edgeid, double avg_time);
  bool modifyEdgeSpeedPf(int edgeid, std::vector<double>);
  bool modifyEdgeType(int edgeid, std::string new_type);
  bool modifyEdgeBlock(int edgeid);
  std::vector<double> shortest_path_dist_vec(
    int src,
    std::vector<int> forbidden_nodes,
    std::vector<std::string> forbidden_road_types);
  std::vector<int> shortest_path_dist(int src, int target, std::vector<int>forbidden_nodes, std::vector<std::string>forbidden_road_types, double & path_length);
  std::vector<int> knn (double lat, double lon, int k, std::string poi, std::vector<int>forbidden_nodes, std::vector<std::string>forbidden_road_types, std::string metric);
  double euclidean_distance(double lat1, double lat2, double lon1, double lon2);
  std::vector<int> shortest_path_time(int src, int target, std::vector<int>forbidden_nodes, std::vector<std::string>forbidden_road_types, double & path_time);
};

// json process_query(json query, Graph &g, int phase = 1);


// Road types can only be: “primary”, “secondary”, “tertiary”, “local”, and “expressway”.
// Node pois can only be: “restaurant”, “petrol station”, “hospital”, “pharmacy”, “hotel”, “atm”.

