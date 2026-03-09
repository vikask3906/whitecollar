-- ═══════════════════════════════════════════════════════════════════════════
--  ADRC — PostgreSQL + PostGIS Initialisation Script
--  Runs automatically on first container boot (empty volume).
--  Docker image: postgis/postgis:15-3.3
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Extensions ─────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── ENUM types ─────────────────────────────────────────────────────────────
CREATE TYPE cluster_status AS ENUM (
    'PENDING_VERIFICATION',
    'CONFIRMED',
    'DISMISSED'
);

CREATE TYPE crisis_status AS ENUM (
    'ACTIVE',
    'CONTAINED',
    'RESOLVED'
);

CREATE TYPE disaster_type AS ENUM (
    'FLOOD',
    'CYCLONE',
    'EARTHQUAKE',
    'FIRE',
    'GAS_LEAK',
    'LANDSLIDE',
    'OTHER'
);

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLE: trusted_nodes
--  Citizens/volunteers/officials who are contactable via Twilio.
--  tier 1 = Public | tier 2 = Registered Volunteer/NGO | tier 3 = Official
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS trusted_nodes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone               VARCHAR(20)  NOT NULL UNIQUE,   -- E.164 format: +91xxxxxxxxxx
    name                VARCHAR(120) NOT NULL,
    tier                SMALLINT     NOT NULL CHECK (tier BETWEEN 1 AND 3),
    preferred_language  VARCHAR(10)  NOT NULL DEFAULT 'en', -- BCP-47 language code
    -- Geography stores (lon, lat) with SRID 4326 (WGS84)
    location            GEOGRAPHY(POINT, 4326),
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Spatial index for fast radius queries (PostGIS GIST)
CREATE INDEX IF NOT EXISTS idx_trusted_nodes_location
    ON trusted_nodes USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_trusted_nodes_tier
    ON trusted_nodes (tier);

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLE: report_clusters
--  A spatial+temporal grouping of Level-1 crisis reports.
--  Created automatically when PostGIS clustering threshold is breached.
--  Awaits L2/L3 confirmation before becoming an Active Crisis.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS report_clusters (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    disaster_type   disaster_type,
    -- Centroid of the cluster
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    radius_m        INTEGER      NOT NULL DEFAULT 500,
    report_count    SMALLINT     NOT NULL DEFAULT 1,
    status          cluster_status NOT NULL DEFAULT 'PENDING_VERIFICATION',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_clusters_location
    ON report_clusters USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_report_clusters_status
    ON report_clusters (status);

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLE: crisis_reports
--  Raw inbound SMS messages — one row per Twilio webhook call.
--  Links back to a cluster once one is detected.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS crisis_reports (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reporter_phone   VARCHAR(20)  NOT NULL,
    raw_text         TEXT         NOT NULL,
    translated_text  TEXT,                    -- populated after Azure Translator call
    detected_language VARCHAR(10),            -- BCP-47 code detected by Translator
    location         GEOGRAPHY(POINT, 4326),  -- from Twilio Geo or manual input
    cluster_id       UUID REFERENCES report_clusters(id) ON DELETE SET NULL,
    is_spam          BOOLEAN      NOT NULL DEFAULT FALSE, -- Azure Content Safety flag
    reported_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crisis_reports_location
    ON crisis_reports USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_crisis_reports_reporter
    ON crisis_reports (reporter_phone);

CREATE INDEX IF NOT EXISTS idx_crisis_reports_cluster
    ON crisis_reports (cluster_id);

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLE: active_crises
--  Confirmed disasters — the table that wakes up the AutoGen orchestrator.
--  orchestration_state JSONB stores dynamic AutoGen chat state.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS active_crises (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    disaster_type       disaster_type    NOT NULL,
    severity            SMALLINT         NOT NULL DEFAULT 1 CHECK (severity BETWEEN 1 AND 5),
    title               VARCHAR(200)     NOT NULL,
    description         TEXT,
    -- Epicentre / incident location
    location            GEOGRAPHY(POINT, 4326) NOT NULL,
    affected_radius_m   INTEGER          NOT NULL DEFAULT 5000,
    -- Warning lead time in hours (0 = sudden onset disaster)
    warning_lead_time_h SMALLINT         NOT NULL DEFAULT 0,
    status              crisis_status    NOT NULL DEFAULT 'ACTIVE',
    -- Tracks AutoGen orchestration phase: RETRIEVAL / PLANNING / HITL_REVIEW / EXECUTION
    orchestration_state JSONB            NOT NULL DEFAULT '{"phase": "RETRIEVAL"}'::jsonb,
    -- Links back to the cluster or external API that triggered this crisis
    source_cluster_id   UUID REFERENCES report_clusters(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_active_crises_location
    ON active_crises USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_active_crises_status
    ON active_crises (status);

CREATE INDEX IF NOT EXISTS idx_active_crises_type
    ON active_crises (disaster_type);

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLE: task_assignments
--  Atomic tasks dispatched to L2/L3 volunteers by the Executor agent.
--  Tracks accept/reject and feedback loop status.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TYPE assignment_status AS ENUM (
    'DISPATCHED',
    'ACCEPTED',
    'REJECTED',
    'COMPLETED'
);

CREATE TABLE IF NOT EXISTS task_assignments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    crisis_id       UUID         NOT NULL REFERENCES active_crises(id) ON DELETE CASCADE,
    node_id         UUID         NOT NULL REFERENCES trusted_nodes(id) ON DELETE CASCADE,
    task_text_en    TEXT         NOT NULL,    -- original English task from Executor
    task_text_local TEXT,                    -- translated task sent via Twilio
    language_sent   VARCHAR(10),             -- BCP-47 code of translated task
    status          assignment_status NOT NULL DEFAULT 'DISPATCHED',
    dispatched_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    responded_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_task_assignments_crisis
    ON task_assignments (crisis_id);

CREATE INDEX IF NOT EXISTS idx_task_assignments_node
    ON task_assignments (node_id);

-- ═══════════════════════════════════════════════════════════════════════════
--  SEED DATA — 3 Trusted Nodes for local development/testing
--  Locations: Delhi, Mumbai, Chennai (approximate city centres)
-- ═══════════════════════════════════════════════════════════════════════════

INSERT INTO trusted_nodes (phone, name, tier, preferred_language, location)
VALUES
  -- L3 Official Dispatcher (Delhi NDRF HQ area)
  ('+919810000001', 'Rajan Sharma (NDRF Delhi)',
   3, 'hi',
   ST_SetSRID(ST_MakePoint(77.2090, 28.6139), 4326)::geography),

  -- L2 Registered Volunteer (Mumbai)
  ('+919820000002', 'Priya Nair (NSS Mumbai)',
   2, 'mr',
   ST_SetSRID(ST_MakePoint(72.8777, 19.0760), 4326)::geography),

  -- L2 Registered Volunteer (Chennai)
  ('+919830000003', 'Arjun Rajan (Red Cross Chennai)',
   2, 'ta',
   ST_SetSRID(ST_MakePoint(80.2707, 13.0827), 4326)::geography)

ON CONFLICT (phone) DO NOTHING;
