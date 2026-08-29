package io.gateway.routing;

import io.gateway.metrics.ConnectMetrics;
import io.gateway.pool.PoolManager;

import java.sql.Connection;
import java.sql.SQLException;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Routes normal tenant traffic to the right pool.
 *
 * <p>For ordinary requests the router borrows a pooled connection from the
 * tenant's pool, uses it, and returns it to the pool. The attempt is timed and
 * recorded so pool usage is visible in the metrics.
 */
public final class Router {

    private static final Logger LOG = LoggerFactory.getLogger(Router.class);

    private final PoolManager poolManager;
    private final ConnectMetrics metrics;

    public Router(PoolManager poolManager, ConnectMetrics metrics) {
        this.poolManager = poolManager;
        this.metrics = metrics;
    }

    /**
     * Handles a single request for the given tenant by borrowing a pooled
     * connection.
     *
     * @param tenantId the tenant to route to
     * @return {@code true} if a connection was borrowed and returned successfully
     */
    public boolean route(String tenantId) {
        long start = System.nanoTime();
        try (Connection conn = poolManager.dataSource(tenantId).getConnection()) {
            boolean valid = conn.isValid(1);
            long elapsedMs = elapsedMs(start);
            metrics.record(tenantId, valid, elapsedMs);
            LOG.debug("routed request for tenant {} in {}ms", tenantId, elapsedMs);
            return valid;
        } catch (SQLException e) {
            long elapsedMs = elapsedMs(start);
            metrics.record(tenantId, false, elapsedMs);
            LOG.warn("request for tenant {} failed after {}ms: {}", tenantId, elapsedMs, e.getMessage());
            return false;
        }
    }

    private static long elapsedMs(long startNanos) {
        return (System.nanoTime() - startNanos) / 1_000_000L;
    }
}
