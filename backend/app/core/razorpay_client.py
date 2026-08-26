"""
Thin wrapper around the Razorpay SDK, test-mode only. This module is only
ever called AFTER the gate has already returned result="allowed" -- it has
no say in whether a transaction happens, only in executing one the gate
already approved. Keeping it separate from gate.py keeps the "who decides"
vs "who executes" boundary visible in the codebase, not just in the README.
"""

import os
import razorpay

_client = razorpay.Client(
    auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
)


def create_razorpay_order(amount_inr: int, receipt: str, notes: dict) -> dict:
    """amount_inr is in rupees; Razorpay wants paise."""
    return _client.order.create({
        "amount": amount_inr * 100,
        "currency": "INR",
        "receipt": receipt,
        "notes": notes,
    })


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        _client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def public_key_id() -> str:
    return os.environ["RAZORPAY_KEY_ID"]