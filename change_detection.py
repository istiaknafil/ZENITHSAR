"""Prototype pixel-wise change detection for two aligned NISAR GCOV layers.

REAL DATA MODE (input GeoTIFFs must already be exported from matching GCOV layers,
registered to exactly the same grid, and contain linear gamma-0 power values):
    python change_detection.py --before data/date1_HHHH.tif --after data/date2_HHHH.tif

OPTIONAL binary QA masks: 1 = valid, 0 = invalid. Only pass masks after interpreting
that product's QA semantics; do not assume the NISAR GCOV `mask` layer itself is a
simple valid/invalid mask.

SYNTHETIC SCREEN-RECORDING DEMO (not real satellite data):
    python change_detection.py --demo
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.transform import from_origin

FLOAT_NODATA = -9999.0
BYTE_NODATA = 255


def read_raster(path: Path):
    """Read one georeferenced band and its standard raster validity mask."""
    with rasterio.open(path) as src:
        if src.count < 1:
            raise ValueError(f"No raster bands found in: {path}")
        data = src.read(1).astype(np.float32)
        valid = src.read_masks(1) > 0
        valid &= np.isfinite(data)
        if src.nodata is not None:
            if np.isnan(src.nodata):
                valid &= np.isfinite(data)
            else:
                valid &= data != src.nodata
        profile = src.profile.copy()
        grid = {
            "width": src.width,
            "height": src.height,
            "crs": src.crs,
            "transform": src.transform,
        }
    return data, valid, profile, grid


def assert_same_grid(grid_a, grid_b, label_a="before", label_b="after"):
    """Prevent pixel-wise subtraction unless both inputs share the exact same grid."""
    differences = []
    for key in ("width", "height", "crs", "transform"):
        if grid_a[key] != grid_b[key]:
            differences.append(key)
    if differences:
        raise ValueError(
            "The input rasters are not aligned. Different properties: "
            + ", ".join(differences)
            + ". Reproject/resample both layers to the same CRS, transform, and "
              "pixel grid before comparing them."
        )


def read_binary_quality_mask(path: Path, expected_grid, shape):
    """Read a prepared binary QA mask: 1 = valid, 0 = invalid."""
    with rasterio.open(path) as src:
        q_grid = {
            "width": src.width,
            "height": src.height,
            "crs": src.crs,
            "transform": src.transform,
        }
        assert_same_grid(expected_grid, q_grid, "image", f"QA mask {path.name}")
        if src.count < 1:
            raise ValueError(f"No band found in quality mask: {path}")
        values = src.read(1)
        qa_valid = np.isfinite(values) & (values > 0)
        if src.nodata is not None:
            qa_valid &= values != src.nodata
    if qa_valid.shape != shape:
        raise ValueError(f"Quality mask has wrong shape: {path}")
    return qa_valid


def write_single_band(path, array, profile, dtype, nodata):
    out_profile = profile.copy()
    out_profile.update(
        driver="GTiff",
        count=1,
        dtype=dtype,
        nodata=nodata,
        compress="deflate",
    )
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(array.astype(dtype), 1)


def make_preview(before, after, change_db, valid, threshold_db, output_path):
    """Save a compact visual preview; invalid pixels are hidden."""
    before_view = np.ma.masked_where(~valid, before)
    after_view = np.ma.masked_where(~valid, after)
    change_view = np.ma.masked_where(~valid, change_db)
    finite_change = np.abs(change_db[valid])
    limit = max(threshold_db, float(np.percentile(finite_change, 98))) if finite_change.size else threshold_db

    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), constrained_layout=True)
    axes[0].imshow(before_view, cmap="gray")
    axes[0].set_title("Date 1 (linear power)")
    axes[1].imshow(after_view, cmap="gray")
    axes[1].set_title("Date 2 (linear power)")
    image = axes[2].imshow(change_view, cmap="RdBu_r", vmin=-limit, vmax=limit)
    axes[2].set_title(f"Difference (dB) | threshold ±{threshold_db:g} dB")
    fig.colorbar(image, ax=axes[2], shrink=0.8, label="dB change")
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    fig.suptitle("SAR change-detection prototype — review candidates, not confirmed causes")
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def compare(before_path, after_path, out_dir, threshold_db,
            qa_before_path=None, qa_after_path=None):
    before, valid_before, profile, grid_before = read_raster(before_path)
    after, valid_after, after_profile, grid_after = read_raster(after_path)
    assert_same_grid(grid_before, grid_after)

    # Extra product-specific QA masks are optional. These must already be converted
    # to binary validity rasters (1 valid / 0 invalid) using the product documentation.
    valid = valid_before & valid_after
    if qa_before_path:
        valid &= read_binary_quality_mask(qa_before_path, grid_before, before.shape)
    if qa_after_path:
        valid &= read_binary_quality_mask(qa_after_path, grid_before, after.shape)

    # GCOV diagonal layers (e.g., HHHH) contain calibrated gamma-0 power.
    # The logarithmic conversion below therefore assumes positive, linear power,
    # NOT values that have already been converted to decibels.
    valid &= (before > 0) & (after > 0)
    if not np.any(valid):
        raise ValueError("No valid overlapping positive-power pixels remain after masking.")

    change_db = np.full(before.shape, FLOAT_NODATA, dtype=np.float32)
    change_db[valid] = 10.0 * np.log10(after[valid]) - 10.0 * np.log10(before[valid])

    # Candidate change is based on absolute dB difference. The threshold is a
    # starting parameter for exploration, not a universal scientific cutoff.
    changed = np.zeros(before.shape, dtype=np.uint8)
    changed[valid] = (np.abs(change_db[valid]) >= threshold_db).astype(np.uint8)
    changed[~valid] = BYTE_NODATA

    out_dir.mkdir(parents=True, exist_ok=True)
    change_path = out_dir / "change_difference_db.tif"
    mask_path = out_dir / "potential_change_mask.tif"
    preview_path = out_dir / "comparison_preview.png"

    write_single_band(change_path, change_db, profile, "float32", FLOAT_NODATA)
    write_single_band(mask_path, changed, profile, "uint8", BYTE_NODATA)
    # Plot nodata cells as NaN so they do not appear as extreme change values.
    plot_change = change_db.copy()
    plot_change[~valid] = np.nan
    make_preview(before, after, plot_change, valid, threshold_db, preview_path)

    candidate_pct = 100.0 * np.count_nonzero(changed[valid] == 1) / np.count_nonzero(valid)
    print(f"Valid overlapping pixels: {np.count_nonzero(valid):,}")
    print(f"Candidate-change pixels (|difference| >= {threshold_db:g} dB): {np.count_nonzero(changed[valid] == 1):,} ({candidate_pct:.2f}%)")
    print(f"Saved difference raster: {change_path}")
    print(f"Saved candidate mask:    {mask_path}")
    print(f"Saved preview:           {preview_path}")
    print("Interpretation warning: candidates need validation; a difference alone does not prove erosion, flooding, or its cause.")


def create_synthetic_demo(folder: Path):
    """Create clearly synthetic data so the processing pipeline can be screen-recorded."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(26)
    before = rng.lognormal(mean=np.log(0.06), sigma=0.22, size=(200, 200)).astype(np.float32)
    after = before.copy()
    after[40:115, 60:135] *= 2.5   # synthetic increased-backscatter patch
    after[130:165, 25:75] *= 0.35  # synthetic decreased-backscatter patch
    after *= rng.lognormal(mean=0.0, sigma=0.035, size=after.shape).astype(np.float32)

    # This is a fake grid used only to demonstrate code execution, not real geography.
    profile = {
        "driver": "GTiff",
        "height": before.shape[0],
        "width": before.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32646",
        "transform": from_origin(500000, 3000000, 20, 20),
        "nodata": FLOAT_NODATA,
        "compress": "deflate",
    }
    before_path = folder / "SYNTHETIC_before.tif"
    after_path = folder / "SYNTHETIC_after.tif"
    with rasterio.open(before_path, "w", **profile) as dst:
        dst.write(before, 1)
    with rasterio.open(after_path, "w", **profile) as dst:
        dst.write(after, 1)
    return before_path, after_path


def main():
    parser = argparse.ArgumentParser(description="Compare aligned GCOV-style backscatter GeoTIFFs.")
    parser.add_argument("--before", type=Path, help="Date-1 single-band GeoTIFF (linear gamma-0 power)")
    parser.add_argument("--after", type=Path, help="Date-2 matching single-band GeoTIFF (linear gamma-0 power)")
    parser.add_argument("--threshold-db", type=float, default=3.0, help="Candidate-change threshold in dB; default: 3")
    parser.add_argument("--out", type=Path, default=Path("outputs"), help="Output directory; default: outputs")
    parser.add_argument("--qa-before", type=Path, help="Optional binary QA mask for date 1 (1 valid, 0 invalid)")
    parser.add_argument("--qa-after", type=Path, help="Optional binary QA mask for date 2 (1 valid, 0 invalid)")
    parser.add_argument("--demo", action="store_true", help="Run a synthetic demonstration; NOT real satellite data")
    args = parser.parse_args()

    if args.threshold_db < 0:
        parser.error("--threshold-db must be non-negative")
    if args.demo:
        print("WARNING: Running SYNTHETIC DEMO DATA, not actual NISAR observations.")
        before_path, after_path = create_synthetic_demo(Path("demo_data"))
    else:
        if not args.before or not args.after:
            parser.error("Provide --before and --after GeoTIFFs, or use --demo for synthetic data.")
        before_path, after_path = args.before, args.after
        for path in (before_path, after_path):
            if not path.exists():
                parser.error(f"Input file not found: {path}")

    compare(before_path, after_path, args.out, args.threshold_db,
            args.qa_before, args.qa_after)


if __name__ == "__main__":
    main()
