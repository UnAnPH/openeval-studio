package io.gateway.failover;

/**
 * Result of a single attempt to re-establish a tenant's connectivity.
 *
 * @param tenantId  the tenant the attempt was made for
 * @param success   whether a connection was successfully opened
 * @param message   on success, a short status; on failure, the connection error message
 * @param elapsedMs how long the failover took overall, in milliseconds
 * @param attempts  how many connect attempts were made
 */
public record FailoverOutcome(String tenantId, boolean success, String message, long elapsedMs, int attempts) {
}
