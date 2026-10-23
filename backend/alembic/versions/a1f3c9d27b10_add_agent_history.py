"""add agent_history to conversations

Revision ID: a1f3c9d27b10
Revises: 8bf39b64a5e4
Create Date: 2026-10-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f3c9d27b10"
down_revision: Union[str, Sequence[str], None] = "8bf39b64a5e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("agent_history", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("agent_history")
