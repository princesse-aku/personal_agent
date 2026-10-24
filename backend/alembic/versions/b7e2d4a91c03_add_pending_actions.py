"""add pending_actions

Revision ID: b7e2d4a91c03
Revises: a1f3c9d27b10
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d4a91c03"
down_revision: Union[str, Sequence[str], None] = "a1f3c9d27b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pending_actions_conversation_id", "pending_actions", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_actions_conversation_id", table_name="pending_actions")
    op.drop_table("pending_actions")
