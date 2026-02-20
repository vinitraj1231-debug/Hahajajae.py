"""
ShieldBot — SQLAlchemy ORM Models
All tables designed for tamper-proof evidence and audit trails.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey,
    Index, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────────────────────

class IncidentType(str, enum.Enum):
    RAID = "raid"
    REPORT_BURST = "report_burst"
    SPAM_FLOOD = "spam_flood"
    LINK_FLOOD = "link_flood"
    MEDIA_FLOOD = "media_flood"
    PHISHING = "phishing"
    BOTNET = "botnet"
    MANUAL = "manual"


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    MITIGATED = "mitigated"
    CLOSED = "closed"
    FALSE_POSITIVE = "false_positive"


class Severity(int, enum.Enum):
    INFO = 1
    WARNING = 2
    HIGH = 3
    CRITICAL = 4


class ActionType(str, enum.Enum):
    MUTE = "mute"
    UNMUTE = "unmute"
    BAN = "ban"
    UNBAN = "unban"
    KICK = "kick"
    DELETE_MESSAGE = "delete_message"
    ENABLE_SLOW_MODE = "enable_slow_mode"
    DISABLE_SLOW_MODE = "disable_slow_mode"
    ENABLE_JOIN_CAPTCHA = "enable_join_captcha"
    DISABLE_JOIN_CAPTCHA = "disable_join_captcha"
    LOCKDOWN = "lockdown"
    RELEASE_LOCKDOWN = "release_lockdown"
    RESTRICT_LINKS = "restrict_links"
    APPROVE_PENDING = "approve_pending"


class EventType(str, enum.Enum):
    JOIN = "join"
    LEAVE = "leave"
    MESSAGE = "message"
    EDITED_MESSAGE = "edited_message"
    DELETED_MESSAGE = "deleted_message"
    REPORT = "report"
    ADMIN_ACTION = "admin_action"
    BOT_ACTION = "bot_action"
    FORWARD = "forward"
    MEDIA = "media"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MOD = "mod"
    VIEWER = "viewer"


# ──────────────────────────────────────────────────────────────
# Core Tables
# ──────────────────────────────────────────────────────────────

class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lockdown_active: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped["User"] = relationship("User", foreign_keys=[owner_id], back_populates="owned_groups")
    events: Mapped[List["Event"]] = relationship("Event", back_populates="group")
    incidents: Mapped[List["Incident"]] = relationship("Incident", back_populates="group")
    bot_actions: Mapped[List["BotAction"]] = relationship("BotAction", back_populates="group")
    group_users: Mapped[List["GroupUser"]] = relationship("GroupUser", back_populates="group")

    __table_args__ = (
        Index("ix_groups_owner_id", "owner_id"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    has_profile_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    account_created: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # hashed for privacy
    fingerprint: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)  # behavioral fingerprint
    global_ban: Mapped[bool] = mapped_column(Boolean, default=False)
    global_ban_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owned_groups: Mapped[List["Group"]] = relationship("Group", foreign_keys="Group.owner_id", back_populates="owner")
    group_users: Mapped[List["GroupUser"]] = relationship("GroupUser", back_populates="user")
    events: Mapped[List["Event"]] = relationship("Event", back_populates="user")


class GroupUser(Base):
    """Per-group user state — trust score, mute state, join info."""
    __tablename__ = "group_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    mute_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    captcha_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    incident_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    group: Mapped["Group"] = relationship("Group", back_populates="group_users")
    user: Mapped["User"] = relationship("User", back_populates="group_users")

    __table_args__ = (
        Index("uq_group_users", "group_id", "user_id", unique=True),
    )


class Event(Base):
    """Immutable audit log of all Telegram events."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    risk_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_features: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    shap_values: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    evidence_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # S3/MinIO key
    evidence_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # sha256
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    group: Mapped["Group"] = relationship("Group", back_populates="events")
    user: Mapped[Optional["User"]] = relationship("User", back_populates="events")
    reports: Mapped[List["Report"]] = relationship("Report", back_populates="event")

    __table_args__ = (
        Index("ix_events_group_type_created", "group_id", "event_type", "created_at"),
    )


class Incident(Base):
    """Security incident — groups related events and actions."""
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    incident_type: Mapped[IncidentType] = mapped_column(Enum(IncidentType), index=True)
    severity: Mapped[Severity] = mapped_column(Enum(Severity))
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus), default=IncidentStatus.OPEN, index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    affected_users: Mapped[List[int]] = mapped_column(JSONB, default=list)  # list of user IDs
    risk_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    auto_mitigated: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # admin user ID
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    export_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)  # optimistic locking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    group: Mapped["Group"] = relationship("Group", back_populates="incidents")
    bot_actions: Mapped[List["BotAction"]] = relationship("BotAction", back_populates="incident")


class BotAction(Base):
    """Every automated or manual bot action with full rollback metadata."""
    __tablename__ = "bot_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    incident_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("incidents.id"), nullable=True, index=True)
    target_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType), index=True)
    is_automated: Mapped[bool] = mapped_column(Boolean, default=True)
    performed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # admin user ID
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    parameters: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)  # action params
    rollback_data: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)  # state before action
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    rolled_back_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_result: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    group: Mapped["Group"] = relationship("Group", back_populates="bot_actions")
    incident: Mapped[Optional["Incident"]] = relationship("Incident", back_populates="bot_actions")


class Report(Base):
    """User reports from within Telegram."""
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    reporter_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reported_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    event_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("events.id"), nullable=True)
    report_type: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    event: Mapped[Optional["Event"]] = relationship("Event", back_populates="reports")


class GlobalBlacklist(Base):
    """Cross-group global threat blacklist (opt-in sharing)."""
    __tablename__ = "global_blacklist"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[List[str]] = mapped_column(JSONB, default=list)
    reported_by_groups: Mapped[List[int]] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaptchaChallenge(Base):
    """Active captcha challenges for pending joiners."""
    __tablename__ = "captcha_challenges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    challenge_type: Mapped[str] = mapped_column(String(50))  # emoji, math, text
    challenge_data: Mapped[Dict] = mapped_column(JSONB)  # question + answer hash
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # bot message to delete
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DashboardUser(Base):
    """Web dashboard admin accounts."""
    __tablename__ = "dashboard_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Immutable audit trail for ALL actions in the system."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # dashboard user
    actor_type: Mapped[str] = mapped_column(String(50))  # "bot", "admin", "system"
    action: Mapped[str] = mapped_column(String(255), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
    )

