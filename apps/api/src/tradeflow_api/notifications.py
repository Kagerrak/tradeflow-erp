from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_notification_manager,
    require_notification_reader,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    device_registrations,
    notification_effect_events,
    notification_preferences,
    notification_read_events,
    operational_notifications,
)

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])

DEFAULT_DEVICE_TTL_DAYS = 90
MASKED_TITLE = "Notification removed"
MASKED_BODY = "This notification has been revoked or is no longer available."


class RegisterDeviceCommand(BaseModel):
    device_token: str = Field(min_length=1, max_length=500)
    platform: str = Field(pattern=r"^(ios|android|web)$")
    app_version: str | None = Field(default=None, max_length=50)
    locale: str = Field(default="en", max_length=10)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _expires_at_after_now(cls, value: datetime | None) -> datetime | None:
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


class DeviceRegistrationResponse(BaseModel):
    device_registration_id: str
    user_subject: str
    device_token_summary: str
    platform: str
    app_version: str | None
    locale: str
    is_active: bool
    expires_at: datetime
    created_at: datetime


class UpdatePreferenceCommand(BaseModel):
    push_enabled: bool
    inbox_enabled: bool
    quiet_hours_start: str | None = Field(default=None, max_length=5)
    quiet_hours_end: str | None = Field(default=None, max_length=5)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _quiet_hours_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _is_hhmm(value):
            raise ValueError("quiet hours must be HH:MM")
        return value


class NotificationPreferenceResponse(BaseModel):
    preference_id: str
    user_subject: str
    category: str
    push_enabled: bool
    inbox_enabled: bool
    quiet_hours_start: str | None
    quiet_hours_end: str | None
    created_at: datetime
    updated_at: datetime


class InboxItemResponse(BaseModel):
    notification_id: str
    notification_type: str
    recipient_subject: str
    title: str
    body: str
    deep_link_path: str
    deep_link_token: str
    status: str
    source_type: str
    source_id: str
    branch_id: str
    warehouse_id: str | None
    required_capability: str | None
    created_at: datetime
    read_at: datetime | None


class DeepLinkResponse(BaseModel):
    notification_id: str
    status: str
    authorized_path: str
    source_type: str
    source_id: str
    title: str
    body: str


class InboxResponse(BaseModel):
    items: list[InboxItemResponse]


class ReadNotificationCommand(BaseModel):
    device_registration_id: str | None = None


class RevokeNotificationCommand(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _is_hhmm(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _token_summary(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _masked(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "title": MASKED_TITLE, "body": MASKED_BODY}


def _read_event_id(notification_id: UUID, idempotency_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:notification-read:{notification_id}:{idempotency_key}")


async def _ensure_default_preferences(session: AsyncSession, user_subject: str) -> None:
    categories = [
        "delivery_assignment",
        "delivery_confirmation",
        "delivery_correction",
        "approval_required",
        "payment_received",
        "payment_rejected",
    ]
    for category in categories:
        await session.execute(
            pg_insert(notification_preferences)
            .values(
                preference_id=uuid4(),
                user_subject=user_subject,
                category=category,
                push_enabled=True,
                inbox_enabled=True,
            )
            .on_conflict_do_nothing(index_elements=["user_subject", "category"])
        )


@router.post(
    "/devices",
    response_model=DeviceRegistrationResponse,
    responses=error_responses(400, 401, 403, 409, 422, 500),
    status_code=201,
)
async def register_device(
    request: Request,
    command: RegisterDeviceCommand,
    actor: Annotated[AuthorizedUser, Depends(require_notification_manager)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DeviceRegistrationResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")

    expires_at = command.expires_at or datetime.now(UTC) + timedelta(days=DEFAULT_DEVICE_TTL_DAYS)
    registration_id = uuid4()
    await session.execute(
        pg_insert(device_registrations)
        .values(
            device_registration_id=registration_id,
            user_subject=actor.subject,
            device_token=command.device_token,
            platform=command.platform,
            app_version=command.app_version,
            locale=command.locale,
            is_active=True,
            expires_at=expires_at,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["user_subject", "device_token", "platform"],
            set_={
                "is_active": True,
                "app_version": command.app_version,
                "locale": command.locale,
                "expires_at": expires_at,
                "updated_at": func.now(),
            },
        )
    )
    row = (
        (
            await session.execute(
                select(device_registrations).where(
                    device_registrations.c.user_subject == actor.subject,
                    device_registrations.c.device_token == command.device_token,
                    device_registrations.c.platform == command.platform,
                )
            )
        )
        .mappings()
        .one()
    )
    await session.commit()
    return DeviceRegistrationResponse(
        device_registration_id=str(row["device_registration_id"]),
        user_subject=row["user_subject"],
        device_token_summary=_token_summary(row["device_token"]),
        platform=row["platform"],
        app_version=row["app_version"],
        locale=row["locale"],
        is_active=row["is_active"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


@router.get(
    "/devices",
    response_model=list[DeviceRegistrationResponse],
    responses=error_responses(401, 403, 500),
)
async def list_devices(
    actor: Annotated[AuthorizedUser, Depends(require_notification_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[DeviceRegistrationResponse]:
    rows = (
        (
            await session.execute(
                select(device_registrations)
                .where(device_registrations.c.user_subject == actor.subject)
                .order_by(device_registrations.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    return [
        DeviceRegistrationResponse(
            device_registration_id=str(row["device_registration_id"]),
            user_subject=row["user_subject"],
            device_token_summary=_token_summary(row["device_token"]),
            platform=row["platform"],
            app_version=row["app_version"],
            locale=row["locale"],
            is_active=row["is_active"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.delete(
    "/devices/{device_registration_id}",
    responses=error_responses(401, 403, 404, 500),
    status_code=204,
)
async def deactivate_device(
    device_registration_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_notification_manager)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    result = await session.execute(
        device_registrations.update()
        .where(
            device_registrations.c.device_registration_id == device_registration_id,
            device_registrations.c.user_subject == actor.subject,
        )
        .values(is_active=False, updated_at=func.now())
        .returning(device_registrations.c.device_registration_id)
    )
    if result.scalar_one_or_none() is None:
        raise AppError(404, "device_not_found", "Device registration not found.")
    await session.commit()


@router.get(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    responses=error_responses(401, 403, 500),
)
async def list_preferences(
    actor: Annotated[AuthorizedUser, Depends(require_notification_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[NotificationPreferenceResponse]:
    await _ensure_default_preferences(session, actor.subject)
    rows = (
        (
            await session.execute(
                select(notification_preferences)
                .where(notification_preferences.c.user_subject == actor.subject)
                .order_by(notification_preferences.c.category)
            )
        )
        .mappings()
        .all()
    )
    return [
        NotificationPreferenceResponse(
            preference_id=str(row["preference_id"]),
            user_subject=row["user_subject"],
            category=row["category"],
            push_enabled=row["push_enabled"],
            inbox_enabled=row["inbox_enabled"],
            quiet_hours_start=row["quiet_hours_start"],
            quiet_hours_end=row["quiet_hours_end"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


@router.put(
    "/preferences/{category}",
    response_model=NotificationPreferenceResponse,
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
async def update_preference(
    category: str,
    command: UpdatePreferenceCommand,
    actor: Annotated[AuthorizedUser, Depends(require_notification_manager)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> NotificationPreferenceResponse:
    await _ensure_default_preferences(session, actor.subject)
    result = await session.execute(
        notification_preferences.update()
        .where(
            notification_preferences.c.user_subject == actor.subject,
            notification_preferences.c.category == category,
        )
        .values(
            push_enabled=command.push_enabled,
            inbox_enabled=command.inbox_enabled,
            quiet_hours_start=command.quiet_hours_start,
            quiet_hours_end=command.quiet_hours_end,
            updated_at=func.now(),
        )
        .returning(notification_preferences)
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise AppError(404, "preference_not_found", "Notification preference not found.")
    await session.commit()
    return NotificationPreferenceResponse(
        preference_id=str(row["preference_id"]),
        user_subject=row["user_subject"],
        category=row["category"],
        push_enabled=row["push_enabled"],
        inbox_enabled=row["inbox_enabled"],
        quiet_hours_start=row["quiet_hours_start"],
        quiet_hours_end=row["quiet_hours_end"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get(
    "/inbox",
    response_model=InboxResponse,
    responses=error_responses(401, 403, 500),
)
async def list_inbox(
    actor: Annotated[AuthorizedUser, Depends(require_notification_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: str | None = None,
    limit: int = 50,
) -> InboxResponse:
    query = select(operational_notifications).where(
        operational_notifications.c.recipient_subject == actor.subject
    )
    if status is not None:
        query = query.where(operational_notifications.c.status == status)
    query = query.order_by(operational_notifications.c.created_at.desc()).limit(limit)
    rows = (await session.execute(query)).mappings().all()
    items: list[InboxItemResponse] = []
    for row in rows:
        item = dict(row)
        if item["status"] == "revoked":
            item = _masked(item)
        item["notification_id"] = str(item["notification_id"])
        item["source_id"] = str(item["source_id"])
        item["branch_id"] = str(item["branch_id"])
        if item["warehouse_id"] is not None:
            item["warehouse_id"] = str(item["warehouse_id"])
        items.append(InboxItemResponse(**item))
    return InboxResponse(items=items)


@router.post(
    "/{notification_id}/read",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
    status_code=204,
)
async def mark_read(
    notification_id: UUID,
    command: ReadNotificationCommand,
    actor: Annotated[AuthorizedUser, Depends(require_notification_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> None:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")

    notification = (
        (
            await session.execute(
                select(operational_notifications).where(
                    operational_notifications.c.notification_id == notification_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if notification is None:
        raise AppError(404, "notification_not_found", "Notification not found.")
    if notification["recipient_subject"] != actor.subject:
        raise AppError(
            403, "notification_access_denied", "Notification is not addressed to this user."
        )
    if notification["status"] == "revoked":
        raise AppError(409, "notification_revoked", "Notification has been revoked.")

    device_registration_id = (
        UUID(command.device_registration_id) if command.device_registration_id else None
    )
    if device_registration_id:
        device = (
            (
                await session.execute(
                    select(device_registrations).where(
                        device_registrations.c.device_registration_id == device_registration_id,
                        device_registrations.c.user_subject == actor.subject,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if device is None:
            raise AppError(404, "device_not_found", "Device registration not found.")

    read_event_id = _read_event_id(notification_id, idempotency_key)
    await session.execute(
        pg_insert(notification_read_events)
        .values(
            read_event_id=read_event_id,
            notification_id=notification_id,
            device_registration_id=device_registration_id,
            read_by=actor.subject,
        )
        .on_conflict_do_nothing(index_elements=["read_event_id"])
    )
    await session.execute(
        operational_notifications.update()
        .where(
            operational_notifications.c.notification_id == notification_id,
            operational_notifications.c.status != "read",
        )
        .values(status="read", read_at=func.now())
    )
    await session.execute(
        pg_insert(notification_effect_events)
        .values(
            effect_event_id=uuid5(
                NAMESPACE_URL,
                f"tradeflow:notification-effect:read:{notification_id}:{read_event_id}",
            ),
            notification_id=notification_id,
            effect_type="read",
            source_type="notification_read",
            source_id=read_event_id,
            device_registration_id=device_registration_id,
            payload={"idempotency_key": idempotency_key},
        )
        .on_conflict_do_nothing(
            index_elements=["notification_id", "effect_type", "source_type", "source_id"]
        )
    )
    await session.commit()


@router.post(
    "/{notification_id}/revoke",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
    status_code=204,
)
async def revoke_notification(
    notification_id: UUID,
    command: RevokeNotificationCommand,
    actor: Annotated[AuthorizedUser, Depends(require_notification_manager)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> None:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")

    notification = (
        (
            await session.execute(
                select(operational_notifications).where(
                    operational_notifications.c.notification_id == notification_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if notification is None:
        raise AppError(404, "notification_not_found", "Notification not found.")
    if notification["recipient_subject"] != actor.subject:
        raise AppError(
            403, "notification_access_denied", "Notification is not addressed to this user."
        )
    if notification["status"] == "revoked":
        return None

    effect_id = uuid5(
        NAMESPACE_URL, f"tradeflow:notification-effect:revoked:{notification_id}:{idempotency_key}"
    )
    await session.execute(
        operational_notifications.update()
        .where(operational_notifications.c.notification_id == notification_id)
        .values(status="revoked", revoked_at=func.now())
    )
    await session.execute(
        pg_insert(notification_effect_events)
        .values(
            effect_event_id=effect_id,
            notification_id=notification_id,
            effect_type="revoked",
            source_type="notification_revoke",
            source_id=effect_id,
            payload={"reason": command.reason, "idempotency_key": idempotency_key},
        )
        .on_conflict_do_nothing(
            index_elements=["notification_id", "effect_type", "source_type", "source_id"]
        )
    )
    await session.commit()


@router.get(
    "/deep-links/{deep_link_token}",
    response_model=DeepLinkResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def resolve_deep_link(
    deep_link_token: str,
    actor: Annotated[AuthorizedUser, Depends(require_notification_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeepLinkResponse:
    notification = (
        (
            await session.execute(
                select(operational_notifications).where(
                    operational_notifications.c.deep_link_token == deep_link_token
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if notification is None:
        raise AppError(404, "deep_link_not_found", "Deep link not found.")
    if notification["recipient_subject"] != actor.subject:
        raise AppError(403, "deep_link_access_denied", "Deep link is not authorized for this user.")

    if (
        notification["required_capability"]
        and notification["required_capability"] not in actor.capabilities
    ):
        raise AppError(403, "capability_required", "Required capability is missing.")

    branch_ok = notification["branch_id"] in actor.branch_ids
    warehouse_ok = (
        notification["warehouse_id"] is None or notification["warehouse_id"] in actor.warehouse_ids
    )
    if not branch_ok or not warehouse_ok:
        raise AppError(403, "scope_required", "Required branch or warehouse scope is missing.")

    item = dict(notification)
    if item["status"] == "revoked":
        item = _masked(item)

    return DeepLinkResponse(
        notification_id=str(item["notification_id"]),
        status=item["status"],
        authorized_path=item["deep_link_path"],
        source_type=item["source_type"],
        source_id=str(item["source_id"]),
        title=item["title"],
        body=item["body"],
    )
