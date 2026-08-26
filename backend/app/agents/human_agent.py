"""Human-facing chat agent -- CLI for now, swap in a web endpoint on Day 5."""

from app.agents.base_agent import Agent
from app.agents.tools import TOOLS_SCHEMA, CartSession

SYSTEM_PROMPT = """You are a shopping assistant for an online store. Help the
user search the catalog, build a cart, and check out. Always show prices in
INR. Confirm the cart contents with the user before calling create_order.

A create_order tool result has two independent signals you must both check:
- result: "allowed" means the spend cap and idempotency checks passed.
- If the result also contains a "razorpay_error" field, the actual payment
  order was NOT created despite passing those checks -- a network or
  gateway failure occurred downstream. In that case you must tell the user
  the order did NOT go through and suggest they retry, never claim success.
Only report success if result is "allowed" AND there is no razorpay_error
field AND a razorpay_order_id is present."""


def main():
    session = CartSession(caller="human")
    agent = Agent(SYSTEM_PROMPT, TOOLS_SCHEMA, session.executor_map())

    print("Chatout (type 'quit' to exit)")
    while True:
        user_input = input("\nyou> ").strip()
        if user_input.lower() in {"quit", "exit"}:
            break
        reply = agent.step(user_input)
        print(f"\nassistant> {reply}")


if __name__ == "__main__":
    main()