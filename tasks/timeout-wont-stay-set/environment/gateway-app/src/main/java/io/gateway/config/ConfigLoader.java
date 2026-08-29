package io.gateway.config;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Parses the tenant registry into a list of {@link TenantSpec}.
 *
 * <p>The registry is a line-oriented text file. Blank lines and lines whose
 * first non-whitespace character is {@code #} are ignored. Every other line
 * describes one tenant:
 *
 * <pre>
 *   &lt;tenantId&gt; key=value key=value ...
 * </pre>
 *
 * <p>Recognized keys:
 * <ul>
 *   <li>{@code url} - the tenant's JDBC URL (required)</li>
 *   <li>{@code connectTimeoutMs} - connect fail-fast budget in ms (required)</li>
 *   <li>{@code mode} - {@code URL} or {@code DATASOURCE} (required)</li>
 *   <li>{@code poolSize} - maximum pooled connections (default 10)</li>
 *   <li>{@code validationTimeoutMs} - validation budget in ms (default 5000)</li>
 *   <li>{@code maxLifetimeMs} - pooled connection lifetime in ms (default 1800000)</li>
 *   <li>{@code maxRetries} - per-tenant failover retry budget (default 3)</li>
 *   <li>{@code maxConcurrent} - per-tenant in-flight connection cap (default 4)</li>
 * </ul>
 *
 * <p>Tenant order is preserved as written in the file.
 */
public final class ConfigLoader {

    private static final int DEFAULT_POOL_SIZE = 10;
    private static final int DEFAULT_VALIDATION_TIMEOUT_MS = 5000;
    private static final long DEFAULT_MAX_LIFETIME_MS = 1_800_000L;
    private static final int DEFAULT_MAX_RETRIES = 3;
    private static final int DEFAULT_MAX_CONCURRENT = 4;

    private ConfigLoader() {
    }

    /** Loads tenant specs from the file at the given path string. */
    public static List<TenantSpec> load(String path) {
        return load(Path.of(path));
    }

    /**
     * Loads tenant specs from the given file, preserving declaration order.
     *
     * @throws UncheckedIOException     if the file cannot be read
     * @throws IllegalArgumentException if any line is malformed
     */
    public static List<TenantSpec> load(Path path) {
        List<String> lines;
        try {
            lines = Files.readAllLines(path);
        } catch (IOException e) {
            throw new UncheckedIOException("unable to read tenant registry at " + path, e);
        }

        List<TenantSpec> specs = new ArrayList<>();
        for (int i = 0; i < lines.size(); i++) {
            String raw = lines.get(i).strip();
            if (raw.isEmpty() || raw.startsWith("#")) {
                continue;
            }
            specs.add(parseLine(raw, i + 1));
        }

        if (specs.isEmpty()) {
            throw new IllegalArgumentException("tenant registry " + path + " defines no tenants");
        }
        return specs;
    }

    private static TenantSpec parseLine(String line, int lineNo) {
        String[] tokens = line.split("\\s+");
        String tenantId = tokens[0];
        if (tenantId.isEmpty()) {
            throw new IllegalArgumentException("line " + lineNo + ": missing tenant id");
        }

        Map<String, String> fields = new LinkedHashMap<>();
        for (int t = 1; t < tokens.length; t++) {
            String token = tokens[t];
            int eq = token.indexOf('=');
            if (eq <= 0 || eq == token.length() - 1) {
                throw new IllegalArgumentException(
                        "line " + lineNo + ": expected key=value but found '" + token + "'");
            }
            fields.put(token.substring(0, eq), token.substring(eq + 1));
        }

        String url = required(fields, "url", lineNo, tenantId);
        Mode mode = parseMode(required(fields, "mode", lineNo, tenantId), lineNo, tenantId);
        int connectTimeoutMs = positiveInt(
                required(fields, "connectTimeoutMs", lineNo, tenantId), "connectTimeoutMs", lineNo, tenantId);

        int poolSize = fields.containsKey("poolSize")
                ? positiveInt(fields.get("poolSize"), "poolSize", lineNo, tenantId)
                : DEFAULT_POOL_SIZE;
        int validationTimeoutMs = fields.containsKey("validationTimeoutMs")
                ? positiveInt(fields.get("validationTimeoutMs"), "validationTimeoutMs", lineNo, tenantId)
                : DEFAULT_VALIDATION_TIMEOUT_MS;
        long maxLifetimeMs = fields.containsKey("maxLifetimeMs")
                ? positiveLong(fields.get("maxLifetimeMs"), "maxLifetimeMs", lineNo, tenantId)
                : DEFAULT_MAX_LIFETIME_MS;
        int maxRetries = fields.containsKey("maxRetries")
                ? positiveInt(fields.get("maxRetries"), "maxRetries", lineNo, tenantId)
                : DEFAULT_MAX_RETRIES;
        int maxConcurrent = fields.containsKey("maxConcurrent")
                ? positiveInt(fields.get("maxConcurrent"), "maxConcurrent", lineNo, tenantId)
                : DEFAULT_MAX_CONCURRENT;

        return new TenantSpec(tenantId, url, connectTimeoutMs, mode,
                poolSize, validationTimeoutMs, maxLifetimeMs, maxRetries, maxConcurrent);
    }

    private static String required(Map<String, String> fields, String key, int lineNo, String tenantId) {
        String value = fields.get(key);
        if (value == null) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): missing required key '" + key + "'");
        }
        return value;
    }

    private static Mode parseMode(String value, int lineNo, String tenantId) {
        try {
            return Mode.valueOf(value.toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): unknown mode '" + value + "'");
        }
    }

    private static int positiveInt(String value, String key, int lineNo, String tenantId) {
        int parsed;
        try {
            parsed = Integer.parseInt(value);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): '" + key + "' must be an integer, got '" + value + "'");
        }
        if (parsed <= 0) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): '" + key + "' must be positive, got " + parsed);
        }
        return parsed;
    }

    private static long positiveLong(String value, String key, int lineNo, String tenantId) {
        long parsed;
        try {
            parsed = Long.parseLong(value);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): '" + key + "' must be an integer, got '" + value + "'");
        }
        if (parsed <= 0) {
            throw new IllegalArgumentException(
                    "line " + lineNo + " (tenant " + tenantId + "): '" + key + "' must be positive, got " + parsed);
        }
        return parsed;
    }
}
