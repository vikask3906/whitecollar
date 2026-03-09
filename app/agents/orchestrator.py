"""
app/agents/orchestrator.py
───────────────────────────
Multi-Agent Orchestrator — Coordinates Retriever → Planner → HITL Pause

WHAT THIS FILE DOES
────────────────────
- Called when an Active Crisis needs orchestration
- Phase 1: RETRIEVAL — calls Retriever agent to fetch SOPs
- Phase 2: PLANNING  — calls Planner agent to generate JSON SOP
- Phase 3: HITL_REVIEW — pauses, saves plan in DB, waits for human approval
- Phase 4: EXECUTION — [Step 5] Executor agent dispatches tasks

HOW IT'S CALLED
────────────────
From the API endpoint: POST /crises/{id}/orchestrate
    result = await run_orchestration(db, crisis_id)

Or from the crisis watcher background task (future).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import retriever, planner
from app.models import ActiveCrisis, CrisisStatus

logger = logging.getLogger(__name__)


async def run_orchestration(
    db: AsyncSession,
    crisis_id: uuid.UUID,
) -> dict[str, Any]:
    """
    Run the full Retriever → Planner → HITL Pause orchestration pipeline.

    Parameters
    ----------
    db         : async SQLAlchemy session
    crisis_id  : UUID of the Active Crisis to process

    Returns
    -------
    dict with keys: status, phase, plan (the generated JSON SOP)
    """
    # ── Load the crisis ───────────────────────────────────────────────────────
    result = await db.execute(
        select(ActiveCrisis).where(ActiveCrisis.id == crisis_id)
    )
    crisis = result.scalar_one_or_none()

    if not crisis:
        logger.error(f"Orchestrator: crisis {crisis_id} not found")
        return {"status": "error", "message": f"Crisis {crisis_id} not found"}

    if crisis.status != CrisisStatus.ACTIVE:
        logger.warning(
            f"Orchestrator: crisis {crisis_id} status is {crisis.status} — "
            "only ACTIVE crises can be orchestrated."
        )
        return {
            "status": "skipped",
            "message": f"Crisis status is {crisis.status}, not ACTIVE",
        }

    disaster_label = crisis.disaster_type.value if crisis.disaster_type else "UNKNOWN"
    logger.info(
        f"🤖 Orchestrator starting for crisis {crisis_id} | {disaster_label}"
    )

    # ═══════════════════════════════════════════════════════════════════════════
    #  PHASE 1: RETRIEVAL
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info("📚 Phase 1: RETRIEVAL — fetching SOPs...")
    await _update_phase(db, crisis, "RETRIEVAL")

    sop_text = await retriever.retrieve_sops(
        disaster_type=crisis.disaster_type,
        region=crisis.title,           # use title as rough region hint
        crisis_description=crisis.description,
    )

    sop_length = len(sop_text)
    logger.info(f"📚 Retriever returned {sop_length} chars of SOP text")

    # ═══════════════════════════════════════════════════════════════════════════
    #  PHASE 2: PLANNING
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info("📋 Phase 2: PLANNING — generating JSON SOP via GPT-4o...")
    await _update_phase(db, crisis, "PLANNING")

    plan = await planner.generate_plan(
        crisis=crisis,
        sop_text=sop_text,
    )

    task_count = len(plan.get("tasks", []))
    logger.info(f"📋 Planner generated plan with {task_count} tasks")

    # ═══════════════════════════════════════════════════════════════════════════
    #  PHASE 3: HITL_REVIEW (PAUSE)
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(
        "⏸️  Phase 3: HITL_REVIEW — plan saved, waiting for human approval..."
    )
    crisis.orchestration_state = {
        "phase": "HITL_REVIEW",
        "plan": plan,
        "sop_chars_retrieved": sop_length,
        "tasks_generated": task_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "awaiting_approval": True,
    }
    crisis.updated_at = datetime.now(timezone.utc)
    await db.flush()

    logger.info(
        f"✅ Orchestration paused at HITL_REVIEW for crisis {crisis_id}. "
        f"Plan has {task_count} tasks. Waiting for human approval via "
        f"POST /crises/{crisis_id}/approve"
    )

    return {
        "status": "paused_for_review",
        "phase": "HITL_REVIEW",
        "crisis_id": str(crisis_id),
        "tasks_generated": task_count,
        "plan": plan,
    }


async def approve_plan(
    db: AsyncSession,
    crisis_id: uuid.UUID,
    human_edits: dict | None = None,
) -> dict[str, Any]:
    """
    Resume orchestration after human approves (or edits) the plan.

    Parameters
    ----------
    db          : async SQLAlchemy session
    crisis_id   : UUID of the Active Crisis
    human_edits : optional dict with edited plan or comment from dashboard

    Returns
    -------
    dict with status and the approved plan
    """
    result = await db.execute(
        select(ActiveCrisis).where(ActiveCrisis.id == crisis_id)
    )
    crisis = result.scalar_one_or_none()

    if not crisis:
        return {"status": "error", "message": f"Crisis {crisis_id} not found"}

    orch_state = crisis.orchestration_state or {}
    current_phase = orch_state.get("phase", "")

    if current_phase != "HITL_REVIEW":
        return {
            "status": "error",
            "message": (
                f"Crisis is in phase '{current_phase}', not HITL_REVIEW. "
                "Cannot approve."
            ),
        }

    # ── Apply human edits if provided ────────────────────────────────────────
    plan = orch_state.get("plan", {})
    if human_edits:
        # Merge human edits into the plan
        if "tasks" in human_edits:
            plan["tasks"] = human_edits["tasks"]
        if "comment" in human_edits:
            plan["human_comment"] = human_edits["comment"]
        if "edited_tasks" in human_edits:
            # Replace specific tasks by ID
            existing_map = {t["id"]: t for t in plan.get("tasks", [])}
            for edited_task in human_edits["edited_tasks"]:
                existing_map[edited_task["id"]] = edited_task
            plan["tasks"] = list(existing_map.values())

        logger.info(f"Human edits applied to plan for crisis {crisis_id}")

    # ── Transition to EXECUTION phase ────────────────────────────────────────
    crisis.orchestration_state = {
        "phase": "EXECUTION",
        "plan": plan,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": "dashboard_user",   # will come from auth in production
        "awaiting_approval": False,
    }
    crisis.updated_at = datetime.now(timezone.utc)
    await db.flush()

    task_count = len(plan.get("tasks", []))
    logger.info(
        f"✅ Plan approved for crisis {crisis_id}. "
        f"Phase → EXECUTION ({task_count} tasks ready for dispatch)"
    )

    # TODO (Step 5): Trigger Executor agent to dispatch tasks to L2/L3 nodes
    # await executor.dispatch_tasks(db, crisis, plan)

    return {
        "status": "approved",
        "phase": "EXECUTION",
        "crisis_id": str(crisis_id),
        "tasks_count": task_count,
        "plan": plan,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _update_phase(
    db: AsyncSession,
    crisis: ActiveCrisis,
    phase: str,
):
    """Update crisis orchestration_state phase in the database."""
    current_state = crisis.orchestration_state or {}
    current_state["phase"] = phase
    crisis.orchestration_state = current_state
    crisis.updated_at = datetime.now(timezone.utc)
    await db.flush()
