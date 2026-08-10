"""Generate the Windows icon from the product's existing purple V mark."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def build_icon(size: int = 256) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = round(8 * scale)
    radius = round(52 * scale)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        fill=(124, 58, 237, 255),
    )

    points = [
        (round(69 * scale), round(68 * scale)),
        (round(128 * scale), round(190 * scale)),
        (round(187 * scale), round(68 * scale)),
    ]
    draw.line(
        points,
        fill=(255, 255, 255, 255),
        width=max(2, round(27 * scale)),
        joint="curve",
    )
    for point in (points[0], points[-1]):
        x, y = point
        radius_cap = max(1, round(13.5 * scale))
        draw.ellipse(
            (x - radius_cap, y - radius_cap, x + radius_cap, y + radius_cap),
            fill=(255, 255, 255, 255),
        )
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--png-output", type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    image = build_icon()
    image.save(
        args.output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    if args.png_output:
        args.png_output.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.png_output, format="PNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
