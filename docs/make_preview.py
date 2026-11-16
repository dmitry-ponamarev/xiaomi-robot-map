"""Generate the README preview image (and a small HACS brand icon).

Both images are drawn from a synthetic but format-accurate RRMap, so the
preview always matches what the integration renders.

    python docs/make_preview.py
"""

from __future__ import annotations

import gzip
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.xiaomi_robot_map.const import ALL_LAYERS  # noqa: E402
from custom_components.xiaomi_robot_map.render import RenderOptions, render_map  # noqa: E402
from custom_components.xiaomi_robot_map.rr_map import parse_rrmap  # noqa: E402
from tests.fixtures.rrmap_builder import (  # noqa: E402
    BLOCK_CHARGER,
    BLOCK_CURRENTLY_CLEANED_ZONES,
    BLOCK_DIGEST,
    BLOCK_GOTO_PATH,
    BLOCK_MOP_PATH,
    BLOCK_NO_CARPET_AREAS,
    BLOCK_NO_GO_AREAS,
    BLOCK_PATH,
    BLOCK_ROBOT_POSITION,
    BLOCK_SUBMAP,
    BLOCK_VIRTUAL_WALLS,
    _block,
    _image_block,
    _obstacle_block,
    _path_block,
    _point_block,
    _rect_block,
)

WIDTH, HEIGHT = 232, 168
LEFT, TOP = 32, 24
MM = 50

# name, number, bounding box
ROOMS = [
    ("living", 1, (8, 8, 96, 76)),
    ("bedroom", 2, (102, 8, 224, 60)),
    ("kitchen", 3, (8, 82, 80, 160)),
    ("bath", 4, (86, 82, 140, 160)),
    ("hall", 5, (146, 66, 224, 160)),
]
def floor_plan() -> tuple[bytes, bytes]:
    """Return the pixel bitmap and the submap flags."""
    image = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)

    for _name, number, (x0, y0, x1, y1) in ROOMS:
        draw.rectangle([x0, y0, x1, y1], fill=0xFF)
        draw.rectangle([x0, y0, x1, y1], outline=1, width=2)
        draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], fill=(number << 3) | 0x07)

    # Interior walls between the lower rooms.
    draw.rectangle([81, 82, 85, 160], fill=1)
    draw.rectangle([141, 82, 145, 160], fill=1)
    draw.rectangle([97, 61, 101, 160], fill=1)
    draw.rectangle([8, 77, 224, 81], fill=1)

    # Doorways (floor coloured, belonging to the room on one side).
    draw.rectangle([52, 77, 64, 81], fill=(3 << 3) | 0x07)
    draw.rectangle([150, 77, 162, 81], fill=(5 << 3) | 0x07)
    draw.rectangle([98, 30, 102, 44], fill=(1 << 3) | 0x07)
    draw.rectangle([81, 110, 85, 124], fill=(4 << 3) | 0x07)

    # A bit of furniture: the sofa, a table and the kitchen counter.
    draw.rectangle([18, 20, 44, 30], fill=1)
    draw.rectangle([30, 46, 44, 56], fill=1)
    draw.rectangle([12, 96, 44, 106], fill=1)
    draw.rectangle([196, 12, 214, 26], fill=1)

    # Unreachable scan artefacts behind the bed.
    draw.rectangle([210, 40, 222, 58], fill=7)

    pixels = image.tobytes()

    submap = bytearray(WIDTH * HEIGHT)
    flags = Image.new("L", (WIDTH, HEIGHT), 0)
    flags_draw = ImageDraw.Draw(flags)
    flags_draw.rectangle([110, 12, 168, 40], fill=0x01)  # carpet in the bedroom
    flags_draw.rectangle([WIDTH - 4, 0, WIDTH - 1, HEIGHT - 1], fill=0x08)  # removed border
    submap[:] = flags.tobytes()
    return pixels, bytes(submap)


def build_demo_map() -> bytes:
    pixels, submap = floor_plan()

    # A boustrophedon path covering the rooms (coordinates are millimetres).
    path: list[tuple[int, int]] = []
    for index, y in enumerate(range(3000, 8600, 520)):
        xs = range(2400, 11800, 700) if index % 2 == 0 else range(11800, 2400, -700)
        path.extend((x, y) for x in xs)
    path.extend([(11800, 8600), (6200, 8600), (2600, 8600)])

    blocks = [
        _image_block(pixels, left=LEFT, top=TOP, width=WIDTH, height=HEIGHT),
        _point_block(BLOCK_CHARGER, 3200, 2600),
        _point_block(BLOCK_ROBOT_POSITION, 10600, 5000, 90),
        _path_block(BLOCK_PATH, path),
        _path_block(BLOCK_GOTO_PATH, [(10600, 5000), (11000, 7000), (10000, 8200)], angle=25),
        _block(BLOCK_MOP_PATH, bytes([1 if index % 3 == 0 else 0 for index in range(len(path))])),
        _rect_block(BLOCK_CURRENTLY_CLEANED_ZONES, [(2100, 5600, 4500, 9200)], 8),
        _rect_block(BLOCK_VIRTUAL_WALLS, [(12400, 3000, 12400, 8000)], 8),
        _rect_block(BLOCK_NO_GO_AREAS, [(2200, 5300, 4600, 5300, 4600, 6600, 2200, 6600)], 16),
        _rect_block(BLOCK_NO_CARPET_AREAS, [(7600, 3800, 10000, 3800, 10000, 5800, 7600, 5800)], 16),
        _obstacle_block(13, [(3000, 3000, 2), (11000, 6500, 0)]),
        _block(BLOCK_SUBMAP, submap),
        _block(BLOCK_DIGEST, b""),
    ]

    header = bytearray(28)
    header[0:2] = b"rr"
    struct.pack_into("<H", header, 0x02, len(header))
    struct.pack_into("<H", header, 0x08, 3)
    struct.pack_into("<H", header, 0x0A, 5)
    struct.pack_into("<I", header, 0x0C, 12)
    struct.pack_into("<I", header, 0x10, 481)
    return gzip.compress(bytes(header) + b"".join(blocks), mtime=0)


def make_preview() -> Path:
    map_data = parse_rrmap(build_demo_map())
    options = {
        "dark": RenderOptions(theme="dark", layers=frozenset(ALL_LAYERS), scale=2.0),
        "light": RenderOptions(theme="light", layers=frozenset(ALL_LAYERS), scale=2.0),
    }
    panels = {}
    labels = {"dark": "Dark theme", "light": "Light theme"}
    for theme, render_options in options.items():
        rendered = render_map(map_data, render_options)
        panel = rendered.image.convert("RGB")
        frame = Image.new("RGB", (panel.width, panel.height + 30), frame_color(theme))
        frame.paste(panel, (0, 30))
        font = ImageFont.load_default(size=15)
        ImageDraw.Draw(frame).text((12, 8), labels[theme], fill=text_color(theme), font=font)
        panels[theme] = frame

    gap = 12
    width = panels["dark"].width + panels["light"].width + gap
    height = max(panel.height for panel in panels.values())
    preview = Image.new("RGB", (width, height), frame_color("dark"))
    preview.paste(panels["dark"], (0, 0))
    preview.paste(panels["light"], (panels["dark"].width + gap, 0))

    target = ROOT / "docs" / "map_preview.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    preview.save(target)
    return target


def frame_color(theme: str) -> tuple[int, int, int]:
    return (10, 12, 16) if theme == "dark" else (238, 240, 244)


def text_color(theme: str) -> tuple[int, int, int]:
    return (190, 200, 215) if theme == "dark" else (60, 66, 78)


def make_icon() -> Path:
    size = 256
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=56, fill=(18, 21, 27, 255))

    # Stylised floor plan.
    accent = (86, 168, 255, 255)
    wall = (120, 132, 150, 255)
    draw.rounded_rectangle([40, 52, 216, 204], radius=10, outline=wall, width=6)
    draw.line([128, 52, 128, 132], fill=wall, width=5)
    draw.line([40, 132, 216, 132], fill=wall, width=5)
    draw.line([128, 132, 128, 204], fill=wall, width=5)

    # Cleaning path.
    points = [(62, 84), (104, 84), (104, 116), (62, 116), (62, 168), (104, 168)]
    draw.line(points, fill=accent, width=7, joint="curve")

    # Robot.
    draw.ellipse([144, 148, 196, 200], fill=(236, 241, 250, 255))
    draw.pieslice([144, 148, 196, 200], start=-60, end=60, fill=accent)
    draw.ellipse([163, 167, 177, 181], fill=(18, 21, 27, 255))

    target = ROOT / "custom_components" / "xiaomi_robot_map" / "brand" / "icon.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    icon.save(target)
    return target


if __name__ == "__main__":
    print(f"preview: {make_preview()}")
    print(f"icon: {make_icon()}")
