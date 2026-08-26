# 🛒 Chatout

**Conversational checkout + agent-readable catalog** — built for the Razorpay AI Buildathon (Track 01: AI Growth & Agentic Commerce).

Chatout lets a merchant's catalog be shopped two ways: by a **human**, through natural chat, or by an **autonomous AI buyer agent**, given nothing but a goal. Both paths run through the exact same gated, capped, audited transaction layer — no LLM ever gets to approve, deny, or interpret a payment.

---

## Table of contents

- [What it solves](#what-it-solves)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Running it](#running-it)
- [Testing](#testing)
- [Failure & recovery](#failure--recovery)
- [Design decisions](#design-decisions)
- [Known limitations](#known-limitations)
- [Status](#status)

---

## What it solves

> "Grow the merchant's revenue, and make them sellable to AI buyers."

Chatout answers this with a single system that serves both a human shopper and an autonomous AI buyer through identical infrastructure:

- A **human** chats naturally ("show me running shoes under ₹3000"), builds a cart, and pays through a real Razorpay Checkout.js flow.
- An **AI buyer agent** is given a goal (e.g. "get running shoes under ₹3000") and transacts end-to-end with zero human confirmation.
- Every money-touching action — from either caller — is **explicit, capped, idempotent, and logged** before a rupee moves.

The differentiator isn't the chat interface. It's that the gate deciding whether a transaction happens **never runs through an LLM**, is stress-tested under real concurrency, and has survived an adversarial attempt to break it (see [Failure & recovery](#failure--recovery)).

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Agent LLM | Groq API, `openai/gpt-oss-120b` (OpenAI-compatible tool-calling) |
| Payments | Razorpay Orders API + Checkout.js (test mode) |
| Data | SQLite (audit log + idempotency), JSON (catalog) |
| Frontend | Single-page HTML/CSS/JS, no build step |
| Testing | `pytest` (unit), custom concurrent stress script |

---

## Architecture

Human (chat) → `human_agent` / `chat.py` router  ─┐
AI Buyer (goal) → `ai_buyer_agent.py` ────────────┼──▶ **TransactionGate** (`gate.py`) ──▶ Razorpay API (test mode)

The gate enforces, in plain deterministic Python:
- a spend cap (₹5,000), checked **per request** and **cumulatively** across all prior allowed orders for that caller
- idempotency (a retried request with the same key replays the original result)
- an action allow-list (`create_order` only)
- a full audit log of every request, including blocked ones — persisted to SQLite

**The core rule the whole system is built around:** the LLM can only *propose* an order by calling `create_order`. Everything after that — cap enforcement, duplicate detection, logging — is deterministic Python with zero LLM or network dependency.

---

## Project structure

**backend/**
- `.env` — not committed
- `requirements.txt`
- `chatout_ui.html` — web UI (open directly in browser)
- `checkout_test.html` — legacy manual payment test page
- `stress_day4.py` — deliberate concurrent/adversarial test script
- **app/**
  - `main.py` — FastAPI app, catalog + order endpoints
  - `catalog.py` — product store, human + agent-readable views
  - **data/**
    - `catalog.json`
    - `audit.db` — generated at runtime, not committed
  - **core/**
    - `gate.py` — the transaction gate; no LLM code touches this
    - `razorpay_client.py` — thin Razorpay SDK wrapper, test mode only
  - **routers/**
    - `chat.py` — HTTP session layer for the web UI
  - **agents/**
    - `tools.py` — shared tool schema + `CartSession`
    - `base_agent.py` — Groq tool-calling loop
    - `human_agent.py` — CLI human chat agent
    - `ai_buyer_agent.py` — autonomous buyer agent
- **tests/**

---

## Setup

**Requirements:** Python 3.10+ (uses `X | None` union syntax throughout).

```
cd backend
py -m pip install -r requirements.txt
```

Create `backend/.env`:
```
GROQ_API_KEY=your_key_from_console.groq.com
GROQ_MODEL=openai/gpt-oss-120b
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=your_test_secret
```

- Groq key: sign up free at console.groq.com → API Keys → Create.
- Razorpay test keys: dashboard.razorpay.com, **Test Mode** toggled on → Settings → API Keys → Generate Test Key.

---

## Running it

**1. Start the backend:**
```
cd backend
py -m uvicorn app.main:app --reload --port 8000
```

**2. Open the UI:**
Open `backend/chatout_ui.html` **directly in a browser** (double-click the file).

> ⚠️ Don't serve this through a file-watching dev server like VS Code Live Server — it watches the whole workspace, including the SQLite audit database, and will force-reload the page (losing chat state) every time an order is placed. This bit us once; see [Failure & recovery](#failure--recovery).

**3. Try both paths:**
- **Shop tab** — chat naturally, build a cart, confirm, and complete a real test-mode payment (Checkout.js modal opens automatically).
- **AI Buyer tab** — give it a goal and watch it search, decide, and transact with no human step.
- **Audit ledger** — updates live under both tabs, showing every request (allowed, blocked, or replayed) from both callers, plus a running cumulative-spend gauge per caller.

**Test card** (Checkout.js modal): `5104 0155 5555 5558`, any future expiry, any CVV. Test mode never moves real money regardless of card used.

---

## Testing

**Unit tests:**
```
cd backend
pytest tests/ -v
```

**Deliberate stress/adversarial test** (server must already be running):
```
cd backend
py stress_day4.py
```
Covers: concurrent double-submission of the same order, malformed catalog queries, unknown SKUs, zero/negative quantities, and unknown caller types.

**Manual audit check:**
```
Invoke-RestMethod http://localhost:8000/audit
```

---

## Failure & recovery

Real bugs found while building and adversarially testing this system — not staged scenarios.

| # | What broke | Root cause | Fix | How it was verified |
|---|---|---|---|---|
| ⭐ | **The spend cap could be bypassed by splitting a purchase.** Explicitly instructed the AI buyer agent to try to defeat the cap ("split into multiple smaller orders"), and it worked — two orders of ₹4,998 and ₹2,499, each individually under the ₹5,000 cap, together totaled ₹7,497, and both were approved with real Razorpay orders created. | The gate checked each `create_order` request in isolation; nothing tracked a caller's cumulative approved spend across multiple orders. | Added a cumulative-allowed-spend check per caller in `gate.py`, alongside the existing per-request check. | Re-ran the exact scenario: first order (₹2,499) still allowed, second (₹4,998) now correctly blocked with an explicit reasoning message stating that splitting does not bypass the cap. |
| 2 | **Agents claimed "purchase successful" when the payment had actually failed.** A transient `ConnectionResetError` caused the Razorpay order-creation call to fail *after* the gate had already approved the transaction — but the agent only checked `result: "allowed"`, not whether Razorpay itself had succeeded, so it reported success anyway. | Gate approval and payment execution were conflated in the agents' success logic, even though `/orders/create` already separated them (`result` vs. an optional `razorpay_error` field). | Both agents' system prompts now explicitly require checking for the *absence* of `razorpay_error` and *presence* of `razorpay_order_id` before claiming success; `/orders/create` also retries once on transient failure. | Re-ran the buyer agent multiple times; its final response now visibly reasons through both checks before claiming success. |
| 3 | **The AI buyer agent's language implied a completed financial transaction when only an order record existed.** Even after fix #2, responses said things like "the shoes have been purchased" — technically defensible, but misleading, since a Razorpay *order* is just an expected-payment record, not a captured charge. There's no card-entry step for a non-human caller. | System prompt didn't distinguish "order created" from "payment captured." | System prompt now requires the agent to state explicitly that the order was created and is awaiting payment, and that no capture step exists for an autonomous buyer. | Verified in the buyer agent's output on the next run. |
| 4 | **The audit log didn't survive a server restart.** The original in-memory list was wiped on any `uvicorn --reload` restart — including one triggered accidentally by an unrelated file save. | Audit trail was held only in a Python list in process memory, never persisted. | Rewrote `gate.py` to persist every entry (and idempotency keys) to SQLite (`app/data/audit.db`). | Placed an order, killed and restarted the server entirely, confirmed the entry was still present in `/audit`. |
| 5 | **Quantity wasn't validated before reaching the gate.** A `create_order` request with `qty=0` or `qty=-1` passed the gate cleanly (cap/idempotency checks don't care about sign) and was only rejected because Razorpay's own API validation caught the resulting ₹0 / negative-amount order. | No sanity check on `qty` anywhere in our own code. | Added an explicit `qty > 0` check in `main.py`, before the gate is even consulted. | `stress_day4.py` confirms a clean `422` from our own code for both `qty=0` and `qty=-1`. |
| 6 | **A local dev-tooling collision wiped chat state on every successful order.** Serving the UI through VS Code Live Server, which watches the whole workspace, meant every SQLite write to `audit.db` was mistaken for a code change and triggered a full page reload — killing in-progress chat sessions and interrupting the Checkout.js flow. | Not an app bug — Live Server's file watcher scope included the runtime data directory. | Documented as an environment gotcha: open the UI file directly, don't serve it through a file-watching dev server. | Reproduced consistently, then confirmed resolved once served without Live Server. |

Also confirmed (not a bug, but load-bearing evidence): **idempotency holds under real concurrency**, not just sequential unit tests — firing two identical `create_order` requests at once via a thread pool produced exactly one `allowed` and one `replayed_idempotent`, with no double-charge and no race in the SQLite write path (guarded by an explicit lock in `gate.py`).

---

## Design decisions

- **The LLM never sees `gate.py`.** It can only propose an order via the `create_order` tool. Cap enforcement, idempotency, and the action allow-list are deterministic Python with zero LLM or network dependency — unit-tested in isolation, stress-tested under concurrency, and adversarially tested against an agent actively trying to defeat them.
- **No refund/cancellation/repeat-charge code path exists at all** — not a disabled one, no code path. This is the answer to "where you chose not to use AI": the gate doesn't reason about whether a refund is appropriate, because refunds aren't an action the system knows how to perform.
- **AI-buyer "confirmation" = pre-authorized intent + cap enforcement, not payment capture.** There's no human-in-the-loop step for the buyer agent. Its authority is bounded entirely by the same server-side gate the human path uses. A gate-approved Razorpay *order* is the completion event for this caller — no *payment* is ever captured, since that requires a human to enter card details.

---

## Known limitations

- **The cumulative spend cap is scoped per caller-type (`human` / `ai_buyer`), not per individual session.** For the AI buyer, "caller type" and "the buyer" are effectively the same thing, so this is correct as-is. For the human path, it means the cap is "total spend through the human channel, ever," not "spend per customer" — a real multi-customer storefront would need a per-session or per-customer identifier threaded through the gate. Documented here as a deliberate scoping decision made under buildathon time constraints.
- **Idempotency keys are derived from `caller + cart contents`, with no time component.** Retrying the exact same cart as the same caller always replays the original result rather than creating a new order — intentional (prevents accidental double-submission), but means re-testing the same scenario during development requires varying quantity/items, or clearing `app/data/audit.db` between runs.

---

## Status

| Day | Scope | Status |
|---|---|---|
| 1 | Catalog, transaction gate (cap/idempotency/audit), `/orders/create` wired and smoke-tested | ✅ |
| 2 | Human + AI buyer agents (Groq tool-calling), verified end to end incl. cap rejection | ✅ |
| 3 | Razorpay test-mode Orders API + Checkout.js, human payment verified end to end | ✅ |
| 4 | Audit log persisted to SQLite; deliberate stress test; qty validation fix | ✅ |
| 5 | Web chat UI (Shop + AI Buyer tabs), live audit ledger with spend gauges; adversarial cap-bypass found and fixed | ✅ |
| 6 | Pitch video, application submission | ⬜ |

---

*Built solo for the Razorpay AI Buildathon, Track 01.*