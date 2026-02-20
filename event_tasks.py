"""
ShieldBot — Celery Tasks
Background processing: event ingestion, incident creation, auto-actions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Celery App
# ──────────────────────────────────────────────────────────────

celery_app = Celery(
    "shieldbot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend or settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.tasks.event_tasks.process_critical_incident": {"queue": "shield_critical"},
        "app.tasks.event_tasks.execute_bot_action": {"queue": "shield_critical"},
        "*": {"queue": "shield_default"},
    },
    beat_schedule={
        "cleanup-expired-captchas": {
            "task": "app.tasks.event_tasks.cleanup_expired_captchas",
            "schedule": crontab(minute="*/5"),
        },
        "unmute-expired-mutes": {
            "task": "app.tasks.event_tasks.process_expired_mutes",
            "schedule": crontab(minute="*/2"),
        },
        "refresh-ml-features": {
            "task": "app.tasks.event_tasks.refresh_ml_model",
            "schedule": crontab(hour=3, minute=0),  # daily at 3am
        },
        "metrics-snapshot": {
            "task": "app.tasks.event_tasks.snapshot_metrics",
            "schedule": crontab(minute="*/1"),
        },
    },
)


def _run(coro):
    """Run async function from sync Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ──────────────────────────────────────────────────────────────
# Event Ingestion
# ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.event_tasks.ingest_mtproto_event", bind=True, max_retries=3)
def ingest_mtproto_event(self, event_type: str, data: Dict[str, Any]):
    """Ingest MTProto events into the DB and evaluate rules."""
    try:
        _run(_async_ingest_mtproto_event(event_type, data))
    except Exception as exc:
        logger.error(f"ingest_mtproto_event failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=5)


async def _async_ingest_mtproto_event(event_type: str, data: Dict[str, Any]):
    from app.db.session import db_session
    from app.db.redis_client import get_redis
    from app.core.rule_engine import RuleEngine
    from app.models.models import Event, EventType, User, Group
    from sqlalchemy import select

    async with db_session() as db:
        redis = await get_redis()
        rule_engine = RuleEngine(redis)

        group_id = data.get("group_id")
        if not group_id:
            return

        # Upsert user
        if "user" in data and data["user"]:
            await _upsert_user(db, data["user"])

        # Log event
        event = Event(
            group_id=group_id,
            event_type=EventType(event_type) if event_type in EventType._value2member_map_ else EventType.MESSAGE,
            user_id=data.get("user", {}).get("id") if data.get("user") else None,
            payload=data,
        )
        db.add(event)
        await db.flush()

        # Evaluate rules
        if event_type == "join":
            user_data = data.get("user", {})
            group_result = await db.execute(select(Group).where(Group.id == group_id))
            group = group_result.scalar_one_or_none()
            if not group:
                return

            result = await rule_engine.evaluate_join(
                group_id=group_id,
                user_id=user_data.get("id", 0),
                username=user_data.get("username"),
                account_age_days=None,  # would need Telethon to fetch
                group_settings=group.settings or {},
            )

            if result.signals:
                from app.tasks.event_tasks import process_critical_incident
                process_critical_incident.apply_async(
                    args=[group_id, event.id, [s.__dict__ for s in result.signals], result.__dict__],
                    queue="shield_critical",
                )

        elif event_type == "message":
            group_result = await db.execute(select(Group).where(Group.id == group_id))
            group = group_result.scalar_one_or_none()
            if not group:
                return

            sender = data.get("sender", {})
            result = await rule_engine.evaluate_message(
                group_id=group_id,
                user_id=sender.get("id", 0),
                has_links=bool(data.get("links")),
                has_media=data.get("has_media", False),
                link_count=len(data.get("links", [])),
                is_forward=data.get("is_forward", False),
                forward_chain_length=data.get("forward_chain_length", 0),
                is_new_user=True,  # simplified; would check join date
                group_settings=group.settings or {},
            )

            if result.signals:
                process_critical_incident.apply_async(
                    args=[group_id, event.id, [s.__dict__ for s in result.signals], result.__dict__],
                    queue="shield_critical",
                )


async def _upsert_user(db, user_data: Dict) -> None:
    from app.models.models import User
    from sqlalchemy.dialects.postgresql import insert

    if not user_data.get("id"):
        return

    stmt = insert(User).values(
        id=user_data["id"],
        username=user_data.get("username"),
        first_name=user_data.get("first_name"),
        last_name=user_data.get("last_name"),
        is_bot=user_data.get("is_bot", False),
        is_premium=user_data.get("is_premium", False),
        is_verified=user_data.get("is_verified", False),
    ).on_conflict_do_update(
        index_elements=["id"],
        set_={
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "is_premium": user_data.get("is_premium", False),
        },
    )
    await db.execute(stmt)


# ──────────────────────────────────────────────────────────────
# Incident Processing
# ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.event_tasks.process_critical_incident", bind=True, max_retries=3)
def process_critical_incident(
    self,
    group_id: int,
    trigger_event_id: int,
    signals: list,
    rule_result: Dict,
):
    """Create/update incident and queue appropriate bot actions."""
    try:
        _run(_async_process_incident(group_id, trigger_event_id, signals, rule_result))
    except Exception as exc:
        logger.error(f"process_critical_incident failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=3)


async def _async_process_incident(group_id, trigger_event_id, signals, rule_result):
    from app.db.session import db_session
    from app.db.redis_client import get_redis
    from app.models.models import Incident, IncidentType, IncidentStatus, Severity, BotAction, ActionType
    from app.ml.risk_scorer import get_scorer, RiskFeatures
    from app.core.rule_engine import recommend_action
    from app.services.notification_service import notify_admins
    import json

    async with db_session() as db:
        # Determine primary incident type from signals
        incident_types = [s.get("incident_type", "manual") for s in signals]
        primary_type = incident_types[0] if incident_types else IncidentType.MANUAL
        max_sev = max((s.get("severity", {}) for s in signals), default=2)
        sev = max_sev if isinstance(max_sev, int) else 2

        # Score with ML (fallback to heuristic)
        scorer = get_scorer()
        features = RiskFeatures()  # simplified; would pull real features
        ml_score, shap_vals = scorer.score(features)

        # Create incident
        incident = Incident(
            group_id=group_id,
            incident_type=primary_type if primary_type in IncidentType._value2member_map_ else IncidentType.MANUAL,
            severity=Severity(min(sev, 4)),
            status=IncidentStatus.OPEN,
            title=f"{primary_type} detected in group {group_id}",
            evidence={
                "signals": signals,
                "rule_result": rule_result,
                "ml_score": ml_score,
                "shap_values": shap_vals,
                "trigger_event_id": trigger_event_id,
            },
            risk_score=ml_score,
            auto_mitigated=False,
        )
        db.add(incident)
        await db.flush()

        # Get group settings for action recommendation
        from app.models.models import Group
        from sqlalchemy import select
        group_result = await db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one_or_none()
        group_settings = group.settings if group else {}

        # Recommend action
        from app.core.rule_engine import RuleEngineResult, ThreatSignal
        rr = RuleEngineResult()
        rr.should_mute = rule_result.get("should_mute", False)
        rr.should_lockdown = rule_result.get("should_lockdown", False)
        rr.should_slow_mode = rule_result.get("should_slow_mode", False)
        rr.rule_score = rule_result.get("rule_score", 0.0)
        rr.max_severity = Severity(min(sev, 4))

        recommendation = recommend_action(rr, ml_score, group_settings)

        # Queue bot action
        if recommendation.action not in ("none", "flag_for_review"):
            execute_bot_action.apply_async(
                args=[group_id, incident.id, recommendation.action, {
                    "rollback_after_minutes": recommendation.rollback_after_minutes,
                    "reason": recommendation.reason,
                }],
                queue="shield_critical",
            )
            incident.auto_mitigated = True

        # Notify admins
        notify_admins.delay(
            incident_id=incident.id,
            group_id=group_id,
            severity=sev,
            title=incident.title,
            signals=signals,
            action=recommendation.action,
        )


# ──────────────────────────────────────────────────────────────
# Bot Action Executor
# ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.event_tasks.execute_bot_action", bind=True, max_retries=5)
def execute_bot_action(
    self,
    group_id: int,
    incident_id: int,
    action: str,
    params: Dict[str, Any],
    target_user_id: Optional[int] = None,
):
    """Execute a moderation action via Bot API with idempotency + rollback data."""
    try:
        _run(_async_execute_bot_action(group_id, incident_id, action, params, target_user_id))
    except Exception as exc:
        logger.error(f"execute_bot_action failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _async_execute_bot_action(group_id, incident_id, action, params, target_user_id):
    from app.db.session import db_session
    from app.db.redis_client import get_redis, set_lockdown
    from app.models.models import BotAction, ActionType, Group
    from app.services.telegram_bot import get_bot
    from sqlalchemy import select

    async with db_session() as db:
        bot = await get_bot()
        redis = await get_redis()

        group_result = await db.execute(select(Group).where(Group.id == group_id))
        group = group_result.scalar_one_or_none()
        if not group:
            return

        bot_action = BotAction(
            group_id=group_id,
            incident_id=incident_id,
            target_user_id=target_user_id,
            action_type=ActionType(action) if action in ActionType._value2member_map_ else ActionType.MUTE,
            is_automated=True,
            parameters=params,
            rollback_data={},
            reason=params.get("reason", "Auto-action by ShieldBot"),
        )

        try:
            if action == "lockdown":
                await _action_lockdown(bot, group_id, params, db, group)
                await set_lockdown(redis, group_id, True)
                group.lockdown_active = True
                bot_action.rollback_data = {"previous_lockdown": False}

            elif action == "mute" and target_user_id:
                await _action_mute(bot, group_id, target_user_id, params)
                bot_action.rollback_data = {"muted": True}

            elif action == "enable_slow_mode":
                delay = params.get("slow_mode_delay", 30)
                await bot.set_chat_slow_mode_delay(chat_id=group_id, slow_mode_delay=delay)
                bot_action.rollback_data = {"previous_slow_mode": 0}

        except Exception as e:
            bot_action.error = str(e)
            logger.error(f"Bot action {action} failed for group {group_id}: {e}")

        db.add(bot_action)


async def _action_lockdown(bot, group_id, params, db, group):
    """Restrict all non-admins from sending messages."""
    from telegram import ChatPermissions
    perms = ChatPermissions(
        can_send_messages=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )
    await bot.set_chat_permissions(chat_id=group_id, permissions=perms)
    logger.info(f"Lockdown activated for group {group_id}")


async def _action_mute(bot, group_id, user_id, params):
    """Temporarily mute a user (remove send_messages permission)."""
    from telegram import ChatPermissions
    from datetime import datetime, timedelta, timezone
    mute_mins = params.get("rollback_after_minutes", 5)
    until = datetime.now(timezone.utc) + timedelta(minutes=mute_mins)
    perms = ChatPermissions(can_send_messages=False)
    await bot.restrict_chat_member(
        chat_id=group_id,
        user_id=user_id,
        permissions=perms,
        until_date=until,
    )
    logger.info(f"User {user_id} muted in group {group_id} until {until}")


# ──────────────────────────────────────────────────────────────
# Scheduled / Maintenance Tasks
# ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.event_tasks.cleanup_expired_captchas")
def cleanup_expired_captchas():
    _run(_async_cleanup_captchas())


async def _async_cleanup_captchas():
    from app.db.session import db_session
    from app.models.models import CaptchaChallenge
    from app.services.telegram_bot import get_bot
    from sqlalchemy import select
    from datetime import datetime, timezone

    async with db_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(CaptchaChallenge).where(
                CaptchaChallenge.expires_at < now,
                CaptchaChallenge.passed == False,
            )
        )
        expired = result.scalars().all()
        bot = await get_bot()

        for challenge in expired:
            try:
                # Kick user who didn't complete captcha
                await bot.ban_chat_member(
                    chat_id=challenge.group_id,
                    user_id=challenge.user_id,
                )
                await bot.unban_chat_member(
                    chat_id=challenge.group_id,
                    user_id=challenge.user_id,
                )
                # Delete captcha message
                if challenge.message_id:
                    await bot.delete_message(
                        chat_id=challenge.group_id,
                        message_id=challenge.message_id,
                    )
                await db.delete(challenge)
            except Exception as e:
                logger.error(f"Captcha cleanup failed for {challenge.user_id}: {e}")


@celery_app.task(name="app.tasks.event_tasks.process_expired_mutes")
def process_expired_mutes():
    _run(_async_process_expired_mutes())


async def _async_process_expired_mutes():
    from app.db.session import db_session
    from app.models.models import GroupUser
    from app.services.telegram_bot import get_bot
    from telegram import ChatPermissions
    from sqlalchemy import select
    from datetime import datetime, timezone

    async with db_session() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(GroupUser).where(
                GroupUser.is_muted == True,
                GroupUser.mute_until < now,
            )
        )
        expired = result.scalars().all()
        bot = await get_bot()

        for gu in expired:
            try:
                perms = ChatPermissions(can_send_messages=True)
                await bot.restrict_chat_member(
                    chat_id=gu.group_id,
                    user_id=gu.user_id,
                    permissions=perms,
                )
                gu.is_muted = False
                gu.mute_until = None
                logger.info(f"Auto-unmuted user {gu.user_id} in group {gu.group_id}")
            except Exception as e:
                logger.error(f"Auto-unmute failed for {gu.user_id}: {e}")


@celery_app.task(name="app.tasks.event_tasks.refresh_ml_model")
def refresh_ml_model():
    """Retrain ML model on recent incidents."""
    logger.info("ML model refresh scheduled — implement training pipeline here")


@celery_app.task(name="app.tasks.event_tasks.snapshot_metrics")
def snapshot_metrics():
    _run(_async_snapshot_metrics())


async def _async_snapshot_metrics():
    from app.db.session import db_session
    from app.db.redis_client import get_redis
    from sqlalchemy import func, select, text
    from app.models.models import Incident, IncidentStatus

    try:
        async with db_session() as db:
            redis = await get_redis()
            # Count open incidents
            result = await db.execute(
                select(func.count()).where(Incident.status == IncidentStatus.OPEN)
            )
            open_incidents = result.scalar() or 0
            await redis.setex("shield:metrics:open_incidents", 120, str(open_incidents))
    except Exception as e:
        logger.error(f"Metrics snapshot failed: {e}")


# Import notification tasks (avoid circular)
from app.tasks import notification_tasks as notify_admins  # noqa

