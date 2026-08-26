# Feed-Sync Platform Benchmark Task

## Context
You are building an RSS/Atom feed synchronization engine with optimistic concurrency and deduplication.

## Requirements
1. **Normalization & Ingestion**: Parse incoming feeds and normalize episode dates to UTC ISO-8601.
2. **Optimistic Concurrency**: Reject stale writes using HTTP `If-Match` headers.
3. **Idempotency**: Multiple calls to `/reconcile` with the same GUID must produce exactly one entry.
4. **Auth**: Require `X-Editor-Key` on all mutative operations.
