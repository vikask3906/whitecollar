"""
app/services/twilio_client.py
──────────────────────────────
Twilio REST Client — Outbound SMS helper

WHERE TO PLUG IN YOUR TWILIO KEYS
───────────────────────────────────
Open your .env file and fill in these 3 values:

  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_AUTH_TOKEN=your_auth_token_here
  TWILIO_PHONE_NUMBER=+1415xxxxxxx

HOW TO GET THE KEYS
────────────────────
1. Go to console.twilio.com
2. Your Account SID and Auth Token are on the Dashboard home page
3. TWILIO_PHONE_NUMBER is your purchased/sandbox Twilio number
   (format must be E.164: +1xxxxxxxxxx)

WHAT THIS FILE DOES
────────────────────
send_sms()
  - Low-level helper: sends ONE SMS to ONE phone number
  - Graceful fallback: if Twilio keys not set, logs warning and returns None

ping_nearby_nodes()
  - Called by clustering.py when a new PENDING_VERIFICATION cluster is created
  - Runs a PostGIS ST_DWithin query to find all L2/L3 TrustedNodes within
    2× the cluster radius
  - Sends each one a targeted SMS: "CLUSTER ALERT: <type> near you.
    Reply YES to confirm. Ref: <cluster_id_short>"
  - Returns the count of nodes pinged

promote_cluster_to_crisis()
  - Called by ingest.py confirm_sms() when an L2/L3 node replies YES
  - Transitions cluster.status → CONFIRMED
  - Creates a new ActiveCrisis row linked to the cluster
  - Returns the new ActiveCrisis object
"""
import logging
import uuid
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ActiveCrisis, CrisisStatus, ReportCluster, TrustedNode

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Low-level SMS sender ──────────────────────────────────────────────────────

async def send_sms(to: str, body: str) -> Optional[str]:
    """
    Send a single SMS via Twilio REST API.

    Parameters
    ----------
    to   : E.164 destination phone number, e.g. "+919810000001"
    body : Message text (max 1600 chars; longer messages split automatically)

    Returns
    -------
    Twilio message SID string on success, or None on failure/skip.
    """
    if not all([
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_phone_number,
    ]):
        logger.warning(
            "Twilio credentials not set — skipping SMS send. "
            "Fill TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env"
        )
        return None

    try:
        # Import here so missing Twilio SDK doesn't crash the whole app
        from twilio.rest import Client as TwilioClient

        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        message = client.messages.create(
            to=to,
            from_=settings.twilio_phone_number,
            body=body,
        )
        logger.info(f"SMS sent to {to} | SID={message.sid}")
        return message.sid

    except Exception as e:
        logger.error(f"Twilio send_sms error to {to}: {e}")
        return None


# ── Cluster confirmation ping ─────────────────────────────────────────────────

async def ping_nearby_nodes(
    db: AsyncSession,
    cluster: ReportCluster,
    radius_multiplier: float = 2.0,
) -> int:
    """
    Find all active L2/L3 TrustedNodes within (radius_multiplier × cluster radius)
    and send each a confirmation request SMS.

    Returns the number of nodes successfully pinged.
    """
    search_radius_m = cluster.radius_m * radius_multiplier
    cluster_id_short = str(cluster.id)[:8].upper()

    disaster_label = (
        cluster.disaster_type.value if cluster.disaster_type else "UNKNOWN DISASTER"
    )

    # ── PostGIS query: L2/L3 nodes within search radius ──────────────────────
    query = text(
        """
        SELECT id, phone, name, preferred_language
        FROM trusted_nodes
        WHERE is_active = TRUE
          AND tier >= 2
          AND location IS NOT NULL
          AND ST_DWithin(
                location,
                ST_GeographyFromText(:cluster_location),
                :search_radius_m
              )
        LIMIT 20
        """
    )

    # Extract raw WKT from cluster location
    cluster_loc_wkt = str(cluster.location)
    if cluster_loc_wkt.startswith("SRID="):
        cluster_loc_wkt = cluster_loc_wkt.split(";", 1)[1]

    result = await db.execute(
        query,
        {
            "cluster_location": f"SRID=4326;{cluster_loc_wkt}",
            "search_radius_m": search_radius_m,
        },
    )
    nearby_nodes = result.fetchall()

    if not nearby_nodes:
        logger.warning(
            f"Cluster {cluster.id}: no L2/L3 nodes found within "
            f"{search_radius_m:.0f}m — cannot send confirmation ping."
        )
        return 0

    # ── Send confirmation SMS to each node ────────────────────────────────────
    pinged = 0
    for node in nearby_nodes:
        sms_body = (
            f"[ADRC ALERT] {disaster_label} cluster detected near you "
            f"({cluster.report_count} public reports). "
            f"Are you on-site? Reply YES to confirm. "
            f"Ref: {cluster_id_short}"
        )
        sid = await send_sms(to=node.phone, body=sms_body)
        if sid or not all([
            settings.twilio_account_sid,
            settings.twilio_auth_token,
        ]):
            # Count as pinged even when Twilio is not configured (dev mode)
            pinged += 1

    logger.info(
        f"Pinged {pinged}/{len(nearby_nodes)} L2/L3 nodes for cluster {cluster.id}"
    )
    return pinged


# ── Promote cluster to Active Crisis ─────────────────────────────────────────

async def promote_cluster_to_crisis(
    db: AsyncSession,
    cluster: ReportCluster,
    confirming_node: TrustedNode,
) -> ActiveCrisis:
    """
    Called when an L2/L3 node replies YES to a confirmation ping.

    1. Sets cluster.status = CONFIRMED
    2. Creates a new ActiveCrisis linked to the cluster
    3. Returns the new ActiveCrisis object (wakes up AutoGen in Step 3)
    """
    from app.models import ClusterStatus
    from datetime import datetime, timezone

    # Mark cluster confirmed
    cluster.status = ClusterStatus.CONFIRMED
    cluster.updated_at = datetime.now(timezone.utc)

    disaster_label = (
        cluster.disaster_type.value if cluster.disaster_type else "UNKNOWN"
    )

    crisis = ActiveCrisis(
        id=uuid.uuid4(),
        disaster_type=cluster.disaster_type,
        severity=2,   # default severity; Planner agent will refine in Step 3
        title=f"{disaster_label} — confirmed by {confirming_node.name}",
        description=(
            f"Cluster of {cluster.report_count} public reports confirmed by "
            f"Level {confirming_node.tier} node: {confirming_node.name} "
            f"({confirming_node.phone})."
        ),
        location=cluster.location,
        affected_radius_m=cluster.radius_m * 3,   # expand radius for response
        warning_lead_time_h=0,                    # sudden onset (crowdsourced)
        status=CrisisStatus.ACTIVE,
        orchestration_state={"phase": "RETRIEVAL"},
        source_cluster_id=cluster.id,
    )
    db.add(crisis)
    await db.flush()

    logger.info(
        f"🚨 Active Crisis created: {crisis.id} | {disaster_label} "
        f"| confirmed by {confirming_node.name}"
    )
    return crisis


# ── Step 5: Executor Task Dispatch ────────────────────────────────────────────

async def dispatch_task_sms(
    db: AsyncSession,
    node: TrustedNode,
    task: dict,
    crisis: ActiveCrisis,
    assignment_id: uuid.UUID
):
    """
    Called by the Executor Agent in Step 5.
    Sends the dynamically generated AutoGen task to the assigned Responder Node.
    """
    priority = task.get("priority", "MEDIUM").upper()
    action = task.get("action", "Respond")
    zone = task.get("zone", "General Area")

    # Shorten Assignment ID for reply reference
    ref_id = str(assignment_id)[:5].upper()

    sms_body = (
        f"🚨 ADRC TASK [{priority}]\n"
        f"Action: {action}\n"
        f"Zone: {zone}\n"
        f"Reply 'DONE {ref_id}' when completed."
    )

    sid = await send_sms(to=node.phone, body=sms_body)
    
    if sid:
        logger.info(f"Task SMS sent to {node.name} for assignment {assignment_id}")
    else:
        logger.info(f"Task SMS skipped (sandbox/dev mode) to {node.name} for {assignment_id}")

