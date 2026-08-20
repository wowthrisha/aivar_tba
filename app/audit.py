"""Append-only, hash-chained audit log - the test double for
SQLAlchemyAuditLog (app/db_store.py), which persists to the real
`audit_records` table at runtime and reuses `_entry_hash`/`GENESIS_HASH`
from this module so the chain algorithm is provably identical regardless
of storage backend. Methods are `async def` with no real awaiting inside,
purely for interface uniformity with the DB-backed version.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    id: str
    prev_hash: str
    entry_hash: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime


def _entry_hash(prev_hash: str, event_type: str, actor: str, payload: dict[str, Any], created_at: datetime) -> str:
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    material = f"{prev_hash}|{event_type}|{actor}|{canonical_payload}|{created_at.isoformat()}"
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass
class AuditVerifyResult:
    valid: bool
    records_checked: int
    first_invalid_id: str | None


class AuditLog:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    async def append(self, event_type: str, actor: str, payload: dict[str, Any]) -> AuditRecord:
        prev_hash = self._records[-1].entry_hash if self._records else GENESIS_HASH
        created_at = datetime.now(timezone.utc)
        entry_hash = _entry_hash(prev_hash, event_type, actor, payload, created_at)
        record = AuditRecord(
            id=str(uuid.uuid4()),
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            event_type=event_type,
            actor=actor,
            payload=payload,
            created_at=created_at,
        )
        self._records.append(record)
        return record

    async def list_records(
        self,
        action_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditRecord]:
        records = self._records
        if action_id is not None:
            records = [r for r in records if r.payload.get("action_id") == action_id]
        if event_type is not None:
            records = [r for r in records if r.event_type == event_type]
        return records[offset : offset + limit]

    async def verify(self) -> AuditVerifyResult:
        prev_hash = GENESIS_HASH
        for i, record in enumerate(self._records):
            if record.prev_hash != prev_hash:
                return AuditVerifyResult(valid=False, records_checked=i, first_invalid_id=record.id)
            expected = _entry_hash(
                record.prev_hash, record.event_type, record.actor, record.payload, record.created_at
            )
            if record.entry_hash != expected:
                return AuditVerifyResult(valid=False, records_checked=i, first_invalid_id=record.id)
            prev_hash = record.entry_hash
        return AuditVerifyResult(valid=True, records_checked=len(self._records), first_invalid_id=None)
