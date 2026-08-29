#ifndef QUERY_H
#define QUERY_H
#include "../nlohmann/json.hpp"
#include "graph.h"

using json = nlohmann::json;

json process_query(json query, Graph& g);

#endif
