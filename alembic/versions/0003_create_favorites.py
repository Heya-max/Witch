"""create favorite entries table

Revision ID: 0003_create_favorites
Revises: 0002_add_payload
Create Date: 2026-08-30 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_create_favorites"
down_revision = "0002_add_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorite_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_favorite_entries_user_id"), "favorite_entries", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_favorite_entries_user_id"), table_name="favorite_entries")
    op.drop_table("favorite_entries")
