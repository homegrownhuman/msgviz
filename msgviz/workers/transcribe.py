#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Voice-message transcription.

Transcribes every audio under  media/audio/<prefix>/<hash>.m4a  locally
with whisper.cpp (binary `whisper-cli`, Metal-accelerated on Apple
Silicon) and writes results INCREMENTALLY to
  data/transcripts.json   { "<rel-path>": {"text": "...", "lang": "de", "prob": ..}, ... }

The key is the project-relative web path, EXACTLY as in the DB column
media.src (e.g. "media/audio/5f/5f9b78f93a35c320.m4a"), so the frontend
(TRANSCRIPTS[src]) can find it.

Chat filter (--chat <slug>) queries visualizer.db for every audio media
of that chat, because the path itself no longer encodes the chat.

Incremental: existing keys are skipped, so re-running (or re-importing)
only transcribes what is still missing.

Prerequisites:
  * whisper-cli   (brew: whisper-cpp)          -> /opt/homebrew/bin/whisper-cli
  * ggml-large-v3.bin                          -> ~/.whisper-models/
  * ffmpeg                                      (for m4a -> 16kHz mono WAV)

Usage:
  python3 -m workers.transcribe                       # all open audios, all chats
  python3 -m workers.transcribe --chat my_mac/wa_bob  # one chat
  python3 -m workers.transcribe --limit 10            # first 10 only
  WHISPER_MODEL=~/.whisper-models/ggml-large-v3.bin python3 -m workers.transcribe
"""
import os, sys, re, json, glob, time, tempfile, subprocess, unicodedata, argparse

from msgviz.paths import project_root as _project_root
from msgviz.core import whisper as _whisper
ROOT = str(_project_root())
MEDIA_ROOT = os.path.join(ROOT, "media")
DATA_DIR = os.path.join(ROOT, "data")
TRANSCRIPT_FILE = os.path.join(DATA_DIR, "transcripts.json")
DB_FILE = os.path.join(ROOT, "data", "visualizer.db")

# Lazy-resolved in run() — paths can change between calls (env override
# at CLI start) and module import must not fail just because whisper
# isn't installed yet.
LANG = os.environ.get("WHISPER_LANG", "auto")   # 'auto' or e.g. 'de'

AUDIO_EXTS = (".m4a", ".caf", ".mp3", ".wav", ".aac", ".amr", ".opus", ".ogg")
MIN_PROB = float(os.environ.get("WHISPER_MIN_PROB", "0.5"))


def load_existing():
    if os.path.exists(TRANSCRIPT_FILE):
        try:
            with open(TRANSCRIPT_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def find_audio(chat=None):
    """All audio media; key = project-relative path (like media.src).

    In the hash layout the path no longer encodes the chat. If a chat
    filter is requested, we ask visualizer.db for exactly those audio
    `src` paths. Without a filter we glob the filesystem for everything
    under media/audio/.
    """
    files = []
    if chat:
        # Via the DB: every audio media for this chat slug.
        if not os.path.isfile(DB_FILE):
            return []
        import sqlite3
        con = sqlite3.connect(DB_FILE)
        rows = con.execute(
            """SELECT m.src FROM media m
               JOIN message msg ON msg.id = m.message_id
               JOIN chat c ON c.id = msg.chat_id
               WHERE c.slug = ?
                 AND m.kind = 'audio'
                 AND m.src IS NOT NULL""",
            (chat,)).fetchall()
        con.close()
        for (rel,) in rows:
            abs_p = os.path.join(ROOT, rel)
            if os.path.isfile(abs_p) and abs_p.lower().endswith(AUDIO_EXTS):
                files.append((rel, abs_p))
    else:
        # Without filter: walk every audio in the hash layout.
        pat = os.path.join(MEDIA_ROOT, "audio", "*", "*")
        for p in glob.glob(pat):
            if not p.lower().endswith(AUDIO_EXTS):
                continue
            rel = os.path.relpath(p, ROOT)
            files.append((rel, p))
    return sorted(files)


def sanitize(text, lang, prob):
    """Filter whisper hallucinations. Returns (cleaned_text, note)."""
    if not text:
        return "", "empty"
    if lang in ("nn", "no", "") and prob < 0.6:
        return "", "no clear language (hallucination)"
    if prob < 0.45:
        return "", f"uncertain ({lang} {prob:.0%}, likely noise)"
    letters = [c for c in text if c.isalpha()]
    if letters:
        latin = sum(1 for c in letters if 'LATIN' in unicodedata.name(c, ''))
        if latin / len(letters) < 0.5:
            return "", "non-latin characters (hallucination)"
    wordchars = re.sub(r'[^A-Za-zÀ-ÿ0-9]', '', text)
    if len(wordchars) < 3:
        return "", "too short / unintelligible"
    note = None
    if prob < MIN_PROB:
        note = f"uncertain recognition (language {lang}, {prob:.0%})"
    return text, note


def transcribe_one(path, paths=None):
    """One audio file -> (text, lang). Raises on error.

    `paths` is an optional WhisperPaths instance; resolved if None.
    """
    if paths is None:
        paths = _whisper.resolve()
        if not paths.is_complete():
            raise RuntimeError(
                "whisper: not all paths resolved: " + ", ".join(paths.missing())
            )
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        subprocess.run([str(paths.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", path, "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", wav],
                       check=True)
        of = os.path.join(td, "out")
        subprocess.run([str(paths.whisper_cli), "-m", str(paths.model),
                        "-l", LANG, "-nt", "-oj", "-of", of, wav],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(of + ".json", encoding="utf-8") as f:
            data = json.load(f)
        lang = (data.get("result") or {}).get("language", "") or ""
        text = " ".join(seg.get("text", "").strip()
                        for seg in data.get("transcription", [])).strip()
        return text, lang


def run(chat=None, limit=None, reporter_phase=None):
    """Incremental transcription.

    reporter_phase: optional PhaseHandle for set_total/tick/note. If
    None, behaves as before (print logs).
    """
    paths = _whisper.resolve()
    if not paths.is_complete():
        msg = ("Whisper setup incomplete — missing: "
               + ", ".join(paths.missing())
               + "\n\n" + _whisper.setup_hint())
        if reporter_phase: reporter_phase.note(msg)
        else: print(msg, file=sys.stderr)
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    transcripts = load_existing()
    audio = find_audio(chat)
    todo = [(rel, p) for (rel, p) in audio if rel not in transcripts]
    if limit:
        todo = todo[:limit]

    info_line = (f"Audio total: {len(audio)} | done: "
                 f"{len(audio)-len([1 for r,_ in audio if r not in transcripts])} "
                 f"| open: {len([1 for r,_ in audio if r not in transcripts])} "
                 f"| this run: {len(todo)}"
                 + (f" | chat: {chat}" if chat else ""))
    if reporter_phase:
        reporter_phase.set_total(len(todo))
        reporter_phase.note(info_line)
    else:
        print(info_line)
    if not todo:
        if reporter_phase: reporter_phase.note("nothing to do")
        else: print("Nothing to do.")
        return

    t0 = time.time()
    done = 0
    for i, (rel, path) in enumerate(todo, 1):
        try:
            text, lang = transcribe_one(path, paths=paths)
            prob = 1.0
            text, note = sanitize(text, lang, prob)
            transcripts[rel] = {"text": text, "lang": lang, "prob": prob}
            if note:
                transcripts[rel]["note"] = note
            preview = (text[:60] + "…") if len(text) > 60 else (text or f"[dropped: {note}]")
            if reporter_phase:
                reporter_phase.note(f"{os.path.basename(path)} ({lang}): {preview[:60]}")
            else:
                print(f"[{i}/{len(todo)}] {os.path.basename(path)} ({lang}): {preview}")
            done += 1
        except Exception as e:
            if reporter_phase:
                reporter_phase.note(f"ERROR {os.path.basename(path)}: {e}")
            else:
                print(f"[{i}/{len(todo)}] ERROR {rel}: {e}")
            transcripts[rel] = {"text": "", "lang": "", "error": str(e)}
        if reporter_phase:
            reporter_phase.tick()
        # Atomic + race-safe write: re-read the file first (a parallel
        # run might have written in the meantime), merge our entries,
        # then atomically replace via temp+rename. The .tmp path
        # contains the PID so parallel workers don't rename each other's
        # tmp away.
        existing = load_existing()
        existing.update(transcripts)
        transcripts = existing
        tmp = f"{TRANSCRIPT_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(transcripts, f, ensure_ascii=False, indent=0)
        os.replace(tmp, TRANSCRIPT_FILE)

    dt = time.time() - t0
    summary = f"{done}/{len(todo)} transcribed in {dt:.0f}s"
    if reporter_phase: reporter_phase.note(summary)
    else: print(f"\nDone: {summary} -> {TRANSCRIPT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", default=None, help="only this chat slug, e.g. my_mac/wa_bob")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(chat=a.chat, limit=a.limit)
