"""
The gate is the one place money-touching decisions get made.

Design principle (this is the "AI judgment: where we chose NOT to use AI" story):
the LLM never sees this code and never gets to approve, deny, or interpret a
transaction. It can only propose an order by calling create_order(); everything
after that -- cap enforcement, duplicate-request detection, logging -- is plain,
deterministic Python that runs the same way every time given the same input.
This module has zero dependency on Claude, Razorpay, or any network call, on
purpose, so it can be tested completely in isolation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Caller(str, Enum):
    HUMAN = "human"
    AI_BUYER = "ai_buyer"


class GateResult(str, Enum):
    ALLOWED = "allowed"
    BLOCKED_CAP = "blocked_cap_exceeded"
    BLOCKED_ACTION = "blocked_action_not_permitted"
    REPLAYED = "replayed_idempotent"


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: float
    caller: str
    action: str
    idempotency_key: str
    amount: int
    items: list
    result: str
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "caller": self.caller,
            "action": self.action,
            "idempotency_key": self.idempotency_key,
            "amount": self.amount,
            "items": self.items,
            "result": self.result,
            "reasoning": self.reasoning,
        }


# Actions a caller (human chat agent OR an autonomous AI buyer agent) is ever
# permitted to request through the gate. Refunds, cancellations, and repeat
# charges are deliberately absent -- there is no code path for them here at
# all, not even a disabled one. That's the point: it isn't a policy the LLM
# is asked to respect, it's an action the gate doesn't know how to perform.
PERMITTED_ACTIONS = {"create_order"}


class TransactionGate:
    def __init__(self, spend_cap: int = 5000):
        self.spend_cap = spend_cap
        self._audit_log: list[AuditEntry] = []
        # idempotency_key -> the AuditEntry produced the first time we saw it
        self._seen_keys: dict[str, AuditEntry] = {}

    def request(
        self,
        *,
        caller: Caller,
        action: str,
        idempotency_key: str,
        amount: int,
        items: list,
        reasoning: str,
    ) -> AuditEntry:
        """
        The single entry point for any money-touching request, whether it
        comes from the human-chat agent or the autonomous AI-buyer agent.

        Every call is logged, including blocked ones -- a blocked attempt is
        still evidence the gate works and belongs in the audit trail.
        """
        # 1. Idempotency check first: a retried call with a key we've already
        #    processed must NEVER re-execute. It returns the original result.
        if idempotency_key in self._seen_keys:
            original = self._seen_keys[idempotency_key]
            replay = AuditEntry(
                entry_id=str(uuid.uuid4()),
                timestamp=time.time(),
                caller=caller.value,
                action=action,
                idempotency_key=idempotency_key,
                amount=amount,
                items=items,
                result=GateResult.REPLAYED.value,
                reasoning=(
                    f"Duplicate request for idempotency_key={idempotency_key}; "
                    f"returning original result ({original.result}) instead of "
                    f"re-executing. Original entry_id={original.entry_id}."
                ),
            )
            self._audit_log.append(replay)
            return replay

        # 2. Action allow-list. If it isn't in PERMITTED_ACTIONS, it's not a
        #    "no" decision the agent needs to reason about -- it's simply not
        #    an action this system exposes.
        if action not in PERMITTED_ACTIONS:
            entry = AuditEntry(
                entry_id=str(uuid.uuid4()),
                timestamp=time.time(),
                caller=caller.value,
                action=action,
                idempotency_key=idempotency_key,
                amount=amount,
                items=items,
                result=GateResult.BLOCKED_ACTION.value,
                reasoning=f"'{action}' is not a permitted gated action.",
            )
            self._audit_log.append(entry)
            self._seen_keys[idempotency_key] = entry
            return entry

        # 3. Hard spend cap, enforced in code -- not something the LLM is
        #    trusted to self-police.
        if amount > self.spend_cap:
            entry = AuditEntry(
                entry_id=str(uuid.uuid4()),
                timestamp=time.time(),
                caller=caller.value,
                action=action,
                idempotency_key=idempotency_key,
                amount=amount,
                items=items,
                result=GateResult.BLOCKED_CAP.value,
                reasoning=(
                    f"Amount {amount} exceeds hard spend cap of {self.spend_cap}. "
                    f"Blocked before any Razorpay call was made."
                ),
            )
            self._audit_log.append(entry)
            self._seen_keys[idempotency_key] = entry
            return entry

        # 4. Allowed. (Caller-supplied reasoning is stored for the audit
        #    trail but never influences 1-3 above.)
        entry = AuditEntry(
            entry_id=str(uuid.uuid4()),
            timestamp=time.time(),
            caller=caller.value,
            action=action,
            idempotency_key=idempotency_key,
            amount=amount,
            items=items,
            result=GateResult.ALLOWED.value,
            reasoning=reasoning,
        )
        self._audit_log.append(entry)
        self._seen_keys[idempotency_key] = entry
        return entry

    def audit_log(self) -> list[dict]:
        return [e.to_dict() for e in self._audit_log]
