"""Dependencies injected into every tool call (PydanticAI RunContext.deps)."""

from dataclasses import dataclass

import httpx


@dataclass
class Deps:
    http: httpx.AsyncClient  # shared HTTP client for the whole agent run
    conversation_id: int
    timezone: str  # IANA name, e.g. "Africa/Lome"
