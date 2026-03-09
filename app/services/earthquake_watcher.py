"""
app/services/earthquake_watcher.py
───────────────────────────────────
USGS Earthquake Real-Time Feed Watcher

WHAT THIS FILE DOES
────────────────────
- Polls the USGS GeoJSON feed every 5 minutes for significant earthquakes
- Filters for quakes with magnitude >= 4.5 (felt by humans)
- Auto-creates ActiveCrisis records for new earthquakes
- Broadcasts to the React dashboard via WebSocket

DATA SOURCE
────────────
USGS Earthquake Hazards Program (free, no API key needed):
https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson

This feed returns all M4.5+ earthquakes from the past hour, updated every minute.
"""
import logging
import uuid
import httpx
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActiveCrisis, CrisisStatus, DisasterType
from app.services.notifier import notifier

logger = logging.getLogger(__name__)

# USGS GeoJSON feed: M4.5+ earthquakes in the past hour
USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson"

# Track already-processed earthquake IDs to avoid duplicates across polls
_processed_quake_ids: set[str] = set()


async def poll_usgs_earthquakes(db: AsyncSession) -> list[dict[str, Any]]:
    """
    Fetch the latest M4.5+ earthquakes from USGS and create crises for new ones.

    Returns
    -------
    list of dicts describing newly created crises
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(USGS_FEED_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"USGS feed fetch failed: {e}")
        return []

    features = data.get("features", [])
    new_crises = []

    for feature in features:
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", [0, 0, 0])  # [lon, lat, depth_km]

        quake_id = feature.get("id", "")
        if quake_id in _processed_quake_ids:
            continue  # Already processed

        magnitude = props.get("mag", 0)
        place = props.get("place", "Unknown location")
        quake_time_ms = props.get("time", 0)
        tsunami = props.get("tsunami", 0)

        # Convert USGS timestamp (milliseconds) to datetime
        quake_dt = datetime.fromtimestamp(quake_time_ms / 1000, tz=timezone.utc)

        # Map magnitude to ADRC severity (1-5)
        if magnitude >= 7.0:
            severity = 5
        elif magnitude >= 6.0:
            severity = 4
        elif magnitude >= 5.0:
            severity = 3
        else:
            severity = 2

        # Affected radius based on magnitude
        affected_radius = int(magnitude * 15_000)  # rough: M5 = 75km, M7 = 105km

        lon, lat = coords[0], coords[1]
        location_wkt = f"SRID=4326;POINT({lon} {lat})"

        title = f"M{magnitude:.1f} Earthquake — {place}"
        description = (
            f"Magnitude {magnitude:.1f} earthquake detected by USGS.\n"
            f"Location: {place}\n"
            f"Depth: {coords[2]:.1f} km\n"
            f"Time: {quake_dt.isoformat()}\n"
            f"Tsunami warning: {'YES' if tsunami else 'No'}"
        )

        crisis = ActiveCrisis(
            id=uuid.uuid4(),
            disaster_type=DisasterType.EARTHQUAKE,
            severity=severity,
            title=title,
            description=description,
            location=location_wkt,
            affected_radius_m=affected_radius,
            warning_lead_time_h=0,  # earthquakes are sudden onset
            status=CrisisStatus.ACTIVE,
            orchestration_state={"phase": "RETRIEVAL", "source": "USGS_FEED"},
        )
        db.add(crisis)
        await db.flush()

        _processed_quake_ids.add(quake_id)

        crisis_data = {
            "id": str(crisis.id),
            "title": title,
            "disaster_type": "EARTHQUAKE",
            "severity": severity,
            "latitude": lat,
            "longitude": lon,
            "affected_radius_m": affected_radius,
            "status": "ACTIVE",
            "source": "USGS",
        }
        new_crises.append(crisis_data)

        # Broadcast to dashboard
        await notifier.broadcast("CRISIS_CONFIRMED", crisis_data)

        logger.info(
            f"🌍 USGS: New earthquake crisis created — {title} (Severity {severity})"
        )

    if new_crises:
        logger.info(f"🌍 USGS poll: {len(new_crises)} new earthquake(s) ingested")
    else:
        logger.debug("🌍 USGS poll: no new significant earthquakes")

    return new_crises
"""
app/services/earthquake_watcher.py
───────────────────────────────────
End of file.
"""
