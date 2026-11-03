# Plan: Transaction Webhook Service (Backend Assessment)

## Context
The assessment asks for a Python
service that accepts payment-processor webhooks, acknowledges them in under 500ms with 202, processes
each transaction in the background (30s simulated delay), stores results persistently, and guarantees
idempotency on `transaction_id`. Deliverables: public GitHub repo with README + a live public URL.
The folder is empty apart from the PDF — this is a greenfield build in `D:\New folder`.

Graders will test: single transaction (~30s to PROCESSED), duplicate prevention, fast responses under
load, and reliability (no lost transactions). Reliability is the deciding factor, so the design uses the
database as the source of truth for pending work instead of in-memory tasks.

## Stack
- **FastAPI + Uvicorn** (async, automatic Pydantic validation, `/docs` for reviewers)
- **PostgreSQL on Neon** (free cloud Postgres), accessed via **asyncpg** (no ORM needed for one table)
- **In-process asyncio worker** backed by the DB (no Redis/Celery — fewer moving parts)
- **Render** web service via Dockerfile (single instance); keep-warm ping to avoid free-tier cold starts
- **pytest + httpx** for tests

## Project layout
```
D:\New folder\
  app/
    __init__.py
    main.py        # FastAPI app, routes, lifespan (pool + worker startup/shutdown)
    config.py      # settings from env: DATABASE_URL, PROCESSING_DELAY_SECONDS (default 30), WORKER_CONCURRENCY
    db.py          # asyncpg pool, schema creation, queries (insert_if_new, get, claim, mark_processed)
    models.py      # Pydantic request/response models
    worker.py      # background processing loop + startup recovery
  tests/
    conftest.py
    test_api.py    # health, webhook 202, validation 422, idempotency, 404
  scripts/
    load_test.py   # concurrent duplicates + timing check against a live URL
  Dockerfile
  requirements.txt
  .env.example
  render.yaml
  README.md
```

## Data model
```sql
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id      TEXT PRIMARY KEY,
  source_account      TEXT NOT NULL,
  destination_account TEXT NOT NULL,
  amount              NUMERIC(18,2) NOT NULL,
  currency            CHAR(3) NOT NULL,
  status              TEXT NOT NULL DEFAULT 'PROCESSING',  -- PROCESSING | PROCESSED
  attempts            INT  NOT NULL DEFAULT 0,
  locked_until        TIMESTAMPTZ,                          -- lease for crash recovery
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_txn_pending ON transactions (status) WHERE status = 'PROCESSING';
```
Schema is created on startup (`CREATE ... IF NOT EXISTS`) — no migration tool needed.

## Endpoints
1. **`GET /`** → `{"status": "HEALTHY", "current_time": "<UTC ISO-8601 with Z>"}`; 200.
2. **`POST /v1/webhooks/transactions`**
   - Validate body (Pydantic: non-empty strings, `amount > 0` as Decimal, 3-letter currency, uppercased). Invalid → 422.
   - `INSERT ... ON CONFLICT (transaction_id) DO NOTHING RETURNING transaction_id` — one fast DB round trip.
   - If inserted → notify the worker (`asyncio.Event` / queue put) so it starts immediately.
   - Always return **202** `{"message": "accepted", "transaction_id": ...}` (duplicates also 202 — graceful).
   - Row is committed *before* the 202 is sent, so an acknowledged webhook is never lost.
3. **`GET /v1/transactions/{transaction_id}`** → list with one object (matches spec's `[{...}]` shape):
   all fields + `status`, `created_at`, `processed_at` (null while processing); 404 if unknown.

## Background processing (`app/worker.py`)
- On startup, launch N worker coroutines (default 50 — each mostly sleeps, so cheap).
- Each worker loops: **claim** one due row atomically:
  ```sql
  UPDATE transactions SET locked_until = now() + interval '2 minutes', attempts = attempts + 1
  WHERE transaction_id = (
    SELECT transaction_id FROM transactions
    WHERE status = 'PROCESSING' AND (locked_until IS NULL OR locked_until < now())
    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
  RETURNING *;
  ```
  then `await asyncio.sleep(PROCESSING_DELAY_SECONDS)`, then
  `UPDATE ... SET status='PROCESSED', processed_at=now() WHERE transaction_id=$1 AND status='PROCESSING'`.
- If nothing to claim, wait on the wake-up event (with a ~5s timeout as a polling fallback).
- **Recovery:** rows left `PROCESSING` after a crash/redeploy become claimable once their lease expires
  (or immediately if never claimed) — picked up automatically on next start. No transaction lost.
- Errors in one job are logged and the lease simply expires → retried; worker loop never dies (try/except around each iteration).
- Graceful shutdown: cancel workers in lifespan; their leased rows are retried after lease expiry.
- `SKIP LOCKED` + conditional final update ⇒ exactly one processing outcome even with multiple instances.

## Performance (500ms requirement)
- Webhook handler does one indexed insert on a pooled connection — typically <50ms.
- `asyncio.sleep` never blocks the event loop; workers use a separate slice of the pool.
- Pool sized (e.g. min 2 / max 10) so webhook requests aren't starved; workers hold connections only during short queries, not during the sleep.

## Deployment
- `Dockerfile` (python:3.12-slim, `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
- Neon database → `DATABASE_URL` env var on Render (with `sslmode=require`).
- Keep-warm: free cron (e.g. cron-job.org / UptimeRobot) hitting `GET /` every 5–10 min, documented in README — avoids cold starts breaking the 500ms test.
- Push to public GitHub repo; Render auto-deploys from it.

## README contents
- Live URL + endpoint list with curl examples
- Local run (`pip install -r requirements.txt`, `.env`, `uvicorn app.main:app --reload`) and Docker run
- How to test (pytest, `scripts/load_test.py`)
- Technical choices: FastAPI, Postgres-as-queue vs Celery/Redis, idempotency via PK + ON CONFLICT, lease-based recovery, trade-offs and what I'd add at scale (signature verification, dedicated queue, metrics)

## Verification
1. `pytest` — health shape, 202 on webhook, 422 on bad payload, 404 on unknown id, duplicate posts → one row
   (tests use `PROCESSING_DELAY_SECONDS=1` against a test Postgres / Neon branch).
2. Local manual: POST one webhook → GET shows `PROCESSING`, `processed_at: null` → after ~30s `PROCESSED`.
3. `scripts/load_test.py <url>`: fire 20 concurrent duplicates + 100 distinct webhooks; assert all 202,
   p99 latency < 500ms, exactly one row per id, all become `PROCESSED`.
4. Reliability: post a webhook, kill the server within 30s, restart → it still reaches `PROCESSED`.
5. Repeat steps 2–3 against the deployed Render URL.
