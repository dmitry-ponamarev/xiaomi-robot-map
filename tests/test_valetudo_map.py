"""Tests for the Valetudo map adapter."""

from __future__ import annotations

import pytest

from custom_components.xiaomi_robot_map.render import RenderOptions, render_map
from custom_components.xiaomi_robot_map.valetudo_map import ValetudoMapError, parse_valetudo_map
from tests.fixtures.valetudo_builder import build_valetudo_map, compress


@pytest.fixture
def document() -> dict:
    return build_valetudo_map()


def test_bitmap_geometry(document: dict) -> None:
    map_data = parse_valetudo_map(document)

    assert map_data.image is not None
    assert (map_data.image.width, map_data.image.height) == (22, 12)
    assert map_data.is_empty is False
    assert map_data.is_complete is True


def test_rooms_are_collected(document: dict) -> None:
    map_data = parse_valetudo_map(document)

    assert set(map_data.rooms) == {1, 2}
    living = map_data.rooms[1]
    # Segment one covers pixels x 1..10, y 1..10; the frame is flipped vertically.
    assert living.x0 == 50
    assert living.x1 == 500
    assert map_data.vacuum_room == 1


def test_positions_and_angles(document: dict) -> None:
    map_data = parse_valetudo_map(document)

    assert map_data.vacuum_position is not None
    assert (map_data.vacuum_position.x, map_data.vacuum_position.y) == (250, 300)
    # Valetudo: 90° clockwise from north (east) -> RRMap: 0° counter-clockwise from east.
    assert map_data.vacuum_position.angle == 0

    assert map_data.charger is not None
    assert (map_data.charger.x, map_data.charger.y) == (950, 400)


def test_paths_and_areas(document: dict) -> None:
    map_data = parse_valetudo_map(document)

    assert map_data.path is not None
    assert map_data.path.points_count == 4
    assert map_data.predicted_path is not None
    assert map_data.predicted_path.points_count == 2

    assert len(map_data.walls) == 1
    wall = map_data.walls[0]
    assert (wall.x0, wall.y0, wall.x1, wall.y1) == (1000, 450, 1000, 50)

    assert len(map_data.no_go_areas) == 1
    assert map_data.no_go_areas[0].points == ((100, 150), (500, 150), (500, 100), (100, 100))
    assert len(map_data.zones) == 1
    assert (map_data.zones[0].x0, map_data.zones[0].y1) == (600, 350)


def test_carpet_material(document: dict) -> None:
    map_data = parse_valetudo_map(document)
    assert map_data.carpets, "carpet segment pixels must be flagged"


def test_rendering(document: dict) -> None:
    map_data = parse_valetudo_map(document)
    rendered = render_map(map_data, RenderOptions(layers=frozenset({"robot", "path"})))
    assert rendered.image.size == (22, 12)
    assert rendered.png.startswith(b"\x89PNG")
    # Image coordinates are top-down: the bitmap row 6 (from the bottom) is row 5.
    x, y = rendered.to_pixel(map_data.vacuum_position.x, map_data.vacuum_position.y)
    assert (round(x), round(y)) == (5, 5)


def test_raw_pixels_are_accepted(document: dict) -> None:
    for layer in document["layers"]:
        layer["pixels"] = []
        layer.pop("compressedPixels")
    document["layers"][0]["pixels"] = [2, 2, 3, 2, 4, 2]
    map_data = parse_valetudo_map(document)
    assert map_data.image is not None


def test_compress_helper_roundtrip() -> None:
    assert compress([(3, 4), (4, 4), (5, 4), (9, 4)]) == [3, 4, 3, 9, 4, 1]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"layers": []},
        {"layers": [{"type": "floor"}]},
        {"layers": [{"type": "floor", "compressedPixels": []}]},
    ],
)
def test_invalid_documents(payload) -> None:
    with pytest.raises(ValetudoMapError):
        parse_valetudo_map(payload)


def test_invalid_pixel_size() -> None:
    document = build_valetudo_map()
    document["pixelSize"] = 0
    with pytest.raises(ValetudoMapError):
        parse_valetudo_map(document)
