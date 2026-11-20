#include <iostream>
#include "graph.h"
#include "../nlohmann/json.hpp"
using json = nlohmann::json;
  Graph::Graph(int V){
    vertices = V;
    nodes.resize(V);
  }

  // ive just copied this from phase 1

  int Graph::size(){
    return vertices;
  }
  bool Graph::deleted(int id){
    return edges[id].blocked;
  }

  bool Graph::addNode(const Node& node){
    nodes[node.id] = node;
    return true;
  }

  bool Graph::addEdge(const Edge& edge){
    if (edges.count(edge.id)) {
      std::cerr << "Edge with ID:" << edge.id << " already exists!";
      return false;
    }
    if (edge.oneway){
      nodes[edge.node1].adj.push_back({edge.node2, edge.id});
    }
    else {
      nodes[edge.node1].adj.push_back({edge.node2, edge.id});
      nodes[edge.node2].adj.push_back({edge.node1, edge.id});
    }
    edges[edge.id] = edge;
    return true;
  }

  bool Graph::removeEdge(int edgeid){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].blocked = true;
    return true;
  }
