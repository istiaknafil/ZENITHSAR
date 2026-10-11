from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageChops

from .image_inspection import ImageInfo, inspect_image


@dataclass(frozen=True)
class ComparisonResult:
    before: ImageInfo
    after: ImageInfo
    mean_absolute_difference: float
    maximum_absolute_difference: float
    changed_pixel_count: int
    resized_for_demo: bool


def compare_images(
    before_path: Path,
    after_path: Path,
    output_path: Path,
    *,
    resize_for_demo: bool = False,
) -> ComparisonResult:
    before, before_info = inspect_image(before_path)
    after, after_info = inspect_image(after_path)
    resized = False
    if before.size != after.size:
        if not resize_for_demo:
            raise ValueError(
                "Image dimensions differ. Re-run with --resize-for-demo only "
                "for a visual demonstration; this is not geospatial alignment."
            )
        after = after.resize(before.size)
        resized = True

    before_rgb = before.convert("RGB")
    after_rgb = after.convert("RGB")
    difference = ImageChops.difference(before_rgb, after_rgb)
    values = np.asarray(difference, dtype=np.float32)
    magnitude = values.mean(axis=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    axes[0].imshow(before_rgb)
    axes[0].set_title("Before")
    axes[1].imshow(after_rgb)
    axes[1].set_title("After")
    axes[2].imshow(difference)
    axes[2].set_title("Visual pixel differences")
    for axis in axes:
        axis.set_axis_off()
    fig.suptitle(
        "Ordinary image comparison — not validated Earth-surface change"
    )
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return ComparisonResult(
        before=before_info,
        after=after_info,
        mean_absolute_difference=float(values.mean()),
        maximum_absolute_difference=float(values.max()),
        changed_pixel_count=int(np.count_nonzero(magnitude)),
        resized_for_demo=resized,
    )

