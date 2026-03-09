"""
app/schemas.py
──────────────
Pydantic v2 schemas for API input/output validation.
Keeps ORM models separate from the API contract.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models import AssignmentStatus, ClusterStatus, CrisisStatus, DisasterType


# ── Helpers ───────────────────────────────────────────────────────────────────
class PointIn(BaseModel):
    """Longitude/Latitude pair for incoming request bodies."""
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)


# ═══════════════════════════════════════════════════════════════════════════════
#  TrustedNode schemas
# ═══════════════════════════════════════════════════════════════════════════════
class TrustedNodeCreate(BaseModel):
    phone: str = Field(..., pattern=r"^\+\d{10,15}$", example="+919810000001")
    name: str = Field(..., min_length=2, max_length=120)
    tier: int = Field(..., ge=1, le=3)
    preferred_language: str = Field(default="en", max_length=10)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)


class TrustedNodeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    phone: str
    name: str
    tier: int
    preferred_language: str
    is_active: bool
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
#  Twilio Webhook payload
#  Twilio POSTs application/x-www-form-urlencoded
# ═══════════════════════════════════════════════════════════════════════════════
class TwilioWebhookPayload(BaseModel):
    """
    Pydantic model for the Twilio SMS webhook body.
    Field names match Twilio's exact parameter names (PascalCase).
    """
    From: str = Field(..., alias="From")   # sender's E.164 phone number
    Body: str = Field(..., alias="Body")   # SMS body text
    # Optional geo fields Twilio may include
    FromCity: Optional[str] = Field(default=None, alias="FromCity")
    FromState: Optional[str] = Field(default=None, alias="FromState")
    FromCountry: Optional[str] = Field(default=None, alias="FromCountry")
    Latitude: Optional[float] = Field(default=None, alias="Latitude")
    Longitude: Optional[float] = Field(default=None, alias="Longitude")

    model_config = {"populate_by_name": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  CrisisReport schemas
# ═══════════════════════════════════════════════════════════════════════════════
class CrisisReportOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    reporter_phone: str
    raw_text: str
    translated_text: Optional[str]
    detected_language: Optional[str]
    is_spam: bool
    cluster_id: Optional[uuid.UUID]
    reported_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
#  ActiveCrisis schemas
# ═══════════════════════════════════════════════════════════════════════════════
class ActiveCrisisCreate(BaseModel):
    disaster_type: DisasterType
    severity: int = Field(default=1, ge=1, le=5)
    title: str = Field(..., min_length=5, max_length=200)
    description: Optional[str] = None
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    affected_radius_m: int = Field(default=5000, ge=100)
    warning_lead_time_h: int = Field(default=0, ge=0)
    source_cluster_id: Optional[uuid.UUID] = None


class ActiveCrisisOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    disaster_type: DisasterType
    severity: int
    title: str
    description: Optional[str]
    affected_radius_m: int
    warning_lead_time_h: int
    status: CrisisStatus
    orchestration_state: dict
    source_cluster_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
#  TaskAssignment schemas
# ═══════════════════════════════════════════════════════════════════════════════
class TaskAssignmentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    crisis_id: uuid.UUID
    node_id: uuid.UUID
    task_text_en: str
    task_text_local: Optional[str]
    language_sent: Optional[str]
    status: AssignmentStatus
    dispatched_at: datetime
    responded_at: Optional[datetime]


# ── Generic responses ─────────────────────────────────────────────────────────
class StatusResponse(BaseModel):
    status: str
    message: Optional[str] = None


class IngestResponse(BaseModel):
    status: str
    report_id: uuid.UUID
    cluster_triggered: bool = False
