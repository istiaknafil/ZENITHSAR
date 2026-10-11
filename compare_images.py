
from PIL import Image, ImageChops, ImageEnhance
import matplotlib.pyplot as plt
import numpy as np

# Open both images
before = Image.open("before.png").convert("RGB")
after = Image.open("after.png").convert("RGB")

# Ensure both images have the same dimensions
if before.size != after.size:
    after = after.resize(before.size)

# Calculate absolute pixel differences
difference = ImageChops.difference(before, after)

# Enhance the differences for visibility
difference = ImageEnhance.Contrast(difference).enhance(3)

# Calculate average pixel difference
values = np.array(difference, dtype=np.float32)
print("Image dimensions:", before.size)
print("Mean absolute pixel difference:",
      round(float(values.mean()), 2))

# Display comparison
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

axes[0].imshow(before)
axes[0].set_title("Before")

axes[1].imshow(after)
axes[1].set_title("After")

axes[2].imshow(difference)
axes[2].set_title("Visual pixel differences")

for ax in axes:
    ax.axis("off")

plt.tight_layout()
plt.savefig("comparison_preview.png", dpi=150)
plt.show()

print("Saved comparison_preview.png")
