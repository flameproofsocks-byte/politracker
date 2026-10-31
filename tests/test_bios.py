"""Politician profile facts: pure helpers (age, spans, service length, links, wikidata parsing) and the summary page."""
import json
from datetime import date

import pytest

from politracker import bios, config, db

TODAY = date(2026, 10, 19)


def term(kind, start, end, state="WA", district=None, party="Democrat", **extra):
    return {"type": kind, "start": start, "end": end, "state": state, "party": party,
            **({"district": district} if district is not None else {}), **extra}


# ---------------------------------------------------------------- pure helpers

def test_age_on_handles_birthdays_and_missing():
    assert bios.age_on("1958-10-13", date(2026, 10, 12)) == 67      # day before the birthday
    assert bios.age_on("1958-10-13", date(2026, 10, 13)) == 68      # on it
    assert bios.age_on("2000-02-29", date(2026, 2, 28)) == 25       # leap-day birthday
    assert bios.age_on(None, TODAY) is None


def test_term_spans_merge_contiguous_terms_and_split_on_gaps_or_changes():
    terms = [term("rep", "1993-01-05", "1995-01-03", district=1), term("sen", "2001-01-03", "2007-01-03", class_=1),
             term("sen", "2007-01-03", "2013-01-03"), term("sen", "2013-01-03", "2019-01-03", party="Independent")]
    spans = bios.term_spans(terms)
    assert [(s["chamber"], s["start"], s["end"], s["party"]) for s in spans] == [
        ("House", "1993-01-05", "1995-01-03", "Democrat"),
        ("Senate", "2001-01-03", "2013-01-03", "Democrat"),            # two contiguous Senate terms merged
        ("Senate", "2013-01-03", "2019-01-03", "Independent")]          # party change splits the span
    gap = bios.term_spans([term("rep", "2003-01-03", "2005-01-03", district=2), term("rep", "2009-01-03", "2011-01-03", district=2)])
    assert len(gap) == 2                                                # a 4-year break is not merged
    assert bios.term_spans([term("prez", "2001-01-20", "2009-01-20")]) == []   # non-Congress offices ignored


def test_service_days_caps_at_today_and_dedupes_overlap():
    terms = [term("rep", "1993-01-05", "1995-01-03"), term("sen", "2001-01-03", "2031-01-03")]
    days = bios.service_days(terms, TODAY)
    assert days == (date(1995, 1, 3) - date(1993, 1, 5)).days + (TODAY - date(2001, 1, 3)).days
    assert bios.human_duration(days) == "27 years, 9 months"
    overlap = [term("rep", "2001-01-03", "2005-01-03"), term("rep", "2003-01-03", "2007-01-03")]
    assert bios.service_days(overlap, date(2030, 1, 1)) == (date(2007, 1, 3) - date(2001, 1, 3)).days   # union, not sum
    assert bios.service_days([term("rep", "2030-01-03", "2032-01-03")], TODAY) == 0                      # not started yet
    assert bios.human_duration(0) == "< 1 month" and bios.human_duration(400) == "1 year, 1 month"


def test_caucus_and_current_term():
    ind = [term("sen", "2025-01-03", "2031-01-03", party="Independent", caucus="Democrat")]
    assert bios.caucus_letter(ind) == "D" and bios.caucus_letter([term("sen", "2025-01-03", "2031-01-03")]) is None
    assert bios.current_term(ind, TODAY)["end"] == "2031-01-03"
    assert bios.current_term([term("rep", "2019-01-03", "2021-01-03")], TODAY) is None
    assert bios.current_term([term("rep", "2025-01-03", "2027-01-03")], date(2027, 1, 3)) is not None   # holds office through the end date


def test_profile_links_are_derived_and_safe():
    links = bios.profile_links({"wikipedia": "Maria Cantwell", "bioguide": "C000127", "govtrack": 300018},
                               {"twitter": "SenatorCantwell", "youtube_id": "UC123", "facebook": "sen cantwell"},
                               [term("sen", "2025-01-03", "2031-01-03", url="https://www.cantwell.senate.gov")])
    by = {label: (g, url) for g, label, url in links}
    assert by["X / Twitter"] == ("social", "https://x.com/SenatorCantwell")
    assert by["YouTube"][1] == "https://www.youtube.com/channel/UC123"
    assert by["Facebook"][1] == "https://www.facebook.com/sen%20cantwell"            # quoted, not raw
    assert by["Wikipedia"][1] == "https://en.wikipedia.org/wiki/Maria_Cantwell"
    assert by["Official website"][1] == "https://www.cantwell.senate.gov"
    assert all(u.startswith("https://") for _, _, u in links)
    evil = bios.profile_links({}, {}, [term("sen", "2025-01-03", "2031-01-03", url="javascript:alert(1)")])
    assert evil == []                                                                # non-http(s) site URLs are dropped


def test_parse_wikidata_labels_and_death_date():
    entity = {"claims": {
        "P19": [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q1"}}}}],
        "P69": [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q2"}}}}],
        "P106": [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q3"}}}},
                 {"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q4"}}}}],
        "P570": [{"mainsnak": {"datavalue": {"type": "time", "value": {"time": "+2025-03-05T00:00:00Z"}}}}]}}
    out = bios.parse_wikidata(entity, {"Q1": "Indianapolis", "Q2": "Miami University", "Q3": "politician", "Q4": "lawyer"})
    assert out == {"birth_place": "Indianapolis", "education": ["Miami University"], "occupations": ["lawyer"], "died": "2025-03-05"}
    assert sorted(bios.wikidata_label_ids(entity)) == ["Q1", "Q2", "Q3", "Q4"]
    assert bios.parse_wikidata({}, {})["birth_place"] is None


def test_build_facts_for_a_sitting_senator_a_former_member_and_a_deceased_one():
    sen = {"birthday": "1958-10-13", "social": {}, "ids": {},
           "terms": [term("rep", "1993-01-05", "1995-01-03", district=1), term("sen", "2001-01-03", "2031-01-03", state_rank="junior")]}
    f = bios.build_facts({"party": "D"}, sen, {"birth_place": "Indianapolis", "education": ["Miami University"], "occupations": [], "died": None}, TODAY)
    assert f["office"] == "Junior U.S. Senator from Washington" and f["age"] == 68 and f["since"] == "January 3, 2001"
    assert f["term_end"] == "January 3, 2031" and f["service"] == "27 years, 9 months" and f["party"] == "Democratic Party"
    assert [t["years"] for t in f["timeline"]] == ["1993–1995", "2001–present"] and f["birth_place"] == "Indianapolis"
    rep = {"birthday": "1950-05-05", "social": {}, "ids": {}, "terms": [term("rep", "2015-01-03", "2023-01-03", district=7)]}
    g = bios.build_facts({"party": "D"}, rep, None, TODAY)
    assert g["office"] is None and g["left_office"] == "January 3, 2023" and g["age"] is None          # no age claimed for former members
    dead = bios.build_facts({"party": "D"}, {**rep, "terms": [term("rep", "2019-01-03", "2025-03-05", district=18)]},
                            {"died": "2025-03-05"}, TODAY)
    assert dead["died"] == "March 5, 2025" and dead["age_at_death"] == 74 and dead["age"] is None
    ind = bios.build_facts({"party": "I"}, {"birthday": "1941-09-08", "social": {}, "ids": {}, "terms": [
        term("sen", "2025-01-03", "2031-01-03", state="VT", party="Independent", caucus="Democrat")]}, None, TODAY)
    assert ind["party"] == "Independent (caucuses with the Democrats)"
    at_large = bios.build_facts({"party": "R"}, {"terms": [term("rep", "2025-01-03", "2027-01-03", state="WY", district=0, party="Republican")],
                                                "birthday": None, "social": {}, "ids": {}}, None, TODAY)
    assert at_large["office"] == "U.S. Representative for Wyoming (at-large)"
    assert bios.build_facts({"party": "D"}, {"terms": [term("rep", "2025-01-03", "2027-01-03", state="WA", district=1)], "birthday": None,
                                             "social": {}, "ids": {}}, None, TODAY)["office"] == "U.S. Representative for Washington's 1st district"


def test_get_wiki_caches_and_survives_being_offline(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "w.db")
    conn = db.connect()
    conn.execute("INSERT INTO members (member_id, full_name, party, state, chamber) VALUES ('X1','X One','D','WA','Senate')")
    conn.execute("INSERT INTO member_bios (member_id, wikipedia_title, wikidata_id) VALUES ('X1', 'X One', 'Q1')")
    conn.commit()
    calls = []
    monkeypatch.setattr(bios, "fetch_wiki", lambda t, q: calls.append(t) or {"extract": "hello", "died": None})
    assert bios.get_wiki(conn, "X1", today=date(2026, 1, 1))["extract"] == "hello" and len(calls) == 1
    bios.get_wiki(conn, "X1", today=date(2026, 1, 20))                           # 19 days later: still cached
    assert len(calls) == 1
    def offline(t, q): raise urllib.error.URLError("no network")
    monkeypatch.setattr(bios, "fetch_wiki", offline)
    assert bios.get_wiki(conn, "X1", today=date(2026, 6, 1))["extract"] == "hello"   # stale cache beats an error
    conn.execute("UPDATE member_bios SET wiki_json = NULL, wiki_fetched = NULL"); conn.commit()
    assert bios.get_wiki(conn, "X1", today=date(2026, 6, 1)) is None                  # nothing cached + offline -> None, no exception
    assert bios.get_wiki(conn, "NOPE") is None


# ---------------------------------------------------------------- the page

def _give_bio(client_db_path=None, member_id="A000370", **kw):
    conn = db.connect()
    terms = kw.pop("terms", [term("rep", "2021-01-03", "2027-01-03", state="NC", district=12)])
    conn.execute("INSERT OR REPLACE INTO member_bios (member_id, birthday, gender, terms_json, social_json, ids_json, wikipedia_title) "
                 "VALUES (?,?,?,?,?,?,?)", (member_id, kw.get("birthday", "1963-10-05"), "F", json.dumps(terms),
                 json.dumps({"twitter": "RepAdams"}), json.dumps({"bioguide": member_id, "wikipedia": "Alma Adams"}), "Alma Adams"))
    conn.commit()


def test_politician_page_summary_without_individual_votes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    _give_bio()
    monkeypatch.setattr(bios, "fetch_wiki", lambda t, q: {"extract": "Alma Adams is an American politician.", "description": "Representative",
                        "url": "https://en.wikipedia.org/wiki/Alma_Adams", "birth_place": "Newport News", "education": ["Bennett College"],
                        "occupations": ["professor"], "died": None})
    r = client.get("/politician/A000370")
    page = r.data.decode()
    assert r.status_code == 200
    assert "Alma Adams is an American politician." in page and "CC BY-SA" in page and "Newport News" in page and "Bennett College" in page
    assert "U.S. Representative for North Carolina&#39;s 12th district" in page or "North Carolina's 12th district" in page
    assert "With their party:" in page and "With your picks:" in page and "Showed up to vote:" in page
    assert "https://x.com/RepAdams" in page and "Congress.gov" in page
    assert "/vote/" not in page                                        # a summary: no individual votes listed
    assert 'class="tabs"' in page and 'href="/member/A000370"' in page   # tab to the votes page
    assert client.get("/politician/NOPE").status_code == 404


def test_politician_page_party_participation_and_picks_numbers(client, monkeypatch, tmp_path):
    from politracker import web
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    monkeypatch.setattr(bios, "fetch_wiki", lambda t, q: {})
    # Adams (D) voted Nay on the GENIUS passage; most Democrats also voted Nay on it, so he sided with his party
    with web.app.test_request_context():
        pa = web.party_alignment("A000370")
        assert pa["all"]["n"] == 1 and pa["passage"]["n"] == 1 and pa["all"]["pct"] in (0.0, 100.0)
        part = web.participation("A000370")
        assert part["total"] == 1 and part["cast"] == 1 and part["missed"] == 0 and part["pct"] == 100.0
    client.post("/bill/119/S%201582/star", headers={"X-Politracker": "1"})         # agree; Adams voted Nay -> 0%
    page = client.get("/politician/A000370").data.decode()
    assert "0.0%" in page and "voted the way you want on 0 of 1" in page


def test_independents_are_compared_with_the_party_they_caucus_with(client, monkeypatch, tmp_path):
    from politracker import web
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    monkeypatch.setattr(bios, "fetch_wiki", lambda t, q: {})
    conn = db.connect()                                  # find a real independent in the Senate fixture (Vermont)
    ind = conn.execute("SELECT member_id FROM members WHERE party = 'I' LIMIT 1").fetchone()[0]
    _give_bio(member_id=ind, terms=[term("sen", "2025-01-03", "2031-01-03", state="VT", party="Independent", caucus="Democrat")])
    with web.app.test_request_context():
        assert web.compare_party({"party": "I", "member_id": ind}) == "'D'"
        assert web.compare_party({"party": "D", "member_id": ind}) is None
        vs_dems = web.party_alignment(ind, "'D'")["all"]
        vs_own = web.party_alignment(ind)["all"]
        assert vs_dems["n"] >= 1 and vs_own["n"] >= 1
    page = client.get(f"/politician/{ind}").data.decode()
    assert "the Democrats (the party they caucus with)" in page
    assert "the party they caucus with" in client.get(f"/member/{ind}?scope=all").data.decode()   # votes page agrees


def test_member_with_no_bio_still_gets_a_page(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    page = client.get("/politician/A000055")             # Aderholt has no member_bios row in the fixture DB
    assert page.status_code == 200 and b"With their party:" in page.data


def test_photo_url_and_name_links_lead_to_the_summary(client):
    from politracker import web
    with web.app.test_request_context():
        assert web.photo_url("C000127").endswith("/C000127.jpg") and web.photo_url("LIS-S428") is None
    assert "/politician/A000370" in client.get("/?member=Adams").data.decode()
    vote = client.get("/vote/H-2025-200").data.decode()
    assert "/politician/A000370" in vote and "/member/A000370" not in vote
