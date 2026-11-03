# Transaction Webhook Service

Receives transaction webhooks from payment processors, acknowledges them with **202 in well under
500ms**, and processes each transaction in the background (30s simulated external call) with
**idempotency** and **no lost transactions**, even across crashes and redeploys.

**Live API:** `https://<your-service>.onrender.com` &nbsp;·&nbsp; Interactive docs: `/docs`

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check → `{"status": "HEALTHY", "current_time": "..."}` |
| `POST` | `/v1/webhooks/transactions` | Accept a webhook → `202 {"message": "accepted", "transaction_id": "..."}` |
| `GET` | `/v1/transactions/{transaction_id}` | Status: `[{..., "status": "PROCESSING" \| "PROCESSED", "created_at", "processed_at"}]`, 404 if unknown |

Invalid payloads return `422` with field-level errors.

## Try it

```bash
BASE=https://<your-service>.onrender.com

curl $BASE/

curl -i -X POST $BASE/v1/webhooks/transactions \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"txn_abc123def456","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

curl $BASE/v1/transactions/txn_abc123def456   # PROCESSING, processed_at null
sleep 32
curl $BASE/v1/transactions/txn_abc123def456   # PROCESSED, processed_at set
```

Sending the same POST again (any number of times) still returns `202`, but the transaction is
stored and processed only once.

## Run locally

Requires Python 3.12+ and a PostgreSQL database (a free [Neon](https://neon.tech) database works).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # set DATABASE_URL
uvicorn app.main:app --reload
```

The table is created automatically on startup.

With Docker:

```bash
docker build -t webhook-service .
docker run -p 8000:8000 -e DATABASE_URL="postgresql://..." webhook-service
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | – | Postgres connection string |
| `PROCESSING_DELAY_SECONDS` | `30` | Simulated external API call duration |
| `WORKER_CONCURRENCY` | `50` | Transactions processed in parallel per instance |
| `LEASE_SECONDS` | `60` | After this, an unfinished claimed job is retried (must exceed the delay) |
| `POLL_INTERVAL_SECONDS` | `5` | How often the idle worker checks for recoverable jobs |

## Testing

**Automated tests** (needs a disposable Postgres; processing delay is shortened to 1s):

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/webhooks_test pytest
```

Covers: health response shape, the full PROCESSING → PROCESSED lifecycle, 10 concurrent
duplicate webhooks → one processing, resend-after-processing doesn't reprocess, validation
errors, 404, and webhook latency.

**End-to-end load test** against any running instance:

```bash
python scripts/load_test.py https://<your-service>.onrender.com --distinct 100 --dupes 20
```

Fires 20 concurrent copies of one webhook plus 100 distinct webhooks, reports median/p99 latency,
then waits until every transaction is `PROCESSED` and checks each has exactly one record.
Local result: 120 webhooks, all 202, p99 ≈ 315ms, all processed exactly once.

**Crash recovery** (verified manually): send a webhook, kill the server during the 30s wait,
restart it. The transaction resumes and reaches `PROCESSED`.

## Technical choices

**FastAPI + Uvicorn.** Async I/O means a 30-second background sleep doesn't hold a thread, and
Pydantic gives request validation and OpenAPI docs for free.

**PostgreSQL (Neon) is both the store and the queue.** The webhook handler does exactly one
thing: `INSERT ... ON CONFLICT (transaction_id) DO NOTHING`, then returns 202. Because the row is
committed *before* we acknowledge, an accepted webhook can't be lost, even if the process dies a
millisecond later. I chose this over FastAPI `BackgroundTasks` (in-memory, lost on restart) and
over Celery + Redis (durable but two more services to run and keep consistent with the database).
At this scale a Postgres-backed queue is the simplest thing that is reliable.

**Idempotency is enforced by the database, not application code.** `transaction_id` is the
primary key, so concurrent duplicate webhooks race on a unique constraint, and exactly one insert
wins. Duplicates get a normal 202, so the payment processor doesn't keep retrying. The final
`UPDATE ... WHERE status = 'PROCESSING'` guarantees a transaction only transitions once.

**Lease-based workers.** Background workers (asyncio tasks in the same process) claim work with
`UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` and set a `locked_until`
lease. If a worker crashes mid-job, the lease expires and another worker picks the job up. This
also means running several instances is safe: `SKIP LOCKED` ensures no two workers take the same
row. On graceful shutdown, in-flight jobs release their lease immediately.

**Fast acknowledgement.** The webhook path is a single indexed insert. Workers use a separate
connection pool, so background load can never starve the API of connections. A new webhook wakes
an idle worker immediately (no polling delay); only one worker polls periodically, to pick up
expired leases while keeping idle database traffic minimal.

### Trade-offs / what I'd add for production
- Verify webhook signatures (e.g. Razorpay's `X-Razorpay-Signature` HMAC).
- A `FAILED` status with max attempts and a dead-letter view for jobs that keep erroring.
- Run workers as a separate process/service so API and processing scale independently.
- Metrics (queue depth, processing lag, latency) and structured logs.
- Free-tier hosting sleeps when idle; an uptime pinger on `GET /` every 5–10 minutes keeps the
  service warm so the first request isn't a cold start.

## Project layout

```
app/
  main.py      FastAPI app, routes, startup/shutdown
  db.py        connection pools; runs the queries in app/sql/
  sql/         schema.sql and one .sql file per query
  worker.py    background processing and recovery
  models.py    request/response models and validation
  config.py    environment settings
tests/         pytest suite
scripts/       load_test.py
```
