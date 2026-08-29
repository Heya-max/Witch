"""create queue entries table

Revision ID: 0001_create_queue
Revises:
Create Date: 2026-08-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_create_queue"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "queue_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("track_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=True),
        sa.Column("requested_by", sa.BigInteger, nullable=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("queue_entries")
