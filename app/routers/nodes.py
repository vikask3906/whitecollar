"""
app/routers/nodes.py
────────────────────
CRUD endpoints for Trusted Nodes (L1/L2/L3 responders).

GET  /nodes        — list all active trusted nodes
POST /nodes        — register a new trusted node
GET  /nodes/{id}   — get a specific node by UUID
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TrustedNode
from app.schemas import TrustedNodeCreate, TrustedNodeOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nodes", tags=["Trusted Nodes"])


@router.get(
    "",
    response_model=list[TrustedNodeOut],
    summary="List Trusted Nodes",
)
async def list_nodes(
    tier: int | None = Query(default=None, ge=1, le=3, description="Filter by tier"),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TrustedNode)
    if tier is not None:
        stmt = stmt.where(TrustedNode.tier == tier)
    if active_only:
        stmt = stmt.where(TrustedNode.is_active == True)
    stmt = stmt.order_by(TrustedNode.tier.desc(), TrustedNode.name)

    result = await db.execute(stmt)
    nodes = result.scalars().all()
    return nodes


@router.post(
    "",
    response_model=TrustedNodeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a Trusted Node",
)
async def create_node(
    payload: TrustedNodeCreate,
    db: AsyncSession = Depends(get_db),
):
    # Check for duplicate phone
    existing = await db.execute(
        select(TrustedNode).where(TrustedNode.phone == payload.phone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A node with phone {payload.phone} already exists.",
        )

    # Build WKT geography if coordinates provided
    location_wkt: str | None = None
    if payload.longitude is not None and payload.latitude is not None:
        location_wkt = f"SRID=4326;POINT({payload.longitude} {payload.latitude})"

    node = TrustedNode(
        phone=payload.phone,
        name=payload.name,
        tier=payload.tier,
        preferred_language=payload.preferred_language,
        location=location_wkt,
    )
    db.add(node)
    await db.flush()
    logger.info(f"New trusted node registered: {node.id} (tier {node.tier})")
    return node


@router.get(
    "/{node_id}",
    response_model=TrustedNodeOut,
    summary="Get a Trusted Node by ID",
)
async def get_node(node_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TrustedNode).where(TrustedNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found.",
        )
    return node
