#include <iostream>
#include <fstream>
#include <chrono>
#include "nlohmann/json.hpp"
#include "Phase-1/query.h"
#include "Phase-1/graph.h"

using json = nlohmann::json;
std::unordered_map<std::string, int> road_types;
std::unordered_map<std::string, int> node_types;

int main(int argc, char* argv[]) {
    if (argc != 4) {
        std::cerr << "Usage: " << argv[0] << " <graph.json> <queries.json> <output.json>" << std::endl;
        return 1;
    }

    // road & node types
    road_types = {{"primary",0},{"secondary",1},{"tertiary",2},{"local",3},{"expressway",4}};
    node_types = {{"restaurant",0},{"petrol station",1},{"hospital",2},{"pharmacy",3},{"hotel",4},{"atm",5}};

    // --- READ GRAPH ---
    std::ifstream graph_file(argv[1]);
    if (!graph_file.is_open()) {
        std::cerr << "Failed to open " << argv[1] << std::endl;
        return 1;
    }

    json graph_json;
    graph_file >> graph_json;

    Graph g(graph_json["meta"]["nodes"]); // dummy init
    for (const auto &node_json : graph_json["nodes"]) {
        Node node;
        node.id = node_json.value("id", -1);
        node.lat = node_json.value("lat", 0.0);
        node.lon = node_json.value("lon", 0.0);

        // POIs safely
        if (node_json.contains("pois") && node_json["pois"].is_array()) {
            for (const auto &s : node_json["pois"]) {
                std::string poi_name = s.is_string() ? s.get<std::string>() : "";
                if (!poi_name.empty() && node_types.count(poi_name))
                    node.poi[node_types[poi_name]] = true;
            }
        }
        g.addNode(node);
    }

    for (const auto &edge_json : graph_json["edges"]) {
        Edge e;
        e.id = edge_json.value("id", -1);
        e.node1 = edge_json.value("u", -1);
        e.node2 = edge_json.value("v", -1);
        e.length = edge_json.value("length", 0.0);
        e.avg_time = edge_json.value("average_time", 0.0);
        e.oneway = edge_json.value("oneway", false);

        // road type safely
        std::string rt = edge_json.value("road_type", "");
        e.roadtype = road_types.count(rt) ? road_types[rt] : 0;

        // speed profile safely
        if (edge_json.contains("speed_profile") && edge_json["speed_profile"].is_array()) {
            for (const auto &v : edge_json["speed_profile"]) {
                e.speed.push_back(v.is_number() ? v.get<double>() : 0.0);
            }
        }

        g.addEdge(e);
    }

    // --- READ QUERIES ---
    std::ifstream queries_file(argv[2]);
    if (!queries_file.is_open()) {
        std::cerr << "Failed to open " << argv[2] << std::endl;
        return 1;
    }

    json queries_json;
    queries_file >> queries_json;

    std::ofstream output_file(argv[3]);
    if (!output_file.is_open()) {
        std::cerr << "Failed to open output file" << std::endl;
        return 1;
    }

    json output;
    output["meta"] = queries_json.value("meta", json::object());
    output["results"] = json::array();
    
    for (const auto &query : queries_json.value("events", json::array())) {
        auto start_time = std::chrono::high_resolution_clock::now();
        json ans = process_query(query, g);
        auto end_time = std::chrono::high_resolution_clock::now();
        ans["processing_time"] =
            std::chrono::duration<double, std::milli>(end_time - start_time).count();
        output["results"].push_back(ans);
    }

    output_file << output.dump(4) << "\n";
    output_file.close();
    return 0;
}
