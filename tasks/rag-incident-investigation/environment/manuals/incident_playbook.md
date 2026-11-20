# Production Architecture & Incident Investigation Playbook

## System Topology & Microservices Overview
Our distributed checkout infrastructure consists of four primary subsystems:
1. `ingress-gateway`: Envoy proxy terminating TLS and routing requests by URI prefix.
2. `order-service`: Handles shopping carts, coupon application, and inventory reservation.
3. `payment-orchestrator`: Interfaces with external acquiring banks and credit processors.
4. `db-cluster`: Primary PostgreSQL cluster with read replicas and Redis L2 cache.

---

## Known Incident Signatures & Root Cause Diagnostics

### Incident INC-8041: Connection Pool Exhaustion (Error Code: ERR_POOL_STARVATION)
* **Symptoms:** HTTP 504 Gateway Timeouts on `/api/v2/checkout/submit` during traffic spikes.
* **Root Cause:** In `payment-orchestrator`, connection timeout inheritance is improperly scoped. When an upstream acquirer experiences latency spikes exceeding 1500ms, the asynchronous HTTP client does not release connection handles back to the connection pool semaphore.
* **Diagnostic Verification:** Check metrics for `active_conns == max_conns (128)` while `idle_conns == 0`.
* **Remediation:** Configure `connect_timeout = 2.0s` and `read_timeout = 3.5s` explicitly on the `PoolManager` instance, and enforce `try/finally` handle release around `dispatch_settlement_batch()`.
* **Playbook Section ID:** `PLAYBOOK-SEC-402-CONCURRENCY`

### Incident INC-9210: Redis Cache Serialization Poisoning (Error Code: ERR_CACHE_POISON)
* **Symptoms:** HTTP 500 Internal Server Errors with `UnicodeDecodeError` or `PickleError`.
* **Root Cause:** Rolling deployment mismatch between v2.1 (using MsgPack) and v2.2 (using JSON).
* **Remediation:** Purge Redis key pattern `checkout:cart:*` and align serialization protocol across all instances.
* **Playbook Section ID:** `PLAYBOOK-SEC-508-CACHE`

### Incident INC-7104: Idempotency Key Lock Livelock (Error Code: ERR_IDEMPOTENCY_LIVELOCK)
* **Symptoms:** Orders stuck in `PROCESSING_PENDING` indefinitely.
* **Root Cause:** Distributed lock lease TTL set to 0 with no heartbeat renewal.
* **Remediation:** Set distributed lock lease time to 30 seconds with exponential backoff on retry.
* **Playbook Section ID:** `PLAYBOOK-SEC-314-LOCKS`
