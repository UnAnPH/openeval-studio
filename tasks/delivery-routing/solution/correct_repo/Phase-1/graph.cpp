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

  bool Graph::modifyEdgeLength(int edgeid, double new_length){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].length = new_length;
    return true;
  }

  bool Graph::modifyEdgeAvg(int edgeid, double avg_time){
    if (!edges.count(edgeid)) return false;

    edges[edgeid].avg_time = avg_time;
    return true;
  }

  bool Graph::modifyEdgeType(int edgeid, std::string new_type){
    edges[edgeid].roadtype = road_types[new_type];
    return true;
  }

  bool Graph::modifyEdgeSpeedPf(int edgeid, std::vector<double> spf){
    if (!edges.count(edgeid)) return false;
    edges[edgeid].speed = spf;
    return true;
  }

  bool Graph::modifyEdgeBlock(int edgeid){
  if (!edges.count(edgeid)) return false;
  // to reopen roads
  edges[edgeid].blocked = false;
  return true;
  }

json process_query(json query, Graph &g){
json output;

    // safety :)
    if (!query.contains("type") || !query["type"].is_string()) {
        output["error"] = "invalid";
        return output;
    }

    output["id"] = query["id"];
    std::string qtype = query.value("type", "");

    // REMOVE EDGE
    if (qtype == "remove_edge") {
        int id = query.value("edge_id", -1);
        if (id < 0) {
            output["done"] = false;
            output["error"] = "invalid";
    return output;
  }

        output["done"] = g.removeEdge(id);
        return output;
    }

    // MODIFY EDGE
    else if (qtype == "modify_edge") {
    int id = query.value("edge_id", -1);
    if (id < 0) {
        output["done"] = false;
        output["error"] = "invalid";
        return output;
    }

    bool unblocked = false;

    if (g.deleted(id)){
      unblocked = g.modifyEdgeBlock(id);
    }



    // if no patch object : nothing to do (or maybe we unblocked?!)
    if (!query.contains("patch") || !query["patch"].is_object()) {
        output["done"] = unblocked;
        return output;
    }

    // reopen (this also checks if edge exists)
    if (!g.modifyEdgeBlock(id)) {
        output["done"] = false;
        output["error"] = "edge_not_found";
        return output;
    }


    auto &patch = query["patch"];
    double epsilon = 1e-9;
    bool changed = false;

    double length = patch.value("length", -1.0);
    if (std::abs(length + 1.0) > epsilon) {
        g.modifyEdgeLength(id, length);
        changed = true;
    }

    double avg_time = patch.value("average_time", -1.0);
    if (std::abs(avg_time + 1.0) > epsilon) {
        g.modifyEdgeAvg(id, avg_time);
        changed = true;
    }

    if (patch.contains("speed_profile") && patch["speed_profile"].is_array()) {
        auto sp = patch["speed_profile"].get<std::vector<double>>();
        if (!sp.empty()) {
            g.modifyEdgeSpeedPf(id, sp);
            changed = true;
        }
    }

    std::string type = patch.value("road_type", "");
    if (!type.empty()) {
        g.modifyEdgeType(id, type);
        changed = true;
    }

    output["done"] = changed;
    if (!changed) output["info"] = "no_changes_applied";
    return output;
}

    else if (qtype == "shortest_path") {
        int id = query.value("id", -1);
        int source = query.value("source", -1);
        int target = query.value("target", -1);
        std::string mode = query.value("mode", "distance");

        if (source < 0 || target < 0) {
            return json{{"error", "invalid"}};
        }

        // forbidden fruits
    std::vector<int> forbidden_nodes;
    std::vector<std::string> forbidden_road_types;

    if (query.contains("constraints") && query["constraints"].is_object()) {
            auto &c = query["constraints"];

            if (c.contains("forbidden_nodes") && c["forbidden_nodes"].is_array()) {
                forbidden_nodes = c["forbidden_nodes"].get<std::vector<int>>();
            }

            if (c.contains("forbidden_road_types") && c["forbidden_road_types"].is_array()) {
                forbidden_road_types = c["forbidden_road_types"].get<std::vector<std::string>>();
    }
        }

        double pathlen = 0;
    std::vector<int> ans;

        if (mode == "distance")
            ans = g.shortest_path_dist(source, target, forbidden_nodes, forbidden_road_types, pathlen);
        else
            ans = g.shortest_path_time(source, target, forbidden_nodes, forbidden_road_types, pathlen);

        output["id"] = id;

        if (ans.empty()) {
            output["possible"] = false;
      return output;
    }

        output["possible"] = true;
        // if (mode == "distance") output["minimum_distance"] = pathlen;
        // else output["minimum_time"] = pathlen;
        // TODO : UNCOMMENT
        output["minimum_time/minimum_distance"] = pathlen;

        output["path"] = ans;
    return output;
  }

    // KNN
    else if (qtype == "knn") {
    int id = query.value("id", -1);
        std::string placetype = query.value("poi", "");
    int k = query.value("k", 0);
    std::string metric = query.value("metric", "euclidean");

        double lat = 0.0, lon = 0.0;

        if (query.contains("query_point") && query["query_point"].is_object()) {
            auto &qp = query["query_point"];
            lat = qp.value("lat", 0.0);
            lon = qp.value("lon", 0.0);
        }

    std::vector<int> forb_nodes;
        if (query.contains("forbidden_nodes") && query["forbidden_nodes"].is_array()) {
        forb_nodes = query["forbidden_nodes"].get<std::vector<int>>();
        }

        std::vector<std::string> forb_types;
        if (query.contains("forbidden_roadtypes") && query["forbidden_roadtypes"].is_array()) {
        forb_types = query["forbidden_roadtypes"].get<std::vector<std::string>>();
        }

        output["id"] = id;
        output["nodes"] = g.knn(lat, lon, k, placetype, forb_nodes, forb_types, metric);
    return output;
  }

  else {
        output["error"] = "wrong_input";
    return output;
  }
}
