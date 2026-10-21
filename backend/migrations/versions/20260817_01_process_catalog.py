"""Crea el catalogo permanente de procesos y diagramas.

Revision ID: 20260817_01
Revises:
Create Date: 2026-08-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=40), server_default="PENTAHO", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_repositories_normalized_name"),
    )

    op.create_table(
        "processes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("original_name", sa.String(length=500), nullable=True),
        sa.Column("normalized_name", sa.String(length=500), nullable=False),
        sa.Column("process_type", sa.String(length=30), nullable=False),
        sa.Column("path", sa.String(length=1500), nullable=False),
        sa.Column("original_path", sa.String(length=1500), nullable=True),
        sa.Column("normalized_path", sa.String(length=1500), nullable=False),
        sa.Column("full_path", sa.String(length=2000), nullable=True),
        sa.Column("internal_identifier", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "process_type IN ('JOB', 'TRANSFORMATION')",
            name="ck_processes_type",
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "source_key",
            name="uq_processes_repository_source_key",
        ),
    )
    op.create_index(
        "ix_processes_repository_name",
        "processes",
        ["repository_id", "normalized_name"],
    )
    op.create_index(
        "ix_processes_repository_path",
        "processes",
        ["repository_id", "normalized_path"],
    )

    op.create_table(
        "documented_roots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("root_process_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column(
            "documented_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')",
            name="ck_documented_roots_status",
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["root_process_id"], ["processes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "root_process_id",
            name="uq_documented_roots_repository_process",
        ),
    )

    op.create_table(
        "documented_root_processes",
        sa.Column("documented_root_id", sa.Uuid(), nullable=False),
        sa.Column("process_id", sa.Uuid(), nullable=False),
        sa.Column("is_root", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["documented_root_id"],
            ["documented_roots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("documented_root_id", "process_id"),
    )

    op.create_table(
        "process_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("source_process_id", sa.Uuid(), nullable=False),
        sa.Column("target_process_id", sa.Uuid(), nullable=True),
        sa.Column("entry_name", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("original_reference", sa.Text(), nullable=True),
        sa.Column("resolution_criterion", sa.String(length=200), nullable=True),
        sa.Column("reason_code", sa.String(length=200), nullable=True),
        sa.Column("target_name_snapshot", sa.String(length=500), nullable=True),
        sa.Column("target_path_snapshot", sa.String(length=1500), nullable=True),
        sa.Column("target_type_snapshot", sa.String(length=30), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_process_id"], ["processes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "source_key",
            name="uq_process_relations_repository_source_key",
        ),
    )
    op.create_index("ix_process_relations_source", "process_relations", ["source_process_id"])
    op.create_index("ix_process_relations_target", "process_relations", ["target_process_id"])

    op.create_table(
        "process_diagrams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("process_id", sa.Uuid(), nullable=False),
        sa.Column("recommended_width", sa.Integer(), nullable=False),
        sa.Column("recommended_height", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("process_id", name="uq_process_diagrams_process"),
    )

    op.create_table(
        "diagram_elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=1500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("element_type", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("source_x", sa.Float(), nullable=False),
        sa.Column("source_y", sa.Float(), nullable=False),
        sa.Column("display_x", sa.Float(), nullable=False),
        sa.Column("display_y", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("navigable", sa.Boolean(), nullable=False),
        sa.Column("target_process_id", sa.Uuid(), nullable=True),
        sa.Column("target_status", sa.String(length=80), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["diagram_id"], ["process_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_process_id"], ["processes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "diagram_id",
            "source_key",
            name="uq_diagram_elements_diagram_source_key",
        ),
    )
    op.create_index("ix_diagram_elements_target", "diagram_elements", ["target_process_id"])

    op.create_table(
        "diagram_hops",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("origin_name", sa.String(length=500), nullable=False),
        sa.Column("destination_name", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("evaluation", sa.String(length=100), nullable=True),
        sa.Column("unconditional", sa.Boolean(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["diagram_id"], ["process_diagrams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "diagram_id",
            "source_key",
            name="uq_diagram_hops_diagram_source_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("diagram_hops")
    op.drop_index("ix_diagram_elements_target", table_name="diagram_elements")
    op.drop_table("diagram_elements")
    op.drop_table("process_diagrams")
    op.drop_index("ix_process_relations_target", table_name="process_relations")
    op.drop_index("ix_process_relations_source", table_name="process_relations")
    op.drop_table("process_relations")
    op.drop_table("documented_root_processes")
    op.drop_table("documented_roots")
    op.drop_index("ix_processes_repository_path", table_name="processes")
    op.drop_index("ix_processes_repository_name", table_name="processes")
    op.drop_table("processes")
    op.drop_table("repositories")
