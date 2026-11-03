"""Background processor.

Postgres is the queue: a webhook only inserts a row, and workers lease pending rows
with FOR UPDATE SKIP LOCKED. Anything still PROCESSING after a crash or redeploy is
picked up again once its lease expires, so no acknowledged transaction is lost.
"""

import asyncio
import logging

import asyncpg

from app import config, db

log = logging.getLogger("worker")


class Worker:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.wakeup = asyncio.Event()
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        for i in range(config.WORKER_CONCURRENCY):
            self.tasks.append(asyncio.create_task(self._run(i), name=f"worker-{i}"))
        log.info("started %d workers", len(self.tasks))

    def notify(self) -> None:
        self.wakeup.set()

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, n: int) -> None:
        while True:
            # Clear *before* claiming: a notify() that lands during the claim query
            # stays set, so the wait below returns at once and we look again.
            # (Clearing after an empty claim could wipe that signal and strand the job.)
            self.wakeup.clear()
            try:
                job = await db.claim_next(self.pool, config.LEASE_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker-%d: claim failed, backing off", n)
                await asyncio.sleep(2)
                continue

            if job is None:
                # Only worker 0 polls (to catch expired leases / rows left by a crash);
                # the rest sleep until woken, keeping idle DB traffic near zero.
                timeout = config.POLL_INTERVAL_SECONDS if n == 0 else None
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
                except TimeoutError:
                    pass
                continue

            # Other idle workers may also have work waiting.
            self.wakeup.set()
            await self._process(job["transaction_id"], job["attempts"])

    async def _process(self, transaction_id: str, attempt: int) -> None:
        log.info("processing %s (attempt %d)", transaction_id, attempt)
        try:
            # Simulated external API call.
            await asyncio.sleep(config.PROCESSING_DELAY_SECONDS)
            await db.mark_processed(self.pool, transaction_id)
            log.info("processed %s", transaction_id)
        except asyncio.CancelledError:
            # Shutting down: hand the job back so the next instance picks it up immediately.
            try:
                await asyncio.shield(db.release(self.pool, transaction_id))
            except Exception:
                pass
            raise
        except Exception:
            # Lease will expire and the job is retried.
            log.exception("failed processing %s; will retry after lease expiry", transaction_id)
