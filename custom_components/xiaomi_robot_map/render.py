"""Render a parsed RRMap into a PNG image (Pillow only, no HA imports)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .const import (
    DEFAULT_LAYERS,
    DEFAULT_ROBOT_RADIUS,
    DEFAULT_SCALE,
    DEFAULT_THEME,
    LAYER_CHARGER,
    LAYER_GOTO_PATH,
    LAYER_NO_CARPET_AREAS,
    LAYER_NO_GO_AREAS,
    LAYER_NO_MOPPING_AREAS,
    LAYER_OBSTACLES,
    LAYER_PATH,
    LAYER_PREDICTED_PATH,
    LAYER_ROBOT,
    LAYER_ROOM_NUMBERS,
    LAYER_VIRTUAL_WALLS,
    LAYER_ZONES,
)
from .rr_map import (
    MM_PER_PIXEL,
    PIXEL_FLOOR,
    PIXEL_OUTSIDE,
    PIXEL_SCAN,
    PIXEL_WALL,
    Area,
    MapImage,
    Point,
    RRMap,
)

_LOGGER = logging.getLogger(__name__)

LABEL_FONT_SIZE = 11
LABEL_MARGIN = 4

Color = tuple[int, ...]

CALIBRATION_CENTER = 25500
CALIBRATION_DIFF = 1000

THEMES: dict[str, dict[str, object]] = {
    "dark": {
        "background": (16, 18, 23, 255),
        "outside": (16, 18, 23, 255),
        "floor": (43, 47, 56, 255),
        "wall": (94, 103, 118, 255),
        "grey_wall": (62, 68, 80, 255),
        "scan": (33, 36, 43, 255),
        "carpet": (63, 78, 96, 255),
        "path": (86, 168, 255, 235),
        "mop_path": (150, 222, 255, 220),
        "goto_path": (76, 217, 140, 235),
        "predicted_path": (255, 191, 82, 220),
        "zones_fill": (255, 214, 102, 40),
        "zones_outline": (255, 214, 102, 220),
        "wall_line": (255, 92, 92, 235),
        "no_go_fill": (255, 82, 105, 70),
        "no_go_outline": (255, 82, 105, 235),
        "no_mop_fill": (168, 130, 255, 60),
        "no_mop_outline": (168, 130, 255, 235),
        "no_carpet_fill": (255, 140, 60, 55),
        "no_carpet_outline": (255, 140, 60, 235),
        "charger": (86, 240, 190, 210),
        "charger_outline": (86, 240, 190, 255),
        "robot": (232, 238, 250, 255),
        "robot_outline": (12, 14, 18, 255),
        "robot_accent": (0, 194, 255, 255),
        "room_label": (235, 240, 250, 230),
        "text": (220, 226, 238, 255),
        "text_dim": (150, 158, 174, 255),
        "room_palette": [
            (240, 178, 122),
            (133, 193, 233),
            (217, 136, 128),
            (120, 190, 210),
            (205, 130, 120),
            (243, 196, 120),
            (120, 200, 150),
            (245, 176, 110),
            (222, 205, 120),
            (110, 200, 180),
            (110, 160, 210),
            (160, 150, 215),
            (215, 160, 190),
            (140, 190, 130),
            (200, 180, 140),
            (170, 170, 200),
        ],
    },
    "light": {
        "background": (245, 246, 249, 255),
        "outside": (232, 234, 240, 255),
        "floor": (255, 255, 255, 255),
        "wall": (138, 146, 160, 255),
        "grey_wall": (176, 182, 192, 255),
        "scan": (240, 242, 246, 255),
        "carpet": (206, 220, 235, 255),
        "path": (32, 118, 220, 235),
        "mop_path": (0, 158, 200, 220),
        "goto_path": (24, 160, 90, 235),
        "predicted_path": (230, 160, 30, 220),
        "zones_fill": (240, 190, 60, 45),
        "zones_outline": (200, 150, 30, 220),
        "wall_line": (220, 60, 60, 235),
        "no_go_fill": (235, 70, 90, 55),
        "no_go_outline": (215, 50, 70, 235),
        "no_mop_fill": (140, 105, 235, 45),
        "no_mop_outline": (130, 95, 225, 235),
        "no_carpet_fill": (235, 120, 40, 40),
        "no_carpet_outline": (220, 110, 30, 235),
        "charger": (30, 170, 130, 200),
        "charger_outline": (20, 140, 105, 255),
        "robot": (40, 44, 52, 255),
        "robot_outline": (255, 255, 255, 255),
        "robot_accent": (0, 150, 220, 255),
        "room_label": (40, 44, 52, 230),
        "text": (30, 34, 42, 255),
        "text_dim": (110, 118, 132, 255),
        "room_palette": [
            (250, 205, 160),
            (170, 214, 240),
            (236, 176, 172),
            (168, 210, 224),
            (230, 176, 168),
            (250, 220, 160),
            (172, 220, 190),
            (250, 206, 158),
            (240, 228, 170),
            (166, 222, 206),
            (166, 196, 232),
            (196, 186, 236),
            (234, 190, 214),
            (186, 218, 172),
            (230, 214, 180),
            (204, 204, 228),
        ],
    },
}


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Options controlling how the map is drawn."""

    theme: str = DEFAULT_THEME
    layers: frozenset[str] = frozenset(DEFAULT_LAYERS)
    scale: float = DEFAULT_SCALE
    rotate: int = 0
    trim: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    path_width: int = 2
    robot_radius: int = DEFAULT_ROBOT_RADIUS
    charger_radius: int = 5

    def palette(self) -> dict[str, object]:
        return THEMES.get(self.theme, THEMES[DEFAULT_THEME])


@dataclass(frozen=True, slots=True)
class ImageTransform:
    """Map coordinates (mm) -> pixels of the rendered image."""

    width: int
    height: int
    source_width: int
    source_height: int
    left: int
    top: int
    trim_left: int
    trim_top: int
    trim_right: int
    trim_bottom: int
    scale: float
    rotation: int

    @property
    def pre_rotation_width(self) -> float:
        return (self.source_width - self.trim_left - self.trim_right) * self.scale

    @property
    def pre_rotation_height(self) -> float:
        return (self.source_height - self.trim_top - self.trim_bottom) * self.scale

    def _source(self, x: float, y: float) -> tuple[float, float]:
        """Map coordinates to pixels of the untransformed full-size bitmap."""
        px = x / MM_PER_PIXEL - self.left
        py = self.source_height - (y / MM_PER_PIXEL - self.top) - 1
        return px, py

    def _crop_scale(self, px: float, py: float) -> tuple[float, float]:
        return (px - self.trim_left) * self.scale, (py - self.trim_top) * self.scale

    def _rotate(self, px: float, py: float) -> tuple[float, float]:
        pre_w = self.pre_rotation_width
        pre_h = self.pre_rotation_height
        if self.rotation == 90:
            return pre_h - 1 - py, px
        if self.rotation == 180:
            return pre_w - 1 - px, pre_h - 1 - py
        if self.rotation == 270:
            return py, pre_w - 1 - px
        return px, py

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """Convert map coordinates to pixel coordinates of the rendered map."""
        px, py = self._source(x, y)
        px, py = self._crop_scale(px, py)
        return self._rotate(px, py)


@dataclass(frozen=True, slots=True)
class RenderedMap:
    """The rendered image plus everything needed to interpret it."""

    image: Image.Image
    transform: ImageTransform | None
    scale: float = 1.0
    rotation: int = 0
    is_empty: bool = False

    @property
    def png(self) -> bytes:
        import io

        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    def to_pixel(self, x: float, y: float) -> tuple[float, float] | None:
        if self.transform is None:
            return None
        return self.transform.to_pixel(x, y)

    def calibration_points(self) -> list[dict[str, object]] | None:
        """Calibration in the format used by lovelace vacuum map cards."""
        if self.transform is None:
            return None
        points = [
            Point(CALIBRATION_CENTER, CALIBRATION_CENTER),
            Point(CALIBRATION_CENTER + CALIBRATION_DIFF, CALIBRATION_CENTER),
            Point(CALIBRATION_CENTER, CALIBRATION_CENTER + CALIBRATION_DIFF),
        ]
        result = []
        for point in points:
            px, py = self.transform.to_pixel(point.x, point.y)
            result.append(
                {
                    "vacuum": {"x": point.x, "y": point.y},
                    "map": {"x": int(round(px)), "y": int(round(py))},
                }
            )
        return result


def _palette_for(theme: dict[str, object], *, room_colors: bool) -> bytes:
    palette = bytearray(768)
    rooms: Sequence[Color] = theme["room_palette"]  # type: ignore[assignment]
    for value in range(256):
        if value == PIXEL_OUTSIDE:
            color = theme["outside"]
        elif value == PIXEL_WALL:
            color = theme["wall"]
        elif value == PIXEL_FLOOR:
            color = theme["floor"]
        elif value == PIXEL_SCAN:
            color = theme["scan"]
        else:
            low = value & 0x07
            if low == 0:
                color = theme["grey_wall"]
            elif low == 1:
                color = theme["wall"]
            elif low == 7:
                color = rooms[(value >> 3) % len(rooms)] if room_colors else theme["floor"]
            else:
                color = theme["grey_wall"]
        palette[value * 3 : value * 3 + 3] = bytes(color[:3])  # type: ignore[index]
    return bytes(palette)


def _build_mask(indices: Iterable[int], width: int, height: int) -> Image.Image:
    mask = bytearray(width * height)
    for index in indices:
        if 0 <= index < len(mask):
            mask[index] = 255
    return Image.frombytes("L", (width, height), bytes(mask)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def _base_image(map_image: MapImage, theme: dict[str, object], room_colors: bool) -> Image.Image:
    width, height = map_image.width, map_image.height
    image = Image.frombytes("P", (width, height), map_image.pixels)
    image.putpalette(_palette_for(theme, room_colors=room_colors))
    image = image.convert("RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return image


def _apply_removed(image: Image.Image, map_image: MapImage, removed: set[int], theme: dict[str, object]) -> Image.Image:
    if not removed:
        return image
    mask = _build_mask(removed, map_image.width, map_image.height)
    patch = Image.new("RGBA", image.size, theme["outside"])  # type: ignore[arg-type]
    return Image.composite(patch, image, mask)


def _draw_carpet(image: Image.Image, map_image: MapImage, theme: dict[str, object], carpets: set[int]) -> Image.Image:
    if not carpets:
        return image
    width, height = map_image.width, map_image.height
    mask = _build_mask(carpets, width, height)
    hatch = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(hatch)
    color = theme["carpet"]
    for offset in range(-height, width + height, 6):
        draw.line([(offset, 0), (offset + height * 2, height * 2)], fill=color, width=1)
    return Image.composite(Image.alpha_composite(image, hatch), image, mask)


class _Canvas:
    """Helper that draws with transparency on top of an RGBA image."""

    def __init__(self, image: Image.Image, transform: ImageTransform):
        self.image = image
        self.transform = transform

    def point(self, x: float, y: float) -> tuple[float, float]:
        """Map coordinates -> pixels of the finished (cropped/scaled/rotated) image."""
        return self.transform.to_pixel(x, y)

    def layer(self, draw: Callable[[ImageDraw.ImageDraw], None]) -> None:
        overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        draw(ImageDraw.Draw(overlay, "RGBA"))
        self.image = Image.alpha_composite(self.image, overlay)


def _draw_robot(canvas: _Canvas, point: Point, radius: float, theme: dict[str, object]) -> None:
    px, py = canvas.point(point.x, point.y)
    angle = point.angle or 0
    outline = theme["robot_outline"]
    fill = theme["robot"]
    accent = theme["robot_accent"]

    def draw(d: ImageDraw.ImageDraw) -> None:
        d.ellipse([px - radius, py - radius, px + radius, py + radius], fill=fill, outline=outline, width=2)
        wedge = radius * 0.9
        d.pieslice(
            [px - wedge, py - wedge, px + wedge, py + wedge],
            start=-angle - 32,
            end=-angle + 32,
            fill=accent,
        )
        dot = max(1.0, radius * 0.24)
        d.ellipse([px - dot, py - dot, px + dot, py + dot], fill=outline)

    canvas.layer(draw)


def _draw_charger(canvas: _Canvas, point: Point, radius: float, theme: dict[str, object]) -> None:
    px, py = canvas.point(point.x, point.y)
    color = theme["charger"]
    outline = theme["charger_outline"]

    def draw(d: ImageDraw.ImageDraw) -> None:
        d.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color, outline=outline, width=2)
        dot = max(1.0, radius * 0.3)
        d.ellipse([px - dot, py - dot, px + dot, py + dot], fill=outline)

    canvas.layer(draw)


def _draw_path(canvas: _Canvas, chunks: Iterable[Sequence[Point]], width: int, color: Color) -> None:
    def draw(d: ImageDraw.ImageDraw) -> None:
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            points = [canvas.point(p.x, p.y) for p in chunk]
            d.line(points, fill=color, width=width, joint="curve")

    canvas.layer(draw)


def _draw_walls(canvas: _Canvas, walls: Iterable, color: Color, width: int = 2) -> None:
    def draw(d: ImageDraw.ImageDraw) -> None:
        for wall in walls:
            d.line([canvas.point(wall.x0, wall.y0), canvas.point(wall.x1, wall.y1)], fill=color, width=width)

    canvas.layer(draw)


def _draw_areas(canvas: _Canvas, areas: Iterable[Area], fill: Color, outline: Color) -> None:
    def draw(d: ImageDraw.ImageDraw) -> None:
        for area in areas:
            polygon = [canvas.point(x, y) for x, y in area.points]
            if len(polygon) >= 3:
                d.polygon(polygon, fill=fill, outline=outline)

    canvas.layer(draw)


def _draw_zones(canvas: _Canvas, zones: Iterable, fill: Color, outline: Color) -> None:
    def draw(d: ImageDraw.ImageDraw) -> None:
        for zone in zones:
            polygon = [
                canvas.point(zone.x0, zone.y0),
                canvas.point(zone.x1, zone.y0),
                canvas.point(zone.x1, zone.y1),
                canvas.point(zone.x0, zone.y1),
            ]
            d.polygon(polygon, fill=fill, outline=outline)

    canvas.layer(draw)


def _draw_obstacles(
    canvas: _Canvas, obstacles: Iterable, theme: dict[str, object], scale: float = 1.0
) -> None:
    color = theme["wall_line"]
    stroke = max(1, round(2 * scale))

    def draw(d: ImageDraw.ImageDraw) -> None:
        for obstacle in obstacles:
            px, py = canvas.point(obstacle.x, obstacle.y)
            radius = 3 * scale
            d.line([(px - radius, py - radius), (px + radius, py + radius)], fill=color, width=stroke)
            d.line([(px - radius, py + radius), (px + radius, py - radius)], fill=color, width=stroke)

    canvas.layer(draw)


def _label_font(scale: float) -> ImageFont.ImageFont:
    """Pillow default font, sized for the rendered scale."""
    size = max(10, int(round(LABEL_FONT_SIZE * (scale if scale and scale > 0 else 1.0))))
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def _draw_room_numbers(
    image: Image.Image,
    map_data: RRMap,
    theme: dict[str, object],
    transform: ImageTransform | None,
) -> Image.Image:
    """Draw room numbers upright, whatever the map rotation/scale is.

    The numbers are painted on the finished image, so rotating the map never
    rotates the text: only the room centres are transformed.
    """
    if transform is None or not map_data.rooms:
        return image
    font = _label_font(transform.scale)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for number, room in map_data.rooms.items():
        px, py = transform.to_pixel((room.x0 + room.x1) / 2, (room.y0 + room.y1) / 2)
        if not (-LABEL_MARGIN <= px <= image.width + LABEL_MARGIN) or not (
            -LABEL_MARGIN <= py <= image.height + LABEL_MARGIN
        ):
            continue
        draw.text(
            (px, py),
            str(number),
            fill=theme["room_label"],
            font=font,
            anchor="mm",
            stroke_width=2,
            stroke_fill=theme["background"],
        )
    return Image.alpha_composite(image, overlay)


def _finalize(image: Image.Image, map_image: MapImage, options: RenderOptions) -> tuple[Image.Image, ImageTransform]:
    width, height = map_image.width, map_image.height
    trim_left_pct, trim_top_pct, trim_right_pct, trim_bottom_pct = options.trim
    trim_left = max(0, min(width - 1, int(width * trim_left_pct / 100)))
    trim_right = max(0, min(width - 1, int(width * trim_right_pct / 100)))
    trim_top = max(0, min(height - 1, int(height * trim_top_pct / 100)))
    trim_bottom = max(0, min(height - 1, int(height * trim_bottom_pct / 100)))

    if width - trim_left - trim_right < 20:
        trim_left = trim_right = 0
    if height - trim_top - trim_bottom < 20:
        trim_top = trim_bottom = 0

    cropped = image.crop((trim_left, trim_top, width - trim_right, height - trim_bottom))
    scale = options.scale if options.scale and options.scale > 0 else 1.0
    if scale != 1:
        cropped = cropped.resize(
            (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale))),
            resample=Image.Resampling.NEAREST,
        )

    rotation = options.rotate if options.rotate in (0, 90, 180, 270) else 0
    if rotation == 90:
        cropped = cropped.transpose(Image.Transpose.ROTATE_270)
    elif rotation == 180:
        cropped = cropped.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        cropped = cropped.transpose(Image.Transpose.ROTATE_90)

    transform = ImageTransform(
        width=cropped.width,
        height=cropped.height,
        source_width=width,
        source_height=height,
        left=map_image.left,
        top=map_image.top,
        trim_left=trim_left,
        trim_top=trim_top,
        trim_right=trim_right,
        trim_bottom=trim_bottom,
        scale=scale,
        rotation=rotation,
    )
    return cropped, transform


def render_map(map_data: RRMap, options: RenderOptions | None = None) -> RenderedMap:
    """Render the map into an RGBA image."""
    options = options or RenderOptions()
    if map_data.image is None or map_data.is_empty:
        return render_placeholder("NO MAP", options)

    map_image = map_data.image
    theme = options.palette()
    image = _base_image(map_image, theme, room_colors=True)
    image = _apply_removed(image, map_image, map_data.removed, theme)
    image = _draw_carpet(image, map_image, theme, map_data.carpets)

    # Crop / scale / rotate the raster first, then draw every vector layer on the
    # finished image: raising the resolution upscales only the pixel grid, while
    # paths, walls, the robot and the labels keep their own crisp geometry.
    image, transform = _finalize(image, map_image, options)
    canvas = _Canvas(image, transform)
    layers = options.layers
    scale = transform.scale
    path_width = max(1, round(options.path_width * scale))
    stroke = max(1, round(2 * scale))

    if LAYER_NO_CARPET_AREAS in layers and map_data.no_carpet_areas:
        _draw_areas(canvas, map_data.no_carpet_areas, theme["no_carpet_fill"], theme["no_carpet_outline"])  # type: ignore[arg-type]
    if LAYER_NO_MOPPING_AREAS in layers and map_data.no_mopping_areas:
        _draw_areas(canvas, map_data.no_mopping_areas, theme["no_mop_fill"], theme["no_mop_outline"])  # type: ignore[arg-type]
    if LAYER_NO_GO_AREAS in layers and map_data.no_go_areas:
        _draw_areas(canvas, map_data.no_go_areas, theme["no_go_fill"], theme["no_go_outline"])  # type: ignore[arg-type]
    if LAYER_ZONES in layers and map_data.zones:
        _draw_zones(canvas, map_data.zones, theme["zones_fill"], theme["zones_outline"])  # type: ignore[arg-type]
    if LAYER_VIRTUAL_WALLS in layers and map_data.walls:
        _draw_walls(canvas, map_data.walls, theme["wall_line"], width=stroke)  # type: ignore[arg-type]
    if LAYER_PREDICTED_PATH in layers and map_data.predicted_path:
        _draw_path(canvas, map_data.predicted_path.chunks, path_width, theme["predicted_path"])  # type: ignore[arg-type]
    if LAYER_GOTO_PATH in layers and map_data.goto_path:
        _draw_path(canvas, map_data.goto_path.chunks, path_width, theme["goto_path"])  # type: ignore[arg-type]
    if LAYER_PATH in layers and map_data.path:
        _draw_path(canvas, map_data.path.chunks, path_width, theme["path"])  # type: ignore[arg-type]
    if map_data.mop_path and LAYER_PATH in layers:
        _draw_path(canvas, map_data.mop_path.chunks, path_width, theme["mop_path"])  # type: ignore[arg-type]
    if LAYER_OBSTACLES in layers and map_data.obstacles:
        _draw_obstacles(canvas, map_data.obstacles, theme, scale)
    if LAYER_CHARGER in layers and map_data.charger:
        _draw_charger(canvas, map_data.charger, options.charger_radius * scale, theme)
    if LAYER_ROBOT in layers and map_data.vacuum_position:
        _draw_robot(canvas, map_data.vacuum_position, options.robot_radius * scale, theme)

    final = canvas.image
    if LAYER_ROOM_NUMBERS in layers and map_data.rooms:
        final = _draw_room_numbers(final, map_data, theme, transform)

    return RenderedMap(
        image=final,
        transform=transform,
        scale=scale,
        rotation=options.rotate,
        is_empty=False,
    )


def render_placeholder(
    text: str = "NO MAP",
    options: RenderOptions | None = None,
    width: int = 320,
    height: int = 200,
) -> RenderedMap:
    """Render a neutral placeholder image with a message."""
    options = options or RenderOptions()
    theme = options.palette()
    image = Image.new("RGBA", (width, height), theme["background"])  # type: ignore[arg-type]
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.multiline_text(
        (width / 2, height / 2),
        text,
        fill=theme["text_dim"],
        font=font,
        anchor="mm",
        align="center",
    )
    draw.rectangle([0, 0, width - 1, height - 1], outline=theme["wall"], width=1)
    return RenderedMap(image=image, transform=None, is_empty=True)
