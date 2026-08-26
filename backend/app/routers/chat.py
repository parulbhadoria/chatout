"""
Day 5 chat API -- a thin HTTP session wrapper around the existing Agent +
tools, so a web frontend can drive the same human and AI-buyer agents the
CLI scripts use, without duplicating any tool-calling logic.

Session state (message history + cart) lives in memory only, keyed by a
session_id the frontend generates and sends back on every message. Unlike
the audit log, this does NOT need to survive a server restart -- chat
session state is UI convenience, not the money-relevant record.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.base_agent import Agent
from app.agents.tools import TOOLS_SCHEMA, CartSession
from app.agents.human_agent import SYSTEM_PROMPT as HUMAN_SYSTEM_PROMPT
from app.agents.ai_buyer_agent import SYSTEM_PROMPT as BUYER_SYSTEM_PROMPT

router = APIRouter()

# session_id -> (Agent, CartSession)
_human_sessions: dict[str, tuple[Agent, CartSession]] = {}


def _extract_pending_payment(events: list) -> dict | None:
    """If the most recent create_order call succeeded with a real Razorpay
    order (human caller only), surface exactly what the frontend needs to
    open Checkout.js. Returns None if the most recent create_order call
    was blocked, replayed, or hit a razorpay_error."""
    for event in reversed(events):
        if event["tool"] != "create_order":
            continue
        result = event["result"]
        if (
            result.get("result") == "allowed"
            and "razorpay_order_id" in result
            and "razorpay_error" not in result
        ):
            return {
                "order_id": result["razorpay_order_id"],
                "amount": result["razorpay_amount"],
                "currency": result["razorpay_currency"],
                "key_id": result["razorpay_key_id"],
            }
        return None
    return None


class StartSessionResponse(BaseModel):
    session_id: str


@router.post("/chat/human/start", response_model=StartSessionResponse)
def start_human_session():
    session_id = str(uuid.uuid4())
    cart_session = CartSession(caller="human")
    agent = Agent(HUMAN_SYSTEM_PROMPT, TOOLS_SCHEMA, cart_session.executor_map())
    _human_sessions[session_id] = (agent, cart_session)
    return StartSessionResponse(session_id=session_id)


class HumanMessageRequest(BaseModel):
    session_id: str
    message: str


@router.post("/chat/human/message")
def human_message(req: HumanMessageRequest):
    if req.session_id not in _human_sessions:
        # Auto-recover an unknown session (e.g. after a server restart)
        # instead of erroring out -- chat state is not the audit-critical
        # record, so silently starting fresh here is an acceptable choice.
        cart_session = CartSession(caller="human")
        agent = Agent(HUMAN_SYSTEM_PROMPT, TOOLS_SCHEMA, cart_session.executor_map())
        _human_sessions[req.session_id] = (agent, cart_session)

    agent, cart_session = _human_sessions[req.session_id]
    reply = agent.step(req.message)
    pending_payment = _extract_pending_payment(agent.last_events)

    return {
        "reply": reply,
        "events": agent.last_events,
        "cart": cart_session.cart,
        "pending_payment": pending_payment,
    }


class BuyerRunRequest(BaseModel):
    goal: str


@router.post("/chat/buyer/run")
def buyer_run(req: BuyerRunRequest):
    """The AI buyer path is single-shot by design (Day 2) -- no human
    confirmation step -- so each run gets a fresh agent and cart."""
    cart_session = CartSession(caller="ai_buyer")
    agent = Agent(BUYER_SYSTEM_PROMPT, TOOLS_SCHEMA, cart_session.executor_map())
    reply = agent.step(req.goal)
    return {
        "reply": reply,
        "events": agent.last_events,
    }