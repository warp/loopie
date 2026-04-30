"""Async connection pool for Postgres / AlloyDB via DATABASE_URL."""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    url = database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at AlloyDB (or local Postgres) "
            "after applying sql/migrations (001_init.sql, 002_notes_calendar_event.sql, …)."
        )
    max_size = int(os.environ.get("DB_POOL_MAX", "5"))
    connect_timeout_s = float(os.environ.get("DB_CONNECT_TIMEOUT_S", "10"))
    t0 = time.perf_counter_ns()
    try:
        _pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=max_size,
            timeout=connect_timeout_s,
        )
    except Exception as e:
        # Don't log credentials; only log host/port/db when present.
        parsed = urlparse(url)
        safe_target = parsed.hostname or "unknown-host"
        if parsed.port:
            safe_target = f"{safe_target}:{parsed.port}"
        if parsed.path and parsed.path != "/":
            safe_target = f"{safe_target}{parsed.path}"
        logger.warning(
            '{"event":"db_pool_create","ok":false,"target":%r,"error":%r}',
            safe_target,
            str(e),
        )
        raise
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    logger.info(
        '{"event":"db_pool_create","ok":true,"min_size":1,"max_size":%d,"elapsed_ms":%.3f}',
        max_size,
        elapsed_ms,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
