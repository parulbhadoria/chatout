import time

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from app import catalog
from app.core.gate import TransactionGate, Caller
from app.core import razorpay_client
from app.routers import chat

app = FastAPI(title="Chatout API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # test-only; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)

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
    Flow: resolve items -> gate.request() decides allow/block/replay ->
    ONLY if allowed, a real Razorpay test-mode order is created. Razorpay
    is never touched for a blocked or replayed request.
    """
    try:
        caller = Caller(x_caller)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown caller type: {x_caller}")

    amount = 0
    resolved_items = []
    for item in req.items:
        if item.qty <= 0:
            raise HTTPException(status_code=422, detail=f"Invalid quantity for {item.sku}: must be positive")
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

    response = {
        "result": entry.result,
        "entry_id": entry.entry_id,
        "amount": entry.amount,
        "items": entry.items,
        "reasoning": entry.reasoning,
    }

    if entry.result != "allowed":
        return response

    # Gate approved it -- now, and only now, create the real Razorpay
    # test-mode order.
    rp_order = None
    last_error = None
    for attempt in range(2):
        try:
            rp_order = razorpay_client.create_razorpay_order(
                amount_inr=entry.amount,
                receipt=entry.entry_id,
                notes={"caller": caller.value, "reasoning": req.reasoning},
            )
            break
        except Exception as e:
            last_error = e
            time.sleep(0.5)

    if rp_order is None:
        response["razorpay_error"] = str(last_error)
        return response

    response["razorpay_order_id"] = rp_order["id"]
    response["razorpay_amount"] = rp_order["amount"]  # paise
    response["razorpay_currency"] = rp_order["currency"]
    response["razorpay_key_id"] = razorpay_client.public_key_id()

    if caller == Caller.AI_BUYER:
        response["note"] = (
            "AI buyer path: gate-approved and Razorpay test-mode order "
            "created. No card-capture step exists for a non-human caller."
        )

    return response


# ---------- Payment verification (human / Checkout.js path only) ----------

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@app.post("/orders/verify")
def verify_payment(req: VerifyPaymentRequest):
    ok = razorpay_client.verify_signature(
        req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Payment signature verification failed")
    return {"status": "verified", "razorpay_payment_id": req.razorpay_payment_id}


# ---------- Audit trail ----------

@app.get("/audit")
def audit_log():
    return {"entries": gate.audit_log()}