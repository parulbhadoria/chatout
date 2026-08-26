"""
Shared tool definitions + cart state, used by both the human chat agent and
the AI buyer agent. Tools talk to the running FastAPI server over HTTP (not
to catalog.py / gate.py directly) so that both callers demonstrably hit the
exact same code path -- this is what makes the audit log's caller field
meaningful instead of decorative.
"""

import hashlib
import json
import httpx

API_BASE = "http://localhost:8000"

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the product catalog by keyword, category, and/or max price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword, e.g. 'running shoes'. Empty string for no filter."},
                    "category": {"type": ["string", "null"], "description": "Optional category filter."},
                    "max_price": {"type": ["integer", "null"], "description": "Optional max price in INR."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "Add a product to the cart by SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "qty": {"type": "integer", "description": "Quantity, default 1."},
                },
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": "View current cart contents.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Check out the current cart. The only money-touching tool -- capped and audited server-side, outside this agent's control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "Brief reason for this order, stored in the audit log."},
                },
                "required": ["reasoning"],
            },
        },
    },
]


class CartSession:
    """Per-agent cart state + tool executors bound to one caller identity."""

    def __init__(self, caller: str):
        self.caller = caller  # "human" or "ai_buyer"
        self.cart: dict[str, int] = {}
        self.client = httpx.Client(base_url=API_BASE, timeout=10.0)

    def _cart_signature(self) -> str:
        raw = json.dumps(sorted(self.cart.items()))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def search_catalog(self, query: str = "", category: str | None = None, max_price: int | None = None) -> dict:
        params = {"q": query}
        if category:
            params["category"] = category
        if max_price is not None:
            params["max_price"] = max_price
        resp = self.client.get("/catalog/agent", params=params)
        resp.raise_for_status()
        return resp.json()

    def add_to_cart(self, sku: str, qty: int = 1) -> dict:
        self.cart[sku] = self.cart.get(sku, 0) + qty
        return {"cart": dict(self.cart)}

    def view_cart(self) -> dict:
        return {"cart": self.cart}

    def create_order(self, reasoning: str) -> dict:
        if not self.cart:
            return {"error": "cart is empty"}
        # Idempotency key is derived from caller + cart contents: retrying
        # the SAME cart reuses the same key (so the gate replays instead of
        # double-charging); changing the cart produces a fresh key.
        idempotency_key = f"{self.caller}:{self._cart_signature()}"
        items = [{"sku": sku, "qty": qty} for sku, qty in self.cart.items()]
        resp = self.client.post(
            "/orders/create",
            headers={"X-Caller": self.caller},
            json={"items": items, "idempotency_key": idempotency_key, "reasoning": reasoning},
        )
        if resp.status_code != 200:
            return {"error": f"order request failed: {resp.status_code} {resp.text}"}
        result = resp.json()
        if result["result"] == "allowed":
            self.cart = {}
        return result

    def executor_map(self) -> dict:
        return {
            "search_catalog": self.search_catalog,
            "add_to_cart": self.add_to_cart,
            "view_cart": self.view_cart,
            "create_order": self.create_order,
        }