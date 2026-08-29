#include "graph.h"

std::vector<Order> load_orders_from_json(const json& q) {
    std::vector<Order> orders;

    for (auto& o : q["orders"]) {
        Order ord;
        ord.id = o["order_id"];
        ord.pickup   = o["pickup"];
        ord.dropoff  = o["dropoff"];
        orders.push_back(ord);
    }
    return orders;
}

Fleet load_fleet_from_json(const json& q) {
    Fleet f;
    f.num_delivery_guys = q["fleet"]["num_delievery_guys"];
    f.depot_node        = q["fleet"]["depot_node"];
    return f;
}
