# tests/fixtures/

Realistic mini source data for the pytest suite. Everything here is
synthetic (no real chat content) and feeds the importer/sync/media
pipeline in tests.

## Contents

### `sample_chat.db`
SQLite file in Apple's `chat.db` layout (not the v2 schema!). Contains:

- 1 chat: `chat_identifier = "+491701234567"`, `service_name = "iMessage"`
- 1 handle: `id = "+491701234567"`
- 7 messages spread over 2 days, 3 from me + 4 from the other party,
  alternating
- 1 message with an attachment (`MSG-0003`, `cache_has_attachments=1`),
  linked to an `attachment` row (`ATT-0001`, image/jpeg)
- 1 tapback (`TAPBACK-0001`, `associated_message_type=2000`, "Liked" on
  `MSG-0002`)
- 1 edited message (`MSG-0006`, `date_edited` set, `message_summary_info`
  as a placeholder blob)

Apple's date format (`message.date` etc.) is in **nanoseconds since
2001-01-01 UTC**.

Total `message` row count: **8** (7 real + 1 tapback).

### `sample_whatsapp.db`
SQLite file in WhatsApp Desktop's `ChatStorage.sqlite` layout (the
Core Data `ZWA*` tables, **not** the v2 schema). Drives the
`whatsapp_live` adapter tests (`test_whatsapp_db.py`). Contains:

- 2 chat sessions: a 1:1 (`ZSESSIONTYPE=0`, "Alice") and a group
  (`ZSESSIONTYPE=1`, "Dev Team") with 2 `ZWAGROUPMEMBER` rows
- 1:1: 4 messages (2 me / 2 Alice), one image + one voice note,
  each with a `ZWAMEDIAITEM` row
- group: 3 normal messages from Bob / Carol / me (sender resolved via
  `ZWAGROUPMEMBER`, not the group JID) + 1 message with an unknown
  `ZMESSAGETYPE` (=99) carrying text, to exercise the enum-drift path
- declared column types use WhatsApp's real spellings (`TIMESTAMP`,
  `VARCHAR`) so the drift probe's storage-class normalisation is tested

WhatsApp's date format (`ZMESSAGEDATE` etc.) is in **seconds since
2001-01-01 UTC** (unlike iMessage's nanoseconds).

### `sample_whatsapp/`
WhatsApp export folder (in the format the WhatsApp app produces with
"Export chat including media"):

- `_chat.txt` — 10 lines in the German format
  `[DD.MM.YY, HH:MM:SS] Sender: text`, mix of "Owner" and "Testperson".
  Includes:
  - 1 `<attached: ...PHOTO...jpg>` reference
  - 1 `<attached: ...AUDIO...opus>` reference
  - 1 `This message was deleted.` line (retraction test)
- `00000001-PHOTO-2018-05-10-12-30-45.jpg` — valid 10×10 JPEG
- `00000002-AUDIO-2018-05-10-12-31-00.opus` — valid 0.1 s Ogg/Opus
  (silence, 48 kHz mono)

### `sample_imgs/`
Tiny images for hash and layout tests in the media pipeline:

- `sample.png` — 10×10 PNG with alpha channel (crimson)
- `sample.jpg` — 10×10 JPEG (from PNG via `sips`)
- `sample.heic` — valid HEIC (from JPG via `sips -s format heic`)
- `same_as_sample.heic` — byte-identical copy of `sample.heic` (dedup test)

## Rebuilding

```bash
# Rebuild the Apple chat.db fixture (overwrites an existing sample_chat.db):
python3 tests/fixtures/build_sample_chat_db.py

# Rebuild the WhatsApp ChatStorage.sqlite fixture (overwrites sample_whatsapp.db):
python3 tests/fixtures/build_sample_whatsapp_db.py

# Rebuild media files (PNG/JPG/HEIC + WhatsApp photo/opus):
python3 tests/fixtures/build_media_fixtures.py
```

Both scripts use only the Python standard library plus the macOS tools
`sips` (HEIC/JPG) and `ffmpeg` (Opus). If a tool is missing, the script
falls back to static mini dummy files with correct magic bytes
(warning on stderr).

## Replacing

If you need different content, edit the lists at the top of the
respective `build_*.py` scripts (message tuples in
`build_sample_chat_db.py`, media files in `build_media_fixtures.py`)
and rerun. Both scripts are idempotent and overwrite existing files.
