from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from typing import TYPE_CHECKING

FLOAT_NODATA = -9999.0
BYTE_NODATA = 255
_COMPATIBILITY_TAGS = (
    "POLARIZATION",
    "POLARISATION",
    "ACQUISITION_ID",
    "ACQUISITION_MODE",
    "ORBIT_DIRECTION",
    "UNITS",
    "UNIT",
    "MEASUREMENT_UNIT",
)


@dataclass(frozen=True)
class SarChangeResult:
    difference_path: str
    mask_path: str
    preview_path: str
    summary_path: str
    valid_pixel_count: int
    candidate_pixel_count: int
    threshold_db: float
    units: str
    synthetic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _grid(src: Any) -> dict[str, Any]:
    return {
        "width": src.width,
        "height": src.height,
        "crs": src.crs,
        "transform": src.transform,
        "bounds": src.bounds,
        "res": src.res,
    }


def _assert_compatible(
    before: Any, after: Any
) -> None:
    left, right = _grid(before), _grid(after)
    differences = [
        key for key in ("width", "height", "crs", "transform", "bounds", "res")
        if left[key] != right[key]
    ]
    if differences:
        raise ValueError(
            "Input rasters are not spatially compatible; differing properties: "
            + ", ".join(differences)
            + ". Reproject/resample both layers to the same grid first."
        )
    before_tags = {key: before.tags().get(key) for key in _COMPATIBILITY_TAGS}
    after_tags = {key: after.tags().get(key) for key in _COMPATIBILITY_TAGS}
    for key in _COMPATIBILITY_TAGS:
        if before_tags[key] and after_tags[key] and before_tags[key] != after_tags[key]:
            raise ValueError(
                f"Incompatible raster metadata: {key} differs "
                f"({before_tags[key]!r} vs {after_tags[key]!r})."
            )


def _valid_pixels(
    src: Any, data: np.ndarray, units: str
) -> np.ndarray:
    valid = (src.read_masks(1) > 0) & np.isfinite(data)
    if src.nodata is not None and not np.isnan(src.nodata):
        valid &= data != src.nodata
    if units == "linear":
        valid &= data > 0
    elif units == "db":
        valid &= data > -200
    else:
        raise ValueError("units must be 'linear' or 'db'.")
    return valid


def _write(path: Path, array: np.ndarray, profile: dict[str, Any], dtype: str, nodata: float) -> None:
    output_profile = profile.copy()
    output_profile.update(
        driver="GTiff", count=1, dtype=dtype, nodata=nodata, compress="deflate"
    )
    with rasterio.open(path, "w", **output_profile) as dst:
        dst.write(array.astype(dtype), 1)


def _save_preview(
    before: np.ndarray,
    after: np.ndarray,
    difference: np.ndarray,
    valid: np.ndarray,
    threshold_db: float,
    units: str,
    path: Path,
) -> None:
    masked_before = np.ma.masked_where(~valid, before)
    masked_after = np.ma.masked_where(~valid, after)
    masked_difference = np.ma.masked_where(~valid, difference)
    finite = np.abs(difference[valid])
    limit = max(threshold_db, float(np.percentile(finite, 98))) if finite.size else threshold_db
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), constrained_layout=True)
    axes[0].imshow(masked_before, cmap="gray")
    axes[0].set_title(f"Before ({units})")
    axes[1].imshow(masked_after, cmap="gray")
    axes[1].set_title(f"After ({units})")
    image = axes[2].imshow(masked_difference, cmap="RdBu_r", vmin=-limit, vmax=limit)
    axes[2].set_title(f"Difference (dB), threshold ±{threshold_db:g}")
    fig.colorbar(image, ax=axes[2], shrink=0.8, label="dB")
    for axis in axes:
        axis.set_axis_off()
    fig.suptitle("Candidate SAR changes — requires interpretation and validation")
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def compare_sar(
    before_path: Path,
    after_path: Path,
    output_dir: Path,
    *,
    threshold_db: float = 3.0,
    units: str = "linear",
    polarization: str | None = None,
    acquisition: str | None = None,
) -> SarChangeResult:
    """Compare two prepared, single-band, co-registered SAR GeoTIFF layers.

    This intentionally does not read HDF5. NISAR HDF5 products need product-specific
    GCOV dataset and metadata inspection before conversion to analysis-ready GeoTIFF.
    """
    try:
        import rasterio
    except ImportError as exc:
        raise RuntimeError(
            "Rasterio could not be loaded. Install requirements.txt and, on "
            "Windows, use a Python environment permitted to load Rasterio's "
            "native DLLs."
        ) from exc
    if threshold_db < 0:
        raise ValueError("threshold_db must be non-negative.")
    if units not in {"linear", "db"}:
        raise ValueError("units must be 'linear' or 'db'.")
    if not before_path.exists() or not after_path.exists():
        missing = [str(p) for p in (before_path, after_path) if not p.exists()]
        raise FileNotFoundError("Input file not found: " + ", ".join(missing))

    with rasterio.open(before_path) as before_src, rasterio.open(after_path) as after_src:
        if before_src.count < 1 or after_src.count < 1:
            raise ValueError("Each SAR input must contain at least one raster band.")
        _assert_compatible(before_src, after_src)
        if polarization:
            expected = polarization.upper()
            if expected not in {"HH", "HV", "VH", "VV"}:
                raise ValueError("polarization must be one of HH, HV, VH, or VV.")
            observed = before_src.tags().get("POLARIZATION") or before_src.tags().get("POLARISATION")
            if observed and observed.upper() != expected:
                raise ValueError(
                    f"Requested polarization {expected} does not match input metadata {observed}."
                )
        unit_tags = [
            before_src.tags().get(key) or after_src.tags().get(key)
            for key in ("UNITS", "UNIT", "MEASUREMENT_UNIT")
        ]
        declared_unit = next((value for value in unit_tags if value), None)
        if declared_unit:
            normalized = declared_unit.lower().replace(" ", "")
            if units == "linear" and ("db" in normalized or "decibel" in normalized):
                raise ValueError(
                    "Input metadata declares dB values, but --units linear was selected."
                )
            if units == "db" and "linear" in normalized:
                raise ValueError(
                    "Input metadata declares linear values, but --units db was selected."
                )
        before = before_src.read(1).astype(np.float32)
        after = after_src.read(1).astype(np.float32)
        valid = _valid_pixels(before_src, before, units) & _valid_pixels(after_src, after, units)
        if not np.any(valid):
            raise ValueError("No valid overlapping SAR pixels remain after masking.")

        difference = np.full(before.shape, FLOAT_NODATA, dtype=np.float32)
        if units == "linear":
            difference[valid] = 10 * np.log10(after[valid]) - 10 * np.log10(before[valid])
        else:
            difference[valid] = after[valid] - before[valid]
        changed = np.full(before.shape, BYTE_NODATA, dtype=np.uint8)
        changed[valid] = (np.abs(difference[valid]) >= threshold_db).astype(np.uint8)
        profile = before_src.profile.copy()

    output_dir.mkdir(parents=True, exist_ok=True)
    difference_path = output_dir / "sar_difference_db.tif"
    mask_path = output_dir / "potential_change_mask.tif"
    preview_path = output_dir / "sar_comparison_preview.png"
    summary_path = output_dir / "sar_processing_summary.json"
    _write(difference_path, difference, profile, "float32", FLOAT_NODATA)
    _write(mask_path, changed, profile, "uint8", BYTE_NODATA)
    _save_preview(before, after, difference, valid, threshold_db, units, preview_path)
    candidate_count = int(np.count_nonzero(changed[valid]))
    summary = {
        "inputs": {"before": str(before_path), "after": str(after_path)},
        "settings": {
            "units": units,
            "threshold_db": threshold_db,
            "threshold_is_heuristic": True,
            "polarization": polarization,
            "acquisition": acquisition,
        },
        "valid_pixel_count": int(np.count_nonzero(valid)),
        "candidate_pixel_count": candidate_count,
        "candidate_percentage": 100 * candidate_count / int(np.count_nonzero(valid)),
        "interpretation_warning": (
            "Flagged pixels are candidate changes requiring interpretation and "
            "validation, not confirmed flooding, erosion, or other events."
        ),
        "hdf5_note": "This pipeline accepts prepared GeoTIFF layers, not arbitrary NISAR HDF5.",
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return SarChangeResult(
        str(difference_path), str(mask_path), str(preview_path), str(summary_path),
        int(np.count_nonzero(valid)), candidate_count, threshold_db, units
    )
