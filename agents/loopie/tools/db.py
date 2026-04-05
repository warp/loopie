"""Async connection pool for Postgres / AlloyDB via DATABASE_URL."""

from __future__ import annotations

import logging
import os

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
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=int(os.environ.get("DB_POOL_MAX", "5")))
    logger.info("Database pool created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
