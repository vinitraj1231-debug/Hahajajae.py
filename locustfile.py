
"""
ShieldBot — Load Tests
Simulates 1000 joins/min to validate sliding window performance.
Run: locust -f tests/load/locustfile.py --host=http://localhost:8000
"""
import random
import time
from locust import HttpUser, task, between, events


class ShieldBotUser(HttpUser):
    wait_time = between(0.05, 0.2)  # aggressive – simulate raid

    def on_start(self):
        # Auth once per user
        resp = self.client.post("/api/v1/auth/token", data={
            "username": "admin",
            "password": "changeme123",
        })
        if resp.status_code == 200:
            self.token = resp.json().get("access_token")
        else:
            self.token = None

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(5)
    def simulate_join_event(self):
        """Simulate a join event via webhook (Bot API style)."""
        user_id = random.randint(100000, 999999999)
        group_id = -100123456789
        payload = {
            "update_id": random.randint(1, 999999),
            "chat_member": {
                "chat": {"id": group_id, "title": "Test Group", "type": "supergroup"},
                "from": {"id": 1, "first_name": "Bot", "is_bot": True},
                "date": int(time.time()),
                "old_chat_member": {
                    "user": {"id": user_id, "first_name": f"User{user_id}", "is_bot": False},
                    "status": "left",
                },
                "new_chat_member": {
                    "user": {"id": user_id, "first_name": f"User{user_id}", "is_bot": False},
                    "status": "member",
                },
            },
        }
        self.client.post(
            f"/api/v1/webhook/telegram?token=test",
            json=payload,
            headers=self._headers(),
            name="/webhook/telegram [join]",
        )

    @task(3)
    def simulate_message_event(self):
        """Simulate a message event."""
        user_id = random.randint(100000, 999999)
        group_id = -100123456789
        payload = {
            "update_id": random.randint(1, 999999),
            "message": {
                "message_id": random.randint(1, 99999),
                "from": {"id": user_id, "first_name": "User", "is_bot": False},
                "chat": {"id": group_id, "type": "supergroup"},
                "date": int(time.time()),
                "text": "Hello world " + "x" * random.randint(0, 200),
            },
        }
        self.client.post(
            f"/api/v1/webhook/telegram?token=test",
            json=payload,
            headers=self._headers(),
            name="/webhook/telegram [message]",
        )

    @task(2)
    def list_incidents(self):
        self.client.get(
            "/api/v1/incidents/?limit=20",
            headers=self._headers(),
            name="/incidents [list]",
        )

    @task(1)
    def get_metrics(self):
        self.client.get(
            "/api/v1/metrics/summary",
            headers=self._headers(),
            name="/metrics/summary",
        )

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")


class RaidSimulationUser(HttpUser):
    """Simulates a coordinated raid — 20 users join within seconds."""
    wait_time = between(0.01, 0.05)

    @task
    def raid_join(self):
        group_id = -100999888777
        user_id = random.randint(1000000, 9999999)
        # Usernames are very similar (botnet detection trigger)
        username = f"attk{random.randint(1000, 1099)}"
        payload = {
            "update_id": random.randint(1, 999999),
            "chat_member": {
                "chat": {"id": group_id, "title": "Target Group", "type": "supergroup"},
                "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
                "date": int(time.time()),
                "old_chat_member": {
                    "user": {"id": user_id, "first_name": username, "username": username, "is_bot": False},
                    "status": "left",
                },
                "new_chat_member": {
                    "user": {"id": user_id, "first_name": username, "username": username, "is_bot": False},
                    "status": "member",
                },
            },
        }
        self.client.post(
            "/api/v1/webhook/telegram?token=test",
            json=payload,
            name="/webhook [raid_join]",
        )


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n🛡️  ShieldBot Load Test Starting")
    print("   Target: Validate sliding window at 1000 joins/min")
    print("   Also tests: ML scorer, rule engine, Redis throughput\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n🛡️  ShieldBot Load Test Complete")
    stats = environment.stats.total
    print(f"   Total Requests: {stats.num_requests}")
    print(f"   Failures:       {stats.num_failures}")
    print(f"   Avg Latency:    {stats.avg_response_time:.0f}ms")
    print(f"   95th pct:       {stats.get_response_time_percentile(0.95):.0f}ms")
