import io.gateway.config.Mode;
import io.gateway.config.TenantSpec;
import io.gateway.failover.FailoverController;
import io.gateway.failover.FailoverOutcome;
import io.gateway.metrics.ConnectMetrics;
import io.gateway.pool.PoolManager;

import java.io.FileWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.sql.Connection;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Gray-box verifier for the stacked-bug gateway. Drives the agent's code with
 * held-out randomized configs and checks three per-tenant isolation guarantees,
 * in order, stopping at the first failure:
 *
 *   L1  connect-timeout isolation: each pool's physical connect gives up at its
 *       own connect timeout (URL pools via the driver's ORA-12170, DataSource
 *       via ORA-18714/wall-clock); an external-abandon "fix" is rejected.
 *   L2  retry-budget isolation: each tenant's failover retries up to its own
 *       maxRetries regardless of another tenant's prior failover.
 *   L3  concurrency-cap isolation: a tenant under its own in-flight cap is not
 *       throttled by another tenant saturating its cap.
 *
 * Deeper checks are only meaningful once the shallower fix is in, so a failure
 * stops the ladder. Writes a JSON verdict to argv[0]. A connect-must-hang
 * self-check guards against a broken black hole reporting a false pass.
 */
public class Verifier {

    static final int TOL_MS = 2500;
    static final int TOL_DS_MS = 3500;
    static final Pattern TCP_TO = Pattern.compile("TCP connect timeout of (\\d+)\\s*ms");

    public static void main(String[] args) throws Exception {
        String out = args.length > 0 ? args[0] : "/tmp/verifier_result.json";
        try {
            boolean hangs = false;
            for (int i = 0; i < 3 && !hangs; i++) {
                hangs = rawConnectHangs("192.0.2.31", 1521, 3000);
            }
            if (!hangs) {
                write(out, "{\"harnessValid\":false,\"reason\":\"black hole did not hang on a raw connect\"}");
                System.out.println("HARNESS-INVALID: black hole did not hang");
                return;
            }

            Layer l1 = checkL1();
            Layer l2 = l1.ok ? checkL2() : Layer.skip("not evaluated - fix L1 first");
            Layer l3 = (l1.ok && l2.ok) ? checkL3() : Layer.skip("not evaluated - fix L" + (l1.ok ? "2" : "1") + " first");

            String firstFailure = !l1.ok ? "L1" : !l2.ok ? "L2" : !l3.ok ? "L3" : "none";
            String json = "{\"harnessValid\":true"
                    + ",\"l1\":" + l1.ok + ",\"l2\":" + l2.ok + ",\"l3\":" + l3.ok
                    + ",\"firstFailure\":\"" + firstFailure + "\""
                    + ",\"l1detail\":\"" + esc(l1.detail) + "\""
                    + ",\"l2detail\":\"" + esc(l2.detail) + "\""
                    + ",\"l3detail\":\"" + esc(l3.detail) + "\"}";
            write(out, json);
            System.out.println(json);
        } catch (Throwable t) {
            write(out, "{\"harnessValid\":true,\"error\":\"" + esc(String.valueOf(t)) + "\"}");
            throw t;
        }
    }

    // L1: every pool's physical connect honors its own connect timeout.
    static Layer checkL1() {
        Random rnd = new Random();
        int shortMs = pick(rnd, new int[]{6000, 7000, 8000, 9000});
        // The long pool sits above the driver's ~20s default connect ceiling, so a
        // login-timeout "fix" caps it at ~20s and misses the configured value while
        // the real per-pool connect-timeout fix honors it.
        int longMs = pick(rnd, new int[]{25000, 26000, 27000, 28000});
        int dsMs = pick(rnd, new int[]{13000, 14000, 15000, 16000});
        TenantSpec uShort = mk("u-short", "192.0.2.41", shortMs, Mode.URL, 3, 8);
        TenantSpec uLong = mk("u-long", "192.0.2.42", longMs, Mode.URL, 3, 8);
        TenantSpec dsKeep = mk("ds-keep", "192.0.2.43", dsMs, Mode.DATASOURCE, 3, 8);
        PoolManager pm = new PoolManager();
        pm.register(uShort);
        pm.register(uLong);
        pm.register(dsKeep);
        Result rShort = probe(pm, uShort);
        Result rLong = probe(pm, uLong);
        Result rDs = probe(pm, dsKeep);
        boolean ok = rShort.ok && rLong.ok && rDs.ok;
        return new Layer(ok, "short=" + rShort.summary() + " long=" + rLong.summary() + " ds=" + rDs.summary());
    }

    // L2: each tenant's failover gets its own retry budget, regardless of order.
    static Layer checkL2() {
        Random rnd = new Random();
        int rA = pick(rnd, new int[]{3, 4, 5});
        int rB = pick(rnd, new int[]{3, 4, 5});
        int tmo = 800;
        PoolManager pm = new PoolManager();
        pm.register(mk("r-a", "192.0.2.51", tmo, Mode.URL, rA, 8));
        pm.register(mk("r-b", "192.0.2.52", tmo, Mode.URL, rB, 8));
        FailoverController fc = new FailoverController(pm, new ConnectMetrics());
        FailoverOutcome oa = fc.reestablish("r-a");
        FailoverOutcome ob = fc.reestablish("r-b");
        boolean ok = oa.attempts() == rA && ob.attempts() == rB;
        return new Layer(ok, "r-a attempts=" + oa.attempts() + "/" + rA + " r-b attempts=" + ob.attempts() + "/" + rB);
    }

    // L3: a tenant under its own in-flight cap is not throttled by another
    // tenant saturating its cap.
    static Layer checkL3() throws Exception {
        int n = 2;
        PoolManager pm = new PoolManager();
        pm.register(mk("q-a", "192.0.2.61", 1500, Mode.URL, 3, n));
        pm.register(mk("q-b", "192.0.2.62", 6000, Mode.URL, 3, n));
        List<Thread> saturators = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            Thread t = new Thread(() -> {
                try (Connection c = pm.openConnection("q-b")) {
                    // never reached - the black hole never completes a connect
                } catch (Throwable ignore) {
                }
            });
            t.setDaemon(true);
            saturators.add(t);
        }
        for (Thread t : saturators) {
            t.start();
        }
        Thread.sleep(600);
        Result ra = probe(pm, mk("q-a", "192.0.2.61", 1500, Mode.URL, 3, n));
        for (Thread t : saturators) {
            t.interrupt();
        }
        boolean quotaRejected = ra.err != null && ra.err.contains("concurrency limit reached");
        boolean ok = "ORA-12170".equals(ra.source) && !quotaRejected;
        return new Layer(ok, "q-a source=" + ra.source + " quotaRejected=" + quotaRejected + " err=" + trim(ra.err, 70));
    }

    // A non-driver connection error that is neither the L3 quota rejection nor a
    // deliberate external-timeout wrapper means the black hole failed to hold this
    // connect this run (a routing/socket fast-fail such as "No route to host") -
    // retry it. Detected by error kind, not timing, so it covers short-timeout
    // probes too. A real external-wrapper "fix" reports a timeout and is not
    // retried away, so it still fails.
    static Result probe(PoolManager pm, TenantSpec s) {
        Result r = probeOnce(pm, s);
        for (int i = 0; i < 4 && isFlake(r); i++) {
            r = probeOnce(pm, s);
        }
        return r;
    }

    static boolean isFlake(Result r) {
        if (r.connected || r.err == null) {
            return false;
        }
        if (!"non-driver".equals(r.source)) {
            return false;
        }
        if (r.err.contains("concurrency limit reached")) {
            return false;
        }
        return !r.err.contains("timed out") && !r.err.contains("timeout");
    }

    static Result probeOnce(PoolManager pm, TenantSpec s) {
        long t0 = System.nanoTime();
        String err = null;
        boolean connected = false;
        try (Connection c = pm.openConnection(s.tenantId())) {
            connected = true;
        } catch (Throwable e) {
            err = root(e);
        }
        int elapsed = (int) ((System.nanoTime() - t0) / 1_000_000);
        Integer ora12170 = parse(err);
        boolean ora18714 = err != null && err.contains("ORA-18714");
        int configured = s.connectTimeoutMs();
        int observed;
        String source;
        boolean ok;
        if (connected) {
            observed = elapsed;
            source = "connected";
            ok = false;
        } else if (ora12170 != null) {
            // Driver's own TCP connect timeout - its named value must match the tenant's own.
            observed = ora12170;
            source = "ORA-12170";
            ok = Math.abs(ora12170 - configured) <= TOL_MS;
        } else if (ora18714) {
            // Driver's own login timeout (carries no value) - bounded by wall-clock.
            observed = elapsed;
            source = "ORA-18714";
            ok = Math.abs(elapsed - configured) <= TOL_DS_MS;
        } else {
            // No Oracle driver timeout (e.g. an external Future/executor TimeoutException).
            observed = elapsed;
            source = "non-driver";
            ok = false;
        }
        return new Result(s.tenantId(), configured, observed, elapsed, source, connected, ok, err);
    }

    static boolean rawConnectHangs(String host, int port, int timeoutMs) {
        try (Socket sk = new Socket()) {
            sk.connect(new InetSocketAddress(host, port), timeoutMs);
            return false;
        } catch (SocketTimeoutException e) {
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    static TenantSpec mk(String id, String ip, int ms, Mode mode, int maxRetries, int maxConcurrent) {
        return new TenantSpec(id, "jdbc:oracle:thin:@//" + ip + ":1521/svc", ms, mode,
                5, 5000, 1800000L, maxRetries, maxConcurrent);
    }

    static int pick(Random r, int[] a) {
        return a[r.nextInt(a.length)];
    }

    static Integer parse(String msg) {
        if (msg == null) {
            return null;
        }
        Matcher m = TCP_TO.matcher(msg);
        return m.find() ? Integer.parseInt(m.group(1)) : null;
    }

    static String root(Throwable e) {
        Throwable r = e;
        while (r.getCause() != null) {
            r = r.getCause();
        }
        return r.getClass().getSimpleName() + ": " + String.valueOf(r.getMessage()).replaceAll("\\s+", " ").trim();
    }

    static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    static String trim(String s, int n) {
        return s == null ? "" : (s.length() > n ? s.substring(0, n) : s);
    }

    static void write(String path, String s) throws Exception {
        try (FileWriter w = new FileWriter(path)) {
            w.write(s);
        }
    }

    static final class Layer {
        final boolean ok;
        final String detail;

        Layer(boolean ok, String detail) {
            this.ok = ok;
            this.detail = detail;
        }

        static Layer skip(String detail) {
            return new Layer(false, detail);
        }
    }

    static final class Result {
        final String tenant;
        final int configuredMs;
        final int observedMs;
        final int elapsedMs;
        final String source;
        final boolean connected;
        final boolean ok;
        final String err;

        Result(String tenant, int configuredMs, int observedMs, int elapsedMs,
               String source, boolean connected, boolean ok, String err) {
            this.tenant = tenant;
            this.configuredMs = configuredMs;
            this.observedMs = observedMs;
            this.elapsedMs = elapsedMs;
            this.source = source;
            this.connected = connected;
            this.ok = ok;
            this.err = err;
        }

        String summary() {
            return source + "(" + observedMs + "ms,cfg=" + configuredMs + ",ok=" + ok + ")";
        }
    }
}
