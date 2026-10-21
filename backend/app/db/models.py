from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Repository(TimestampMixin, Base):
    """Repositorio logico documentado; nunca almacena el XML original."""

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "normalized_name",
            name="uq_repositories_normalized_name",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="PENTAHO",
        server_default="PENTAHO",
    )

    processes: Mapped[list[Process]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )
    documented_roots: Mapped[list[DocumentedRoot]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class Process(TimestampMixin, Base):
    """Job o transformacion unica dentro de un repositorio."""

    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "source_key",
            name="uq_processes_repository_source_key",
        ),
        CheckConstraint(
            "process_type IN ('JOB', 'TRANSFORMATION')",
            name="ck_processes_type",
        ),
        Index("ix_processes_repository_name", "repository_id", "normalized_name"),
        Index("ix_processes_repository_path", "repository_id", "normalized_path"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    process_type: Mapped[str] = mapped_column(String(30), nullable=False)
    path: Mapped[str] = mapped_column(String(1500), nullable=False)
    original_path: Mapped[str | None] = mapped_column(String(1500))
    normalized_path: Mapped[str] = mapped_column(String(1500), nullable=False)
    full_path: Mapped[str | None] = mapped_column(String(2000))
    internal_identifier: Mapped[str | None] = mapped_column(String(500))

    repository: Mapped[Repository] = relationship(back_populates="processes")
    diagram: Mapped[ProcessDiagram | None] = relationship(
        back_populates="process",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DocumentedRoot(TimestampMixin, Base):
    """Proceso raiz que el usuario decidio documentar permanentemente."""

    __tablename__ = "documented_roots"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "root_process_id",
            name="uq_documented_roots_repository_process",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')",
            name="ck_documented_roots_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    root_process_id: Mapped[UUID] = mapped_column(
        ForeignKey("processes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )
    documented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    repository: Mapped[Repository] = relationship(back_populates="documented_roots")
    root_process: Mapped[Process] = relationship(foreign_keys=[root_process_id])
    process_links: Mapped[list[DocumentedRootProcess]] = relationship(
        back_populates="documented_root",
        cascade="all, delete-orphan",
    )


class DocumentedRootProcess(Base):
    """Procesos alcanzables que forman parte de una documentacion."""

    __tablename__ = "documented_root_processes"

    documented_root_id: Mapped[UUID] = mapped_column(
        ForeignKey("documented_roots.id", ondelete="CASCADE"),
        primary_key=True,
    )
    process_id: Mapped[UUID] = mapped_column(
        ForeignKey("processes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_root: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    depth: Mapped[int | None] = mapped_column(Integer)

    documented_root: Mapped[DocumentedRoot] = relationship(
        back_populates="process_links"
    )
    process: Mapped[Process] = relationship()


class ProcessRelation(TimestampMixin, Base):
    """Llamada entre dos procesos del catalogo del repositorio."""

    __tablename__ = "process_relations"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "source_key",
            name="uq_process_relations_repository_source_key",
        ),
        Index("ix_process_relations_source", "source_process_id"),
        Index("ix_process_relations_target", "target_process_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_process_id: Mapped[UUID] = mapped_column(
        ForeignKey("processes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_process_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processes.id", ondelete="SET NULL")
    )
    entry_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    original_reference: Mapped[str | None] = mapped_column(Text)
    resolution_criterion: Mapped[str | None] = mapped_column(String(200))
    reason_code: Mapped[str | None] = mapped_column(String(200))
    target_name_snapshot: Mapped[str | None] = mapped_column(String(500))
    target_path_snapshot: Mapped[str | None] = mapped_column(String(1500))
    target_type_snapshot: Mapped[str | None] = mapped_column(String(30))

    source_process: Mapped[Process] = relationship(foreign_keys=[source_process_id])
    target_process: Mapped[Process | None] = relationship(foreign_keys=[target_process_id])


class ProcessDiagram(TimestampMixin, Base):
    """Lienzo navegable asociado a un Job o transformacion."""

    __tablename__ = "process_diagrams"
    __table_args__ = (
        UniqueConstraint("process_id", name="uq_process_diagrams_process"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    process_id: Mapped[UUID] = mapped_column(
        ForeignKey("processes.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommended_width: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_height: Mapped[int] = mapped_column(Integer, nullable=False)

    process: Mapped[Process] = relationship(back_populates="diagram")
    elements: Mapped[list[DiagramElement]] = relationship(
        back_populates="diagram",
        cascade="all, delete-orphan",
        order_by="DiagramElement.display_order",
    )
    hops: Mapped[list[DiagramHop]] = relationship(
        back_populates="diagram",
        cascade="all, delete-orphan",
        order_by="DiagramHop.display_order",
    )


class DiagramElement(TimestampMixin, Base):
    """Rectangulo del diagrama, con posicion original y posicion editable."""

    __tablename__ = "diagram_elements"
    __table_args__ = (
        UniqueConstraint(
            "diagram_id",
            "source_key",
            name="uq_diagram_elements_diagram_source_key",
        ),
        Index("ix_diagram_elements_target", "target_process_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    diagram_id: Mapped[UUID] = mapped_column(
        ForeignKey("process_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(1500), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    element_type: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    source_x: Mapped[float] = mapped_column(Float, nullable=False)
    source_y: Mapped[float] = mapped_column(Float, nullable=False)
    display_x: Mapped[float] = mapped_column(Float, nullable=False)
    display_y: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    navigable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    target_process_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processes.id", ondelete="SET NULL")
    )
    target_status: Mapped[str | None] = mapped_column(String(80))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    diagram: Mapped[ProcessDiagram] = relationship(back_populates="elements")
    target_process: Mapped[Process | None] = relationship()


class DiagramHop(TimestampMixin, Base):
    """Conexion visual entre dos elementos de un diagrama."""

    __tablename__ = "diagram_hops"
    __table_args__ = (
        UniqueConstraint(
            "diagram_id",
            "source_key",
            name="uq_diagram_hops_diagram_source_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    diagram_id: Mapped[UUID] = mapped_column(
        ForeignKey("process_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_name: Mapped[str] = mapped_column(String(500), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluation: Mapped[str | None] = mapped_column(String(100))
    unconditional: Mapped[bool] = mapped_column(Boolean, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    diagram: Mapped[ProcessDiagram] = relationship(back_populates="hops")
