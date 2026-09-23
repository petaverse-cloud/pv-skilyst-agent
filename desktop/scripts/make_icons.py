#!/usr/bin/env python3
"""Generate the app icons from code (no design asset, no image library).

Tauri needs RGBA PNGs plus a macOS `.icns` and a Windows `.ico`. Rather than
commit binaries nobody can regenerate, this script draws the placeholder mark
(dark rounded tile + a cyan/violet beam + a play triangle) and writes every
format Tauri asks for, using only the standard library.

    python3 scripts/make_icons.py        # -> src-tauri/icons/

Replace the artwork when the real identity exists; the formats stay the same.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"
SIZES = (32, 128, 256, 512)
ICO_SIZES = (16, 32, 48, 256)
ICNS_TYPES = {32: b"ic11", 64: b"ic12", 128: b"ic07", 256: b"ic08", 512: b"ic09"}

TOP = (27, 36, 48)
BOTTOM = (11, 14, 20)
BEAM_A = (53, 194, 255)
BEAM_B = (139, 92, 246)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _inside_rounded(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _inside_triangle(x: float, y: float, size: float) -> bool:
    """A play triangle centred in the tile."""
    left, right = size * 0.40, size * 0.66
    top, bottom = size * 0.33, size * 0.67
    if not (left <= x <= right and top <= y <= bottom):
        return False
    # the hypotenuse runs from (left, top) to (left, bottom) -> narrowing wedge
    return x - left <= (1 - abs((y - (top + bottom) / 2) / ((bottom - top) / 2))) * (right - left)


def render(size: int, supersample: int = 3) -> bytes:
    """RGBA pixel buffer for one icon size, drawn with supersampled coverage."""
    radius = size * 0.22
    samples = supersample * supersample
    step = 1.0 / (supersample + 1)
    pixels = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(1, supersample + 1):
                for sx in range(1, supersample + 1):
                    x, y = px + sx * step, py + sy * step
                    if not _inside_rounded(x, y, size, radius):
                        continue
                    colour = _mix(TOP, BOTTOM, y / size)
                    if abs((x + y) - size) < size * 0.16:            # the diagonal beam
                        colour = _mix(BEAM_A, BEAM_B, x / size)
                    if _inside_triangle(x, y, size):
                        colour = (255, 255, 255)
                    acc[0] += colour[0]
                    acc[1] += colour[1]
                    acc[2] += colour[2]
                    acc[3] += 255.0
            offset = (py * size + px) * 4
            if acc[3] > 0:
                # premultiplied average, then un-premultiply for straight RGBA
                alpha = acc[3] / samples
                pixels[offset] = round(acc[0] / samples * 255 / alpha)
                pixels[offset + 1] = round(acc[1] / samples * 255 / alpha)
                pixels[offset + 2] = round(acc[2] / samples * 255 / alpha)
                pixels[offset + 3] = round(alpha)
    return bytes(pixels)


def png(size: int, pixels: bytes) -> bytes:
    raw = b"".join(b"\x00" + pixels[row * size * 4:(row + 1) * size * 4] for row in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)     # 8-bit RGBA (color type 6)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, payloads, offset = b"", b"", 6 + 16 * len(images)
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        payloads += data
        offset += len(data)
    return header + entries + payloads


def icns(images: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(kind + struct.pack(">I", len(data) + 8) + data for kind, data in images)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = {size: png(size, render(size)) for size in sorted(set(SIZES) | set(ICO_SIZES)
                                                                  | set(ICNS_TYPES) | {64})}
    written = []
    for size, name in ((32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png"),
                       (512, "icon.png")):
        (OUT / name).write_bytes(rendered[size])
        written.append(name)
    (OUT / "icon.ico").write_bytes(ico([(size, rendered[size]) for size in ICO_SIZES]))
    written.append("icon.ico")
    (OUT / "icon.icns").write_bytes(icns([(kind, rendered[size]) for size, kind in
                                          sorted(ICNS_TYPES.items())]))
    written.append("icon.icns")
    for name in written:
        print(f"{name}: {(OUT / name).stat().st_size} bytes")


if __name__ == "__main__":
    main()
