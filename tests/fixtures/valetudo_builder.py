"""Build a ValetudoMap JSON document for tests and the previews."""

from __future__ import annotations

from collections.abc import Iterable

PIXEL_SIZE_CM = 5
FLOOR_X = range(1, 21)
FLOOR_Y = range(1, 11)
WALL_X = range(0, 22)
WALL_Y = range(0, 12)


def compress(pixels: Iterable[tuple[int, int]]) -> list[int]:
    """Run-length encode pixels the way Valetudo does: (x_start, y, count)."""
    blocks: list[int] = []
    current_x: int | None = None
    current_y: int | None = None
    count = 0
    for x, y in sorted(set(pixels), key=lambda pixel: (pixel[1], pixel[0])):
        if current_y == y and current_x is not None and x == current_x + count:
            count += 1
        else:
            if current_x is not None:
                blocks.extend((current_x, current_y, count))
            current_x, current_y, count = x, y, 1
    if current_x is not None:
        blocks.extend((current_x, current_y, count))
    return blocks


def _ring() -> list[tuple[int, int]]:
    ring = []
    for x in WALL_X:
        ring.append((x, 0))
        ring.append((x, 11))
    for y in WALL_Y:
        ring.append((0, y))
        ring.append((21, y))
    return ring


def build_valetudo_map() -> dict:
    """A two room flat, 21x11 pixels at 5 cm per pixel."""
    segment_one = [(x, y) for x in range(1, 11) for y in FLOOR_Y]
    segment_two = [(x, y) for x in range(11, 21) for y in FLOOR_Y]
    floor = [(x, y) for x in FLOOR_X for y in FLOOR_Y]

    return {
        "__class": "ValetudoMap",
        "metaData": {"version": 2, "nonce": "test"},
        "size": {"x": 110, "y": 60},
        "pixelSize": PIXEL_SIZE_CM,
        "layers": [
            {"__class": "MapLayer", "type": "floor", "pixelCount": len(floor), "compressedPixels": compress(floor)},
            {"__class": "MapLayer", "type": "wall", "pixelCount": len(_ring()), "compressedPixels": compress(_ring())},
            {
                "__class": "MapLayer",
                "type": "segment",
                "pixelCount": len(segment_one),
                "compressedPixels": compress(segment_one),
                "metaData": {"segmentId": "1", "name": "Living room", "material": "wood"},
            },
            {
                "__class": "MapLayer",
                "type": "segment",
                "pixelCount": len(segment_two),
                "compressedPixels": compress(segment_two),
                "metaData": {"segmentId": "2", "name": "Bedroom", "material": "carpet"},
            },
        ],
        "entities": [
            {
                "__class": "PointMapEntity",
                "type": "robot_position",
                "points": [25, 25],
                "metaData": {"angle": 90},
            },
            {"__class": "PointMapEntity", "type": "charger_location", "points": [95, 15], "metaData": {}},
            {"__class": "PathMapEntity", "type": "path", "points": [15, 15, 25, 25, 35, 25, 45, 35]},
            {"__class": "PathMapEntity", "type": "predicted_path", "points": [45, 35, 55, 45]},
            {"__class": "LineMapEntity", "type": "virtual_wall", "points": [100, 10, 100, 50]},
            {
                "__class": "PolygonMapEntity",
                "type": "no_go_area",
                "points": [10, 40, 50, 40, 50, 45, 10, 45],
            },
            {
                "__class": "PolygonMapEntity",
                "type": "active_zone",
                "points": [60, 20, 90, 20, 90, 40, 60, 40],
            },
        ],
    }
