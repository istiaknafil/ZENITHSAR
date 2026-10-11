
from PIL import Image, ImageOps
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# Load your image
image_path = Path("satellite.png")

if not image_path.exists():
    raise FileNotFoundError(
        f"Cannot find {image_path.resolve()}. "
        "Check the filename."
    )

img = Image.open(image_path).convert("RGB")

# Convert to grayscale
gray = ImageOps.grayscale(img)

# Enhance contrast for visual inspection
enhanced = ImageOps.autocontrast(gray)

# Convert pixels to numbers
pixels = np.array(gray)

print("Image dimensions:", img.size)
print("Pixel intensity range:", pixels.min(), "to", pixels.max())
print("Mean pixel intensity:", round(float(pixels.mean()), 2))

# Display three views
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

axes[0].imshow(img)
axes[0].set_title("Original image")

axes[1].imshow(gray, cmap="gray", vmin=0, vmax=255)
axes[1].set_title("Grayscale")

axes[2].imshow(enhanced, cmap="gray", vmin=0, vmax=255)
axes[2].set_title("Contrast enhanced")

for ax in axes:
    ax.axis("off")

plt.tight_layout()
plt.savefig("image_inspection.png", dpi=150)
plt.show()

print("Saved: image_inspection.png")
