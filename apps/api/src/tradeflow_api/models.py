from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

metadata = MetaData()

platform_command_receipts = Table(
    "platform_command_receipts",
    metadata,
    Column("command_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("actor_subject", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("response_json", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
