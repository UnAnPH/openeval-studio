The project in `/app` is a multi-tenant database gateway. It reads tenant
definitions from `/app/tenants.conf` — each tenant has its own JDBC URL and its
own connection settings — and builds a separate HikariCP connection pool per
tenant inside one JVM. The gateway enforces three per-tenant guarantees, and
each must hold independently for every tenant, regardless of how many tenants
are configured or what the others are doing:

- **Connect timeout.** Opening a connection for a tenant must give up at roughly
  that tenant's own `connectTimeoutMs`. (None of the tenant databases are
  reachable in this environment, so a physical connection attempt blocks until
  the connect timeout elapses.)
- **Failover retry budget.** When a tenant's connection fails, the failover path
  retries up to that tenant's own `maxRetries` before giving up.
- **Concurrency cap.** At most `maxConcurrent` connection attempts may be in
  flight for a tenant at once; an attempt made while that tenant is already at
  its cap is rejected immediately. The cap is per tenant — one tenant being at
  its cap must never reject or delay another tenant's attempt.

One or more of these guarantees is currently violated: a tenant's behavior is
being affected by *other* tenants. For instance, a tenant whose `connectTimeoutMs`
is 10 seconds will sometimes take far longer to give up when its pool opens a
fresh connection during failover — but only when more than one tenant is
configured, and never with a single tenant in isolation. On inspection each
tenant's configuration is correct; the leaks are in how the gateway manages
shared machinery across tenants.

Fix the gateway so all three guarantees hold per tenant, independent of tenant
count, registration order, and what other tenants are doing. The connect timeout
must bound the connection attempt itself — do not satisfy it by leaving the
underlying connect running and abandoning it with an external timeout or
executor. Do not change the single-tenant case, and do not change pools
configured through a supplied `DataSource` rather than a JDBC URL. Work within
the libraries already vendored under `/app/lib`; do not add new dependencies.
Keep the public signatures of `io.gateway.pool.PoolManager` (`register`,
`openConnection`) and `io.gateway.failover.FailoverController` (`reestablish`)
intact — the fixes are behavioral, not API changes.
