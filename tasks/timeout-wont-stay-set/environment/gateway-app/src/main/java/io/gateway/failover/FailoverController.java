package io.gateway.failover;

import io.gateway.config.TenantSpec;
import io.gateway.metrics.ConnectMetrics;
import io.gateway.pool.PoolManager;

import java.sql.Connection;
import java.sql.SQLException;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Re-establishes a tenant's connectivity after it has been marked unhealthy.
 *
 * <p>Re-establishing retries a fresh physical connection through
 * {@link PoolManager#openConnection(String)} up to the tenant's configured retry
 * budget, with a short backoff between attempts and an overall time bound. The
 * timed outcome, including how many attempts were made, is reported through the
 * metrics recorder and the log.
 */
public final class FailoverController {

    private static final Logger LOG = LoggerFactory.getLogger(FailoverController.class);

    /** Overall wall-clock bound for a single failover, across all its attempts. */
    private static final long FAILOVER_DEADLINE_MS = 30_000L;
    /** Pause between successive attempts. */
    private static final long BACKOFF_MS = 50L;

    private final PoolManager poolManager;
    private final ConnectMetrics metrics;

    /** Remaining attempts in the retry budget. */
    private int retriesRemaining = -1;

    public FailoverController(PoolManager poolManager, ConnectMetrics metrics) {
        this.poolManager = poolManager;
        this.metrics = metrics;
    }

    /**
     * Marks the tenant unhealthy and retries until a connection is established,
     * the tenant's retry budget is spent, or the failover deadline passes.
     *
     * @param tenantId the tenant to re-establish
     * @return the timed outcome of the failover, including the attempt count
     */
    public FailoverOutcome reestablish(String tenantId) {
        LOG.info("re-establishing connectivity for tenant {}", tenantId);
        long start = System.nanoTime();
        long deadline = start + TimeUnit.MILLISECONDS.toNanos(FAILOVER_DEADLINE_MS);

        int maxRetries = maxRetriesFor(tenantId);
        if (retriesRemaining < 0) retriesRemaining = maxRetries;
        int attempts = 0;
        String lastMessage = "not attempted";

        while (attempts < maxRetries && retriesRemaining > 0 && System.nanoTime() < deadline) {
            attempts++;
            retriesRemaining--;
            try (Connection conn = poolManager.openConnection(tenantId)) {
                long elapsedMs = elapsedMs(start);
                metrics.record(tenantId, true, elapsedMs);
                LOG.info("tenant {} re-established in {}ms after {} attempt(s)", tenantId, elapsedMs, attempts);
                return new FailoverOutcome(tenantId, true, "connected", elapsedMs, attempts);
            } catch (SQLException e) {
                lastMessage = e.getMessage();
                sleep(BACKOFF_MS);
            }
        }

        long elapsedMs = elapsedMs(start);
        metrics.record(tenantId, false, elapsedMs);
        LOG.warn("tenant {} failover exhausted after {} attempt(s) in {}ms: {}",
                tenantId, attempts, elapsedMs, lastMessage);
        return new FailoverOutcome(tenantId, false, lastMessage, elapsedMs, attempts);
    }

    private int maxRetriesFor(String tenantId) {
        TenantSpec spec = poolManager.spec(tenantId);
        return spec == null ? 1 : spec.maxRetries();
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static long elapsedMs(long startNanos) {
        return (System.nanoTime() - startNanos) / 1_000_000L;
    }
}
