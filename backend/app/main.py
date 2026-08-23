from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

from app import catalog
from app.core.gate import TransactionGate, Caller

app = FastAPI(title="Chatout API", version="0.1.0")

# Single shared gate instance for the process. Every money-touching request,
# from either the human-chat path or the AI-buyer path, goes through this.
gate = TransactionGate(spend_cap=5000)


# ---------- Catalog: human-facing ----------

@app.get("/catalog/browse")
def browse_catalog(q: str = "", category: str | None = None, max_price: int | None = None):
    results = catalog.search(query=q, category=category, max_price=max_price)
    return {"count": len(results), "products": catalog.human_view(results)}


# ---------- Catalog: agent-readable ----------

@app.get("/catalog/agent")
def agent_catalog(q: str = "", category: str | None = None, max_price: int | None = None):
    results = catalog.search(query=q, category=category, max_price=max_price)
    return {
        "schema_version": "1.0",
        "count": len(results),
        "products": catalog.agent_view(results),
    }


# ---------- Gated order creation ----------

class OrderItem(BaseModel):
    sku: str
    qty: int


class CreateOrderRequest(BaseModel):
    items: list[OrderItem]
    idempotency_key: str
    reasoning: str


@app.post("/orders/create")
def create_order(req: CreateOrderRequest, x_caller: str = Header(default="human")):
    """
    x_caller header distinguishes the human-chat agent from the autonomous
    AI-buyer agent. Both are routed through the exact same gate -- there is
    no separate, looser code path for either.
    """
    try:
        caller = Caller(x_caller)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown caller type: {x_caller}")

    amount = 0
    resolved_items = []
    for item in req.items:
        product = catalog.get_product(item.sku)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Unknown SKU: {item.sku}")
        if product["stock"] < item.qty:
            raise HTTPException(status_code=409, detail=f"Insufficient stock for {item.sku}")
        amount += product["price"] * item.qty
        resolved_items.append({"sku": item.sku, "qty": item.qty, "unit_price": product["price"]})

    entry = gate.request(
        caller=caller,
        action="create_order",
        idempotency_key=req.idempotency_key,
        amount=amount,
        items=resolved_items,
        reasoning=req.reasoning,
    )

    return {
        "result": entry.result,
        "entry_id": entry.entry_id,
        "amount": entry.amount,
        "items": entry.items,
        "reasoning": entry.reasoning,
    }


# ---------- Audit trail ----------

@app.get("/audit")
def audit_log():
    return {"entries": gate.audit_log()}
