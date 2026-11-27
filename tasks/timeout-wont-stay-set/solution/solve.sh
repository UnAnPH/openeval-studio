#!/bin/bash
set -euo pipefail

# Reference solution. Restore per-tenant isolation at all three layers.
#   L1  each URL pool's physical connect uses its own driver connect timeout,
#       not a process-wide default that co-resident pools overwrite.
#   L2  each failover gets its own retry budget, not a counter shared and
#       carried across tenants.
#   L3  each tenant gets its own concurrency gate, not one shared semaphore.
# Behavioral only: public API and the single-tenant / supplied-DataSource paths
# are untouched. Uses sed (the task image has no python).

PM=/app/src/main/java/io/gateway/pool/PoolManager.java
FC=/app/src/main/java/io/gateway/failover/FailoverController.java

# L1
sed -i 's|            c.setJdbcUrl(spec.jdbcUrl());|            c.addDataSourceProperty("oracle.net.CONNECT_TIMEOUT", String.valueOf(spec.connectTimeoutMs()));\n            c.setJdbcUrl(spec.jdbcUrl());|' "$PM"
grep -q 'oracle.net.CONNECT_TIMEOUT' "$PM" || { echo "solve: L1 patch did not apply" >&2; exit 1; }

# L3
sed -i 's|        limiters.put(spec.tenantId(), gate);|        limiters.put(spec.tenantId(), new Semaphore(spec.maxConcurrent()));|' "$PM"
grep -q 'limiters.put(spec.tenantId(), new Semaphore' "$PM" || { echo "solve: L3 patch did not apply" >&2; exit 1; }

# L2
sed -i 's|        if (retriesRemaining < 0) retriesRemaining = maxRetries;|        int retriesRemaining = maxRetries;|' "$FC"
grep -q 'int retriesRemaining = maxRetries;' "$FC" || { echo "solve: L2 patch did not apply" >&2; exit 1; }

bash /app/build.sh
