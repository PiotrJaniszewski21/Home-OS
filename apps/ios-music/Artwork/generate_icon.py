from pathlib import Path

from PIL import Image, ImageDraw


size = 1024
image = Image.new("RGB", (size, size))
pixels = image.load()
for y in range(size):
    for x in range(size):
        blend = (x + y) / (2 * (size - 1))
        pixels[x, y] = (
            int(255 - 42 * blend),
            int(45 - 15 * blend),
            int(92 + 70 * blend),
        )

draw = ImageDraw.Draw(image)
white = (255, 255, 255)
line_width = 56
draw.line([(218, 492), (512, 238), (806, 492)], fill=white, width=line_width, joint="curve")
draw.line([(274, 446), (274, 782), (750, 782), (750, 446)], fill=white, width=line_width, joint="curve")

draw.line([(486, 438), (486, 676)], fill=white, width=54)
draw.line([(486, 438), (656, 408)], fill=white, width=54)
draw.line([(656, 408), (656, 620)], fill=white, width=54)
draw.ellipse((372, 626, 496, 750), fill=white)
draw.ellipse((542, 570, 666, 694), fill=white)

output = Path(__file__).parents[1] / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon-1024.png"
output.parent.mkdir(parents=True, exist_ok=True)
image.save(output, format="PNG", optimize=True)
