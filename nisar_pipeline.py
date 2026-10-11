"""Optional NISAR GCOV discovery and inspection helpers.

This module deliberately separates Earthdata discovery from browser rendering.
It never embeds credentials and never assumes that a product dataset path exists.
Install the optional dependencies before using these helpers:

    pip install earthaccess h5py
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class DatasetMetadata:
    path: str
    shape: tuple[int, ...] | None
    dtype: str | None
    attributes: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_gcov(
    *,
    start: str | None = None,
    end: str | None = None,
    short_name: str = "NISAR_L2_GCOV_PROVISIONAL_V1",
    count: int = 20,
    bounding_box: tuple[float, float, float, float] | None = None,
) -> list[Any]:
    """Search Earthdata metadata without downloading products.

    Authentication is not required for public metadata search in most
    configurations. Downloading or streaming products still requires a valid
    Earthdata Login session and ASF access agreement.
    """
    try:
        import earthaccess
    except ImportError as exc:
        raise RuntimeError(
            "earthaccess is optional and is not installed. Install it to search "
            "NISAR GCOV metadata."
        ) from exc

    kwargs: dict[str, Any] = {"short_name": short_name, "count": count}
    if start or end:
        kwargs["temporal"] = (start, end)
    if bounding_box:
        kwargs["bounding_box"] = bounding_box
    return list(earthaccess.search_data(**kwargs))


def inspect_hdf5(path: Path) -> list[DatasetMetadata]:
    """Inspect all HDF5 datasets and attributes without guessing a path."""
    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError(
            "h5py is optional and is not installed. Install it to inspect GCOV HDF5."
        ) from exc
    if not path.exists():
        raise FileNotFoundError(f"NISAR product not found: {path}")

    records: list[DatasetMetadata] = []
    with h5py.File(path, "r") as product:
        def visit(name: str, obj: Any) -> None:
            if not isinstance(obj, h5py.Dataset):
                return
            attributes = {
                key: value.item() if hasattr(value, "item") else value
                for key, value in obj.attrs.items()
                if isinstance(value, (str, int, float, bytes)) or getattr(value, "shape", None) == ()
            }
            records.append(
                DatasetMetadata(
                    path=f"/{name}",
                    shape=tuple(obj.shape) if obj.shape else (),
                    dtype=str(obj.dtype),
                    attributes=attributes,
                )
            )

        product.visititems(visit)
    return records


def find_candidate_backscatter_layers(records: Iterable[DatasetMetadata]) -> list[DatasetMetadata]:
    """Return likely GCOV raster candidates for human review.

    Candidates are suggestions only. The caller must inspect units, frequency,
    polarization/covariance semantics, and geolocation before processing.
    """
    candidates = []
    for record in records:
        path = record.path.lower()
        if "/gcov/" in path and "/grids/" in path and record.shape and len(record.shape) >= 2:
            candidates.append(record)
    return candidates
