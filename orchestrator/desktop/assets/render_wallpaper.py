"""Render the GWEN desktop wallpaper: a dark dusk landscape, generated at image
build time from this script so no binary asset is committed.

Deterministic by construction: every random choice comes from one seeded
``random.Random`` and Pillow's PNG encoder is stable for a given version, so
rebuilding the image reproduces the same bytes. Pillow is already present in
the desktop image as a dependency of strands-agents-tools; no numpy, no
ImageMagick. Runs as root during ``docker build`` only.

Usage: render_wallpaper.py OUTPUT.png [--size WxH]
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

DEFAULT_SIZE = (1440, 900)  # The Xvfb framebuffer in start-desktop.sh.
SEED = 20260930

# Sky stops from zenith (0.0) to the bottom of the canvas (1.0).
SKY_STOPS = (
    (0.00, (5, 7, 15)),
    (0.30, (12, 17, 38)),
    (0.52, (34, 30, 66)),
    (0.66, (92, 52, 84)),
    (0.74, (176, 94, 88)),
    (0.82, (232, 138, 96)),
    (1.00, (40, 30, 52)),
)
# Ridge layers from farthest to nearest: (baseline as a fraction of height,
# amplitude in px, colour, extra jaggedness).
RIDGES = (
    (0.700, 62, (118, 78, 104), 0.35),
    (0.745, 54, (78, 54, 88), 0.45),
    (0.790, 48, (46, 36, 66), 0.60),
    (0.840, 42, (24, 21, 42), 0.80),
    (0.905, 34, (9, 9, 18), 1.00),
)
# Solid backdrop colour xfce4-desktop.xml uses behind the zoomed image.
BACKDROP_RGB = (7, 8, 13)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _sky_colour(t: float) -> tuple[int, int, int]:
    for (t0, c0), (t1, c1) in zip(SKY_STOPS, SKY_STOPS[1:]):
        if t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            u = u * u * (3 - 2 * u)  # smoothstep keeps bands from reading as lines
            return tuple(round(_lerp(a, b, u)) for a, b in zip(c0, c1))
    return SKY_STOPS[-1][1]


def _sky(size: tuple[int, int], rng: random.Random) -> Image.Image:
    width, height = size
    sky = Image.new("RGB", size)
    draw = ImageDraw.Draw(sky)
    for y in range(height):
        draw.line([(0, y), (width, y)], fill=_sky_colour(y / max(1, height - 1)))
    # Seeded grain (+/-3 levels) breaks 8-bit banding in the dark gradient.
    grain = Image.frombytes("L", size, rng.randbytes(width * height)).point(lambda v: v // 37)
    return ImageChops.add(sky, Image.merge("RGB", (grain, grain, grain)), 1.0, -3)


def _stars(size: tuple[int, int], rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    count = max(60, width * height // 2600)
    for _ in range(count):
        x = rng.uniform(0, width)
        # Denser near the zenith, fading out well above the horizon glow.
        y = height * 0.62 * rng.random() ** 1.7
        brightness = int(rng.triangular(90, 255, 150))
        tint = (brightness, brightness, min(255, brightness + rng.randint(0, 25)))
        if rng.random() < 0.08:
            r = rng.uniform(1.0, 1.8)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(*tint, 235))
            arm = r * 3
            draw.line([(x - arm, y), (x + arm, y)], fill=(*tint, 90))
            draw.line([(x, y - arm), (x, y + arm)], fill=(*tint, 90))
        else:
            draw.point((x, y), fill=(*tint, rng.randint(120, 255)))
    return layer


def _moon(size: tuple[int, int]) -> Image.Image:
    width, height = size
    cx, cy, radius = width * 0.735, height * 0.205, height * 0.037
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [cx - radius * 3.2, cy - radius * 3.2, cx + radius * 3.2, cy + radius * 3.2],
        fill=(214, 196, 255, 110),
    )
    layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(radius * 1.6)))
    disk = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(disk)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(244, 238, 226, 255))
    # Terminator: a slightly offset sky-coloured disk gives a waxing gibbous.
    off = radius * 0.55
    draw.ellipse(
        [cx - radius * 1.05 - off, cy - radius * 1.05, cx + radius * 1.05 - off, cy + radius * 1.05],
        fill=(*_sky_colour(cy / height), 0),
    )
    return Image.alpha_composite(layer, disk.filter(ImageFilter.GaussianBlur(0.6)))


def _horizon_glow(size: tuple[int, int]) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [width * 0.05, height * 0.66, width * 0.95, height * 0.96], fill=(255, 176, 120, 120)
    )
    return layer.filter(ImageFilter.GaussianBlur(height * 0.09))


def _ridge_profile(rng: random.Random, width: int, baseline: float, amplitude: float, jag: float):
    waves = []
    for octave, k in enumerate((1, 2, 3, 5, 8, 13)):
        freq = k * rng.uniform(0.75, 1.3)
        phase = rng.uniform(0, math.tau)
        amp = amplitude / (k ** 0.85)
        if octave >= 3:
            amp *= jag
        waves.append((freq, phase, amp))
    # A few sharp summits (triangle waves at low frequency), not a scalloped edge.
    peaks = [(rng.uniform(4, 9), rng.uniform(0, math.tau), amplitude * 0.45 * jag) for _ in range(2)]
    # Fine seeded relief, interpolated so neighbouring columns stay coherent.
    cells = 96
    relief = [rng.uniform(-1, 1) for _ in range(cells + 1)]
    relief_amp = amplitude * 0.10 * jag

    def profile(x: float) -> float:
        t = x / width
        y = sum(a * math.sin(math.tau * f * t + p) for f, p, a in waves)
        for f, p, a in peaks:
            phase = (f * t + p / math.tau) % 1.0
            y -= a * (1 - abs(2 * phase - 1)) ** 2.4
        cell = t * cells
        i = min(cells - 1, int(cell))
        u = cell - i
        y += relief_amp * _lerp(relief[i], relief[i + 1], u * u * (3 - 2 * u))
        return baseline + y

    return profile


def _ridges(size: tuple[int, int], rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    step = max(1, width // 720)
    for index, (base, amplitude, colour, jag) in enumerate(RIDGES):
        profile = _ridge_profile(rng, width, height * base, amplitude * height / DEFAULT_SIZE[1], jag)
        points = [(x, profile(x)) for x in range(0, width + step, step)]
        points += [(width, height), (0, height)]
        ridge = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(ridge).polygon(points, fill=(*colour, 255))
        layer = Image.alpha_composite(layer, ridge)
        if index < len(RIDGES) - 1:
            # Valley mist sits on top of each ridge and fades before the next one.
            mist = Image.new("RGBA", size, (0, 0, 0, 0))
            mist_draw = ImageDraw.Draw(mist)
            top, bottom = int(height * base) - 10, int(height * RIDGES[index + 1][0]) + 6
            for y in range(top, bottom):
                alpha = int(70 * (1 - (y - top) / max(1, bottom - top)) ** 1.5)
                mist_draw.line([(0, y), (width, y)], fill=(232, 170, 150, alpha))
            layer = Image.alpha_composite(layer, mist.filter(ImageFilter.GaussianBlur(height * 0.012)))
    return layer


def _vignette(size: tuple[int, int]) -> Image.Image:
    mask = Image.radial_gradient("L").resize(size, Image.Resampling.BICUBIC)
    return mask.point(lambda v: int(max(0, v - 96) * 0.9))


def render(size: tuple[int, int] = DEFAULT_SIZE, seed: int = SEED) -> Image.Image:
    rng = random.Random(seed)
    canvas = _sky(size, rng).convert("RGBA")
    canvas = Image.alpha_composite(canvas, _stars(size, rng))
    canvas = Image.alpha_composite(canvas, _horizon_glow(size))
    canvas = Image.alpha_composite(canvas, _moon(size))
    canvas = Image.alpha_composite(canvas, _ridges(size, rng))
    flat = canvas.convert("RGB")
    return Image.composite(Image.new("RGB", size, BACKDROP_RGB), flat, _vignette(size))


def _parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.partition("x")
    size = (int(width), int(height))
    if min(size) < 16:
        raise argparse.ArgumentTypeError("size must be at least 16x16")
    return size


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=_parse_size, default=DEFAULT_SIZE, help="WIDTHxHEIGHT, default 1440x900")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render(args.size).save(args.output, format="PNG", optimize=True)
    print(args.output)
    return args.output


if __name__ == "__main__":
    main()
