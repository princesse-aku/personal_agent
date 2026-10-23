"""Current weather and forecasts via WeatherAPI.com."""

import os

import httpx
from pydantic_ai import RunContext

from agent.deps import Deps

WEATHERAPI_URL = "https://api.weatherapi.com/v1/forecast.json"


async def get_weather(ctx: RunContext[Deps], city: str, days: int = 1) -> dict:
    """Get the current weather and a daily forecast for a city.

    Call once per city; to compare several cities, call it for each one.
    days=1 gives today only; use days=2 for "tomorrow", up to 3 days ahead.

    Args:
        city: City name, optionally with country (e.g. "Cotonou, Benin").
        days: Number of forecast days including today (1-3).
    """
    api_key = os.getenv("WEATHERAPI_KEY")
    if not api_key:
        return {"error": "La météo n'est pas configurée (WEATHERAPI_KEY manquante)."}

    try:
        resp = await ctx.deps.http.get(
            WEATHERAPI_URL,
            params={
                "key": api_key,
                "q": city,
                "days": max(1, min(days, 3)),
                "lang": "fr",
                "aqi": "no",
                "alerts": "no",
            },
        )
    except httpx.HTTPError:
        return {"error": "Le service météo est injoignable pour le moment."}

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code >= 400:
        code = (data.get("error") or {}).get("code")
        if code == 1006:
            return {"error": f"Ville introuvable : « {city} ». Demande une précision à l'utilisateur."}
        return {"error": f"La requête météo a échoué (code {resp.status_code})."}

    loc, cur = data["location"], data["current"]
    return {
        "location": f'{loc["name"]}, {loc["country"]}',
        "local_time": loc["localtime"],
        "current": {
            "temp_c": cur["temp_c"],
            "feels_like_c": cur["feelslike_c"],
            "condition": cur["condition"]["text"],
            "humidity": cur["humidity"],
            "wind_kph": cur["wind_kph"],
        },
        "forecast": [
            {
                "date": d["date"],
                "min_c": d["day"]["mintemp_c"],
                "max_c": d["day"]["maxtemp_c"],
                "condition": d["day"]["condition"]["text"],
                "chance_of_rain": d["day"]["daily_chance_of_rain"],
            }
            for d in data["forecast"]["forecastday"]
        ],
    }
