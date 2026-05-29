#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Image OCR (screenshots & documents).

Reads every image under  media/images/<prefix>/<hash>.<ext>  with the
available OCR engine (macOS Vision / Tesseract / null fallback — see
msgviz.core.ocr) and writes INCREMENTALLY to
  data/ocr.json  { "<rel-path>": {"text": "...", "lines": N, "is_screenshot": bool}, ... }

The key is the project-relative web path, EXACTLY as in media.src (so
the frontend matches it the same way as transcripts.json).

Detection logic:
- The OCR engine returns (text, lines).
- "is_screenshot" = True when lines >= MIN_LINES AND chars >= MIN_CHARS.
- Conservative thresholds (8 lines / 120 chars) — a photo with a sign
  is still detected as "has text", but NOT flagged as a screenshot.
  Both land in ocr.json; the display button appears as soon as `text`
  is non-empty.

Engine selection:
- Default: auto-detect (Vision on macOS, else Tesseract, else null).
- Override: env `MSGVIZ_OCR_ENGINE=vision|tesseract|null`.

Usage:
  msgviz ocr                                # incremental, all media
  msgviz ocr --chat my_mac/wa_bob
  msgviz ocr --limit 50                     # quick check
"""
import os, sys, re, json, glob, time, subprocess, argparse, unicodedata

from msgviz.paths import project_root as _project_root
from msgviz.core.ocr import get_engine
ROOT = str(_project_root())
MEDIA_ROOT = os.path.join(ROOT, "media")
DATA_DIR = os.path.join(ROOT, "data")
OCR_FILE = os.path.join(DATA_DIR, "ocr.json")
DB_FILE = os.path.join(ROOT, "data", "visualizer.db")

IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp")
MIN_LINES = int(os.environ.get("OCR_MIN_LINES", "8"))
MIN_CHARS = int(os.environ.get("OCR_MIN_CHARS", "120"))


def load_existing():
    if os.path.exists(OCR_FILE):
        try:
            with open(OCR_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def find_images(chat=None):
    """All image media; key = project-relative path (like media.src).

    In the hash layout the path no longer encodes the slug; the chat
    filter goes via visualizer.db.
    """
    files = []
    if chat:
        if not os.path.isfile(DB_FILE):
            return []
        import sqlite3
        con = sqlite3.connect(DB_FILE)
        rows = con.execute(
            """SELECT m.src FROM media m
               JOIN message msg ON msg.id = m.message_id
               JOIN chat c ON c.id = msg.chat_id
               WHERE c.slug = ?
                 AND m.kind = 'image'
                 AND m.src IS NOT NULL""",
            (chat,)).fetchall()
        con.close()
        for (rel,) in rows:
            abs_p = os.path.join(ROOT, rel)
            if os.path.isfile(abs_p) and abs_p.lower().endswith(IMG_EXTS):
                files.append((rel, abs_p))
    else:
        pat = os.path.join(MEDIA_ROOT, "images", "*", "*")
        for p in glob.glob(pat):
            if not p.lower().endswith(IMG_EXTS):
                continue
            rel = os.path.relpath(p, ROOT)
            files.append((rel, p))
    return sorted(files)


def is_text_dominant(text, lines):
    """True if the recognized text is clearly real writing (not OCR noise)."""
    if not text or lines < 2:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    # Share of Latin letters (otherwise possibly sticker/emoji noise).
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin / len(letters) >= 0.5


def ocr_one(path, engine=None):
    """One image file -> (text, lines). Raises RuntimeError on engine error.

    `engine` is an OCREngine instance. If None: auto-detect via get_engine().
    """
    if engine is None:
        engine = get_engine()
    return engine.recognize(path)


def run(chat=None, limit=None, reporter_phase=None, engine=None):
    if engine is None:
        engine = get_engine()
    if engine.name == "null":
        msg = ("No OCR engine available. macOS: build tools/ocr/ocr "
               "(swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr) — Linux: "
               "pip install 'msgviz[ocr-tesseract]' + apt install tesseract-ocr "
               "tesseract-ocr-deu tesseract-ocr-eng.")
        if reporter_phase: reporter_phase.note(msg)
        else: print(msg)
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    ocr = load_existing()
    images = find_images(chat)
    todo = [(rel, p) for (rel, p) in images if rel not in ocr]
    if limit:
        todo = todo[:limit]

    already = len(images) - len([1 for r, _ in images if r not in ocr])
    info = (f"Images total: {len(images)} | done: {already} "
            f"| open: {len(images)-already} | this run: {len(todo)} "
            f"| engine: {engine.name}"
            + (f" | chat: {chat}" if chat else ""))
    if reporter_phase:
        reporter_phase.set_total(len(todo))
        reporter_phase.note(info)
    else:
        print(info)
    if not todo:
        if reporter_phase: reporter_phase.note("nothing to do")
        else: print("Nothing to do.")
        return

    t0 = time.time()
    n_text = n_screenshot = 0
    for i, (rel, path) in enumerate(todo, 1):
        try:
            text, lines = ocr_one(path, engine=engine)
            chars = sum(len(s.strip()) for s in text.splitlines())
            has_text = is_text_dominant(text, lines)
            is_screenshot = has_text and (lines >= MIN_LINES) and (chars >= MIN_CHARS)
            rec = {"lines": lines, "is_screenshot": is_screenshot}
            if has_text:
                rec["text"] = text
                n_text += 1
            if is_screenshot:
                n_screenshot += 1
            ocr[rel] = rec
            preview = (text[:55] + "…") if len(text) > 55 else (text or "[no text]")
            tag = "SCR" if is_screenshot else ("TXT" if has_text else "—")
            if reporter_phase:
                reporter_phase.note(f"{tag} lines={lines:3d} {os.path.basename(path)}: {preview}")
            else:
                print(f"[{i}/{len(todo)}] {tag} lines={lines:3d} {os.path.basename(path):20s} {preview!r}")
        except Exception as e:
            ocr[rel] = {"lines": 0, "is_screenshot": False, "error": str(e)}
            if reporter_phase:
                reporter_phase.note(f"ERROR {os.path.basename(path)}: {e}")
            else:
                print(f"[{i}/{len(todo)}] ERROR {rel}: {e}")
        if reporter_phase: reporter_phase.tick()
        if i % 20 == 0 or i == len(todo):
            # Race-safe: re-load, merge, atomic replace via per-PID .tmp.
            existing = load_existing()
            existing.update(ocr)
            ocr = existing
            tmp = f"{OCR_FILE}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ocr, f, ensure_ascii=False, indent=0)
            os.replace(tmp, OCR_FILE)
    dt = time.time() - t0
    summary = f"{len(todo)} images in {dt:.0f}s | text: {n_text} | screenshots: {n_screenshot}"
    if reporter_phase: reporter_phase.note(summary)
    else:
        print(f"\nDone: {summary}")
        print(f"  -> {OCR_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(chat=a.chat, limit=a.limit)
