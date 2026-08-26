"""
The gate is the one place money-touching decisions get made.

Design principle (this is the "AI judgment: where we chose NOT to use AI"
story): the LLM never sees this code and never gets to approve, deny, or
interpret a transaction. It can only propose an order by calling
create_order(); everything after that -- cap enforcement, duplicate-request
detection, logging -- is plain, deterministic Python that runs the same way
every time given the same input.

Day 4 change: the audit log and idempotency record are now persisted to
SQLite instead of an in-process list/dict. Previously, a server restart
silently wiped the entire audit trail -- a real trust gap we hit and are
fixing here, not a hypothetical one.

Day 5+ change: the spend cap now also checks CUMULATIVE allowed spend per
caller, not just the size of a single request. Adversarial testing found
that an agent could split a purchase over the cap into multiple smaller
orders, each individually under ₹5,000 but totaling well over it -- a real
gap, not a hypothetical one. The cap is meant to bound how much a caller
can spend, not how much they can spend per request.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from threading import Lock


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


PERMITTED_ACTIONS = {"create_order"}

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "audit.db")


class TransactionGate:
    def __init__(self, spend_cap: int = 5000, db_path: str | None = None):
        self.spend_cap = spend_cap
        self.db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # check_same_thread=False: FastAPI's dev server can hand requests to
        # different threads. We serialize actual access with our own lock
        # below rather than trusting SQLite's default thread-safety here.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = Lock()
        self._init_schema()

    def _init_schema(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                entry_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                caller TEXT NOT NULL,
                action TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                items TEXT NOT NULL,
                result TEXT NOT NULL,
                reasoning TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_idempotency_key
            ON audit_log(idempotency_key)
        """)
        self._conn.commit()

    def _row_to_entry(self, row) -> AuditEntry:
        return AuditEntry(
            entry_id=row[0],
            timestamp=row[1],
            caller=row[2],
            action=row[3],
            idempotency_key=row[4],
            amount=row[5],
            items=json.loads(row[6]),
            result=row[7],
            reasoning=row[8],
        )

    def _insert(self, entry: AuditEntry):
        self._conn.execute(
            """INSERT INTO audit_log
               (entry_id, timestamp, caller, action, idempotency_key,
                amount, items, result, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.entry_id, entry.timestamp, entry.caller, entry.action,
                entry.idempotency_key, entry.amount, json.dumps(entry.items),
                entry.result, entry.reasoning,
            ),
        )
        self._conn.commit()

    def _find_by_idempotency_key(self, key: str) -> AuditEntry | None:
        cur = self._conn.execute(
            # Earliest entry for this key is the "original" -- later ones
            # (if any ever slipped through) are the ones we're guarding
            # against, so always resolve to the first.
            "SELECT * FROM audit_log WHERE idempotency_key = ? ORDER BY timestamp ASC LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
        return self._row_to_entry(row) if row else None

    def _cumulative_allowed(self, caller: str) -> int:
        """Total amount this caller has already had ALLOWED (not blocked or
        replayed) across all prior requests. Used to catch a purchase split
        across multiple orders that each individually stay under the cap."""
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM audit_log WHERE caller = ? AND result = 'allowed'",
            (caller,),
        )
        return cur.fetchone()[0]

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

        Locked end-to-end so two concurrent requests with the same
        idempotency_key can't both pass the "have we seen this key" check
        before either has written its row -- see the Day 4 stress test for
        why this matters in practice, not just in theory.
        """
        with self._lock:
            original = self._find_by_idempotency_key(idempotency_key)
            if original is not None:
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
                self._insert(replay)
                return replay

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
                self._insert(entry)
                return entry

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
                self._insert(entry)
                return entry

            cumulative = self._cumulative_allowed(caller.value)
            if cumulative + amount > self.spend_cap:
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
                        f"This request ({amount}) plus this caller's prior allowed "
                        f"spend ({cumulative}) would total {cumulative + amount}, "
                        f"exceeding the cap of {self.spend_cap}. Blocked -- "
                        f"splitting a purchase into multiple smaller orders does "
                        f"not bypass the cap, because it tracks cumulative spend "
                        f"per caller, not just the size of a single request."
                    ),
                )
                self._insert(entry)
                return entry

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
            self._insert(entry)
            return entry

    def audit_log(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM audit_log ORDER BY timestamp ASC")
        return [self._row_to_entry(row).to_dict() for row in cur.fetchall()]