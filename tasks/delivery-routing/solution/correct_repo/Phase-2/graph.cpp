#include <iostream>
#include "graph.h"
#include "../nlohmann/json.hpp"
#include "query.h"
using json = nlohmann::json;
  Graph::Graph(int V){
    vertices = V;
    nodes.resize(V);
    road_types["primary"] = 0;
    road_types["secondary"] = 1;
    road_types["tertiary"] = 2;
    road_types["local"] = 3;
    road_types["expressway"] = 4;

    node_types["restaurant"] = 0;
    node_types["petrol station"] = 1;
    node_types["hospital"] = 2;
    node_types["pharmacy"] = 3;
    node_types["hotel"] = 4;
    node_types["atm"] = 5;
  }
  // * made this dict to save space :) now we need a number not a string and lookup should be O(1) since its small

  // Road types can only be: “primary”, “secondary”, “tertiary”, “local”, and “expressway”.
  // Node pois can only be: “restaurant”, “petrol station”, “hospital”, “pharmacy”, “hotel”, “atm”.

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

json process_query(json query, Graph &g){
    json output;

    // safety :)
    if (!query.contains("type") || !query["type"].is_string()) {
        output["error"] = "invalid";
        return output;
    }
    std::string qtype = query.value("type", "");

    if (qtype == "k_shortest_paths")
    {
        int id = query["id"];
        int source = query["source"];
        int target = query["target"];
        int k = query["k"];

        auto paths = g.k_short_exact(source, target, k);

        output["id"] = id;
        output["paths"] = json::array();

        for (auto &p : paths) {
            double len = g.length_of_path(p);
            json entry;
            entry["path"] = p;
            entry["length"] = len;
            output["paths"].push_back(entry);
        }

        return output;
    }

      else if (qtype == "k_shortest_paths_heuristic")
      {    
          int id = query["id"];
          int source = query["source"];
          int target = query["target"];
          int k = query["k"];
          int overlap_threshold = query["overlap_threshold"];

          auto paths = g.k_short_heuristic(source, target, k,overlap_threshold);

        output["id"] = id;
        output["paths"] = json::array();

        for (auto &p : paths) {
            double len = g.length_of_path(p);
            json entry;
            entry["path"] = p;
            entry["length"] = len;
            output["paths"].push_back(entry);
        }

        return output;
      }

    else if(qtype=="approx_shortest_path"){
      int id=query.value("id",-1);
      if (!query.contains("queries") || !query["queries"].is_array()){
      return json{{"error","invalid_queries_array"}}; //error handling
      }
      long long time_budget=query.value("time_budget_ms",0LL);
      double acceptable_error_pct = query.value("acceptable_error_pct", 5.0);
      double w = 1.0 + acceptable_error_pct / 100.0; // mapping

      json distances = json::array();
      for (auto &qq : query["queries"]) {
          int src = qq.value("source", -1);
          int tgt = qq.value("target", -1);
          if (src < 0 || tgt < 0) {
              distances.push_back({
                  {"source", src},
                  {"target", tgt},
                  {"approx_shortest_distance", nullptr}
              });
              continue;
          }
          std::vector<int> forbidden_nodes; 
          std::vector<std::string> forbidden_road_types;

          double approx_d = g.approx_shortest_distance_alt(src, tgt, w, -1);
          if (approx_d < 0) {
              distances.push_back({
                  {"source", src},
                  {"target", tgt},
                  {"approx_shortest_distance", nullptr}
              });
          } else {
              distances.push_back({
                  {"source", src},
                  {"target", tgt},
                  {"approx_shortest_distance", approx_d}
              });
          }
      }
      output["id"]=id;
      output["distances"]=distances;
      return output;
    }
    else {
        output["error"] = "wrong_input";
    return output;
  }
}
