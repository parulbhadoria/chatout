# Chatout

Conversational checkout + agent-readable catalog, built for the Razorpay AI
Buildathon (Track 01 — AI Growth & Agentic Commerce).

## What it solves

Lets a merchant's catalog be shopped by a human via chat **or** transacted
against by an autonomous AI buyer agent — with every money-touching action
explicit, capped, idempotent, and auditable, regardless of which kind of
caller initiated it.

## Architecture

- `backend/app/catalog.py` — single catalog store, exposed as two shapes:
  - `/catalog/browse` — human-facing (prose, price display, etc.)
  - `/catalog/agent` — agent-readable (typed, minimal-prose, versioned schema)
- `backend/app/core/gate.py` — the transaction gate. **No LLM code touches
  this module.** It enforces, in plain deterministic Python:
  - a hard spend cap (default ₹5,000) per transaction
  - idempotency (a retried request with the same key replays the original
    result instead of re-executing — this is what stops a network-retry or
    an LLM re-issuing a tool call from causing a double charge)
  - an action allow-list (`create_order` only — refunds, cancellations, and
    repeat charges have no code path here at all, gated or otherwise)
  - a full audit log of every request, including blocked ones
- `backend/app/main.py` — FastAPI app wiring the catalog endpoints and the
  gated `/orders/create` endpoint. An `X-Caller` header (`human` /
  `ai_buyer`) distinguishes the two calling agents, but both are routed
  through the exact same gate instance.

## Status (Day 1)

- [x] Catalog (15 products) + human + agent-readable endpoints
- [x] Transaction gate: spend cap, idempotency, audit log — unit tested in isolation
- [x] `/orders/create` wired through the gate, smoke-tested end to end
- [ ] Claude-powered human chat agent (Day 2)
- [ ] Claude-powered AI buyer agent, calling the same gated endpoints (Day 2)
- [ ] Razorpay test-mode Orders API + Checkout.js (Day 3)
- [ ] Frontend chat UI (Day 5)

## Running locally

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Run tests:

```bash
cd backend
pytest tests/ -v
```
