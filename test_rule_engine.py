"""
ShieldBot — Unit Tests
Tests for rule engine, ML scorer, and Redis helpers.
Run with: pytest tests/unit/ -v --cov=app
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import numpy as np


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeRedis:
    """Minimal in-memory Redis compatible with our usage."""

    def __init__(self):
        self._sorted_sets: Dict[str, Dict[str, float]] = {}
        self._kv: Dict[str, str] = {}
        self._sets: Dict[str, set] = {}

    async def zadd(self, key, mapping):
        self._sorted_sets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, min_val, max_val):
        if key in self._sorted_sets:
            min_f = float("-inf") if min_val == "-inf" else float(min_val)
            max_f = float("inf")  if max_val == "+inf" else float(max_val)
            self._sorted_sets[key] = {
                k: v for k, v in self._sorted_sets[key].items()
                if not (min_f <= v <= max_f)
            }

    async def zcard(self, key):
        return len(self._sorted_sets.get(key, {}))

    async def zcount(self, key, min_val, max_val):
        if key not in self._sorted_sets:
            return 0
        min_f = float("-inf") if min_val == "-inf" else float(min_val)
        max_f = float("inf")  if max_val == "+inf" else float(max_val)
        return sum(1 for v in self._sorted_sets[key].values() if min_f <= v <= max_f)

    async def zrangebyscore(self, key, min_val, max_val, withscores=False):
        if key not in self._sorted_sets:
            return []
        min_f = float("-inf") if min_val == "-inf" else float(min_val)
        max_f = float("inf")  if max_val == "+inf" else float(max_val)
        return [k for k, v in self._sorted_sets[key].items() if min_f <= v <= max_f]

    async def expire(self, key, ttl):
        pass

    async def sadd(self, key, *values):
        self._sets.setdefault(key, set()).update(values)

    async def smembers(self, key):
        return self._sets.get(key, set())

    async def get(self, key):
        return self._kv.get(key)

    async def set(self, key, value):
        self._kv[key] = str(value)

    async def setex(self, key, ttl, value):
        self._kv[key] = str(value)

    async def exists(self, key):
        return 1 if key in self._kv or key in self._sorted_sets else 0

    async def delete(self, *keys):
        for k in keys:
            self._kv.pop(k, None)
            self._sorted_sets.pop(k, None)
            self._sets.pop(k, None)

    async def incr(self, key):
        val = int(self._kv.get(key, 0)) + 1
        self._kv[key] = str(val)
        return val

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, min_val, max_val):
        self._ops.append(("zremrangebyscore", key, min_val, max_val))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "zadd":
                await self._redis.zadd(op[1], op[2])
                results.append(1)
            elif op[0] == "zremrangebyscore":
                await self._redis.zremrangebyscore(op[1], op[2], op[3])
                results.append(None)
            elif op[0] == "zcard":
                results.append(await self._redis.zcard(op[1]))
            elif op[0] == "expire":
                results.append(None)
            elif op[0] == "incr":
                results.append(await self._redis.incr(op[1]))
        return results


# ──────────────────────────────────────────────────────────────
# Redis Sliding Window Tests
# ──────────────────────────────────────────────────────────────

class TestSlidingWindow:
    def setup_method(self):
        self.redis = FakeRedis()

    def test_sliding_window_increments(self):
        from app.db.redis_client import sliding_window_add_and_count
        count = run(sliding_window_add_and_count(self.redis, "test:key", 60, "user1"))
        assert count == 1

    def test_sliding_window_accumulates(self):
        from app.db.redis_client import sliding_window_add_and_count
        for i in range(15):
            count = run(sliding_window_add_and_count(self.redis, "test:joins", 60, f"user{i}"))
        assert count == 15

    def test_sliding_window_count_no_add(self):
        from app.db.redis_client import sliding_window_add_and_count, sliding_window_count

        for i in range(5):
            run(sliding_window_add_and_count(self.redis, "test:window", 60, f"user{i}"))

        count = run(sliding_window_count(self.redis, "test:window", 60))
        assert count == 5

    def test_trust_score_default(self):
        from app.db.redis_client import get_trust_score
        score = run(get_trust_score(self.redis, 12345))
        assert score == 0.5

    def test_trust_score_set_and_get(self):
        from app.db.redis_client import set_trust_score, get_trust_score
        run(set_trust_score(self.redis, 12345, 0.85))
        score = run(get_trust_score(self.redis, 12345))
        assert abs(score - 0.85) < 0.001

    def test_trust_score_adjust(self):
        from app.db.redis_client import set_trust_score, adjust_trust_score
        run(set_trust_score(self.redis, 42, 0.5))
        new_score = run(adjust_trust_score(self.redis, 42, -0.2))
        assert abs(new_score - 0.3) < 0.001

    def test_trust_score_clamps(self):
        from app.db.redis_client import set_trust_score, adjust_trust_score
        run(set_trust_score(self.redis, 99, 0.1))
        score = run(adjust_trust_score(self.redis, 99, -5.0))
        assert score == 0.0


# ──────────────────────────────────────────────────────────────
# Rule Engine Tests
# ──────────────────────────────────────────────────────────────

class TestRuleEngine:
    def setup_method(self):
        self.redis = FakeRedis()
        from app.core.rule_engine import RuleEngine
        self.engine = RuleEngine(self.redis)
        self.group_settings = {}

    def test_join_spike_not_triggered_below_threshold(self):
        """Under 20 joins in 30s should not trigger raid signal."""
        result = run(self.engine.evaluate_join(
            group_id=1,
            user_id=100,
            username="normal_user",
            account_age_days=365,
            group_settings={},
        ))
        assert not result.should_lockdown

    def test_join_spike_triggers_at_threshold(self):
        """20+ joins in 30s should trigger raid."""
        for i in range(19):
            run(self.engine.evaluate_join(
                group_id=1, user_id=i + 1, username=f"user{i}",
                account_age_days=365, group_settings={}
            ))
        # 20th join should trigger
        result = run(self.engine.evaluate_join(
            group_id=1, user_id=20, username="user20",
            account_age_days=365, group_settings={}
        ))
        assert result.should_lockdown
        assert any(s.rule_id == "JOIN_SPIKE" for s in result.signals)

    def test_custom_join_threshold(self):
        """Per-group custom threshold should override default."""
        settings = {"join_spike_count": 5, "join_spike_window_s": 30}
        for i in range(4):
            run(self.engine.evaluate_join(
                group_id=99, user_id=i, username=f"u{i}",
                account_age_days=100, group_settings=settings
            ))
        result = run(self.engine.evaluate_join(
            group_id=99, user_id=5, username="u5",
            account_age_days=100, group_settings=settings
        ))
        assert result.should_lockdown

    def test_link_flood_triggered(self):
        """5+ link events in 60s should trigger link flood."""
        for i in range(4):
            run(self.engine.evaluate_message(
                group_id=1, user_id=i + 100, has_links=True, has_media=False,
                link_count=2, is_forward=False, forward_chain_length=0,
                is_new_user=True, group_settings={}
            ))
        result = run(self.engine.evaluate_message(
            group_id=1, user_id=200, has_links=True, has_media=False,
            link_count=2, is_forward=False, forward_chain_length=0,
            is_new_user=True, group_settings={}
        ))
        assert result.should_mute
        assert any(s.rule_id == "LINK_FLOOD" for s in result.signals)

    def test_no_link_flood_without_links(self):
        """Messages without links shouldn't trigger link flood."""
        for i in range(10):
            result = run(self.engine.evaluate_message(
                group_id=2, user_id=i, has_links=False, has_media=False,
                link_count=0, is_forward=False, forward_chain_length=0,
                is_new_user=False, group_settings={}
            ))
        assert not any(s.rule_id == "LINK_FLOOD" for s in result.signals)

    def test_report_burst_triggers(self):
        """5+ reports against same user in 5 minutes should trigger."""
        for i in range(4):
            run(self.engine.evaluate_report_burst(
                group_id=1, target_user_id=9999, group_settings={}
            ))
        result = run(self.engine.evaluate_report_burst(
            group_id=1, target_user_id=9999, group_settings={}
        ))
        assert result.should_mute
        assert any(s.rule_id == "REPORT_BURST" for s in result.signals)

    def test_username_similarity_detection(self):
        """Similar usernames joining in short period should trigger botnet rule."""
        similar_names = ["attacker0", "attacker1", "attacker2", "attacker3"]
        for i, name in enumerate(similar_names):
            result = run(self.engine.evaluate_join(
                group_id=1, user_id=i + 500, username=name,
                account_age_days=1, group_settings={}
            ))
        # Last result should have username similarity signal
        assert any(s.rule_id == "USERNAME_SIMILARITY" for s in result.signals)


# ──────────────────────────────────────────────────────────────
# Action Recommendation Tests
# ──────────────────────────────────────────────────────────────

class TestActionRecommendation:
    def test_lockdown_recommended_on_raid(self):
        from app.core.rule_engine import RuleEngineResult, ThreatSignal, recommend_action
        from app.models.models import Severity, IncidentType

        rr = RuleEngineResult()
        rr.should_lockdown = True
        rr.rule_score = 0.9
        rr.max_severity = Severity.CRITICAL
        rr.add_signal(ThreatSignal(
            rule_id="JOIN_SPIKE", description="test",
            severity=Severity.CRITICAL, incident_type=IncidentType.RAID,
            score_contribution=0.7
        ))

        rec = recommend_action(rr, ml_risk_score=0.8, group_settings={})
        assert rec.action == "lockdown"

    def test_mute_recommended_on_high_score(self):
        from app.core.rule_engine import RuleEngineResult, ThreatSignal, recommend_action
        from app.models.models import Severity, IncidentType

        rr = RuleEngineResult()
        rr.should_mute = True
        rr.rule_score = 0.8
        rr.max_severity = Severity.HIGH

        rec = recommend_action(rr, ml_risk_score=0.9, group_settings={})
        assert rec.action == "mute"

    def test_no_action_below_threshold(self):
        from app.core.rule_engine import RuleEngineResult, recommend_action
        from app.models.models import Severity

        rr = RuleEngineResult()
        rr.rule_score = 0.1
        rr.max_severity = Severity.INFO

        rec = recommend_action(rr, ml_risk_score=0.1, group_settings={})
        assert rec.action == "none"

    def test_custom_mute_duration(self):
        from app.core.rule_engine import RuleEngineResult, recommend_action
        from app.models.models import Severity

        rr = RuleEngineResult()
        rr.should_mute = True
        rr.rule_score = 0.9
        rr.max_severity = Severity.HIGH

        rec = recommend_action(rr, ml_risk_score=0.9, group_settings={"mute_duration_minutes": 15})
        assert rec.rollback_after_minutes == 15


# ──────────────────────────────────────────────────────────────
# ML Risk Scorer Tests
# ──────────────────────────────────────────────────────────────

class TestMLRiskScorer:
    def test_heuristic_score_new_account(self):
        from app.ml.risk_scorer import MLRiskScorer, RiskFeatures
        scorer = MLRiskScorer(model_path="/nonexistent")
        features = RiskFeatures(account_age_days=1, has_profile_photo=0)
        score = scorer._heuristic_score(features)
        assert score >= 0.3  # new account penalty

    def test_heuristic_score_old_verified_account(self):
        from app.ml.risk_scorer import MLRiskScorer, RiskFeatures
        scorer = MLRiskScorer(model_path="/nonexistent")
        features = RiskFeatures(
            account_age_days=730,
            has_profile_photo=1,
            is_verified=1,
            previous_report_count_30d=0,
        )
        score = scorer._heuristic_score(features)
        assert score < 0.2

    def test_score_returns_float_in_range(self):
        from app.ml.risk_scorer import MLRiskScorer, RiskFeatures
        scorer = MLRiskScorer(model_path="/nonexistent")
        features = RiskFeatures()
        score, shap = scorer.score(features)
        assert 0.0 <= score <= 1.0
        assert isinstance(shap, dict)

    def test_username_entropy_random_string(self):
        from app.ml.risk_scorer import compute_username_entropy
        # High entropy = more random = suspicious
        entropy = compute_username_entropy("xk39f2pq81")
        assert entropy > 2.0

    def test_username_entropy_repeated_chars(self):
        from app.ml.risk_scorer import compute_username_entropy
        entropy = compute_username_entropy("aaaaaaaaaa")
        assert entropy == 0.0

    def test_feature_vector_shape(self):
        from app.ml.risk_scorer import RiskFeatures
        arr = RiskFeatures().to_array()
        assert arr.shape == (1, len(RiskFeatures.feature_names()))


# ──────────────────────────────────────────────────────────────
# Evidence Store Tests
# ──────────────────────────────────────────────────────────────

class TestEvidenceStore:
    def test_sha256_is_deterministic(self):
        from app.services.evidence_store import EvidenceStore
        store = EvidenceStore()
        data = b"test message data"
        h1 = store._sha256(data)
        h2 = store._sha256(data)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_different_data(self):
        from app.services.evidence_store import EvidenceStore
        store = EvidenceStore()
        h1 = store._sha256(b"data1")
        h2 = store._sha256(b"data2")
        assert h1 != h2

    def test_key_builder_format(self):
        from app.services.evidence_store import EvidenceStore
        store = EvidenceStore()
        key = store._build_key("groups/1/incidents/42", "abc12345", "message.json")
        assert "groups/1/incidents/42" in key
        assert "abc12345" in key
        assert key.endswith("message.json")


# ──────────────────────────────────────────────────────────────
# Rate Limiter Tests
# ──────────────────────────────────────────────────────────────

class TestRateLimiter:
    def setup_method(self):
        self.redis = FakeRedis()

    def test_not_rate_limited_below_limit(self):
        from app.db.redis_client import is_rate_limited
        for _ in range(4):
            limited = run(is_rate_limited(self.redis, "api", "127.0.0.1", 5, 60))
        assert not limited

    def test_rate_limited_at_limit(self):
        from app.db.redis_client import is_rate_limited
        for _ in range(5):
            run(is_rate_limited(self.redis, "api2", "192.168.1.1", 5, 60))
        limited = run(is_rate_limited(self.redis, "api2", "192.168.1.1", 5, 60))
        assert limited

