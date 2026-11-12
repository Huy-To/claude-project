from datetime import date, datetime, timezone

from btcliq import macro
from btcliq.macro import (BLS_DATES, build, bls_events, et_ms, fomc_from_page, merge_ff, parse_12h, parse_bea,
                          parse_fed, parse_ff, parse_fomc_page, parse_wh, speaker, wh_impact, wh_title)


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def test_eastern_time_follows_daylight_saving():
    assert utc(et_ms(date(2026, 12, 9), 14, 0)) == "2026-12-09 19:00"   # EST, UTC-5
    assert utc(et_ms(date(2026, 7, 29), 14, 0)) == "2026-07-29 18:00"   # EDT, UTC-4


def test_parse_12h():
    assert parse_12h("2:00 p.m.") == (14, 0)
    assert parse_12h("10:50 a.m.") == (10, 50)
    assert parse_12h("12:15 p.m.") == (12, 15)
    assert parse_12h("12:30 a.m.") == (0, 30)
    assert parse_12h("Noon") == (12, 0)
    assert parse_12h("") is None


def test_speaker_formats():
    assert speaker("Speech - Vice Chair Philip N. Jefferson") == ("Speech", "Vice Chair", "Philip N. Jefferson", "Jefferson")
    assert speaker("Speech--Governor Lael Brainard")[1:] == ("Governor", "Lael Brainard", "Brainard")
    assert speaker("Testimony -- Chairman Kevin Warsh")[1] == "Chair"
    assert speaker("Discussion - Vice Chair for Supervision Michelle W. Bowman")[1] == "Vice Chair for Supervision"
    assert speaker("Testimony - Randall D. Guynn, Director, Division of Supervision")[1:] == ("", "Randall D. Guynn", "Guynn")


FED = {"events": [
    {"type": "FOMC", "title": "FOMC Meeting", "month": "2026-12", "days": "9", "time": "2:00 p.m.",
     "description": "&lt;p&gt;Two-day meeting, December 8 - 9&lt;/p&gt;&#10;&#10;&lt;p&gt;Press Conference&lt;/p&gt;"},
    {"type": "FOMC", "title": "FOMC Press Conference", "month": "2026-12", "days": "9", "time": "2:30 p.m."},
    {"type": "FOMC", "title": " FOMC Minutes", "month": "2026-11", "days": "18", "time": "2:00 p.m.",
     "description": "&lt;p&gt;Meeting of October 27-28&lt;/p&gt;"},
    {"type": "Testimony", "title": "Testimony - Chairman Kevin Warsh", "month": "2027-02", "days": "10",
     "time": "10:00 a.m.", "description": "Semiannual Monetary Policy Report to Congress"},
    {"type": "Speeches", "title": "Speech - Governor Christopher J. Waller", "month": "2026-11", "days": "16",
     "time": "4:00 a.m.", "description": "Economic Outlook", "live": "https://example.org/live"},
    {"type": "Speeches", "title": "Discussion - Governor Michael S. Barr", "month": "2026-11", "days": "17",
     "time": "10:00 a.m.", "description": "Community Banking"},
    {"type": "Beige", "title": "Beige Book", "month": "2026-11", "days": "25", "time": "2:00 p.m."},
    {"type": "Stat", "title": "H.4.1 - Factors Affecting Reserve Balances", "month": "2026-11", "days": "5, 12, 19"},
    {"type": "Other", "title": "Holiday - Thanksgiving Day", "month": "2026-11", "days": "26", "time": ""},
    {},
]}


def test_parse_fed():
    ev = {e["kind"] + ":" + e["day"]: e for e in parse_fed(FED, {"2026-12-09": True})}
    assert set(ev) == {"fomc:2026-12-09", "minutes:2026-11-18", "testimony:2027-02-10", "speech:2026-11-16",
                       "speech:2026-11-17", "beige:2026-11-25"}
    fomc = ev["fomc:2026-12-09"]
    assert fomc["impact"] == 3 and fomc["title"] == "FOMC rate decision" and utc(fomc["t"]) == "2026-12-09 19:00"
    assert fomc["detail"] == ("Press conference 30 min later. New economic projections and dot plot. "
                              "Two-day meeting, December 8–9.")
    assert ev["minutes:2026-11-18"]["detail"] == "From the October 27–28 meeting"
    assert ev["testimony:2027-02-10"]["title"] == "Chair Warsh testifies" and ev["testimony:2027-02-10"]["impact"] == 3
    waller = ev["speech:2026-11-16"]
    assert waller["title"] == "Governor Waller speaks" and waller["impact"] == 2   # economic outlook
    assert waller["link"] == "https://example.org/live" and waller["short"] == "Waller"
    barr = ev["speech:2026-11-17"]
    assert barr["title"] == "Governor Barr in a discussion" and barr["impact"] == 1


FOMC_HTML = """
<h4><a id="1">2027 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>January</strong></div><div class="fomc-meeting__date col">26-27</div>
<div class="fomc-meeting__month col"><strong>March</strong></div><div class="fomc-meeting__date col">16-17*</div>
<div class="fomc-meeting__month col"><strong>Apr/May</strong></div><div class="fomc-meeting__date col">30-1</div>
<h4><a id="2">2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>December</strong></div><div class="fomc-meeting__date col">8-9*</div>
<div class="fomc-meeting__month col"><strong>October</strong></div><div class="fomc-meeting__date col">22 (notation vote)</div>
"""


def test_fomc_page_and_next_year():
    meetings = parse_fomc_page(FOMC_HTML)
    assert [(m["start"].isoformat(), m["day"].isoformat(), m["sep"]) for m in meetings] == [
        ("2027-01-26", "2027-01-27", False), ("2027-03-16", "2027-03-17", True),
        ("2027-04-30", "2027-05-01", False), ("2026-12-08", "2026-12-09", True)]
    ev = fomc_from_page(meetings, "2026-12")      # the Fed's JSON already covers 2026
    assert [e["day"] for e in ev] == ["2027-01-27", "2027-03-17", "2027-05-01"]
    assert utc(ev[0]["t"]) == "2027-01-27 19:00"
    assert ev[1]["detail"] == ("Press conference 30 min later. New economic projections and dot plot. "
                               "Two-day meeting, March 16–17.")
    assert ev[2]["detail"].endswith("Two-day meeting, April 30–May 1.")


def wh(details, time="10:00:00", typ="President Schedule", coverage="Closed Press", day="2026-11-12", where="Oval Office"):
    return {"date": day, "time": time, "type": typ, "details": details, "coverage": coverage, "location": where}


def test_white_house_schedule():
    ev = parse_wh([
        wh("The President participates in Executive Time", "08:00:00"),
        wh("The President receives his Intelligence Briefing", "11:00:00"),
        wh("The President delivers remarks on tariffs", "14:00:00", coverage="Open Press"),
        wh("The President meets with the Prime Minister of Japan", "15:00:00"),
        wh("TBD: The President and the First Lady depart the White House en route Arlington", None),
        wh("White House Press Office: Lunch lid until 1:15 PM", "12:13:00", typ="Pool Report Schedule"),
        wh("The President departs The White House en route to Joint Base Andrews", "16:00:00", typ="Pool Report Schedule"),
        wh("The President calls an unscheduled press conference at the White House", "16:41:00",
           typ="Pool Report Schedule", where="James S. Brady Press Briefing Room"),
        wh("Press Briefing by Press Secretary", "13:00:00", typ="Briefing Schedule:"),
    ])
    got = [(e["title"], e["kind"], e["impact"]) for e in ev]
    assert got == [
        ("Executive Time", "schedule", 1),
        ("Intelligence Briefing", "schedule", 1),
        ("Delivers remarks on tariffs", "schedule", 3),
        ("Meets with the Prime Minister of Japan", "schedule", 2),
        ("The President and the First Lady depart the White House en route Arlington", "schedule", 1),
        ("Calls an unscheduled press conference at the White House", "pool", 3),
        ("Press Briefing by Press Secretary", "briefing", 2),
    ]
    assert utc(ev[0]["t"]) == "2026-11-12 13:00" and ev[0]["detail"] == "Oval Office, closed press"
    assert ev[4]["t"] is None and ev[4]["day"] == "2026-11-12"
    assert ev[5]["detail"] == "Pool report, James S. Brady Press Briefing Room"


def test_white_house_impact_and_titles():
    assert wh_impact("The President participates in a Policy Meeting on tariffs") == 1   # closed door
    assert wh_impact("The President participates in a Pre-Tape Interview") == 1
    assert wh_impact("The President delivers an Address to the Nation") == 3
    assert wh_impact("The President signs an executive order on digital assets") == 3
    assert wh_impact("The President greets guests", "Open Press") == 2
    assert wh_title("The President and The First Lady participate in a ceremony").startswith("The President and")


BEA_ICS = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Personal Income and Outlays\\, October 2026\r\n"
           "DTSTART:20261125T133000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:GDP (Advance Estimate)\\, 4th Quarter\r\n"
           "  and Year 2026\r\nDTSTART;VALUE=DATE-TIME:20270128T133000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\n"
           "SUMMARY:GDP by County and Personal Income by County\\, 2025\r\nDTSTART:20261202T133000Z\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nSUMMARY:U.S. International Trade in Goods and Services\\, October 2026\r\n"
           "DTSTART:20261208T133000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_parse_bea():
    ev = parse_bea(BEA_ICS)
    assert [(e["kind"], e["impact"], e["detail"], utc(e["t"])) for e in ev] == [
        ("pce", 3, "Personal income and outlays for October 2026", "2026-11-25 13:30"),
        ("gdp", 3, "Q4 2026, advance estimate", "2027-01-28 13:30"),
    ]


def test_bls_dates():
    for d in BLS_DATES["jobs"]:
        assert date.fromisoformat(d).weekday() == 4, d          # the jobs report comes out on Fridays
    ev = {(e["kind"], e["day"]): e for e in bls_events()}
    cpi = ev[("cpi", "2027-01-13")]
    assert cpi["detail"] == "Consumer prices for December 2026" and utc(cpi["t"]) == "2027-01-13 13:30"
    assert ev[("jobs", "2026-12-04")]["impact"] == 3 and ev[("ppi", "2026-12-15")]["impact"] == 2


def ff(title, when, impact="High", forecast="", previous="", country="USD"):
    return {"title": title, "country": country, "date": when, "impact": impact, "forecast": forecast, "previous": previous}


def test_forexfactory_merges_into_known_events():
    base = bls_events() + parse_fed(FED)
    rows = [
        ff("CPI m/m", "2026-12-10T08:30:00-05:00", forecast="0.3%", previous="0.6%"),
        ff("Core CPI m/m", "2026-12-10T08:30:00-05:00", forecast="0.2%", previous="0.3%"),
        ff("CPI y/y", "2026-12-10T08:30:00-05:00", impact="Low", forecast="2.9%", previous="3.0%"),
        ff("PPI m/m", "2026-12-15T08:30:00-05:00", forecast="0.4%", previous="1.3%"),
        ff("ADP Non-Farm Employment Change", "2026-12-02T08:15:00-05:00", forecast="45K", previous="42K"),
        ff("Federal Funds Rate", "2026-12-09T14:00:00-05:00", forecast="3.50%", previous="3.75%"),
        ff("FOMC Statement", "2026-12-09T14:00:00-05:00"),
        ff("FOMC Member Waller Speaks", "2026-11-16T04:00:00-05:00", impact="Medium"),
        ff("FOMC Member Hammack Speaks", "2026-11-17T12:00:00-05:00", impact="Medium"),
        ff("Core Retail Sales m/m", "2026-12-16T08:30:00-05:00", forecast="0.3%", previous="0.2%"),
        ff("Retail Sales m/m", "2026-12-16T08:30:00-05:00", forecast="0.2%", previous="0.1%"),
        ff("Crude Oil Inventories", "2026-12-16T10:30:00-05:00", impact="Low"),
        ff("Bank Holiday", "2026-11-26T08:00:00-05:00", impact="Holiday"),
        ff("CPI y/y", "2026-12-10T08:30:00-05:00", country="EUR"),
    ]
    ev = merge_ff(base, parse_ff(rows))
    by = lambda kind, day: [e for e in ev if e["kind"] == kind and e["day"] == day]
    cpi = by("cpi", "2026-12-10")
    assert len(cpi) == 1 and [v["name"] for v in cpi[0]["values"]] == ["CPI m/m", "Core CPI m/m", "CPI y/y"]
    ppi = by("ppi", "2026-12-15")[0]
    assert ppi["impact"] == 3 and ppi["values"][0]["forecast"] == "0.4%"     # ForexFactory rates it high
    assert by("fomc", "2026-12-09")[0]["values"] == [{"name": "Federal Funds Rate", "forecast": "3.50%", "previous": "3.75%"}]
    assert not by("jobs", "2026-12-02")                                     # ADP isn't the jobs report
    assert [e["title"] for e in ev if e["day"] == "2026-12-02"] == ["ADP Non-Farm Employment Change"]
    assert len(by("speech", "2026-11-16")) == 1                             # merged with the Fed's own entry
    hammack = by("speech", "2026-11-17")
    assert {e["title"] for e in hammack} == {"Governor Barr in a discussion", "Hammack speaks"}
    retail = [e for e in ev if e["title"] == "Retail Sales"]
    assert len(retail) == 1 and len(retail[0]["values"]) == 2 and retail[0]["impact"] == 3
    assert not [e for e in ev if "Crude" in e["title"] or "Holiday" in e["title"]]


def test_build_window():
    now = et_ms(date(2026, 11, 12), 1, 0)
    items = [wh("The President participates in a Policy Meeting", "10:00:00", day="2026-10-20"),
             wh("The President delivers an Address to the Nation", "21:00:00", day="2026-10-20"),
             wh("TBD: The President departs for Florida", None, day="2026-11-13")]
    ev = build(FED, FOMC_HTML, items, BEA_ICS, None, now)
    titles = [e["title"] for e in ev]
    assert "Policy Meeting" not in titles                     # old and low impact
    assert "Delivers an Address to the Nation" in titles      # old but high impact: kept for the chart
    assert "Departs for Florida" in titles                    # date only
    assert titles.count("FOMC rate decision") == 4            # Dec 2026 from the JSON; 2027 from the page
    starts = [e["t"] if e["t"] is not None else et_ms(date.fromisoformat(e["day"])) for e in ev]
    assert starts == sorted(starts)
    assert all(macro.DAY_MS * -macro.PAST_DAYS <= s - now <= macro.DAY_MS * macro.AHEAD_DAYS for s in starts)
