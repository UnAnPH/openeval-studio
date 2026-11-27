package io.gateway;

import io.gateway.config.ConfigLoader;
import io.gateway.config.TenantSpec;
import io.gateway.failover.FailoverController;
import io.gateway.failover.FailoverOutcome;
import io.gateway.metrics.ConnectMetrics;
import io.gateway.pool.PoolManager;
import io.gateway.routing.Router;

import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Entry point for the multi-tenant database gateway.
 *
 * <p>On startup the gateway loads the tenant registry from
 * {@code /app/tenants.conf} and registers a connection pool for each tenant.
 * It then runs a single CLI command and exits:
 *
 * <pre>
 *   Gateway failover &lt;tenantId&gt;   re-establish a tenant and print the connect outcome
 *   Gateway route    &lt;tenantId&gt;   route one normal request through the tenant's pool
 * </pre>
 */
public final class Gateway {

    private static final Logger LOG = LoggerFactory.getLogger(Gateway.class);
    private static final String CONFIG_PATH = "/app/tenants.conf";

    private Gateway() {
    }

    public static void main(String[] args) {
        if (args.length != 2) {
            usage();
            System.exit(2);
        }

        String command = args[0];
        String tenantId = args[1];

        List<TenantSpec> specs = ConfigLoader.load(CONFIG_PATH);
        ConnectMetrics metrics = new ConnectMetrics();
        PoolManager poolManager = new PoolManager();
        for (TenantSpec spec : specs) {
            poolManager.register(spec);
        }
        LOG.info("registered {} tenants", specs.size());

        switch (command) {
            case "failover" -> runFailover(poolManager, metrics, tenantId);
            case "route" -> runRoute(poolManager, metrics, tenantId);
            default -> {
                usage();
                System.exit(2);
            }
        }
    }

    private static void runFailover(PoolManager poolManager, ConnectMetrics metrics, String tenantId) {
        FailoverController controller = new FailoverController(poolManager, metrics);
        FailoverOutcome outcome = controller.reestablish(tenantId);
        System.out.printf(
                "tenant=%s status=%s attempts=%d elapsedMs=%d message=%s%n",
                outcome.tenantId(),
                outcome.success() ? "ok" : "failed",
                outcome.attempts(),
                outcome.elapsedMs(),
                outcome.message());
    }

    private static void runRoute(PoolManager poolManager, ConnectMetrics metrics, String tenantId) {
        Router router = new Router(poolManager, metrics);
        boolean ok = router.route(tenantId);
        System.out.printf(
                "tenant=%s status=%s elapsedMs=%d%n",
                tenantId,
                ok ? "ok" : "failed",
                metrics.lastElapsedMs(tenantId));
    }

    private static void usage() {
        System.err.println("usage: Gateway <failover|route> <tenantId>");
    }
}
