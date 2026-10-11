"""Bounded NISAR GCOV streaming preprocessing.

Remote HDF5 files are opened through earthaccess/fsspec and never copied to
the project filesystem. Only small previews and JSON metadata are persisted.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

OUTPUT_ROOT = Path(__file__).resolve().parent / "data" / "streamed"
MAX_GRANULES = 5
MAX_PIXELS = 1_000_000
_AUTHENTICATED = False


def _ensure_earthdata_login(earthaccess: Any) -> None:
    global _AUTHENTICATED
    if _AUTHENTICATED:
        return
    try:
        earthaccess.login(strategy="environment")
    except (
        earthaccess.exceptions.LoginStrategyUnavailable,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise RuntimeError(f"Earthdata authentication failed: {exc}") from exc
    _AUTHENTICATED = True


class CountingFile:
    def __init__(self, file_object: Any) -> None:
        self._file = file_object
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._file.read(size)
        self.bytes_read += len(data)
        return data

    def readinto(self, buffer: Any) -> int:
        data = self._file.read(len(buffer))
        count = len(data)
        buffer[:count] = data
        self.bytes_read += count
        return count

    def seek(self, *args: Any) -> int:
        return self._file.seek(*args)

    def tell(self) -> int:
        return self._file.tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def write(self, _data: bytes) -> int:
        raise OSError("remote NISAR stream is read-only")

    def close(self) -> None:
        self._file.close()

    def flush(self) -> None:
        flush = getattr(self._file, "flush", None)
        if flush is not None:
            flush()

    def truncate(self, size: int | None = None) -> int:
        truncate = getattr(self._file, "truncate", None)
        if truncate is None:
            return self.tell()
        return truncate(size) if size is not None else truncate()

    def __enter__(self) -> "CountingFile":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return value


def _scalar_attributes(item: Any) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in getattr(item, "attrs", {}).items()
        if np.asarray(value).ndim == 0
    }


def _discover_georeferencing(product: Any) -> dict[str, Any]:
    coordinates: dict[str, Any] = {}
    projection: dict[str, Any] = {}

    def visit(name: str, item: Any) -> None:
        lower_name = name.lower()
        if hasattr(item, "shape") and lower_name.endswith(("xcoordinates", "ycoordinates")):
            coordinate_name = "xCoordinates" if lower_name.endswith("xcoordinates") else "yCoordinates"
            shape = list(item.shape)
            entry: dict[str, Any] = {"path": f"/{name}", "shape": shape}
            if len(shape) == 1 and shape[0]:
                first = _json_value(item[0])
                last = _json_value(item[shape[0] - 1])
                if isinstance(first, (int, float)) and isinstance(last, (int, float)):
                    entry["range"] = [first, last]
            coordinates[coordinate_name] = entry
        attrs = _scalar_attributes(item)
        for key, value in attrs.items():
            normalized = key.lower()
            if "epsg" in normalized or "projection" in normalized or normalized in {
                "spatial_ref",
                "crs",
                "proj4",
            }:
                projection[key] = value

    product.visititems(visit)
    result: dict[str, Any] = {"coordinates": coordinates}
    if projection:
        result["projection_attributes"] = projection
    if "xCoordinates" in coordinates and "yCoordinates" in coordinates:
        x_range = coordinates["xCoordinates"].get("range")
        y_range = coordinates["yCoordinates"].get("range")
        if x_range and y_range:
            result["bounds"] = {
                "xmin": min(x_range),
                "ymin": min(y_range),
                "xmax": max(x_range),
                "ymax": max(y_range),
            }
    return result


def _candidate_paths(product: Any, frequency: str = "frequencyA", polarization: str = "HH") -> list[str]:
    candidates: list[str] = []

    def visit(name: str, item: Any) -> None:
        if not hasattr(item, "shape") or len(getattr(item, "shape", ())) < 2:
            return
        normalized = f"/{name}".lower()
        polarization_paths = (f"/{polarization.lower()}{polarization.lower()}", f"/{polarization.lower()}") 
        if f"/gcov/grids/{frequency.lower()}/" in normalized and normalized.endswith(polarization_paths):
            candidates.append(f"/{name}")

    product.visititems(visit)
    return candidates


def _read_overview(dataset: Any) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = dataset.shape[-2], dataset.shape[-1]
    stride = min(
        max(1, math.ceil(math.sqrt((height * width) / MAX_PIXELS))),
        height,
        width,
    )
    array = np.asarray(dataset[::stride, ::stride], dtype=np.float32)
    valid = np.isfinite(array) & (array > 0)
    if not np.any(valid):
        raise ValueError("selected GCOV dataset contains no positive finite samples")
    db = np.full(array.shape, np.nan, dtype=np.float32)
    db[valid] = 10.0 * np.log10(array[valid])
    low, high = np.nanpercentile(db, [2, 98])
    scaled = np.clip((db - low) / max(high - low, 1e-6) * 255, 0, 255)
    image = np.zeros(array.shape, dtype=np.uint8)
    image[valid] = scaled[valid].astype(np.uint8)
    return image, {
        "shape": list(dataset.shape),
        "chunks": list(dataset.chunks) if dataset.chunks else None,
        "stride": stride,
        "valid_pixels": int(np.count_nonzero(valid)),
        "db_percentiles": [float(low), float(high)],
    }


def _read_power_overview(dataset: Any) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = dataset.shape[-2], dataset.shape[-1]
    stride = max(1, math.ceil(math.sqrt((height * width) / MAX_PIXELS)))
    sampled_height = height // stride
    sampled_width = width // stride
    if stride == 1:
        power = np.asarray(dataset[:sampled_height, :sampled_width], dtype=np.float32)
    else:
        window = np.asarray(
            dataset[: sampled_height * stride, : sampled_width * stride],
            dtype=np.float32,
        )
        power = window.reshape(sampled_height, stride, sampled_width, stride).mean(
            axis=(1, 3), dtype=np.float32
        )
    valid = np.isfinite(power) & (power > 0)
    return power, {
        "shape": list(dataset.shape),
        "chunks": list(dataset.chunks) if dataset.chunks else None,
        "stride": stride,
        "valid_pixels": int(np.count_nonzero(valid)),
    }


def _coordinate_window(product: Any, dataset_path: str, bbox: tuple[float, float, float, float] | None) -> tuple[slice, slice, dict[str, Any]]:
    if bbox is None:
        return slice(None), slice(None), {}
    import pyproj

    group_path = dataset_path.rsplit("/", 1)[0]
    group = product[group_path]
    if "xCoordinates" not in group or "yCoordinates" not in group:
        raise ValueError("selected GCOV grid has no xCoordinates and yCoordinates datasets")
    x = np.asarray(group["xCoordinates"][:])
    y = np.asarray(group["yCoordinates"][:])
    epsg_value = None
    if "projection" in group:
        projection = group["projection"]
        epsg_value = _json_value(projection[()] if hasattr(projection, "shape") else projection)
    if epsg_value is None:
        for key, value in getattr(group, "attrs", {}).items():
            if "epsg" in str(key).lower():
                epsg_value = _json_value(value)
                break
    if epsg_value is None:
        raise ValueError("selected GCOV grid has no numeric projection/EPSG metadata")
    if isinstance(epsg_value, str):
        digits = "".join(character for character in epsg_value if character.isdigit())
        epsg_value = digits
    epsg = int(epsg_value)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    west, south, east, north = bbox
    x0, y0 = transformer.transform(west, south)
    x1, y1 = transformer.transform(east, north)
    x_indices = np.where((x >= min(x0, x1)) & (x <= max(x0, x1)))[0]
    y_indices = np.where((y >= min(y0, y1)) & (y <= max(y0, y1)))[0]
    if not x_indices.size or not y_indices.size:
        raise ValueError("requested AOI does not overlap the selected NISAR grid")
    y_slice = slice(int(y_indices.min()), int(y_indices.max()) + 1)
    x_slice = slice(int(x_indices.min()), int(x_indices.max()) + 1)
    return y_slice, x_slice, {
        "epsg": epsg,
        "x_range": [float(x[x_slice][0]), float(x[x_slice][-1])],
        "y_range": [float(y[y_slice][0]), float(y[y_slice][-1])],
        "source_bbox": list(bbox),
    }


def _read_power_window(dataset: Any, y_slice: slice, x_slice: slice) -> tuple[np.ndarray, dict[str, Any]]:
    height = y_slice.stop - y_slice.start
    width = x_slice.stop - x_slice.start
    stride = min(
        max(1, math.ceil(math.sqrt((height * width) / MAX_PIXELS))),
        height,
        width,
    )
    sampled_height = max(1, height // stride)
    sampled_width = max(1, width // stride)
    window = np.asarray(
        dataset[
            y_slice.start : y_slice.start + sampled_height * stride,
            x_slice.start : x_slice.start + sampled_width * stride,
        ],
        dtype=np.float32,
    )
    if stride > 1:
        power = window.reshape(sampled_height, stride, sampled_width, stride).mean(
            axis=(1, 3), dtype=np.float32
        )
    else:
        power = window
    valid = np.isfinite(power) & (power > 0)
    if not np.any(valid):
        raise ValueError("selected AOI contains no positive finite power samples")
    return power, {
        "shape": list(dataset.shape),
        "chunks": list(dataset.chunks) if dataset.chunks else None,
        "stride": stride,
        "valid_pixels": int(np.count_nonzero(valid)),
    }


def _open_dataset_overview(
    granule: Any,
    frequency: str,
    polarization: str,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[np.ndarray, dict[str, Any], int]:
    import earthaccess
    import h5py

    _ensure_earthdata_login(earthaccess)
    handles = earthaccess.open(
        [granule],
        credentials_endpoint="https://nisar.asf.earthdatacloud.nasa.gov/s3credentials",
        show_progress=False,
        open_kwargs={"cache_type": "blockcache", "block_size": 16 * 1024 * 1024},
    )
    if not handles:
        raise RuntimeError("earthaccess.open returned no file handle")
    counter = CountingFile(handles[0])
    try:
        with h5py.File(counter, "r") as product:
            paths = _candidate_paths(product, frequency=frequency, polarization=polarization)
            if not paths:
                raise ValueError(
                    f"no {frequency} {polarization} GCOV dataset was found"
                )
            y_slice, x_slice, aoi_details = _coordinate_window(product, paths[0], bbox)
            dataset = product[paths[0]]
            if y_slice.start is None and y_slice.stop is None and x_slice.start is None and x_slice.stop is None:
                power, details = _read_power_overview(dataset)
            else:
                power, details = _read_power_window(dataset, y_slice, x_slice)
            details["dataset_paths"] = paths
            details["selected_dataset"] = paths[0]
            details["georeferencing"] = _discover_georeferencing(product)
            details["aoi"] = aoi_details
            if aoi_details:
                details["georeferencing"]["bounds"] = {
                    "xmin": min(aoi_details["x_range"]),
                    "ymin": min(aoi_details["y_range"]),
                    "xmax": max(aoi_details["x_range"]),
                    "ymax": max(aoi_details["y_range"]),
                }
            return power, details, counter.bytes_read
    finally:
        counter.close()
        for handle in handles[1:]:
            handle.close()


def stream_change_pair(
    before_granule: Any,
    after_granule: Any,
    before_product_id: str,
    after_product_id: str,
    *,
    frequency: str = "frequencyA",
    polarization: str = "HH",
    threshold_db: float = 3.0,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    if before_product_id == after_product_id:
        raise ValueError("before and after observations must be different")
    before, before_details, before_bytes = _open_dataset_overview(
        before_granule, frequency, polarization, bbox
    )
    after, after_details, after_bytes = _open_dataset_overview(
        after_granule, frequency, polarization, bbox
    )
    if before.shape != after.shape:
        raise ValueError(
            "selected products do not have matching sampled grid shapes; "
            "a georeferenced reprojection is required before comparison"
        )
    before_geo = before_details.get("georeferencing", {})
    after_geo = after_details.get("georeferencing", {})
    if before_geo.get("bounds") and after_geo.get("bounds"):
        if before_geo["bounds"] != after_geo["bounds"]:
            raise ValueError(
                "selected products have different coordinate bounds; "
                "a georeferenced alignment step is required before comparison"
            )
    valid = np.isfinite(before) & np.isfinite(after) & (before > 0) & (after > 0)
    if not np.any(valid):
        raise ValueError("selected pair contains no overlapping positive-power samples")
    difference = np.full(before.shape, np.nan, dtype=np.float32)
    difference[valid] = 10 * np.log10(after[valid]) - 10 * np.log10(before[valid])
    changed = valid & (np.abs(difference) >= threshold_db)
    finite = np.abs(difference[valid])
    limit = max(threshold_db, float(np.percentile(finite, 98)))
    scaled = np.zeros(difference.shape, dtype=np.uint8)
    scaled[valid] = np.clip(
        (difference[valid] + limit) / (2 * limit) * 255, 0, 255
    ).astype(np.uint8)
    output_root = OUTPUT_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    stem = "_".join(
        "".join(c if c.isalnum() or c in "._-" else "_" for c in value)
        for value in (before_product_id, after_product_id)
    )
    image_path = output_root / f"{stem}_{frequency}_{polarization}_change.png"
    metadata_path = output_root / f"{stem}_{frequency}_{polarization}_change.json"
    Image.fromarray(scaled, mode="L").save(image_path, optimize=True)
    result = {
        "before_product_id": before_product_id,
        "after_product_id": after_product_id,
        "frequency": frequency,
        "polarization": polarization,
        "threshold_db": threshold_db,
        "valid_pixels": int(np.count_nonzero(valid)),
        "changed_pixels": int(np.count_nonzero(changed)),
        "changed_percent": float(np.count_nonzero(changed) / np.count_nonzero(valid) * 100),
        "bytes_read": before_bytes + after_bytes,
        "before": before_details,
        "after": after_details,
        "preview": image_path.relative_to(Path(__file__).resolve().parent).as_posix(),
        "warning": (
            "Candidate backscatter change only. This overview is not georeferenced "
            "until product coordinates and projection metadata are validated."
        ),
    }
    metadata_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def stream_granule(
    granule: Any,
    product_id: str,
    *,
    frequency: str = "frequencyA",
    polarization: str = "HH",
    threshold_db: float = 3.0,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    import earthaccess
    import h5py

    _ensure_earthdata_login(earthaccess)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = "".join(character if character.isalnum() or character in "._-" else "_" for character in product_id)
    bbox_key = "" if bbox is None else "_" + "_".join(f"{value:.5f}" for value in bbox)
    variant = f"{frequency}_{polarization}{bbox_key}"
    image_path = OUTPUT_ROOT / f"{stem}_{variant}_preview.png"
    metadata_path = OUTPUT_ROOT / f"{stem}_{variant}_stream.json"
    if image_path.exists() and metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8")) | {"cached": True}

    started = time.perf_counter()
    handles = earthaccess.open(
        [granule],
        credentials_endpoint="https://nisar.asf.earthdatacloud.nasa.gov/s3credentials",
        show_progress=False,
        open_kwargs={"cache_type": "blockcache", "block_size": 16 * 1024 * 1024},
    )
    if not handles:
        raise RuntimeError("earthaccess.open returned no file handle")
    counter = CountingFile(handles[0])
    try:
        with h5py.File(counter, "r") as product:
            paths = _candidate_paths(product, frequency=frequency, polarization=polarization)
            if not paths:
                raise ValueError("no frequencyA HHHH/HVHV GCOV datasets were found")
            dataset_path = paths[0]
            y_slice, x_slice, aoi_details = _coordinate_window(product, dataset_path, bbox)
            dataset = product[dataset_path]
            if y_slice.start is None and y_slice.stop is None and x_slice.start is None and x_slice.stop is None:
                image, details = _read_overview(dataset)
            else:
                subset, details = _read_power_window(dataset, y_slice, x_slice)
                valid = np.isfinite(subset) & (subset > 0)
                if not np.any(valid):
                    raise ValueError("selected AOI contains no positive finite samples")
                db = np.full(subset.shape, np.nan, dtype=np.float32)
                db[valid] = 10 * np.log10(subset[valid])
                low, high = np.nanpercentile(db, [2, 98])
                image = np.zeros(subset.shape, dtype=np.uint8)
                image[valid] = np.clip((db[valid] - low) / max(high - low, 1e-6) * 255, 0, 255).astype(np.uint8)
                details["db_percentiles"] = [float(low), float(high)]
            details["aoi"] = aoi_details
            georeferencing = _discover_georeferencing(product)
            if aoi_details:
                georeferencing["bounds"] = {
                    "xmin": min(aoi_details["x_range"]),
                    "ymin": min(aoi_details["y_range"]),
                    "xmax": max(aoi_details["x_range"]),
                    "ymax": max(aoi_details["y_range"]),
                }
            product.visititems(visit_coordinates)
            attributes = {
                key: _json_value(value)
                for key, value in product[dataset_path].attrs.items()
                if np.asarray(value).ndim == 0
            }
    finally:
        counter.close()
        for handle in handles[1:]:
            handle.close()

    Image.fromarray(image, mode="L").save(image_path, optimize=True)
    result = {
        "product_id": product_id,
        "dataset_paths": paths,
        "selected_dataset": dataset_path,
        "frequency": frequency,
        "polarization": polarization,
        "threshold_db": threshold_db,
        "details": details,
        "coordinates": georeferencing["coordinates"],
        "georeferencing": georeferencing,
        "attributes": attributes,
        "bytes_read": counter.bytes_read,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "preview": image_path.relative_to(Path(__file__).resolve().parent).as_posix(),
        "cached": False,
        "warning": "Preview is a calibrated-data overview; it is not a confirmed environmental change.",
    }
    metadata_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
