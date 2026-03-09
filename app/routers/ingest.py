"""
app/routers/ingest.py
─────────────────────
POST /webhook/sms      — Twilio inbound SMS (citizen reports)
POST /webhook/sms/confirm — L2/L3 node "YES" confirmation

Full Phase 1 pipeline:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Twilio SMS arrives                                             │
  │       ↓                                                         │
  │  1. Parse phone + body + location                               │
  │  2. Save raw CrisisReport to PostgreSQL                         │
  │  3. Azure AI Translator → translated_text + detected_language   │
  │  4. Azure AI Content Safety → is_spam flag                      │
  │  5. [if not spam + has location]                                │
  │     PostGIS ST_DWithin cluster check                            │
  │       ↓ cluster threshold met?                                  │
  │  6. Create ReportCluster (PENDING_VERIFICATION)                 │
  │  7. Twilio: ping nearby L2/L3 nodes → "Reply YES to confirm"    │
  │                                                                 │
  │  L2/L3 node replies YES →                                       │
  │  8. Verify sender is a valid L2/L3 TrustedNode                  │
  │  9. Promote cluster → CONFIRMED                                 │
  │  10. Create ActiveCrisis (wakes AutoGen in Step 3)              │
  └─────────────────────────────────────────────────────────────────┘
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CrisisReport, ReportCluster, TrustedNode
from app.services import clustering, content_safety, translator, twilio_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Ingest"])


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /webhook/sms
#  Receives every inbound SMS from citizens via Twilio
# ═══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/sms",
    summary="Twilio SMS Webhook — Citizen Ingest",
    description=(
        "Full Phase 1 pipeline: receive → translate → safety check → "
        "cluster → ping L2/L3 nodes."
    ),
    response_class=Response,
)
async def receive_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Twilio sends application/x-www-form-urlencoded POST bodies.
    We read the form directly to handle Twilio's exact PascalCase field names.
    """
    form = await request.form()

    from_phone: str = form.get("From", "").strip()
    body: str = form.get("Body", "").strip()
    raw_latitude = form.get("Latitude")
    raw_longitude = form.get("Longitude")

    if not from_phone or not body:
        logger.warning("Received malformed Twilio webhook — missing From or Body.")
        return _twiml("")

    logger.info(f"📱 SMS from {from_phone}: '{body[:80]}'")

    # ── Step 1: Build WKT geography if Twilio provided coordinates ───────────
    location_wkt: str | None = None
    if raw_latitude and raw_longitude:
        try:
            lat = float(raw_latitude)
            lon = float(raw_longitude)
            location_wkt = f"SRID=4326;POINT({lon} {lat})"
        except ValueError:
            logger.warning("Invalid lat/lon from Twilio — ignoring coordinates.")

    # ── Step 2: Persist raw report ────────────────────────────────────────────
    report = CrisisReport(
        id=uuid.uuid4(),
        reporter_phone=from_phone,
        raw_text=body,
        location=location_wkt,
    )
    db.add(report)
    await db.flush()
    logger.info(f"💾 Report saved: {report.id}")

    # ── Step 3: Azure AI Translator ───────────────────────────────────────────
    # Translates regional language (Hindi, Tamil, Marathi, etc.) → English
    # Falls back gracefully if AZURE_TRANSLATOR_KEY not set
    translated_text, detected_lang = await translator.translate_to_english(body)
    report.translated_text = translated_text
    report.detected_language = detected_lang
    await db.flush()

    # ── Step 4: Azure AI Content Safety ──────────────────────────────────────
    # Checks for spam, abuse, test messages on the translated text
    # Falls back to False (allow through) if AZURE_CONTENT_SAFETY_KEY not set
    flagged = await content_safety.is_spam_or_unsafe(translated_text)
    report.is_spam = flagged
    await db.flush()

    if flagged:
        logger.info(
            f"🚫 Report {report.id} flagged as spam/unsafe — "
            "excluded from clustering."
        )
        return _twiml("")   # silent acknowledgement — no reply to suspected spammer

    # ── Step 5: PostGIS Clustering ────────────────────────────────────────────
    # Only runs if the report has a GPS location
    new_cluster: ReportCluster | None = None
    if location_wkt:
        new_cluster = await clustering.check_and_create_cluster(db=db, report=report)
    else:
        logger.info(
            f"Report {report.id}: no GPS location — skipping cluster check. "
            "Consider asking citizens to share location."
        )

    # ── Step 6 & 7: Ping L2/L3 nodes if cluster was created ──────────────────
    if new_cluster:
        pinged = await twilio_client.ping_nearby_nodes(db=db, cluster=new_cluster)
        logger.info(
            f"🔔 Cluster {new_cluster.id} created — pinged {pinged} L2/L3 node(s)."
        )

    # ── Return empty TwiML (no reply SMS to sender) ───────────────────────────
    return _twiml("")


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /webhook/sms/confirm
#  Receives "YES" reply from L2/L3 Trusted Nodes
# ═══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/sms/confirm",
    summary="L2/L3 Confirmation Webhook",
    description=(
        "When a Level 2/3 node replies YES, promotes the cluster to "
        "an Active Crisis and wakes AutoGen."
    ),
    response_class=Response,
)
async def confirm_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    from_phone: str = form.get("From", "").strip()
    body: str = (form.get("Body", "") or "").strip().upper()

    logger.info(f"📲 Confirmation SMS from {from_phone}: '{body}'")

    # Only process "YES" replies
    reply_keywords = {"YES", "CONFIRM", "CONFIRMED", "OK", "HAAN", "हाँ"}
    if not any(kw in body for kw in reply_keywords):
        logger.info(f"Non-YES reply from {from_phone}: '{body}' — ignoring.")
        return _twiml("")

    # ── Verify sender is a valid L2/L3 TrustedNode ────────────────────────────
    node_result = await db.execute(
        select(TrustedNode).where(
            TrustedNode.phone == from_phone,
            TrustedNode.tier >= 2,
            TrustedNode.is_active == True,
        )
    )
    node: TrustedNode | None = node_result.scalar_one_or_none()

    if not node:
        logger.warning(
            f"Confirmation from unknown/L1 phone {from_phone} — ignoring."
        )
        return _twiml("")

    # ── Find the most recent PENDING_VERIFICATION cluster near this node ──────
    from app.models import ClusterStatus
    from sqlalchemy import text

    # Get the node's location WKT
    node_loc_result = await db.execute(
        text("SELECT ST_AsText(location) AS wkt FROM trusted_nodes WHERE id = :id"),
        {"id": str(node.id)},
    )
    node_loc_row = node_loc_result.fetchone()
    if not node_loc_row or not node_loc_row.wkt:
        logger.warning(f"Node {node.id} has no location set — cannot find cluster.")
        return _twiml(f"Hi {node.name}! We couldn't find a cluster near you. Please call the NDRF hotline.")

    # Find the nearest PENDING cluster within 5km, created in last 2 hours
    cluster_query = text(
        """
        SELECT id FROM report_clusters
        WHERE status = 'PENDING_VERIFICATION'
          AND created_at > NOW() - INTERVAL '2 hours'
          AND ST_DWithin(
                location,
                ST_GeographyFromText(:node_loc),
                5000
              )
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    cluster_result = await db.execute(
        cluster_query, {"node_loc": f"SRID=4326;{node_loc_row.wkt}"}
    )
    cluster_row = cluster_result.fetchone()

    if not cluster_row:
        logger.info(
            f"Node {node.name} replied YES but no pending cluster found nearby."
        )
        return _twiml(
            f"Thank you {node.name}! No pending alerts found near you right now."
        )

    # Load the full cluster ORM object
    cluster_obj_result = await db.execute(
        select(ReportCluster).where(ReportCluster.id == cluster_row.id)
    )
    cluster: ReportCluster = cluster_obj_result.scalar_one()

    # ── Promote cluster → Active Crisis ───────────────────────────────────────
    active_crisis = await twilio_client.promote_cluster_to_crisis(
        db=db, cluster=cluster, confirming_node=node
    )

    disaster = active_crisis.disaster_type.value if active_crisis.disaster_type else "DISASTER"
    logger.info(
        f"🚨 Crisis {active_crisis.id} created | {disaster} | "
        f"confirmed by {node.name}"
    )

    # ── Reply to the confirming node with acknowledgement ─────────────────────
    return _twiml(
        f"Thank you {node.name}! Crisis #{str(active_crisis.id)[:8].upper()} "
        f"logged. Response teams are being coordinated. Stay safe."
    )


# ── Helper ────────────────────────────────────────────────────────────────────

def _twiml(message: str) -> Response:
    """
    Return a TwiML XML response.
    If message is empty → silent acknowledgement (no SMS reply sent).
    If message has text → Twilio sends it back as an SMS reply.
    """
    if message:
        body_tag = f"<Message>{message}</Message>"
    else:
        body_tag = ""
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body_tag}</Response>'
    return Response(content=twiml, media_type="application/xml")
