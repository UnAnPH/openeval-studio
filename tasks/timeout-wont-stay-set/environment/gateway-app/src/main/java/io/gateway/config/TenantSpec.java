package io.gateway.config;

/**
 * Immutable description of a single tenant's pool configuration, as parsed from
 * the tenant registry.
 *
 * @param tenantId          stable identifier used to route requests to this tenant
 * @param jdbcUrl           the tenant's Oracle JDBC URL
 * @param connectTimeoutMs  per-tenant connect fail-fast budget, in milliseconds;
 *                          enforces the tenant's connect SLA
 * @param mode              how the pool is wired ({@link Mode#URL} or {@link Mode#DATASOURCE})
 * @param poolSize          maximum number of pooled connections for this tenant
 * @param validationTimeoutMs  how long connection validation may take, in milliseconds
 * @param maxLifetimeMs     maximum lifetime of a pooled connection, in milliseconds
 * @param maxRetries        per-tenant failover retry budget (attempts before giving up)
 * @param maxConcurrent     per-tenant cap on in-flight connection attempts
 */
public record TenantSpec(String tenantId, String jdbcUrl, int connectTimeoutMs, Mode mode,
                         int poolSize, int validationTimeoutMs, long maxLifetimeMs,
                         int maxRetries, int maxConcurrent) {}
