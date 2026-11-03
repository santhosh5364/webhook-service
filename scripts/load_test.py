"""End-to-end check against a running service.

Usage: python scripts/load_test.py https://your-service.onrender.com [--distinct 100] [--dupes 20]

1. Sends N copies of one webhook at the same time (idempotency).
2. Sends M distinct webhooks at the same time (latency under load).
3. Polls until everything is PROCESSED and checks each id has exactly one record.
"""

import argparse
import asyncio
import statistics
import time
import uuid

import httpx


def body(txn_id):
    return {
        "transaction_id": txn_id,
        "source_account": "acc_user_789",
        "destination_account": "acc_merchant_456",
        "amount": 1500,
        "currency": "INR",
    }


async def post(client, txn_id, latencies):
    start = time.perf_counter()
    r = await client.post("/v1/webhooks/transactions", json=body(txn_id))
    latencies.append((time.perf_counter() - start) * 1000)
    return r.status_code


async def main(base_url, distinct, dupes, timeout):
    run = uuid.uuid4().hex[:8]
    dup_id = f"txn_dup_{run}"
    ids = [f"txn_load_{run}_{i}" for i in range(distinct)]
    latencies: list[float] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        await client.get("/")  # warm up

        codes = await asyncio.gather(*(post(client, dup_id, latencies) for _ in range(dupes)))
        codes += await asyncio.gather(*(post(client, i, latencies) for i in ids))
        assert all(c == 202 for c in codes), f"non-202 responses: {set(codes)}"

        latencies.sort()
        p99 = latencies[max(0, int(len(latencies) * 0.99) - 1)]
        print(f"{len(codes)} webhooks: median {statistics.median(latencies):.0f}ms, "
              f"p99 {p99:.0f}ms, max {latencies[-1]:.0f}ms")

        print("waiting for processing (~30s)...")
        pending = set(ids + [dup_id])
        deadline = time.time() + timeout
        while pending and time.time() < deadline:
            await asyncio.sleep(3)
            results = await asyncio.gather(*(client.get(f"/v1/transactions/{t}") for t in pending))
            for t, r in zip(list(pending), results):
                data = r.json()
                assert len(data) == 1, f"{t} has {len(data)} records"
                if data[0]["status"] == "PROCESSED":
                    pending.discard(t)
            print(f"  {len(pending)} still processing")

        if pending:
            raise SystemExit(f"FAIL: {len(pending)} not processed within {timeout}s")
        print(f"PASS: all {distinct + 1} transactions processed exactly once; "
              f"{dupes} duplicate webhooks collapsed into one")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--distinct", type=int, default=100)
    p.add_argument("--dupes", type=int, default=20)
    p.add_argument("--timeout", type=int, default=180)
    a = p.parse_args()
    asyncio.run(main(a.base_url.rstrip("/"), a.distinct, a.dupes, a.timeout))
