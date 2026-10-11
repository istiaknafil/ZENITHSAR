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


class CountingFile:
    def __init__(self, file_object: Any) -> None:
        self._file = file_object
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._file.read(size)
        self.bytes_read += len(data)
        return data

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


def _candidate_paths(product: Any) -> list[str]:
    candidates: list[str] = []

    def visit(name: str, item: Any) -> None:
        if not hasattr(item, "shape") or len(getattr(item, "shape", ())) < 2:
            return
        normalized = f"/{name}".lower()
        if "/gcov/grids/frequencya/" in normalized and normalized.endswith(("/hhhh", "/hvhv")):
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


def stream_granule(granule: Any, product_id: str) -> dict[str, Any]:
    import earthaccess
    import h5py

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = "".join(character if character.isalnum() or character in "._-" else "_" for character in product_id)
    image_path = OUTPUT_ROOT / f"{stem}_frequencyA_preview.png"
    metadata_path = OUTPUT_ROOT / f"{stem}_stream.json"
    if image_path.exists() and metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8")) | {"cached": True}

    started = time.perf_counter()
    handles = earthaccess.open([granule], show_progress=False)
    if not handles:
        raise RuntimeError("earthaccess.open returned no file handle")
    counter = CountingFile(handles[0])
    try:
        with h5py.File(counter, "r") as product:
            paths = _candidate_paths(product)
            if not paths:
                raise ValueError("no frequencyA HHHH/HVHV GCOV datasets were found")
            dataset_path = paths[0]
            image, details = _read_overview(product[dataset_path])
            coordinates = {}
            for name in ("xCoordinates", "yCoordinates"):
                matches = [key for key in product.keys() if key.endswith(name)]
                if matches:
                    coordinates[name] = {"path": matches[0], "shape": list(product[matches[0]].shape)}
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
