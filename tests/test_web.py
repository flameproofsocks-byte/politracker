import json
import pytest

from politracker import config, db, ingest, parse
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def test_pages_render(client):
    assert b"GENIUS" in client.get("/votes?scope=all").data
    assert b"GENIUS" not in client.get("/votes?scope=passage&chamber=Senate").data
    assert client.get("/vote/H-2025-200").status_code == 200
    assert client.get("/vote/nope").status_code == 404
    page = client.get("/?member=Adams").data
    assert b"/politician/A000370" in page
    m = client.get("/member/A000370?scope=all")
    assert m.status_code == 200 and b"GENIUS" in m.data and b"Nay" in m.data


def test_topic_filter_and_bill_panel(client):
    conn = db.connect()
    conn.execute("INSERT INTO bills (congress, bill_id, title, policy_area, sponsor_name, became_law, law_number, url) "
                 "VALUES (119, 'S 1582', 'Guiding and Establishing National Innovation for U.S. Stablecoins', "
                 "'Finance and Financial Sector', 'Sen. Hagerty', 1, '119-27', 'https://example/bill')")
    conn.commit()
    assert b"GENIUS" in client.get("/votes?scope=all&topic=Finance+and+Financial+Sector").data
    assert b"GENIUS" not in client.get("/votes?scope=all&topic=Health").data
    assert b"Finance and Financial" in client.get("/votes?scope=all&q=financial").data   # topic is searchable
    page = client.get("/vote/H-2025-200").data
    assert b"Bill details" in page and b"119-27" in page
    assert b"By topic" in client.get("/member/A000370?scope=all").data


def test_summary_endpoint(client, monkeypatch):
    from politracker import summarize
    calls = []
    monkeypatch.setattr(summarize.congress, "get_key", lambda: "k")
    monkeypatch.setattr(summarize.congress, "fetch_source_text", lambda c, b, k: ("CRS summary (Test)", "bill text"))
    monkeypatch.setattr(summarize, "run_llm", lambda system, user: calls.append(user) or "WHAT IT DOES: stuff")
    url = "/bill/119/S%201582/summary"
    assert client.post(url).status_code == 403                                   # no CSRF header
    assert client.post("/bill/119/HR%209999/summary", headers={"X-Politracker": "1"}).status_code == 404
    r = client.post(url, headers={"X-Politracker": "1"})
    assert r.status_code == 200 and r.get_json()["summary"] == "WHAT IT DOES: stuff"
    client.post(url, headers={"X-Politracker": "1"})
    assert len(calls) == 1                                                       # second hit served from cache
    assert b"WHAT IT DOES: stuff" in client.get("/vote/H-2025-200").data        # shown on page, button hidden
    assert b"<source>" in calls[0].encode() and b"S 1582" in calls[0].encode()


def test_summary_errors_surface(client, monkeypatch):
    from politracker import summarize
    monkeypatch.setattr(summarize.congress, "get_key", lambda: "k")
    monkeypatch.setattr(summarize.congress, "fetch_source_text", lambda c, b, k: None)
    r = client.post("/bill/119/S%201582/summary", headers={"X-Politracker": "1"})
    assert r.status_code == 502 and "too new" in r.get_json()["error"]


def test_summary_truncation_is_disclosed(client, monkeypatch):
    from politracker import summarize
    seen = []
    monkeypatch.setattr(config, "SUMMARY_MAX_CHARS", 10)
    monkeypatch.setattr(summarize.congress, "get_key", lambda: "k")
    monkeypatch.setattr(summarize.congress, "fetch_source_text", lambda c, b, k: ("Bill text (Enrolled)", "x" * 50))
    monkeypatch.setattr(summarize, "run_llm", lambda system, user: seen.append(user) or "ok")
    r = client.post("/bill/119/S%201582/summary", headers={"X-Politracker": "1"}).get_json()
    assert r["truncated"] is True and "x" * 11 not in seen[0] and "cut off for length" in seen[0]
    assert b"cut for length" in client.get("/vote/H-2025-200").data


H = {"X-Politracker": "1"}


def test_star_cycles_blank_green_red_blank(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    url = "/bill/119/S%201582/star"
    assert client.post(url).status_code == 403                                           # no CSRF header
    assert client.post("/bill/119/HR%209999/star", headers=H).status_code == 404
    assert client.post(url, headers=H).get_json() == {"stance": "agree"}
    assert b'data-stance="agree"' in client.get("/vote/H-2025-200").data
    assert client.post(url, headers=H).get_json() == {"stance": "disagree"}
    assert b'data-stance="disagree"' in client.get("/vote/H-2025-200").data
    assert client.post(url, headers=H).get_json() == {"stance": None}
    assert b'data-stance="agree"' not in client.get("/vote/H-2025-200").data
    assert json.loads((tmp_path / "saved.json").read_text()) == []


def test_legacy_plain_stars_load_as_agree(client, monkeypatch, tmp_path):
    f = tmp_path / "saved.json"
    f.write_text('[[119, "S 1582"]]')                      # pre-stance file format
    monkeypatch.setattr(config, "SAVED_PATH", f)
    assert b'data-stance="agree"' in client.get("/vote/H-2025-200").data
    client.post("/bill/119/S%205/star", headers=H)         # first write upgrades the file
    assert json.loads(f.read_text()) == [[119, "S 1582", "agree"], [119, "S 5", "agree"]]


def _picks(client, member):
    import re
    html = client.get(f"/member/{member}?scope=all").data.decode()
    m = re.search(r"With your picks:</b>\s*(.*?)</div>", html, re.S)
    return re.sub(r"<[^>]+>", " ", m.group(1)), html


def test_member_alignment_with_your_picks(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    text, _ = _picks(client, "A000370")
    assert "thumbs up or thumbs down" in text                                     # nothing rated yet
    client.post("/bill/119/S%201582/star", headers=H)                         # green: you agree with GENIUS Act
    # Adams voted Nay, Aderholt voted Yea on its House passage
    text, html = _picks(client, "A000370")
    assert "0.0%" in text and "0 of 1" in text and 'class="match-no"' in html     # opposed what you want
    text, html = _picks(client, "A000055")
    assert "100.0%" in text and "1 of 1" in text and 'class="match-yes"' in html
    client.post("/bill/119/S%201582/star", headers=H)                         # flip to red: you disagree
    assert "100.0%" in _picks(client, "A000370")[0] and "0.0%" in _picks(client, "A000055")[0]


def test_procedural_votes_are_not_scored(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    client.post("/bill/119/S%205/star", headers=H)                            # S 5 only has a cloture vote
    text, html = _picks(client, "LIS-S428")                                   # Alsobrooks voted Yea on cloture
    assert "none of your 1 rated bills" in text
    assert 'class="match-yes"' not in html and 'class="match-no"' not in html


def test_sql_and_python_match_rules_agree(client, monkeypatch, tmp_path):
    from politracker import web
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    url = "/bill/119/S%201582/star"
    for expected in ("agree", "disagree"):                  # first click -> green, second click -> red
        assert client.post(url, headers=H).get_json() == {"stance": expected}
        with web.app.test_request_context():
            rows = web.q(f"SELECT mv.position, v.is_passage, {web.MATCH_SQL} AS m FROM member_votes mv "
                         "JOIN votes v USING (vote_id) WHERE v.vote_id = 'H-2025-200'")
            assert {r["position"] for r in rows} >= {"Yea", "Nay"}      # both outcomes are exercised
            assert all(r["m"] == web.vote_matches(expected, r["position"], r["is_passage"]) for r in rows)


def _order(client, url):
    import re
    html = client.get(url).data.decode()
    table = html.split('class="starcol"', 1)[1]            # first starcol is the table header
    return re.findall(r"/vote/([HS]-[\w-]+)\"", table)


def test_default_order_green_then_red_then_unrated(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    client.post("/bill/119/S%205/star", headers=H)                            # S 5 (Senate, older) -> green
    client.post("/bill/119/S%201582/star", headers=H)
    client.post("/bill/119/S%201582/star", headers=H)                         # S 1582 (House, newer) -> red
    assert _order(client, "/votes?scope=all") == ["S-119-1-00001", "H-2025-200"]               # green before red
    assert _order(client, "/votes?scope=all&sort=star&dir=asc") == ["H-2025-200", "S-119-1-00001"]


def test_sort_by_matches_you(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    conn = db.connect()                                   # a 2nd passage vote Adams cast as Yea, on HR 5
    conn.execute("INSERT INTO votes (vote_id, chamber, congress, session, roll_num, vote_date, question, bill_id, "
                 "title, result, is_passage) VALUES ('H-2025-999','House',119,1,999,'2025-08-01','On Passage','HR 5',"
                 "'Test Act','Passed',1)")
    conn.execute("INSERT INTO member_votes (vote_id, member_id, position, position_raw, party) "
                 "VALUES ('H-2025-999','A000370','Yea','Yea','D')")
    conn.commit()
    client.post("/bill/119/S%201582/star", headers=H)     # green: Adams voted Nay on it  -> mismatch (-1)
    client.post("/bill/119/HR%205/star", headers=H)       # green: Adams voted Yea on it  -> match (+1)
    order = lambda d: _order(client, f"/member/A000370?scope=all&sort=match&dir={d}")
    assert order("asc") == ["H-2025-200", "H-2025-999"]   # mismatch first
    assert order("desc") == ["H-2025-999", "H-2025-200"]  # match first (also the first-click default)
    page = client.get("/member/A000370?scope=all").data.decode()
    assert 'class="match-yes" title="Voted the way you want on this bill"' in page
    assert 'class="match-no" title="Voted against what you want on this bill"' in page
    assert ">Matches you" in page


def test_default_sort_is_rated_then_newest(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    # House vote 2025-07-17 is newer than Senate vote 2025-01-09
    assert _order(client, "/votes?scope=all") == ["H-2025-200", "S-119-1-00001"]
    client.post("/bill/119/S%205/star", headers=H)                 # rate the Senate bill (older)
    assert _order(client, "/votes?scope=all") == ["S-119-1-00001", "H-2025-200"]       # rated first
    assert _order(client, "/votes?scope=all&sort=star&dir=asc") == ["H-2025-200", "S-119-1-00001"]
    assert _order(client, "/votes?scope=all&sort=date&dir=desc") == ["H-2025-200", "S-119-1-00001"]  # explicit sort ignores stars
    assert _order(client, "/votes?scope=all&sort=date&dir=asc") == ["S-119-1-00001", "H-2025-200"]


def test_column_sorts_and_header_links(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    assert _order(client, "/votes?scope=all&sort=vote&dir=asc") == ["H-2025-200", "S-119-1-00001"]   # House < Senate
    assert _order(client, "/votes?scope=all&sort=vote&dir=desc") == ["S-119-1-00001", "H-2025-200"]
    # bills sort by type then NUMBER: 'S 5' before 'S 1582' (plain text order would put 1582 first)
    assert _order(client, "/votes?scope=all&sort=bill&dir=asc") == ["S-119-1-00001", "H-2025-200"]
    assert _order(client, "/votes?scope=all&sort=bill&dir=desc") == ["H-2025-200", "S-119-1-00001"]
    page = client.get("/votes?scope=all&sort=date&dir=desc").data.decode()
    assert "sort=date&amp;dir=asc" in page or "sort=date&dir=asc" in page      # clicking the active header flips it
    assert "\\u2013" not in page and "\u25bc" in page                          # no literal escapes; arrow shown
    # junk / injection attempts fall back to the default instead of reaching SQL
    assert client.get("/votes?scope=all&sort=1;DROP TABLE votes&dir=up").status_code == 200
    assert client.get("/votes?scope=all&sort=position").status_code == 200   # member-only key on the votes page
    assert client.get("/votes?scope=all&sort=match").status_code == 200      # likewise


def test_member_page_sorting(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    for key in ("star", "date", "vote", "bill", "result", "position", "party", "match"):
        for d in ("asc", "desc"):
            r = client.get(f"/member/A000370?scope=all&sort={key}&dir={d}")
            assert r.status_code == 200, (key, d)
    assert b"Party majority" in client.get("/member/A000370?scope=all").data


def test_my_picks_page_and_direct_set(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    empty = client.get("/mine").data.decode()
    assert "haven't rated any bills" in empty and 'href="/mine"' in empty              # nav link on every page
    url = "/bill/119/S%201582/star"
    assert client.post(url + "?to=disagree", headers=H).get_json() == {"stance": "disagree"}   # set directly, no cycling
    assert client.post(url + "?to=disagree", headers=H).get_json() == {"stance": "disagree"}   # idempotent
    assert client.post(url + "?to=bogus", headers=H).status_code == 400
    assert client.post(url + "?to=agree", headers=H).get_json() == {"stance": "agree"}
    page = client.get("/mine").data.decode()
    assert "S 1582" in page and "Not enough data for a lean" in page and 'data-to="none"' in page
    assert 'class="agree on"' in page and 'class="disagree "' in page                  # current pick highlighted
    assert '<span class="pill">1</span>' in client.get("/votes").data.decode()           # nav badge counts picks
    assert client.post(url + "?to=none", headers=H).get_json() == {"stance": None}
    assert "haven't rated any bills" in client.get("/mine").data.decode()
    assert client.post(url + "?to=agree").status_code == 403                            # CSRF header still required


def test_my_picks_with_enough_partisan_bills(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    conn = db.connect()
    for n in (1, 2, 3):                                   # 3 passage votes: Democrats all Yea, Republicans all Nay
        conn.execute("INSERT INTO votes (vote_id, chamber, congress, session, roll_num, vote_date, question, bill_id, "
                     "title, result, is_passage) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                     (f"H-2025-90{n}", "House", 119, 1, 900 + n, f"2025-09-0{n}", "On Passage", f"HR {n}", f"Test Act {n}", "Passed"))
        for i, party in enumerate("DDDRRR"):
            mid = f"T{party}{n}{i}"
            conn.execute("INSERT OR IGNORE INTO members (member_id, full_name, party, state, chamber) VALUES (?,?,?,?,?)",
                         (mid, mid, party, "XX", "House"))
            conn.execute("INSERT INTO member_votes (vote_id, member_id, position, position_raw, party) VALUES (?,?,?,?,?)",
                         (f"H-2025-90{n}", mid, "Yea" if party == "D" else "Nay", "", party))
    conn.commit()
    for n in (1, 2, 3):
        client.post(f"/bill/119/HR%20{n}/star", headers=H)                    # agree with all three
    page = client.get("/mine").data.decode()
    assert "Strongly closer to Democrats" in page and "lean +100" in page
    assert "sided with Democrats on 3, with Republicans on 0" in page
    client.post("/bill/119/HR%201/star?to=disagree", headers=H)                # flip one: now 2 D-side, 1 R-side
    page = client.get("/mine").data.decode()
    assert "Closer to Democrats" in page and "Strongly" not in page and "lean +33" in page
    assert "sided with Democrats on 2, with Republicans on 1" in page
    assert "You generally favor" in page and "You generally oppose" not in page        # 2 agree vs 1 disagree, unclassified
    assert "(unclassified)" in page


def _add_bill(conn, bill_id="S 1582", brief="This bill sets rules for stablecoins.", source="crs"):
    conn.execute("INSERT OR REPLACE INTO bills (congress, bill_id, title, policy_area, brief, brief_source) "
                 "VALUES (119, ?, 'Official Title', 'Finance', ?, ?)", (bill_id, brief, source))
    conn.commit()


def test_summary_column_is_last_and_searchable(client, monkeypatch, tmp_path):
    import re
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    _add_bill(db.connect())
    for url in ("/votes?scope=all", "/member/A000370?scope=all"):
        page = client.get(url).data.decode()
        header = re.search(r"<tr>(<th class=\"starcol\".*?)</tr>", page, re.S).group(1)   # the vote-history table
        assert header.rstrip().endswith("<th>Summary</th>"), url                    # furthest right column
        assert "This bill sets rules for stablecoins." in page
    assert 'title="Lead of the Congress Research Service summary' in client.get("/votes?scope=all").data.decode()
    assert "H-2025-200" in client.get("/votes?scope=all&q=stablecoins").data.decode()   # brief text is searchable
    assert "H-2025-200" not in client.get("/votes?scope=all&q=nonexistentword").data.decode()
    # a bill with no brief shows a dash, a vote with no bill (nominations etc.) shows nothing
    conn = db.connect(); conn.execute("UPDATE bills SET brief = NULL, brief_source = NULL"); conn.commit()
    assert "&mdash;" in client.get("/votes?scope=all").data.decode()


def test_summary_column_on_my_picks(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    _add_bill(db.connect())
    client.post("/bill/119/S%201582/star", headers=H)
    page = client.get("/mine").data.decode()
    assert "<th>Summary</th></tr>" in page and "This bill sets rules for stablecoins." in page


def test_process_summary_replaces_crs_brief(client, monkeypatch):
    from politracker import summarize
    conn = db.connect(); _add_bill(conn, brief="CRS lead sentence.", source="crs")
    monkeypatch.setattr(summarize.congress, "get_key", lambda: "k")
    monkeypatch.setattr(summarize.congress, "fetch_source_text", lambda c, b, k: ("CRS summary (Test)", "text"))
    monkeypatch.setattr(summarize, "run_llm", lambda s, u: "WHAT IT DOES: Plain AI sentence.\n\nKEY PROVISIONS:\n- a")
    client.post("/bill/119/S%201582/summary", headers=H)
    row = db.connect().execute("SELECT brief, brief_source FROM bills WHERE bill_id = 'S 1582'").fetchone()
    assert tuple(row) == ("Plain AI sentence.", "ai")
    page = client.get("/votes?scope=all").data.decode()
    assert "Plain AI sentence." in page and "CRS lead sentence." not in page and "written by AI" in page


def test_run_briefs_syncs_ai_and_never_overwrites_it(client, monkeypatch):
    from politracker import congress
    conn = db.connect()
    _add_bill(conn, brief=None, source=None)
    conn.execute("INSERT INTO bill_summaries (congress, bill_id, summary, source, truncated, model, created_at) "
                 "VALUES (119, 'S 1582', 'WHAT IT DOES: From the AI.\n\nKEY PROVISIONS:\n- a', 'x', 0, 'm', '2026-01-01')")
    conn.commit()
    monkeypatch.setattr(congress, "get_key", lambda: None)                      # offline: only the AI sync can run
    congress.run_briefs()
    assert tuple(db.connect().execute("SELECT brief, brief_source FROM bills").fetchone()) == ("From the AI.", "ai")
    monkeypatch.setattr(congress, "get_key", lambda: "k")
    monkeypatch.setattr(congress, "_api_json", lambda path, key: {"summaries": [{"text": "<p>CRS says this.</p>", "updateDate": "2026"}]})
    congress.run_briefs()
    assert tuple(db.connect().execute("SELECT brief, brief_source FROM bills").fetchone()) == ("From the AI.", "ai")


def test_member_page_has_no_rated_bills_card_but_sorts_rated_first(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    conn = db.connect()                                   # give Adams a 2nd, newer passage vote on HR 5
    conn.execute("INSERT INTO votes (vote_id, chamber, congress, session, roll_num, vote_date, question, bill_id, "
                 "title, result, is_passage) VALUES ('H-2025-999','House',119,1,999,'2025-09-01','On Passage','HR 5',"
                 "'Test Act','Passed',1)")
    conn.execute("INSERT INTO member_votes (vote_id, member_id, position, position_raw, party) "
                 "VALUES ('H-2025-999','A000370','Yea','Yea','D')")
    conn.commit()
    client.post("/bill/119/S%201582/star", headers=H)                      # rate the OLDER bill
    page = client.get("/member/A000370?scope=all").data.decode()
    assert "Your rated bills" not in page and "With your picks" in page    # card gone, the percentage stays
    assert _order(client, "/member/A000370?scope=all") == ["H-2025-200", "H-2025-999"]              # rated bill first by default
    assert _order(client, "/member/A000370?scope=all&sort=date&dir=desc") == ["H-2025-999", "H-2025-200"]  # ...unless you sort otherwise
    assert "<h3>Your rated bills</h3>" in client.get("/mine").data.decode()       # the editable list lives on My picks


def test_api_members_typeahead(client):
    r = client.get("/api/members?q=ad")
    names = [m["name"] for m in r.get_json()]
    assert r.status_code == 200 and "Adams" in names and "Aderholt" in names        # House fixture members (last names only)
    assert set(r.get_json()[0]) == {"id", "name", "party", "state", "chamber"}
    assert client.get("/api/members?q=a").get_json() == []                             # needs 2+ characters
    assert client.get("/api/members").get_json() == []
    assert client.get("/api/members?q=zzzzqq").get_json() == []
    first = client.get("/api/members?q=der").get_json()                                # best match first (earliest in the name)
    assert first and first[0]["name"] == "Aderholt"
    assert len(client.get("/api/members?q=er").get_json()) <= 8


def test_home_page_quick_filters_and_recent_votes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    home = client.get("/").data.decode()
    assert "Latest final votes" in home and "/vote/H-2025-200" in home and "GENIUS" in home   # unfiltered: recent passage votes
    assert "thumbs up (approve)" in home                                                          # no picks yet -> prompt, not a lean
    assert 'data-suggest="suggest"' in home and "/api/members" not in home                     # JS wiring lives in app.js
    # quick filters narrow the member list; no recent-votes strip while browsing
    dems = client.get("/?chamber=House&party=D").data.decode()
    assert "/politician/A000370" in dems and "/politician/A000055" not in dems and "Latest final votes" not in dems
    reps = client.get("/?party=R").data.decode()
    assert "/politician/A000055" in reps and "/politician/A000370" not in reps
    assert "/politician/A000370" in client.get("/?state=nc").data.decode()                          # state is case-insensitive
    inds = client.get("/?party=I&chamber=Senate").data.decode()                                  # real independents are in the Senate fixture
    assert "<td>VT</td>" in inds and 'class="chip I"' in inds
    assert "No politicians match" in client.get("/?party=I&chamber=House").data.decode()        # ...but none in the House one
    assert 'href="/"' in dems and "Clear" in dems                                               # a way back
    assert "/politician/A000370" in client.get("/?member=Adams&party=D").data.decode()              # name + filters combine
    assert "/politician/A000370" not in client.get("/?member=Adams&party=R").data.decode()
    assert client.get("/?chamber=bogus&party=zz").status_code == 200                            # junk values are ignored


def test_home_lean_card_when_picks_exist(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")
    client.post("/bill/119/S%201582/star", headers=H)
    home = client.get("/").data.decode()
    assert "Your lean" in home and "Based on 1 rated bill." in home and "thumbs up (approve)" not in home


def test_static_assets_and_nav_highlight(client):
    css = client.get("/static/style.css")
    assert css.status_code == 200 and b"var(--accent)" in css.data
    assert client.get("/static/app.js").status_code == 200
    assert 'class="nav active" href="/votes"' in client.get("/votes").data.decode()
    assert 'class="nav active" href="/votes"' in client.get("/vote/H-2025-200").data.decode()   # vote pages sit under Votes
    assert 'class="nav active" href="/mine"' in client.get("/mine").data.decode()
    assert 'class="nav active" href="/"' in client.get("/").data.decode()
    assert 'class="nav active" href="/votes"' not in client.get("/").data.decode()


def test_thumbs_controls_render_both_buttons_with_state(client, monkeypatch, tmp_path):
    import re
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")

    def box(url, bill='S 1582'):
        html = client.get(url).data.decode()
        m = re.search(r'<span class="thumbs" data-congress="119" data-bill="' + bill + r'" data-stance="(\w*)">(.*?)</span>', html, re.S)
        return m.group(1), m.group(2)
    stance, inner = box("/vote/H-2025-200")
    assert stance == "" and inner.count("<button") == 2 and 'data-to="agree"' in inner and 'data-to="disagree"' in inner
    assert inner.count('aria-pressed="false"') == 2 and "<svg" in inner and " on" not in re.sub(r'class="thumb (up|down)"', "", inner)
    client.post("/bill/119/S%201582/star?to=agree", headers=H)
    stance, inner = box("/vote/H-2025-200")
    assert stance == "agree" and 'class="thumb up on"' in inner and 'class="thumb down"' in inner
    assert re.search(r'class="thumb up on"[^>]*aria-pressed="true"|aria-pressed="true"[^>]*>', inner) and "You approve of this bill" in inner
    client.post("/bill/119/S%201582/star?to=disagree", headers=H)
    stance, inner = box("/votes?scope=all")                                            # same control in the vote list rows
    assert stance == "disagree" and 'class="thumb down on"' in inner and "You deny this bill" in inner
    # no control where there is no bill (the Senate cloture row for S 5 has one; a nomination would not)
    assert "star agree" not in client.get("/votes?scope=all").data.decode()            # the old star markup is gone


def test_rating_column_header_and_wording(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")          # never read the user's real picks
    page = client.get("/votes?scope=all").data.decode()
    assert 'title="Sort by your rating (approved first)">Rating' in page and "\u2605" not in page and "\u2606" not in page
    assert "thumbs up (approve) or thumbs down (deny)" in page and "green (you agree)" not in page
    mine = client.get("/mine").data.decode()
    assert "thumbs up" in mine and "&#9734;" not in mine
