"""The Obsidian vault: knowledge notes Claude can read, and the pinned notes it always carries.

A note is knowledge when its `categories` property links to [[Knowledge]]. Its `summary`
property is the one line Claude sees up front; the body is read only when a question needs
it. Pinned notes are the bullets in "Pinned notes.md": short, always sent, approved by you.

Nothing here needs Obsidian to be running. The vault is plain Markdown files.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("btcliq")

KNOWLEDGE_CATEGORY = "Knowledge"
PINNED_FILE = "Pinned notes.md"
SKIP_DIRS = {".obsidian", ".trash", ".git", "Templates"}
MAX_NOTE_CHARS = 9_000          # one read returns at most this much; longer notes are read by section
MAX_PINNED = 40                 # the pinned list is sent with every question, so it stays short
MAX_PINNED_CHARS = 400

PINNED_HEADER = """---
categories:
  - "[[Memory]]"
summary: Short notes the chat carries into every conversation. One bullet each.
---
Each bullet below is sent to Claude with every question, so keep them short.
Add, edit or delete bullets here or from the Claude view in btcliq; both stay in step.

"""


# ---------- front matter (the properties block at the top of a note) ----------

def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.lower() in ("null", "~", ""):
        return None
    return v


def parse_note(text: str) -> tuple[dict, str]:
    """(properties, body). Understands what Obsidian writes: scalars, lists and inline lists."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    props: dict = {}
    key = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*-\s+(.*)$", line)
        if m and key is not None:
            if not isinstance(props.get(key), list):
                props[key] = []
            props[key].append(_scalar(m.group(1)))
            continue
        m = re.match(r"^([^\s:#][^:]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            props[key] = [_scalar(x) for x in re.split(r",(?![^\[]*\]\])", val[1:-1]) if x.strip()]
        elif val == "":
            props[key] = None        # a list may follow on the next lines
        else:
            props[key] = _scalar(val)
    return props, "\n".join(lines[end + 1:]).lstrip("\n")


def link_target(v) -> str:
    """'[[Knowledge|what Claude knows]]' -> 'Knowledge'."""
    s = str(v or "").strip()
    m = re.match(r"^\[\[([^\]|#]+)", s)
    return (m.group(1) if m else s).strip()


def categories(props: dict) -> list[str]:
    raw = props.get("categories")
    if raw is None:
        return []
    return [link_target(x) for x in (raw if isinstance(raw, list) else [raw]) if x]


@dataclass
class Note:
    title: str
    path: Path
    props: dict
    body: str
    mtime: float

    @property
    def summary(self) -> str:
        s = self.props.get("summary")
        if isinstance(s, str) and s.strip():
            return " ".join(s.split())
        for line in self.body.split("\n"):      # no summary property: the first line of prose
            line = line.strip()
            if line and not line.startswith(("#", "!", "|", ">", "```", "---")):
                return " ".join(line.split())[:200]
        return ""

    def sections(self) -> list[tuple[int, str, int, int]]:
        """(level, heading, start line, end line) for every heading."""
        lines = self.body.split("\n")
        heads = [(i, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines)
                 if (m := re.match(r"^(#{1,6})\s+(.*)$", l))]
        out = []
        for n, (i, lvl, title) in enumerate(heads):
            end = next((j for j, l2, _ in heads[n + 1:] if l2 <= lvl), len(lines))
            out.append((lvl, title, i, end))
        return out


# ---------- the vault ----------

@dataclass
class Vault:
    path: Path | None
    fallback_dir: Path | None = None      # where pinned notes live when there is no vault
    _cache: dict = field(default_factory=dict)
    _scan: tuple[float, list[Note]] | None = None

    @classmethod
    def find(cls, flag: str | None, project: Path, fallback_dir: Path | None = None) -> "Vault":
        """--vault, then BTCLIQ_VAULT, then a bitVault folder next to the project."""
        for cand in (flag, os.environ.get("BTCLIQ_VAULT"), project.parent / "bitVault"):
            if cand and Path(cand).expanduser().is_dir():
                return cls(Path(cand).expanduser().resolve(), fallback_dir)
        if flag or os.environ.get("BTCLIQ_VAULT"):
            log.warning("Vault folder not found: %s", flag or os.environ.get("BTCLIQ_VAULT"))
        return cls(None, fallback_dir)

    @property
    def ok(self) -> bool:
        return self.path is not None and self.path.is_dir()

    def _load(self, p: Path) -> Note | None:
        try:
            st = p.stat()
            hit = self._cache.get(p)
            if hit and hit[0] == (st.st_mtime, st.st_size):
                return hit[1]
            props, body = parse_note(p.read_text(encoding="utf-8", errors="replace"))
            note = Note(p.stem, p, props, body, st.st_mtime)
            self._cache[p] = ((st.st_mtime, st.st_size), note)
            return note
        except OSError:
            return None

    def notes(self) -> list[Note]:
        """Every knowledge note, re-read only when a file changed. Rescans at most every 2s."""
        if not self.ok:
            return []
        if self._scan and time.time() - self._scan[0] < 2:
            return self._scan[1]
        out = []
        for root, dirs, files in os.walk(self.path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if not f.endswith(".md") or f == PINNED_FILE:
                    continue
                note = self._load(Path(root) / f)
                if note and KNOWLEDGE_CATEGORY in categories(note.props):
                    out.append(note)
        out.sort(key=lambda n: n.title.lower())
        self._scan = (time.time(), out)
        return out

    def index(self) -> list[dict]:
        return [{"note": n.title, "summary": n.summary, "chars": len(n.body),
                 "updated": datetime.fromtimestamp(n.mtime).strftime("%Y-%m-%d")} for n in self.notes()]

    def get(self, title: str) -> Note | None:
        want = link_target(title).lower().removesuffix(".md")
        notes = self.notes()
        return (next((n for n in notes if n.title.lower() == want), None)
                or next((n for n in notes if want in [str(a).lower() for a in (n.props.get("aliases") or [])]), None)
                or next((n for n in notes if want and want in n.title.lower()), None))

    def read(self, title: str, section: str | None = None) -> dict:
        n = self.get(title)
        if not n:
            return {"error": f"no knowledge note named {title!r}", "notes": [x.title for x in self.notes()]}
        lines = n.body.split("\n")
        secs = n.sections()
        top = min((lvl for lvl, *_ in secs), default=1)
        outline = [("  " * (lvl - top)) + t for lvl, t, _, _ in secs]      # indented under their parents
        if section:
            want = section.strip().lower().lstrip("# ")
            hit = (next((s for s in secs if s[1].lower() == want), None)
                   or next((s for s in secs if want in s[1].lower()), None))
            if not hit:
                return {"note": n.title, "error": f"no section matching {section!r}", "sections": outline}
            text = "\n".join(lines[hit[2]:hit[3]]).strip()
            out = {"note": n.title, "section": hit[1], "text": text[:MAX_NOTE_CHARS]}
            if len(text) > MAX_NOTE_CHARS:
                out["cut"] = "section is longer than one read; ask for one of its sub-sections"
                out["sections"] = [("  " * (lvl - hit[0] - 1)) + t for lvl, t, i, _ in secs if hit[2] < i < hit[3]]
            return out
        if len(n.body) <= MAX_NOTE_CHARS:
            return {"note": n.title, "text": n.body.strip()}
        first = secs[0][2] if secs else len(lines)
        return {"note": n.title, "summary": n.summary,
                "text": "\n".join(lines[:max(first, 1)]).strip()[:2000],
                "cut": "this note is long: read it one section at a time with the `section` argument",
                "sections": outline}

    def search(self, query: str, limit: int = 6) -> list[dict]:
        terms = [t for t in re.findall(r"[a-z0-9$%.]+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        hits = []
        for n in self.notes():
            lines = n.body.split("\n")
            heading = ""
            for i, line in enumerate(lines):
                if m := re.match(r"^#{1,6}\s+(.*)$", line):
                    heading = m.group(1).strip()
                low = line.lower()
                score = sum(low.count(t) for t in terms)
                if score:
                    score += 2 * sum(t in n.title.lower() for t in terms) + sum(t in heading.lower() for t in terms)
                    hits.append((score, n.title, heading, " ".join(" ".join(lines[max(0, i - 1):i + 2]).split())[:320]))
        hits.sort(key=lambda h: -h[0])
        seen, out = set(), []
        for score, title, heading, text in hits:
            if (title, heading) in seen:
                continue
            seen.add((title, heading))
            out.append({"note": title, "section": heading, "text": text})
            if len(out) >= limit:
                break
        return out

    # ---------- pinned notes ----------

    def _pinned_path(self) -> Path | None:
        if self.ok:
            return self.path / PINNED_FILE
        return self.fallback_dir / "pinned-notes.md" if self.fallback_dir else None

    def pinned(self) -> list[dict]:
        p = self._pinned_path()
        if not p or not p.exists():
            return []
        try:
            _, body = parse_note(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []
        out = []
        for line in body.split("\n"):
            m = re.match(r"^\s*[-*]\s+(.+)$", line)
            if not m:
                continue
            text = m.group(1).strip()
            d = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[·:|-]\s*(.+)$", text)
            date, text = (d.group(1), d.group(2).strip()) if d else ("", text)
            if text:
                out.append({"id": hashlib.sha1(text.encode()).hexdigest()[:10], "text": text, "date": date})
        return out

    def _write_pinned(self, notes: list[dict]) -> None:
        p = self._pinned_path()
        if not p:
            raise RuntimeError("no place to save pinned notes")
        head = PINNED_HEADER
        if p.exists():                      # keep whatever the user wrote above the bullets
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.split("\n")
            first = next((i for i, l in enumerate(lines) if re.match(r"^\s*[-*]\s+", l)
                          and not self._in_front_matter(lines, i)), None)
            head = "\n".join(lines[:first]).rstrip("\n") + "\n\n" if first is not None else text.rstrip("\n") + "\n\n"
        body = "".join(f"- {n['date'] + ' · ' if n.get('date') else ''}{n['text']}\n" for n in notes)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(head + body, encoding="utf-8")
        tmp.replace(p)

    @staticmethod
    def _in_front_matter(lines: list[str], i: int) -> bool:
        if not lines or lines[0].strip() != "---":
            return False
        end = next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), None)
        return end is not None and i < end

    @staticmethod
    def _clean(text: str) -> str:
        text = " ".join(str(text or "").split()).lstrip("-* ").strip()
        if not text:
            raise ValueError("a note needs some text")
        if len(text) > MAX_PINNED_CHARS:
            raise ValueError(f"keep pinned notes under {MAX_PINNED_CHARS} characters; put longer material in a knowledge note")
        return text

    def add_pinned(self, text: str) -> dict:
        text = self._clean(text)
        notes = self.pinned()
        if any(n["text"].lower() == text.lower() for n in notes):
            return next(n for n in notes if n["text"].lower() == text.lower())
        if len(notes) >= MAX_PINNED:
            raise ValueError(f"the pinned list is full ({MAX_PINNED}); remove one first")
        note = {"id": hashlib.sha1(text.encode()).hexdigest()[:10], "text": text,
                "date": datetime.now().strftime("%Y-%m-%d")}
        self._write_pinned(notes + [note])
        return note

    def edit_pinned(self, note_id: str, text: str) -> dict:
        text = self._clean(text)
        notes = self.pinned()
        hit = next((n for n in notes if n["id"] == note_id), None)
        if not hit:
            raise KeyError(note_id)
        hit["text"] = text
        hit["id"] = hashlib.sha1(text.encode()).hexdigest()[:10]
        self._write_pinned(notes)
        return hit

    def remove_pinned(self, note_id: str) -> bool:
        notes = self.pinned()
        keep = [n for n in notes if n["id"] != note_id]
        if len(keep) == len(notes):
            return False
        self._write_pinned(keep)
        return True

    def status(self) -> dict:
        return {"ok": self.ok, "path": str(self.path) if self.path else None,
                "name": self.path.name if self.path else None,
                "knowledge_notes": len(self.notes()), "pinned": len(self.pinned())}
