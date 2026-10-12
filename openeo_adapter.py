"""Optional openEO batch-job integration.

The Earthdata streaming path remains the default. This adapter only connects
when an openEO endpoint is configured by the deployment environment.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any


def configuration() -> dict[str, Any]:
    return {
        "configured": bool(os.getenv("OPENEO_URL")),
        "url": os.getenv("OPENEO_URL"),
        "auth": (
            "bearer" if os.getenv("OPENEO_TOKEN")
            else "basic" if os.getenv("OPENEO_USERNAME") and os.getenv("OPENEO_PASSWORD")
            else "oidc"
        ),
    }


def build_change_process_graph(
    *,
    collection_id: str,
    before: datetime,
    after: datetime,
    bbox: tuple[float, float, float, float],
    bands: list[str] | None = None,
) -> dict[str, Any]:
    west, south, east, north = bbox
    extent = {"west": west, "south": south, "east": east, "north": north}
    before_date = before.isoformat()
    after_date = after.isoformat()
    before_node = {
        "process_id": "load_collection",
        "arguments": {
            "id": collection_id,
            "spatial_extent": extent,
            "temporal_extent": [before_date, before_date],
            "bands": bands,
        },
        "result": False,
    }
    after_node = {
        "process_id": "load_collection",
        "arguments": {
            "id": collection_id,
            "spatial_extent": extent,
            "temporal_extent": [after_date, after_date],
            "bands": bands,
        },
        "result": False,
    }
    return {
        "before": before_node,
        "after": after_node,
        "difference": {
            "process_id": "subtract",
            "arguments": {
                "x": {"from_node": "after"},
                "y": {"from_node": "before"},
            },
            "result": True,
        },
    }


def submit_change_job(
    *,
    collection_id: str,
    before: datetime,
    after: datetime,
    bbox: tuple[float, float, float, float],
    bands: list[str] | None = None,
) -> dict[str, Any]:
    url = os.getenv("OPENEO_URL")
    if not url:
        raise RuntimeError("openEO is not configured; set OPENEO_URL to enable it")
    try:
        import openeo
    except ImportError as exc:
        raise RuntimeError(
            "openEO support is configured but the openeo package is not installed"
        ) from exc

    connection = openeo.connect(url)
    token = os.getenv("OPENEO_TOKEN")
    username = os.getenv("OPENEO_USERNAME")
    password = os.getenv("OPENEO_PASSWORD")
    if token:
        connection.authenticate_bearer_token(token)
    elif username and password:
        connection.authenticate_basic(username, password)
    else:
        connection.authenticate_oidc()

    graph = build_change_process_graph(
        collection_id=collection_id,
        before=before,
        after=after,
        bbox=bbox,
        bands=bands,
    )
    job = connection.create_job(
        process_graph=graph,
        title=f"EarthChange NISAR change {before.date()} to {after.date()}",
    )
    job.start()
    return {
        "job_id": job.job_id,
        "status": "submitted",
        "backend": url,
        "process_graph": graph,
    }
