"""
app/services/weather_watcher.py
───────────────────────────────
IMD (India Meteorological Department) Weather Warning Watcher

WHAT THIS FILE DOES
────────────────────
- Polls the IMD RSS feed for district-wise nowcasts and severe weather warnings
- Detects cyclone, flood, and extreme weather alerts for Indian states
- Auto-creates ActiveCrisis records with warning_lead_time_h > 0 (PREDICTABLE)
- Broadcasts to the React dashboard via WebSocket

DATA SOURCES
─────────────
1. IMD District Nowcast RSS: https://mausam.imd.gov.in/imd_latest/contents/dist_nowcast_rss.php
2. For hackathon demo: Also includes OpenMeteo free API for severe weather
   (no key needed): https://api.open-meteo.com/v1/forecast

NOTE
─────
The IMD RSS feed requires IP whitelisting for production use.
For the hackathon demo, we also use the Open-Meteo API as a reliable fallback
to detect extreme weather conditions (heavy rain, high winds) in Indian cities.
"""
import logging
import uuid
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActiveCrisis, CrisisStatus, DisasterType
from app.services.notifier import notifier

logger = logging.getLogger(__name__)

# IMD RSS feed URL
IMD_RSS_URL = "https://mausam.imd.gov.in/imd_latest/contents/dist_nowcast_rss.php"

# Open-Meteo API for Indian metro cities (free, no key needed)
# Checks for extreme weather: heavy rain (>50mm), high winds (>80km/h)
INDIAN_CITIES = [
    {"name": "Delhi",     "lat": 28.6139, "lon": 77.2090},
    {"name": "Mumbai",    "lat": 19.0760, "lon": 72.8777},
    {"name": "Chennai",   "lat": 13.0827, "lon": 80.2707},
    {"name": "Kolkata",   "lat": 22.5726, "lon": 88.3639},
    {"name": "Bengaluru", "lat": 12.9716, "lon": 77.5946},
]

# Track already-processed alerts to avoid duplicates
_processed_weather_ids: set[str] = set()


async def poll_imd_warnings(db: AsyncSession) -> list[dict[str, Any]]:
    """
    Try fetching IMD RSS first. If it fails (IP not whitelisted),
    fall back to Open-Meteo extreme weather detection for Indian cities.
    """
    new_crises = []

    # ── Attempt 1: IMD RSS Feed ──────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(IMD_RSS_URL)
            if resp.status_code == 200:
                imd_crises = await _parse_imd_rss(db, resp.text)
                new_crises.extend(imd_crises)
                if imd_crises:
                    return new_crises
    except Exception as e:
        logger.warning(f"IMD RSS fetch failed (IP whitelist required): {e}")

    # ── Attempt 2: Open-Meteo Severe Weather Check ────────────────────────────
    try:
        meteo_crises = await _check_open_meteo_extremes(db)
        new_crises.extend(meteo_crises)
    except Exception as e:
        logger.error(f"Open-Meteo check failed: {e}")

    if not new_crises:
        logger.debug("🌦️ Weather poll: no severe weather warnings")

    return new_crises


async def _parse_imd_rss(db: AsyncSession, xml_text: str) -> list[dict]:
    """Parse IMD RSS feed XML and create crises for severe warnings."""
    new_crises = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            description = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            # Check if this is a severe warning
            severe_keywords = [
                "heavy rain", "cyclone", "flood", "thunderstorm",
                "warning", "alert", "red alert", "orange alert"
            ]
            title_lower = title.lower()
            is_severe = any(kw in title_lower for kw in severe_keywords)

            if not is_severe:
                continue

            alert_id = f"imd_{hash(title + pub_date)}"
            if alert_id in _processed_weather_ids:
                continue

            # Determine disaster type from keywords
            if "cyclone" in title_lower:
                dtype = DisasterType.CYCLONE
            elif "flood" in title_lower or "heavy rain" in title_lower:
                dtype = DisasterType.FLOOD
            elif "landslide" in title_lower:
                dtype = DisasterType.LANDSLIDE
            else:
                dtype = DisasterType.OTHER

            # Default location (center of India) — IMD RSS doesn't give coords
            location_wkt = "SRID=4326;POINT(78.9629 20.5937)"

            crisis = ActiveCrisis(
                id=uuid.uuid4(),
                disaster_type=dtype,
                severity=3,
                title=f"IMD WARNING: {title}",
                description=description[:500] if description else title,
                location=location_wkt,
                affected_radius_m=50000,
                warning_lead_time_h=24,  # forecasted = predictable
                status=CrisisStatus.ACTIVE,
                orchestration_state={"phase": "RETRIEVAL", "source": "IMD_RSS"},
            )
            db.add(crisis)
            await db.flush()

            _processed_weather_ids.add(alert_id)

            crisis_data = {
                "id": str(crisis.id),
                "title": f"IMD WARNING: {title}",
                "disaster_type": dtype.value,
                "severity": 3,
                "latitude": 20.5937,
                "longitude": 78.9629,
                "affected_radius_m": 50000,
                "status": "ACTIVE",
                "source": "IMD",
            }
            new_crises.append(crisis_data)
            await notifier.broadcast("CRISIS_CONFIRMED", crisis_data)

            logger.info(f"🌧️ IMD: New weather crisis — {title}")

    except ET.ParseError as e:
        logger.error(f"IMD RSS parse error: {e}")

    return new_crises


async def _check_open_meteo_extremes(db: AsyncSession) -> list[dict]:
    """
    Use the free Open-Meteo API to check for extreme weather in Indian cities.
    Triggers a crisis if: rain > 50mm/day OR wind > 80km/h.
    """
    new_crises = []

    for city in INDIAN_CITIES:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={city['lat']}&longitude={city['lon']}"
            f"&daily=precipitation_sum,wind_speed_10m_max"
            f"&timezone=Asia/Kolkata&forecast_days=1"
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.debug(f"Open-Meteo failed for {city['name']}: {e}")
            continue

        daily = data.get("daily", {})
        rain = (daily.get("precipitation_sum") or [0])[0]
        wind = (daily.get("wind_speed_10m_max") or [0])[0]

        alert_id = f"meteo_{city['name']}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        if alert_id in _processed_weather_ids:
            continue

        # Thresholds for severe weather
        is_extreme = False
        if rain and rain > 50:  # >50mm heavy rainfall
            dtype = DisasterType.FLOOD
            title = f"Heavy Rainfall Warning — {city['name']} ({rain:.0f}mm expected)"
            severity = 4 if rain > 100 else 3
            is_extreme = True
        elif wind and wind > 80:  # >80km/h severe wind
            dtype = DisasterType.CYCLONE
            title = f"Severe Wind Warning — {city['name']} ({wind:.0f}km/h expected)"
            severity = 4 if wind > 120 else 3
            is_extreme = True

        if not is_extreme:
            continue

        location_wkt = f"SRID=4326;POINT({city['lon']} {city['lat']})"

        crisis = ActiveCrisis(
            id=uuid.uuid4(),
            disaster_type=dtype,
            severity=severity,
            title=title,
            description=(
                f"Automated weather alert for {city['name']}.\n"
                f"Expected rainfall: {rain:.0f}mm\n"
                f"Expected max wind: {wind:.0f}km/h\n"
                f"Source: Open-Meteo API forecast"
            ),
            location=location_wkt,
            affected_radius_m=30000,
            warning_lead_time_h=12,
            status=CrisisStatus.ACTIVE,
            orchestration_state={"phase": "RETRIEVAL", "source": "OPEN_METEO"},
        )
        db.add(crisis)
        await db.flush()

        _processed_weather_ids.add(alert_id)

        crisis_data = {
            "id": str(crisis.id),
            "title": title,
            "disaster_type": dtype.value,
            "severity": severity,
            "latitude": city["lat"],
            "longitude": city["lon"],
            "affected_radius_m": 30000,
            "status": "ACTIVE",
            "source": "OPEN_METEO",
        }
        new_crises.append(crisis_data)
        await notifier.broadcast("CRISIS_CONFIRMED", crisis_data)

        logger.info(f"⛈️ Open-Meteo: Severe weather crisis — {title}")

    return new_crises
