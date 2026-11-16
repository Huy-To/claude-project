"""The Notes view: read and write any Markdown note in the Obsidian vault, like Obsidian does.

The notes are the vault's own .md files, so Obsidian and btcliq edit the same thing. New notes go
into a Notes folder by default; any note in the vault can be opened, edited, renamed or moved to
the vault's .trash folder (Obsidian's "move to .trash" setting, so nothing is erased).

Rules that keep this safe:
- every path is relative to the vault and must stay inside it: no absolute paths, no "..",
  no hidden folders such as .obsidian (except the move into .trash),
- notes must end in .md; attachments (pasted images) are images, kept in Notes/attachments,
- every save names the version it was based on (a hash of the file). If the file changed since,
  for example because Obsidian saved it, the save is refused with the current text so the page
  can ask which one to keep. Writes go through a temp file, so a crash never leaves half a note.

The chat can search and read the notes in the Notes folder (lookups notes_search, notes_read).
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .vault import parse_note

NOTES_DIR = "Notes"
ATTACH_DIR = "Notes/attachments"
SCRATCH = "Notes/Scratch.md"
TRASH_DIR = ".trash"
MAX_NOTE_BYTES = 2_000_000
MAX_ATTACH_BYTES = 15_000_000
MAX_READ_CHARS = 9_000                   # one chat read returns at most this much
FILE_TYPES = {                           # what the page may show as an embed (![[file]])
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".avif": "image/avif",
    ".pdf": "application/pdf",
}
IMAGE_TYPES = {k: v for k, v in FILE_TYPES.items() if v.startswith("image/") and k != ".svg"}
BAD_NAME = re.compile(r'[\\:*?"<>|\x00-\x1f]')


class NoteError(ValueError):
    """Something the user can fix: a bad name, a missing note, a note that already exists."""


class Conflict(Exception):
    """The file changed on disk since the page loaded it."""

    def __init__(self, current: dict | None):
        super().__init__("This note changed on disk since you opened it")
        self.current = current


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


@dataclass
class Notebook:
    root: Path | None

    @property
    def ok(self) -> bool:
        return self.root is not None and self.root.is_dir()

    # ---------- paths ----------

    def rel(self, path: str, *, kind: str = "note") -> PurePosixPath:
        """A clean vault-relative path, or NoteError. kind: note (.md) or file (an attachment)."""
        if not self.ok:
            raise NoteError("no Obsidian vault is connected (start btcliq with --vault)")
        s = str(path or "").replace("\\", "/").strip()
        if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
            raise NoteError("note paths stay inside the vault")
        s = s.strip("/")
        if not s:
            raise NoteError("a note needs a name")
        p = PurePosixPath(s)
        if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
            raise NoteError("note paths stay inside the vault")
        if any(part.startswith(".") for part in p.parts):
            raise NoteError("hidden folders (like .obsidian) are not notes")
        if any(BAD_NAME.search(part) for part in p.parts):
            raise NoteError('names cannot contain \\ : * ? " < > |')
        if kind == "note" and p.suffix.lower() != ".md":
            raise NoteError("notes are .md files")
        if kind == "file" and p.suffix.lower() not in FILE_TYPES:
            raise NoteError("only images and PDFs can be embedded")
        return p

    def full(self, rel: PurePosixPath) -> Path:
        f = (self.root / rel).resolve()
        root = self.root.resolve()
        if f != root and root not in f.parents:          # a symlink pointing out of the vault
            raise NoteError("note paths stay inside the vault")
        return f

    # ---------- listing ----------

    def tree(self) -> dict:
        """Every note and embeddable file, sorted, with folders. Hidden folders are skipped."""
        if not self.ok:
            return {"ok": False, "notes": [], "files": [], "folders": []}
        notes, files, folders = [], [], set()
        root = self.root
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            here = Path(dirpath)
            rel_dir = here.relative_to(root).as_posix()
            if rel_dir != ".":
                folders.add(rel_dir)
            for n in names:
                if n.startswith("."):
                    continue
                f = here / n
                ext = f.suffix.lower()
                if ext != ".md" and ext not in FILE_TYPES:
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                rel = f.relative_to(root).as_posix()
                row = {"path": rel, "name": f.stem if ext == ".md" else f.name, "mtime": int(st.st_mtime * 1000),
                       "size": st.st_size}
                (notes if ext == ".md" else files).append(row)
        notes.sort(key=lambda r: r["path"].lower())
        files.sort(key=lambda r: r["path"].lower())
        return {"ok": True, "name": root.name, "notes": notes, "files": files, "folders": sorted(folders, key=str.lower),
                "notes_dir": NOTES_DIR, "scratch": SCRATCH}

    # ---------- one note ----------

    def read(self, path: str) -> dict:
        rel = self.rel(path)
        f = self.full(rel)
        if not f.is_file():
            raise FileNotFoundError(str(rel))
        data = f.read_bytes()
        return {"path": rel.as_posix(), "name": rel.stem, "text": data.decode("utf-8", errors="replace"),
                "hash": digest(data), "mtime": int(f.stat().st_mtime * 1000)}

    def _current(self, f: Path, rel: PurePosixPath) -> dict | None:
        try:
            return self.read(rel.as_posix()) if f.is_file() else None
        except OSError:
            return None

    def write(self, path: str, text: str, base: str | None, *, create: bool = False) -> dict:
        """Save a note. base is the hash the page loaded (None for a new note).

        create=True refuses to overwrite an existing file. Otherwise the save goes through only if
        the file on disk still has hash `base` (or is missing and base is None)."""
        rel = self.rel(path)
        f = self.full(rel)
        data = str(text).encode("utf-8")
        if len(data) > MAX_NOTE_BYTES:
            raise NoteError(f"a note can be at most {MAX_NOTE_BYTES // 1_000_000} MB")
        if f.exists():
            if not f.is_file():
                raise NoteError(f"{rel} is a folder")
            if create:
                raise NoteError(f"a note named {rel.as_posix()} already exists")
            cur = f.read_bytes()
            if digest(cur) != base:
                raise Conflict(self._current(f, rel))
        elif base is not None and not create:
            raise Conflict(None)            # it was deleted or renamed elsewhere
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.btcliq-tmp")
        tmp.write_bytes(data)
        tmp.replace(f)
        return {"path": rel.as_posix(), "name": rel.stem, "hash": digest(data), "mtime": int(f.stat().st_mtime * 1000)}

    def rename(self, src: str, dst: str) -> dict:
        a, b = self.rel(src), self.rel(dst)
        fa, fb = self.full(a), self.full(b)
        if not fa.is_file():
            raise FileNotFoundError(str(a))
        if fb.exists() and fa.resolve() != fb.resolve():
            raise NoteError(f"a note named {b.as_posix()} already exists")
        fb.parent.mkdir(parents=True, exist_ok=True)
        fa.replace(fb)
        return {"path": b.as_posix(), "name": b.stem}

    def trash(self, path: str) -> dict:
        """Move a note to the vault's .trash folder, as Obsidian does. Same name twice gets a suffix."""
        rel = self.rel(path)
        f = self.full(rel)
        if not f.is_file():
            raise FileNotFoundError(str(rel))
        dest_dir = self.root / TRASH_DIR
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / f.name
        n = 1
        while dest.exists():
            dest = dest_dir / f"{f.stem} {n}{f.suffix}"
            n += 1
        f.replace(dest)
        return {"path": rel.as_posix(), "trashed_as": dest.relative_to(self.root).as_posix()}

    # ---------- attachments ----------

    def file(self, path: str) -> tuple[Path, str]:
        rel = self.rel(path, kind="file")
        f = self.full(rel)
        if not f.is_file():
            raise FileNotFoundError(str(rel))
        return f, FILE_TYPES[rel.suffix.lower()]

    def attach(self, data: bytes, ext: str, name: str | None = None) -> dict:
        """Save a pasted or dropped image into Notes/attachments, named like Obsidian names them."""
        ext = "." + str(ext or "").lower().lstrip(".")
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in IMAGE_TYPES:
            raise NoteError("only PNG, JPEG, GIF, WebP, BMP or AVIF images can be pasted")
        if len(data) > MAX_ATTACH_BYTES:
            raise NoteError(f"an image can be at most {MAX_ATTACH_BYTES // 1_000_000} MB")
        stem = BAD_NAME.sub("", Path(name or "").stem).strip(" .") if name else ""
        stem = stem or "Pasted image " + time.strftime("%Y%m%d%H%M%S")
        rel = self.rel(f"{ATTACH_DIR}/{stem}{ext}", kind="file")
        f = self.full(rel)
        n = 1
        while f.exists():
            rel = self.rel(f"{ATTACH_DIR}/{stem} {n}{ext}", kind="file")
            f = self.full(rel)
            n += 1
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return {"path": rel.as_posix(), "name": f.name}

    # ---------- search and links ----------

    def _walk_md(self, under: str | None = None):
        if not self.ok:
            return
        base = self.root / under if under else self.root
        if not base.is_dir():
            return
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for n in names:
                if n.endswith(".md") and not n.startswith("."):
                    f = Path(dirpath) / n
                    try:
                        yield f.relative_to(self.root).as_posix(), f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue

    def search(self, query: str, limit: int = 40, under: str | None = None) -> list[dict]:
        """Notes whose name or text holds every word of the query; one snippet per note."""
        terms = [t for t in str(query or "").lower().split() if t]
        if not terms:
            return []
        hits = []
        for rel, text in self._walk_md(under):
            low = text.lower()
            name = PurePosixPath(rel).stem.lower()
            if not all(t in low or t in name for t in terms):
                continue
            score = sum(low.count(t) for t in terms) + 5 * sum(t in name for t in terms)
            lines = text.split("\n")
            heading, best = "", None
            for i, line in enumerate(lines):
                if m := re.match(r"^#{1,6}\s+(.*)$", line):
                    heading = m.group(1).strip()
                if any(t in line.lower() for t in terms):
                    best = (i, heading, " ".join(line.split())[:240])
                    break
            hits.append((score, {"path": rel, "name": PurePosixPath(rel).stem,
                                 "line": best[0] if best else None, "section": best[1] if best else "",
                                 "text": best[2] if best else ""}))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    def backlinks(self, path: str) -> list[dict]:
        """Notes that link to this one with [[name]], [[name|alias]], [[name#heading]] or [[folder/name]]."""
        rel = self.rel(path)
        name = rel.stem.lower()
        full = rel.with_suffix("").as_posix().lower()
        out = []
        for other, text in self._walk_md():
            if other == rel.as_posix():
                continue
            for i, line in enumerate(text.split("\n")):
                targets = [m.group(1).split("|")[0].split("#")[0].strip().lower().removesuffix(".md")
                           for m in re.finditer(r"!?\[\[([^\]\n]+)\]\]", line)]
                if any(t == name or t == full or t.endswith("/" + name) and full.endswith(t) for t in targets):
                    out.append({"path": other, "name": PurePosixPath(other).stem, "line": i,
                                "text": " ".join(line.split())[:240]})
                    break
        return out

    # ---------- the chat's view: the Notes folder ----------

    def chat_search(self, query: str, limit: int = 8) -> list[dict]:
        return [{"note": h["name"], "path": h["path"], "section": h["section"], "text": h["text"]}
                for h in self.search(query, limit, under=NOTES_DIR)]

    def chat_list(self) -> list[dict]:
        return [{"note": PurePosixPath(rel).stem, "path": rel, "chars": len(text)} for rel, text in self._walk_md(NOTES_DIR)]

    def chat_read(self, note: str) -> dict:
        want = str(note or "").strip().removesuffix(".md").lower()
        rows = list(self._walk_md(NOTES_DIR))
        hit = (next((r for r in rows if r[0].lower().removesuffix(".md") in (want, f"{NOTES_DIR.lower()}/{want}")), None)
               or next((r for r in rows if PurePosixPath(r[0]).stem.lower() == want), None)
               or next((r for r in rows if want and want in PurePosixPath(r[0]).stem.lower()), None))
        if not hit:
            return {"error": f"no note named {note!r} in the {NOTES_DIR} folder",
                    "notes": [PurePosixPath(r[0]).stem for r in rows][:60]}
        rel, text = hit
        props, body = parse_note(text)
        out = {"note": PurePosixPath(rel).stem, "path": rel, "text": body.strip()[:MAX_READ_CHARS]}
        if props:
            out["properties"] = {k: v for k, v in props.items()}
        if len(body.strip()) > MAX_READ_CHARS:
            out["cut"] = f"the note is longer than one read; only the first {MAX_READ_CHARS} characters are shown"
        return out
