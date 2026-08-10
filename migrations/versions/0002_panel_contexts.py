"""Store the context rendered by each interface panel.

Revision ID: 0002_panel_contexts
Revises: 0001_initial
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_panel_contexts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "home_panels",
        sa.Column("context", sa.String(length=16), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "room_panels",
        sa.Column("context", sa.String(length=16), nullable=False, server_default="legacy"),
    )


def downgrade() -> None:
    with op.batch_alter_table("room_panels") as batch:
        batch.drop_column("context")
    with op.batch_alter_table("home_panels") as batch:
        batch.drop_column("context")
