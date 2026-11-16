#!/usr/bin/env python3
"""Render a raw RRMap blob to PNG without Home Assistant running.

Useful for troubleshooting: enable "Store the raw map on disk" in the
integration options (or grab the file from the cloud) and render it offline.

Run it with the Python that Home Assistant uses, e.g.::

    python tools/render_map.py map.bin map.png --theme dark --scale 2 --rotate 90
    python tools/render_map.py map.bin map.png --layers all --room-numbers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.xiaomi_robot_map.render import RenderOptions, render_map  # noqa: E402
from custom_components.xiaomi_robot_map.rr_map import parse_rrmap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="raw map file (.bin / .gz)")
    parser.add_argument("output", type=Path, nargs="?", help="output PNG (default: <input>.png)")
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    parser.add_argument("--trim", default="0,0,0,0", help="left,top,right,bottom in percent")
    parser.add_argument("--layers", default="all", help="'all', 'none' or a comma separated list")
    parser.add_argument("--room-numbers", action="store_true")
    parser.add_argument("--json", action="store_true", help="print parsed attributes as JSON")
    args = parser.parse_args()

    raw = args.input.read_bytes()
    map_data = parse_rrmap(raw)

    if args.layers == "all":
        from custom_components.xiaomi_robot_map.const import ALL_LAYERS

        layers = frozenset(ALL_LAYERS)
    elif args.layers == "none":
        layers = frozenset()
    else:
        layers = frozenset(part.strip() for part in args.layers.split(",") if part.strip())

    trim = tuple(float(part) for part in args.trim.split(","))
    rendered = render_map(
        map_data,
        RenderOptions(
            theme=args.theme,
            layers=layers,
            scale=args.scale,
            rotate=args.rotate,
            trim=trim,  # type: ignore[arg-type]
        ),
    )

    output = args.output or args.input.with_suffix(".png")
    rendered.image.save(output)
    print(
        f"{output}: {rendered.image.width}x{rendered.image.height}, "
        f"map index {map_data.map_index}, rooms {len(map_data.rooms)}, "
        f"path points {map_data.path.points_count if map_data.path else 0}"
    )

    if args.json:
        import json

        print(json.dumps(map_data.as_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
