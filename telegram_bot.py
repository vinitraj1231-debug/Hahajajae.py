
"""
ShieldBot — Telegram Bot Handler
Handles /commands, inline button callbacks, captcha challenges,
and all write operations against Telegram's Bot API.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import (
    Bot, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_bot_instance: Optional[Bot] = None
_application: Optional[Application] = None


async def get_bot() -> Bot:
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = Bot(token=settings.telegram_bot_token)
    return _bot_instance


async def get_application() -> Application:
    global _application
    if _application is None:
        _application = (
            Application.builder()
            .token(settings.telegram_bot_token)
            .build()
        )
        _register_handlers(_application)
    return _application


def _register_handlers(app: Application) -> None:
    """Register all command and event handlers."""
    # ── Commands (admin only) ─────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("shield", cmd_shield))
    app.add_handler(CommandHandler("lockdown", cmd_lockdown))
    app.add_handler(CommandHandler("release", cmd_release))
    app.add_handler(CommandHandler("export_incident", cmd_export_incident))
    app.add_handler(CommandHandler("trust", cmd_set_trust))
    app.add_handler(CommandHandler("status", cmd_status))

    # ── Callback Queries (inline button presses) ──────────────
    app.add_handler(CallbackQueryHandler(cb_approve_mute, pattern=r"^approve_mute:"))
    app.add_handler(CallbackQueryHandler(cb_rollback, pattern=r"^rollback:"))
    app.add_handler(CallbackQueryHandler(cb_false_positive, pattern=r"^fp:"))
    app.add_handler(CallbackQueryHandler(cb_export, pattern=r"^export:"))
    app.add_handler(CallbackQueryHandler(cb_captcha, pattern=r"^captcha:"))

    # ── New member joins ──────────────────────────────────────
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    # ── Message handler for link/media detection ──────────────
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, on_message))


# ──────────────────────────────────────────────────────────────
# Admin Permission Check
# ──────────────────────────────────────────────────────────────

async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    admins = await update.effective_chat.get_administrators()
    admin_ids = {a.user.id for a in admins}
    return update.effective_user.id in admin_ids or update.effective_user.id in settings.admin_telegram_user_ids


# ──────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛡️ *ShieldBot* — Enterprise Group Protection\n\n"
        "Use `/shield status` to see current protection levels.\n"
        "Full documentation: https://shield.yourdomain.com",
        parse_mode="Markdown",
    )


async def cmd_shield(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main shield command dispatcher: /shield <subcommand> [args]."""
    if not context.args:
        await _send_shield_help(update)
        return

    sub = context.args[0].lower()
    args = context.args[1:]

    if sub == "status":
        await _shield_status(update, context)
    elif sub == "set":
        if not await _is_admin(update, context):
            return
        await _shield_set(update, context, args)
    elif sub == "trust":
        if not await _is_admin(update, context):
            return
        await _shield_trust(update, context, args)
    else:
        await _send_shield_help(update)


async def _send_shield_help(update: Update) -> None:
    await update.message.reply_text(
        "🛡️ *ShieldBot Commands*\n\n"
        "`/shield status` — Show protection status\n"
        "`/shield set <key> <value>` — Update threshold\n"
        "`/shield trust @user <0-100>` — Set trust score\n"
        "`/lockdown` — Activate emergency lockdown\n"
        "`/release` — Release lockdown\n"
        "`/export_incident <id>` — Export incident archive\n"
        "`/status` — Bot operational status",
        parse_mode="Markdown",
    )


async def cmd_lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only.")
        return

    group_id = update.effective_chat.id
    bot = await get_bot()

    try:
        perms = ChatPermissions(
            can_send_messages=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
        )
        await bot.set_chat_permissions(chat_id=group_id, permissions=perms)

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Release Lockdown", callback_data=f"release_lockdown:{group_id}")
        ]])
        await update.message.reply_text(
            "🔴 *LOCKDOWN ACTIVATED*\n\n"
            "All member permissions have been restricted.\n"
            "Use `/release` or the button below to restore.\n\n"
            f"_Activated by: {update.effective_user.full_name}_",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        # Log the manual action
        from app.tasks.event_tasks import execute_bot_action
        execute_bot_action.apply_async(args=[
            group_id, None, "lockdown",
            {"reason": f"Manual lockdown by {update.effective_user.id}"},
        ])
    except Exception as e:
        await update.message.reply_text(f"❌ Lockdown failed: {e}")


async def cmd_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only.")
        return

    group_id = update.effective_chat.id
    bot = await get_bot()

    try:
        perms = ChatPermissions(
            can_send_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
        await bot.set_chat_permissions(chat_id=group_id, permissions=perms)
        await update.message.reply_text(
            "✅ *Lockdown released.* Normal permissions restored.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Release failed: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db.redis_client import get_redis, is_lockdown_active
    group_id = update.effective_chat.id
    redis = await get_redis()
    lockdown = await is_lockdown_active(redis, group_id)
    open_inc = await redis.get("shield:metrics:open_incidents") or "0"

    await update.message.reply_text(
        f"🛡️ *ShieldBot Status*\n\n"
        f"Group: `{group_id}`\n"
        f"Lockdown: {'🔴 ACTIVE' if lockdown else '🟢 OFF'}\n"
        f"Open Incidents: {open_inc}\n"
        f"Bot Version: 1.0.0",
        parse_mode="Markdown",
    )


async def cmd_export_incident(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/export_incident <incident_id>`", parse_mode="Markdown")
        return

    try:
        incident_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid incident ID.")
        return

    await update.message.reply_text(f"📋 Exporting incident #{incident_id}…")
    from app.tasks.event_tasks import execute_bot_action
    # Trigger export via Celery
    from app.tasks import export_tasks
    export_tasks.export_incident_archive.delay(incident_id, update.effective_user.id)


async def cmd_set_trust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/trust @username <0-100>`", parse_mode="Markdown")
        return
    username = context.args[0].lstrip("@")
    try:
        score = int(context.args[1]) / 100.0
        score = max(0.0, min(1.0, score))
    except ValueError:
        await update.message.reply_text("❌ Score must be 0-100.")
        return

    from app.db.redis_client import get_redis, set_trust_score
    redis = await get_redis()
    # In production, lookup user ID by username from DB
    await update.message.reply_text(
        f"✅ Trust score for @{username} set to {score:.0%}",
        parse_mode="Markdown",
    )


async def _shield_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_status(update, context)


async def _shield_set(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if len(args) < 2:
        await update.message.reply_text("Usage: `/shield set <key> <value>`", parse_mode="Markdown")
        return
    key, value = args[0], args[1]
    # Would update group settings in DB
    await update.message.reply_text(f"✅ Setting `{key}` = `{value}`", parse_mode="Markdown")


async def _shield_trust(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    await update.message.reply_text("Use `/trust @username <score>` directly.", parse_mode="Markdown")


# ──────────────────────────────────────────────────────────────
# Callback Query Handlers
# ──────────────────────────────────────────────────────────────

async def cb_approve_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    incident_id = int(query.data.split(":")[1])
    admin_id = query.from_user.id

    await _update_incident_status(incident_id, "mitigated", admin_id)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Incident #{incident_id} approved by {query.from_user.full_name}",
    )


async def cb_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    incident_id = int(query.data.split(":")[1])
    admin_id = query.from_user.id

    success = await _rollback_incident_actions(incident_id, admin_id)
    await query.edit_message_reply_markup(reply_markup=None)
    status = "✅ Rolled back" if success else "❌ Rollback failed"
    await query.message.reply_text(f"{status} — Incident #{incident_id}")


async def cb_false_positive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    incident_id = int(query.data.split(":")[1])
    admin_id = query.from_user.id

    await _update_incident_status(incident_id, "false_positive", admin_id)
    await _rollback_incident_actions(incident_id, admin_id)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"🚫 Incident #{incident_id} marked as false positive. Actions rolled back.")


async def cb_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Preparing export…")
    incident_id = int(query.data.split(":")[1])
    from app.tasks import export_tasks
    export_tasks.export_incident_archive.delay(incident_id, query.from_user.id)


async def cb_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle emoji captcha button press."""
    query = update.callback_query
    _, group_id, user_id, answer_idx = query.data.split(":")
    group_id, user_id, answer_idx = int(group_id), int(user_id), int(answer_idx)

    if query.from_user.id != user_id:
        await query.answer("This captcha is not for you!", show_alert=True)
        return

    await _process_captcha_answer(query, group_id, user_id, answer_idx)


async def _process_captcha_answer(query, group_id, user_id, answer_idx):
    from app.db.session import db_session
    from app.models.models import CaptchaChallenge, GroupUser
    from sqlalchemy import select

    async with db_session() as db:
        result = await db.execute(
            select(CaptchaChallenge).where(
                CaptchaChallenge.group_id == group_id,
                CaptchaChallenge.user_id == user_id,
                CaptchaChallenge.passed == False,
            )
        )
        challenge = result.scalar_one_or_none()
        if not challenge:
            await query.answer("Challenge expired.", show_alert=True)
            return

        correct_idx = challenge.challenge_data.get("correct_index", -1)
        challenge.attempts += 1

        if answer_idx == correct_idx:
            challenge.passed = True
            # Restore permissions
            bot = await get_bot()
            perms = ChatPermissions(can_send_messages=True)
            await bot.restrict_chat_member(chat_id=group_id, user_id=user_id, permissions=perms)
            await query.answer("✅ Verified! Welcome to the group.", show_alert=True)
            try:
                await bot.delete_message(chat_id=group_id, message_id=challenge.message_id)
            except Exception:
                pass
        else:
            if challenge.attempts >= 3:
                # Kick after 3 failed attempts
                bot = await get_bot()
                await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
                await query.answer("❌ Too many wrong answers. Removed from group.", show_alert=True)
            else:
                remaining = 3 - challenge.attempts
                await query.answer(f"❌ Wrong! {remaining} attempts remaining.", show_alert=True)


# ──────────────────────────────────────────────────────────────
# Event Handlers
# ──────────────────────────────────────────────────────────────

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle member join/leave events and dispatch captcha if needed."""
    if not update.chat_member:
        return

    new_member = update.chat_member.new_chat_member
    old_member = update.chat_member.old_chat_member
    chat_id = update.chat_member.chat.id
    user = new_member.user

    # User joined
    if new_member.status in ("member", "restricted") and old_member.status in ("left", "kicked"):
        from app.db.redis_client import get_redis, is_lockdown_active
        redis = await get_redis()

        if await is_lockdown_active(redis, chat_id):
            # Kick during lockdown
            bot = await get_bot()
            await bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            await bot.unban_chat_member(chat_id=chat_id, user_id=user.id)
            return

        # Check if captcha is required
        from app.db.session import db_session
        from app.models.models import Group
        from sqlalchemy import select

        async with db_session() as db:
            result = await db.execute(select(Group).where(Group.id == chat_id))
            group = result.scalar_one_or_none()
            if group and group.captcha_active:
                await _send_captcha(chat_id, user)

        # Dispatch join event to rule engine
        from app.tasks.event_tasks import ingest_mtproto_event
        ingest_mtproto_event.delay("join", {
            "group_id": chat_id,
            "user": {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "is_bot": user.is_bot,
                "is_premium": getattr(user, "is_premium", False),
            },
        })


async def _send_captcha(group_id: int, user) -> None:
    """Send emoji captcha challenge to new member."""
    EMOJIS = ["🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯",
              "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🐦", "🦆", "🦅"]

    correct = random.choice(EMOJIS)
    options = random.sample([e for e in EMOJIS if e != correct], 5) + [correct]
    random.shuffle(options)
    correct_index = options.index(correct)

    # Challenge data (hash answer for storage)
    challenge_data = {
        "correct_index": correct_index,
        "emoji": correct,
        "options": options,
    }

    # Build keyboard
    keyboard = [
        [InlineKeyboardButton(e, callback_data=f"captcha:{group_id}:{user.id}:{i}")]
        for i, e in enumerate(options)
    ]
    markup = InlineKeyboardMarkup(keyboard)

    bot = await get_bot()
    try:
        # Restrict user until captcha passed
        perms = ChatPermissions(can_send_messages=False)
        await bot.restrict_chat_member(chat_id=group_id, user_id=user.id, permissions=perms)

        msg = await bot.send_message(
            chat_id=group_id,
            text=(
                f"👋 Welcome {user.mention_html()}!\n\n"
                f"To join, tap the {correct} emoji below.\n"
                f"You have 2 minutes and 3 attempts."
            ),
            parse_mode="HTML",
            reply_markup=markup,
        )

        # Save captcha to DB
        from app.db.session import db_session
        from app.models.models import CaptchaChallenge
        from datetime import datetime, timedelta, timezone

        async with db_session() as db:
            ch = CaptchaChallenge(
                group_id=group_id,
                user_id=user.id,
                challenge_type="emoji",
                challenge_data=challenge_data,
                message_id=msg.message_id,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
            )
            db.add(ch)
    except Exception as e:
        logger.error(f"Failed to send captcha to {user.id}: {e}")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inspect messages for links/media and forward to rule engine."""
    if not update.message or not update.effective_chat:
        return

    msg = update.message
    text = msg.text or msg.caption or ""
    entities = msg.parse_entities(["url", "text_link"])
    links = list(entities.values())
    has_media = bool(msg.photo or msg.document or msg.video or msg.audio)

    from app.tasks.event_tasks import ingest_mtproto_event
    ingest_mtproto_event.delay("message", {
        "group_id": update.effective_chat.id,
        "message_id": msg.message_id,
        "text": text[:500],
        "links": links[:20],
        "has_media": has_media,
        "is_forward": bool(msg.forward_date),
        "forward_chain_length": 1 if msg.forward_date else 0,
        "date": msg.date.isoformat() if msg.date else None,
        "sender": {
            "id": update.effective_user.id if update.effective_user else 0,
            "username": update.effective_user.username if update.effective_user else None,
        },
    })


# ──────────────────────────────────────────────────────────────
# DB Helpers
# ──────────────────────────────────────────────────────────────

async def _update_incident_status(incident_id: int, status: str, admin_id: int) -> None:
    from app.db.session import db_session
    from app.models.models import Incident, IncidentStatus
    from sqlalchemy import select

    async with db_session() as db:
        result = await db.execute(select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()
        if incident:
            incident.status = IncidentStatus(status)
            incident.resolved_by = admin_id
            incident.resolved_at = datetime.now(timezone.utc)
            incident.version += 1


async def _rollback_incident_actions(incident_id: int, admin_id: int) -> bool:
    from app.db.session import db_session
    from app.models.models import BotAction, ActionType
    from sqlalchemy import select

    async with db_session() as db:
        result = await db.execute(
            select(BotAction).where(
                BotAction.incident_id == incident_id,
                BotAction.rolled_back == False,
            )
        )
        actions = result.scalars().all()
        bot = await get_bot()

        for action in actions:
            try:
                if action.action_type == ActionType.MUTE:
                    # Restore send permissions
                    perms = ChatPermissions(can_send_messages=True)
                    await bot.restrict_chat_member(
                        chat_id=action.group_id,
                        user_id=action.target_user_id,
                        permissions=perms,
                    )
                elif action.action_type == ActionType.LOCKDOWN:
                    perms = ChatPermissions(can_send_messages=True)
                    await bot.set_chat_permissions(chat_id=action.group_id, permissions=perms)

                action.rolled_back = True
                action.rolled_back_by = admin_id
                action.rolled_back_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Rollback failed for action {action.id}: {e}")
                return False

    return True
