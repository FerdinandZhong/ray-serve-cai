"""
Weather MCP tools — example module for the generic MCP engine.

Provides two tools that query the US National Weather Service API:
  - get_alerts(state)   — active weather alerts for a US state code
  - get_forecast(lat, lon) — 5-period forecast for a lat/lon

Usage:
    Deploy via the management API with engine_config:
      {"mcp_module": "ray_serve_cai.engines.mcps.weather_tools"}

Reference:
    https://github.com/ray-project/ray/blob/master/python/ray/llm/examples/sglang/modules/sglang_engine.py
"""

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"

# ── FastMCP instance — the MCP engine discovers this automatically ───────────

mcp = FastMCP("weather", stateless_http=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _nws_request(url: str) -> dict[str, Any] | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None


def _format_alert(feature: dict) -> str:
    props = feature["properties"]
    return (
        f"Event: {props.get('event', 'Unknown')}\n"
        f"Area: {props.get('areaDesc', 'Unknown')}\n"
        f"Severity: {props.get('severity', 'Unknown')}\n"
        f"Description: {props.get('description', 'No description available')}\n"
        f"Instructions: {props.get('instruction', 'No specific instructions provided')}"
    )


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Fetch active weather alerts for a US state code (e.g. 'CA')."""
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await _nws_request(url)
    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."
    features = data["features"]
    if not features:
        return "No active alerts for this state."
    return "\n---\n".join(_format_alert(f) for f in features)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Fetch a 5-period weather forecast for given lat/lon."""
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await _nws_request(points_url)
    if not points_data or "properties" not in points_data:
        return "Unable to fetch forecast data for this location."

    forecast_url = points_data["properties"].get("forecast")
    if not forecast_url:
        return "No forecast URL found for this location."

    forecast_data = await _nws_request(forecast_url)
    if not forecast_data or "properties" not in forecast_data:
        return "Unable to fetch detailed forecast."

    periods = forecast_data["properties"].get("periods", [])
    if not periods:
        return "No forecast periods available."

    parts: list[str] = []
    for p in periods[:5]:
        parts.append(
            f"{p['name']}:\n"
            f"Temperature: {p['temperature']}\u00b0{p['temperatureUnit']}\n"
            f"Wind: {p['windSpeed']} {p['windDirection']}\n"
            f"Forecast: {p['detailedForecast']}"
        )
    return "\n---\n".join(parts)
