"""The Notes view's file access: any .md in the vault, nothing outside it, no lost edits."""

import asyncio
import json

import pytest

from test_desk_chat import http, look, make_app, make_desk

from btcliq.desk.notebook import Conflict, Notebook, NoteError, digest

run = asyncio.run
H = {"X-Btcliq-Trade": "page-token"}


def make_nb(tmp_path):
    v = tmp_path / "vault"
    (v / ".obsidian").mkdir(parents=True)
    (v / ".obsidian" / "app.json").write_text("{}")
    (v / "Templates").mkdir()
    (v / "Templates" / "Daily.md").write_text("# {{date}}")
    (v / "Home.md").write_text("Start at [[Plan]] and [[Notes/Journal|my journal]].\n")
    (v / "Notes").mkdir()
    (v / "Notes" / "Journal.md").write_text("---\ntags: [trade]\n---\n# Monday\nSold the 90k call. See [[Plan#Exits]].\n")
    (v / "Notes" / "pic.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.md").write_text("outside the vault")
    return Notebook(v)


def test_paths_never_leave_the_vault(tmp_path):
    nb = make_nb(tmp_path)
    for bad in ["../secret.md", "/etc/passwd.md", "Notes/../../secret.md", ".obsidian/app.md", "Notes/.hidden.md",
                "", "Notes/a:b.md", "Notes/plain.txt"]:
        with pytest.raises(NoteError):
            nb.rel(bad)
    with pytest.raises(NoteError):
        nb.file("Home.md")                                       # embeds are images or PDFs only
    (nb.root / "Notes" / "escape.md").symlink_to(tmp_path / "secret.md")
    with pytest.raises(NoteError):
        nb.read("Notes/escape.md")                               # a symlink pointing out is refused


def test_tree_lists_notes_and_files_but_not_hidden_folders(tmp_path):
    t = make_nb(tmp_path).tree()
    paths = [n["path"] for n in t["notes"]]
    assert paths == ["Home.md", "Notes/Journal.md", "Templates/Daily.md"]
    assert [f["path"] for f in t["files"]] == ["Notes/pic.png"]
    assert t["folders"] == ["Notes", "Templates"] and t["scratch"] == "Notes/Scratch.md"


def test_save_round_trips_exactly_and_refuses_to_overwrite_newer_edits(tmp_path):
    nb = make_nb(tmp_path)
    n = nb.read("Notes/Journal.md")
    text = n["text"] + "\n- [ ] roll the put\n> [!warning] CPI 8:30\n$$x^2$$\n"
    saved = nb.write("Notes/Journal.md", text, n["hash"])
    assert (nb.root / "Notes" / "Journal.md").read_text() == text and saved["hash"] == digest(text.encode())
    with pytest.raises(Conflict) as e:                            # the page still holds the old hash
        nb.write("Notes/Journal.md", "stale", n["hash"])
    assert e.value.current["text"] == text
    (nb.root / "Notes" / "Journal.md").write_text("Obsidian saved this")
    with pytest.raises(Conflict):
        nb.write("Notes/Journal.md", "mine", saved["hash"])
    assert not list(nb.root.rglob("*.btcliq-tmp"))


def test_create_rename_and_trash(tmp_path):
    nb = make_nb(tmp_path)
    nb.write("Notes/Ideas/New idea.md", "# idea\n", None, create=True)       # folders are made as needed
    with pytest.raises(NoteError):
        nb.write("Notes/Ideas/New idea.md", "again", None, create=True)
    with pytest.raises(Conflict):
        nb.write("Notes/Gone.md", "x", "somehash")                          # deleted elsewhere since it was opened
    assert nb.rename("Notes/Ideas/New idea.md", "Notes/Better idea.md")["path"] == "Notes/Better idea.md"
    with pytest.raises(NoteError):
        nb.rename("Notes/Better idea.md", "Home.md")
    assert nb.trash("Notes/Better idea.md")["trashed_as"] == ".trash/Better idea.md"
    nb.write("Notes/Better idea.md", "two", None, create=True)
    assert nb.trash("Notes/Better idea.md")["trashed_as"] == ".trash/Better idea 1.md"
    assert "Notes/Better idea.md" not in [n["path"] for n in nb.tree()["notes"]]


def test_pasted_images_land_in_attachments(tmp_path):
    nb = make_nb(tmp_path)
    a = nb.attach(b"img", "png")
    b = nb.attach(b"img", "png", a["name"])
    assert a["path"].startswith("Notes/attachments/Pasted image ") and b["path"].endswith(" 1.png")
    assert nb.file(a["path"])[1] == "image/png"
    with pytest.raises(NoteError):
        nb.attach(b"<svg onload=alert(1)>", "svg")                          # pasted SVG could carry script
    assert nb.attach(b"x", "png", "../../evil")["path"] == "Notes/attachments/evil.png"   # only the bare name is kept


def test_search_and_backlinks(tmp_path):
    nb = make_nb(tmp_path)
    assert [h["path"] for h in nb.search("sold call")] == ["Notes/Journal.md"]
    assert nb.search("sold call")[0]["section"] == "Monday"
    nb.write("Plan.md", "## Exits\n", None, create=True)
    assert sorted(l["path"] for l in nb.backlinks("Plan.md")) == ["Home.md", "Notes/Journal.md"]
    assert [l["path"] for l in nb.backlinks("Notes/Journal.md")] == ["Home.md"]


def test_chat_reads_only_the_notes_folder(tmp_path, monkeypatch):
    desk = make_desk(tmp_path, monkeypatch)
    root = desk.notebook.root
    (root / "Notes").mkdir()
    (root / "Notes" / "Trade journal.md").write_text("---\ntags: [btc]\n---\nBought the 26DEC 100k call at 0.012.\n")
    (root / "Private outside.md").write_text("100k call outside the Notes folder")
    hit = look(desk, "notes_search", {"query": "100k call"})
    assert hit.ok and [m["note"] for m in hit.data["matches"]] == ["Trade journal"]
    r = look(desk, "notes_read", {"note": "trade journal"})
    assert r.ok and r.data["text"].startswith("Bought") and r.data["properties"] == {"tags": ["btc"]}
    assert "Trade journal" in look(desk, "notes_read", {"note": "nope"}).error
    assert look(desk, "notes_search", {}).data["notes"][0]["note"] == "Trade journal"


def test_note_routes_need_the_page_token_and_report_conflicts(tmp_path, monkeypatch):
    desk = make_desk(tmp_path, monkeypatch)
    app = make_app(desk)

    async def go(c):
        out = {"no token": (await c.get("/api/desk/vault/tree")).status}
        r = await c.put("/api/desk/vault/note", headers=H, json={"path": "Notes/A.md", "text": "one", "create": True})
        first = await r.json()
        out["create"] = r.status
        r = await c.get("/api/desk/vault/note", headers=H, params={"path": "Notes/A.md"})
        out["read"] = (await r.json())["text"]
        r = await c.put("/api/desk/vault/note", headers=H, json={"path": "Notes/A.md", "text": "two", "base": first["hash"]})
        out["save"] = r.status
        r = await c.put("/api/desk/vault/note", headers=H, json={"path": "Notes/A.md", "text": "three", "base": first["hash"]})
        body = await r.json()
        out["conflict"] = (r.status, body["conflict"], body["current"]["text"])
        out["escape"] = (await c.get("/api/desk/vault/note", headers=H, params={"path": "../x.md"})).status
        out["missing"] = (await c.get("/api/desk/vault/note", headers=H, params={"path": "Nope.md"})).status
        r = await c.post("/api/desk/vault/attach", headers=H, params={"ext": "png"}, data=b"\x89PNG")
        att = (await r.json())["path"]
        r = await c.get("/api/desk/vault/file", headers=H, params={"path": att})
        out["file"] = (r.status, r.headers["Content-Type"], await r.read())
        out["search"] = [h["path"] for h in (await (await c.get("/api/desk/vault/search", headers=H, params={"q": "two"})).json())["hits"]]
        out["trash"] = (await c.delete("/api/desk/vault/note", headers=H, params={"path": "Notes/A.md"})).status
        return out

    out = run(http(app, go))
    assert out["no token"] == 403 and out["create"] == 200 and out["read"] == "one" and out["save"] == 200
    assert out["conflict"] == (409, True, "two")
    assert out["escape"] == 400 and out["missing"] == 404
    assert out["file"] == (200, "image/png", b"\x89PNG")
    assert out["search"] == ["Notes/A.md"] and out["trash"] == 200
