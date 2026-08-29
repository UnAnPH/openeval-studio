#include "./nlohmann/json.hpp"
#include <iostream>
#include <fstream>
#include <chrono>
#include "Phase-1/graph.h"
#include "Phase-1/query.h"
/*
    Add other includes that you require, only write code wherever indicated
*/

using json = nlohmann::json;

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cerr << "Usage: " << argv[0] << " <graph.json> <queries.json>" << std::endl;
        return 1;
    }

    // Read graph from first file
    /*
        Add your graph reading and processing code here
        Initialize any classes and data structures needed for query processing
    */

    std::ifstream graph_file(argv[1]); // read graph.json :)
    if (!graph_file.is_open()) {
        std::cerr << "Failed to open " << argv[1] << std::endl;
        return 1;
    }
    json graph_json;
    graph_file >> graph_json;
    std::string test_id = graph_json["meta"]["id"];
    std::string test_descr = graph_json["meta"]["description"];
    Graph g(graph_json["meta"]["nodes"]);
    // g is going to be our graph

    for (json node_json : graph_json["nodes"]){
      Node node;
      node.id = node_json["id"];
      node.lat = node_json["lat"];
      node.lon = node_json["lon"];
      node.poi = node_json["pois"];
      g.addNode(node);
    }

    for (json edge_json : graph_json["edges"]){
      Edge e;
      e.id = edge_json["id"];
      e.node1 = edge_json["u"];
      e.node2 = edge_json["v"];
      e.avg_time = edge_json["average_time"];
      e.speed = edge_json["speed_profile"].get<std::vector<double>>();
      e.oneway = edge_json["oneway"];
      e.roadtype = edge_json["road_type"];
      g.addEdge(e);
    }


    // Read queries from second file
    std::ifstream queries_file(argv[2]);
    if (!queries_file.is_open()) {
        std::cerr << "Failed to open " << argv[2] << std::endl;
        return 1;
    }
    json queries_json;
    queries_file >> queries_json;

    std::ofstream output_file("output.json");
    if (!output_file.is_open()) {
        std::cerr << "Failed to open output.json for writing" << std::endl;
        return 1;
    }

    for (const auto& query : queries_json) {
        auto start_time = std::chrono::high_resolution_clock::now();

        /*
            Add your query processing code here
            Each query should return a json object which should be printed to sample.json
        */
        // Answer each query replacing the function process_query using
        // whatever function or class methods that you have implemented
        json result = process_query(query,g);

        auto end_time = std::chrono::high_resolution_clock::now();
        result["processing_time"] = std::chrono::duration<double, std::milli>(end_time - start_time).count();

        output_file << result.dump(4) << '\n';
    }

    output_file.close();
    return 0;
}
