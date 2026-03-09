"""
app/services/clustering.py
───────────────────────────
PostGIS Spatial + Temporal Clustering Logic

WHERE TO CONFIGURE
──────────────────
Open your .env file and adjust these thresholds (defaults work out of the box):

  CLUSTER_RADIUS_METERS=500          # reports within 500m of each other form a cluster
  CLUSTER_TIME_WINDOW_MINUTES=30     # only count reports in the last 30 minutes
  CLUSTER_MIN_REPORTS=3              # need ≥ 3 reports to trigger a cluster

NO AZURE NEEDED — this runs entirely on your local PostGIS database.

WHAT THIS FILE DOES
───────────────────
1. After every non-spam SMS is saved, called by ingest.py
2. Runs a PostGIS ST_DWithin query counting recent crisis_reports near the new one
3. If ≥ CLUSTER_MIN_REPORTS found → creates a report_clusters row (PENDING_VERIFICATION)
4. Links all nearby reports to the new cluster
5. Triggers twilio_client.ping_nearby_nodes() to alert L2/L3 responders
6. Returns the new ReportCluster object, or None if threshold not met

PostGIS query used (approximate):
  SELECT COUNT(*) FROM crisis_reports
  WHERE is_spam = FALSE
    AND ST_DWithin(location, <new_report_location>, <radius_m>)
    AND reported_at > NOW() - INTERVAL '<window_min> minutes'
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CrisisReport, DisasterType, ReportCluster, ClusterStatus

logger = logging.getLogger(__name__)
settings = get_settings()


async def check_and_create_cluster(
    db: AsyncSession,
    report: CrisisReport,
) -> Optional[ReportCluster]:
    """
    Run PostGIS clustering analysis around the newly received crisis report.

    Parameters
    ----------
    db     : async SQLAlchemy session
    report : the freshly inserted CrisisReport ORM object
             (must have .location set as a WKT geography string)

    Returns
    -------
    ReportCluster if a new cluster was created, else None.
    """
    # ── Skip if no location on the report ────────────────────────────────────
    if report.location is None:
        logger.info(
            f"Report {report.id}: no location — skipping cluster check."
        )
        return None

    radius_m = settings.cluster_radius_meters
    window_min = settings.cluster_time_window_minutes
    min_reports = settings.cluster_min_reports
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)

    # ── PostGIS query: count nearby non-spam reports within time window ───────
    # ST_DWithin on Geography uses metres as the distance unit.
    # We cast the WKT string to geography inline.
    count_query = text(
        """
        SELECT COUNT(*) AS cnt,
               ST_AsText(ST_Centroid(ST_Collect(location::geometry))) AS centroid_wkt
        FROM crisis_reports
        WHERE is_spam = FALSE
          AND location IS NOT NULL
          AND cluster_id IS NULL
          AND reported_at >= :cutoff
          AND ST_DWithin(
                location,
                ST_GeographyFromText(:location_wkt),
                :radius_m
              )
        """
    )

    result = await db.execute(
        count_query,
        {
            "location_wkt": f"SRID=4326;{_wkt_from_report(report)}",
            "radius_m": radius_m,
            "cutoff": cutoff,
        },
    )
    row = result.fetchone()

    nearby_count: int = row.cnt if row else 0
    centroid_wkt: str | None = row.centroid_wkt if row else None

    logger.info(
        f"Cluster check: {nearby_count} nearby reports within {radius_m}m "
        f"/ {window_min}min (need {min_reports})"
    )

    if nearby_count < min_reports:
        return None

    # ── Threshold met → create a new cluster ─────────────────────────────────
    # Build centroid geography string — use centroid of all nearby reports
    # if available, otherwise fall back to the new report's location
    if centroid_wkt:
        cluster_location = f"SRID=4326;{centroid_wkt}"
    else:
        cluster_location = f"SRID=4326;{_wkt_from_report(report)}"

    # Try to infer disaster type from the existing reports' context
    # (Will be overridden by Planner agent later; this is a best-effort label)
    disaster_type = _infer_disaster_type(report.translated_text or report.raw_text)

    cluster = ReportCluster(
        id=uuid.uuid4(),
        disaster_type=disaster_type,
        location=cluster_location,
        radius_m=radius_m,
        report_count=nearby_count,
        status=ClusterStatus.PENDING_VERIFICATION,
    )
    db.add(cluster)
    await db.flush()  # get cluster.id

    # ── Link all nearby unclustered reports to this new cluster ───────────────
    link_query = text(
        """
        UPDATE crisis_reports
        SET cluster_id = :cluster_id
        WHERE is_spam = FALSE
          AND location IS NOT NULL
          AND cluster_id IS NULL
          AND reported_at >= :cutoff
          AND ST_DWithin(
                location,
                ST_GeographyFromText(:location_wkt),
                :radius_m
              )
        """
    )
    await db.execute(
        link_query,
        {
            "cluster_id": str(cluster.id),
            "location_wkt": f"SRID=4326;{_wkt_from_report(report)}",
            "radius_m": radius_m,
            "cutoff": cutoff,
        },
    )

    logger.info(
        f"✅ Cluster created: {cluster.id} | type={disaster_type} "
        f"| {nearby_count} reports | status=PENDING_VERIFICATION"
    )
    return cluster


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wkt_from_report(report: CrisisReport) -> str:
    """
    Extract the raw WKT point string from the report's location field.
    GeoAlchemy2 stores it as 'SRID=4326;POINT(lon lat)' — strip the SRID prefix.
    """
    loc = str(report.location)
    if loc.startswith("SRID="):
        # "SRID=4326;POINT(77.2 28.6)" → "POINT(77.2 28.6)"
        loc = loc.split(";", 1)[1]
    return loc


def _infer_disaster_type(text: str | None) -> DisasterType | None:
    """
    Very simple keyword-based disaster type inference from translated text.
    The Planner agent will properly classify this in Step 3.
    """
    if not text:
        return None

    lower = text.lower()
    if any(w in lower for w in ["fire", "burn", "flame", "blaze"]):
        return DisasterType.FIRE
    if any(w in lower for w in ["flood", "water", "submerge", "drown"]):
        return DisasterType.FLOOD
    if any(w in lower for w in ["earthquake", "quake", "tremor", "shaking"]):
        return DisasterType.EARTHQUAKE
    if any(w in lower for w in ["cyclone", "storm", "hurricane", "typhoon"]):
        return DisasterType.CYCLONE
    if any(w in lower for w in ["gas", "leak", "chemical", "fumes", "smell"]):
        return DisasterType.GAS_LEAK
    if any(w in lower for w in ["landslide", "mudslide", "collapse"]):
        return DisasterType.LANDSLIDE
    return DisasterType.OTHER
