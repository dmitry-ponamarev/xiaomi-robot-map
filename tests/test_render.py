"""Tests for the map renderer and the coordinate transforms."""

from __future__ import annotations

import pytest
from PIL import Image

from custom_components.xiaomi_robot_map.render import (
    THEMES,
    ImageTransform,
    RenderOptions,
    _draw_room_numbers,
    render_map,
    render_placeholder,
)
from custom_components.xiaomi_robot_map.rr_map import parse_rrmap
from tests.fixtures.rrmap_builder import HEIGHT, LEFT, TOP, WIDTH, build_map

THRESHOLD = 3


@pytest.fixture
def map_data():
    return parse_rrmap(build_map())


def _color_at(image: Image.Image, x: float, y: float) -> tuple[int, int, int]:
    pixel = image.convert("RGB").getpixel((int(x), int(y)))
    return tuple(pixel)  # type: ignore[return-value]


def test_default_render_size(map_data) -> None:
    rendered = render_map(map_data)
    assert rendered.image.size == (WIDTH, HEIGHT)
    assert rendered.transform is not None
    assert rendered.image.mode == "RGBA"


def test_robot_position_maps_to_the_right_room(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset()))
    position = map_data.vacuum_position
    x, y = rendered.to_pixel(position.x, position.y)
    assert (round(x), round(y)) == (28, 81)

    room_a = THEMES["dark"]["room_palette"][1]
    color = _color_at(rendered.image, x, y)
    assert color == pytest.approx(room_a, abs=THRESHOLD)


def test_y_axis_is_flipped(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset()))
    # Map point near the bottom edge of the bitmap must be near the bottom of the image.
    _, y_bottom = rendered.to_pixel(600, (TOP + 1) * 50)
    _, y_top = rendered.to_pixel(600, (TOP + HEIGHT - 2) * 50)
    assert y_bottom > y_top
    assert 0 <= y_top < HEIGHT and 0 <= y_bottom < HEIGHT


@pytest.mark.parametrize(("rotation", "size", "point"), [(90, (HEIGHT, WIDTH), (14, 28)), (180, (WIDTH, HEIGHT), (99, 14))])
def test_rotation(map_data, rotation: int, size: tuple[int, int], point: tuple[int, int]) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset(), rotate=rotation))
    assert rendered.image.size == size
    position = map_data.vacuum_position
    x, y = rendered.to_pixel(position.x, position.y)
    assert (round(x), round(y)) == point


def test_rotation_is_applied_to_the_image(map_data) -> None:
    """The transform must describe the same rotation that Pillow performs."""
    options = RenderOptions(layers=frozenset(), rotate=90)
    rendered = render_map(map_data, options)
    # (28, 81) in the unrotated image is room A; after a 90° clockwise turn it
    # must land at (96 - 1 - 81, 28) = (14, 28).
    room_a = THEMES["dark"]["room_palette"][1]
    assert _color_at(rendered.image, 14, 28) == pytest.approx(room_a, abs=THRESHOLD)


def test_scale(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset(), scale=2.0))
    assert rendered.image.size == (WIDTH * 2, HEIGHT * 2)
    position = map_data.vacuum_position
    x, y = rendered.to_pixel(position.x, position.y)
    assert (round(x), round(y)) == (56, 162)


def test_trim(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset(), trim=(10, 0, 0, 0)))
    assert rendered.image.size == (WIDTH - 12, HEIGHT)
    position = map_data.vacuum_position
    x, _ = rendered.to_pixel(position.x, position.y)
    assert round(x) == 28 - 12


def test_trim_is_ignored_when_too_aggressive(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset(), trim=(49, 49, 49, 49)))
    assert rendered.image.size == (WIDTH, HEIGHT)


def test_calibration_points(map_data) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset()))
    points = rendered.calibration_points()
    assert points is not None and len(points) == 3
    assert points[0]["vacuum"] == {"x": 25500, "y": 25500}
    # Independent computation of the same transform.
    expected_x = 25500 / 50 - LEFT
    expected_y = HEIGHT - (25500 / 50 - TOP) - 1
    assert points[0]["map"] == {"x": round(expected_x), "y": round(expected_y)}
    assert points[1]["map"]["x"] > points[0]["map"]["x"]
    assert points[2]["map"]["y"] < points[0]["map"]["y"]


def test_path_is_drawn(map_data) -> None:
    base = render_map(map_data, RenderOptions(layers=frozenset()))
    with_path = render_map(map_data, RenderOptions(layers=frozenset({"path"})))
    assert base.image.tobytes() != with_path.image.tobytes()


def test_robot_layer_is_drawn(map_data) -> None:
    without = render_map(map_data, RenderOptions(layers=frozenset()))
    with_robot = render_map(map_data, RenderOptions(layers=frozenset({"robot"})))
    assert without.image.tobytes() != with_robot.image.tobytes()
    robot_pixel = THEMES["dark"]["robot"]
    x, y = with_robot.to_pixel(map_data.vacuum_position.x, map_data.vacuum_position.y)
    colors = {
        with_robot.image.convert("RGB").getpixel((round(x) + dx, round(y) + dy))
        for dx in range(-3, 4)
        for dy in range(-3, 4)
    }
    assert any(color == pytest.approx(robot_pixel[:3], abs=6) for color in colors)


def _label_patch_bbox(map_data, rotation: int) -> tuple[int, int]:
    """Bounding box (w, h) of the room-1 glyph drawn on a blank canvas."""
    transform = ImageTransform(
        width=HEIGHT * 2 if rotation != 90 else WIDTH * 2,
        height=WIDTH * 2 if rotation != 90 else HEIGHT * 2,
        source_width=WIDTH,
        source_height=HEIGHT,
        left=LEFT,
        top=TOP,
        trim_left=0,
        trim_top=0,
        trim_right=0,
        trim_bottom=0,
        scale=2.0,
        rotation=rotation,
    )
    canvas = Image.new("RGBA", (transform.width, transform.height), (255, 255, 255, 255))
    painted = _draw_room_numbers(canvas, map_data, THEMES["dark"], transform)

    # Only look at room 1: a crop around its transformed centre, so the other
    # rooms' numbers cannot stretch the bounding box.
    room = map_data.rooms[1]
    px, py = transform.to_pixel((room.x0 + room.x1) / 2, (room.y0 + room.y1) / 2)
    patch = painted.crop((int(px) - 20, int(py) - 20, int(px) + 21, int(py) + 21))
    mask = patch.convert("RGB").point(lambda value: 0 if value > 250 else 255).convert("1")
    box = mask.getbbox()
    assert box is not None, "no room number was drawn"
    return box[2] - box[0], box[3] - box[1]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_room_numbers_stay_upright_when_rotated(map_data, rotation: int) -> None:
    """Labels are painted on the finished image, so rotation must not tilt them."""
    # A digit is taller than it is wide; a tilted one would come out wider.
    width, height = _label_patch_bbox(map_data, rotation)
    assert height > width, f"the label looks rotated at {rotation}°: {width}x{height}"


def test_room_numbers_follow_the_rotation(map_data) -> None:
    """The number sits at the transformed room centre, inside its room."""
    label = THEMES["dark"]["room_label"]
    for rotation in (0, 90, 180, 270):
        rendered = render_map(
            map_data, RenderOptions(layers=frozenset({"room_numbers"}), rotate=rotation, scale=2.0)
        )
        room = map_data.rooms[2]
        x, y = rendered.to_pixel((room.x0 + room.x1) / 2, (room.y0 + room.y1) / 2)
        hits = 0
        for dx in range(-10, 11):
            for dy in range(-10, 11):
                px, py = int(x) + dx, int(y) + dy
                if 0 <= px < rendered.image.width and 0 <= py < rendered.image.height:
                    color = rendered.image.convert("RGB").getpixel((px, py))
                    if color == pytest.approx(label[:3], abs=12):
                        hits += 1
        assert hits > 5, f"no room number under rotation {rotation}"


@pytest.mark.parametrize("scale", [1.0, 2.0, 4.0, 6.0])
def test_resolution_multiplier_scales_the_image(map_data, scale: float) -> None:
    rendered = render_map(map_data, RenderOptions(layers=frozenset(), scale=scale))
    assert rendered.image.size == (int(WIDTH * scale), int(HEIGHT * scale))
    assert rendered.scale == scale


def test_light_theme(map_data) -> None:
    dark = render_map(map_data, RenderOptions(theme="dark"))
    light = render_map(map_data, RenderOptions(theme="light"))
    assert dark.image.tobytes() != light.image.tobytes()


def test_placeholder() -> None:
    rendered = render_placeholder("NO MAP")
    assert rendered.is_empty is True
    assert rendered.transform is None
    assert rendered.image.size == (320, 200)
    assert rendered.png.startswith(b"\x89PNG")


def test_empty_map_renders_placeholder() -> None:
    import struct

    from custom_components.xiaomi_robot_map.rr_map import parse_rrmap as parse

    header = bytearray(28)
    header[0:2] = b"rr"
    struct.pack_into("<H", header, 0x02, len(header))
    empty = parse(bytes(header))
    assert empty.is_empty
    rendered = render_map(empty)
    assert rendered.is_empty is True
