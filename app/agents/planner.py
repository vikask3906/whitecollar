"""
app/agents/planner.py
──────────────────────
Agent 2: The Planner — Strategy Synthesizer

WHAT THIS FILE DOES
────────────────────
- Receives SOPs from the Retriever + live crisis context
- Calls Azure OpenAI GPT-4o with a strict RAG system prompt
- The LLM MUST NOT invent procedures — only produces from official SOPs
- Generates a prioritized JSON SOP (tasks, zones, resource needs)

AZURE OPENAI SETUP
───────────────────
Fill in .env:
    AZURE_OPENAI_ENDPOINT=https://YOUR.openai.azure.com/
    AZURE_OPENAI_API_KEY=<Key 1>
    AZURE_OPENAI_DEPLOYMENT=gpt-4o

Without key → returns a mock template JSON SOP for local testing.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.models import ActiveCrisis, DisasterType

logger = logging.getLogger(__name__)
settings = get_settings()

# ── System prompt: enforces strict RAG behavior ──────────────────────────────
_SYSTEM_PROMPT = """You are an NDMA-certified Disaster Response Planner operating under the 
National Disaster Management Authority (NDMA) of India.

CRITICAL RULES:
1. You MUST ONLY generate response plans based on the official SOPs provided below.
2. You MUST NOT invent, assume, or hallucinate any procedures not in the SOPs.
3. If the SOPs do not cover a specific scenario, say "NO SOP COVERAGE" for that item.
4. All tasks must be prioritized by life-safety first, then property, then environment.

OUTPUT FORMAT:
You MUST return a valid JSON object with this exact structure:
{
    "incident_summary": "Brief description of the crisis",
    "disaster_type": "FIRE|FLOOD|EARTHQUAKE|...",
    "severity_assessment": 1-5,
    "estimated_affected_population": number,
    "phase": "IMMEDIATE|SHORT_TERM|LONG_TERM",
    "tasks": [
        {
            "id": 1,
            "priority": "CRITICAL|HIGH|MEDIUM|LOW",
            "action": "Specific action text from SOP",
            "sop_reference": "Section number from SOP",
            "assigned_tier": 2 or 3,
            "resource_needed": "Description of resources",
            "zone": "Description of deployment zone",
            "estimated_time_minutes": number
        }
    ],
    "evacuation_zones": [
        {
            "zone_name": "Description",
            "radius_meters": number,
            "priority": "IMMEDIATE|STAGED|MONITOR"
        }
    ],
    "resource_summary": {
        "fire_tenders": number,
        "ambulances": number,
        "ndrf_teams": number,
        "boats": number,
        "shelters": number
    },
    "communication_plan": "Summary of communication protocol from SOP"
}

Return ONLY the JSON object. No markdown, no explanation, no preamble."""


async def generate_plan(
    crisis: ActiveCrisis,
    sop_text: str,
) -> dict[str, Any]:
    """
    Generate a prioritized JSON SOP plan using Azure OpenAI GPT-4o.

    Parameters
    ----------
    crisis   : ActiveCrisis ORM object with disaster details
    sop_text : Concatenated SOP text from the Retriever agent

    Returns
    -------
    dict — JSON SOP plan
    """
    disaster_label = crisis.disaster_type.value if crisis.disaster_type else "UNKNOWN"

    # Build the user message with crisis context + retrieved SOPs
    user_message = _build_user_message(crisis, sop_text)

    # ── Try Azure OpenAI ──────────────────────────────────────────────────────
    if settings.azure_openai_api_key and settings.azure_openai_endpoint:
        plan = await _call_azure_openai(user_message)
        if plan:
            logger.info(
                f"Planner: GPT-4o generated plan with {len(plan.get('tasks', []))} tasks"
            )
            return plan

    # ── Fallback: mock template plan ──────────────────────────────────────────
    logger.warning(
        "AZURE_OPENAI_API_KEY not set — returning mock template plan. "
        "Set it in .env for real GPT-4o planning."
    )
    return _mock_plan(crisis, disaster_label)


def _build_user_message(crisis: ActiveCrisis, sop_text: str) -> str:
    """Build the full user prompt with crisis context and SOPs."""
    disaster_label = crisis.disaster_type.value if crisis.disaster_type else "UNKNOWN"
    
    return f"""INCIDENT REPORT:
- Type: {disaster_label}
- Title: {crisis.title}
- Description: {crisis.description or 'No additional details'}
- Severity: {crisis.severity}/5
- Warning Lead Time: {crisis.warning_lead_time_h} hours ({"SUDDEN ONSET" if crisis.warning_lead_time_h == 0 else "PREDICTABLE"})
- Affected Radius: {crisis.affected_radius_m}m
- Timestamp: {datetime.now(timezone.utc).isoformat()}

OFFICIAL SOPs (BASE YOUR PLAN EXCLUSIVELY ON THESE):
{sop_text}

Generate the JSON response plan now. Remember: ONLY use procedures from the SOPs above."""


async def _call_azure_openai(user_message: str) -> dict | None:
    """Call Azure OpenAI GPT-4o and parse the JSON response."""
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version="2024-06-01",
        )

        response = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,          # low temp for deterministic, factual output
            max_tokens=4000,
            response_format={"type": "json_object"},   # enforce JSON output
        )

        raw_content = response.choices[0].message.content
        plan = json.loads(raw_content)
        logger.info(f"Planner: GPT-4o returned valid JSON plan")
        return plan

    except json.JSONDecodeError as e:
        logger.error(f"Planner: GPT-4o returned invalid JSON: {e}")
    except Exception as e:
        logger.error(f"Planner: Azure OpenAI error: {e}")
    
    return None


def _mock_plan(crisis: ActiveCrisis, disaster_label: str) -> dict:
    """Return a mock JSON SOP plan for local testing without Azure OpenAI."""
    return {
        "incident_summary": crisis.title,
        "disaster_type": disaster_label,
        "severity_assessment": crisis.severity,
        "estimated_affected_population": 500,
        "phase": "IMMEDIATE",
        "tasks": [
            {
                "id": 1,
                "priority": "CRITICAL",
                "action": f"Establish {200 if disaster_label == 'FIRE' else 500}m perimeter around incident site",
                "sop_reference": "Section 1.2",
                "assigned_tier": 3,
                "resource_needed": "Police barricades, traffic cones",
                "zone": f"Within {crisis.affected_radius_m}m of epicentre",
                "estimated_time_minutes": 15,
            },
            {
                "id": 2,
                "priority": "CRITICAL",
                "action": "Evacuate all persons within inner perimeter",
                "sop_reference": "Section 2.1",
                "assigned_tier": 2,
                "resource_needed": "Evacuation buses, megaphones",
                "zone": "Inner zone — residential and commercial",
                "estimated_time_minutes": 30,
            },
            {
                "id": 3,
                "priority": "HIGH",
                "action": "Deploy emergency medical support to staging area",
                "sop_reference": "Section 3.2",
                "assigned_tier": 2,
                "resource_needed": "1 ambulance per 50 evacuees",
                "zone": "Staging area — 500m from incident",
                "estimated_time_minutes": 20,
            },
            {
                "id": 4,
                "priority": "HIGH",
                "action": "Set up relief camp at nearest designated safe zone",
                "sop_reference": "Section 5.1",
                "assigned_tier": 2,
                "resource_needed": "Tents, water, food supplies",
                "zone": "Designated rally point / open ground",
                "estimated_time_minutes": 60,
            },
            {
                "id": 5,
                "priority": "MEDIUM",
                "action": "Activate public advisory via SMS blast to area residents",
                "sop_reference": "Section 4.3",
                "assigned_tier": 3,
                "resource_needed": "SMS gateway, message template",
                "zone": f"Within {crisis.affected_radius_m * 2}m radius",
                "estimated_time_minutes": 10,
            },
        ],
        "evacuation_zones": [
            {
                "zone_name": "Inner perimeter — immediate danger",
                "radius_meters": crisis.affected_radius_m,
                "priority": "IMMEDIATE",
            },
            {
                "zone_name": "Outer perimeter — caution zone",
                "radius_meters": crisis.affected_radius_m * 2,
                "priority": "STAGED",
            },
        ],
        "resource_summary": {
            "fire_tenders": 2 if disaster_label == "FIRE" else 0,
            "ambulances": 3,
            "ndrf_teams": 1,
            "boats": 5 if disaster_label in ("FLOOD", "CYCLONE") else 0,
            "shelters": 2,
        },
        "communication_plan": (
            "Incident Commander on NDRF Channel 5. "
            "State EOC updates every 15 min. "
            "Public SMS advisory within 2km. "
            "Media briefing at 60 min mark."
        ),
        "_meta": {
            "generated_by": "MOCK_TEMPLATE (Azure OpenAI not configured)",
            "note": "Set AZURE_OPENAI_API_KEY in .env for real GPT-4o plans",
        },
    }
