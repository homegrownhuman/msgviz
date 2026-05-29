#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper: build the mini media files for the test fixtures.

Usage:
    python3 tests/fixtures/build_media_fixtures.py

Writes to:
    tests/fixtures/sample_whatsapp/00000001-PHOTO-2018-05-10-12-30-45.jpg
    tests/fixtures/sample_whatsapp/00000002-AUDIO-2018-05-10-12-31-00.opus
    tests/fixtures/sample_imgs/sample.jpg
    tests/fixtures/sample_imgs/sample.png
    tests/fixtures/sample_imgs/sample.heic
    tests/fixtures/sample_imgs/same_as_sample.heic   (byte-identical copy)

How:
 - 10x10 PNG with alpha channel: raw PNG stream, no external libs.
 - 10x10 JPG: via `sips` from the PNG (available on macOS).
   Fallback: minimal embedded JPG header.
 - HEIC: via `sips -s format heic` from the JPG. Fallback: dummy header.
 - opus: via `ffmpeg` 0.1 s of silence. Fallback: empty file with OggS header.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
WA = HERE / "sample_whatsapp"
IMGS = HERE / "sample_imgs"
WA.mkdir(parents=True, exist_ok=True)
IMGS.mkdir(parents=True, exist_ok=True)


def make_png_rgba(path: Path, w: int = 10, h: int = 10) -> None:
    """Write a bare RGBA PNG without external libs."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    # Pixel: rgba=(220,20,60,255) – crimson, semi-transparent in column 0
    rows = []
    for y in range(h):
        row = b"\x00"  # filter: None
        for x in range(w):
            alpha = 128 if x == 0 else 255
            row += bytes((220, 20, 60, alpha))
        rows.append(row)
    idat = zlib.compress(b"".join(rows), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png)


def make_jpg_via_sips(src_png: Path, dst_jpg: Path) -> bool:
    sips = shutil.which("sips")
    if not sips:
        return False
    res = subprocess.run(
        [sips, "-s", "format", "jpeg", str(src_png), "--out", str(dst_jpg)],
        capture_output=True,
    )
    return res.returncode == 0 and dst_jpg.exists()


def make_heic_via_sips(src_jpg: Path, dst_heic: Path) -> bool:
    sips = shutil.which("sips")
    if not sips:
        return False
    res = subprocess.run(
        [sips, "-s", "format", "heic", str(src_jpg), "--out", str(dst_heic)],
        capture_output=True,
    )
    return res.returncode == 0 and dst_heic.exists()


# Minimal valid JPEG stream (1x1 white pixel). Fallback if sips is missing.
_TINY_JPEG = bytes.fromhex(
    "FFD8FFE000104A46494600010100000100010000FFDB004300080606070605080707"
    "07090908"
    "0A0C140D0C0B0B0C1912130F141D1A1F1E1D1A1C1C20242E2720222C231C1C2837292C"
    "30313434"
    "1F27393D38323C2E333432FFC0000B080001000101011100FFC4001F000001050101"
    "01010101"
    "00000000000000000102030405060708090A0BFFC400B5100002010303020403050504"
    "04000001"
    "7D01020300041105122131410613516107227114328191A1082342B1C11552D1F02433"
    "62728209"
    "0A161718191A25262728292A3435363738393A434445464748494A535455565758595A"
    "63646566"
    "6768696A737475767778797A838485868788898A92939495969798999AA2A3A4A5A6A7"
    "A8A9AAB2"
    "B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6E7E8E9"
    "EAF1F2F3"
    "F4F5F6F7F8F9FAFFDA0008010100003F00FBD0FFD9"
)


def make_opus_via_ffmpeg(dst: Path) -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    res = subprocess.run(
        [
            ff, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "0.1", "-c:a", "libopus", "-b:a", "16k", str(dst),
        ],
        capture_output=True,
    )
    return res.returncode == 0 and dst.exists() and dst.stat().st_size > 0


# Minimal Ogg/Opus header (valid 'OggS' magic + Opus header page).
# Enough for format sniffing tests; won't play. Fallback without ffmpeg.
_TINY_OPUS = (
    b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x13" + b"OpusHead\x01\x01\x00\x00\x80\xbb\x00\x00\x00\x00\x00"
)


def main() -> None:
    # ---- sample_imgs/ -----------------------------------------------------
    png = IMGS / "sample.png"
    make_png_rgba(png)

    jpg = IMGS / "sample.jpg"
    if not make_jpg_via_sips(png, jpg):
        jpg.write_bytes(_TINY_JPEG)

    heic = IMGS / "sample.heic"
    if not make_heic_via_sips(jpg, heic):
        print("WARN: sips could not produce HEIC — writing dummy file with"
              " ftypheic header (not valid HEIC, but sniffable).",
              file=sys.stderr)
        heic.write_bytes(
            b"\x00\x00\x00\x20ftypheic\x00\x00\x00\x00heicmif1miafMiHB"
        )

    same = IMGS / "same_as_sample.heic"
    same.write_bytes(heic.read_bytes())  # exact copy for dedup test

    # ---- sample_whatsapp/ media -------------------------------------------
    wa_photo = WA / "00000001-PHOTO-2018-05-10-12-30-45.jpg"
    # A small JPG (not the same as sample.jpg, to keep hashes distinct).
    # The tiny JPEG stream is enough.
    if not jpg.exists() or jpg.stat().st_size == 0:
        wa_photo.write_bytes(_TINY_JPEG)
    else:
        # Copy sample.jpg — it is small and valid.
        wa_photo.write_bytes(jpg.read_bytes())

    wa_audio = WA / "00000002-AUDIO-2018-05-10-12-31-00.opus"
    if not make_opus_via_ffmpeg(wa_audio):
        print("WARN: ffmpeg missing or libopus not available — writing dummy"
              " with Ogg/Opus header.", file=sys.stderr)
        wa_audio.write_bytes(_TINY_OPUS)

    # ---- Report ------------------------------------------------------------
    for p in sorted([png, jpg, heic, same, wa_photo, wa_audio]):
        print(f"  {p.relative_to(HERE)}  {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
