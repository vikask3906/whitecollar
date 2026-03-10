# AI Disaster Response Coordinator (ADRC)
### Microsoft AI Unlocked Hackathon — Track 4: Agent Teamwork

---

## Project Overview

ADRC is a Multi-Agent Disaster Response System built on **Microsoft AutoGen**, **Azure AI Services**, and **FastAPI + PostgreSQL/PostGIS**. It implements a "Golden Hour Protocol" prioritizing life-critical response within the first 60 minutes of a disaster.

---

## Architecture at a Glance

```
[Citizens/SMS] → [Twilio] → [FastAPI Ingest] → [Azure Translator + Content Safety]
                                                        ↓
                                              [PostGIS Clustering]
                                                        ↓
                                          [L2/L3 Confirmation via Twilio SMS]
                                                        ↓
                                          [Active_Crises Table] ─── wakes up ───►
                                                                                  │
            AutoGen Group Chat:                                                   │
            [Retriever Agent] → [Planner Agent] → [HITL Dashboard] → [Executor Agent]
            (Azure AI Search)    (GPT-4o + RAG)   (React+Mapbox)     (Twilio Dispatch)
```

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Backend runtime |
| Docker Desktop | Latest | PostgreSQL + PostGIS |
| Git | Any | Version control |

---

## Step 1: Local Setup

### 1. Clone & enter the project
```bash
cd ssdn_microhard
```

### 2. Copy environment template
```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### 3. Create & activate Python virtual environment
```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Start the database
```bash
docker-compose up -d

# Verify PostGIS is running
docker exec -it adrc_db psql -U adrc_user -d adrc_db -c "SELECT PostGIS_Version();"
```

### 6. Start the API server
```bash
uvicorn app.main:app --reload --port 8000
```

### 7. Open the interactive docs
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **pgAdmin**: http://localhost:5050 (admin@adrc.local / admin)

---

## Verification Checklist

```bash
# Health check
curl http://localhost:8000/health

# List pre-seeded trusted nodes (Delhi L3, Mumbai L2, Chennai L2)
curl http://localhost:8000/nodes

# Simulate incoming citizen SMS
curl -X POST http://localhost:8000/webhook/sms \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "From=%2B919876543210&Body=There+is+a+fire+near+my+house&Latitude=28.61&Longitude=77.20"

# Create a test Active Crisis
curl -X POST http://localhost:8000/crises \
  -H "Content-Type: application/json" \
  -d '{
    "disaster_type": "FIRE",
    "severity": 3,
    "title": "Building fire near Connaught Place, Delhi",
    "longitude": 77.2167,
    "latitude": 28.6315,
    "affected_radius_m": 1000,
    "warning_lead_time_h": 0
  }'
```

---

## Project Structure

```
ssdn_microhard/
├── app/
│   ├── __init__.py
│   ├── main.py          ← FastAPI entry point
│   ├── config.py        ← Pydantic settings (reads .env)
│   ├── database.py      ← Async SQLAlchemy engine + session
│   ├── models.py        ← ORM models (GeoAlchemy2)
│   ├── schemas.py       ← Pydantic v2 API schemas
│   └── routers/
│       ├── __init__.py
│       ├── ingest.py    ← POST /webhook/sms (Twilio)
│       ├── crises.py    ← GET/POST /crises
│       └── nodes.py     ← GET/POST /nodes
├── db/
│   └── init.sql         ← PostGIS DDL, ENUMs, indexes, seed data
├── alembic/
│   └── env.py           ← Alembic migration environment
├── alembic.ini
├── docker-compose.yml   ← PostGIS 15-3.3 + pgAdmin4
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Database Schema

| Table | Purpose |
|---|---|
| `trusted_nodes` | Citizens/volunteers/officials (tier 1/2/3) |
| `crisis_reports` | Raw inbound SMS reports |
| `report_clusters` | Geospatial groupings awaiting confirmation |
| `active_crises` | Confirmed disasters (wakes AutoGen) |
| `task_assignments` | Atomic tasks dispatched to field responders |

---

## Roadmap

| Step | Feature | Status |
|---|---|---|
| ✅ Step 1 | FastAPI backend + PostgreSQL/PostGIS schema | Complete |
| ✅ Step 2 | Azure Content Safety + PostGIS clustering | Complete |
| ✅ Step 3 | AutoGen multi-agent setup (Retriever + Planner) | Complete |
| ✅ Step 4 | HITL Dashboard (React + WebSockets) | Complete |
| ✅ Step 5 | Executor Agent + Twilio dispatch + feedback loop | Complete |
| ✅ Step 6 | External Disaster APIs (USGS Quakes + IMD Weather) | Complete |

