"""
Prometheus metrics endpoints.

Exposes Ray cluster metrics via three routes:

  GET /api/v1/metrics            — head node's own Prometheus metrics (fast)
  GET /api/v1/metrics/all        — aggregated metrics from ALL alive nodes (10 s cache)
  GET /api/v1/metrics/discovery  — Ray's Prometheus service-discovery JSON
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Metrics"])

METRICS_PORT = int(os.environ.get("RAY_METRICS_PORT", "9090"))
PROM_SD_FILE = Path("/tmp/ray/prom_metrics_service_discovery.json")

# Simple in-memory cache for the expensive /metrics/all fan-out.
_all_cache: Optional[str] = None
_all_cache_ts: float = 0.0
_ALL_CACHE_TTL = 10.0  # seconds


async def _fetch_metrics(host: str, port: int, timeout: float = 5.0) -> str:
    """Fetch Prometheus text from a single node.  Returns empty string on failure."""
    import httpx

    url = f"http://{host}:{port}/metrics"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.debug("Failed to scrape %s: %s", url, exc)
        return ""


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/metrics",
            summary="Head node Prometheus metrics",
            response_class=PlainTextResponse)
async def head_metrics():
    """
    Return the head node's own Prometheus metrics.

    Fast — no fan-out to other nodes.  Useful for quick ``curl`` checks or
    as the default Prometheus scrape target behind the ``/metrics`` nginx route.
    """
    text = await _fetch_metrics("127.0.0.1", METRICS_PORT)
    if not text:
        return PlainTextResponse(
            "# head node metrics unavailable\n",
            status_code=503,
        )
    return PlainTextResponse(text)


@router.get("/metrics/all",
            summary="All nodes Prometheus metrics (aggregated)",
            response_class=PlainTextResponse)
async def all_metrics():
    """
    Scrape every alive Ray node's Prometheus exporter in parallel and return
    the concatenated text.  Results are cached for 10 s.
    """
    global _all_cache, _all_cache_ts

    now = time.monotonic()
    if _all_cache is not None and (now - _all_cache_ts) < _ALL_CACHE_TTL:
        return PlainTextResponse(_all_cache)

    import ray

    try:
        nodes = ray.nodes()
    except Exception as exc:
        logger.error("ray.nodes() failed: %s", exc)
        return PlainTextResponse(
            f"# ray.nodes() error: {exc}\n",
            status_code=503,
        )

    alive_ips = [
        n["NodeManagerAddress"]
        for n in nodes
        if n.get("Alive") and n.get("NodeManagerAddress")
    ]

    if not alive_ips:
        return PlainTextResponse("# no alive nodes found\n", status_code=503)

    # Fan-out scrapes in parallel.
    tasks = [_fetch_metrics(ip, METRICS_PORT) for ip in alive_ips]
    results = await asyncio.gather(*tasks)

    parts: list[str] = []
    for ip, text in zip(alive_ips, results):
        parts.append(f"# node: {ip}\n")
        if text:
            parts.append(text)
            if not text.endswith("\n"):
                parts.append("\n")
        else:
            parts.append(f"# node {ip} UNREACHABLE\n")

    aggregated = "".join(parts)
    _all_cache = aggregated
    _all_cache_ts = now

    return PlainTextResponse(aggregated)


@router.get("/metrics/discovery",
            summary="Prometheus service-discovery targets",
            response_class=JSONResponse)
async def metrics_discovery():
    """
    Return Ray's auto-generated ``prom_metrics_service_discovery.json``.

    External Prometheus can use this via ``http_sd_configs`` to dynamically
    discover all scrape targets in the cluster.

    Format::

        [{"targets": ["10.0.0.1:9090"], "labels": {"ray_node_id": "..."}}]
    """
    if not PROM_SD_FILE.exists():
        return JSONResponse(
            {"error": f"Service discovery file not found: {PROM_SD_FILE}"},
            status_code=404,
        )
    try:
        data = json.loads(PROM_SD_FILE.read_text())
    except Exception as exc:
        return JSONResponse(
            {"error": f"Failed to read service discovery file: {exc}"},
            status_code=500,
        )
    return JSONResponse(content=data)
