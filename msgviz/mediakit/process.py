#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media processing: conversion (HEIC→JPG, opus→m4a, mov→mp4),
content hashing, hash-based path layout.

Source-agnostic: no dependency on Apple DB / WhatsApp export / other
importer code. Callers pass an absolute source path + metadata and
receive a web-relative output path + type.

Path layout:
  media/<kind>/<prefix>/<hash>.<ext>
  originals/<prefix>/<hash>.<orig_ext>

<hash>   = 16-hex SHA-256 of the ORIGINAL source file (before conversion).
<prefix> = first 2 hex chars (sharding against huge directories).
<kind>   ∈ {images, videos, audio, files}.

Implications:
  - Identical content → identical path → automatic dedup.
  - Re-processing is idempotent: if the target file already exists,
    no re-conversion happens.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import hashlib

# ---------------------------------------------------------------------------
# Module configuration. Tests override MEDIA_ROOT / ORIG_ROOT / OUT to
# tmpdir paths; in production runs they point at the project root.
# ---------------------------------------------------------------------------
HOME = os.path.expanduser("~")
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
MEDIA_ROOT = "media"
ORIG_ROOT = "originals"
MAX_DIM = 1600
HASH_LEN = 16

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
FAST = os.environ.get("FAST") == "1"

# Sub-folder per media kind (hash layout: media/<sub>/<prefix>/<hash>.<ext>).
SUB_IMG = "images"
SUB_VID = "videos"
SUB_AUD = "audio"
SUB_OTH = "files"
SUB_TXT = "text"  # historisch, wird nicht mehr aktiv genutzt

# Format-Mappings ----------------------------------------------------------
MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heic",
    "image/webp": ".webp", "image/tiff": ".tiff",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/3gpp": ".3gp",
    "audio/x-m4a": ".m4a", "audio/mp4": ".m4a",
    "audio/amr": ".amr", "audio/aac": ".aac",
}
HEIC_MIMES = {"image/heic", "image/heif", "image/heic-sequence"}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif",
              ".webp", ".tiff", ".tif")
VIDEO_EXTS = (".mov", ".mp4", ".3gp", ".m4v", ".avi")
AUDIO_EXTS = (".caf", ".amr", ".m4a", ".aac", ".wav", ".opus")


def classify(mlow: str, ext: str) -> str:
    """MIME + Extension → 'image' | 'video' | 'audio' | 'other'."""
    e = ext.lower()
    if mlow.startswith("image/") or e in IMAGE_EXTS:
        return "image"
    if mlow.startswith("video/") or e in VIDEO_EXTS:
        return "video"
    if mlow.startswith("audio/") or e in AUDIO_EXTS:
        return "audio"
    return "other"


def has_alpha(src: str) -> bool:
    """True wenn das Bild einen Alpha-Kanal hat (sips-basiert, macOS)."""
    try:
        out = subprocess.run(
            ["sips", "-g", "hasAlpha", src],
            capture_output=True, text=True,
        ).stdout
        return "hasAlpha: yes" in out
    except Exception:
        return False


def is_portrait(path: str, typ: str) -> bool:
    """True if the web media is portrait (height > width)."""
    p = os.path.join(OUT, path) if not os.path.isabs(path) else path
    try:
        if typ == "image":
            out = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", p],
                capture_output=True, text=True,
            ).stdout
            w = h = 0
            for line in out.splitlines():
                if "pixelWidth" in line:
                    w = int(line.split(":")[1])
                if "pixelHeight" in line:
                    h = int(line.split(":")[1])
            return h > w > 0
        if typ == "video" and FFPROBE:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", p],
                capture_output=True, text=True,
            ).stdout.strip()
            if "," in out:
                w, h = out.split(",")[:2]
                return int(h) > int(w) > 0
    except Exception:
        pass
    return False


# Hash + Pfad-Layout ---------------------------------------------------------
def content_hash(path: str) -> str:
    """SHA-256-Prefix der Datei. Streaming-Lesung."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_LEN]


def _hash_web_rel(kind_dir: str, h: str, ext: str) -> str:
    """media/<kind>/<prefix>/<hash>.<ext> (Web-Pfad)."""
    if not ext.startswith("."):
        ext = "." + ext
    return f"{MEDIA_ROOT}/{kind_dir}/{h[:2]}/{h}{ext.lower()}"


def _hash_orig_rel(h: str, ext: str) -> str:
    """originals/<prefix>/<hash>.<ext> (Web-Pfad, Original)."""
    if not ext.startswith("."):
        ext = "." + ext
    return f"{ORIG_ROOT}/{h[:2]}/{h}{ext.lower()}"


def _abspath(rel: str) -> str:
    """Absolute path for a web-relative path. Supports absolute
    MEDIA_ROOT/ORIG_ROOT too (tests set them to tmpdir paths)."""
    if os.path.isabs(rel):
        return rel
    for root in (MEDIA_ROOT, ORIG_ROOT):
        if not root:
            continue
        if rel.startswith(root + "/") or rel == root:
            if os.path.isabs(root):
                rest = rel[len(root):].lstrip("/")
                return os.path.join(root, rest)
            return os.path.join(OUT, rel)
    return os.path.join(OUT, rel)


def _ensure_parent(absp: str) -> None:
    os.makedirs(os.path.dirname(absp), exist_ok=True)


def ensure_dirs(slug: str) -> None:
    """No-op stub. Kept for API compatibility. Directories are created
    lazily per hash path anyway."""
    return None


# ---------------------------------------------------------------------------
# Main function: process one attachment
# ---------------------------------------------------------------------------
def process_asset(src, idx, mime, transfer_name, is_sticker, slug, st, is_me):
    """Process one attachment → (rel, typ). Updates stats.

    Layout: hash-based, source-agnostic. The `idx` and `slug` parameters
    remain in the signature (caller compatibility) but don't feed into
    the path.
    """
    mlow = (mime or "").lower()
    ext = MIME_EXT.get(mlow)
    if not ext and transfer_name:
        ext = os.path.splitext(transfer_name)[1].lower() or None
    if not ext:
        ext = os.path.splitext(src)[1] or ".bin"
    typ = classify(mlow, ext)
    is_heic = mlow in HEIC_MIMES or ext.lower() in (".heic", ".heif")

    h = content_hash(src)

    # Original sichern (nur Bilder)
    if typ == "image":
        oext = ext if ext.startswith(".") else "." + ext
        if is_heic and oext.lower() not in (".heic", ".heif"):
            oext = ".heic"
        orig_rel = _hash_orig_rel(h, oext)
        orig_abs = _abspath(orig_rel)
        if not os.path.exists(orig_abs):
            _ensure_parent(orig_abs)
            shutil.copy2(src, orig_abs)
        try:
            st["bytes_orig"] += os.path.getsize(orig_abs)
        except OSError:
            pass

    if typ == "audio":
        rel = _hash_web_rel(SUB_AUD, h, ".m4a")
        dst = _abspath(rel)
        st["media"]["audio"]["me" if is_me else "them"] += 1
        if os.path.exists(dst):
            return rel, "audio"
        _ensure_parent(dst)
        ok = False
        if FFMPEG:
            try:
                subprocess.run([FFMPEG, "-y", "-i", src, "-c:a", "aac", "-b:a", "96k", dst],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = os.path.exists(dst) and os.path.getsize(dst) > 0
            except Exception:
                ok = False
        if not ok:
            try:
                subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", src, dst],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = os.path.exists(dst) and os.path.getsize(dst) > 0
            except Exception:
                ok = False
        if not ok:
            rel = _hash_web_rel(SUB_AUD, h, ext)
            dst = _abspath(rel)
            if not os.path.exists(dst):
                _ensure_parent(dst)
                shutil.copy2(src, dst)
        return rel, "audio"

    if typ == "image":
        st["media"]["image"]["me" if is_me else "them"] += 1
        want_png = (ext.lower() == ".png") or is_sticker or (is_heic and has_alpha(src))
        if want_png:
            rel = _hash_web_rel(SUB_IMG, h, ".png")
            dst = _abspath(rel)
            if os.path.exists(dst):
                return rel, "image"
            _ensure_parent(dst)
            try:
                subprocess.run(["sips", "-s", "format", "png", "-Z", str(MAX_DIM), src, "--out", dst],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                shutil.copy2(src, dst)
            return rel, "image"
        if ext.lower() == ".gif":
            rel = _hash_web_rel(SUB_IMG, h, ".gif")
            dst = _abspath(rel)
            if not os.path.exists(dst):
                _ensure_parent(dst)
                shutil.copy2(src, dst)
            return rel, "image"
        rel = _hash_web_rel(SUB_IMG, h, ".jpg")
        dst = _abspath(rel)
        if os.path.exists(dst):
            return rel, "image"
        _ensure_parent(dst)
        try:
            subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_DIM), src, "--out", dst],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            shutil.copy2(src, dst)
        return rel, "image"

    if typ == "video":
        st["media"]["video"]["me" if is_me else "them"] += 1
        rel = _hash_web_rel(SUB_VID, h, ".mp4")
        dst = _abspath(rel)
        if os.path.exists(dst):
            return rel, "video"
        _ensure_parent(dst)
        if FFMPEG:
            try:
                subprocess.run([FFMPEG, "-y", "-i", src,
                                "-vf", "scale='min(1280,iw)':'-2'",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(dst) and os.path.getsize(dst) > 0:
                    return rel, "video"
            except Exception:
                pass
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except OSError:
                    pass
        rel = _hash_web_rel(SUB_VID, h, ext)
        dst = _abspath(rel)
        if not os.path.exists(dst):
            _ensure_parent(dst)
            shutil.copy2(src, dst)
        return rel, "video"

    st["media"]["other"]["me" if is_me else "them"] += 1
    rel = _hash_web_rel(SUB_OTH, h, ext)
    dst = _abspath(rel)
    if not os.path.exists(dst):
        _ensure_parent(dst)
        shutil.copy2(src, dst)
    return rel, "other"


def new_stats() -> dict:
    """Fresh stats container for one processing run."""
    return {
        "msgs_total": 0, "msgs_me": 0, "msgs_them": 0,
        "media": {t: {"me": 0, "them": 0} for t in ("image", "video", "audio", "other")},
        "bytes": {t: 0 for t in ("image", "video", "audio", "other")},
        "bytes_orig": 0,
        "first": None, "last": None,
    }
