"""Two-step confirmation for side-effecting actions.

A tool that changes something (update/delete an event, send an email…) never acts directly:
it calls `propose()`, which stores the action and returns a summary for the user. The action
only runs through `confirm_action`, and only if it was proposed in an EARLIER turn, so the
user has necessarily seen the proposal and answered before anything happens.
"""

import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

from pydantic_ai import RunContext

from agent.deps import Deps
from database.db import SessionLocal
from database.models import PendingAction

Executor = Callable[[RunContext[Deps], dict], Awaitable[dict]]
EXECUTORS: dict[str, Executor] = {}


def executor(kind: str):
    """Register the function that really performs an action of this kind once confirmed."""

    def decorator(fn: Executor) -> Executor:
        EXECUTORS[kind] = fn
        return fn

    return decorator


def propose(ctx: RunContext[Deps], kind: str, payload: dict, summary: str) -> dict:
    action_id = uuid4().hex[:12]
    with SessionLocal() as db:
        # A new proposal of the same kind replaces older unanswered ones in this conversation.
        db.query(PendingAction).filter_by(
            conversation_id=ctx.deps.conversation_id, kind=kind, status="pending"
        ).update({"status": "cancelled"})
        db.add(
            PendingAction(
                id=action_id,
                conversation_id=ctx.deps.conversation_id,
                kind=kind,
                payload=json.dumps(payload, ensure_ascii=False),
                summary=summary,
                run_id=ctx.deps.run_id,
            )
        )
        db.commit()
    return {
        "status": "awaiting_confirmation",
        "action_id": action_id,
        "summary": summary,
        "instruction": (
            "Présente ce résumé à l'utilisateur et demande-lui de confirmer. "
            "N'appelle confirm_action qu'après sa réponse explicite (« oui », « confirme »…)."
        ),
    }


async def confirm_action(ctx: RunContext[Deps], action_id: str) -> dict:
    """Execute an action previously proposed (event update/deletion, email…).

    Only call this when the user's LATEST message explicitly approves the proposal
    (e.g. "oui", "vas-y", "je confirme"). If the user refuses or changes something,
    call cancel_action or make a new proposal instead.

    Args:
        action_id: The action_id returned by the proposal.
    """
    with SessionLocal() as db:
        action = db.get(PendingAction, action_id)
        if action is None or action.conversation_id != ctx.deps.conversation_id:
            return {"error": "Action introuvable. Refais une proposition."}
        if action.status != "pending":
            return {"error": f"Cette action n'est plus en attente (statut : {action.status})."}
        if action.run_id == ctx.deps.run_id:
            # Guard in code, not just in the prompt: no self-confirmation within the same turn.
            return {
                "error": "Impossible de confirmer dans le même tour que la proposition. "
                "Montre le résumé à l'utilisateur et attends sa réponse."
            }
        kind, payload = action.kind, json.loads(action.payload)

    run = EXECUTORS.get(kind)
    if run is None:
        return {"error": f"Type d'action inconnu : {kind}."}
    result = await run(ctx, payload)

    with SessionLocal() as db:
        action = db.get(PendingAction, action_id)
        action.status = "failed" if "error" in result else "done"
        db.commit()
    return result


async def cancel_action(ctx: RunContext[Deps], action_id: str) -> dict:
    """Cancel a proposed action the user declined.

    Args:
        action_id: The action_id returned by the proposal.
    """
    with SessionLocal() as db:
        action = db.get(PendingAction, action_id)
        if action is None or action.conversation_id != ctx.deps.conversation_id:
            return {"error": "Action introuvable."}
        if action.status == "pending":
            action.status = "cancelled"
            db.commit()
    return {"status": "cancelled"}
