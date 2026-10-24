"""Notion tools: search pages shared with the integration, read, create and append content."""

import os

import httpx
from pydantic_ai import RunContext

from agent.deps import Deps

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _headers() -> dict | None:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"}


def _error(resp: httpx.Response) -> dict:
    if resp.status_code == 401:
        return {"error": "Token Notion invalide."}
    if resp.status_code == 404:
        return {"error": "Page introuvable ou non partagée avec l'intégration Notion."}
    if resp.status_code == 429:
        return {"error": "Trop de requêtes vers Notion, réessaie dans un instant."}
    return {"error": f"Échec de l'opération Notion (code {resp.status_code})."}


async def _call(ctx: RunContext[Deps], method: str, path: str, **kwargs) -> httpx.Response | dict:
    headers = _headers()
    if headers is None:
        return {"error": "Notion n'est pas configuré sur ce serveur (NOTION_TOKEN manquant)."}
    try:
        return await ctx.deps.http.request(method, f"{API}{path}", headers=headers, **kwargs)
    except httpx.HTTPError:
        return {"error": "Notion est injoignable pour le moment."}


def _plain(rich: list) -> str:
    return "".join(r.get("plain_text", "") for r in rich or [])


def _title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title")) or "(sans titre)"
    return "(sans titre)"


def _rich(text: str) -> list:
    # Notion limits each text object to 2000 characters.
    return [{"type": "text", "text": {"content": text[i : i + 2000]}} for i in range(0, len(text), 2000)] or [
        {"type": "text", "text": {"content": ""}}
    ]


def _to_blocks(markdown: str) -> list:
    """Minimal Markdown -> Notion blocks: headings, bullets, to-dos, numbered items, paragraphs."""
    blocks = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            kind, text = "heading_3", s[4:]
        elif s.startswith("## "):
            kind, text = "heading_2", s[3:]
        elif s.startswith("# "):
            kind, text = "heading_1", s[2:]
        elif s.startswith(("- [ ] ", "- [x] ")):
            blocks.append({"type": "to_do", "to_do": {"rich_text": _rich(s[6:]), "checked": s[3] == "x"}})
            continue
        elif s.startswith(("- ", "* ")):
            kind, text = "bulleted_list_item", s[2:]
        elif len(s) > 2 and s[0].isdigit() and s[1:3] in (". ", ") "):
            kind, text = "numbered_list_item", s[3:]
        else:
            kind, text = "paragraph", s
        blocks.append({"type": kind, kind: {"rich_text": _rich(text)}})
    return blocks[:100]  # API limit per request


def _block_text(block: dict) -> str:
    kind = block.get("type", "")
    data = block.get(kind, {})
    text = _plain(data.get("rich_text"))
    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ",
              "numbered_list_item": "1. "}.get(kind, "")
    if kind == "to_do":
        prefix = "- [x] " if data.get("checked") else "- [ ] "
    if kind == "child_page":
        return f"[sous-page] {data.get('title', '')}"
    return f"{prefix}{text}" if text else ""


async def notion_search(ctx: RunContext[Deps], query: str = "") -> dict:
    """Search the Notion pages shared with the integration (by title).

    Use short keywords ("Machine Learning", "réunion"); an empty query lists recent pages.

    Args:
        query: Keywords to look for in page titles.
    """
    resp = await _call(ctx, "POST", "/search", json={
        "query": query, "filter": {"property": "object", "value": "page"}, "page_size": 10,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    })
    if isinstance(resp, dict):
        return resp
    if resp.status_code != 200:
        return _error(resp)
    pages = [
        {"id": p["id"], "title": _title(p), "last_edited": p.get("last_edited_time", "")[:10], "url": p.get("url")}
        for p in resp.json().get("results", [])
    ]
    return {"pages": pages} if pages else {"pages": [], "note": "Aucune page trouvée (ou non partagée avec l'intégration)."}


async def notion_read_page(ctx: RunContext[Deps], page_id: str) -> dict:
    """Read the text content of a Notion page found with notion_search.

    Args:
        page_id: The page id from notion_search.
    """
    lines: list[str] = []
    cursor = None
    for _ in range(5):  # up to 500 blocks
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        resp = await _call(ctx, "GET", f"/blocks/{page_id}/children", params=params)
        if isinstance(resp, dict):
            return resp
        if resp.status_code != 200:
            return _error(resp)
        data = resp.json()
        lines += [t for b in data.get("results", []) if (t := _block_text(b))]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    text = "\n".join(lines)
    return {"page_id": page_id, "content": text[:15000] or "(page vide)", "truncated": len(text) > 15000}


async def notion_create_page(ctx: RunContext[Deps], title: str, content: str, parent_page_id: str | None = None) -> dict:
    """Create a new Notion page with the given content.

    Content uses simple Markdown: "# Titre", "## Sous-titre", "- puce", "- [ ] tâche", paragraphs.
    The page is created under NOTION_PARENT_PAGE_ID unless parent_page_id is given.

    Args:
        title: Page title.
        content: Page body in simple Markdown.
        parent_page_id: Optional id of the parent page (from notion_search).
    """
    parent = parent_page_id or os.getenv("NOTION_PARENT_PAGE_ID")
    if not parent:
        found = await notion_search(ctx, "")
        if "error" in found or not found.get("pages"):
            return {"error": "Aucune page parente disponible : partage une page avec l'intégration Notion."}
        parent = found["pages"][0]["id"]
    resp = await _call(ctx, "POST", "/pages", json={
        "parent": {"page_id": parent},
        "properties": {"title": {"title": _rich(title)}},
        "children": _to_blocks(content),
    })
    if isinstance(resp, dict):
        return resp
    if resp.status_code != 200:
        return _error(resp)
    page = resp.json()
    return {"status": "created", "page_id": page["id"], "title": title, "url": page.get("url")}


async def notion_append(ctx: RunContext[Deps], page_id: str, content: str) -> dict:
    """Append content (simple Markdown: paragraphs, "- puce", "- [ ] tâche") to an existing Notion page.

    Args:
        page_id: The page id from notion_search.
        content: Text to add at the end of the page.
    """
    blocks = _to_blocks(content)
    if not blocks:
        return {"error": "Rien à ajouter."}
    resp = await _call(ctx, "PATCH", f"/blocks/{page_id}/children", json={"children": blocks})
    if isinstance(resp, dict):
        return resp
    if resp.status_code != 200:
        return _error(resp)
    return {"status": "appended", "blocks_added": len(blocks)}
