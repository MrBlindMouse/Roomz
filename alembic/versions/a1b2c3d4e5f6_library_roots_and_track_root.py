"""library_roots and tracks.library_root_id, drop filename unique

Revision ID: a1b2c3d4e5f6
Revises: 934cd24cd9b0
Create Date: 2026-03-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "934cd24cd9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create library_roots table
    op.create_table(
        "library_roots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_library_roots_path"),
        "library_roots",
        ["path"],
        unique=True,
    )

    # Add library_root_id to tracks (nullable)
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("library_root_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_tracks_library_root_id",
            "library_roots",
            ["library_root_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Insert default library root (data/music)
    conn = op.get_bind()
    from pathlib import Path
    from datetime import UTC, datetime
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    default_music = str((project_root / "data" / "music").resolve())
    conn.execute(
        sa.text(
            "INSERT INTO library_roots (path, name, created_at) VALUES (:path, :name, :created_at)"
        ),
        {"path": default_music, "name": "Default", "created_at": datetime.now(UTC).isoformat()},
    )
    result = conn.execute(sa.text("SELECT id FROM library_roots WHERE path = :path"), {"path": default_music})
    row = result.fetchone()
    default_root_id = row[0] if row else 1

    # Backfill existing tracks with default root
    conn.execute(
        sa.text("UPDATE tracks SET library_root_id = :rid WHERE library_root_id IS NULL"),
        {"rid": default_root_id},
    )

    # Drop unique constraint on filename (SQLite: drop the unique index)
    op.drop_index(op.f("ix_tracks_filename"), table_name="tracks")
    op.create_index(
        op.f("ix_tracks_filename"),
        "tracks",
        ["filename"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tracks_filepath"),
        "tracks",
        ["filepath"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tracks_filepath"), table_name="tracks")
    op.drop_index(op.f("ix_tracks_filename"), table_name="tracks")
    op.create_index(
        op.f("ix_tracks_filename"),
        "tracks",
        ["filename"],
        unique=True,
    )
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tracks_library_root_id", type_="foreignkey")
        batch_op.drop_column("library_root_id")
    op.drop_index(op.f("ix_library_roots_path"), table_name="library_roots")
    op.drop_table("library_roots")
