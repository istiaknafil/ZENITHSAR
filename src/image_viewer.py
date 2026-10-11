from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from PIL import Image

from .image_inspection import ImageInfo, inspect_image, make_preview


def save_image_preview(
    image_path: Path,
    output_path: Path,
    *,
    grayscale: bool = False,
    enhance_contrast: bool = False,
) -> ImageInfo:
    image, info = inspect_image(image_path)
    preview = make_preview(
        image, grayscale=grayscale, enhance_contrast=enhance_contrast
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path)
    return info


def save_inspection_figure(
    image_path: Path,
    output_path: Path,
    *,
    grayscale: bool = False,
    enhance_contrast: bool = False,
) -> ImageInfo:
    image, info = inspect_image(image_path)
    gray = make_preview(image, grayscale=True)
    processed = make_preview(
        image, grayscale=grayscale, enhance_contrast=enhance_contrast
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    axes[0].imshow(image)
    axes[0].set_title("Original image")
    axes[1].imshow(gray, cmap="gray")
    axes[1].set_title("Grayscale")
    axes[2].imshow(processed, cmap="gray" if processed.mode == "L" else None)
    axes[2].set_title("Processed preview")
    for axis in axes:
        axis.set_axis_off()
    fig.suptitle("Team Zenith image inspection")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return info

