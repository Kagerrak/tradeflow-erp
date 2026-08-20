from __future__ import annotations

from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import (
    delivery_corrections,
    delivery_dispatches,
    delivery_state,
    device_registrations,
    notification_deliveries,
    notification_effect_events,
    operational_notifications,
    outbox_events,
    outbox_handler_receipts,
    role_template_capabilities,
    role_templates,
    user_branch_scopes,
    user_role_templates,
    user_warehouse_scopes,
    users,
)

HANDLER_NAME = "notifications.operational.v1"

NOTIFICATION_TYPE_CAPABILITIES: dict[str, str | None] = {
    "delivery_confirmation": "fulfillment:delivery-read",
    "delivery_correction": "fulfillment:delivery-correction-request",
}


async def _is_recipient_authorized(
    session: AsyncSession,
    recipient_subject: str,
    branch_id: UUID,
    warehouse_id: UUID | None,
    required_capability: str | None,
) -> bool:
    user = (
        (
            await session.execute(
                select(users.c.is_active).where(users.c.subject == recipient_subject)
            )
        )
        .mappings()
        .one_or_none()
    )
    if user is None or not user["is_active"]:
        return False

    if required_capability is not None:
        capability_exists = await session.scalar(
            select(1)
            .select_from(
                user_role_templates.join(
                    role_templates,
                    user_role_templates.c.role_template_id == role_templates.c.role_template_id,
                ).join(
                    role_template_capabilities,
                    role_templates.c.role_template_id
                    == role_template_capabilities.c.role_template_id,
                )
            )
            .where(
                user_role_templates.c.user_subject == recipient_subject,
                role_templates.c.is_active.is_(True),
                role_template_capabilities.c.capability_code == required_capability,
            )
        )
        if capability_exists is None:
            return False

    branch_exists = await session.scalar(
        select(1).where(
            user_branch_scopes.c.user_subject == recipient_subject,
            user_branch_scopes.c.branch_id == branch_id,
        )
    )
    if branch_exists is None:
        return False

    if warehouse_id is not None:
        warehouse_exists = await session.scalar(
            select(1).where(
                user_warehouse_scopes.c.user_subject == recipient_subject,
                user_warehouse_scopes.c.warehouse_id == warehouse_id,
            )
        )
        if warehouse_exists is None:
            return False

    return True


def _id(kind: str, source_id: UUID | str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:{kind}:{source_id}")


def _deep_link_token(notification_id: UUID) -> str:
    return str(_id("notification-deep-link", notification_id))


async def _record_effect(
    session: AsyncSession,
    notification_id: UUID,
    effect_type: str,
    source_type: str,
    source_id: UUID,
    payload: dict[str, Any] | None = None,
    device_registration_id: UUID | None = None,
) -> None:
    effect_event_id = _id(f"notification-effect:{effect_type}", f"{notification_id}:{source_id}")
    await session.execute(
        pg_insert(notification_effect_events)
        .values(
            effect_event_id=effect_event_id,
            notification_id=notification_id,
            effect_type=effect_type,
            source_type=source_type,
            source_id=source_id,
            device_registration_id=device_registration_id,
            payload=payload or {},
        )
        .on_conflict_do_nothing(
            index_elements=["notification_id", "effect_type", "source_type", "source_id"]
        )
    )


async def _ensure_delivery_rows(
    session: AsyncSession,
    notification_id: UUID,
    recipient_subject: str,
) -> list[UUID]:
    devices = list(
        (
            await session.execute(
                select(device_registrations.c.device_registration_id).where(
                    device_registrations.c.user_subject == recipient_subject,
                    device_registrations.c.is_active.is_(True),
                    device_registrations.c.expires_at > func.now(),
                )
            )
        ).scalars()
    )
    delivery_ids: list[UUID] = []
    for device_id in devices:
        delivery_id = uuid4()
        await session.execute(
            pg_insert(notification_deliveries)
            .values(
                delivery_id=delivery_id,
                notification_id=notification_id,
                device_registration_id=device_id,
                provider="noop",
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["notification_id", "device_registration_id"])
        )
        delivery_ids.append(delivery_id)
    return delivery_ids


async def _create_or_update_notification(
    session: AsyncSession,
    outbox_event_id: UUID,
    source_type: str,
    source_id: UUID,
    recipient_subject: str,
    notification_type: str,
    title: str,
    body: str,
    deep_link_path: str,
    branch_id: UUID,
    warehouse_id: UUID | None,
    required_capability: str | None,
    correlation_id: str,
) -> UUID:
    notification_id = _id(
        f"operational-notification:{notification_type}",
        f"{outbox_event_id}:{recipient_subject}",
    )
    deep_link_token = _deep_link_token(notification_id)

    insert_result = await session.execute(
        pg_insert(operational_notifications)
        .values(
            notification_id=notification_id,
            source_event_id=outbox_event_id,
            source_type=source_type,
            source_id=source_id,
            recipient_subject=recipient_subject,
            notification_type=notification_type,
            title=title,
            body=body,
            deep_link_path=deep_link_path,
            deep_link_token=deep_link_token,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            required_capability=required_capability,
            status="pending",
            correlation_id=correlation_id,
        )
        .on_conflict_do_nothing(
            index_elements=["source_event_id", "recipient_subject", "notification_type"]
        )
        .returning(operational_notifications.c.notification_id)
    )
    created = insert_result.scalar_one_or_none() is not None

    existing = (
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
    if existing is None:
        raise AppError(
            500,
            "notification_identity_missing",
            "Notification identity could not be established.",
        )

    if created:
        await _record_effect(
            session,
            notification_id=notification_id,
            effect_type="created",
            source_type="outbox_event",
            source_id=outbox_event_id,
        )

    await _ensure_delivery_rows(session, notification_id, recipient_subject)

    if existing["status"] == "pending":
        await session.execute(
            operational_notifications.update()
            .where(operational_notifications.c.notification_id == notification_id)
            .values(status="delivered")
        )
        await _record_effect(
            session,
            notification_id=notification_id,
            effect_type="delivered",
            source_type="outbox_event",
            source_id=outbox_event_id,
        )
    elif existing["status"] == "revoked":
        await _record_effect(
            session,
            notification_id=notification_id,
            effect_type="masked",
            source_type="outbox_event",
            source_id=outbox_event_id,
            payload={"reason": "notification_revoked"},
        )

    return notification_id


async def _handle_delivery_confirmed(
    session: AsyncSession,
    event: dict[str, Any],
) -> UUID:
    delivery_id = UUID(str(event["payload"]["delivery_id"]))
    confirmation_id = UUID(str(event["payload"]["confirmation_id"]))
    source = (
        (
            await session.execute(
                select(
                    delivery_dispatches.c.branch_id,
                    delivery_dispatches.c.warehouse_id,
                    delivery_state.c.assigned_to,
                )
                .join(
                    delivery_state,
                    delivery_state.c.delivery_id == delivery_dispatches.c.delivery_id,
                )
                .where(delivery_dispatches.c.delivery_id == delivery_id)
            )
        )
        .mappings()
        .one()
    )
    recipient = cast(str, source["assigned_to"])
    branch_id = cast(UUID, source["branch_id"])
    warehouse_id = cast(UUID | None, source["warehouse_id"])
    required_capability = NOTIFICATION_TYPE_CAPABILITIES["delivery_confirmation"]

    if not await _is_recipient_authorized(
        session,
        recipient,
        branch_id,
        warehouse_id,
        required_capability,
    ):
        raise AppError(
            403,
            "notification_recipient_unauthorized",
            "Assigned delivery user is not authorized to receive this notification.",
        )

    return await _create_or_update_notification(
        session=session,
        outbox_event_id=event["outbox_event_id"],
        source_type="delivery_confirmation",
        source_id=confirmation_id,
        recipient_subject=recipient,
        notification_type="delivery_confirmation",
        title="Delivery confirmed",
        body=f"Delivery {delivery_id} has been confirmed and a receipt issued.",
        deep_link_path=f"/deliveries/{delivery_id}",
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        required_capability=required_capability,
        correlation_id=event["correlation_id"],
    )


async def _handle_delivery_correction_posted(
    session: AsyncSession,
    event: dict[str, Any],
) -> UUID:
    correction_id = UUID(str(event["payload"]["correction_id"]))
    correction = (
        (
            await session.execute(
                select(
                    delivery_corrections.c.correction_id,
                    delivery_corrections.c.branch_id,
                    delivery_corrections.c.warehouse_id,
                    delivery_corrections.c.delivery_id,
                ).where(delivery_corrections.c.correction_id == correction_id)
            )
        )
        .mappings()
        .one()
    )
    delivery_id = cast(UUID, correction["delivery_id"])
    dispatch = (
        (
            await session.execute(
                select(
                    delivery_dispatches.c.initial_assignee_subject,
                ).where(delivery_dispatches.c.delivery_id == delivery_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    recipient = cast(str, dispatch["initial_assignee_subject"]) if dispatch is not None else None
    if recipient is None:
        raise AppError(
            404,
            "delivery_not_found",
            "Delivery for correction notification not found.",
        )

    branch_id = cast(UUID, correction["branch_id"])
    warehouse_id = cast(UUID | None, correction["warehouse_id"])
    required_capability = NOTIFICATION_TYPE_CAPABILITIES["delivery_correction"]

    if not await _is_recipient_authorized(
        session,
        recipient,
        branch_id,
        warehouse_id,
        required_capability,
    ):
        raise AppError(
            403,
            "notification_recipient_unauthorized",
            "Assigned delivery user is not authorized to receive this correction notification.",
        )

    return await _create_or_update_notification(
        session=session,
        outbox_event_id=event["outbox_event_id"],
        source_type="delivery_correction",
        source_id=correction_id,
        recipient_subject=recipient,
        notification_type="delivery_correction",
        title="Delivery corrected",
        body=f"Delivery {delivery_id} has been corrected. Review the updated receipt.",
        deep_link_path=f"/delivery-corrections/{correction_id}",
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        required_capability=required_capability,
        correlation_id=event["correlation_id"],
    )


async def create_notifications_for_event(
    session: AsyncSession,
    outbox_event_id: UUID,
) -> UUID:
    event = (
        (
            await session.execute(
                select(outbox_events).where(outbox_events.c.outbox_event_id == outbox_event_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if event is None:
        raise AppError(404, "outbox_event_not_found", "Outbox event not found.")

    prior = await session.scalar(
        select(outbox_handler_receipts.c.result_id).where(
            outbox_handler_receipts.c.outbox_event_id == outbox_event_id,
            outbox_handler_receipts.c.handler_name == HANDLER_NAME,
        )
    )
    if prior is not None:
        return cast(UUID, prior)

    event_type = event["event_type"]
    if event_type == "delivery.confirmed.v1":
        notification_id = await _handle_delivery_confirmed(session, dict(event))
    elif event_type == "delivery.correction.posted.v1":
        notification_id = await _handle_delivery_correction_posted(session, dict(event))
    else:
        raise AppError(
            400,
            "unsupported_outbox_event",
            f"Event type {event_type} is not supported for operational notifications.",
        )

    await session.execute(
        pg_insert(outbox_handler_receipts)
        .values(
            outbox_handler_receipt_id=_id("outbox-handler-notification", outbox_event_id),
            outbox_event_id=outbox_event_id,
            handler_name=HANDLER_NAME,
            result_id=notification_id,
        )
        .on_conflict_do_nothing(index_elements=["outbox_event_id", "handler_name"])
    )
    return notification_id
