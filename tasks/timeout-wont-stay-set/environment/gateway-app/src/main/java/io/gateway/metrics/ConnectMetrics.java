package io.gateway.metrics;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Records connection-attempt counts and durations per tenant.
 *
 * <p>Used by the routing and failover paths to track how often connections are
 * opened for each tenant and how long the most recent attempt took.
 */
public final class ConnectMetrics {

    /** Per-tenant rolling counters and the duration of the most recent attempt. */
    private static final class Stat {
        long attempts;
        long successes;
        long failures;
        long lastElapsedMs;
    }

    private final Map<String, Stat> stats = new ConcurrentHashMap<>();

    /**
     * Records the outcome and duration of a single connect attempt for a tenant.
     *
     * @param tenantId  the tenant the attempt was made for
     * @param ok        whether a connection was obtained
     * @param elapsedMs how long the attempt took, in milliseconds
     */
    public void record(String tenantId, boolean ok, long elapsedMs) {
        Stat s = stats.computeIfAbsent(tenantId, k -> new Stat());
        synchronized (s) {
            s.attempts++;
            if (ok) {
                s.successes++;
            } else {
                s.failures++;
            }
            s.lastElapsedMs = elapsedMs;
        }
    }

    /** Returns the duration of the most recent attempt for a tenant, or 0 if none. */
    public long lastElapsedMs(String tenantId) {
        Stat s = stats.get(tenantId);
        if (s == null) {
            return 0L;
        }
        synchronized (s) {
            return s.lastElapsedMs;
        }
    }

    /** Returns a human-readable snapshot of the recorded metrics. */
    public String snapshot() {
        StringBuilder sb = new StringBuilder();
        stats.forEach((tenantId, s) -> {
            synchronized (s) {
                sb.append(tenantId)
                        .append(": attempts=").append(s.attempts)
                        .append(" successes=").append(s.successes)
                        .append(" failures=").append(s.failures)
                        .append(" lastElapsedMs=").append(s.lastElapsedMs)
                        .append('\n');
            }
        });
        return sb.toString();
    }
}
