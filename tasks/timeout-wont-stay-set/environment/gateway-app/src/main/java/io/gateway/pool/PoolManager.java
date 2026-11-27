package io.gateway.pool;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;

import io.gateway.config.Mode;
import io.gateway.config.TenantSpec;

import java.sql.Connection;
import java.sql.SQLException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;

import oracle.jdbc.pool.OracleDataSource;

/**
 * Owns one connection pool per tenant inside a single JVM and routes
 * connection requests to the right pool.
 *
 * <p>Each tenant is registered once via {@link #register(TenantSpec)}, which
 * builds and stores a {@link HikariDataSource} keyed by tenant id along with the
 * tenant's connection limit. Normal traffic borrows pooled connections through
 * {@link #dataSource(String)}; health and failover paths open fresh physical
 * connections through {@link #openConnection(String)}, which admits at most the
 * tenant's configured number of in-flight attempts.
 */
public final class PoolManager {

    private final Map<String, HikariDataSource> pools = new ConcurrentHashMap<>();
    private final Map<String, TenantSpec> registry = new ConcurrentHashMap<>();
    private final Map<String, Semaphore> limiters = new ConcurrentHashMap<>();

    /** Admission gate bounding the number of in-flight connection attempts. */
    private Semaphore gate;

    /**
     * Builds and stores the connection pool for the given tenant, along with its
     * concurrency limit. The pool is configured with the tenant's own connect
     * timeout, pool size, validation timeout, and connection lifetime.
     */
    public void register(TenantSpec spec) {
        HikariConfig c = new HikariConfig();
        c.setPoolName(spec.tenantId());
        c.setDriverClassName("oracle.jdbc.OracleDriver");
        c.setUsername("u");
        c.setPassword("p");
        c.setConnectionTimeout(spec.connectTimeoutMs());
        c.setMaximumPoolSize(spec.poolSize());
        c.setMinimumIdle(0);
        c.setInitializationFailTimeout(-1);
        c.setValidationTimeout(spec.validationTimeoutMs());
        c.setMaxLifetime(spec.maxLifetimeMs());

        if (spec.mode() == Mode.DATASOURCE) {
            c.setDataSource(buildOracleDataSource(spec.jdbcUrl()));
        } else {
            c.setJdbcUrl(spec.jdbcUrl());
        }

        pools.put(spec.tenantId(), new HikariDataSource(c));
        registry.put(spec.tenantId(), spec);
        if (gate == null) {
            gate = new Semaphore(spec.maxConcurrent());
        }
        limiters.put(spec.tenantId(), gate);
    }

    /**
     * Opens a fresh physical connection for the given tenant by delegating to the
     * tenant pool's underlying connection source, admitting at most the tenant's
     * configured number of in-flight attempts. Used by deep health and failover
     * checks. Any {@link SQLException} raised while connecting is propagated to
     * the caller.
     *
     * @throws SQLException if the tenant is unknown, its in-flight limit is
     *                      reached, or the connection cannot be opened
     */
    public Connection openConnection(String tenantId) throws SQLException {
        HikariDataSource ds = pools.get(tenantId);
        if (ds == null) {
            throw new SQLException("unknown tenant: " + tenantId);
        }
        Semaphore limiter = limiters.get(tenantId);
        boolean admitted = limiter == null || limiter.tryAcquire();
        if (!admitted) {
            throw new SQLException("tenant " + tenantId + " concurrency limit reached");
        }
        try {
            return HikariProbe.connectionSource(ds).getConnection();
        } catch (SQLException e) {
            throw e;
        } catch (Exception e) {
            throw new SQLException("unable to open connection for tenant " + tenantId, e);
        } finally {
            if (limiter != null) {
                limiter.release();
            }
        }
    }

    /** Returns the registered spec for a tenant, or {@code null} if unknown. */
    public TenantSpec spec(String tenantId) {
        return registry.get(tenantId);
    }

    /**
     * Returns the tenant's pool for normal traffic, which borrows pooled
     * connections via the returned data source.
     *
     * @throws IllegalArgumentException if the tenant is unknown
     */
    public HikariDataSource dataSource(String tenantId) {
        HikariDataSource ds = pools.get(tenantId);
        if (ds == null) {
            throw new IllegalArgumentException("unknown tenant: " + tenantId);
        }
        return ds;
    }

    private static OracleDataSource buildOracleDataSource(String jdbcUrl) {
        try {
            OracleDataSource ods = new OracleDataSource();
            ods.setURL(jdbcUrl);
            return ods;
        } catch (SQLException e) {
            throw new IllegalStateException("unable to configure data source for " + jdbcUrl, e);
        }
    }
}
