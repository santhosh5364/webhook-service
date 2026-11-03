from pathlib import Path

import asyncpg

from app import config

SQL_DIR = Path(__file__).parent / "sql"


def _load(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


# Loaded once at import, so a missing file fails at startup rather than mid-request.
SCHEMA = _load("schema")
INSERT_TRANSACTION = _load("insert_transaction")
GET_TRANSACTION = _load("get_transaction")
CLAIM_NEXT = _load("claim_next")
MARK_PROCESSED = _load("mark_processed")
RELEASE = _load("release")


async def create_pool(min_size: int, max_size: int) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        config.DATABASE_URL,
        min_size=min_size,
        max_size=max_size,
        command_timeout=10,
        # Keeps pgbouncer-style poolers (e.g. Neon's pooled endpoint) happy.
        statement_cache_size=0,
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    return pool


async def insert_if_new(pool: asyncpg.Pool, txn) -> bool:
    """Insert the transaction. Returns False if the id already existed."""
    row = await pool.fetchrow(
        INSERT_TRANSACTION,
        txn.transaction_id,
        txn.source_account,
        txn.destination_account,
        txn.amount,
        txn.currency,
    )
    return row is not None


async def get_transaction(pool: asyncpg.Pool, transaction_id: str):
    return await pool.fetchrow(GET_TRANSACTION, transaction_id)


async def claim_next(pool: asyncpg.Pool, lease_seconds: int):
    """Atomically lease one pending transaction. Safe across many workers/instances."""
    return await pool.fetchrow(CLAIM_NEXT, float(lease_seconds))


async def mark_processed(pool: asyncpg.Pool, transaction_id: str) -> bool:
    result = await pool.execute(MARK_PROCESSED, transaction_id)
    return result.endswith(" 1")


async def release(pool: asyncpg.Pool, transaction_id: str) -> None:
    """Give a job back immediately (e.g. on shutdown) so it doesn't wait for lease expiry."""
    await pool.execute(RELEASE, transaction_id)
