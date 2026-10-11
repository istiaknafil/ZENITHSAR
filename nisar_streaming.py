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

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self._file.close()

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
    stride = max(1, math.ceil(math.sqrt((height * width) / MAX_PIXELS)))
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


def _open_dataset_overview(
    granule: Any,
    frequency: str,
    polarization: str,
) -> tuple[np.ndarray, dict[str, Any], int]:
    import earthaccess
    import h5py

    _ensure_earthdata_login(earthaccess)
    handles = earthaccess.open([granule], show_progress=False)
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
            power, details = _read_power_overview(product[paths[0]])
            details["dataset_paths"] = paths
            details["selected_dataset"] = paths[0]
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
) -> dict[str, Any]:
    if before_product_id == after_product_id:
        raise ValueError("before and after observations must be different")
    before, before_details, before_bytes = _open_dataset_overview(
        before_granule, frequency, polarization
    )
    after, after_details, after_bytes = _open_dataset_overview(
        after_granule, frequency, polarization
    )
    if before.shape != after.shape:
        raise ValueError(
            "selected products do not have matching sampled grid shapes; "
            "a georeferenced reprojection is required before comparison"
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
) -> dict[str, Any]:
    import earthaccess
    import h5py

    _ensure_earthdata_login(earthaccess)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = "".join(character if character.isalnum() or character in "._-" else "_" for character in product_id)
    variant = f"{frequency}_{polarization}"
    image_path = OUTPUT_ROOT / f"{stem}_{variant}_preview.png"
    metadata_path = OUTPUT_ROOT / f"{stem}_{variant}_stream.json"
    if image_path.exists() and metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8")) | {"cached": True}

    started = time.perf_counter()
    handles = earthaccess.open([granule], show_progress=False)
    if not handles:
        raise RuntimeError("earthaccess.open returned no file handle")
    counter = CountingFile(handles[0])
    try:
        with h5py.File(counter, "r") as product:
            paths = _candidate_paths(product, frequency=frequency, polarization=polarization)
            if not paths:
                raise ValueError("no frequencyA HHHH/HVHV GCOV datasets were found")
            dataset_path = paths[0]
            image, details = _read_overview(product[dataset_path])
            coordinates: dict[str, Any] = {}
            def visit_coordinates(name: str, item: Any) -> None:
                for coordinate_name in ("xCoordinates", "yCoordinates"):
                    if name.endswith(coordinate_name) and hasattr(item, "shape"):
                        coordinates[coordinate_name] = {
                            "path": f"/{name}",
                            "shape": list(item.shape),
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
        "coordinates": coordinates,
        "attributes": attributes,
        "bytes_read": counter.bytes_read,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "preview": image_path.relative_to(Path(__file__).resolve().parent).as_posix(),
        "cached": False,
        "warning": "Preview is a calibrated-data overview; it is not a confirmed environmental change.",
    }
    metadata_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
