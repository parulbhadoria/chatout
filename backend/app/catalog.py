"""
Two views onto the same catalog, on purpose.

- /catalog/browse  -- human-facing: prose-friendly, meant for a chat UI or a
  storefront page to render.
- /catalog/agent   -- agent-readable: flat, typed, minimal-prose JSON meant
  to be machine-consumed by ANY agent (ours or a third party's), with a
  documented schema so it's genuinely something an external AI buyer could
  integrate against, not just our own frontend with a different content-type.
"""

import json
import os

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "catalog.json")

with open(_CATALOG_PATH) as f:
    _CATALOG = json.load(f)


def get_catalog() -> list[dict]:
    return _CATALOG


def get_product(product_id: str) -> dict | None:
    for p in _CATALOG:
        if p["id"] == product_id:
            return p
    return None


def search(query: str = "", category: str | None = None, max_price: int | None = None) -> list[dict]:
    query = (query or "").lower().strip()
    results = []
    for p in _CATALOG:
        if query and query not in p["name"].lower() and query not in p["description"].lower() and query not in p["category"].lower():
            continue
        if category and p["category"].lower() != category.lower():
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        results.append(p)
    return results


def human_view(products: list[dict]) -> list[dict]:
    """Prose-friendly shape for a chat UI / storefront page."""
    return [
        {
            "id": p["id"],
            "title": p["name"],
            "price_display": f"\u20b9{p['price']}",
            "in_stock": p["stock"] > 0,
            "blurb": p["description"],
            "category": p["category"].capitalize(),
        }
        for p in products
    ]


def agent_view(products: list[dict]) -> list[dict]:
    """
    Minimal, typed, machine-consumable shape. This is the contract an
    external AI buyer would integrate against -- stable field names,
    no display formatting, no prose baked into values.
    """
    return [
        {
            "sku": p["id"],
            "name": p["name"],
            "category": p["category"],
            "price_inr": p["price"],
            "stock_qty": p["stock"],
            "purchasable": p["stock"] > 0,
            "description": p["description"],
        }
        for p in products
    ]
