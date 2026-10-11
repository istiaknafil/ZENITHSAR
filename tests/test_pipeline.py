from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import rasterio
from rasterio.transform import from_origin

from src.image_comparison import compare_images
from src.image_inspection import inspect_image
from src.sar_change_detection import compare_sar


def test_image_inspection_and_comparison(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.jpg"
    Image.fromarray(np.zeros((4, 5, 3), dtype=np.uint8)).save(before)
    Image.fromarray(np.full((4, 5, 3), 10, dtype=np.uint8)).save(after)
    _, info = inspect_image(before)
    assert info.width == 5 and info.height == 4 and info.mean == 0
    result = compare_images(before, after, tmp_path / "compare.png")
    assert result.mean_absolute_difference == 10
    assert (tmp_path / "compare.png").exists()


def test_image_comparison_rejects_mismatched_dimensions(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (4, 4)).save(before)
    Image.new("RGB", (5, 4)).save(after)
    with pytest.raises(ValueError, match="dimensions differ"):
        compare_images(before, after, tmp_path / "compare.png")


def test_image_inspection_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        inspect_image(tmp_path / "missing.png")


def _write_raster(path: Path, values: np.ndarray, *, transform=None, nodata=-9999.0) -> None:
    rasterio = pytest.importorskip("rasterio", exc_type=ImportError)
    from rasterio.transform import from_origin

    profile = {
        "driver": "GTiff", "height": values.shape[0], "width": values.shape[1],
        "count": 1, "dtype": "float32", "crs": "EPSG:32646",
        "transform": transform or from_origin(500000, 3000000, 20, 20),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values.astype("float32"), 1)


def test_sar_linear_change_and_nodata(tmp_path: Path) -> None:
    before = np.ones((5, 5), dtype=np.float32)
    after = before.copy()
    after[1, 1] = 2
    before[0, 0] = -9999
    after[0, 0] = -9999
    _write_raster(tmp_path / "before.tif", before)
    _write_raster(tmp_path / "after.tif", after)
    result = compare_sar(tmp_path / "before.tif", tmp_path / "after.tif", tmp_path / "out")
    assert result.valid_pixel_count == 24
    assert result.candidate_pixel_count == 1
    assert Path(result.difference_path).exists()
    assert Path(result.mask_path).exists()
    assert Path(result.summary_path).exists()


def test_sar_rejects_mismatched_grid(tmp_path: Path) -> None:
    values = np.ones((3, 3), dtype=np.float32)
    _write_raster(tmp_path / "before.tif", values)
    _write_raster(tmp_path / "after.tif", values, transform=from_origin(500000, 3000000, 10, 10))
    with pytest.raises(ValueError, match="not spatially compatible"):
        compare_sar(tmp_path / "before.tif", tmp_path / "after.tif", tmp_path / "out")


def test_sar_rejects_no_valid_pixels(tmp_path: Path) -> None:
    values = np.full((3, 3), -9999, dtype=np.float32)
    _write_raster(tmp_path / "before.tif", values)
    _write_raster(tmp_path / "after.tif", values)
    with pytest.raises(ValueError, match="No valid"):
        compare_sar(tmp_path / "before.tif", tmp_path / "after.tif", tmp_path / "out")
