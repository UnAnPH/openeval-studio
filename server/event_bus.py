"""Redis Streams, Pub/Sub & Distributed Locking for OpenEval Telemetry.

Provides horizontal scaling across gateway replicas via Redis Streams and Pub/Sub,
Redis distributed mutexes (lock:session:{tenant_id}:{session_id}) with automatic fallback,
strict multi-tenant channel and stream isolation (watcher:events:{tenant_id}),
and zero-dependency in-memory queue/stream fallback for local development and testing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast

import redis.asyncio as aioredis
from fastapi import Request
from redis.exceptions import RedisError

from server.db import current_user_id, get_current_user_id

logger = logging.getLogger("openeval.server.event_bus")

REDIS_URL = os.getenv("REDIS_URL", "").strip()
BROADCAST_CHANNEL = "openeval:broadcast:events"
BROADCAST_STREAM = "watcher:events:broadcast"


def _to_jsonable(value: Any) -> Any:
    """Recursively convert Pydantic models / nested containers for JSON serialization."""
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def get_tenant_channel(user_id: int | None) -> str:
    """Format the tenant-scoped Redis Pub/Sub channel name."""
    if user_id is not None:
        return f"openeval:tenant:{user_id}:events"
    return BROADCAST_CHANNEL


def get_stream_key(user_id: int | None) -> str:
    """Format the tenant-scoped Redis Stream key name."""
    if user_id is not None:
        return f"watcher:events:{user_id}"
    return BROADCAST_STREAM


class EventBus:
    """Resilient telemetry event bus supporting Redis Streams, Pub/Sub, and distributed locks."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url if redis_url is not None else REDIS_URL
        self._pool: aioredis.ConnectionPool | None = None
        self._redis_client: aioredis.Redis | None = None
        self._sync_redis_client: Any = None
        self._redis_available: bool | None = None

        # In-memory Pub/Sub subscribers: user_id -> set[asyncio.Queue]
        self._in_memory_subscribers: dict[int | None, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )

        # In-memory Stream log: user_id -> list[tuple[stream_id, dict[str, Any]]]
        self._in_memory_streams: dict[int | None, list[tuple[str, dict[str, Any]]]] = defaultdict(
            list
        )
        self._in_memory_stream_notifiers: dict[int | None, set[asyncio.Event]] = defaultdict(set)
        self._seq = 0

        # Concurrency & distributed locking fallbacks
        self._async_locks: dict[tuple[Any, tuple[int | None, str]], asyncio.Lock] = {}
        self._sync_locks: dict[tuple[int | None, str], threading.Lock] = {}
        self._lock = asyncio.Lock()
        self._lock_loop: Any = None
        self._active_loop: Any = None
        self._thread_lock = threading.Lock()

    async def get_redis_client(self) -> aioredis.Redis | None:
        """Get or initialize the async Redis client with connection pooling."""
        if not self.redis_url:
            return None

        current_loop = None
        with contextlib.suppress(RuntimeError):
            current_loop = asyncio.get_running_loop()

        if self._redis_client is not None and getattr(self, "_active_loop", None) is current_loop:
            return self._redis_client

        if (
            self._redis_client is not None
            and getattr(self, "_active_loop", None) is not current_loop
        ):
            self._redis_client = None
            self._pool = None

        if getattr(self, "_lock_loop", None) is not current_loop:
            self._lock = asyncio.Lock()
            self._lock_loop = current_loop

        async with self._lock:
            if (
                self._redis_client is not None
                and getattr(self, "_active_loop", None) is current_loop
            ):
                return self._redis_client

            try:
                self._pool = aioredis.ConnectionPool.from_url(
                    self.redis_url,
                    max_connections=20,
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                    socket_timeout=5.0,
                )
                client = aioredis.Redis(connection_pool=self._pool)
                await client.ping()
                self._redis_client = client
                self._active_loop = current_loop
                self._redis_available = True
                logger.info("Connected to Redis telemetry bus at %s", self.redis_url)
                return self._redis_client
            except (RedisError, OSError, TimeoutError, ConnectionRefusedError) as e:
                logger.info(
                    "Redis unavailable at %s: %s. Using in-memory fallback.", self.redis_url, e
                )
                self._redis_available = False
                return None

    def get_sync_redis_client(self) -> Any:
        """Get or initialize synchronous Redis client for sync execution paths."""
        if not self.redis_url:
            return None
        with self._thread_lock:
            if self._sync_redis_client is not None:
                return self._sync_redis_client
            try:
                import redis

                pool = redis.ConnectionPool.from_url(
                    self.redis_url,
                    max_connections=10,
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                    socket_timeout=5.0,
                )
                client = redis.Redis(connection_pool=pool)
                client.ping()
                self._sync_redis_client = client
                return self._sync_redis_client
            except Exception as exc:
                logger.debug("Failed initializing sync Redis client: %s", exc)
                return None

    async def is_redis_available(self) -> bool:
        """Check whether Redis is currently active."""
        if not self.redis_url:
            return False
        client = await self.get_redis_client()
        return client is not None

    # -------------------------------------------------------------------------
    # Distributed Locking (Redis Distributed Mutex + Local Fallback)
    # -------------------------------------------------------------------------

    def _get_in_memory_async_lock(self, key: tuple[int | None, str]) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        full_key = (loop, key)
        with self._thread_lock:
            if full_key not in self._async_locks:
                self._async_locks[full_key] = asyncio.Lock()
            return self._async_locks[full_key]

    def _get_in_memory_sync_lock(self, key: tuple[int | None, str]) -> threading.Lock:
        with self._thread_lock:
            if key not in self._sync_locks:
                self._sync_locks[key] = threading.Lock()
            return self._sync_locks[key]

    @asynccontextmanager
    async def session_lock(
        self,
        tenant_id: int | None,
        session_id: str,
        timeout: float = 5.0,
        blocking_timeout: float = 5.0,
        sleep: float = 0.05,
    ) -> AsyncGenerator[bool, None]:
        """Acquire an asynchronous Redis lock keyed by lock:session:{tenant_id}:{session_id}.

        If Redis is unavailable, cleanly falls back to an in-memory lock per (tenant_id, session_id).
        """
        lock_key = f"lock:session:{tenant_id}:{session_id}"
        client = await self.get_redis_client()

        if client is not None:
            lock = client.lock(
                lock_key,
                timeout=timeout,
                blocking_timeout=blocking_timeout,
                sleep=sleep,
            )
            acquired = False
            try:
                await lock.acquire()
                acquired = True
                yield True
            finally:
                if acquired:
                    with contextlib.suppress(Exception):
                        await lock.release()
        else:
            mem_lock = self._get_in_memory_async_lock((tenant_id, session_id))
            async with mem_lock:
                yield True

    @contextmanager
    def session_lock_sync(
        self,
        tenant_id: int | None,
        session_id: str,
        timeout: float = 5.0,
        blocking_timeout: float = 5.0,
        sleep: float = 0.05,
    ) -> Generator[bool, None, None]:
        """Synchronously acquire Redis distributed lock or thread lock fallback."""
        lock_key = f"lock:session:{tenant_id}:{session_id}"
        sync_client = self.get_sync_redis_client()

        if sync_client is not None:
            lock = sync_client.lock(
                lock_key,
                timeout=timeout,
                blocking_timeout=blocking_timeout,
                sleep=sleep,
            )
            acquired = False
            try:
                lock.acquire()
                acquired = True
                yield True
            finally:
                if acquired:
                    with contextlib.suppress(Exception):
                        lock.release()
        else:
            mem_lock = self._get_in_memory_sync_lock((tenant_id, session_id))
            with mem_lock:
                yield True

    # -------------------------------------------------------------------------
    # Redis Streams (Event Publishing, Backlog Replay & Real-time Consumption)
    # -------------------------------------------------------------------------

    def _next_in_memory_id(self) -> str:
        with self._thread_lock:
            self._seq += 1
            return f"{int(time.time() * 1000)}-{self._seq}"

    def _add_to_in_memory_stream(
        self,
        user_id: int | None,
        stream_id: str,
        event_type: str,
        payload_data: dict[str, Any],
    ) -> None:
        with self._thread_lock:
            stream = self._in_memory_streams[user_id]
            stream.append(
                (stream_id, {"event": event_type, "data": payload_data, "user_id": user_id})
            )
            if len(stream) > 5000:
                del stream[: len(stream) - 5000]

            # Also maintain broadcast stream
            if user_id is not None:
                b_stream = self._in_memory_streams[None]
                b_stream.append(
                    (stream_id, {"event": event_type, "data": payload_data, "user_id": user_id})
                )
                if len(b_stream) > 5000:
                    del b_stream[: len(b_stream) - 5000]

            # Notify waiting in-memory readers
            for ev in list(self._in_memory_stream_notifiers.get(user_id, set())):
                ev.set()
            for ev in list(self._in_memory_stream_notifiers.get(None, set())):
                ev.set()

    async def xadd(
        self,
        event_type: str,
        data: dict[str, Any],
        user_id: int | None = None,
        maxlen: int = 5000,
    ) -> str:
        """Append an event to the tenant's Redis Stream: watcher:events:{tenant_id} MAXLEN ~ 5000."""
        payload_data = dict(data)
        if user_id is None:
            user_id = payload_data.get("user_id")
        if user_id is None:
            user_id = current_user_id.get()
        if user_id is None:
            try:
                user_id = get_current_user_id()
            except Exception:
                user_id = None

        if user_id is not None:
            payload_data["user_id"] = user_id

        stream_key = get_stream_key(user_id)
        data_json = json.dumps(_to_jsonable(payload_data))
        client = await self.get_redis_client()
        stream_id: str | None = None

        if client is not None:
            try:
                raw_sid = await client.xadd(
                    stream_key,
                    {
                        "event": event_type,
                        "data": data_json,
                        "user_id": str(user_id or ""),
                    },
                    maxlen=maxlen,
                    approximate=True,
                )
                stream_id = raw_sid.decode("utf-8") if isinstance(raw_sid, bytes) else str(raw_sid)
            except Exception as e:
                logger.warning("Failed appending to Redis Stream %s: %s", stream_key, e)

        if not stream_id:
            stream_id = self._next_in_memory_id()

        # Update in-memory log & subscribers
        self._add_to_in_memory_stream(user_id, stream_id, event_type, payload_data)
        self._deliver_in_memory({"event": event_type, "data": payload_data}, user_id)

        # Backwards compatibility Pub/Sub broadcast
        if client is not None:
            channel = get_tenant_channel(user_id)
            with contextlib.suppress(Exception):
                await client.publish(
                    channel, json.dumps({"event": event_type, "data": _to_jsonable(payload_data)})
                )

        return stream_id

    def xadd_sync(
        self,
        event_type: str,
        data: dict[str, Any],
        user_id: int | None = None,
        maxlen: int = 5000,
    ) -> str:
        """Synchronously append an event to Redis Stream or schedule on running loop."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # In running loop: schedule task and return generated stream ID
                stream_id = self._next_in_memory_id()
                loop.create_task(self.xadd(event_type, data, user_id=user_id, maxlen=maxlen))
                return stream_id
        except RuntimeError:
            pass

        payload_data = dict(data)
        if user_id is None:
            user_id = payload_data.get("user_id") or current_user_id.get()
        if user_id is not None:
            payload_data["user_id"] = user_id

        stream_key = get_stream_key(user_id)
        data_json = json.dumps(_to_jsonable(payload_data))
        sync_client = self.get_sync_redis_client()
        stream_id: str | None = None

        if sync_client is not None:
            try:
                stream_id = sync_client.xadd(
                    stream_key,
                    {
                        "event": event_type,
                        "data": data_json,
                        "user_id": str(user_id or ""),
                    },
                    maxlen=maxlen,
                    approximate=True,
                )
            except Exception as exc:
                logger.debug("Sync xadd failed: %s", exc)

        if not stream_id:
            stream_id = self._next_in_memory_id()

        self._add_to_in_memory_stream(user_id, stream_id, event_type, payload_data)
        self._deliver_in_memory({"event": event_type, "data": payload_data}, user_id)
        return stream_id

    async def xrange(
        self,
        user_id: int | None,
        last_event_id: str,
        count: int = 100,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Query missed historical events using XRANGE watcher:events:{tenant_id} (<last_event_id> + COUNT 100."""
        stream_key = get_stream_key(user_id)
        client = await self.get_redis_client()

        if client is not None:
            try:
                try:
                    # Redis 6.2+ exclusive min syntax '('
                    raw_items = cast(
                        Any,
                        await client.xrange(
                            stream_key, min=f"({last_event_id}", max="+", count=count
                        ),
                    )
                except Exception:
                    # Fallback if exclusive '(' syntax is not supported
                    raw_items = cast(
                        Any,
                        await client.xrange(stream_key, min=last_event_id, max="+", count=count),
                    )
                    raw_items = [item for item in raw_items if item[0] != last_event_id]

                results: list[tuple[str, dict[str, Any]]] = []
                for msg_id, fields in raw_items:
                    msg_id_str = (
                        msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                    )
                    data_val = fields.get("data", "{}")
                    parsed_data = json.loads(data_val) if isinstance(data_val, str) else data_val
                    results.append(
                        (
                            msg_id_str,
                            {
                                "event": fields.get("event", "message"),
                                "data": parsed_data,
                                "user_id": user_id,
                            },
                        )
                    )
                return results
            except Exception as exc:
                logger.warning("Error executing XRANGE on %s: %s", stream_key, exc)

        # In-memory stream fallback
        with self._thread_lock:
            stream = self._in_memory_streams.get(user_id, [])
            results = []
            for msg_id, item in stream:
                if msg_id > last_event_id:
                    results.append((msg_id, item))
                    if len(results) >= count:
                        break
            return results

    async def xread(
        self,
        user_id: int | None,
        last_id: str,
        count: int = 50,
        block_ms: int = 15000,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Read new events from stream using blocking XREAD. Returns [] on timeout."""
        stream_key = get_stream_key(user_id)
        client = await self.get_redis_client()

        if client is not None:
            try:
                read_res = cast(
                    Any, await client.xread({stream_key: last_id}, count=count, block=block_ms)
                )
                if not read_res:
                    return []

                results: list[tuple[str, dict[str, Any]]] = []
                for _, entries in read_res:
                    for msg_id, fields in entries:
                        msg_id_str = (
                            msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                        )
                        data_val = fields.get("data", "{}")
                        parsed_data = (
                            json.loads(data_val) if isinstance(data_val, str) else data_val
                        )
                        results.append(
                            (
                                msg_id_str,
                                {
                                    "event": fields.get("event", "message"),
                                    "data": parsed_data,
                                    "user_id": user_id,
                                },
                            )
                        )
                return results
            except Exception as exc:
                logger.warning("Error executing XREAD on %s: %s", stream_key, exc)

        # In-memory fallback
        ev = asyncio.Event()
        with self._thread_lock:
            self._in_memory_stream_notifiers[user_id].add(ev)

        try:
            # Check if events already arrived
            with self._thread_lock:
                stream = self._in_memory_streams.get(user_id, [])
                missed = [] if last_id == "$" else [item for item in stream if item[0] > last_id]

            if missed:
                return missed[:count]

            # Wait for new event or timeout
            try:
                await asyncio.wait_for(ev.wait(), timeout=block_ms / 1000.0)
            except TimeoutError:
                return []

            with self._thread_lock:
                stream = self._in_memory_streams.get(user_id, [])
                if last_id == "$":
                    return stream[-count:] if stream else []
                return [item for item in stream if item[0] > last_id][:count]
        finally:
            with self._thread_lock:
                self._in_memory_stream_notifiers[user_id].discard(ev)

    async def get_latest_stream_id(self, user_id: int | None) -> str:
        """Get the current latest stream ID for the tenant, or '0-0' if empty."""
        stream_key = get_stream_key(user_id)
        client = await self.get_redis_client()
        if client is not None:
            try:
                latest = await client.xrevrange(stream_key, max="+", min="-", count=1)
                if latest:
                    raw_latest = latest[0][0]
                    return (
                        raw_latest.decode("utf-8")
                        if isinstance(raw_latest, bytes)
                        else str(raw_latest)
                    )
                return "0-0"
            except Exception as exc:
                logger.debug("Error querying latest stream id on %s: %s", stream_key, exc)

        with self._thread_lock:
            stream = self._in_memory_streams.get(user_id, [])
            return stream[-1][0] if stream else "0-0"

    # -------------------------------------------------------------------------
    # Backward Compatibility (Pub/Sub & In-Memory Queues)
    # -------------------------------------------------------------------------

    def subscribe_in_memory(self, user_id: int | None) -> asyncio.Queue[dict[str, Any]]:
        """Register an in-memory queue for a tenant."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._in_memory_subscribers[user_id].add(q)
        return q

    def unsubscribe_in_memory(self, user_id: int | None, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unregister an in-memory queue."""
        self._in_memory_subscribers[user_id].discard(q)
        if not self._in_memory_subscribers[user_id]:
            self._in_memory_subscribers.pop(user_id, None)

    def _deliver_in_memory(self, payload: dict[str, Any], user_id: int | None) -> None:
        """Deliver event to in-memory queues."""
        targets: set[asyncio.Queue[dict[str, Any]]] = set()
        if user_id is not None:
            targets.update(self._in_memory_subscribers.get(user_id, set()))
        targets.update(self._in_memory_subscribers.get(None, set()))

        try:
            from server.app import watcher_sse_subscribers

            targets.update(watcher_sse_subscribers)
        except Exception:
            pass

        dead: set[asyncio.Queue[dict[str, Any]]] = set()
        for q in targets:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.add(q)
        for dq in dead:
            self._in_memory_subscribers[user_id].discard(dq)
            self._in_memory_subscribers[None].discard(dq)
            try:
                from server.app import watcher_sse_subscribers

                watcher_sse_subscribers.discard(dq)
            except Exception:
                pass

    async def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        user_id: int | None = None,
    ) -> None:
        """Publish event to both Redis Stream and Pub/Sub channel."""
        await self.xadd(event_type, data, user_id=user_id)

    def publish_sync(
        self,
        event_type: str,
        data: dict[str, Any],
        user_id: int | None = None,
    ) -> None:
        """Synchronously publish an event."""
        self.xadd_sync(event_type, data, user_id=user_id)

    async def create_redis_subscriber(self, user_id: int | None) -> Any | None:
        """Subscribe to Redis channels for the tenant using pubsub."""
        client = await self.get_redis_client()
        if client is None:
            return None

        tenant_ch = get_tenant_channel(user_id)
        pubsub = client.pubsub()
        if user_id is not None:
            await pubsub.subscribe(tenant_ch, BROADCAST_CHANNEL)
        else:
            await pubsub.subscribe(BROADCAST_CHANNEL)
        return pubsub

    async def close(self) -> None:
        """Close Redis client and connection pool."""
        if self._redis_client is not None:
            with contextlib.suppress(Exception):
                await self._redis_client.aclose()
            self._redis_client = None

        if self._pool is not None:
            with contextlib.suppress(Exception):
                await self._pool.aclose()
            self._pool = None

        with self._thread_lock:
            if self._sync_redis_client is not None:
                with contextlib.suppress(Exception):
                    self._sync_redis_client.close()
                self._sync_redis_client = None

        self._redis_available = None


# Global event bus singleton
_GLOBAL_EVENT_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    global _GLOBAL_EVENT_BUS
    if _GLOBAL_EVENT_BUS is None:
        _GLOBAL_EVENT_BUS = EventBus()
    return _GLOBAL_EVENT_BUS


async def get_redis_dependency(request: Request) -> aioredis.Redis | None:
    """FastAPI dependency for injecting async Redis client."""
    return await get_event_bus().get_redis_client()
