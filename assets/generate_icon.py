"""Run once to create assets/airpods_icon.png (used by build.bat)."""
from PIL import Image, ImageDraw

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Simple earbuds silhouette — two circles with stems
for cx in (80, 176):
    draw.ellipse((cx - 36, 60, cx + 36, 132), fill=(255, 255, 255, 240))
    draw.ellipse((cx - 22, 76, cx + 22, 116), fill=(30, 30, 30, 255))
    draw.rounded_rectangle((cx - 12, 130, cx + 12, 190), radius=8,
                            fill=(255, 255, 255, 240))

img.save("airpods_icon.png")
print("Saved airpods_icon.png")
