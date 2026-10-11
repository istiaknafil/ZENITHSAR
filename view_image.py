
from PIL import Image
import matplotlib.pyplot as plt
from pathlib import Path

# Image filename inside the TeamZenith folder
image_path = Path("satellite.png")

# Check whether the file exists
if not image_path.exists():
    raise FileNotFoundError(
        f"Image not found: {image_path.resolve()}\n"
        "Check the filename and folder."
    )

# Open the image
img = Image.open(image_path)

print("Image loaded successfully!")
print("File:", image_path.name)
print("Format:", img.format)
print("Dimensions (width, height):", img.size)
print("Color mode:", img.mode)

# Display the image
plt.figure(figsize=(10, 7))
plt.imshow(img)
plt.title("Team Zenith - Uploaded Satellite Image")
plt.axis("off")
plt.tight_layout()

# Save a preview in the same folder
plt.savefig("satellite_preview.png", dpi=150, bbox_inches="tight")
plt.show()

print("Preview saved as satellite_preview.png")
