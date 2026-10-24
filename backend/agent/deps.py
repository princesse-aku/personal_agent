"""Dependencies injected into every tool call (PydanticAI RunContext.deps)."""

from dataclasses import dataclass, field
from uuid import uuid4

import httpx


@dataclass
class Deps:
    http: httpx.AsyncClient  # shared HTTP client for the whole agent run
    conversation_id: int
    timezone: str  # IANA name, e.g. "Africa/Lome"
    # One id per user turn. An action proposed in this turn can't be confirmed in the same
    # turn: the user must have seen the proposal and answered first.
    run_id: str = field(default_factory=lambda: uuid4().hex)
