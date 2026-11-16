"""Unit tests for the RRMap parser."""

from __future__ import annotations

import gzip

import pytest

from custom_components.xiaomi_robot_map.rr_map import (
    MM_PER_PIXEL,
    RRMapFormatError,
    decompress,
    parse_rrmap,
)
from tests.fixtures.rrmap_builder import HEIGHT, LEFT, TOP, WIDTH, build_map


@pytest.fixture
def raw_map() -> bytes:
    return build_map()


def test_header_and_image(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert map_data.header_length == 28
    assert map_data.major_version == 3
    assert map_data.minor_version == 5
    assert map_data.map_index == 7
    assert map_data.map_sequence == 42
    assert map_data.is_complete is True

    assert map_data.image is not None
    assert map_data.image.width == WIDTH
    assert map_data.image.height == HEIGHT
    assert map_data.image.left == LEFT
    assert map_data.image.top == TOP
    assert len(map_data.image.pixels) == WIDTH * HEIGHT


def test_positions(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert map_data.charger is not None
    assert (map_data.charger.x, map_data.charger.y) == (2000, 1500)
    assert map_data.vacuum_position is not None
    assert (map_data.vacuum_position.x, map_data.vacuum_position.y) == (2600, 1500)
    assert map_data.vacuum_position.angle == 45
    assert (map_data.goto_target.x, map_data.goto_target.y) == (2600, 1800)


def test_paths(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert map_data.path is not None
    assert map_data.path.points_count == 6
    assert [(p.x, p.y) for p in map_data.path.chunks[0][:2]] == [(900, 900), (1100, 1000)]
    assert map_data.goto_path is not None
    assert map_data.goto_path.points_count == 3
    assert map_data.predicted_path is not None
    assert map_data.predicted_path.points_count == 3
    # Mop path is a subset of the path, selected by the mask.
    assert map_data.mop_path is not None
    assert map_data.mop_path.points_count == 6


def test_walls_zones_and_areas(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert [(w.x0, w.y0, w.x1, w.y1) for w in map_data.walls] == [(800, 400, 800, 3000)]
    assert [(z.x0, z.y0, z.x1, z.y1) for z in map_data.zones] == [(1000, 1000, 2600, 2000)]
    assert map_data.no_go_areas[0].points == (
        (3000, 500),
        (3400, 500),
        (3400, 900),
        (3000, 900),
    )
    assert len(map_data.no_carpet_areas) == 1


def test_obstacles(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert [(o.x, o.y, o.type) for o in map_data.obstacles] == [(2200, 800, 0), (2500, 1200, 5)]
    assert map_data.obstacles[0].description == "cable"
    assert map_data.obstacles[1].description == "extension cord"


def test_carpet_and_removed_cells(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert len(map_data.carpets) == 18 * 30
    assert len(map_data.removed) == 3 * HEIGHT
    # Carpets live in the bedroom (x 70..99, y 12..29).
    assert (70 + WIDTH * 12) in map_data.carpets


def test_rooms(raw_map: bytes) -> None:
    map_data = parse_rrmap(raw_map)

    assert set(map_data.rooms) == {1, 2, 3}
    living = map_data.rooms[1]
    assert (living.x0, living.y0) == ((LEFT + 8) * MM_PER_PIXEL, (TOP + 8) * MM_PER_PIXEL)
    assert (living.x1, living.y1) == ((LEFT + 52) * MM_PER_PIXEL, (TOP + 48) * MM_PER_PIXEL)
    assert map_data.vacuum_room == 1


def test_plain_and_zlib_payloads() -> None:
    raw = build_map(compress=False)
    assert parse_rrmap(raw).image is not None


def test_decompress_variants() -> None:
    raw = build_map(compress=False)
    assert decompress(gzip.compress(raw)) == raw
    assert decompress(raw) == raw


def test_invalid_header() -> None:
    with pytest.raises(RRMapFormatError):
        parse_rrmap(b"rr")
    with pytest.raises(RRMapFormatError):
        parse_rrmap(b"rr" + b"\x00" * 30)


def test_truncated_map_is_tolerated(raw_map: bytes) -> None:
    raw = gzip.decompress(raw_map)
    truncated = raw[: len(raw) // 2]
    map_data = parse_rrmap(truncated)
    # The parser must not raise, it just reports what it managed to read.
    assert map_data.map_index == 7


def test_unknown_block_is_recorded(raw_map: bytes) -> None:
    raw = gzip.decompress(raw_map)
    from tests.fixtures.rrmap_builder import _block

    raw = raw + _block(999, b"\x01\x02\x03")
    map_data = parse_rrmap(raw)
    assert 999 in map_data.unknown_blocks


def test_as_dict_roundtrip(raw_map: bytes) -> None:
    attributes = parse_rrmap(raw_map).as_dict()
    assert attributes["rooms"]["1"]["x0"] == (LEFT + 8) * MM_PER_PIXEL
    assert len(attributes["path"]["chunks"][0]) == 6
    assert attributes["image"]["width"] == WIDTH
