"""Market-moving US dates for the calendar in the Market tab: Fed meetings and speeches, the White House
schedule, and the big data releases (CPI, jobs report, PCE, GDP, PPI).

Where each part comes from (all public, no key):
- The Federal Reserve Board's calendar, federalreserve.gov/json/calendar.json: FOMC decisions and press
  conferences, minutes, the Beige Book, testimony and Board members' speeches, through December.
- The FOMC meeting calendar page: next year's meetings, and which ones bring new projections (dot plot).
- The President's public schedule as the White House press office publishes it, plus press briefings and
  unscheduled appearances from pool reports, via Roll Call's Factbase. The press office releases each
  day's schedule the evening before, so this only reaches today and tomorrow.
- BEA's release calendar (iCalendar): GDP, and personal income and outlays (PCE inflation).
- BLS dates for CPI, the jobs report and PPI, built in below because bls.gov blocks automated requests.
- ForexFactory's calendar for this week: forecasts, previous values and impact ratings, plus what the
  sources above miss (jobless claims, retail sales, ISM, regional Fed presidents' speeches).

Times are Eastern at the source; events carry UTC milliseconds and the browser shows local time.
Impact: 3 high (FOMC decision, CPI, jobs, PCE, the Chair speaking), 2 medium, 1 low.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp

log = logging.getLogger("btcliq")
ET = ZoneInfo("America/New_York")
DAY_MS = 86_400_000

# name: (url, seconds between refreshes, label shown in the browser)
SOURCES = {
    "fed": ("https://www.federalreserve.gov/json/calendar.json", 6 * 3600, "Federal Reserve calendar"),
    "fomc": ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", 24 * 3600, "FOMC meeting calendar"),
    "wh": ("https://media-cdn.factba.se/rss/json/trump/calendar.json", 600, "White House schedule (Roll Call Factbase)"),
    "bea": ("https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics", 24 * 3600, "BEA release calendar"),
    "ff": ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", 3600, "ForexFactory weekly calendar"),
}
RETRY_SECONDS = 300
PAST_DAYS = 45       # high-impact events kept this far back, for the marks on the chart
AHEAD_DAYS = 400
RECENT_HOURS = 36    # everything from this far back is kept, so "today" works in every time zone

# Release dates (8:30 am Eastern) for the three BLS reports that move markets most. bls.gov blocks
# automated requests, so they're built in. 2026: bls.gov/schedule/2026 (checked Nov 12, 2026).
# 2027: OMB's "Schedule of Release Dates for Principal Federal Economic Indicators for 2027"
# (whitehouse.gov/wp-content/uploads/2026/10/FY2027-PFEI.pdf). OMB publishes the next year's each fall.
BLS_DATES = {
    "jobs": ["2026-10-02", "2026-11-06", "2026-12-04",
             "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07", "2027-06-04",
             "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"],
    "cpi": ["2026-10-14", "2026-11-10", "2026-12-10",
            "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
            "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"],
    "ppi": ["2026-10-15", "2026-11-13", "2026-12-15",
            "2027-01-14", "2027-02-12", "2027-03-12", "2027-04-14", "2027-05-13", "2027-06-11",
            "2027-07-15", "2027-08-10", "2027-09-10", "2027-10-15", "2027-11-12", "2027-12-15"],
}
BLS_LAST = max(d for dates in BLS_DATES.values() for d in dates)
# kind: (title, chart label, impact, what the report measures)
DATA_KINDS = {
    "jobs": ("Jobs report", "Jobs", 3, "Payrolls and unemployment"),
    "cpi": ("CPI inflation", "CPI", 3, "Consumer prices"),
    "ppi": ("PPI inflation", "PPI", 2, "Producer prices"),
    "pce": ("PCE inflation", "PCE", 3, "Personal income and outlays"),
    "gdp": ("GDP", "GDP", 2, "Gross domestic product"),
    "claims": ("Jobless claims", "Claims", 1, "Weekly unemployment claims"),
}
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


# ---------- helpers ----------
def et_ms(d: date, h: int = 0, m: int = 0) -> int:
    """UTC milliseconds for a wall-clock time in New York (daylight saving handled by zoneinfo)."""
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=ET).timestamp() * 1000)


def et_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, ET).date().isoformat()


def clean(s) -> str:
    """Plain text from the (double-escaped) HTML snippets in the Fed's calendar."""
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s or "")))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def dashes(s: str) -> str:
    """'December 8 - 9' -> 'December 8–9'."""
    return re.sub(r"\s*-\s*", "–", s)


def event(src: str, kind: str, impact: int, title: str, t: int | None = None, day: str | None = None, *,
          short: str = "", detail: str = "", where: str = "", via: str = "", link: str = "",
          values: list | None = None) -> dict:
    if day is None and t is not None:
        day = et_day(t)
    key = f"{src}|{kind}|{day}|{t}|{title}"
    return {"id": hashlib.sha1(key.encode()).hexdigest()[:12], "t": t, "day": day, "src": src, "kind": kind,
            "impact": impact, "title": title, "short": short or title[:16], "detail": detail, "where": where,
            "via": via, "link": link, "values": values or []}


TIME_12H = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\b", re.I)


def parse_12h(s) -> tuple[int, int] | None:
    """'2:00 p.m.' -> (14, 0)."""
    m = TIME_12H.search(s or "")
    if m:
        return int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0), int(m.group(2))
    if re.search(r"\bnoon\b", s or "", re.I):
        return 12, 0
    return None


# ---------- Federal Reserve ----------
ROLE = re.compile(r"^(Vice Chair for Supervision|Vice Chair|Chairman|Chair|Governor)\s+(.+)$")
POLICY_TOPIC = re.compile(r"outlook|monetary policy|inflation|economy|economic|labor market|interest rate", re.I)


def speaker(title: str) -> tuple[str, str, str, str]:
    """'Speech - Vice Chair Philip N. Jefferson' -> ('Speech', 'Vice Chair', 'Philip N. Jefferson', 'Jefferson')."""
    m = re.match(r"^\s*(.*?)\s*-{1,2}\s*(.+?)\s*$", title)
    what, who = (m.group(1), m.group(2)) if m else ("", title.strip())
    who = who.split(",")[0].strip()          # 'Randall D. Guynn, Director, Division of ...'
    r = ROLE.match(who)
    role, name = (r.group(1), r.group(2).strip()) if r else ("", who)
    role = "Chair" if role == "Chairman" else role
    parts = [p for p in name.split() if p not in ("Jr.", "Jr", "Sr.", "II", "III")]
    return what, role, name, parts[-1] if parts else name


def parse_fed(raw: dict, sep_days: dict[str, bool] | None = None) -> list[dict]:
    """The Board's calendar JSON -> FOMC decisions, minutes, testimony, speeches, the Beige Book."""
    sep_days = sep_days or {}
    out: list[dict] = []
    decisions: list[tuple[date, int | None, str, str]] = []
    pressers: dict[str, tuple[int, int]] = {}
    for e in (raw or {}).get("events") or []:
        typ, title, month = e.get("type"), (e.get("title") or "").strip(), e.get("month") or ""
        if typ not in ("FOMC", "Speeches", "Testimony", "Beige") or not re.fullmatch(r"\d{4}-\d{2}", month):
            continue
        hm = parse_12h(e.get("time"))
        desc, where = clean(e.get("description")), clean(e.get("location"))
        link = str(e.get("live") or e.get("link") or "")
        link = link if re.match(r"https?://", link) else ""
        for dd in re.findall(r"\d+", e.get("days") or ""):
            try:
                d = date(int(month[:4]), int(month[5:]), int(dd))
            except ValueError:
                continue
            t = et_ms(d, *hm) if hm else None
            low = title.lower()
            if typ == "FOMC":
                if "press conference" in low:
                    if hm:
                        pressers[d.isoformat()] = hm
                elif "minutes" in low:
                    m = re.search(r"Meeting of (.+)", desc)
                    out.append(event("fed", "minutes", 2, "FOMC minutes", t, d.isoformat(), short="Minutes",
                                     detail=f"From the {dashes(m.group(1))} meeting" if m else "",
                                     via="Federal Reserve"))
                elif "meeting" in low:
                    m = re.search(r"Two-day meeting,?\s*(.+?)(?:\s+Press Conference|$)", desc, re.I)
                    decisions.append((d, t, dashes(m.group(1)) if m else "", link))
            elif typ == "Beige":
                out.append(event("fed", "beige", 1, "Beige Book", t, d.isoformat(), short="Beige Book",
                                 detail="The Fed's survey of business conditions across its 12 districts",
                                 via="Federal Reserve"))
            else:
                what, role, name, last = speaker(title)
                testimony = typ == "Testimony" or what.lower().startswith("testimony")
                if role == "Chair":
                    impact = 3
                elif role == "Vice Chair":
                    impact = 2
                elif role:
                    impact = 2 if POLICY_TOPIC.search(desc) else 1
                else:
                    impact = 1
                who = f"{role} {last}" if role else name
                verb = ("testifies" if testimony else
                        "in a discussion" if re.search(r"discussion|panel|conversation|fireside", what, re.I)
                        else "speaks")
                out.append(event("fed", "testimony" if testimony else "speech", impact, f"{who} {verb}", t,
                                 d.isoformat(), short=last, detail=desc, where=where, via="Federal Reserve",
                                 link=link))
    for d, t, span, link in decisions:
        out.append(fomc_decision(d, t, span, sep_days.get(d.isoformat(), False), pressers.get(d.isoformat()),
                                 "Federal Reserve", link))
    return out


def fomc_decision(d: date, t: int | None, span: str, sep: bool, presser: tuple[int, int] | None,
                  via: str, link: str = "") -> dict:
    notes = []
    if presser and t is not None:
        mins = round((et_ms(d, *presser) - t) / 60000)
        notes.append(f"Press conference {mins} min later." if mins > 0 else "Press conference follows.")
    if sep:
        notes.append("New economic projections and dot plot.")
    if span:
        notes.append(f"Two-day meeting, {span}.")
    return event("fed", "fomc", 3, "FOMC rate decision", t, d.isoformat(), short="FOMC", detail=" ".join(notes),
                 via=via, link=link)


def parse_fomc_page(text: str) -> list[dict]:
    """The FOMC meeting calendar page -> [{'start', 'day' (decision day), 'sep'}] for every year shown."""
    out = []
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", text or "")]
    for pos, year in heads:
        end = min((p for p, _ in heads if p > pos), default=len(text))
        for mon, dates in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?'
                                     r'fomc-meeting__date[^>]*>([^<]+)<', text[pos:end], re.S):
            if "(" in dates:               # notation votes and unscheduled meetings
                continue
            nums = [int(x) for x in re.findall(r"\d+", dates)]
            months = [MONTHS.get(p.strip()[:3].lower()) for p in mon.split("/")]
            if not nums or not months or None in months:
                continue
            try:
                out.append({"start": date(year, months[0], nums[0]), "day": date(year, months[-1], nums[-1]),
                            "sep": "*" in dates})
            except ValueError:
                continue
    return out


def fomc_from_page(meetings: list[dict], covered_until: str) -> list[dict]:
    """Meetings after the Fed's JSON calendar ends (next year), at the usual 2:00 pm Eastern."""
    out = []
    for m in meetings:
        if m["day"].isoformat()[:7] <= covered_until:
            continue
        a, b = m["start"], m["day"]
        span = (f"{MONTH_NAMES[a.month - 1]} {a.day}–{b.day}" if a.month == b.month
                else f"{MONTH_NAMES[a.month - 1]} {a.day}–{MONTH_NAMES[b.month - 1]} {b.day}")
        out.append(fomc_decision(b, et_ms(b, 14, 0), span, m["sep"], (14, 30), "Federal Reserve meeting calendar"))
    return out


# ---------- White House ----------
WH_PUBLIC = re.compile(r"\b(remarks|address|speech|speaks|press conference|news conference|press briefing|gaggle|"
                       r"statement|signs|signing ceremony|announces?|announcement|interview|meets with|bilateral|"
                       r"summit|town hall|rally|roundtable)\b", re.I)
WH_PRIVATE = re.compile(r"intelligence briefing|pre-tape|executive time|policy meeting|pool call time", re.I)
WH_MARKET = re.compile(r"tariff|trade (deal|agreement|war|polic)|crypto|bitcoin|digital asset|stablecoin|"
                       r"federal reserve|\bthe fed\b|fed chair|interest rate|inflation|econom|tax (cut|bill|plan)|"
                       r"debt ceiling|shutdown|sanction", re.I)
WH_POOL_KEEP = re.compile(r"unscheduled|press conference|remarks|speaks|statement|announce|gaggle|interview|"
                          r"calls in|signs|address", re.I)


def wh_impact(text: str, coverage: str = "") -> int:
    if re.search(r"address to the nation|unscheduled press conference", text, re.I):
        return 3
    public = bool(WH_PUBLIC.search(text)) and not WH_PRIVATE.search(text)
    if public and WH_MARKET.search(text):
        return 3
    if public or coverage.lower().startswith("open press"):
        return 2
    return 1


def wh_title(details: str) -> str:
    """'The President participates in a Policy Meeting' -> 'Policy Meeting'."""
    s = re.sub(r"^TBD:\s*", "", details.strip())
    s = re.sub(r"^The President\s+(participates in|receives)\s+(an?\s+|the\s+|his\s+)?", "", s)
    s = re.sub(r"^The President\s+(?!and\b)(?=[a-z])", "", s)
    return s[:1].upper() + s[1:]


def parse_wh(items: list) -> list[dict]:
    """Factbase's feed of the President's schedule -> events. Pool reports only add unscheduled appearances."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("type") or "").rstrip(":").strip()
        details = re.sub(r"\s+", " ", str(it.get("details") or "")).strip()
        if not details or not it.get("date"):
            continue
        if typ == "Pool Report Schedule":
            if not WH_POOL_KEEP.search(details) or re.search(r"\blid\b", details, re.I):
                continue
            kind, via = "pool", "Pool report via Roll Call Factbase"
        elif typ == "Briefing Schedule":
            kind, via = "briefing", "White House press office via Roll Call Factbase"
        elif typ == "President Schedule":
            kind, via = "schedule", "White House schedule via Roll Call Factbase"
        else:
            continue
        try:
            d = date.fromisoformat(str(it["date"]))
        except ValueError:
            continue
        t = None
        m = re.match(r"(\d{1,2}):(\d{2})", str(it.get("time") or ""))
        if m and not details.startswith("TBD"):
            t = et_ms(d, int(m.group(1)), int(m.group(2)))
        coverage = str(it.get("coverage") or "").strip()
        where = str(it.get("location") or "").strip()
        detail = ", ".join(x for x in (where, coverage.lower()) if x)
        if kind == "pool":
            detail = f"Pool report{', ' + where if where else ''}"
        out.append(event("wh", kind, 2 if kind == "briefing" else wh_impact(details, coverage), wh_title(details),
                         t, d.isoformat(), short="White House", detail=detail, via=via))
    return out


# ---------- BEA ----------
def parse_bea(text: str) -> list[dict]:
    """BEA's iCalendar feed -> GDP and personal income and outlays (PCE) releases."""
    out = []
    text = re.sub(r"\r?\n[ \t]", "", text or "")       # unfold long lines
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        sm = re.search(r"^SUMMARY[^:\n]*:(.*)$", block, re.M)
        dm = re.search(r"^DTSTART[^:\n]*:(\d{8})(?:T(\d{4})(\d{2})?(Z?))?", block, re.M)
        if not sm or not dm:
            continue
        summary = re.sub(r"\\([,;\\])", r"\1", sm.group(1)).strip()
        d = date(int(dm.group(1)[:4]), int(dm.group(1)[4:6]), int(dm.group(1)[6:]))
        if dm.group(2) is None:
            t = et_ms(d, 8, 30)
        elif dm.group(4):
            t = int(datetime(d.year, d.month, d.day, int(dm.group(2)[:2]), int(dm.group(2)[2:]),
                             tzinfo=timezone.utc).timestamp() * 1000)
        else:
            t = et_ms(d, int(dm.group(2)[:2]), int(dm.group(2)[2:]))
        if summary.startswith("Personal Income and Outlays"):
            period = summary.split(",", 1)[1].strip() if "," in summary else ""
            out.append(event("data", "pce", 3, "PCE inflation", t, short="PCE",
                             detail=f"Personal income and outlays for {period}" if period else "Personal income and outlays",
                             via="BEA release calendar"))
        elif m := re.match(r"(?:GDP|Gross Domestic Product) \((Advance|Second|Third) Estimate\)", summary):
            q = re.search(r"(\d)(?:st|nd|rd|th) Quarter(?: and Year)? (\d{4})", summary)
            est = m.group(1).lower()
            out.append(event("data", "gdp", 3 if est == "advance" else 2, "GDP", t, short="GDP",
                             detail=f"Q{q.group(1)} {q.group(2)}, {est} estimate" if q else f"{est.capitalize()} estimate",
                             via="BEA release calendar"))
    return out


def bls_events() -> list[dict]:
    out = []
    for kind, dates in BLS_DATES.items():
        title, short, impact, what = DATA_KINDS[kind]
        for s in dates:
            d = date.fromisoformat(s)
            pm, py = (d.month - 2) % 12 + 1, d.year - (d.month == 1)
            out.append(event("data", kind, impact, title, et_ms(d, 8, 30), s, short=short,
                             detail=f"{what} for {MONTH_NAMES[pm - 1]} {py}", via="BLS release schedule (built in)"))
    return out


# ---------- ForexFactory: this week's forecasts ----------
FF_FAMILIES = [
    ("claims", re.compile(r"unemployment claims|jobless claims", re.I)),
    ("jobs", re.compile(r"non-?farm|unemployment rate|average hourly earnings", re.I)),
    ("cpi", re.compile(r"\bCPI\b")),
    ("ppi", re.compile(r"\bPPI\b")),
    ("pce", re.compile(r"\bPCE\b|personal (income|spending)", re.I)),
    ("gdp", re.compile(r"\bGDP\b")),
    ("minutes", re.compile(r"FOMC meeting minutes", re.I)),
    ("fomc", re.compile(r"FOMC (statement|press conference|economic projections)|federal funds rate", re.I)),
    ("beige", re.compile(r"beige book", re.I)),
    ("trump", re.compile(r"president trump speaks", re.I)),
]
FF_SPEAKER = re.compile(r"^(Fed Chair|Fed Vice Chair|FOMC Member|Fed Governor)\s+(.+?)\s+(Speaks|Testifies)$", re.I)
FF_IMPACT = {"high": 3, "medium": 2, "low": 1}


def base_name(title: str) -> str:
    """'Core Retail Sales m/m' -> 'Retail Sales', so related lines share a row."""
    s = re.sub(r"^(Core|Prelim|Final|Advance|Flash|Revised)\s+", "", title.strip())
    return re.sub(r"\s+(m/m|q/q|y/y|3m/3m)$", "", s).strip()


def parse_ff(rows: list) -> list[dict]:
    """ForexFactory's week -> US groups: lines released together (CPI m/m, core, y/y) share one group."""
    groups: dict[tuple, dict] = {}
    for r in rows or []:
        if not isinstance(r, dict) or r.get("country") != "USD":
            continue
        imp = FF_IMPACT.get(str(r.get("impact") or "").lower())
        if not imp:                        # holidays
            continue
        try:
            t = int(datetime.fromisoformat(str(r["date"])).timestamp() * 1000)
        except (KeyError, ValueError):
            continue
        title = str(r.get("title") or "").strip()
        fam, name, role, testifies = "other", "", "", False
        if sp := FF_SPEAKER.match(title):
            fam, role, name, testifies = "speech", sp.group(1), sp.group(2), sp.group(3).lower() == "testifies"
        elif re.match(r"ADP\b", title):     # ADP's private payrolls report, not the BLS jobs report
            fam = "other"
        else:
            fam = next((f for f, rx in FF_FAMILIES if rx.search(title)), "other")
        key = (fam, t, name or (base_name(title) if fam == "other" else ""))
        g = groups.setdefault(key, {"family": fam, "t": t, "name": name, "role": role, "testifies": testifies,
                                    "title": base_name(title) if fam == "other" else title, "impact": 0,
                                    "values": []})
        g["impact"] = max(g["impact"], imp)
        g["values"].append({"name": title, "forecast": str(r.get("forecast") or ""),
                            "previous": str(r.get("previous") or "")})
    return list(groups.values())


def merge_ff(events: list[dict], groups: list[dict]) -> list[dict]:
    """Add forecasts to matching events (same report within 3 hours, same speaker within 90 minutes);
    medium and high-impact groups that match nothing become events of their own."""
    def near(e, t, minutes):
        return e["t"] is not None and abs(e["t"] - t) <= minutes * 60_000

    for g in groups:
        fam, t = g["family"], g["t"]
        vals = [v for v in g["values"] if v["forecast"] or v["previous"]]
        if fam in ("cpi", "ppi", "jobs", "pce", "gdp", "fomc", "minutes", "beige"):
            target = next((e for e in events if e["kind"] == fam and near(e, t, 180)), None)
        elif fam == "speech":
            target = next((e for e in events if e["kind"] in ("speech", "testimony") and near(e, t, 90)
                           and g["name"].lower() in e["title"].lower()), None)
        elif fam == "trump":
            target = next((e for e in events if e["src"] == "wh" and e["impact"] >= 2 and near(e, t, 45)), None)
        else:
            target = None
        if target:
            have = {v["name"] for v in target["values"]}
            target["values"] = target["values"] + [v for v in vals if v["name"] not in have]
            target["impact"] = max(target["impact"], g["impact"])
            continue
        if g["impact"] < 2:
            continue
        if fam in DATA_KINDS:
            title, short, impact, what = DATA_KINDS[fam]
            events.append(event("data", fam, max(impact, g["impact"]), title, t, short=short, detail=what,
                                via="ForexFactory", values=vals))
        elif fam == "speech":
            label = {"fed chair": "Fed Chair", "fed vice chair": "Fed Vice Chair", "fomc member": "FOMC member",
                     "fed governor": "Fed governor"}.get(g["role"].lower(), "")
            events.append(event("fed", "testimony" if g["testifies"] else "speech", g["impact"],
                                f"{g['name']} {'testifies' if g['testifies'] else 'speaks'}", t, short=g["name"],
                                detail=label, via="ForexFactory"))
        elif fam == "trump":
            events.append(event("wh", "remarks", g["impact"], "President Trump speaks", t, short="Trump",
                                via="ForexFactory"))
        elif fam == "fomc":
            events.append(event("fed", "fomc", 3, "FOMC rate decision", t, short="FOMC", via="ForexFactory",
                                values=vals))
        elif fam in ("minutes", "beige"):
            title = "FOMC minutes" if fam == "minutes" else "Beige Book"
            events.append(event("fed", fam, g["impact"], title, t, short=title, via="ForexFactory"))
        else:
            events.append(event("data", "other", g["impact"], g["title"], t, short=g["title"][:16],
                                via="ForexFactory", values=vals))
    return events


# ---------- everything together ----------
def build(fed: dict | None, fomc_html: str | None, wh: list | None, bea: str | None, ff: list | None,
          now_ms: int) -> list[dict]:
    """All sources -> one list, sorted by time, trimmed to the window the browser uses."""
    def safe(name, fn, *a):
        try:
            return fn(*a)
        except Exception:
            log.exception("calendar: couldn't read the %s", SOURCES[name][2])
            return []

    meetings = safe("fomc", parse_fomc_page, fomc_html) if fomc_html else []
    events = safe("fed", parse_fed, fed, {m["day"].isoformat(): m["sep"] for m in meetings}) if fed else []
    covered = max((e["day"][:7] for e in events if e["kind"] == "fomc"), default="")
    events += fomc_from_page(meetings, covered)
    events += bls_events()
    if bea:
        events += safe("bea", parse_bea, bea)
    if wh:
        events += safe("wh", parse_wh, wh)
    if ff:
        groups = safe("ff", parse_ff, ff)
        events = safe("ff", merge_ff, events, groups) or events

    lo, recent, hi = now_ms - PAST_DAYS * DAY_MS, now_ms - RECENT_HOURS * 3_600_000, now_ms + AHEAD_DAYS * DAY_MS
    recent_day, hi_day = et_day(recent), et_day(hi)

    def keep(e):
        if e["t"] is None:
            return recent_day <= e["day"] <= hi_day
        return lo <= e["t"] <= hi and (e["t"] >= recent or e["impact"] >= 3)

    def start(e):
        return e["t"] if e["t"] is not None else et_ms(date.fromisoformat(e["day"]))

    out, seen = [], set()
    for e in sorted(filter(keep, events), key=lambda e: (start(e), -e["impact"])):
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


class MacroCalendar:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session
        self.raw: dict[str, object] = {k: None for k in SOURCES}
        self.meta = {k: {"ok": False, "updated": None, "error": "", "etag": None, "modified": None}
                     for k in SOURCES}
        self.next_at = {k: 0.0 for k in SOURCES}
        self.events: list[dict] = []
        self.updated: int | None = None
        self.on_update = None
        self._task: asyncio.Task | None = None

    async def _fetch(self, name: str) -> bool:
        """Refresh one source; True if its content changed."""
        url, every, label = SOURCES[name]
        m = self.meta[name]
        headers = {k: v for k, v in (("If-None-Match", m["etag"]), ("If-Modified-Since", m["modified"])) if v}
        try:
            async with self.s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=45)) as r:
                if r.status == 304:
                    changed = False
                else:
                    r.raise_for_status()
                    body = (await r.read()).decode("utf-8-sig", errors="replace")
                    self.raw[name] = json.loads(body) if name in ("fed", "wh", "ff") else body
                    m["etag"], m["modified"] = r.headers.get("ETag"), r.headers.get("Last-Modified")
                    m["updated"] = int(time.time() * 1000)
                    changed = True
            if not m["ok"] and m["error"]:
                log.info("calendar: %s is back", label)
            m.update(ok=True, error="")
            self.next_at[name] = time.time() + every
            return changed
        except Exception as e:
            if not m["error"]:
                log.warning("calendar: %s unavailable: %s", label, e or type(e).__name__)
            m.update(ok=False, error=str(e) or type(e).__name__)
            self.next_at[name] = time.time() + RETRY_SECONDS
            return False

    def rebuild(self) -> None:
        self.events = build(self.raw["fed"], self.raw["fomc"], self.raw["wh"], self.raw["bea"], self.raw["ff"],
                            int(time.time() * 1000))
        self.updated = int(time.time() * 1000)

    def snapshot(self) -> dict:
        return {"ready": self.updated is not None, "updated": self.updated, "events": self.events,
                "bls_until": BLS_LAST,
                "sources": {k: {"label": SOURCES[k][2], "url": SOURCES[k][0], "ok": m["ok"],
                                "updated": m["updated"], "error": m["error"]} for k, m in self.meta.items()}}

    async def run(self) -> None:
        if (date.fromisoformat(BLS_LAST) - date.today()).days < 60:
            log.warning("calendar: built-in BLS dates end on %s; add next year's from OMB's schedule "
                        "(see BLS_DATES in macro.py)", BLS_LAST)
        while True:
            now = time.time()
            due = [k for k, at in self.next_at.items() if now >= at]
            if due:
                changed = await asyncio.gather(*(self._fetch(k) for k in due))
                if any(changed) or self.updated is None:
                    try:
                        self.rebuild()
                    except Exception:
                        log.exception("calendar: couldn't build the event list")
                    if self.on_update:
                        try:
                            await self.on_update(self.snapshot())
                        except Exception as e:
                            log.warning("calendar: couldn't send the update: %s", e)
            await asyncio.sleep(20)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
