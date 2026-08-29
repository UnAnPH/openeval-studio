#include <iostream>
#include <vector>
#include <string>
#include <map>
#include <./nlohmann/json.hpp>
#include "graph.h"

/* ! THIS FILE DEFINES NODE AND GRAPH */

struct Edge{
  int id; // edge id
  int node1;
  int node2;
  bool oneway = false; // !!!!!!!!!!!!! MUST BE CHANGED AFTER PHASE 1 -> MAKE EDGES DIRECTED
  double length;
  double avg_time;
  std::vector<double> speed; // size of vector = length
  // im not sure if i should keep this a vector or make it an array because we know size at compile time
  // also how to know if json object empty or something idk
  // Fallback to average_time if speed_profile missing.
  // Time-dependent profiles use 96 × 15-minute slots (the average speeds throughout the day in 15 min intervals).
  std::string roadtype; // ? or should we keep it int and have a dictionary of our own but that means roadtypes must be known beforehand
};

struct Node{
  int id; // label of node
  double lat; // latitude
  double lon; // longitude
  std::vector<std::string> poi; // points of interest
  std::vector<std::pair<int,int>> adj; // adjacent {node id,edge id} pairs
};
// map of poi of string to int?? this will tell us all nodes close to a restaurant, say

class Graph{
  int vertices; // number of nodes
  std::unordered_map<int, Node> nodes; // maps label to nodes
  std::unordered_map<int, Edge> edges; // maps edge id to edges
  // im not sure whether to keep the actual object or a pointer - whats better???

  public :

  Graph(int V){
    vertices = V;
  }

  bool addNode(const Node& node){
    if (nodes.count(node.id)) {
      std::cerr << "Node with ID:" << node.id << " already exists!";
      return false;
    }
    nodes[node.id] = node;
    return true;
  }

  bool addEdge(const Edge& edge){
    if (edges.count(edge.id)) {
      std::cerr << "Edge with ID:" << edge.id << " already exists!";
      return false;
    }
    edges[edge.id] = edge;
    return true;
  }

  bool removeEdge(int edgeid){
    if (!edges.count(edgeid)) return false;

    edges.erase(edgeid);
    return true;
  }

  bool modifyEdgeLength(int edgeid, double new_length){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].length = new_length;
    return true;
  }

  bool modifyEdgeAvg(int edgeid, double avg_time){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].avg_time = avg_time;
    return true;
  }

  bool modifyEdgeType(int edgeid, std::string new_type){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].roadtype = new_type;
    return true;
  }
};

