"""
Day 4 stress test. Run this against a live server (uvicorn already
running) and read the output honestly -- whatever breaks here is more
instructive than a scripted decline-card scenario, per the roadmap.
"""

import concurrent.futures
import json
import time

import httpx

BASE = "http://localhost:8000"


def test_double_submit_same_idempotency_key():
    """Fire two IDENTICAL create_order requests concurrently. Exactly one
    should be 'allowed' and the other 'replayed_idempotent' -- if both come
    back 'allowed', the gate has a race condition."""
    key = f"stress-double-{time.time()}"
    body = {
        "items": [{"sku": "P001", "qty": 1}],
        "idempotency_key": key,
        "reasoning": "stress test: concurrent double submit",
    }

    def fire():
        with httpx.Client() as c:
            return c.post(f"{BASE}/orders/create", headers={"X-Caller": "human"}, json=body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(fire) for _ in range(2)]
        results = [f.result().json() for f in futures]

    print("\n=== double submit, same idempotency_key ===")
    for r in results:
        print(json.dumps(r, indent=2))

    outcomes = sorted(r["result"] for r in results)
    if outcomes == ["allowed", "replayed_idempotent"]:
        print(">>> PASS: exactly one allowed, one replayed.")
    else:
        print(f">>> FAIL / INTERESTING: got {outcomes} -- investigate.")


def test_malformed_catalog_query():
    """Send garbage query params to the agent-readable catalog endpoint."""
    print("\n=== malformed catalog query ===")
    with httpx.Client() as c:
        r = c.get(f"{BASE}/catalog/agent", params={"max_price": "not-a-number"})
        print(f"status: {r.status_code}")
        print(r.text[:500])


def test_unknown_sku():
    print("\n=== order with unknown SKU ===")
    with httpx.Client() as c:
        r = c.post(
            f"{BASE}/orders/create",
            headers={"X-Caller": "human"},
            json={
                "items": [{"sku": "DOES_NOT_EXIST", "qty": 1}],
                "idempotency_key": f"stress-badsku-{time.time()}",
                "reasoning": "stress test: unknown sku",
            },
        )
        print(f"status: {r.status_code}")
        print(r.text[:500])


def test_zero_and_negative_qty():
    print("\n=== zero / negative quantity ===")
    with httpx.Client() as c:
        for qty in [0, -1]:
            r = c.post(
                f"{BASE}/orders/create",
                headers={"X-Caller": "human"},
                json={
                    "items": [{"sku": "P001", "qty": qty}],
                    "idempotency_key": f"stress-qty{qty}-{time.time()}",
                    "reasoning": f"stress test: qty={qty}",
                },
            )
            print(f"qty={qty} -> status {r.status_code}: {r.text[:300]}")


def test_unknown_caller_header():
    print("\n=== unknown X-Caller header ===")
    with httpx.Client() as c:
        r = c.post(
            f"{BASE}/orders/create",
            headers={"X-Caller": "definitely_not_a_real_caller"},
            json={
                "items": [{"sku": "P001", "qty": 1}],
                "idempotency_key": f"stress-badcaller-{time.time()}",
                "reasoning": "stress test: bad caller",
            },
        )
        print(f"status: {r.status_code}")
        print(r.text[:500])


if __name__ == "__main__":
    test_double_submit_same_idempotency_key()
    test_malformed_catalog_query()
    test_unknown_sku()
    test_zero_and_negative_qty()
    test_unknown_caller_header()