"""
Autonomous AI buyer agent. Given a natural-language goal, it searches the
agent-readable catalog, decides what to buy, and calls create_order -- with
zero human confirmation step. The only thing standing between this agent
and an unbounded purchase is the gate (backend/app/core/gate.py), which it
hits over the exact same /orders/create endpoint as the human agent.
"""

import sys

from app.agents.base_agent import Agent
from app.agents.tools import TOOLS_SCHEMA, CartSession

SYSTEM_PROMPT = """You are an autonomous purchasing agent acting on behalf of
a buyer with a specific goal. You have no human to confirm with -- you must
decide and act within the goal's constraints yourself. Search the catalog,
pick item(s) that satisfy the goal, add them to the cart, then call
create_order with a short reasoning string. If create_order is blocked
(e.g. over the spend cap), explain why in your final answer instead of
retrying with a different amount to work around it -- the cap is not
negotiable.

A create_order tool result has two independent signals you must both check:
- result: "allowed" means the spend cap and idempotency checks passed.
- If the result also contains a "razorpay_error" field, the actual payment
  order was NOT created despite passing those checks. In that case your
  final summary must say the order did NOT go through.

IMPORTANT: even when result is "allowed" and a razorpay_order_id is
present, this only means a Razorpay ORDER was created -- a record of
expected payment. No money has moved and no card has been charged. You
have no way to capture a real payment, because that requires a human to
enter card details through a checkout flow, which does not exist for you.
Never say the purchase, payment, or transaction was "completed" or
"successful" in a financial sense. Instead say the order was created and
is awaiting payment, and that no payment capture step exists for an
autonomous buyer in this system. Give a final summary of what happened,
using this precise distinction."""


def run_buyer(goal: str) -> str:
    session = CartSession(caller="ai_buyer")
    agent = Agent(SYSTEM_PROMPT, TOOLS_SCHEMA, session.executor_map())
    return agent.step(goal)


if __name__ == "__main__":
    goal = " ".join(sys.argv[1:]) or "Get me running shoes under ₹3000."
    print(f"goal> {goal}\n")
    result = run_buyer(goal)
    print(f"buyer agent> {result}")