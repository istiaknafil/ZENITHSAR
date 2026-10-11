from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


@dataclass(frozen=True)
class ImageInfo:
    filename: str
    format: str
    width: int
    height: int
    mode: str
    bands: int
    dtype: str
    minimum: float
    maximum: float
    mean: float
    standard_deviation: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_image(path: Path) -> tuple[Image.Image, ImageInfo]:
    """Open a PNG/JPEG and return the original image and basic pixel statistics."""
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Supported image formats are PNG, JPG, and JPEG.")

    with Image.open(path) as opened:
        image = opened.copy()
        values = np.asarray(image)
        numeric = values.astype(np.float64)
        info = ImageInfo(
            filename=path.name,
            format=opened.format or path.suffix.lstrip(".").upper(),
            width=opened.width,
            height=opened.height,
            mode=opened.mode,
            bands=1 if values.ndim == 2 else values.shape[2],
            dtype=str(values.dtype),
            minimum=float(numeric.min()),
            maximum=float(numeric.max()),
            mean=float(numeric.mean()),
            standard_deviation=float(numeric.std()),
        )
    return image, info


def make_preview(
    image: Image.Image,
    *,
    grayscale: bool = False,
    enhance_contrast: bool = False,
) -> Image.Image:
    """Apply optional display-only transformations without changing the source."""
    preview = ImageOps.grayscale(image) if grayscale else image.copy()
    if enhance_contrast:
        preview = ImageOps.autocontrast(preview)
        preview = ImageEnhance.Contrast(preview).enhance(1.15)
    return preview

