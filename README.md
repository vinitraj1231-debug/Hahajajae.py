# 🛡️ ShieldBot — Enterprise Telegram Group Protection

> **Enterprise-grade Telegram protection system** combining deterministic rule engines,
> LightGBM ML risk scoring, tamper-proof evidence archival, and a React admin dashboard.

---

## Architecture Overview

```
                          ┌────────────────────────────────────┐
                          │          Telegram Platform          │
                          │  Groups / Channels / Bot API       │
                          └─────────┬──────────────┬───────────┘
                                    │              │
                          ┌─────────▼──────┐  ┌───▼──────────────┐
                          │ Telethon User  │  │  Bot API Instance │
                          │ (MTProto R/O)  │  │  (Write Actions)  │
                          └────────┬───────┘  └───────┬──────────┘
                                   │                  │
                          ┌────────▼──────────────────▼──────────┐
                          │              Event Bus                 │
                          │         (RabbitMQ / Redis)             │
                          └────────────────┬───────────────────────┘
                                           │
               ┌───────────────────────────┼──────────────────────────┐
               │                           │                          │
     ┌─────────▼──────┐          ┌─────────▼──────┐        ┌─────────▼──────┐
     │  Rule Engine   │          │   ML Scorer    │        │  Evidence      │
     │  (Heuristics)  │          │  (LightGBM +   │        │  Store         │
     │  Deterministic │          │   SHAP)        │        │  (MinIO/S3)    │
     └───────┬────────┘          └──────┬─────────┘        └────────────────┘
             │                          │
             └─────────┬────────────────┘
                       │
             ┌─────────▼──────────────┐
             │    Action Executor     │
             │  (Idempotent + Rollback│
             └─────────┬──────────────┘
                       │
         ┌─────────────┼──────────────┐
         │             │              │
  ┌──────▼──┐  ┌───────▼────┐  ┌─────▼──────┐
  │PostgreSQL│  │   Redis    │  │  FastAPI   │
  │(Events, │  │ (Trust,    │  │ Dashboard  │
  │Incidents│  │  Windows)  │  │    API     │
  └─────────┘  └────────────┘  └─────┬──────┘
                                      │
                               ┌──────▼──────┐
                               │   React     │
                               │  Dashboard  │
                               └─────────────┘
```

---

## Key Features

### 🔒 Prevention & Detection
| Feature | Description |
|---------|-------------|
| **Raid Detection** | Sliding window: >20 joins/30s → lockdown + captcha |
| **Spam Flood** | >100 messages/60s → slow-mode auto-enable |
| **Botnet Detection** | Levenshtein similarity across joiner usernames |
| **Phishing/Links** | >5 external links/60s from different users |
| **New Account Ratio** | >60% joiners with accounts < 7 days old |
| **Report Burst** | >5 reports against same user/content in 5 min |
| **ML Risk Scoring** | LightGBM with 13 behavioral features + SHAP explainability |

### 🛡️ Mitigations (TOS-Compliant)
- **Staged escalation**: Mute (5m) → Manual review → Escalate
- **Emoji captcha** for new joiners when captcha mode active
- **Temporary slow-mode** during message floods
- **Lockdown mode**: Freeze all non-admin permissions with one-click rollback
- **Auto-unmute**: Celery Beat restores permissions after mute expires
- **Global blacklist**: Opt-in cross-group threat sharing

### 📦 Evidence Collection
- Message JSON + media archived to MinIO with SHA-256 hashes
- Immutable retention with bucket versioning
- One-click ZIP export per incident (JSON + media + admin notes)
- Pre-signed URLs for secure download

### 🎛️ Admin Workflow
- Real-time Telegram DM alerts to all configured admins
- Inline keyboard buttons: Approve / Rollback / False Positive / Export
- Optimistic locking on incident updates (version field)
- Full audit trail in `audit_logs` table
- SHAP feature importance per incident for explainability

---

## Quick Start

### 1. Clone & Configure
```bash
git clone https://github.com/yourorg/shieldbot.git
cd shieldbot
cp .env.example .env
# Edit .env — at minimum set:
#   SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD
#   TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_USER_IDS
```

### 2. Launch Stack
```bash
docker compose up -d --build
```

### 3. Initialize Database
```bash
docker compose exec backend alembic upgrade head
# Or for initial setup:
docker compose exec postgres psql -U shieldbot -d shieldbot -f /docker-entrypoint-initdb.d/init.sql
```

### 4. Configure MTProto (Optional but recommended)
```bash
# Log in with your monitoring account to generate session
docker compose run --rm mtproto_listener python -m app.services.auth_setup
```

### 5. Register Webhook
```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -d "url=https://your-domain.com/api/v1/webhook/telegram?token=<YOUR_TOKEN>"
```

### 6. Add Bot to Groups
Invite `@your_shieldbot` to your Telegram groups and promote it to **Admin** with:
- Delete messages ✅
- Restrict members ✅
- Ban users ✅

---

## Admin Commands (in Telegram)

| Command | Description |
|---------|-------------|
| `/shield status` | Current protection status |
| `/shield set join_threshold 15` | Change join spike threshold |
| `/lockdown` | Emergency lockdown (freezes all non-admin permissions) |
| `/release` | Release lockdown |
| `/trust @username 85` | Set trust score (0-100) |
| `/export_incident 1234` | Export incident archive to admin DM |

---

## Configuration Thresholds (per group via API or `/shield set`)

```json
{
  "join_spike_count": 20,
  "join_spike_window_s": 30,
  "msg_spike_count": 100,
  "msg_spike_window_s": 60,
  "report_burst_count": 5,
  "report_burst_window_s": 300,
  "new_account_age_days": 7,
  "new_account_ratio": 0.6,
  "levenshtein_threshold": 3,
  "link_flood_count": 5,
  "link_flood_window_s": 60,
  "mute_duration_minutes": 5,
  "captcha_enabled": true,
  "lockdown_duration_minutes": 15
}
```

---

## API Reference

### Authentication
```http
POST /api/v1/auth/token
Content-Type: application/x-www-form-urlencoded
username=admin&password=yourpassword
```

### Key Endpoints
```http
GET  /api/v1/incidents/?status=open&severity=4
POST /api/v1/incidents/{id}/action          {"action": "approve_mute"}
GET  /api/v1/incidents/{id}/export
GET  /api/v1/groups/
POST /api/v1/groups/{id}/settings           {...thresholds...}
POST /api/v1/groups/{id}/lockdown
POST /api/v1/groups/{id}/release
GET  /api/v1/metrics/summary
GET  /api/v1/metrics/timeseries?hours=24
POST /api/v1/actions/execute                {"group_id": -100..., "action": "mute"}
```

---

## ML Model Training

```bash
# Export labeled incidents from DB
docker compose exec backend python -m app.ml.train \
  --output /app/models/risk_model.lgbm \
  --data /app/data/incidents_labeled.parquet

# The trainer uses 5-fold CV and reports:
#   Mean CV AUC, Precision/Recall, Feature Importances
```

Training features:
- `account_age_days` — How old the Telegram account is
- `message_freq_60s` — Messages sent in last 60 seconds
- `distinct_links_300s` — Unique external links in 5 minutes
- `has_profile_photo` — Binary: profile photo set
- `username_entropy` — Shannon entropy of username characters
- `forward_chain_length` — Number of forwards in chain
- `previous_report_count_30d` — Reports received in last 30 days
- `is_premium` / `is_verified` — Account status signals
- `join_cluster_size` — Size of the join wave
- `similarity_score` — Username similarity to known attackers

---

## Testing

```bash
# Unit tests
docker compose exec backend pytest tests/unit/ -v --cov=app --cov-report=term

# Load test (1000 joins/min simulation)
pip install locust
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
  --users=50 --spawn-rate=10 --run-time=2m --headless

# Integration tests (requires running stack)
docker compose exec backend pytest tests/integration/ -v
```

---

## Deployment — Production Checklist

- [ ] Change default admin password in `dashboard_users`
- [ ] Set strong `SECRET_KEY` (64+ random chars)
- [ ] Configure TLS via nginx or cloud LB
- [ ] Set `allowed_origins` to your dashboard domain
- [ ] Restrict `/metrics` endpoint to internal IPs
- [ ] Configure `ADMIN_TELEGRAM_USER_IDS` for your team
- [ ] Set up nightly DB backups: `pg_dump shieldbot | gzip > backup_$(date +%Y%m%d).sql.gz`
- [ ] Configure Sentry DSN for error tracking
- [ ] Set up Grafana alerts for `shieldbot_incidents_created_total{severity="4"}`
- [ ] Enable MinIO bucket versioning for evidence immutability
- [ ] Rotate bot tokens via BotFather every 90 days
- [ ] Add MTProto session file to secure secret storage

---

## Runbook — CRITICAL Incident Response

```
1. /lockdown                         ← Freeze joins + permissions
2. Bot auto-notifies all admins      ← Check Telegram DMs for details
3. Review incident in dashboard      ← Triage signals and SHAP explanation
4. Click "Approve" or "Rollback"     ← Make decision
5. /export_incident <id>             ← Get ZIP archive for appeals
6. Follow Telegram ToS appeal process with archive as evidence
7. /release                          ← When threat has passed
```

---

## Legal Notice

ShieldBot is designed to **protect communities** from raids, spam, phishing, and botnet attacks.
It does **not** and must not be used to:
- Silence legitimate users or political speech
- Bypass official Telegram moderation
- Harass, stalk, or target individuals

All automated actions prefer **reversible mitigations** (mute > ban) and provide admin rollback.
Maintain compliance with Telegram Terms of Service and applicable local laws.
