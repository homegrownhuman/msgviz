# -*- coding: utf-8 -*-
"""msgviz person — manage persons."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import typer

from ._helpers import confirm_or_abort, console, die, open_db, render_table

app = typer.Typer(no_args_is_help=True, help="Manage persons.")


# ---------------------------------------------------------------------------
# Avatar storage helpers
# ---------------------------------------------------------------------------
_AVATAR_DIR = "media/avatars"
_HASH_LEN = 16
_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _store_avatar_file(src: Path) -> str:
    """Copy an avatar source file into the project's media/avatars/<prefix>/
    <hash>.<ext> layout. Returns the web-relative path (suitable for
    person.avatar_src and the /media/... static mount).
    """
    from msgviz.paths import project_root

    if not src.is_file():
        die(f"Avatar file not found: {src}")
    ext = src.suffix.lower()
    if ext not in _ALLOWED_EXTS:
        die(f"Unsupported avatar extension {ext!r}. Allowed: "
            f"{sorted(_ALLOWED_EXTS)}")

    # Content-hash for dedup.
    h = hashlib.sha256()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    digest = h.hexdigest()[:_HASH_LEN]
    prefix = digest[:2]
    rel = f"{_AVATAR_DIR}/{prefix}/{digest}{ext}"

    target = project_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(src, target)
    return rel


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Display name of the person."),
    aliases: str = typer.Option(
        "",
        "--aliases",
        "-a",
        help="Comma-separated alternative names (case-insensitive match).",
    ),
    handles: str = typer.Option(
        "",
        "--handles",
        "-H",
        help="Comma-separated handles (phone/email) attached to this person.",
    ),
) -> None:
    """Create a person (+ optional aliases + handles)."""
    with open_db() as con:
        existing = con.execute(
            "SELECT id FROM person WHERE display_name = ?", (name,)
        ).fetchone()
        if existing:
            die(f"Person '{name}' already exists (id={existing[0]}).")
        pid = con.execute(
            "INSERT INTO person(display_name) VALUES(?)", (name,)
        ).lastrowid

        n_alias = 0
        for a in (s.strip() for s in aliases.split(",") if s.strip()):
            try:
                con.execute(
                    "INSERT INTO person_alias(value, person_id) VALUES(?, ?)", (a, pid)
                )
                n_alias += 1
            except Exception as e:
                console.print(f"[yellow]Alias skipped ({a}):[/yellow] {e}")

        n_handle = 0
        for h in (s.strip() for s in handles.split(",") if s.strip()):
            try:
                con.execute(
                    "INSERT INTO handle(value, person_id) VALUES(?, ?)", (h, pid)
                )
                n_handle += 1
            except Exception as e:
                console.print(f"[yellow]Handle skipped ({h}):[/yellow] {e}")

        con.commit()
    console.print(
        f"[green]Person created:[/green] {name} (id={pid}) "
        f"– {n_alias} aliases, {n_handle} handles"
    )


@app.command("set-avatar")
def set_avatar(
    name: str = typer.Argument(..., help="Display name of the person (must already exist)."),
    image: Path = typer.Argument(
        ...,
        help="Path to an image file (jpg/png/gif/webp).",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
) -> None:
    """Assign an avatar image to a person.

    The file is hashed and copied into <root>/media/avatars/<prefix>/
    <hash>.<ext>. Re-running with the same file is idempotent.
    """
    rel = _store_avatar_file(image)
    with open_db() as con:
        row = con.execute(
            "SELECT id FROM person WHERE display_name = ?", (name,)
        ).fetchone()
        if row is None:
            die(f"Person '{name}' not found. Create it first with `msgviz person add`.")
        con.execute(
            "UPDATE person SET avatar_src = ? WHERE id = ?", (rel, row[0])
        )
        con.commit()
    console.print(f"[green]Avatar set:[/green] {name} → {rel}")


@app.command("clear-avatar")
def clear_avatar(
    name: str = typer.Argument(..., help="Display name of the person."),
) -> None:
    """Remove a person's avatar (frontend falls back to initials)."""
    with open_db() as con:
        row = con.execute(
            "SELECT id, avatar_src FROM person WHERE display_name = ?", (name,)
        ).fetchone()
        if row is None:
            die(f"Person '{name}' not found.")
        if not row[1]:
            console.print(f"[dim]{name} has no avatar.[/dim]")
            return
        con.execute("UPDATE person SET avatar_src = NULL WHERE id = ?", (row[0],))
        con.commit()
    console.print(f"[green]Avatar cleared:[/green] {name}")


@app.command("import-avatars")
def import_avatars(
    source: str = typer.Option(
        "addressbook",
        "--from",
        help="Source of avatar images. Currently only 'addressbook' is "
             "recognized; not yet implemented.",
    ),
) -> None:
    """[NOT IMPLEMENTED YET] Import avatars from the system Address Book.

    Matches DB persons to Contacts.app entries by phone/email handle and
    copies the avatar image into the local media store.

    macOS-specific. Currently a stub — use `msgviz person set-avatar` for
    individual people or `msgviz person auto-avatars` for fallback
    initials avatars.
    """
    die(
        f"`import-avatars --from {source}` is not implemented yet. "
        "Use `msgviz person set-avatar <name> <file>` for individual avatars "
        "or `msgviz person auto-avatars` for initials avatars."
    )


@app.command("auto-avatars")
def auto_avatars(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Only generate for persons without an avatar (default), or for all.",
    ),
) -> None:
    """Generate initials avatars (colored PNG with monogram) for persons.

    Requires Pillow (`pip install msgviz[dev]`). Produces deterministic
    colors per name so re-runs give the same avatar.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        die("Pillow not installed. `pip install Pillow` or `pip install 'msgviz[dev]'`.")

    with open_db() as con:
        rows = con.execute(
            "SELECT id, display_name, avatar_src FROM person ORDER BY id"
        ).fetchall()

    n_created = 0
    n_skipped = 0
    for row in rows:
        if row["avatar_src"] and only_missing:
            n_skipped += 1
            continue
        rel = _generate_initials_avatar(row["display_name"])
        with open_db() as con:
            con.execute(
                "UPDATE person SET avatar_src = ? WHERE id = ?",
                (rel, row["id"]),
            )
            con.commit()
        console.print(f"  [green]✓[/green] {row['display_name']} → {rel}")
        n_created += 1
    console.print(
        f"\n[green]Done:[/green] {n_created} avatar(s) generated, {n_skipped} skipped."
    )


def _generate_initials_avatar(name: str) -> str:
    """Render a 512×512 PNG with the person's initials on a colored bg.

    Color is hashed from the name → stable across runs.
    """
    from PIL import Image, ImageDraw, ImageFont
    import io
    from msgviz.paths import project_root

    # Color from hash for stable per-name color.
    h = hashlib.md5(name.encode()).digest()
    bg = (h[0] % 180 + 40, h[1] % 180 + 40, h[2] % 180 + 40)
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fg = (255, 255, 255) if lum < 128 else (20, 20, 20)

    # Initials: take first letter of each whitespace-separated word, up to 2.
    parts = [p for p in name.split() if p]
    initials = "".join(p[0] for p in parts[:2]).upper() or "?"

    img = Image.new("RGB", (512, 512), bg)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 240)
    except Exception:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 240)
        except Exception:
            font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), initials, font=font)
    w = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(((512 - w) / 2 - bbox[0], (512 - th) / 2 - bbox[1]),
           initials, font=font, fill=fg)

    # Encode + hash for the content-addressed path.
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()[:_HASH_LEN]
    prefix = digest[:2]
    rel = f"{_AVATAR_DIR}/{prefix}/{digest}.png"

    target = project_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return rel


@app.command("list")
def list_() -> None:
    """List every person with their handle and alias counts."""
    with open_db(readonly=True) as con:
        rows = con.execute(
            """SELECT p.id, p.display_name AS name,
                      (SELECT COUNT(*) FROM handle h WHERE h.person_id = p.id) AS handles,
                      (SELECT COUNT(*) FROM person_alias a WHERE a.person_id = p.id) AS aliases,
                      CASE WHEN p.avatar_src IS NULL THEN '' ELSE '✓' END AS avatar
               FROM person p
               ORDER BY p.display_name COLLATE NOCASE"""
        ).fetchall()
    render_table("Persons", [dict(r) for r in rows])


@app.command("migrate-from-sources")
def migrate_from_sources(
    sources_path: str = typer.Option(
        None,
        "--sources",
        "-s",
        help="Path to sources.json (default: <config_dir>/sources.json).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only print what would happen. No changes."
    ),
    remove_from_sources: bool = typer.Option(
        False,
        "--remove-from-sources",
        help="After a successful import: drop the 'people' key from sources.json.",
    ),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip the safety copy."),
) -> None:
    """Migrate the 'people' map from sources.json into the DB.

    Phase 0.7: sources.json should only declare devices/chats. Persons
    move into the DB tables 'person' + 'handle' + 'person_alias'. This
    command is the explicit, one-shot migration path.
    """
    import json
    from pathlib import Path

    from msgviz.paths import config_dir

    src = Path(sources_path) if sources_path else (config_dir() / "sources.json")
    if not src.is_file():
        die(f"sources.json not found: {src}")

    cfg = json.loads(src.read_text(encoding="utf-8"))
    people_map = cfg.get("people") or {}
    if not people_map:
        console.print(f"[dim]No 'people' map in {src} — nothing to do.[/dim]")
        return

    console.print(f"[bold]'people' map in {src}:[/bold] {len(people_map)} entries")
    def _existing_handle_pid(con, handle_value: str):
        """Direct DB lookup — NO side effects (resolve_handle would
        otherwise create new persons)."""
        from msgviz.core.person_resolver import norm_handle

        nv = norm_handle(handle_value)
        row = con.execute(
            "SELECT person_id FROM handle WHERE value=?", (nv,)
        ).fetchone()
        return row[0] if row else None

    if dry_run:
        console.print("[yellow]--dry-run: no changes.[/yellow]")
        with open_db(readonly=True) as con:
            for handle, name in people_map.items():
                existing = _existing_handle_pid(con, handle)
                if existing:
                    console.print(
                        f"  [dim]ok[/dim]   {handle} -> {name} (person {existing} already present)"
                    )
                else:
                    console.print(f"  [green]new[/green]  {handle} -> {name} (would be created)")
        return

    if not no_backup:
        from msgviz.core.backup import backup_db
        bk = backup_db("person-migrate-from-sources")
        if bk is not None:
            console.print(f"[dim]DB backup -> {bk}[/dim]")

    from msgviz.core.person_resolver import PersonResolver

    n_new = 0
    n_existing = 0
    with open_db() as con:
        res = PersonResolver(con)
        for handle, name in people_map.items():
            existing = _existing_handle_pid(con, handle)
            if existing is not None:
                n_existing += 1
                continue
            pid = res.resolve_name(name)
            res.add_handle(handle, pid)
            n_new += 1
        con.commit()

    console.print(
        f"[green]Imported:[/green] {n_new} new handle links, "
        f"{n_existing} already present."
    )

    if remove_from_sources:
        backup = src.with_suffix(".json.bak-pre-people-removal")
        backup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        cfg.pop("people", None)
        src.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]'people' map removed.[/green] sources.json backup -> {backup}")
    else:
        console.print(
            "[yellow]'people' map kept in sources.json[/yellow] — drop it with "
            "[bold]--remove-from-sources[/bold]."
        )


@app.command("merge")
def merge(
    keep_id: int = typer.Argument(..., help="ID of the person to keep."),
    drop_id: int = typer.Argument(..., help="ID of the person to merge into keep."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip the safety copy."),
) -> None:
    """Merge two persons — every handle, alias and message of `drop`
    moves to `keep`, then `drop` is deleted.
    """
    if keep_id == drop_id:
        die("keep and drop must be different.")
    if not no_backup:
        from msgviz.core.backup import backup_db
        bk = backup_db(f"person-merge-{drop_id}-into-{keep_id}")
        if bk is not None:
            console.print(f"[dim]Backup -> {bk}[/dim]")
    with open_db() as con:
        keep = con.execute(
            "SELECT display_name FROM person WHERE id = ?", (keep_id,)
        ).fetchone()
        drop = con.execute(
            "SELECT display_name FROM person WHERE id = ?", (drop_id,)
        ).fetchone()
        if keep is None or drop is None:
            die("One of the IDs does not exist.")
        if not yes:
            confirm_or_abort(
                f"Merge '{drop[0]}' (id={drop_id}) -> '{keep[0]}' (id={keep_id}). Continue?"
            )
        con.execute("UPDATE handle SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
        con.execute(
            "UPDATE person_alias SET person_id = ? WHERE person_id = ?", (keep_id, drop_id)
        )
        # Keep the dropped display_name as an alias.
        con.execute(
            "INSERT OR IGNORE INTO person_alias(value, person_id) VALUES(?, ?)",
            (drop[0], keep_id),
        )
        # Messages: from_person_id and to_person_id where present.
        try:
            con.execute(
                "UPDATE message SET sender_person_id = ? WHERE sender_person_id = ?",
                (keep_id, drop_id),
            )
        except Exception:
            pass  # column might be named differently depending on schema state
        con.execute("DELETE FROM person WHERE id = ?", (drop_id,))
        con.commit()
    console.print(f"[green]Merge OK:[/green] {drop[0]} -> {keep[0]}")
