"""Parser for Roborock / Xiaomi "RRMap" vacuum map files.

The cloud returns a gzip compressed binary blob.  The format is a small
header followed by a chain of typed blocks::

    header  : magic(2) length(2) version(2) major(2) minor(2) index(4) sequence(4)
    block   : type(2) header_length(2) data_length(4) <type specific header> <data>

Map coordinates are millimetres, the bitmap uses 50 mm per pixel and its
origin is the bottom left corner of the image block.

This module has no Home Assistant imports so it can be used (and tested)
outside of Home Assistant, e.g. by ``tools/render_map.py``.
"""

from __future__ import annotations

import gzip
import logging
import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

MM_PER_PIXEL = 50.0

# Block types.
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
BLOCK_BLOCKS = 11
BLOCK_NO_MOPPING_AREAS = 12
BLOCK_OBSTACLES = 13
BLOCK_IGNORED_OBSTACLES = 14
BLOCK_OBSTACLES_WITH_PHOTO = 15
BLOCK_IGNORED_OBSTACLES_WITH_PHOTO = 16
BLOCK_SUBMAP = 17
BLOCK_MOP_PATH = 18
BLOCK_NO_CARPET_AREAS = 19
BLOCK_DIGEST = 1024

# Bit flags of the SUBMAP block.
SUBMAP_CARPET = 0x01
SUBMAP_REMOVED_BORDER = 0x08
SUBMAP_REMOVED_AREA = 0x10

# Pixel values of the bitmap.
PIXEL_OUTSIDE = 0x00
PIXEL_WALL = 0x01
PIXEL_SCAN = 0x07
PIXEL_FLOOR = 0xFF

# Obstacle types reported by newer firmwares.
OBSTACLE_TYPES: dict[int, str] = {
    0: "cable",
    1: "pet waste",
    2: "shoes",
    3: "poop",
    4: "pedestal",
    5: "extension cord",
    9: "weighting scale",
    10: "clothes",
    25: "dustpan",
    26: "furniture with a crossbar",
    27: "furniture with a crossbar",
    34: "clothes",
    48: "cable",
    49: "pet",
    50: "pet",
    51: "fabric/paper balls",
}


class RRMapError(Exception):
    """Base error for map parsing."""


class RRMapFormatError(RRMapError):
    """Raised when the blob is not a valid RRMap file."""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Point:
    """A point in map coordinates (millimetres)."""

    x: int
    y: int
    angle: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"x": self.x, "y": self.y}
        if self.angle is not None:
            data["angle"] = self.angle
        return data


@dataclass(slots=True)
class Wall:
    """A virtual wall segment."""

    x0: int
    y0: int
    x1: int
    y1: int

    def as_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass(slots=True)
class Zone:
    """An axis aligned rectangle (currently cleaned zone)."""

    x0: int
    y0: int
    x1: int
    y1: int

    def as_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass(slots=True)
class Area:
    """A quadrilateral area (no-go / no-mopping / no-carpet zone)."""

    points: tuple[tuple[int, int], ...]

    def as_dict(self) -> dict[str, int]:
        data: dict[str, int] = {}
        for index, (x, y) in enumerate(self.points):
            data[f"x{index}"] = x
            data[f"y{index}"] = y
        return data


@dataclass(slots=True)
class Obstacle:
    """An obstacle detected by the robot."""

    x: int
    y: int
    type: int | None = None
    description: str | None = None
    confidence: float | None = None
    photo_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"x": self.x, "y": self.y}
        if self.type is not None:
            data["type"] = self.type
        if self.description is not None:
            data["description"] = self.description
        if self.confidence is not None:
            data["confidence"] = round(self.confidence, 2)
        if self.photo_name is not None:
            data["photo_name"] = self.photo_name
        return data


@dataclass(slots=True)
class Path:
    """A cleaned path, split into continuous chunks."""

    chunks: list[list[Point]] = field(default_factory=list)

    @property
    def points_count(self) -> int:
        return sum(len(chunk) for chunk in self.chunks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "points_count": self.points_count,
            "chunks": [[p.as_dict() for p in chunk] for chunk in self.chunks],
        }


@dataclass(slots=True)
class MapImage:
    """The map bitmap with its placement in map coordinates."""

    width: int
    height: int
    left: int
    top: int
    # One byte per pixel, row 0 is the *bottom* row of the image.
    pixels: bytes

    def pixel_at_map_position(self, x: int, y: int) -> int | None:
        px = round(x / MM_PER_PIXEL) - self.left
        py = round(y / MM_PER_PIXEL) - self.top
        if 0 <= px < self.width and 0 <= py < self.height:
            return self.pixels[px + self.width * py]
        return None


@dataclass(slots=True)
class Room:
    """A room segment, bounding box in map coordinates (millimetres)."""

    number: int
    x0: int
    y0: int
    x1: int
    y1: int

    def as_dict(self) -> dict[str, int]:
        return {
            "number": self.number,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
        }


@dataclass(slots=True)
class RRMap:
    """A parsed vacuum map."""

    raw_size: int = 0
    header_length: int = 0
    major_version: int = 0
    minor_version: int = 0
    map_index: int = 0
    map_sequence: int = 0
    image: MapImage | None = None
    charger: Point | None = None
    vacuum_position: Point | None = None
    goto_target: Point | None = None
    path: Path | None = None
    mop_path: Path | None = None
    goto_path: Path | None = None
    predicted_path: Path | None = None
    walls: list[Wall] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    no_go_areas: list[Area] = field(default_factory=list)
    no_mopping_areas: list[Area] = field(default_factory=list)
    no_carpet_areas: list[Area] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    ignored_obstacles: list[Obstacle] = field(default_factory=list)
    obstacles_with_photo: list[Obstacle] = field(default_factory=list)
    ignored_obstacles_with_photo: list[Obstacle] = field(default_factory=list)
    carpets: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)
    rooms: dict[int, Room] = field(default_factory=dict)
    vacuum_room: int | None = None
    is_complete: bool = False
    unknown_blocks: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Return True when the map has no usable bitmap."""
        return self.image is None or self.image.width <= 0 or self.image.height <= 0

    def as_dict(self, include_paths: bool = True) -> dict[str, Any]:
        """Serialise to a JSON friendly dict (used for entity attributes)."""
        data: dict[str, Any] = {
            "map_index": self.map_index,
            "map_sequence": self.map_sequence,
            "major_version": self.major_version,
            "minor_version": self.minor_version,
            "is_complete": self.is_complete,
            "raw_size": self.raw_size,
            "rooms": {str(number): room.as_dict() for number, room in sorted(self.rooms.items())},
            "vacuum_room": self.vacuum_room,
            "charger": self.charger.as_dict() if self.charger else None,
            "vacuum_position": self.vacuum_position.as_dict() if self.vacuum_position else None,
            "goto_target": self.goto_target.as_dict() if self.goto_target else None,
            "walls": [wall.as_dict() for wall in self.walls],
            "zones": [zone.as_dict() for zone in self.zones],
            "no_go_areas": [area.as_dict() for area in self.no_go_areas],
            "no_mopping_areas": [area.as_dict() for area in self.no_mopping_areas],
            "no_carpet_areas": [area.as_dict() for area in self.no_carpet_areas],
            "obstacles": [obstacle.as_dict() for obstacle in self.obstacles],
        }
        if self.image is not None:
            data["image"] = {
                "width": self.image.width,
                "height": self.image.height,
                "left": self.image.left,
                "top": self.image.top,
                "size": self.image.width * self.image.height,
            }
        if include_paths:
            data["path"] = self.path.as_dict() if self.path else None
        else:
            data["path_points"] = self.path.points_count if self.path else 0
        return data


# --------------------------------------------------------------------------
# Low level helpers
# --------------------------------------------------------------------------


def _u8(data: bytes, offset: int) -> int:
    return data[offset] & 0xFF


def _u16(data: bytes, offset: int) -> int:
    return (data[offset] & 0xFF) | ((data[offset + 1] & 0xFF) << 8)


def _u32(data: bytes, offset: int) -> int:
    return (
        (data[offset] & 0xFF)
        | ((data[offset + 1] & 0xFF) << 8)
        | ((data[offset + 2] & 0xFF) << 16)
        | ((data[offset + 3] & 0xFF) << 24)
    )


def decompress(raw: bytes) -> bytes:
    """Return the uncompressed map, accepting gzip, zlib or plain blobs."""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        try:
            return gzip.decompress(raw)
        except OSError as err:
            raise RRMapFormatError(f"invalid gzip payload: {err}") from err
    if len(raw) >= 2 and raw[0] == 0x78 and raw[1] in (0x01, 0x9C, 0xDA):
        try:
            return zlib.decompress(raw)
        except zlib.error as err:
            raise RRMapFormatError(f"invalid zlib payload: {err}") from err
    return raw


# --------------------------------------------------------------------------
# Block parsers
# --------------------------------------------------------------------------


def _parse_point(data: bytes, data_length: int) -> Point:
    angle = None
    if data_length > 8:
        raw_angle = _u32(data, 0x08)
        if raw_angle > 0xFF:
            raw_angle = (raw_angle & 0xFF) - 256
        angle = raw_angle
    return Point(_u32(data, 0x00), _u32(data, 0x04), angle)


def _parse_path(raw: bytes, block_start: int, header: bytes) -> Path:
    data_length = _u32(header, 0x04)
    start = block_start + len(header)
    points: list[Point] = []
    for offset in range(start, start + data_length - 3, 4):
        points.append(Point(_u16(raw, offset), _u16(raw, offset + 2)))
    return Path([points] if points else [])


def _parse_rects(header: bytes, data: bytes, size: int, count_offset: int = 0x08) -> list[tuple[int, ...]]:
    count = _u16(header, count_offset)
    rects = []
    for offset in range(0, count * size, size):
        if offset + size > len(data):
            break
        rects.append(tuple(_u16(data, offset + i * 2) for i in range(size // 2)))
    return rects


def _parse_areas(header: bytes, data: bytes) -> list[Area]:
    areas = []
    for rect in _parse_rects(header, data, 16):
        # A quadrilateral is stored as x0 y0 x1 y1 x2 y2 x3 y3.
        areas.append(Area(tuple((rect[i], rect[i + 1]) for i in range(0, 8, 2))))
    return areas


def _parse_walls(header: bytes, data: bytes) -> list[Wall]:
    return [Wall(*rect) for rect in _parse_rects(header, data, 8)]


def _parse_zones(header: bytes, data: bytes) -> list[Zone]:
    return [Zone(*rect) for rect in _parse_rects(header, data, 8)]


def _parse_obstacles(header: bytes, data: bytes) -> list[Obstacle]:
    count = _u16(header, 0x08)
    if count == 0 or not data:
        return []
    size = len(data) // count
    obstacles: list[Obstacle] = []
    if size < 6:
        return obstacles
    for offset in range(0, count * size, size):
        if offset + 4 > len(data):
            break
        obstacle = Obstacle(_u16(data, offset), _u16(data, offset + 2))
        if size >= 6:
            obstacle.type = _u16(data, offset + 4)
            obstacle.description = OBSTACLE_TYPES.get(obstacle.type)
        if size >= 10:
            u1 = _u16(data, offset + 6)
            u2 = _u16(data, offset + 8)
            obstacle.confidence = 0.0 if u2 == 0 else u1 * 10.0 / u2
        if size == 28 and _u8(data, offset + 12) > 0:
            photo = data[offset + 12 : offset + 28]
            obstacle.photo_name = photo.split(b"\x00", 1)[0].decode("ascii", "replace")
        obstacles.append(obstacle)
    return obstacles


def _parse_goto_target(data: bytes) -> Point:
    return Point(_u16(data, 0x00), _u16(data, 0x02))


def _parse_mop_path(path: Path | None, mask: bytes) -> Path | None:
    if path is None:
        return None
    chunks: list[list[Point]] = []
    for chunk in path.chunks:
        current: list[Point] = []
        for index, point in enumerate(chunk):
            if index < len(mask) and mask[index]:
                current.append(point)
            elif current:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)
    return Path(chunks)


def _parse_submap(data: bytes) -> tuple[set[int], set[int]]:
    carpets: set[int] = set()
    removed: set[int] = set()
    for index, value in enumerate(data):
        if value & SUBMAP_CARPET:
            carpets.add(index)
        if value & (SUBMAP_REMOVED_BORDER | SUBMAP_REMOVED_AREA):
            removed.add(index)
    return carpets, removed


def build_rooms(image: MapImage) -> dict[int, Room]:
    """Collect a bounding box per room segment from the bitmap."""
    boxes: dict[int, list[int]] = {}
    pixels = image.pixels
    width = image.width
    for index, value in enumerate(pixels):
        if value in (PIXEL_FLOOR, PIXEL_SCAN, PIXEL_OUTSIDE, PIXEL_WALL):
            continue
        if value & 0x07 != 0x07:
            continue
        number = value >> 3
        px = index % width
        py = index // width
        box = boxes.get(number)
        if box is None:
            boxes[number] = [px, py, px, py]
        else:
            box[0] = min(box[0], px)
            box[1] = min(box[1], py)
            box[2] = max(box[2], px)
            box[3] = max(box[3], py)
    return {
        number: Room(
            number=number,
            x0=int((image.left + box[0]) * MM_PER_PIXEL),
            y0=int((image.top + box[1]) * MM_PER_PIXEL),
            x1=int((image.left + box[2]) * MM_PER_PIXEL),
            y1=int((image.top + box[3]) * MM_PER_PIXEL),
        )
        for number, box in boxes.items()
    }


def vacuum_room_at(image: MapImage, position: Point | None) -> int | None:
    if position is None:
        return None
    pixel = image.pixel_at_map_position(position.x, position.y)
    if pixel is None or pixel in (PIXEL_FLOOR, PIXEL_SCAN, PIXEL_OUTSIDE, PIXEL_WALL):
        return None
    if pixel & 0x07 == 0x07:
        return pixel >> 3
    return None


# --------------------------------------------------------------------------
# Main entry points
# --------------------------------------------------------------------------


def parse_rrmap(raw: bytes) -> RRMap:
    """Parse a (optionally compressed) RRMap blob into :class:`RRMap`."""
    data = decompress(raw)
    if len(data) < 20:
        raise RRMapFormatError(f"map data is too short ({len(data)} bytes)")

    # firmware versions ship headers of 20, 24 or 28 bytes; the reference parser
    # simply trusts the length stored in the blob, so do the same
    header_length = _u16(data, 0x02)
    if header_length < 12 or header_length > len(data):
        raise RRMapFormatError(f"unexpected map header length: {header_length}")

    result = RRMap(
        raw_size=len(raw),
        header_length=header_length,
        major_version=_u16(data, 0x08),
        minor_version=_u16(data, 0x0A),
        map_index=_u32(data, 0x0C),
        map_sequence=_u32(data, 0x10),
    )

    position = header_length
    image_block: tuple[bytes, bytes, int] | None = None
    path: Path | None = None

    while position + 8 <= len(data):
        block_type = _u16(data, position)
        block_header_length = _u16(data, position + 2)
        block_data_length = _u32(data, position + 4)
        if block_header_length < 8 or position + block_header_length > len(data):
            _LOGGER.debug("Stopping map parsing: bad block header at %s", position)
            break
        header = data[position : position + block_header_length]
        data_start = position + block_header_length
        block = data[data_start : data_start + block_data_length]
        if len(block) < block_data_length:
            _LOGGER.debug(
                "Block %s at %s is truncated (%s of %s bytes)",
                block_type,
                position,
                len(block),
                block_data_length,
            )

        try:
            _parse_block(result, block_type, header, block, position, data, path)
            if block_type == BLOCK_PATH:
                path = result.path
            elif block_type == BLOCK_IMAGE:
                image_block = (header, block, block_data_length)
        except (IndexError, ValueError, struct.error) as err:  # pragma: no cover - defensive
            _LOGGER.warning("Unable to parse map block %s: %s", block_type, err)

        position = data_start + block_data_length

    if image_block is not None:
        header, block, block_data_length = image_block
        _apply_image(result, header, block, block_data_length)

    if result.image is not None:
        result.rooms = build_rooms(result.image)
        result.vacuum_room = vacuum_room_at(result.image, result.vacuum_position)

    return result


def _parse_block(
    result: RRMap,
    block_type: int,
    header: bytes,
    data: bytes,
    block_start: int,
    raw: bytes,
    path: Path | None,
) -> None:
    if block_type == BLOCK_CHARGER:
        result.charger = _parse_point(data, len(data))
    elif block_type == BLOCK_ROBOT_POSITION:
        result.vacuum_position = _parse_point(data, len(data))
    elif block_type == BLOCK_PATH:
        result.path = _parse_path(raw, block_start, header)
    elif block_type == BLOCK_GOTO_PATH:
        result.goto_path = _parse_path(raw, block_start, header)
    elif block_type == BLOCK_GOTO_PREDICTED_PATH:
        result.predicted_path = _parse_path(raw, block_start, header)
    elif block_type == BLOCK_MOP_PATH:
        result.mop_path = _parse_mop_path(path, data)
    elif block_type == BLOCK_GOTO_TARGET:
        result.goto_target = _parse_goto_target(data)
    elif block_type == BLOCK_CURRENTLY_CLEANED_ZONES:
        result.zones = _parse_zones(header, data)
    elif block_type == BLOCK_VIRTUAL_WALLS:
        result.walls = _parse_walls(header, data)
    elif block_type == BLOCK_NO_GO_AREAS:
        result.no_go_areas = _parse_areas(header, data)
    elif block_type == BLOCK_NO_MOPPING_AREAS:
        result.no_mopping_areas = _parse_areas(header, data)
    elif block_type == BLOCK_NO_CARPET_AREAS:
        result.no_carpet_areas = _parse_areas(header, data)
    elif block_type == BLOCK_OBSTACLES:
        result.obstacles = _parse_obstacles(header, data)
    elif block_type == BLOCK_IGNORED_OBSTACLES:
        result.ignored_obstacles = _parse_obstacles(header, data)
    elif block_type == BLOCK_OBSTACLES_WITH_PHOTO:
        result.obstacles_with_photo = _parse_obstacles(header, data)
    elif block_type == BLOCK_IGNORED_OBSTACLES_WITH_PHOTO:
        result.ignored_obstacles_with_photo = _parse_obstacles(header, data)
    elif block_type == BLOCK_SUBMAP:
        result.carpets, result.removed = _parse_submap(data)
    elif block_type == BLOCK_DIGEST:
        result.is_complete = True
    elif block_type in (BLOCK_IMAGE, BLOCK_BLOCKS):
        # Parsed after the block loop (needs all other data).
        return
    else:
        result.unknown_blocks.append(block_type)
        _LOGGER.debug(
            "Unknown map block %s (header %s bytes, data %s bytes)",
            block_type,
            len(header),
            len(data),
        )


def _apply_image(result: RRMap, header: bytes, data: bytes, data_length: int) -> None:
    header_length = len(header)
    top = _u32(header, header_length - 16)
    left = _u32(header, header_length - 12)
    height = _u32(header, header_length - 8)
    width = _u32(header, header_length - 4)
    if width == 0 or height == 0:
        _LOGGER.debug("Map image is empty (%sx%s)", width, height)
        return
    expected = width * height
    if data_length != expected:
        _LOGGER.warning(
            "Map image size mismatch: %sx%s (%s pixels) but %s bytes of data",
            width,
            height,
            expected,
            data_length,
        )
        if data_length < expected:
            data = data + bytes(expected - data_length)
    result.image = MapImage(width=width, height=height, left=left, top=top, pixels=bytes(data[:expected]))


def iter_points(*paths: Path | None) -> Iterable[Point]:
    """Yield all points of the given paths."""
    for path in paths:
        if path is None:
            continue
        for chunk in path.chunks:
            yield from chunk
