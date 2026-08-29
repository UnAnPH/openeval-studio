package io.gateway.pool;

import com.zaxxer.hikari.HikariDataSource;

import java.lang.reflect.Field;

import javax.sql.DataSource;

/**
 * Low-level health-probe utilities for a {@link HikariDataSource}.
 *
 * <p>A deep health probe needs to open a <em>real, physical</em> connection to
 * the backing database rather than borrow a connection that the pool may have
 * already established and cached. {@link #connectionSource(HikariDataSource)}
 * exposes the underlying connection source a pool delegates to, so a probe can
 * open a fresh physical connection directly from it.
 */
final class HikariProbe {

    private HikariProbe() {
    }

    /**
     * Returns the underlying connection source that the given pool delegates to,
     * suitable for opening a fresh physical connection during a deep health probe.
     */
    static DataSource connectionSource(HikariDataSource ds) throws Exception {
        Object pool = field(ds, "pool");
        if (pool == null) {
            pool = field(ds, "fastPathPool");
        }
        for (Class<?> k = pool.getClass(); k != null; k = k.getSuperclass()) {
            for (Field f : k.getDeclaredFields()) {
                if (DataSource.class.isAssignableFrom(f.getType())) {
                    f.setAccessible(true);
                    Object v = f.get(pool);
                    if (v != null) {
                        return (DataSource) v;
                    }
                }
            }
        }
        throw new IllegalStateException("connection source unavailable");
    }

    /** Reads the named field from {@code o}, or returns {@code null} if it is absent. */
    private static Object field(Object o, String name) throws Exception {
        try {
            Field f = o.getClass().getDeclaredField(name);
            f.setAccessible(true);
            return f.get(o);
        } catch (NoSuchFieldException e) {
            return null;
        }
    }
}
