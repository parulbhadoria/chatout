import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.gate import TransactionGate, Caller, GateResult


def make_gate(cap=5000):
    return TransactionGate(spend_cap=cap)


def test_allows_order_under_cap():
    gate = make_gate()
    entry = gate.request(
        caller=Caller.HUMAN,
        action="create_order",
        idempotency_key="k1",
        amount=2499,
        items=[{"id": "P001", "qty": 1}],
        reasoning="User asked for running shoes under 3000.",
    )
    assert entry.result == GateResult.ALLOWED.value
    assert len(gate.audit_log()) == 1


def test_blocks_order_over_cap():
    gate = make_gate(cap=5000)
    entry = gate.request(
        caller=Caller.AI_BUYER,
        action="create_order",
        idempotency_key="k2",
        amount=6000,
        items=[{"id": "P006", "qty": 1}],
        reasoning="Buyer agent wanted the dumbbell pair plus extras.",
    )
    assert entry.result == GateResult.BLOCKED_CAP.value


def test_cap_boundary_is_inclusive():
    # amount == cap should be allowed; only strictly over the cap is blocked
    gate = make_gate(cap=5000)
    entry = gate.request(
        caller=Caller.HUMAN,
        action="create_order",
        idempotency_key="k_boundary",
        amount=5000,
        items=[],
        reasoning="Exactly at cap.",
    )
    assert entry.result == GateResult.ALLOWED.value


def test_idempotent_replay_does_not_double_charge():
    gate = make_gate()
    first = gate.request(
        caller=Caller.HUMAN,
        action="create_order",
        idempotency_key="dupe-key",
        amount=1899,
        items=[{"id": "P002", "qty": 1}],
        reasoning="First attempt.",
    )
    # simulate a retried tool call with the same idempotency key
    # (e.g. network blip causes the LLM/tool layer to re-issue the call)
    second = gate.request(
        caller=Caller.HUMAN,
        action="create_order",
        idempotency_key="dupe-key",
        amount=1899,
        items=[{"id": "P002", "qty": 1}],
        reasoning="Retried attempt after a timeout.",
    )
    assert first.result == GateResult.ALLOWED.value
    assert second.result == GateResult.REPLAYED.value
    # exactly ONE allowed order exists in the log for this key
    allowed_entries = [
        e for e in gate.audit_log()
        if e["idempotency_key"] == "dupe-key" and e["result"] == GateResult.ALLOWED.value
    ]
    assert len(allowed_entries) == 1


def test_blocked_action_has_no_bypass():
    gate = make_gate()
    entry = gate.request(
        caller=Caller.AI_BUYER,
        action="refund_order",  # not in PERMITTED_ACTIONS -- no such path exists
        idempotency_key="k3",
        amount=100,
        items=[],
        reasoning="Buyer agent tried to self-issue a refund.",
    )
    assert entry.result == GateResult.BLOCKED_ACTION.value


def test_every_request_is_logged_including_blocked_ones():
    gate = make_gate(cap=1000)
    gate.request(
        caller=Caller.AI_BUYER, action="create_order", idempotency_key="a",
        amount=500, items=[], reasoning="ok",
    )
    gate.request(
        caller=Caller.AI_BUYER, action="create_order", idempotency_key="b",
        amount=5000, items=[], reasoning="too much",
    )
    gate.request(
        caller=Caller.AI_BUYER, action="cancel_order", idempotency_key="c",
        amount=0, items=[], reasoning="not permitted",
    )
    log = gate.audit_log()
    assert len(log) == 3
    results = {e["result"] for e in log}
    assert GateResult.ALLOWED.value in results
    assert GateResult.BLOCKED_CAP.value in results
    assert GateResult.BLOCKED_ACTION.value in results


def test_two_different_callers_do_not_share_idempotency_collision_silently():
    # different callers using the SAME key is itself a suspicious case worth
    # being explicit about: gate treats it as a replay (safe default), but
    # this test documents that behaviour rather than leaving it implicit.
    gate = make_gate()
    gate.request(
        caller=Caller.HUMAN, action="create_order", idempotency_key="shared",
        amount=100, items=[], reasoning="human order",
    )
    second = gate.request(
        caller=Caller.AI_BUYER, action="create_order", idempotency_key="shared",
        amount=100, items=[], reasoning="ai buyer order, same key",
    )
    assert second.result == GateResult.REPLAYED.value
