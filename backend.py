from __future__ import annotations

import os
import re
import secrets
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
import earthaccess

from nisar_pipeline import DatasetMetadata, inspect_hdf5, discover_gcov
from nisar_streaming import MAX_GRANULES, stream_change_pair, stream_granule

LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DATA_ROOT = (ROOT / "data" / "earthdata").resolve()
DOWNLOAD_ROOT = (DATA_ROOT / "downloads").resolve()
MAX_DOWNLOAD_MB = float(os.getenv("EARTHDATA_MAX_DOWNLOAD_MB", "512"))
COLLECTIONS = (
    "NISAR_L2_GCOV_PROVISIONAL_V1",
    "NISAR_L2_GCOV_BETA_V1",
)
CATALOG_RESULT_CAP = 1000
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,220}$")


class BBox(BaseModel):
    west: float
    south: float
    east: float
    north: float

    @field_validator("west", "east")
    @classmethod
    def longitude_range(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return value

    @field_validator("south", "north")
    @classmethod
    def latitude_range(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> "BBox":
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("bbox must be ordered west < east and south < north")
        return self


class ObservationSearch(BaseModel):
    collection: str = "NISAR_L2_GCOV_PROVISIONAL_V1"
    start: datetime | None = None
    end: datetime | None = None
    bbox: BBox | None = None
    count: int = Field(default=20, ge=1, le=100)

    @field_validator("collection")
    @classmethod
    def supported_collection(cls, value: str) -> str:
        if value not in COLLECTIONS:
            raise ValueError(f"collection must be one of: {', '.join(COLLECTIONS)}")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> "ObservationSearch":
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must not be after end")
        if self.bbox:
            self.bbox.validate_order()
        return self


class Observation(BaseModel):
    id: str
    product_id: str
    collection: str
    provider: str | None = None
    acquisition_start: str | None = None
    acquisition_end: str | None = None
    size_mb: float | None = None
    data_urls: list[str]
    source: str = "NASA CMR via earthaccess"
    download_status: str = "not_downloaded"


def _search_payload(request: ObservationSearch, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """Run a bounded catalog query and expose whether the inventory is partial."""
    requested = min(page * page_size, CATALOG_RESULT_CAP)
    try:
        results = discover_gcov(
            start=request.start.isoformat() if request.start else None,
            end=request.end.isoformat() if request.end else None,
            short_name=request.collection,
            count=requested,
            bounding_box=(
                (request.bbox.west, request.bbox.south, request.bbox.east, request.bbox.north)
                if request.bbox
                else None
            ),
        )
        all_observations = [_normalize_granule(result, request.collection) for result in results]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Earthdata search failed: {exc}") from exc

    start = (page - 1) * page_size
    observations = all_observations[start : start + page_size]
    for index, observation in enumerate(observations):
        app.state.observations[observation.id] = observation
        source_index = start + index
        if source_index < len(results):
            app.state.granules[observation.id] = results[source_index]
    dates = sorted(
        observation.acquisition_start
        for observation in all_observations
        if observation.acquisition_start
    )
    complete = len(all_observations) < requested
    return {
        "status": "complete" if complete else "partial",
        "count": len(observations),
        "page": page,
        "page_size": page_size,
        "has_more": start + page_size < len(all_observations),
        "inventory_complete": complete,
        "observations": [observation.model_dump() for observation in observations],
        "timeline": {
            "earliest_acquisition": dates[0] if dates else None,
            "latest_acquisition": dates[-1] if dates else None,
            "available_dates": dates,
        },
        "source": "NASA CMR via earthaccess",
    }


class ProductInspection(BaseModel):
    product_id: str
    path: str
    datasets: list[DatasetMetadata]
    candidate_count: int


app = FastAPI(title="EarthChange AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("EARTHCHANGE_ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.state.observations: dict[str, Any] = {}
app.state.products: dict[str, Path] = {}
app.state.granules: dict[str, Any] = {}


def _normalize_granule(granule: Any, collection: str) -> Observation:
    record = dict(granule)
    metadata = record.get("meta", {})
    umm = record.get("umm", {})
    product_id = str(umm.get("GranuleUR") or metadata.get("native-id") or "")
    if not product_id:
        raise ValueError("Earthdata result did not contain a product identifier")
    temporal = umm.get("TemporalExtent", {}).get("RangeDateTime", {})
    links = [
        item["URL"]
        for item in umm.get("RelatedUrls", [])
        if item.get("Type") == "GET DATA" and item.get("URL")
    ]
    if not links:
        links = [str(link) for link in granule.data_links()]
    return Observation(
        id=secrets.token_urlsafe(12),
        product_id=product_id,
        collection=collection,
        provider=metadata.get("provider-id"),
        acquisition_start=temporal.get("BeginningDateTime"),
        acquisition_end=temporal.get("EndingDateTime"),
        # earthaccess reports granule size in megabytes. Preserve that unit so
        # the download guard does not mistake multi-gigabyte products for bytes.
        size_mb=float(record["size"]) if record.get("size") else None,
        data_urls=links,
    )


def _safe_product_path(product_id: str) -> Path:
    if not _SAFE_ID.fullmatch(product_id):
        raise HTTPException(status_code=400, detail="invalid product id")
    candidate = (DOWNLOAD_ROOT / product_id).resolve()
    if candidate.parent != DOWNLOAD_ROOT:
        raise HTTPException(status_code=400, detail="invalid product path")
    return candidate


def _validated_optional_bbox(
    west: float | None,
    south: float | None,
    east: float | None,
    north: float | None,
) -> tuple[float, float, float, float] | None:
    values = (west, south, east, north)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise HTTPException(status_code=422, detail="west, south, east, and north are all required")
    try:
        bbox = BBox(west=west, south=south, east=east, north=north)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return bbox.west, bbox.south, bbox.east, bbox.north


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "earthchange-api"}


@app.get("/api/earthdata/status")
def earthdata_status() -> dict[str, Any]:
    authenticated = bool(os.getenv("EARTHDATA_USERNAME") or os.getenv("EARTHDATA_LOGIN"))
    return {
        "search": "available",
        "download": "requires Earthdata Login and ASF access agreement",
        "authenticated": authenticated,
        "collections": list(COLLECTIONS),
    }


@app.get("/api/earthdata/collections")
def earthdata_collections() -> dict[str, Any]:
    return {"collections": [{"short_name": name, "provider": "ASF"} for name in COLLECTIONS]}


@app.post("/api/earthdata/observations/search")
def search_observations(request: ObservationSearch) -> dict[str, Any]:
    request.validate_dates()
    return _search_payload(request, page=1, page_size=request.count)


@app.post("/api/earthdata/archive/search")
def archive_search(
    request: ObservationSearch,
    page: int = Query(default=1, ge=1, le=50),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    request.validate_dates()
    return _search_payload(request, page=page, page_size=page_size)


@app.get("/api/earthdata/timeline")
def earthdata_timeline(
    collection: str = Query(default="NISAR_L2_GCOV_PROVISIONAL_V1"),
    start: datetime | None = None,
    end: datetime | None = None,
    west: float | None = Query(default=None, ge=-180, le=180),
    south: float | None = Query(default=None, ge=-90, le=90),
    east: float | None = Query(default=None, ge=-180, le=180),
    north: float | None = Query(default=None, ge=-90, le=90),
) -> dict[str, Any]:
    bbox = None
    bbox_values = (west, south, east, north)
    if any(value is not None for value in bbox_values):
        if any(value is None for value in bbox_values):
            raise HTTPException(status_code=422, detail="west, south, east, and north are all required")
        bbox = BBox(west=west, south=south, east=east, north=north)
    try:
        request = ObservationSearch(collection=collection, start=start, end=end, bbox=bbox, count=100)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = _search_payload(request, page=1, page_size=CATALOG_RESULT_CAP)
    all_observations = [
        Observation.model_validate(item) for item in payload["observations"]
    ]
    dates = sorted(
        observation.acquisition_start
        for observation in all_observations
        if observation.acquisition_start
    )
    return {
        "status": "complete" if len(all_observations) < CATALOG_RESULT_CAP else "partial",
        "inventory_complete": len(all_observations) < CATALOG_RESULT_CAP,
        "collection": collection,
        "earliest_acquisition": dates[0] if dates else None,
        "latest_acquisition": dates[-1] if dates else None,
        "available_dates": dates,
        "observation_count": len(all_observations),
        "source": "NASA CMR via earthaccess",
    }


@app.post("/api/earthdata/observations/{observation_id}/download")
def download_observation(observation_id: str) -> dict[str, Any]:
    """Deprecated hard-stop: complete NISAR granules are never downloaded."""
    raise HTTPException(
        status_code=410,
        detail="Full granule downloads are disabled. Use the streaming preview endpoint.",
    )


@app.post("/api/earthdata/stream")
def stream_observations(
    observation_ids: list[str],
    frequency: str = Query(default="frequencyA", pattern=r"^frequency[A-Z]$"),
    polarization: str = Query(default="HH", pattern=r"^(HH|HV|VH|VV)$"),
    threshold_db: float = Query(default=3.0, ge=0),
    west: float | None = Query(default=None, ge=-180, le=180),
    south: float | None = Query(default=None, ge=-90, le=90),
    east: float | None = Query(default=None, ge=-180, le=180),
    north: float | None = Query(default=None, ge=-90, le=90),
) -> dict[str, Any]:
    bbox = _validated_optional_bbox(west, south, east, north)
    if not 1 <= len(observation_ids) <= MAX_GRANULES:
        raise HTTPException(status_code=422, detail=f"select between 1 and {MAX_GRANULES} granules")
    missing = [item for item in observation_ids if item not in app.state.observations]
    if missing:
        raise HTTPException(status_code=404, detail="all granules must come from a prior catalog search")
    results = []
    for observation_id in observation_ids:
        observation = app.state.observations[observation_id]
        granule = app.state.granules[observation_id]
        print(
            f"streaming granule {observation.product_id}: "
            f"catalog_size_mb={observation.size_mb if observation.size_mb is not None else 'unknown'}",
            flush=True,
        )
        try:
            results.append(
                stream_granule(
                    granule,
                    observation.product_id,
                    frequency=frequency,
                    polarization=polarization,
                    threshold_db=threshold_db,
                    bbox=bbox,
                )
            )
        except (
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            earthaccess.exceptions.DownloadFailure,
            earthaccess.exceptions.LoginAttemptFailure,
            earthaccess.exceptions.ServiceOutage,
        ) as exc:
            raise HTTPException(status_code=422, detail=f"streaming failed for {observation.product_id}: {exc}") from exc
        except Exception as exc:
            LOGGER.exception("unexpected streaming failure for %s", observation.product_id)
            raise HTTPException(
                status_code=422,
                detail=f"unexpected streaming failure for {observation.product_id}: {type(exc).__name__}: {exc}",
            ) from exc
    return {
        "status": "complete",
        "count": len(results),
        "parameters": {
            "frequency": frequency,
            "polarization": polarization,
            "threshold_db": threshold_db,
        },
        "granules": results,
        "storage": "Only derived previews and JSON metadata were written; no .h5 file was saved.",
    }


@app.post("/api/earthdata/change")
def change_observations(
    observation_ids: list[str],
    frequency: str = Query(default="frequencyA", pattern=r"^frequency[A-Z]$"),
    polarization: str = Query(default="HH", pattern=r"^(HH|HV|VH|VV)$"),
    threshold_db: float = Query(default=3.0, ge=0),
    west: float | None = Query(default=None, ge=-180, le=180),
    south: float | None = Query(default=None, ge=-90, le=90),
    east: float | None = Query(default=None, ge=-180, le=180),
    north: float | None = Query(default=None, ge=-90, le=90),
) -> dict[str, Any]:
    bbox = _validated_optional_bbox(west, south, east, north)
    if len(observation_ids) != 2 or observation_ids[0] == observation_ids[1]:
        raise HTTPException(status_code=422, detail="select exactly two different observations")
    missing = [item for item in observation_ids if item not in app.state.observations]
    if missing:
        raise HTTPException(status_code=404, detail="both observations must come from a prior catalog search")
    before_id, after_id = observation_ids
    before = app.state.observations[before_id]
    after = app.state.observations[after_id]
    if before.collection != after.collection:
        raise HTTPException(
            status_code=422,
            detail="observations have different processing maturity/collections; select matching products",
        )
    try:
        result = stream_change_pair(
            app.state.granules[before_id],
            app.state.granules[after_id],
            before.product_id,
            after.product_id,
            frequency=frequency,
            polarization=polarization,
            threshold_db=threshold_db,
            bbox=bbox,
        )
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        earthaccess.exceptions.DownloadFailure,
        earthaccess.exceptions.LoginAttemptFailure,
        earthaccess.exceptions.ServiceOutage,
    ) as exc:
        raise HTTPException(status_code=422, detail=f"change processing failed: {exc}") from exc
    except Exception as exc:
        LOGGER.exception("unexpected change failure for %s -> %s", before.product_id, after.product_id)
        raise HTTPException(
            status_code=422,
            detail=f"unexpected change failure: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "status": "complete",
        "result": result,
        "storage": "Only a derived preview and JSON metadata were written; no .h5 file was saved.",
    }


@app.post("/api/earthdata/observations/{observation_id}/full-download")
def forbidden_full_download(observation_id: str) -> dict[str, Any]:
    observation = app.state.observations.get(observation_id)
    raise HTTPException(
        status_code=410,
        detail="Full granule downloads are disabled. Use POST /api/earthdata/stream.",
    )


@app.post("/api/products/{product_id}/inspect", response_model=ProductInspection)
def inspect_product(product_id: str) -> ProductInspection:
    path = app.state.products.get(product_id)
    if not path or path.resolve().parent != DOWNLOAD_ROOT or not path.exists():
        raise HTTPException(status_code=404, detail="product is not downloaded in this session")
    try:
        datasets = inspect_hdf5(path)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"product inspection failed: {exc}") from exc
    candidates = [record for record in datasets if "/gcov/" in record.path.lower() and "/grids/" in record.path.lower()]
    return ProductInspection(product_id=product_id, path=path.name, datasets=datasets, candidate_count=len(candidates))


STATIC_ROOT = ROOT / "static"
STREAMED_ROOT = ROOT / "data" / "streamed"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STREAMED_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/data/streamed", StaticFiles(directory=STREAMED_ROOT), name="streamed")
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="frontend")
