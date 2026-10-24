"""Google Drive tools: search files and read the content of supported types (read-only scope)."""

import io
import os
from typing import Literal
from urllib.parse import quote

from pydantic_ai import RunContext

from agent.deps import Deps
from tools.google_auth import google_error, google_request, guard

FILES_URL = "https://www.googleapis.com/drive/v3/files"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

# Google-native formats are exported to text; regular files are downloaded as-is.
EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-yaml")
TYPE_FILTERS = {
    "document": "mimeType = 'application/vnd.google-apps.document'",
    "spreadsheet": "mimeType = 'application/vnd.google-apps.spreadsheet'",
    "presentation": "mimeType = 'application/vnd.google-apps.presentation'",
    "pdf": "mimeType = 'application/pdf'",
    "folder": "mimeType = 'application/vnd.google-apps.folder'",
}
LABELS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.folder": "Dossier",
    "application/pdf": "PDF",
}


def _folder() -> str | None:
    # Optional: restrict the agent to one shared folder (only files directly inside it).
    return os.getenv("GOOGLE_DRIVE_FOLDER_ID") or None


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _file_view(f: dict) -> dict:
    return {
        "id": f["id"],
        "name": f.get("name"),
        "type": LABELS.get(f.get("mimeType", ""), f.get("mimeType")),
        "modified": (f.get("modifiedTime") or "")[:10],
        "link": f.get("webViewLink"),
    }


@guard
async def drive_search(
    ctx: RunContext[Deps],
    query: str,
    file_type: Literal["any", "document", "spreadsheet", "presentation", "pdf", "folder"] = "any",
    max_results: int = 10,
) -> dict:
    """Search the user's Google Drive by file name and content.

    Use short keywords (e.g. "proposition projet", "agents IA"), not a full sentence.
    Then call drive_read_file with the id of the most relevant result to read it.

    Args:
        query: Keywords to look for in file names and contents.
        file_type: Optional filter on the kind of file.
        max_results: Number of files to return (1-20).
    """
    # Each keyword must appear in the name or the content (order and exact phrase don't matter).
    words = [_escape(w) for w in query.split() if len(w) > 1][:6] or [_escape(query.strip())]
    clauses = ["trashed = false"] + [f"(name contains '{w}' or fullText contains '{w}')" for w in words]
    if file_type != "any":
        clauses.append(TYPE_FILTERS[file_type])
    if folder := _folder():
        clauses.append(f"'{_escape(folder)}' in parents")

    resp = await google_request(
        ctx.deps.http,
        "GET",
        FILES_URL,
        params={
            "q": " and ".join(clauses),
            "pageSize": max(1, min(max_results, 20)),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            "spaces": "drive",
        },
    )
    if resp.status_code != 200:
        return google_error(resp, "Google Drive")
    files = [_file_view(f) for f in resp.json().get("files", [])]
    if not files:
        return {"files": [], "note": "Aucun fichier trouvé. Essaie d'autres mots-clés."}
    return {"files": files}


@guard
async def drive_read_file(ctx: RunContext[Deps], file_id: str, max_chars: int = 12000) -> dict:
    """Read the text content of a Google Drive file found with drive_search.

    Supports Google Docs, Sheets (as CSV), Slides, PDF and plain-text files.
    Use the content to answer the user's question or summarize the document.

    Args:
        file_id: The file id from drive_search.
        max_chars: Maximum number of characters to return.
    """
    http = ctx.deps.http
    meta_resp = await google_request(
        http,
        "GET",
        f"{FILES_URL}/{quote(file_id, safe='')}",
        params={"fields": "id,name,mimeType,modifiedTime,webViewLink,parents,size"},
    )
    if meta_resp.status_code != 200:
        return google_error(meta_resp, "le fichier")
    meta = meta_resp.json()
    mime = meta.get("mimeType", "")
    info = _file_view(meta)

    if (folder := _folder()) and folder not in (meta.get("parents") or []):
        return {"error": "Ce fichier est en dehors du dossier autorisé pour l'agent."}
    if int(meta.get("size") or 0) > MAX_DOWNLOAD_BYTES:
        return {**info, "error": "Fichier trop volumineux pour être lu (plus de 10 Mo)."}

    url = f"{FILES_URL}/{quote(file_id, safe='')}"
    if mime in EXPORTS:
        resp = await google_request(http, "GET", f"{url}/export", params={"mimeType": EXPORTS[mime]})
    elif mime == "application/pdf" or mime.startswith(TEXT_TYPES):
        resp = await google_request(http, "GET", url, params={"alt": "media"})
    else:
        return {**info, "error": f"Ce type de fichier ({info['type']}) n'est pas lisible par l'agent."}
    if resp.status_code != 200:
        return google_error(resp, "le contenu du fichier")

    if mime == "application/pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(resp.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return {**info, "error": "Impossible d'extraire le texte de ce PDF."}
        if not text.strip():
            return {**info, "error": "Ce PDF ne contient pas de texte extractible (probablement un scan)."}
    else:
        text = resp.content.decode("utf-8", errors="replace")

    text = text.strip()
    limit = max(1000, min(max_chars, 30000))
    return {**info, "content": text[:limit], "truncated": len(text) > limit, "total_chars": len(text)}
