"""The PydanticAI agent: model, instructions and tool registry."""

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from agent.deps import Deps
from tools.actions import cancel_action, confirm_action
from tools.calendar import (
    calendar_create_event,
    calendar_list_events,
    calendar_propose_delete,
    calendar_propose_update,
)
from tools.drive import drive_read_file, drive_search
from tools.email import email_propose
from tools.notion import notion_append, notion_create_page, notion_read_page, notion_search
from tools.weather import get_weather
from tools.web import web_search

RODIUMAI_BASE_URL = os.getenv("RODIUMAI_BASE_URL", "https://api.rodiumai.io/v1")
PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "agent.md").read_text(encoding="utf-8")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# One agent definition; the model is chosen per request (validated against ALLOWED_MODELS).
agent = Agent(
    deps_type=Deps,
    instructions=PROMPT,
    tools=[
        web_search,
        get_weather,
        calendar_list_events,
        calendar_create_event,
        calendar_propose_update,
        calendar_propose_delete,
        drive_search,
        drive_read_file,
        email_propose,
        notion_search,
        notion_read_page,
        notion_create_page,
        notion_append,
        confirm_action,
        cancel_action,
    ],
    retries=2,
)


@agent.instructions
def current_datetime(ctx: RunContext[Deps]) -> str:
    # Re-evaluated on every run, so "demain" is always relative to the real current date.
    now = datetime.now(ZoneInfo(ctx.deps.timezone))
    return (
        f"Date et heure actuelles : {JOURS[now.weekday()]} {now:%Y-%m-%d %H:%M}, "
        f"fuseau horaire {ctx.deps.timezone} (UTC{now:%z})."
    )


@lru_cache
def get_model(name: str) -> OpenAIChatModel:
    # RodiumAI exposes an OpenAI-compatible API, so the OpenAI provider works with a custom base_url.
    provider = OpenAIProvider(base_url=RODIUMAI_BASE_URL, api_key=os.environ["RODIUMAI_API_KEY"])
    return OpenAIChatModel(name, provider=provider)


def trim_history(messages: list[ModelMessage], max_turns: int = 10) -> list[ModelMessage]:
    """Keep the last `max_turns` user turns, cutting only at a user prompt so tool calls stay paired."""
    turn_starts = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    if len(turn_starts) <= max_turns:
        return messages
    return messages[turn_starts[-max_turns]:]
