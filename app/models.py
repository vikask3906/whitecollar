"""
app/models.py
─────────────
SQLAlchemy ORM models — mirror the DDL in db/init.sql.
GeoAlchemy2 Geography type is used for all spatial columns.
"""
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ── Enum mirrors (must match db/init.sql) ─────────────────────────────────────
import enum


class ClusterStatus(str, enum.Enum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class CrisisStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CONTAINED = "CONTAINED"
    RESOLVED = "RESOLVED"


class DisasterType(str, enum.Enum):
    FLOOD = "FLOOD"
    CYCLONE = "CYCLONE"
    EARTHQUAKE = "EARTHQUAKE"
    FIRE = "FIRE"
    GAS_LEAK = "GAS_LEAK"
    LANDSLIDE = "LANDSLIDE"
    OTHER = "OTHER"


class AssignmentStatus(str, enum.Enum):
    DISPATCHED = "DISPATCHED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


# ── Helpers ───────────────────────────────────────────────────────────────────
def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ═══════════════════════════════════════════════════════════════════════════════
#  TrustedNode
# ═══════════════════════════════════════════════════════════════════════════════
class TrustedNode(Base):
    __tablename__ = "trusted_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tier: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("tier BETWEEN 1 AND 3", name="ck_tier"),
        nullable=False,
    )
    preferred_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="en"
    )
    # Geography(POINT, 4326) stores (lon, lat)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # relationships
    assignments: Mapped[list["TaskAssignment"]] = relationship(
        back_populates="node"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ReportCluster
# ═══════════════════════════════════════════════════════════════════════════════
class ReportCluster(Base):
    __tablename__ = "report_clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    disaster_type: Mapped[DisasterType | None] = mapped_column(
        Enum(DisasterType, name="disaster_type"), nullable=True
    )
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=False
    )
    radius_m: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    report_count: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    status: Mapped[ClusterStatus] = mapped_column(
        Enum(ClusterStatus, name="cluster_status"),
        default=ClusterStatus.PENDING_VERIFICATION,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # relationships
    reports: Mapped[list["CrisisReport"]] = relationship(back_populates="cluster")
    crises: Mapped[list["ActiveCrisis"]] = relationship(back_populates="source_cluster")


# ═══════════════════════════════════════════════════════════════════════════════
#  CrisisReport
# ═══════════════════════════════════════════════════════════════════════════════
class CrisisReport(Base):
    __tablename__ = "crisis_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    reporter_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_clusters.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # relationships
    cluster: Mapped["ReportCluster | None"] = relationship(back_populates="reports")


# ═══════════════════════════════════════════════════════════════════════════════
#  ActiveCrisis
# ═══════════════════════════════════════════════════════════════════════════════
class ActiveCrisis(Base):
    __tablename__ = "active_crises"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    disaster_type: Mapped[DisasterType] = mapped_column(
        Enum(DisasterType, name="disaster_type"), nullable=False
    )
    severity: Mapped[int] = mapped_column(
        SmallInteger,
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_severity"),
        default=1,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=False
    )
    affected_radius_m: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    warning_lead_time_h: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    status: Mapped[CrisisStatus] = mapped_column(
        Enum(CrisisStatus, name="crisis_status"),
        default=CrisisStatus.ACTIVE,
        nullable=False,
    )
    orchestration_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=lambda: {"phase": "RETRIEVAL"}
    )
    source_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_clusters.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # relationships
    source_cluster: Mapped["ReportCluster | None"] = relationship(
        back_populates="crises"
    )
    assignments: Mapped[list["TaskAssignment"]] = relationship(
        back_populates="crisis"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  TaskAssignment
# ═══════════════════════════════════════════════════════════════════════════════
class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    crisis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("active_crises.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trusted_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_text_en: Mapped[str] = mapped_column(Text, nullable=False)
    task_text_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_sent: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, name="assignment_status"),
        default=AssignmentStatus.DISPATCHED,
        nullable=False,
    )
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # relationships
    crisis: Mapped["ActiveCrisis"] = relationship(back_populates="assignments")
    node: Mapped["TrustedNode"] = relationship(back_populates="assignments")
