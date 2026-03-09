"""
app/routers/orchestration.py
──────────────────────────────
API endpoints to trigger and manage the multi-agent orchestration pipeline.

POST /crises/{id}/orchestrate — kick off Retriever → Planner → HITL pause
GET  /crises/{id}/plan        — get the generated JSON SOP plan
POST /crises/{id}/approve     — human approves plan → transition to EXECUTION
"""
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import orchestrator
from app.database import get_db
from app.models import ActiveCrisis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crises", tags=["Orchestration"])


# ── Request/Response schemas ──────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    """Optional human edits submitted from the HITL dashboard."""
    comment: Optional[str] = None
    edited_tasks: Optional[list[dict]] = None  # tasks with modified text
    tasks: Optional[list[dict]] = None         # full replacement task list


class OrchestrationResponse(BaseModel):
    status: str
    phase: Optional[str] = None
    crisis_id: Optional[str] = None
    tasks_generated: Optional[int] = None
    tasks_count: Optional[int] = None
    plan: Optional[dict] = None
    message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /crises/{id}/orchestrate
# ═══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/{crisis_id}/orchestrate",
    response_model=OrchestrationResponse,
    summary="Trigger AutoGen Orchestration",
    description=(
        "Kicks off the Retriever → Planner → HITL Pause pipeline. "
        "Returns the generated JSON SOP plan and pauses for human review."
    ),
)
async def trigger_orchestration(
    crisis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await orchestrator.run_orchestration(db=db, crisis_id=crisis_id)

    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("message", "Orchestration failed"),
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /crises/{id}/plan
# ═══════════════════════════════════════════════════════════════════════════════
@router.get(
    "/{crisis_id}/plan",
    summary="Get Generated Plan",
    description="Returns the current JSON SOP plan from orchestration_state.",
)
async def get_plan(
    crisis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ActiveCrisis).where(ActiveCrisis.id == crisis_id)
    )
    crisis = result.scalar_one_or_none()

    if not crisis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crisis {crisis_id} not found",
        )

    orch_state = crisis.orchestration_state or {}
    plan = orch_state.get("plan")

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No plan generated yet. Current phase: "
                f"{orch_state.get('phase', 'UNKNOWN')}. "
                f"Trigger orchestration first via POST /crises/{crisis_id}/orchestrate"
            ),
        )

    return {
        "crisis_id": str(crisis_id),
        "phase": orch_state.get("phase"),
        "awaiting_approval": orch_state.get("awaiting_approval", False),
        "generated_at": orch_state.get("generated_at"),
        "tasks_count": len(plan.get("tasks", [])),
        "plan": plan,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /crises/{id}/approve
# ═══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/{crisis_id}/approve",
    response_model=OrchestrationResponse,
    summary="Approve Plan (HITL)",
    description=(
        "Human official approves the generated plan (optionally with edits). "
        "Transitions the crisis from HITL_REVIEW to EXECUTION phase."
    ),
)
async def approve_plan(
    crisis_id: uuid.UUID,
    body: Optional[ApproveRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    human_edits = body.model_dump(exclude_none=True) if body else None

    result = await orchestrator.approve_plan(
        db=db,
        crisis_id=crisis_id,
        human_edits=human_edits,
    )

    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Approval failed"),
        )

    return result
