import time
import uuid
from concurrent.futures import ThreadPoolExecutor


def payload(txn_id=None, **overrides):
    body = {
        "transaction_id": txn_id or f"txn_{uuid.uuid4().hex[:12]}",
        "source_account": "acc_user_789",
        "destination_account": "acc_merchant_456",
        "amount": 1500,
        "currency": "INR",
    }
    body.update(overrides)
    return body


def wait_for_status(client, txn_id, wanted, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/transactions/{txn_id}")
        if r.status_code == 200 and r.json()[0]["status"] == wanted:
            return r.json()[0]
        time.sleep(0.2)
    raise AssertionError(f"{txn_id} never reached {wanted}")


def test_health(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "HEALTHY"
    assert body["current_time"].endswith("Z")


def test_single_transaction_lifecycle(client):
    body = payload()
    r = client.post("/v1/webhooks/transactions", json=body)
    assert r.status_code == 202

    first = client.get(f"/v1/transactions/{body['transaction_id']}").json()
    assert isinstance(first, list) and len(first) == 1
    assert first[0]["status"] == "PROCESSING"
    assert first[0]["processed_at"] is None

    done = wait_for_status(client, body["transaction_id"], "PROCESSED")
    assert done["processed_at"] is not None
    assert done["amount"] == 1500
    assert done["currency"] == "INR"


def test_duplicates_processed_once(client):
    body = payload()
    with ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(lambda _: client.post("/v1/webhooks/transactions", json=body).status_code, range(10)))
    assert codes == [202] * 10

    done = wait_for_status(client, body["transaction_id"], "PROCESSED")
    processed_at = done["processed_at"]

    # Resending after processing must not reprocess.
    assert client.post("/v1/webhooks/transactions", json=body).status_code == 202
    time.sleep(1.5)
    again = client.get(f"/v1/transactions/{body['transaction_id']}").json()[0]
    assert again["processed_at"] == processed_at


def test_validation_errors(client):
    assert client.post("/v1/webhooks/transactions", json={"transaction_id": "x"}).status_code == 422
    assert client.post("/v1/webhooks/transactions", json=payload(amount=-5)).status_code == 422
    assert client.post("/v1/webhooks/transactions", json=payload(currency="RUPEES")).status_code == 422


def test_unknown_transaction_404(client):
    assert client.get("/v1/transactions/does_not_exist").status_code == 404


def test_webhook_is_fast(client):
    start = time.perf_counter()
    r = client.post("/v1/webhooks/transactions", json=payload())
    assert r.status_code == 202
    assert time.perf_counter() - start < 0.5
