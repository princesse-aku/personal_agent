import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # before importing the agent, which reads env vars at import time

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pydantic_ai import AgentRunResultEvent, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from agent.core import agent, get_model, trim_history
from agent.deps import Deps
from database.db import SessionLocal, get_db
from database.models import Conversation, Message

log = logging.getLogger("agent")

if not os.getenv("RODIUMAI_API_KEY"):
    raise RuntimeError("RODIUMAI_API_KEY is required")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Lome")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
PREVIEW_LENGTH = 60

# Roles the LLM understands. Custom roles stored in the DB stay out of its prompt.
LLM_ROLES = {"user", "assistant"}
NOTIFICATION_ROLE = "system-notification"
NOTE_ROLE = "note"
NOTIFICATION_EVERY = 10
NOTIFICATION_TEXT = "Notification système : Une dizaine de messages écrits."

ALLOWED_MODELS = [
    m.strip()
    for m in os.getenv(
        "ALLOWED_MODELS",
        "openai/gpt-4o-mini,anthropic/claude-sonnet-4-5-20250929",
    ).split(",")
    if m.strip()
]
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", ALLOWED_MODELS[0])
if DEFAULT_MODEL not in ALLOWED_MODELS:
    ALLOWED_MODELS.insert(0, DEFAULT_MODEL)

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]



PUBLIC_PATHS = {"/api/health", "/health"}


def require_password(request: Request, x_app_password: str = Header(default="")) -> None:
    # Shared access password so that a public URL can't be used to act on the test accounts.
    # Disabled when APP_PASSWORD is empty (local development).
    if request.url.path in PUBLIC_PATHS:
        return
    if APP_PASSWORD and not secrets.compare_digest(x_app_password, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="Mot de passe d'accès invalide.")


# All JSON routes live on `api`, mounted under /api; the root app also serves the built frontend.
api = FastAPI(title="Agent IA personnel", dependencies=[Depends(require_password)])
api.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConversationResponse(BaseModel):
    conversation_id: int


class ConversationSummary(BaseModel):
    id: int
    created_at: datetime
    preview: str | None


class ChatRequest(BaseModel):
    conversation_id: int
    message: str
    model: str | None = None


class NoteRequest(BaseModel):
    content: str = Field(min_length=1)


class NoteResponse(BaseModel):
    seq: int
    role: str
    content: str
    created_at: datetime


class ModelsResponse(BaseModel):
    models: list[str]
    default: str


class MessageResponse(BaseModel):
    seq: int
    role: str
    content: str
    created_at: datetime


def resolve_model(requested: str | None) -> str:
    model = requested or DEFAULT_MODEL
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not allowed. Choose one of: {', '.join(ALLOWED_MODELS)}",
        )
    return model


def load_messages(db: Session, conversation_id: int) -> list[Message]:
    if db.get(Conversation, conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)
        ).all()
    )


def dialogue_count(rows: list[Message]) -> int:
    return sum(1 for m in rows if m.role in LLM_ROLES)


def sse_data(payload: dict | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@api.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/auth/check")
def auth_check() -> dict:
    return {"ok": True}


@api.get("/models")
def list_models() -> ModelsResponse:
    return ModelsResponse(models=ALLOWED_MODELS, default=DEFAULT_MODEL)


@api.post("/conversations", status_code=201)
def create_conversation(db: Session = Depends(get_db)) -> ConversationResponse:
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    return ConversationResponse(conversation_id=conversation.id)


@api.get("/conversations")
def list_conversations(db: Session = Depends(get_db)) -> list[ConversationSummary]:
    rows = db.execute(
        select(Conversation, Message.content)
        .outerjoin(
            Message,
            and_(Message.conversation_id == Conversation.id, Message.seq == 1),
        )
        .order_by(Conversation.id.desc())
    ).all()
    return [
        ConversationSummary(
            id=conversation.id,
            created_at=conversation.created_at,
            preview=content[:PREVIEW_LENGTH] if content else None,
        )
        for conversation, content in rows
    ]


@api.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int, db: Session = Depends(get_db)) -> list[MessageResponse]:
    return [
        MessageResponse(seq=m.seq, role=m.role, content=m.content, created_at=m.created_at)
        for m in load_messages(db, conversation_id)
    ]


@api.post("/conversations/{conversation_id}/notes", status_code=201)
def add_note(
    conversation_id: int, req: NoteRequest, db: Session = Depends(get_db)
) -> NoteResponse:
    rows = load_messages(db, conversation_id)
    next_seq = rows[-1].seq + 1 if rows else 1
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    note = Message(
        conversation_id=conversation_id,
        seq=next_seq,
        role=NOTE_ROLE,
        content=content,
    )
    db.add(note)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The conversation was updated concurrently, please retry.",
        )
    db.refresh(note)
    return NoteResponse(
        seq=note.seq, role=note.role, content=note.content, created_at=note.created_at
    )


@api.post("/chat")
async def chat(req: ChatRequest, request: Request):
    model_name = resolve_model(req.model)
    message_text = req.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    with SessionLocal() as db:
        rows = load_messages(db, req.conversation_id)
        conversation = db.get(Conversation, req.conversation_id)
        # The agent sees its own history (with tool calls), never the notes stored in `messages`.
        history = (
            ModelMessagesTypeAdapter.validate_json(conversation.agent_history)
            if conversation.agent_history
            else []
        )
        next_seq = rows[-1].seq + 1 if rows else 1
        prior_dialogue = dialogue_count(rows)

    async def event_stream():
        reply_parts: list[str] = []
        result = None
        client_disconnected = False

        def text_chunk(text: str) -> str:
            reply_parts.append(text)
            # Same delta shape as before, so the existing frontend parser keeps working.
            return sse_data({"choices": [{"delta": {"content": text}}]})

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as http:
                deps = Deps(http=http, conversation_id=req.conversation_id, timezone=APP_TIMEZONE)
                async with agent.run_stream_events(
                    message_text,
                    message_history=history,
                    model=get_model(model_name),
                    deps=deps,
                    model_settings={"max_tokens": MAX_TOKENS},
                    usage_limits=UsageLimits(request_limit=12),
                ) as events:
                    async for event in events:
                        if await request.is_disconnected():
                            client_disconnected = True
                            break
                        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                            # A new text block after a tool call: separate it from the previous one.
                            if reply_parts and event.part.content:
                                yield text_chunk("\n\n")
                            if event.part.content:
                                yield text_chunk(event.part.content)
                        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                            if event.delta.content_delta:
                                yield text_chunk(event.delta.content_delta)
                        elif isinstance(event, FunctionToolCallEvent):
                            yield sse_event(
                                "tool",
                                {"id": event.part.tool_call_id, "name": event.part.tool_name, "status": "running"},
                            )
                        elif isinstance(event, FunctionToolResultEvent):
                            yield sse_event(
                                "tool",
                                {"id": event.part.tool_call_id, "name": event.part.tool_name, "status": "done"},
                            )
                        elif isinstance(event, AgentRunResultEvent):
                            result = event.result
        except UsageLimitExceeded:
            yield sse_event("error", {"detail": "La tâche demandait trop d'étapes, essaie de la découper."})
            return
        except ModelHTTPError as exc:
            log.warning("LLM HTTP error %s", exc.status_code)
            yield sse_event("error", {"detail": f"The LLM API call failed ({exc.status_code})."})
            return
        except (UnexpectedModelBehavior, httpx.HTTPError):
            log.exception("Agent run failed")
            yield sse_event("error", {"detail": "The LLM API call failed."})
            return
        except Exception:
            log.exception("Unexpected agent error")
            yield sse_event("error", {"detail": "Erreur interne de l'agent."})
            return

        if client_disconnected or result is None:
            # Stop = cancel the turn entirely: nothing is written to the DB.
            return

        reply = "".join(reply_parts).strip() or str(result.output).strip()
        if not reply:
            yield sse_event("error", {"detail": "Empty response from the LLM."})
            return

        notification = None
        if (prior_dialogue + 2) % NOTIFICATION_EVERY == 0:
            notification = NOTIFICATION_TEXT

        try:
            with SessionLocal() as db:
                conversation = db.get(Conversation, req.conversation_id)
                if conversation is None:
                    yield sse_event("error", {"detail": "Conversation not found."})
                    return
                conversation.agent_history = ModelMessagesTypeAdapter.dump_json(
                    trim_history(result.all_messages())
                ).decode()
                db.add_all(
                    [
                        Message(conversation_id=req.conversation_id, seq=next_seq, role="user", content=message_text),
                        Message(conversation_id=req.conversation_id, seq=next_seq + 1, role="assistant", content=reply),
                    ]
                )
                if notification:
                    db.add(
                        Message(
                            conversation_id=req.conversation_id,
                            seq=next_seq + 2,
                            role=NOTIFICATION_ROLE,
                            content=notification,
                        )
                    )
                db.commit()
        except IntegrityError:
            yield sse_event("error", {"detail": "The conversation was updated concurrently, please retry."})
            return
        except Exception:
            log.exception("Failed to save turn")
            yield sse_event("error", {"detail": "Failed to save the conversation turn."})
            return

        usage = result.usage() if callable(result.usage) else result.usage
        meta: dict = {
            "usage": {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.input_tokens + usage.output_tokens,
            }
        }
        if notification:
            meta["notification"] = notification
        yield sse_event("meta", meta)
        yield sse_data("[DONE]")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Root app: /api + the compiled React frontend (one URL, same origin, no CORS needed) ---
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "frontend" / "dist"))

app = FastAPI(title="Agent IA personnel", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api)
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
