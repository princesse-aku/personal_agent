"""Google OAuth 2.0 access tokens from a refresh token stored in environment variables.

The one-time consent is done beforehand (OAuth Playground); in production the app only
holds GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN and exchanges the
refresh token for short-lived access tokens. No token file, no browser on the server.
"""

import functools
import os
import time

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"

_cache: dict = {"token": None, "expires_at": 0.0}


class GoogleAuthError(Exception):
    """Raised with a user-facing message when Google access is not available."""


async def get_access_token(http: httpx.AsyncClient) -> str:
    if _cache["token"] and time.time() < _cache["expires_at"] - 60:
        return _cache["token"]

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise GoogleAuthError("Les services Google ne sont pas configurés sur ce serveur.")

    try:
        resp = await http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except httpx.HTTPError as exc:
        raise GoogleAuthError("Google est injoignable pour le moment.") from exc

    if resp.status_code != 200:
        error = resp.json().get("error") if resp.headers.get("content-type", "").startswith("application/json") else None
        if error == "invalid_grant":
            raise GoogleAuthError("L'autorisation Google a expiré ou a été révoquée : il faut régénérer le refresh token.")
        raise GoogleAuthError(f"Échec de l'authentification Google ({resp.status_code}).")

    data = resp.json()
    _cache["token"] = data["access_token"]
    _cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _cache["token"]


async def google_request(http: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Authenticated request to a Google API; retries once with a fresh token on 401."""
    for attempt in range(2):
        token = await get_access_token(http)
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        resp = await http.request(method, url, headers=headers, **kwargs)
        if resp.status_code != 401 or attempt == 1:
            return resp
        _cache["token"] = None  # token revoked or expired early: refresh and retry
    return resp


def google_error(resp: httpx.Response, what: str) -> dict:
    """Turn a Google API error response into a short message for the model."""
    if resp.status_code == 403:
        return {"error": f"Accès refusé par Google pour {what} (permissions insuffisantes)."}
    if resp.status_code == 404:
        return {"error": f"{what.capitalize()} introuvable."}
    if resp.status_code == 429:
        return {"error": "Trop de requêtes vers Google, réessaie dans un instant."}
    return {"error": f"Échec de l'opération Google sur {what} (code {resp.status_code})."}


def guard(fn):
    """Turn auth/network/date failures into readable tool errors instead of crashing the run."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except GoogleAuthError as exc:
            return {"error": str(exc)}
        except ValueError:
            return {"error": "Date ou heure invalide. Utilise le format AAAA-MM-JJ ou AAAA-MM-JJTHH:MM."}
        except httpx.HTTPError:
            return {"error": "Le service Google est injoignable pour le moment."}

    return wrapper
