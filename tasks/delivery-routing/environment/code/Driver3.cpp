// phase3_driver.cpp  -- nlohmann-safe + supports "events" array query format
#include <iostream>
#include <fstream>
#include <vector>
#include <utility>
#include <exception>
#include <algorithm>

#include "nlohmann/json.hpp"
#include "Phase-3/graph.h"
#include "Phase-3/scheduler.h"

using json = nlohmann::json;

static int extract_num_drivers(const json &fleet) {
    if (!fleet.is_object()) return -1;
    if (fleet.contains("num_delivery_guys")) return fleet.value("num_delivery_guys", -1);
    if (fleet.contains("num_delievery_guys")) return fleet.value("num_delievery_guys", -1);
    if (fleet.contains("num_delivery_guys")) return fleet.value("num_delivery_guys", -1);
    if (fleet.contains("num_drivers")) return fleet.value("num_drivers", -1);
    if (fleet.contains("num_delivery_guys")) return fleet.value("num_delivery_guys", -1);
    if (fleet.contains("num_deliverers")) return fleet.value("num_deliverers", -1);
    // fallback: any integer-looking key
    for (auto it = fleet.begin(); it != fleet.end(); ++it) {
        if (it.value().is_number_integer()) {
            // slightly risky but sometimes useful; don't pick depot_node
            if (it.key() != "depot_node") return it.value().get<int>();
        }
    }
    return -1;
}

static int extract_depot_node(const json &fleet) {
    if (!fleet.is_object()) return -1;
    if (fleet.contains("depot_node")) return fleet.value("depot_node", -1);
    if (fleet.contains("depot")) return fleet.value("depot", -1);
    return -1;
}

int main(int argc, char* argv[]) {
    if (argc != 3 && argc != 4) {
        std::cerr << "Usage: " << argv[0]
                  << " <graph.json> <phase3_query.json> [<output.json>]" << std::endl;
        return 1;
    }

    const char* graph_path  = argv[1];
    const char* query_path  = argv[2];
    const char* output_path = (argc == 4 ? argv[3] : "output_phase3.json");

    json graph_json;
    json query_json;

    // --- read graph safely ---
    try {
        std::ifstream gf(graph_path);
        if (!gf.is_open()) {
            std::cerr << "Failed to open graph file: " << graph_path << std::endl;
            return 1;
        }
        gf >> graph_json;
    } catch (const json::parse_error &e) {
        std::cerr << "Failed to parse graph JSON: " << e.what() << std::endl;
        return 1;
    } catch (const std::exception &e) {
        std::cerr << "Error reading graph file: " << e.what() << std::endl;
        return 1;
    }

    // --- determine num_nodes robustly ---
    int num_nodes = 0;
    try {
        if (graph_json.contains("meta") && graph_json["meta"].is_object()) {
            num_nodes = graph_json["meta"].value("nodes", 0);
        }
        if (num_nodes <= 0 && graph_json.contains("nodes") && graph_json["nodes"].is_array()) {
            num_nodes = static_cast<int>(graph_json["nodes"].size());
            std::cerr << "Warning: graph.meta.nodes missing — using nodes array size (" << num_nodes << ").\n";
        }
        if (num_nodes <= 0) {
            std::cerr << "Unable to determine num_nodes from graph.json (meta.nodes or nodes array required).\n";
            return 1;
        }
    } catch (const std::exception &e) {
        std::cerr << "Error inspecting graph meta: " << e.what() << std::endl;
        return 1;
    }

    Graph g(num_nodes);
    std::vector<std::pair<double,double>> node_coords(static_cast<size_t>(num_nodes), {0.0, 0.0});

    // --- load nodes ---
    if (!graph_json.contains("nodes") || !graph_json["nodes"].is_array()) {
        std::cerr << "graph JSON missing 'nodes' array or it is not an array.\n";
        return 1;
    }
    for (const auto &n : graph_json["nodes"]) {
        int nid = n.value("id", -1);
        double lat = n.value("lat", 0.0);
        double lon = n.value("lon", 0.0);
        if (nid < 0) {
            std::cerr << "Skipping node with invalid id.\n";
            continue;
        }
        Node node;
        node.id = nid;
        node.lat = lat;
        node.lon = lon;
        g.addNode(node);
        if (nid >= 0 && nid < num_nodes) node_coords[static_cast<size_t>(nid)] = {lat, lon};
        else std::cerr << "Warning: node id " << nid << " outside [0," << (num_nodes-1) << "].\n";
    }

    // --- load edges ---
    if (!graph_json.contains("edges") || !graph_json["edges"].is_array()) {
        std::cerr << "graph JSON missing 'edges' array or it is not an array.\n";
        return 1;
    }
    for (const auto &ejson : graph_json["edges"]) {
        Edge e;
        e.id = ejson.value("id", -1);
        e.node1 = ejson.value("u", -1);
        e.node2 = ejson.value("v", -1);
        if (ejson.contains("average_time")) e.length = ejson.value("average_time", 1.0);
        else if (ejson.contains("length")) e.length = ejson.value("length", 1.0);
        else e.length = 1.0;
        e.oneway = ejson.value("oneway", false);
        g.addEdge(e);
    }

    // --- read query JSON safely ---
    try {
        std::ifstream qf(query_path);
        if (!qf.is_open()) {
            std::cerr << "Failed to open query file: " << query_path << std::endl;
            return 1;
        }
        qf >> query_json;
    } catch (const json::parse_error &e) {
        std::cerr << "Failed to parse query JSON: " << e.what() << std::endl;
        return 1;
    } catch (const std::exception &e) {
        std::cerr << "Error reading query file: " << e.what() << std::endl;
        return 1;
    }

    json final_output;
    final_output["meta"] = query_json.value("meta", json::object());

    // Helper lambda to process one event-like object and return its result JSON
    auto process_one = [&](const json &event_obj) -> json {
        json result;
        // event id (if present)
        // if (event_obj.contains("id")) result["event_id"] = event_obj.value("id", "");
        // else if (event_obj.contains("event_id")) result["event_id"] = event_obj.value("event_id", "");
        // else result["event_id"] = "";

        // extract fleet and orders
        if (!event_obj.contains("fleet") || !event_obj["fleet"].is_object()) {
            result["error"] = "Missing or invalid fleet object";
            return result;
        }
        const json &fleet = event_obj["fleet"];
        int num_drivers = extract_num_drivers(fleet);
        int depot_node  = extract_depot_node(fleet);

        if (num_drivers <= 0) {
            result["error"] = "Invalid number of drivers in fleet";
            return result;
        }
        if (depot_node < 0) {
            result["error"] = "Invalid depot_node in fleet";
            return result;
        }

        if (!event_obj.contains("orders") || !event_obj["orders"].is_array()) {
            result["error"] = "Missing or invalid orders array";
            return result;
        }

        // parse orders
        std::vector<Order> orders;
        for (const auto &o : event_obj["orders"]) {
            Order ord;
            ord.id = o.value("order_id", -1);
            ord.pickup = o.value("pickup", -1);
            ord.dropoff = o.value("dropoff", -1);
            if (ord.id < 0) {
                std::cerr << "Skipping order with invalid order_id.\n";
                continue;
            }
            orders.push_back(ord);
        }

        // assign & route
        std::vector<std::vector<int>> driver_order_indices;
        try {
            driver_order_indices = assign_orders_kmeans_pick_drop(orders, node_coords, num_drivers, 0.6);
        } catch (const std::exception &e) {
            result["error"] = std::string("assign_orders_kmeans_pick_drop failed: ") + e.what();
            return result;
        }

        std::vector<std::vector<int>> driver_routes;
        double total_delivery_time_s = 0.0;
        try {
            build_routes(
                g, depot_node, orders,
                driver_order_indices, driver_routes, total_delivery_time_s
            );
        } catch (const std::exception &e) {
            result["error"] = std::string("build_routes") + e.what();
            return result;
        }

        // produce assignments array
        json assignments = json::array();
        size_t max_drivers = std::max(driver_order_indices.size(), driver_routes.size());
        for (size_t d = 0; d < max_drivers; ++d) {
            json a;
            a["driver_id"] = static_cast<int>(d);
            if (d < driver_routes.size()) a["route"] = driver_routes[d];
            else a["route"] = json::array();
            json oids = json::array();
            if (d < driver_order_indices.size()) {
                for (int idx : driver_order_indices[d]) {
                    if (idx >= 0 && idx < static_cast<int>(orders.size())) oids.push_back(orders[idx].id);
                    else std::cerr << "Warning: order index out of range: " << idx << "\n";
                }
            }
            a["order_ids"] = oids;
            assignments.push_back(a);
        }

        result["assignments"] = assignments;
        result["metrics"]["total_delivery_time_s"] = total_delivery_time_s;
        return result;
    };

    // If input contains "events" array, process each
    // if (query_json.contains("events") && query_json["events"].is_array()) {
    //     json events_results = json::array();
    //     for (const auto &ev : query_json["events"]) {
    //         events_results.push_back(process_one(ev));
    //     }
    //     final_output["events_results"] = events_results;
    // } else {
    //     // fallback: old single-query style (top-level fleet & orders)
    //     final_output = process_one(query_json);
    //! } 
// Build results as an array (one entry per event)
    //* json results = json::array();

    // if (query_json.contains("events") && query_json["events"].is_array()) {
    //     for (const auto &ev : query_json["events"]) {
    //         results.push_back(process_one(ev));
    //     }
    // } else {
    //     // fallback: old single-query style (top-level fleet & orders)
    //     results.push_back(process_one(query_json));
    // }

    //* final_output["results"] = results;

    // Build results as an array (one entry per event), with processing_time for each
        json results = json::array();

        if (query_json.contains("events") && query_json["events"].is_array()) {
            for (const auto &ev : query_json["events"]) {
                auto start_time = std::chrono::high_resolution_clock::now();

                json ans = process_one(ev);

                auto end_time = std::chrono::high_resolution_clock::now();
                double processing_ms =
                    std::chrono::duration<double, std::milli>(end_time - start_time).count();

                ans["processing_time"] = processing_ms;
                results.push_back(ans);
            }
        } else {
            // fallback: old single-query style (top-level fleet & orders)
            auto start_time = std::chrono::high_resolution_clock::now();

            json ans = process_one(query_json);

            auto end_time = std::chrono::high_resolution_clock::now();
            double processing_ms =
                std::chrono::duration<double, std::milli>(end_time - start_time).count();

            ans["processing_time"] = processing_ms;
            results.push_back(ans);
        }

        final_output["results"] = results;

    // write output file
    try {
        std::ofstream of(output_path);
        if (!of.is_open()) {
            std::cerr << "Failed to open output file: " << output_path << std::endl;
            return 1;
        }
        of << final_output.dump(4) << "\n";
    } catch (const std::exception &e) {
        std::cerr << "Failed to write output: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
