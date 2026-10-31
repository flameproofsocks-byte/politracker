from pathlib import Path

from politracker import parse

FIX = Path(__file__).parent / "fixtures"


def test_house_passage_vote():
    r = parse.parse_house_vote((FIX / "house_roll200.xml").read_text(), 2025)
    v = r["vote"]
    assert v["vote_id"] == "H-2025-200" and v["vote_date"] == "2025-07-17"
    assert v["bill_id"] == "S 1582" and v["title"] == "GENIUS Act" and v["is_passage"] == 1
    assert len(r["cast"]) == sum(1 for _ in r["members"])
    yeas = sum(c["position"] == "Yea" for c in r["cast"])
    assert yeas == v["yeas"] == 308


def test_senate_cloture_vote():
    r = parse.parse_senate_vote((FIX / "senate_vote1.xml").read_text())
    v = r["vote"]
    assert v["vote_id"] == "S-119-1-00001" and v["vote_date"] == "2025-01-09"
    assert v["bill_id"] == "S 5" and v["is_passage"] == 0   # cloture is procedural
    assert sum(c["position"] == "Yea" for c in r["cast"]) == v["yeas"] == 84
    assert r["cast"][0]["member_key"][0] == "lis"


def test_invalid_input_returns_none():
    assert parse.parse_house_vote('<xml>Error sanitizing file "roll999.xml".</xml>', 2025) is None
    assert parse.parse_senate_vote("<html>nope</html>") is None
    assert parse.parse_house_vote("not xml at all", 2025) is None


def test_norm_bill_id():
    assert parse.norm_bill_id("H R 1") == "HR 1"
    assert parse.norm_bill_id("S. 5") == "S 5"
    assert parse.norm_bill_id("H.R. 7147") == "HR 7147"
    assert parse.norm_bill_id("S.J.Res. 55") == "SJRES 55"
    assert parse.norm_bill_id("QUORUM") is None
    assert parse.norm_bill_id("PN373") is None


def test_norm_position():
    assert [parse.norm_position(x) for x in ("Aye", "No", "Yea", "Not Voting", "Johnson (LA)")] == \
        ["Yea", "Nay", "Yea", "Not Voting", "Other"]


def test_congress_bill_parse_and_url():
    import json
    from politracker import congress
    payload = json.loads((FIX / "congress_bill_hr1.json").read_text())
    b = congress.parse_bill(payload, 119, "HR 1")
    assert b["policy_area"] == "Economics and Public Finance"
    assert b["sponsor_id"] == "A000375" and b["sponsor_party"] == "R"
    assert b["became_law"] == 1 and b["law_number"] == "119-21"
    assert b["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/1"
    assert congress.bill_url(118, "SJRES 55").endswith("118th-congress/senate-joint-resolution/55")


def test_html_to_text():
    from politracker import congress
    t = congress.html_to_text("<p><strong>Stop Insider Trading Act</strong></p><p>This bill bars &amp; limits trades.<br/>Second.</p>")
    assert t == "Stop Insider Trading Act\n\nThis bill bars & limits trades.\nSecond."
    assert "&lt;DOC&gt;" not in congress.html_to_text("<pre>&lt;DOC&gt; text</pre>") and "<DOC> text" in congress.html_to_text("<pre>&lt;DOC&gt; text</pre>")


def test_brief_from_crs_skips_title_and_keeps_whole_sentences():
    from politracker import congress
    html = ("<p><strong>Community Bank Deposit Access Act of 2025</strong></p><p>This bill changes the treatment of "
            "certain deposits so they are no longer classified as brokered deposits. Brokered deposits are funds "
            "placed by a broker.</p><p>Second paragraph.</p>")
    assert congress.brief_from_crs(html) == ("This bill changes the treatment of certain deposits so they are no longer "
                                             "classified as brokered deposits. Brokered deposits are funds placed by a broker.")
    # no title line (joint resolutions often start straight in), nbsp normalised
    assert congress.brief_from_crs("<p>This joint resolution eliminates new,\xa0stricter standards.</p>") == \
        "This joint resolution eliminates new, stricter standards."
    assert congress.brief_from_crs("") is None


def test_brief_length_cap_respects_abbreviations():
    from politracker import congress
    sentence = "This bill directs the U.S. Department of the Interior to expand production on federal land (i.e., public lands). "
    out = congress.brief_from_crs("<p>X Act</p><p>" + sentence * 4 + "</p>")
    assert len(out) <= congress.BRIEF_MAX and out.endswith("lands).")
    assert out.count("This bill") == 1 or out.count("This bill") == 2          # whole sentences only, U.S./i.e. didn't split
    runon = "<p>" + "word " * 200 + "</p>"                                      # no sentence boundary at all
    cut = congress.brief_from_crs(runon)
    assert len(cut) <= congress.BRIEF_MAX + 1 and cut.endswith("…")


def test_brief_from_ai_summary():
    from politracker import congress
    text = "WHAT IT DOES: This bill bars Members of Congress from buying stocks.\n\nKEY PROVISIONS:\n- x"
    assert congress.brief_from_ai(text) == "This bill bars Members of Congress from buying stocks."
    assert congress.brief_from_ai("garbage") is None and congress.brief_from_ai(None) is None


def test_db_migration_adds_brief_columns_to_an_old_database(tmp_path):
    import sqlite3
    from politracker import db
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE bills (congress INTEGER NOT NULL, bill_id TEXT NOT NULL, title TEXT, policy_area TEXT, "
                "sponsor_id TEXT, sponsor_name TEXT, sponsor_party TEXT, introduced_date TEXT, latest_action_date TEXT, "
                "latest_action_text TEXT, became_law INTEGER NOT NULL DEFAULT 0, law_number TEXT, "
                "status TEXT NOT NULL DEFAULT 'ok', url TEXT, PRIMARY KEY (congress, bill_id))")
    old.execute("INSERT INTO bills (congress, bill_id, title) VALUES (119, 'HR 1', 'Keep me')")
    old.commit(); old.close()
    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bills)")}
    assert {"brief", "brief_source", "brief_checked"} <= cols
    assert conn.execute("SELECT title, brief FROM bills").fetchone()[:] == ("Keep me", None)   # data preserved
    db.connect(path)                                                                              # idempotent
