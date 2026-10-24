"""Google Calendar tools (primary calendar of the authorized test account)."""

from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic_ai import RunContext

from agent.deps import Deps
from tools.actions import executor, propose
from tools.google_auth import google_error, google_request, guard as _guard

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


# ---------- helpers ----------

def _tz(ctx: RunContext[Deps]) -> ZoneInfo:
    return ZoneInfo(ctx.deps.timezone)


def _parse_local(value: str, tz: ZoneInfo) -> datetime:
    """'2026-10-30T10:00' (local time) or a full ISO datetime with offset -> aware datetime."""
    dt = datetime.fromisoformat(value.strip().replace(" ", "T"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _when(field: dict, tz: ZoneInfo) -> str:
    if "dateTime" in field:
        return datetime.fromisoformat(field["dateTime"]).astimezone(tz).strftime("%Y-%m-%d %H:%M")
    return f'{field.get("date")} (journée entière)'


def _event_view(ev: dict, tz: ZoneInfo) -> dict:
    return {
        "id": ev["id"],
        "title": ev.get("summary", "(sans titre)"),
        "start": _when(ev.get("start", {}), tz),
        "end": _when(ev.get("end", {}), tz),
        "location": ev.get("location"),
        "description": (ev.get("description") or "")[:300] or None,
    }


async def _get_event(ctx: RunContext[Deps], event_id: str) -> dict:
    resp = await google_request(ctx.deps.http, "GET", f"{EVENTS_URL}/{quote(event_id, safe='')}")
    if resp.status_code != 200:
        return google_error(resp, "l'événement")
    return resp.json()


# ---------- tools ----------

@_guard
async def calendar_list_events(
    ctx: RunContext[Deps],
    start_date: str,
    end_date: str | None = None,
    query: str | None = None,
) -> dict:
    """List the user's Google Calendar events between two dates (inclusive).

    Use it for "mes rendez-vous de demain", "cette semaine", "à venir" (next 7 days),
    or to find an event's id before modifying or deleting it.

    Args:
        start_date: First day, YYYY-MM-DD, in the user's timezone.
        end_date: Last day, YYYY-MM-DD (inclusive). Defaults to start_date.
        query: Optional text to match in event titles/descriptions.
    """
    tz = _tz(ctx)
    first = date.fromisoformat(start_date)
    last = date.fromisoformat(end_date) if end_date else first
    params = {
        "timeMin": datetime.combine(first, time.min, tz).isoformat(),
        "timeMax": datetime.combine(last + timedelta(days=1), time.min, tz).isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 50,
        "timeZone": ctx.deps.timezone,
    }
    if query:
        params["q"] = query
    resp = await google_request(ctx.deps.http, "GET", EVENTS_URL, params=params)
    if resp.status_code != 200:
        return google_error(resp, "l'agenda")
    events = [_event_view(ev, tz) for ev in resp.json().get("items", [])]
    return {"events": events} if events else {"events": [], "note": "Aucun événement sur cette période."}


@_guard
async def calendar_create_event(
    ctx: RunContext[Deps],
    title: str,
    start: str,
    end: str | None = None,
    duration_minutes: int = 60,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Create an event in the user's Google Calendar.

    Only call it once the title, the day and the start time are known; ask the user if
    any of them is missing. If no end or duration is given, 60 minutes is used: say so.

    Args:
        title: Event title.
        start: Local start, YYYY-MM-DDTHH:MM (or YYYY-MM-DD for an all-day event).
        end: Optional local end, same format as start.
        duration_minutes: Used when end is not given.
        description: Optional notes.
        location: Optional place or meeting link.
    """
    tz = _tz(ctx)
    body: dict = {"summary": title}
    if "T" not in start and len(start.strip()) == 10:
        day = date.fromisoformat(start.strip())
        last = date.fromisoformat(end) if end else day
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (last + timedelta(days=1)).isoformat()}  # end date is exclusive
    else:
        begin = _parse_local(start, tz)
        finish = _parse_local(end, tz) if end else begin + timedelta(minutes=duration_minutes)
        if finish <= begin:
            return {"error": "L'heure de fin doit être après l'heure de début."}
        body["start"] = {"dateTime": begin.isoformat(), "timeZone": ctx.deps.timezone}
        body["end"] = {"dateTime": finish.isoformat(), "timeZone": ctx.deps.timezone}
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    resp = await google_request(ctx.deps.http, "POST", EVENTS_URL, json=body)
    if resp.status_code not in (200, 201):
        return google_error(resp, "l'agenda")
    ev = resp.json()
    return {"status": "created", "event": _event_view(ev, tz), "link": ev.get("htmlLink")}


@_guard
async def calendar_propose_update(
    ctx: RunContext[Deps],
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Propose a change to an existing event. Nothing is modified until the user confirms.

    Find the event_id first with calendar_list_events. If only a new start is given,
    the event keeps its duration. Then show the returned summary and wait for the user.

    Args:
        event_id: Id from calendar_list_events.
        title: New title.
        start: New local start, YYYY-MM-DDTHH:MM.
        end: New local end, YYYY-MM-DDTHH:MM.
        description: New description.
        location: New location.
    """
    tz = _tz(ctx)
    ev = await _get_event(ctx, event_id)
    if "error" in ev:
        return ev

    patch: dict = {}
    changes: list[str] = []
    if title and title != ev.get("summary"):
        patch["summary"] = title
        changes.append(f"titre → « {title} »")
    if start or end:
        if "dateTime" not in ev.get("start", {}):
            return {"error": "Modifier l'horaire d'un événement sur la journée entière n'est pas pris en charge."}
        old_start = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(tz)
        old_end = datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(tz)
        new_start = _parse_local(start, tz) if start else old_start
        new_end = _parse_local(end, tz) if end else new_start + (old_end - old_start)
        if new_end <= new_start:
            return {"error": "L'heure de fin doit être après l'heure de début."}
        patch["start"] = {"dateTime": new_start.isoformat(), "timeZone": ctx.deps.timezone}
        patch["end"] = {"dateTime": new_end.isoformat(), "timeZone": ctx.deps.timezone}
        changes.append(
            f"horaire {old_start:%Y-%m-%d %H:%M}–{old_end:%H:%M} → {new_start:%Y-%m-%d %H:%M}–{new_end:%H:%M}"
        )
    if description is not None:
        patch["description"] = description
        changes.append("description mise à jour")
    if location is not None:
        patch["location"] = location
        changes.append(f"lieu → {location}")
    if not patch:
        return {"error": "Aucun changement indiqué."}

    summary = f"Modifier « {ev.get('summary', '(sans titre)')} » : " + " ; ".join(changes)
    return propose(ctx, "calendar.update", {"event_id": event_id, "patch": patch}, summary)


@_guard
async def calendar_propose_delete(ctx: RunContext[Deps], event_id: str) -> dict:
    """Propose deleting an event. Nothing is deleted until the user confirms.

    Only use it when the user explicitly asks to delete. Find the event_id first with
    calendar_list_events; if several events match, ask which one.

    Args:
        event_id: Id from calendar_list_events.
    """
    tz = _tz(ctx)
    ev = await _get_event(ctx, event_id)
    if "error" in ev:
        return ev
    view = _event_view(ev, tz)
    summary = f"Supprimer « {view['title']} » ({view['start']} → {view['end']})"
    return propose(ctx, "calendar.delete", {"event_id": event_id}, summary)


# ---------- executors (run only through confirm_action) ----------

@executor("calendar.update")
@_guard
async def _do_update(ctx: RunContext[Deps], payload: dict) -> dict:
    url = f"{EVENTS_URL}/{quote(payload['event_id'], safe='')}"
    resp = await google_request(ctx.deps.http, "PATCH", url, json=payload["patch"])
    if resp.status_code != 200:
        return google_error(resp, "l'événement")
    return {"status": "updated", "event": _event_view(resp.json(), _tz(ctx))}


@executor("calendar.delete")
@_guard
async def _do_delete(ctx: RunContext[Deps], payload: dict) -> dict:
    url = f"{EVENTS_URL}/{quote(payload['event_id'], safe='')}"
    resp = await google_request(ctx.deps.http, "DELETE", url)
    if resp.status_code in (200, 204):
        return {"status": "deleted"}
    if resp.status_code == 410:
        return {"status": "deleted", "note": "L'événement était déjà supprimé."}
    return google_error(resp, "l'événement")
