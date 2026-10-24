from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database.db import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Full PydanticAI message history (incl. tool calls/results) as JSON, so the agent
    # remembers what tools returned in earlier turns. The `messages` table keeps the UI view.
    agent_history: Mapped[str | None] = mapped_column(Text, default=None)


class Message(Base):
    __tablename__ = "messages"
    # Constraints are named explicitly so Alembic can recreate them in SQLite batch mode.
    # The unique (conversation_id, seq) pair also rejects two requests writing the same turn concurrently.
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_id_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", name="fk_messages_conversation_id"), index=True
    )
    seq: Mapped[int]  # position of the message in its conversation, starting at 1
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system-notification | note
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PendingAction(Base):
    """A side-effecting action (update/delete event, send email…) awaiting the user's explicit OK."""

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # e.g. "calendar.update", "calendar.delete"
    payload: Mapped[str] = mapped_column(Text)  # JSON arguments for the executor
    summary: Mapped[str] = mapped_column(Text)  # human-readable description shown to the user
    run_id: Mapped[str] = mapped_column(String(32))  # turn in which it was proposed
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done | cancelled | failed
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
