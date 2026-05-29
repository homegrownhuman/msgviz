"""
Spec for the content-hash-based media layout.

Expected behavior:

- Path schema: media/<kind>/<prefix>/<hash>.<ext>
  - <kind>     ∈ {images, videos, audio, files}
  - <prefix>   = first 2 hex chars of the hash
  - <hash>     = 16-hex SHA-256 of the ORIGINAL file (before conversion)
  - <ext>      = extension of the converted output file

- Originals: originals/<prefix>/<hash>.<orig_ext>

- Determinism / idempotency: calling twice with the same source produces
  the same output path. If the hash path already exists, no conversion
  happens.

- Cross-source dedup: two different source files with IDENTICAL content
  end up at the same path and are not stored twice.

- Slug does NOT appear in the path.
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


HASH_RE = re.compile(r"^media/(images|videos|audio|files)/[0-9a-f]{2}/[0-9a-f]{16}\.[a-z0-9]+$")
ORIG_HASH_RE = re.compile(r"^originals/[0-9a-f]{2}/[0-9a-f]{16}\.[a-z0-9]+$")


@pytest.fixture
def hash_export(tmp_path, monkeypatch):
    """Configure export_data to write into tmpdir.

    The media logic lives in `mediakit.process`. We patch the real module
    attributes there (otherwise the patch has no effect, because
    `process_asset` reads `mediakit.process.MEDIA_ROOT`, not the re-exported
    attribute on `export_data`).
    """
    from msgviz.legacy import export_data as ex
    import msgviz.mediakit.process as mp
    monkeypatch.setattr(mp, "OUT", str(tmp_path))
    monkeypatch.setattr(mp, "MEDIA_ROOT", "media")
    monkeypatch.setattr(mp, "ORIG_ROOT", "originals")
    monkeypatch.setattr(mp, "FAST", False)
    # Tests reach for `ex.<symbol>` — re-export symbols are constant imports,
    # so we return `ex` with an `OUT` mirror to keep older test calls like
    # `Path(hash_export.OUT)` working.
    monkeypatch.setattr(ex, "OUT", str(tmp_path))
    return ex


def _process_png(ex, src):
    """Helper: run process_asset on a PNG file."""
    st = ex.new_stats()
    # idx is ignored in the new layout; we keep the transition API though.
    rel, typ = ex.process_asset(
        src=str(src), idx=999, mime="image/png", transfer_name=src.name,
        is_sticker=False, slug="ignored/ignored", st=st, is_me=False)
    return rel, typ


def test_png_lands_in_hash_layout(hash_export, sample_imgs_dir):
    src = sample_imgs_dir / "sample.png"
    rel, typ = _process_png(hash_export, src)
    assert typ == "image"
    assert HASH_RE.match(rel), f"Path does not match the hash schema: {rel}"
    # File exists on disk.
    full = Path(hash_export.OUT) / rel
    assert full.is_file()


def test_idempotent_same_path(hash_export, sample_imgs_dir):
    """Calling twice → identical path."""
    src = sample_imgs_dir / "sample.png"
    rel1, _ = _process_png(hash_export, src)
    rel2, _ = _process_png(hash_export, src)
    assert rel1 == rel2


def test_dedup_byte_identical_sources(hash_export, sample_imgs_dir):
    """Two different source paths with byte-identical content → one hash path."""
    src_a = sample_imgs_dir / "sample.heic"
    src_b = sample_imgs_dir / "same_as_sample.heic"
    # Sanity: actually identical.
    assert src_a.read_bytes() == src_b.read_bytes()

    st = hash_export.new_stats()
    rel_a, _ = hash_export.process_asset(
        str(src_a), 1, "image/heic", src_a.name, False, "x/x", st, False)
    rel_b, _ = hash_export.process_asset(
        str(src_b), 2, "image/heic", src_b.name, False, "y/y", st, False)
    assert rel_a == rel_b, "byte-identical sources must produce the same path"


def test_audio_lands_in_audio_subdir(hash_export, sample_whatsapp_dir):
    """opus → m4a, under the audio subtree of the hash layout."""
    src = sample_whatsapp_dir / "00000002-AUDIO-2018-05-10-12-31-00.opus"
    st = hash_export.new_stats()
    rel, typ = hash_export.process_asset(
        str(src), 42, "audio/opus", src.name, False, "z/z", st, False)
    assert typ == "audio"
    assert HASH_RE.match(rel), f"Audio path does not match hash schema: {rel}"
    assert rel.startswith("media/audio/")
    assert rel.endswith(".m4a"), f"Audio should be stored as .m4a, was: {rel}"


def test_slug_does_not_appear_in_path(hash_export, sample_imgs_dir):
    """The slug parameter must not influence the output path."""
    src = sample_imgs_dir / "sample.jpg"
    st = hash_export.new_stats()
    rel_a, _ = hash_export.process_asset(
        str(src), 1, "image/jpeg", src.name, False, "device_a/chat_a", st, False)
    rel_b, _ = hash_export.process_asset(
        str(src), 1, "image/jpeg", src.name, False, "totally_other/dev/chat", st, False)
    assert rel_a == rel_b


def test_original_in_hash_layout(hash_export, sample_imgs_dir):
    """The original lands in the hash-based originals layout."""
    src = sample_imgs_dir / "sample.heic"
    st = hash_export.new_stats()
    hash_export.process_asset(
        str(src), 1, "image/heic", src.name, False, "x/x", st, False)
    # Expected original layout: originals/<prefix>/<hash>.heic
    orig_dir = Path(hash_export.OUT) / "originals"
    assert orig_dir.is_dir(), "originals/ does not exist"
    # Exactly ONE file should be present.
    files = list(orig_dir.rglob("*.heic"))
    assert len(files) == 1, f"expected 1 original, found {len(files)}: {files}"
    # Check path form.
    rel = files[0].relative_to(hash_export.OUT).as_posix()
    assert ORIG_HASH_RE.match(rel), f"Original path does not match hash schema: {rel}"
