#pragma once

#include <vector>
#include <utility>
#include "graph.h"

// we'll use the Order struct already defined in graph.h:
// Order : int id int pickup int dropoff

std::vector<std::vector<int>> assign_orders_kmeans_pick_drop(
    const std::vector<Order> &orders,
    const std::vector<std::pair<double,double>> &node_coords,
    int num_drivers,
    double alpha = 0.6
);

void build_routes(
    Graph &g,
    int depot_node,
    const std::vector<Order> &orders,
    const std::vector<std::vector<int>> &driver_order_indices,
    std::vector<std::vector<int>> &driver_routes,
    double &total_delivery_time_s
);
