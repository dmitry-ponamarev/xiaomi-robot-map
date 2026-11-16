"""Build synthetic but format-accurate RRMap blobs for tests.

The generator mirrors the layout produced by Roborock firmware / the Xiaomi
cloud, so the parser and the renderer can be exercised without a real vacuum.

Run as a script to write a sample map file::

    python tests/fixtures/rrmap_builder.py out/map_data.gz
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

# Same constants as the parser, duplicated on purpose so the fixture builder
# does not depend on the code under test.
BLOCK_CHARGER = 1
BLOCK_IMAGE = 2
BLOCK_PATH = 3
BLOCK_GOTO_PATH = 4
BLOCK_GOTO_PREDICTED_PATH = 5
BLOCK_CURRENTLY_CLEANED_ZONES = 6
BLOCK_GOTO_TARGET = 7
BLOCK_ROBOT_POSITION = 8
BLOCK_NO_GO_AREAS = 9
BLOCK_VIRTUAL_WALLS = 10
BLOCK_SUBMAP = 17
BLOCK_MOP_PATH = 18
BLOCK_NO_CARPET_AREAS = 19
BLOCK_OBSTACLES = 13
BLOCK_DIGEST = 1024

WIDTH = 128
HEIGHT = 96
LEFT = 24  # map offset of the bitmap, in pixels
TOP = 16
MM_PER_PIXEL = 50

ROOM_A = 1
ROOM_B = 2
ROOM_C = 3


def _block(block_type: int, data: bytes, extra_header: bytes = b"") -> bytes:
    header_length = 8 + len(extra_header)
    return struct.pack("<HHI", block_type, header_length, len(data)) + extra_header + data


def _image_block(pixels: bytes, left: int = LEFT, top: int = TOP, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    return _block(
        BLOCK_IMAGE,
        pixels,
        struct.pack("<IIII", top, left, height, width),
    )


def _point_block(block_type: int, x: int, y: int, angle: int | None = None) -> bytes:
    if angle is None:
        return _block(block_type, struct.pack("<II", x, y))
    return _block(block_type, struct.pack("<IIi", x, y, angle))


def _path_block(block_type: int, points: list[tuple[int, int]], angle: int = 0) -> bytes:
    data = b"".join(struct.pack("<HH", x, y) for x, y in points)
    extra = struct.pack("<III", len(points), 10, angle)
    return _block(block_type, data, extra)


def _rect_block(block_type: int, rects: list[tuple[int, ...]], size: int) -> bytes:
    data = b"".join(struct.pack("<" + "H" * (size // 2), *rect) for rect in rects)
    return _block(block_type, data, struct.pack("<H", len(rects)) + b"\x00\x00")


def _obstacle_block(block_type: int, obstacles: list[tuple[int, int, int]]) -> bytes:
    size = 6
    data = b"".join(struct.pack("<HHH", *obstacle) for obstacle in obstacles)
    return _block(block_type, data, struct.pack("<HH", len(obstacles), size * 2))


def make_pixels() -> bytearray:
    """A three-room floor plan with walls, a carpet and a removed area."""
    pixels = bytearray(WIDTH * HEIGHT)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            pixels[x + WIDTH * y] = 0  # outside

    def rect(x0: int, y0: int, x1: int, y1: int, value: int) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                pixels[x + WIDTH * y] = value

    # Outer shell (walls) and floor.
    rect(6, 6, 121, 89, 1)
    rect(7, 7, 120, 88, 0xFF)

    # Room segments (pixel value = (number << 3) | 0x07).
    rect(8, 8, 52, 48, (ROOM_A << 3) | 0x07)   # living room
    rect(55, 8, 119, 48, (ROOM_B << 3) | 0x07)  # bedroom
    rect(8, 52, 119, 87, (ROOM_C << 3) | 0x07)  # kitchen / hallway

    # Interior walls between the segments.
    rect(53, 8, 54, 48, 1)
    rect(8, 49, 119, 51, 1)

    # A doorway and furniture-ish obstacles.
    rect(53, 20, 54, 26, 0xFF)
    rect(20, 30, 26, 34, 0)
    rect(90, 60, 96, 66, 1)

    # Scan artefacts in a corner (value 7).
    rect(112, 80, 118, 86, 7)
    return pixels


def make_submap() -> bytes:
    """Carpet in the bedroom, removed border cells along the right edge."""
    data = bytearray(WIDTH * HEIGHT)
    for y in range(12, 30):
        for x in range(70, 100):
            data[x + WIDTH * y] |= 0x01
    for y in range(HEIGHT):
        for x in range(WIDTH - 3, WIDTH):
            data[x + WIDTH * y] |= 0x08
    return bytes(data)


def build_map(
    *,
    vacuum_x: int = 2600,
    vacuum_y: int = 1500,
    vacuum_angle: int = 45,
    charger: tuple[int, int] = (2000, 1500),
    compress: bool = True,
) -> bytes:
    """Build a full map blob (gzip compressed by default)."""
    pixels = bytes(make_pixels())

    blocks = [
        _image_block(pixels),
        _point_block(BLOCK_CHARGER, *charger),
        _point_block(BLOCK_ROBOT_POSITION, vacuum_x, vacuum_y, vacuum_angle),
        _path_block(
            BLOCK_PATH,
            [(900, 900), (1100, 1000), (1300, 1150), (1500, 1200), (1700, 1400), (1900, 1600)],
        ),
        _path_block(BLOCK_GOTO_PATH, [(1500, 1200), (2000, 1600), (2400, 1900)], angle=30),
        _path_block(BLOCK_GOTO_PREDICTED_PATH, [(1500, 1200), (2100, 1500), (2600, 1800)]),
        _block(BLOCK_MOP_PATH, bytes([1, 1, 1, 1, 1, 1])),
        _block(BLOCK_GOTO_TARGET, struct.pack("<HH", 2600, 1800)),
        _rect_block(BLOCK_CURRENTLY_CLEANED_ZONES, [(1000, 1000, 2600, 2000)], 8),
        _rect_block(BLOCK_VIRTUAL_WALLS, [(800, 400, 800, 3000)], 8),
        _rect_block(BLOCK_NO_GO_AREAS, [(3000, 500, 3400, 500, 3400, 900, 3000, 900)], 16),
        _rect_block(BLOCK_NO_CARPET_AREAS, [(400, 2000, 900, 2000, 900, 2400, 400, 2400)], 16),
        _obstacle_block(BLOCK_OBSTACLES, [(2200, 800, 0), (2500, 1200, 5)]),
        _block(BLOCK_SUBMAP, make_submap()),
        _block(BLOCK_DIGEST, b""),
    ]

    header = bytearray(28)
    header[0:2] = b"rr"
    struct.pack_into("<H", header, 0x02, len(header))
    struct.pack_into("<H", header, 0x04, 1)
    struct.pack_into("<H", header, 0x08, 3)  # major version
    struct.pack_into("<H", header, 0x0A, 5)  # minor version
    struct.pack_into("<I", header, 0x0C, 7)  # map index
    struct.pack_into("<I", header, 0x10, 42)  # map sequence

    raw = bytes(header) + b"".join(blocks)
    return gzip.compress(raw, mtime=0) if compress else raw


def main() -> None:
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "map_data.gz")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_map())
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
