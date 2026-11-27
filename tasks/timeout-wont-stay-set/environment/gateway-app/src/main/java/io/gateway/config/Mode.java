package io.gateway.config;

/**
 * How a tenant's connection pool is wired to its backing database.
 *
 * <ul>
 *   <li>{@link #URL} - the pool is built directly from the tenant's JDBC URL.</li>
 *   <li>{@link #DATASOURCE} - the pool is built from a configured vendor
 *       {@link javax.sql.DataSource} that the pool delegates to.</li>
 * </ul>
 */
public enum Mode { URL, DATASOURCE }
