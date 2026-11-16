"""Convert a Valetudo map document into the internal :class:`RRMap` model.

Valetudo (https://valetudo.cloud) exposes the current map over HTTP as JSON::

    GET http://<robot>/api/v2/robot/state/map

Coordinates and sizes are integers in centimetres, the origin is the top-left
corner, and every layer stores its pixels run-length encoded in
``compressedPixels`` as triples ``(x_start, y, count)``.

The rest of the integration (renderer, sensors, Lovelace calibration) speaks the
RRMap model: millimetres, 50 mm per pixel, origin in the bottom-left corner, one
byte per pixel with ``0xFF`` for floor, ``1`` for wall and ``(room << 3) | 7``
for a room segment.  This module is the adapter between the two.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .rr_map import (
    MM_PER_PIXEL,
    Area,
    MapImage,
    Obstacle,
    Path,
    Point,
    RRMap,
    Wall,
    Zone,
    build_rooms,
    vacuum_room_at,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PIXEL_SIZE_CM = 5.0
# Valetudo measures angles clockwise from north, RRMap counter-clockwise from east.
NORTH_OFFSET_DEGREES = 90


class ValetudoMapError(Exception):
    """Raised when a document is not a usable ValetudoMap."""


def parse_valetudo_map(document: Any) -> RRMap:
    """Convert a ValetudoMap JSON document into an :class:`RRMap`."""
    if not isinstance(document, dict):
        raise ValetudoMapError("expected a JSON object with a Valetudo map")
    layers = document.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValetudoMapError("the document has no map layers")

    pixel_size = _pixel_size(document)
    entities = document.get("entities")
    if not isinstance(entities, list):
        entities = []

    walls: set[tuple[int, int]] = set()
    floor: dict[tuple[int, int], int] = {}
    carpets: set[tuple[int, int]] = set()
    segment_numbers: dict[str, int] = {}

    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layer_type = layer.get("type")
        meta = layer.get("metaData") or {}
        pixels = _layer_pixels(layer)
        if layer_type == "wall":
            walls.update(pixels)
        elif layer_type == "floor":
            for pixel in pixels:
                floor.setdefault(pixel, 0xFF)
        elif layer_type == "segment":
            segment_id = str(meta.get("segmentId") or "")
            number = segment_numbers.setdefault(segment_id, len(segment_numbers) + 1) if segment_id else 0
            material = str(meta.get("material") or "")
            for pixel in pixels:
                floor[pixel] = ((number << 3) | 0x07) if number else 0xFF
                if material.startswith("carpet"):
                    carpets.add(pixel)

    if not floor and not walls:
        raise ValetudoMapError("the map has no pixels")

    xs = [pixel[0] for pixel in floor] + [pixel[0] for pixel in walls]
    ys = [pixel[1] for pixel in floor] + [pixel[1] for pixel in walls]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x + 1
    height = max_y - min_y + 1
    if width <= 0 or height <= 0 or width * height > 4096 * 4096:
        raise ValetudoMapError(f"unrealistic map size: {width}x{height}")

    bitmap = bytearray(width * height)
    carpet_indices: set[int] = set()
    for (x, y), value in floor.items():
        index = _index(x, y, min_x, max_y, width)
        bitmap[index] = value
        if (x, y) in carpets:
            carpet_indices.add(index)
    for (x, y) in walls:
        bitmap[_index(x, y, min_x, max_y, width)] = 0x01

    image = MapImage(width=width, height=height, left=0, top=0, pixels=bytes(bitmap))
    result = RRMap(
        raw_size=0,
        header_length=0,
        major_version=0,
        minor_version=0,
        map_index=0,
        map_sequence=0,
        image=image,
        carpets=carpet_indices,
        is_complete=True,
    )

    frame = _Frame(pixel_size=pixel_size, min_x=min_x, max_y=max_y)
    _convert_entities(result, entities, frame)

    result.rooms = build_rooms(image)
    result.vacuum_room = vacuum_room_at(image, result.vacuum_position)
    return result


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class _Frame:
    """Converts Valetudo centimetre coordinates into local RRMap millimetres."""

    def __init__(self, *, pixel_size: float, min_x: int, max_y: int) -> None:
        self.pixel_size = pixel_size
        self.min_x = min_x
        self.max_y = max_y

    def xy(self, x_cm: float, y_cm: float) -> tuple[int, int]:
        px = x_cm / self.pixel_size - self.min_x
        py = self.max_y - y_cm / self.pixel_size
        return int(round(px * MM_PER_PIXEL)), int(round(py * MM_PER_PIXEL))


def _pixel_size(document: dict[str, Any]) -> float:
    raw = document.get("pixelSize")
    try:
        pixel_size = float(DEFAULT_PIXEL_SIZE_CM if raw is None else raw)
    except (TypeError, ValueError) as err:
        raise ValetudoMapError(f"invalid pixelSize: {err}") from err
    if not 0 < pixel_size <= 100:
        raise ValetudoMapError(f"invalid pixelSize: {pixel_size}")
    return pixel_size


def _layer_pixels(layer: dict[str, Any]) -> list[tuple[int, int]]:
    """Expand the run-length encoded ``compressedPixels`` of a layer."""
    compressed = layer.get("compressedPixels")
    if isinstance(compressed, list) and len(compressed) >= 3:
        pixels: list[tuple[int, int]] = []
        for offset in range(0, len(compressed) - 2, 3):
            try:
                x_start = int(compressed[offset])
                y = int(compressed[offset + 1])
                count = int(compressed[offset + 2])
            except (TypeError, ValueError):
                continue
            if count <= 0 or count > 100000:
                continue
            pixels.extend((x_start + step, y) for step in range(count))
        return pixels

    raw = layer.get("pixels")
    if isinstance(raw, list) and len(raw) >= 2:
        return [(int(raw[i]), int(raw[i + 1])) for i in range(0, len(raw) - 1, 2)]

    _LOGGER.debug("Skipping a Valetudo layer without pixels: %s", layer.get("type"))
    return []


def _index(x: int, y: int, min_x: int, max_y: int, width: int) -> int:
    """Index of a top-left-origin pixel in the bottom-left-origin bitmap."""
    return (x - min_x) + width * (max_y - y)


def _convert_entities(result: RRMap, entities: Iterable[Any], frame: _Frame) -> None:
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "")
        raw_points = entity.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            continue
        try:
            numbers = [float(value) for value in raw_points]
        except (TypeError, ValueError):
            continue

        if entity_type == "robot_position":
            angle = (entity.get("metaData") or {}).get("angle")
            x, y = frame.xy(numbers[0], numbers[1])
            result.vacuum_position = Point(x, y, _convert_angle(angle))
        elif entity_type == "charger_location":
            x, y = frame.xy(numbers[0], numbers[1])
            result.charger = Point(x, y)
        elif entity_type == "go_to_target":
            x, y = frame.xy(numbers[0], numbers[1])
            result.goto_target = Point(x, y)
        elif entity_type in ("path", "predicted_path"):
            path = _convert_path(numbers, frame)
            if entity_type == "path":
                result.path = path
            else:
                result.predicted_path = path
        elif entity_type in ("virtual_wall", "threshold", "curtain"):
            if len(numbers) >= 4:
                x0, y0 = frame.xy(numbers[0], numbers[1])
                x1, y1 = frame.xy(numbers[2], numbers[3])
                result.walls.append(Wall(x0, y0, x1, y1))
        elif entity_type in ("no_go_area", "no_mop_area"):
            area = _convert_area(numbers, frame)
            if entity_type == "no_go_area":
                result.no_go_areas.append(area)
            else:
                result.no_mopping_areas.append(area)
        elif entity_type == "active_zone":
            area = _convert_area(numbers, frame)
            xs = [point[0] for point in area.points]
            ys = [point[1] for point in area.points]
            result.zones.append(Zone(min(xs), min(ys), max(xs), max(ys)))
        elif entity_type == "obstacle":
            x, y = frame.xy(numbers[0], numbers[1])
            label = (entity.get("metaData") or {}).get("label")
            result.obstacles.append(Obstacle(x, y, description=str(label) if label else "obstacle"))


def _convert_path(numbers: list[float], frame: _Frame) -> Path:
    points = []
    for offset in range(0, len(numbers) - 1, 2):
        x, y = frame.xy(numbers[offset], numbers[offset + 1])
        points.append(Point(x, y))
    return Path([points] if points else [])


def _convert_area(numbers: list[float], frame: _Frame) -> Area:
    points = []
    for offset in range(0, len(numbers) - 1, 2):
        points.append(frame.xy(numbers[offset], numbers[offset + 1]))
    if len(points) < 3:
        raise ValetudoMapError("an area needs at least three points")
    return Area(tuple(points))


def _convert_angle(angle: Any) -> int | None:
    """Valetudo: clockwise degrees from north. RRMap: counter-clockwise from east."""
    if angle is None:
        return None
    try:
        value = float(angle)
    except (TypeError, ValueError):
        return None
    return int(round((NORTH_OFFSET_DEGREES - value) % 360)) % 256
