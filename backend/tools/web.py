"""Web search and news via the Tavily Search API."""

import os
from typing import Literal

import httpx
from pydantic_ai import RunContext

from agent.deps import Deps

TAVILY_URL = "https://api.tavily.com/search"


async def web_search(
    ctx: RunContext[Deps],
    query: str,
    topic: Literal["general", "news"] = "general",
    time_range: Literal["day", "week", "month", "year"] | None = None,
    max_results: int = 5,
) -> dict:
    """Search the web for up-to-date information.

    Use topic="news" for recent news and current events, "general" otherwise.
    Use time_range to restrict to recent results (e.g. "week" for "this week").
    Always cite the returned URLs as sources in the final answer.

    Args:
        query: The search query, phrased as keywords (e.g. "Google latest news").
        topic: "news" for current events, "general" for everything else.
        time_range: Optional recency filter.
        max_results: Number of results to return (1-10).
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return {"error": "La recherche web n'est pas configurée (TAVILY_API_KEY manquante)."}

    payload: dict = {
        "query": query,
        "topic": topic,
        "max_results": max(1, min(max_results, 10)),
        "search_depth": "basic",
    }
    if time_range:
        payload["time_range"] = time_range

    try:
        resp = await ctx.deps.http.post(
            TAVILY_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except httpx.HTTPError:
        return {"error": "Le service de recherche est injoignable pour le moment."}

    if resp.status_code == 401:
        return {"error": "Clé Tavily invalide."}
    if resp.status_code == 429:
        return {"error": "Quota de recherche Tavily atteint, réessaie plus tard."}
    if resp.status_code >= 400:
        return {"error": f"La recherche a échoué (code {resp.status_code})."}

    results = resp.json().get("results", [])
    if not results:
        return {"results": [], "note": "Aucun résultat trouvé pour cette requête."}

    return {
        "results": [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": (r.get("content") or "")[:600],
                "published_date": r.get("published_date"),
            }
            for r in results
        ]
    }
