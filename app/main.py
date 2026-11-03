import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request, status

from app import config, db
from app.models import HealthResponse, TransactionOut, TransactionWebhook, WebhookAck
from app.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Separate pools so background jobs can never starve the webhook endpoint of connections.
    pool = await db.create_pool(config.DB_POOL_MIN, config.DB_POOL_MAX)
    worker_pool = await db.create_pool(1, config.WORKER_DB_POOL_MAX)
    worker = Worker(worker_pool)
    worker.start()
    app.state.pool = pool
    app.state.worker = worker
    try:
        yield
    finally:
        await worker.stop()
        await worker_pool.close()
        await pool.close()


app = FastAPI(
    title="Transaction Webhook Service",
    description="Accepts payment webhooks, acknowledges instantly, processes in the background.",
    version="1.0.0",
    lifespan=lifespan,
)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@app.get("/", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="HEALTHY", current_time=_utc_iso(datetime.now(UTC)))


@app.post(
    "/v1/webhooks/transactions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAck,
)
async def receive_webhook(payload: TransactionWebhook, request: Request) -> WebhookAck:
    # The row is committed before we reply, so an acknowledged webhook is never lost.
    created = await db.insert_if_new(request.app.state.pool, payload)
    if created:
        request.app.state.worker.notify()
    else:
        log.info("duplicate webhook ignored: %s", payload.transaction_id)
    return WebhookAck(transaction_id=payload.transaction_id)


@app.get("/v1/transactions/{transaction_id}", response_model=list[TransactionOut])
async def get_transaction(transaction_id: str, request: Request) -> list[TransactionOut]:
    row = await db.get_transaction(request.app.state.pool, transaction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return [
        TransactionOut(
            transaction_id=row["transaction_id"],
            source_account=row["source_account"],
            destination_account=row["destination_account"],
            amount=float(row["amount"]),
            currency=row["currency"],
            status=row["status"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )
    ]
