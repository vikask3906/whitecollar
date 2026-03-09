"""
app/routers/crises.py
─────────────────────
Endpoints for Active Crises.

GET  /crises         — list all crises (filter by status, type)
POST /crises         — manually create a crisis (for dev/testing)
GET  /crises/{id}    — get a single crisis
PATCH /crises/{id}/status — update status (ACTIVE → CONTAINED → RESOLVED)
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ActiveCrisis, CrisisStatus, DisasterType
from app.schemas import ActiveCrisisCreate, ActiveCrisisOut, StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crises", tags=["Active Crises"])


@router.get(
    "",
    response_model=list[ActiveCrisisOut],
    summary="List Active Crises",
)
async def list_crises(
    crisis_status: CrisisStatus | None = Query(
        default=None, alias="status", description="Filter by status"
    ),
    disaster_type: DisasterType | None = Query(
        default=None, description="Filter by disaster type"
    ),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ActiveCrisis)
    if crisis_status:
        stmt = stmt.where(ActiveCrisis.status == crisis_status)
    if disaster_type:
        stmt = stmt.where(ActiveCrisis.disaster_type == disaster_type)
    stmt = stmt.order_by(ActiveCrisis.created_at.desc())

    result = await db.execute(stmt)
    return result.scalars().all()


@router.post(
    "",
    response_model=ActiveCrisisOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an Active Crisis (Dev/Testing)",
    description=(
        "Manually create a crisis for testing. "
        "In production, crises are created automatically after L2/L3 confirmation."
    ),
)
async def create_crisis(
    payload: ActiveCrisisCreate,
    db: AsyncSession = Depends(get_db),
):
    location_wkt = f"SRID=4326;POINT({payload.longitude} {payload.latitude})"

    crisis = ActiveCrisis(
        disaster_type=payload.disaster_type,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
        location=location_wkt,
        affected_radius_m=payload.affected_radius_m,
        warning_lead_time_h=payload.warning_lead_time_h,
        source_cluster_id=payload.source_cluster_id,
        orchestration_state={"phase": "RETRIEVAL"},
    )
    db.add(crisis)
    await db.flush()
    logger.info(
        f"Crisis created manually: {crisis.id} | {crisis.disaster_type} | "
        f"severity={crisis.severity}"
    )
    return crisis


@router.get(
    "/{crisis_id}",
    response_model=ActiveCrisisOut,
    summary="Get Crisis by ID",
)
async def get_crisis(crisis_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ActiveCrisis).where(ActiveCrisis.id == crisis_id)
    )
    crisis = result.scalar_one_or_none()
    if not crisis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crisis {crisis_id} not found.",
        )
    return crisis


@router.patch(
    "/{crisis_id}/status",
    response_model=ActiveCrisisOut,
    summary="Update Crisis Status",
)
async def update_crisis_status(
    crisis_id: uuid.UUID,
    new_status: CrisisStatus = Query(..., description="New status to set"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ActiveCrisis).where(ActiveCrisis.id == crisis_id)
    )
    crisis = result.scalar_one_or_none()
    if not crisis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crisis {crisis_id} not found.",
        )

    crisis.status = new_status
    crisis.updated_at = datetime.utcnow()
    logger.info(f"Crisis {crisis_id} status → {new_status}")
    return crisis
