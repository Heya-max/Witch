"""add payload column to queue entries

Revision ID: 0002_add_payload
Revises: 0001_create_queue
Create Date: 2026-08-29 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_add_payload"
down_revision = "0001_create_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("queue_entries", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("queue_entries", "payload")
