"""The chat's building blocks: lookup registry, vault, saved conversations, usage, prompts, calculators.

Nothing here talks to Claude, OKX or the network.
"""

import asyncio
import json
import math

import pytest

from btcliq.desk import calc, prompt
from btcliq.desk.registry import LookupFailed, Param, Registry, clean_args, pack, tidy
from btcliq.desk.store import Conversations, Usage, cost_usd, title_from
from btcliq.desk.vault import Vault, categories, parse_note

run = asyncio.run


# ---------- registry ----------

def make_registry():
    r = Registry()

    @r.lookup("echo", title="Echo", description="Echo  the   arguments back.",
              params={"tf": Param("string", "timeframe", enum=("1H", "4H"), default="1H"),
                      "limit": Param("integer", "rows", default=10, minimum=1, maximum=50),
                      "tags": Param("array", "tags"), "flag": Param("boolean", "a flag", default=False)})
    async def echo(ctx, tf, limit, tags, flag):
        return {"tf": tf, "limit": limit, "tags": tags, "flag": flag}

    @r.lookup("boom", title="Boom", description="Always fails.")
    async def boom(ctx):
        raise ValueError("kaput")

    @r.lookup("private", title="Private", description="Chat only.", scope="chat")
    async def private(ctx):
        return "secret"

    @r.lookup("slow", title="Slow", description="Never returns.", timeout=0.05)
    async def slow(ctx):
        await asyncio.sleep(5)

    return r


class Chat:
    """Stands in for a question in progress."""


class Ctx:
    def __init__(self, chat=None):
        self.chat = chat


def test_arguments_are_coerced_defaulted_and_clamped():
    l = make_registry().get("echo")
    assert clean_args(l, None) == {"tf": "1H", "limit": 10, "tags": None, "flag": False}
    assert clean_args(l, {"tf": "4h", "limit": "7", "tags": "a, b", "flag": "true"}) == \
        {"tf": "4H", "limit": 7, "tags": ["a", "b"], "flag": True}
    assert clean_args(l, {"limit": 9999})["limit"] == 50 and clean_args(l, {"limit": -3})["limit"] == 1


@pytest.mark.parametrize("raw, needle", [
    ({"tf": "1W"}, "must be one of"), ({"limit": "many"}, "must be an integer"),
    ({"nope": 1}, "unknown argument"), ("text", "JSON object"), ({"flag": "maybe"}, "must be a boolean")])
def test_bad_arguments_say_what_to_change(raw, needle):
    with pytest.raises(LookupFailed, match=needle):
        clean_args(make_registry().get("echo"), raw)


def test_required_arguments():
    r = Registry()

    @r.lookup("need", title="Need", description="x", params={"q": Param("string", "query")}, required=("q",))
    async def need(ctx, q):
        return q

    with pytest.raises(LookupFailed, match="'q' is required"):
        clean_args(r.get("need"), {})
    assert run(r.call("need", {"q": " hi "}, Ctx())).data == "hi"


def test_a_failing_lookup_becomes_an_error_result_not_an_exception():
    r = make_registry()
    res = run(r.call("boom", {}, Ctx()))
    assert not res.ok and "ValueError: kaput" in res.error and res.text.startswith("Error:")
    assert not run(r.call("missing", {}, Ctx())).ok
    assert "timed out" in run(r.call("slow", {}, Ctx())).error
    bad = run(r.call("echo", {"tf": "1W"}, Ctx()))
    assert not bad.ok and "must be one of" in bad.text


def test_chat_only_lookups_are_hidden_from_other_sessions():
    r = make_registry()
    assert "private" not in [t["name"] for t in r.mcp_tools(chat=False)]
    assert "private" in [t["name"] for t in r.mcp_tools(chat=True)]
    assert not run(r.call("private", {}, Ctx(chat=None))).ok           # can't be called by name either
    assert run(r.call("private", {}, Ctx(chat=Chat()))).data == "secret"


def test_tool_lists_are_sorted_and_well_formed():
    r = make_registry()
    names = [t["name"] for t in r.api_tools()]
    assert names == sorted(names)                                       # same order every question: cache-friendly
    echo = next(t for t in r.mcp_tools(chat=True) if t["name"] == "echo")
    assert echo["description"] == "Echo the arguments back."
    assert echo["inputSchema"]["additionalProperties"] is False
    assert echo["inputSchema"]["properties"]["tf"]["enum"] == ["1H", "4H"]
    assert all(t["eager_input_streaming"] for t in r.api_tools())


def test_results_are_tidied_and_cut_to_size():
    assert tidy({"a": 1.23456789, "b": [float("nan"), 2], "c": (1,)}) == {"a": 1.23457, "b": [None, 2], "c": [1]}
    data, text = pack({"rows": [[i, "x" * 50] for i in range(2000)], "columns": ["i", "t"]}, limit=5000)
    assert len(text) <= 5000 and "truncated" in data and len(data["rows"]) < 2000
    assert json.loads(text)["truncated"].startswith("showing ")
    long = "line\n" * 5000
    shown, text = pack(long, limit=1000)
    assert len(shown) <= 1000 and "more characters" in text


# ---------- vault ----------

NOTE = """---
categories:
  - "[[Knowledge]]"
  - "[[Research]]"
summary: "When to trust GARCH: a rough gauge, not an options input"
aliases: [garch, vol forecast]
created: 2026-11-13
---
Intro line.

## Findings
DVOL beats GARCH at 7 to 30 days.

### Detail
R2 0.37 against 0.17.

## Rules
Never act when data quality is not ok.
"""


def make_vault(tmp_path):
    v = tmp_path / "bitVault"
    (v / "Templates").mkdir(parents=True)
    (v / ".obsidian").mkdir()
    (v / "GARCH volatility.md").write_text(NOTE)
    (v / "Architecture note.md").write_text("---\ncategories:\n  - \"[[Architecture]]\"\n---\nNot for the chat.\n")
    (v / "Templates" / "Knowledge note.md").write_text(NOTE)          # templates are never knowledge
    (v / ".obsidian" / "x.md").write_text(NOTE)
    (v / "Loose.md").write_text("no front matter at all")
    return Vault(v, tmp_path / "fallback")


def test_front_matter_is_parsed_the_way_obsidian_writes_it():
    props, body = parse_note(NOTE)
    assert categories(props) == ["Knowledge", "Research"]
    assert props["summary"].startswith("When to trust GARCH") and props["aliases"] == ["garch", "vol forecast"]
    assert body.startswith("Intro line.")
    assert parse_note("plain text") == ({}, "plain text")
    assert categories(parse_note("---\ncategories: \"[[Knowledge|what Claude knows]]\"\n---\nx")[0]) == ["Knowledge"]


def test_only_notes_in_the_knowledge_category_are_readable(tmp_path):
    v = make_vault(tmp_path)
    assert [n["note"] for n in v.index()] == ["GARCH volatility"]
    assert v.index()[0]["summary"].startswith("When to trust GARCH")
    assert v.read("garch")["note"] == "GARCH volatility"                 # by alias
    assert "error" in v.read("Architecture note")
    assert v.status()["knowledge_notes"] == 1


def test_reading_by_section_and_searching(tmp_path):
    v = make_vault(tmp_path)
    assert "DVOL beats GARCH" in v.read("GARCH volatility")["text"]
    sec = v.read("GARCH volatility", "findings")
    assert sec["section"] == "Findings" and "R2 0.37" in sec["text"] and "Never act" not in sec["text"]
    assert "sections" in v.read("GARCH volatility", "no such heading")
    hits = v.search("dvol 30 days")
    assert hits and hits[0]["note"] == "GARCH volatility" and hits[0]["section"] == "Findings"
    assert v.search("zzzz") == []


def test_long_notes_come_back_as_an_outline(tmp_path):
    v = make_vault(tmp_path)
    (v.path / "Long.md").write_text("---\ncategories:\n  - \"[[Knowledge]]\"\n---\nLead.\n\n## A\n" + "word " * 4000 + "\n## B\nend\n")
    v._scan = None
    out = v.read("Long")
    assert "cut" in out and out["sections"] == ["A", "B"] and out["text"] == "Lead."
    assert v.read("Long", "B")["text"].endswith("end")


def test_a_new_note_shows_up_without_a_restart(tmp_path):
    v = make_vault(tmp_path)
    assert len(v.index()) == 1
    (v.path / "New brief.md").write_text("---\ncategories:\n  - \"[[Knowledge]]\"\nsummary: fresh\n---\nbody\n")
    v._scan = None          # the scan is cached for two seconds
    assert [n["note"] for n in v.index()] == ["GARCH volatility", "New brief"]


def test_pinned_notes_live_in_the_vault_and_survive_hand_edits(tmp_path):
    v = make_vault(tmp_path)
    assert v.pinned() == []
    a = v.add_pinned("  Never hold options   through FOMC ")
    assert a["text"] == "Never hold options through FOMC"
    assert v.add_pinned("never hold options through fomc")["id"] == a["id"]        # no duplicates
    v.add_pinned("Prefers 14 to 30 day expiries")
    text = (v.path / "Pinned notes.md").read_text()
    assert text.startswith("---\ncategories:") and "- " in text
    (v.path / "Pinned notes.md").write_text(text + "- a bullet typed in Obsidian\n")     # edited by hand
    assert [n["text"] for n in v.pinned()][-1] == "a bullet typed in Obsidian"
    b = v.edit_pinned(a["id"], "Never hold short-dated options through FOMC")
    assert b["text"].startswith("Never hold short-dated") and len(v.pinned()) == 3
    assert v.remove_pinned(b["id"]) and not v.remove_pinned("nope")
    assert "Each bullet below is sent" in (v.path / "Pinned notes.md").read_text()         # the header is kept
    assert v.index() and all(n["note"] != "Pinned notes" for n in v.index())               # never listed as knowledge
    with pytest.raises(ValueError):
        v.add_pinned("x" * 500)
    with pytest.raises(ValueError):
        v.add_pinned("   ")


def test_without_a_vault_pinned_notes_fall_back_to_the_data_folder(tmp_path):
    v = Vault(None, tmp_path / "data")
    assert not v.ok and v.index() == [] and v.search("x") == []
    v.add_pinned("works anyway")
    assert v.pinned()[0]["text"] == "works anyway" and (tmp_path / "data" / "pinned-notes.md").exists()


def test_vault_is_found_next_to_the_project(tmp_path, monkeypatch):
    monkeypatch.delenv("BTCLIQ_VAULT", raising=False)
    project = tmp_path / "btcliq"
    project.mkdir()
    assert not Vault.find(None, project).ok
    (tmp_path / "bitVault").mkdir()
    assert Vault.find(None, project).path == (tmp_path / "bitVault").resolve()
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("BTCLIQ_VAULT", str(other))
    assert Vault.find(None, project).path == other.resolve()
    assert Vault.find(str(tmp_path / "bitVault"), project).path == (tmp_path / "bitVault").resolve()   # the flag wins


# ---------- conversations and usage ----------

def test_conversations_round_trip_and_stay_inside_their_folder(tmp_path):
    cs = Conversations(tmp_path)
    c = cs.new()
    c["title"] = "CPI week plan"
    c["messages"] += [{"role": "user", "text": "What about the 75k calls?", "ts": 1},
                      {"role": "assistant", "text": "They are cheap against DVOL.", "ts": 2,
                       "blocks": [{"t": "card", "id": "t1", "data": {"rows": ["x" * 30_000]}}, {"t": "text", "text": "ok"}]}]
    cs.save(c)
    got = cs.get(c["id"])
    assert got["title"] == "CPI week plan" and len(got["messages"]) == 2
    assert got["messages"][1]["blocks"][0]["data"] is None and "too large" in got["messages"][1]["blocks"][0]["dropped"]
    assert cs.list()[0]["id"] == c["id"] and cs.list()[0]["messages"] == 2
    for bad in ("../../etc/passwd", "20261113-000000-zzzz", "", None):
        assert cs.get(bad) is None and cs.delete(bad) is False
    assert cs.rename(c["id"], "  Renamed  ")["title"] == "Renamed"
    assert cs.delete(c["id"]) and cs.get(c["id"]) is None and cs.list() == []


def test_search_finds_words_across_conversations(tmp_path):
    cs = Conversations(tmp_path)
    ids = []
    for title, q, a in (("Calls", "the 75k calls into CPI", "buy the spread"), ("Puts", "my 66k put", "let it expire")):
        c = cs.new()
        c["id"] = c["id"][:-4] + f"{len(ids):04x}"
        c["title"] = title
        c["messages"] += [{"role": "user", "text": q, "ts": 1}, {"role": "assistant", "text": a, "ts": 2}]
        cs.save(c)
        ids.append(c["id"])
    hits = cs.search("75k cpi")
    assert [h["id"] for h in hits] == [ids[0]] and hits[0]["hits"][0]["role"] == "user"
    assert cs.search("expire")[0]["title"] == "Puts"
    assert cs.search("expire", skip=ids[1]) == []
    assert cs.search("") == [] and cs.search("nothing-like-this") == []
    part = cs.read(ids[0], 1, 5)
    assert part["turns"][0]["text"] == "buy the spread" and part["messages"] == 2
    assert cs.read("20261113-000000-abcd") is None


def test_usage_ledger_counts_tokens_plan_and_key_spend(tmp_path):
    u = Usage(tmp_path, default_cap=2.0)
    u.note_plan({"status": "allowed", "rateLimitType": "five_hour", "unifiedWindows": {
        "five_hour": {"utilization": 0.24, "resetsAt": 4102444800, "status": "allowed"},
        "seven_day": {"utilization": 0.22, "resetsAt": 4102444800, "status": "allowed"}}})
    e = u.add("claude-sonnet-5-5", "claude-code", {"in": 100, "out": 50, "cache_read": 900, "cache_write": 200})
    assert e["in"] == 1200 and e["out"] == 50 and "usd" not in e           # the plan isn't billed per token
    snap = u.snapshot()
    assert snap["plan"]["five_hour"]["pct"] == 24.0 and snap["today"]["tokens"] == 1250 and snap["key"]["usd"] == 0
    k = u.add("claude-sonnet-5-5", "api", {"in": 1_000_000, "out": 100_000, "cache_read": 0, "cache_write": 0})
    assert k["usd"] == pytest.approx(3.0) and u.key_left() == 0.0          # past the $2 cap
    assert u.set_cap(5) == 5.0 and u.key_left() == pytest.approx(2.0)
    again = Usage(tmp_path)                                                 # reloaded from disk
    assert again.cap == 5.0 and again.snapshot()["key"]["usd"] == pytest.approx(3.0)


def test_a_plan_window_that_has_reset_reads_as_zero(tmp_path):
    u = Usage(tmp_path)
    u.note_plan({"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.9, "resetsAt": 1000}}})
    assert u.snapshot()["plan"]["five_hour"] == {"pct": 0.0, "resets": 1_000_000, "status": None, "stale": True}


def test_cost_uses_cache_prices_and_treats_unknown_models_as_dearest():
    use = {"in": 1_000_000, "out": 0, "cache_read": 1_000_000, "cache_write": 1_000_000, "web_searches": 2}
    assert cost_usd("claude-sonnet-5-5", use) == pytest.approx(2 + 0.10 + 2.5 + 0.02)
    assert cost_usd("some-new-model", {"in": 1_000_000}) == pytest.approx(8.0)


def test_titles_come_from_the_first_question():
    assert title_from("please   how is my put doing?") == "How is my put doing?"
    assert title_from("x" * 200).endswith("…") and len(title_from("word " * 50)) <= 61
    assert title_from("") == "New chat"


# ---------- prompts ----------

def test_system_prompt_is_stable_and_carries_the_index_and_notes():
    know = [{"note": "GARCH volatility", "summary": "when to trust it"}]
    pins = [{"text": "Never hold options through FOMC"}]
    a, b = prompt.system_prompt(know, pins), prompt.system_prompt(know, pins)
    assert a == b                                    # byte-identical: Claude's cache can reuse it
    assert "- GARCH volatility: when to trust it" in a and "- Never hold options through FOMC" in a
    assert "Horizon gate" in a                       # the power-law rules ride here, not in every snapshot
    assert "UTC" not in prompt.SYSTEM.split("# Power-law rules")[0].split("# What you receive")[0]   # no clock in the stable part
    assert "# Knowledge notes" not in prompt.system_prompt([], [])


def conv_of(n):
    msgs = []
    for i in range(n):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "text": f"message {i}", "ts": i})
    return {"id": "x", "messages": msgs, "summary": None}


def test_history_leaves_out_the_new_question_and_uses_the_summary():
    c = conv_of(7)                                    # ends on the user's new question
    summary, turns = prompt.history_for(c)
    assert summary is None and [t["text"] for t in turns] == [f"message {i}" for i in range(6)]
    c["summary"] = {"upto": 4, "text": "earlier: talked about puts"}
    summary, turns = prompt.history_for(c)
    assert summary == "earlier: talked about puts" and [t["text"] for t in turns] == ["message 4", "message 5"]


def test_long_conversations_ask_for_a_summary_that_keeps_recent_turns():
    assert prompt.needs_summary(conv_of(10)) is None
    c = conv_of(30)
    upto = prompt.needs_summary(c)
    assert upto == 22 and c["messages"][upto]["role"] == "user"       # the kept part opens on a user turn
    c["summary"] = {"upto": upto, "text": "s"}
    assert prompt.needs_summary(c) is None                            # nothing new to fold yet
    req = prompt.summary_request(conv_of(30), 22)
    assert "message 21" in req and "message 22" not in req and req.endswith(prompt.SUMMARY_ASK)
    big = conv_of(6)
    big["messages"][1]["text"] = "x" * 40_000
    assert prompt.needs_summary(big) is None or prompt.needs_summary(big) < 6


def test_cli_prompt_and_api_messages_carry_the_same_parts():
    ctx = prompt.context_block("SNAPSHOT", {"view": "chart", "tf": "15m", "tz": "America/New_York"}, web=True)
    assert "<snapshot" in ctx and "chart timeframe 15m" in ctx and "Web search is ON" in ctx
    assert "Web search" not in prompt.context_block("S", {}, web=False)
    turns = [{"role": "user", "text": "q1"}, {"role": "assistant", "text": "a1"}]
    cli = prompt.cli_prompt("sum", turns, ctx, "what now?")
    assert cli.index("<conversation_summary>") < cli.index("<earlier_turns>") < cli.index("<snapshot") < cli.index("<question>")
    msgs = prompt.api_messages("sum", turns, ctx, "what now?")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][0]["text"].startswith("<conversation_summary>")
    assert msgs[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}       # history is the cache point
    assert "cache_control" not in msgs[2]["content"][0]                           # the fresh snapshot is not
    assert msgs[2]["content"][1]["text"] == "<question>\nwhat now?\n</question>"
    alone = prompt.api_messages("sum", [], ctx, "q")
    assert len(alone) == 1 and alone[0]["content"][0]["text"].startswith("<conversation_summary>")


def test_screen_and_account_lines():
    s = prompt.screen_lines({"view": "options", "tf": "1H", "tz": "America/New_York", "option": "BTC-USD-261127-75000-C",
                             "ticket": {"inst": "BTC-USDT-SWAP", "side": "buy", "sz": "2", "px": "68000", "type": "limit"},
                             "drawings": [{"type": "hline", "points": [{"t": "x", "p": 71500.4}]},
                                          {"type": "trend", "points": [{"t": "x", "p": 1}, {"t": "y", "p": 2}]}]})
    assert "View: Options" in s and "BTC-USD-261127-75000-C" in s and "BUY 2 BTC-USDT-SWAP @ 68000" in s
    assert "Drawings on the chart: 2" in s and "71,500" in s
    assert "did not send" in prompt.screen_lines(None)
    a = prompt.account_lines({"configured": True, "trading": True, "halted": {"reason": "news"},
                              "account": {"total_eq_usd": 1234.5},
                              "positions": [{"instId": "BTC-USD-261114-66000-P", "pos": 145.0, "posSide": "net", "unit": "contracts",
                                             "mgnMode": "cross", "avgPx": 0.0009, "markPx": 0.00003, "upl_usd": -86.1, "uplRatio": -0.966}],
                              "orders": []})
    text = "\n".join(a)
    assert "Equity $1,234.50" in text and "HALTED by the kill switch (news)" in text
    assert "BTC-USD-261114-66000-P: +145 contracts (cross)" in text and "(-96.6%)" in text and "Open orders: none" in text
    assert prompt.account_lines({"configured": False}) == []


# ---------- calculators ----------

def test_black76_obeys_put_call_parity_and_its_limits():
    f, k, t, iv = 68000.0, 70000.0, 30 / 365, 0.45
    c, p = calc.black76(f, k, t, iv, "C"), calc.black76(f, k, t, iv, "P")
    assert c - p == pytest.approx(f - k, abs=1e-6)
    assert calc.black76(f, k, 0, iv, "C") == 0 and calc.black76(f, k, 0, iv, "P") == 2000
    assert calc.black76(f, k, t, 0, "P") == 2000


def test_realized_vol_matches_a_hand_calculation():
    closes = [100, 101, 99, 102, 100, 103, 101, 104]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mean = sum(rets) / 7
    want = math.sqrt(sum((r - mean) ** 2 for r in rets) / 6) * math.sqrt(365)
    assert calc.realized_vol(closes, (7, 30)) == {"7d": pytest.approx(want), "30d": None}
    assert calc.pct_rank([1, 2, 3, 4], 3) == 75.0 and calc.pct_rank([], 1) is None
    done = calc.complete_days([{"time": 0}, {"time": calc.DAY_MS}], now_ms=int(1.5 * calc.DAY_MS))
    assert done == [{"time": 0}]                                   # today's bar is still forming


def contract(name, strike, typ, bid, ask, unit="BTC", days=14, iv=0.4, delta=0.3):
    return {"name": name, "strike": float(strike), "type": typ, "bid": bid, "ask": ask, "mark": (bid + ask) / 2,
            "unit": unit, "ct_mult": 0.01, "exp_ms": 1_800_000_000_000 + days * calc.DAY_MS, "iv": iv,
            "delta": delta if typ == "C" else -delta, "gamma": 1e-5, "theta": -30.0, "vega": 20.0, "fwd": 68000.0}


def test_call_spread_payoff():
    legs = [{"c": contract("C72", 72000, "C", 0.0088, 0.0090), "side": "buy", "contracts": 2},
            {"c": contract("C76", 76000, "C", 0.0021, 0.0023), "side": "sell", "contracts": 2}]
    out = calc.payoff(legs, 68000.0, now_ms=1_800_000_000_000)
    debit = 2 * 0.01 * (0.0090 - 0.0021) * 68000                    # buy at the ask, sell at the bid
    assert out["net_premium_usd"] == pytest.approx(-debit) and out["net_premium_reads"].startswith("you pay")
    assert out["max_loss_usd"] == pytest.approx(-debit)
    assert out["max_gain_usd"] == pytest.approx(2 * 0.01 * 4000 - debit)
    assert out["breakevens"] == [round(72000 + debit / 0.02)]
    assert [l["price_from"] for l in out["legs"]] == ["ask", "bid"]
    assert out["greeks_now"]["delta_btc"] == pytest.approx(0.0) and out["days_to_expiry"] == pytest.approx(14)
    assert any("settled in BTC" in n for n in out["notes"])
    rows = {r[0]: r[2] for r in out["at_expiry"]["rows"]}
    assert rows[72000.0] == pytest.approx(-debit) and rows[76000.0] == pytest.approx(out["max_gain_usd"])


def test_naked_legs_are_unlimited_and_usd_margined_legs_are_not_converted():
    long_call = calc.payoff([{"c": contract("C", 70000, "C", 0.01, 0.011), "side": "buy", "contracts": 1}], 68000.0)
    assert long_call["max_gain_usd"] == "unlimited" and long_call["max_loss_usd"] == pytest.approx(-0.011 * 68000 * 0.01)
    short_call = calc.payoff([{"c": contract("C", 70000, "C", 0.01, 0.011), "side": "sell", "contracts": 1}], 68000.0)
    assert short_call["max_loss_usd"] == "unlimited"
    um = calc.payoff([{"c": contract("P", 66000, "P", 400.0, 420.0, unit="USD"), "side": "buy", "contracts": 3, "price": 410.0}], 68000.0)
    assert um["net_premium_usd"] == pytest.approx(-3 * 0.01 * 410) and um["legs"][0]["price_from"] == "given"
    assert um["breakevens"] == [66000 - 410]
    with pytest.raises(ValueError):
        calc.payoff([], 68000.0)


def test_position_risk_reprices_options_and_moves_perps_one_for_one():
    put = contract("BTC-USD-261114-66000-P", 66000, "P", 0.0003, 0.0004, days=0.25, iv=0.5)
    put["fwd"] = 68000.0
    pos = [{"instId": put["name"], "type": "OPTION", "pos": 300.0, "posSide": "net", "mgnMode": "cross",
            "deltaBS": -0.02, "thetaBS": -26.0, "upl_usd": -257.0, "notionalUsd": 204000.0},
           {"instId": "BTC-USDT-SWAP", "type": "SWAP", "pos": 5.0, "posSide": "short", "mgnMode": "cross",
            "notionalUsd": 3400.0, "upl_usd": 12.0, "lever": 2.0, "liqPx": 99000.0}]
    out = calc.position_risk(pos, {put["name"]: put}, 68000.0, now_ms=put["exp_ms"] - int(0.25 * calc.DAY_MS))
    t = out["totals"]
    assert t["delta_btc"] == pytest.approx(-0.02 - 3400 / 68000) and t["delta_usd"] == pytest.approx(-0.02 * 68000 - 3400)
    assert t["theta_usd_per_day"] == -26.0 and t["upl_usd"] == pytest.approx(-245.0)
    what_if = {r[0]: r[2] for r in out["what_if"]["rows"]}
    # -10%: the puts finish about 4,800 in the money on 3 BTC; the short perp gains 10% of 3,400
    assert what_if[-10.0] == pytest.approx(3 * (66000 - 61200) + 340, rel=0.02)
    assert what_if[10.0] == pytest.approx(-340, abs=15)               # plus the few dollars the puts still hold
    rows = {r["instId"]: r for r in out["rows"]}
    assert rows["BTC-USDT-SWAP"]["size"] == -5.0 and rows[put["name"]]["strike"] == 66000.0
    missing = calc.position_risk(pos[:1], {}, 68000.0)
    assert any("No chain data" in n for n in missing["notes"])
