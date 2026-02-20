"""
ShieldBot — Deterministic Rule Engine
Primary decision layer; ML is secondary prioritization only.
All thresholds are per-group tunable (fall back to global defaults).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import Levenshtein
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.redis_client import (
    add_recent_username,
    get_recent_usernames,
    sliding_window_add_and_count,
    sliding_window_count,
)
from app.models.models import IncidentType, Severity

logger = logging.getLogger(__name__)
settings = get_settings()


# ──────────────────────────────────────────────────────────────
# Result Dataclasses
# ──────────────────────────────────────────────────────────────

@dataclass
class ThreatSignal:
    """A single fired rule."""
    rule_id: str
    description: str
    severity: Severity
    incident_type: IncidentType
    evidence: Dict[str, Any] = field(default_factory=dict)
    score_contribution: float = 0.0


@dataclass
class RuleEngineResult:
    """Aggregated output of rule evaluation for an event."""
    signals: List[ThreatSignal] = field(default_factory=list)
    max_severity: Severity = Severity.INFO
    should_mute: bool = False
    should_kick: bool = False
    should_lockdown: bool = False
    should_enable_captcha: bool = False
    should_slow_mode: bool = False
    slow_mode_delay: int = 30  # seconds
    rule_score: float = 0.0  # deterministic threat score [0,1]

    def add_signal(self, signal: ThreatSignal) -> None:
        self.signals.append(signal)
        if signal.severity.value > self.max_severity.value:
            self.max_severity = signal.severity
        self.rule_score = min(1.0, self.rule_score + signal.score_contribution)


# ──────────────────────────────────────────────────────────────
# Group Settings Helper
# ──────────────────────────────────────────────────────────────

def _get(settings_dict: Dict, key: str, default: Any) -> Any:
    return settings_dict.get(key, default)


# ──────────────────────────────────────────────────────────────
# Rule Engine
# ──────────────────────────────────────────────────────────────

class RuleEngine:
    def __init__(self, redis: Redis):
        self.redis = redis

    # ── Join Events ───────────────────────────────────────────

    async def evaluate_join(
        self,
        group_id: int,
        user_id: int,
        username: Optional[str],
        account_age_days: Optional[float],
        group_settings: Dict[str, Any],
    ) -> RuleEngineResult:
        result = RuleEngineResult()
        cfg = group_settings

        # ── Rule 1: Rapid Join Spike ──────────────────────────
        join_limit = _get(cfg, "join_spike_count", settings.default_join_spike_count)
        join_window = _get(cfg, "join_spike_window_s", settings.default_join_spike_window_s)

        join_key = f"shield:joins:{group_id}"
        join_count = await sliding_window_add_and_count(
            self.redis, join_key, join_window, member_suffix=str(user_id)
        )

        if join_count >= join_limit:
            result.add_signal(ThreatSignal(
                rule_id="JOIN_SPIKE",
                description=f"Rapid join spike: {join_count} joins in {join_window}s (limit: {join_limit})",
                severity=Severity.CRITICAL,
                incident_type=IncidentType.RAID,
                evidence={"join_count": join_count, "window_s": join_window},
                score_contribution=0.7,
            ))
            result.should_lockdown = True
            result.should_enable_captcha = True

        # ── Rule 2: New Account Age ───────────────────────────
        new_acc_days = _get(cfg, "new_account_age_days", settings.default_new_account_age_days)
        new_acc_ratio = _get(cfg, "new_account_ratio", settings.default_new_account_ratio)
        new_ratio_key = f"shield:newratio:{group_id}"

        if account_age_days is not None and account_age_days < new_acc_days:
            new_count = await sliding_window_add_and_count(
                self.redis, new_ratio_key, join_window, member_suffix=f"new:{user_id}"
            )
            total_count = join_count or 1
            ratio = new_count / total_count

            if ratio >= new_acc_ratio and join_count >= 5:
                result.add_signal(ThreatSignal(
                    rule_id="NEW_ACCOUNT_RATIO",
                    description=f"High new-account ratio: {ratio:.0%} of recent joiners have accounts < {new_acc_days} days old",
                    severity=Severity.HIGH,
                    incident_type=IncidentType.RAID,
                    evidence={"ratio": ratio, "new_count": new_count, "total_count": total_count},
                    score_contribution=0.4,
                ))
                result.should_enable_captcha = True

        # ── Rule 3: Username Similarity (botnet detection) ────
        lev_threshold = _get(cfg, "levenshtein_threshold", settings.default_levenshtein_threshold)
        if username:
            await add_recent_username(self.redis, group_id, username, ttl=join_window * 2)
            recent = await get_recent_usernames(self.redis, group_id)

            similar_count = sum(
                1 for u in recent
                if u != username and Levenshtein.distance(username, u) <= lev_threshold
            )
            if similar_count >= 3:
                result.add_signal(ThreatSignal(
                    rule_id="USERNAME_SIMILARITY",
                    description=f"Botnet suspected: {similar_count} joiners with similar usernames (Levenshtein ≤ {lev_threshold})",
                    severity=Severity.HIGH,
                    incident_type=IncidentType.BOTNET,
                    evidence={"similar_count": similar_count, "username": username, "threshold": lev_threshold},
                    score_contribution=0.45,
                ))

        return result

    # ── Message Events ────────────────────────────────────────

    async def evaluate_message(
        self,
        group_id: int,
        user_id: int,
        has_links: bool,
        has_media: bool,
        link_count: int,
        is_forward: bool,
        forward_chain_length: int,
        is_new_user: bool,
        group_settings: Dict[str, Any],
    ) -> RuleEngineResult:
        result = RuleEngineResult()
        cfg = group_settings

        # ── Rule 4: Message Rate Spike ────────────────────────
        msg_limit = _get(cfg, "msg_spike_count", settings.default_msg_spike_count)
        msg_window = _get(cfg, "msg_spike_window_s", settings.default_msg_spike_window_s)

        msg_key = f"shield:msgs:{group_id}"
        msg_count = await sliding_window_add_and_count(
            self.redis, msg_key, msg_window, member_suffix=str(user_id)
        )

        if msg_count >= msg_limit:
            result.add_signal(ThreatSignal(
                rule_id="MSG_SPIKE",
                description=f"Message flood: {msg_count} messages in {msg_window}s (limit: {msg_limit})",
                severity=Severity.HIGH,
                incident_type=IncidentType.SPAM_FLOOD,
                evidence={"msg_count": msg_count, "window_s": msg_window},
                score_contribution=0.5,
            ))
            result.should_slow_mode = True
            result.slow_mode_delay = 30

        # ── Rule 5: Link Flood ────────────────────────────────
        if has_links:
            link_limit = _get(cfg, "link_flood_count", settings.default_link_flood_count)
            link_window = _get(cfg, "link_flood_window_s", settings.default_link_flood_window_s)

            link_key = f"shield:links:{group_id}"
            link_count_window = await sliding_window_add_and_count(
                self.redis, link_key, link_window, member_suffix=f"{user_id}:{link_count}"
            )

            if link_count_window >= link_limit:
                result.add_signal(ThreatSignal(
                    rule_id="LINK_FLOOD",
                    description=f"Link flood: {link_count_window} link-events from different users in {link_window}s",
                    severity=Severity.HIGH,
                    incident_type=IncidentType.PHISHING,
                    evidence={"link_count": link_count_window, "window_s": link_window},
                    score_contribution=0.55,
                ))
                result.should_mute = True

        # ── Rule 6: Media Flood (new users only) ──────────────
        if has_media and is_new_user:
            media_limit = _get(cfg, "media_flood_count", settings.default_media_flood_count)
            media_window = _get(cfg, "media_flood_window_s", settings.default_media_flood_window_s)

            media_key = f"shield:media:{group_id}"
            media_count = await sliding_window_add_and_count(
                self.redis, media_key, media_window, member_suffix=str(user_id)
            )

            if media_count >= media_limit:
                result.add_signal(ThreatSignal(
                    rule_id="MEDIA_FLOOD",
                    description=f"Media flood: {media_count} media posts from new users in {media_window}s",
                    severity=Severity.HIGH,
                    incident_type=IncidentType.SPAM_FLOOD,
                    evidence={"media_count": media_count, "window_s": media_window},
                    score_contribution=0.5,
                ))
                result.should_mute = True

        # ── Rule 7: Suspicious Forward Chain ─────────────────
        if is_forward and forward_chain_length > 5:
            result.add_signal(ThreatSignal(
                rule_id="LONG_FORWARD_CHAIN",
                description=f"Suspicious mass-forward: chain length {forward_chain_length}",
                severity=Severity.WARNING,
                incident_type=IncidentType.SPAM_FLOOD,
                evidence={"chain_length": forward_chain_length},
                score_contribution=0.25,
            ))

        return result

    # ── Report Events ─────────────────────────────────────────

    async def evaluate_report_burst(
        self,
        group_id: int,
        target_user_id: int,
        group_settings: Dict[str, Any],
    ) -> RuleEngineResult:
        result = RuleEngineResult()
        cfg = group_settings

        report_limit = _get(cfg, "report_burst_count", settings.default_report_burst_count)
        report_window = _get(cfg, "report_burst_window_s", settings.default_report_burst_window_s)

        report_key = f"shield:reports:{group_id}:{target_user_id}"
        report_count = await sliding_window_add_and_count(
            self.redis, report_key, report_window, member_suffix="report"
        )

        if report_count >= report_limit:
            result.add_signal(ThreatSignal(
                rule_id="REPORT_BURST",
                description=f"Report burst: {report_count} reports against user {target_user_id} in {report_window}s",
                severity=Severity.CRITICAL,
                incident_type=IncidentType.REPORT_BURST,
                evidence={
                    "report_count": report_count,
                    "target_user_id": target_user_id,
                    "window_s": report_window,
                },
                score_contribution=0.65,
            ))
            result.should_mute = True

        return result


# ──────────────────────────────────────────────────────────────
# Action Recommender
# ──────────────────────────────────────────────────────────────

@dataclass
class ActionRecommendation:
    action: str  # "mute", "kick", "ban", "slow_mode", "lockdown", "captcha", "none"
    severity: Severity
    reason: str
    rollback_after_minutes: Optional[int] = None
    signals: List[str] = field(default_factory=list)


def recommend_action(
    rule_result: RuleEngineResult,
    ml_risk_score: float,
    group_settings: Dict[str, Any],
) -> ActionRecommendation:
    """
    Combine rule engine output + ML score into a final action recommendation.
    Uses staged escalation: mute -> kick -> ban.
    Never auto-bans; always prefers reversible actions.
    """
    cfg = group_settings
    combined_score = (rule_result.rule_score * 0.7) + (ml_risk_score * 0.3)
    signal_ids = [s.rule_id for s in rule_result.signals]

    if rule_result.should_lockdown:
        return ActionRecommendation(
            action="lockdown",
            severity=Severity.CRITICAL,
            reason=f"Automatic lockdown triggered by: {', '.join(signal_ids)}",
            rollback_after_minutes=_get(cfg, "lockdown_duration_minutes", 15),
            signals=signal_ids,
        )

    if rule_result.should_mute or combined_score >= settings.risk_auto_mute_threshold:
        mute_mins = _get(cfg, "mute_duration_minutes", settings.default_mute_duration_minutes)
        return ActionRecommendation(
            action="mute",
            severity=rule_result.max_severity,
            reason=f"Auto-mute: rules={signal_ids}, score={combined_score:.2f}",
            rollback_after_minutes=mute_mins,
            signals=signal_ids,
        )

    if combined_score >= settings.risk_escalate_threshold:
        return ActionRecommendation(
            action="flag_for_review",
            severity=rule_result.max_severity,
            reason=f"Risk score {combined_score:.2f} above escalation threshold",
            signals=signal_ids,
        )

    return ActionRecommendation(
        action="none",
        severity=Severity.INFO,
        reason="Below action threshold",
        signals=signal_ids,
    )

