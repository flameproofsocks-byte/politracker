"""Videos tab: blurb/outline/slide logic (pure), the editor + save/draft endpoints, the presenter, and slide-image export."""
import io
import json
import re
import zipfile

import pytest

from politracker import config, db, frames, video

H = {"X-Politracker": "1"}
PRONOUN = re.compile(r"\b(he|she|him|her|hers|his|himself|herself)\b", re.I)


# ---------------------------------------------------------------- pure helpers

def test_short_title_prefers_the_shortest_and_trims_cleanly():
    assert video.short_title("A bill to protect the name, image, and likeness rights of student athletes and for other purposes.",
                             "Protect College Sports Act of 2026") == "Protect College Sports Act of 2026"
    assert video.short_title("Water Resources Development Act.", None, "") == "Water Resources Development Act"   # trailing period dropped
    long = "word " * 40
    out = video.short_title(long, limit=30)
    assert len(out) <= 31 and out.endswith("…") and not out[:-1].endswith(" ") and out.count("wor") >= 1
    assert video.short_title(None, "", "   ") == ""


def test_length_estimates_and_status_boundaries():
    assert video.word_count("It's a three-word test, isn’t it?") == 6 and video.word_count("") == 0 and video.word_count(None) == 0
    assert video.estimate_seconds("word " * 39) == 15 and video.estimate_seconds("word " * 78) == 30
    lo, hi = video.TARGET_SECONDS
    assert [video.length_status(s) for s in (0, lo - 1, lo, hi, hi + 1)] == ["empty", "short", "good", "good", "long"]


def test_names_and_list_joining():
    assert video.first_last_name("Maria Cantwell") == ("Maria", "Cantwell")
    assert video.first_last_name("Gerald E. Connolly Jr.") == ("Gerald", "Connolly") and video.first_last_name("") == ("", "")
    assert video.first_last_name("Madonna") == ("Madonna", "Madonna")
    assert [video.a_an(w) for w in ("Democratic", "Independent", "Republican")] == ["a", "an", "a"]
    assert video.join_and(["a"]) == "a" and video.join_and(["a", "b"]) == "a and b" and video.join_and(["a", "b", "c"]) == "a, b, and c"
    assert video.join_and([]) == "" and video.join_and(["a", "", None]) == "a"


def vote(bill, position="Yea", date="2026-01-01", topic=None, congress=119, **kw):
    return {"congress": congress, "bill_id": bill, "position": position, "vote_date": date, "topic": topic, "title": bill + " Act",
            "tally": "50–49", "key": f"{congress}|{bill}", **kw}


def test_dedupe_candidates_and_selection():
    rows = [vote("HR 1", "Nay", "2026-02-01"), vote("HR 1", "Yea", "2026-01-01"), vote("HR 2"), vote("HR 3", "Nay")]   # newest first
    deduped = video.dedupe_latest(rows)
    assert [(v["bill_id"], v["position"]) for v in deduped] == [("HR 1", "Nay"), ("HR 2", "Yea"), ("HR 3", "Nay")]    # the latest vote on HR 1 wins
    scores = {(119, "HR 1"): 50, (119, "HR 2"): 80, (119, "HR 4"): 99}
    yeas = [vote("HR 2"), vote("HR 4", date="2026-03-01"), vote("HR 5", date="2026-04-01"), vote("HR 6", date="2026-05-01")]
    cand = video.pick_candidates(yeas, "Yea", scores, limit=3)
    assert [v["bill_id"] for v in cand] == ["HR 4", "HR 2", "HR 6"]                     # by score, then newest first among unscored
    assert video.pick_candidates(yeas, "Nay", scores) == []
    # selection: no saved picks -> top defaults; saved picks keep their order and drop anything no longer valid
    assert [v["bill_id"] for v in video.resolve_selection(None, yeas, "Yea", cand)] == ["HR 4", "HR 2", "HR 6"]
    assert [v["bill_id"] for v in video.resolve_selection([[119, "HR 6"], [119, "HR 4"]], yeas, "Yea", cand)] == ["HR 6", "HR 4"]
    assert [v["bill_id"] for v in video.resolve_selection([[119, "HR 6"], [119, "NOPE 1"]], yeas, "Yea", cand)] == ["HR 6"]
    assert video.resolve_selection([[119, "HR 6"]], yeas, "Nay", []) == []              # right bill, wrong position
    assert video.resolve_selection([], yeas, "Yea", cand) == []                         # the user explicitly picked none
    many = [vote(f"HR {i}") for i in range(10)]
    assert len(video.resolve_selection([[119, f"HR {i}"] for i in range(10)], many, "Yea", many)) == video.MAX_PICKS


def test_topic_leanings_need_a_consistent_direction():
    votes = ([vote(f"A{i}", "Yea", topic="Energy") for i in range(6)] + [vote(f"B{i}", "Nay", topic="Energy") for i in range(7)]   # mixed: neither
             + [vote(f"C{i}", "Yea", topic="Health") for i in range(4)] + [vote("C9", "Nay", topic="Health")]                      # 80% Yea
             + [vote(f"D{i}", "Nay", topic="Taxation") for i in range(3)]
             + [vote(f"E{i}", "Yea", topic="Congress") for i in range(9)]                                                          # generic: ignored
             + [vote("F1", "Yea", topic="Arts"), vote("F2", "Yea"), vote("F3", "Present", topic="Arts")])                          # below min_votes / no topic
    out = video.topic_leanings(votes)
    assert out["supports"] == [("Health", 4)] and out["opposes"] == [("Taxation", 3)]
    assert not ({t for t, _ in out["supports"]} & {t for t, _ in out["opposes"]})        # a topic can't be on both sides
    wide = video.topic_leanings(votes, min_share=0.5)
    assert ("Energy", 7) in wide["opposes"] and ("Energy", 6) not in wide["supports"]


def facts(**kw):
    base = {"name": "Maria Cantwell", "first": "Maria", "last": "Cantwell", "party": "D", "party_adj": "Democratic",
            "office": "U.S. Senator from Washington", "current": True, "since_year": 1993, "left_year": None,
            "party_pct": 97.5, "party_n": 997, "party_cmp": "Democrats", "participation": 99.8,
            "for_bills": [{"title": "GENIUS Act", "tally": "68–30"}, {"title": "Farm Bill", "tally": "70–25"}],
            "against_bills": [{"title": "Rescissions Act of 2025", "tally": "51–48"}],
            "supports": [("Health", 4), ("Energy", 3)], "opposes": [("Taxation", 3)]}
    return {**base, **kw}


def test_blurb_is_factual_complete_and_pronoun_free():
    b = video.build_blurb(facts())
    assert b.startswith("Maria Cantwell is a Democratic U.S. Senator from Washington, in Congress since 1993.")
    assert "Voted for GENIUS Act (68–30) and Farm Bill (70–25)." in b and "Voted against Rescissions Act of 2025 (51–48)." in b
    assert "Sided with the Democrats on 97.5% of 997 Yea/Nay votes." in b
    assert "Mostly votes Yea on Health and Energy." in b and "Mostly votes Nay on Taxation." in b
    assert not PRONOUN.search(b)
    sparse = video.build_blurb(facts(for_bills=[], against_bills=[], supports=[], opposes=[], party_pct=None))
    assert sparse == "Maria Cantwell is a Democratic U.S. Senator from Washington, in Congress since 1993."          # empty parts are skipped, not blank
    former = video.build_blurb(facts(office=None, current=False, left_year=2025, since_year=2009))
    assert former.startswith("Maria Cantwell was a Democratic member of Congress from 2009 to 2025.")
    assert video.build_blurb(facts(party_adj="Independent")).startswith("Maria Cantwell is an Independent")
    assert video.build_blurb(facts(office=None, since_year=None)).startswith("Maria Cantwell is a Democratic member of Congress.")


def test_outline_follows_the_script_beats_in_the_right_tense():
    lines = video.build_outline(facts()).split("\n")
    assert lines[0] == "You've probably seen ads about Maria Cantwell." and lines[1] == "And they all seem to tell a different story."
    assert lines[2] == "So let's see how Maria actually votes." and lines[3] == "Maria has been in Congress since 1993."
    assert lines[4] == "Maria voted for GENIUS Act and Farm Bill." and lines[5] == "But voted against Rescissions Act of 2025."
    assert lines[6] == "Maria sides with the Democrats 97.5% of the time."
    assert lines[7] == "We can't know Maria's future votes, but the record says Maria generally stands for health and energy."
    assert not PRONOUN.search("\n".join(lines))
    past = video.build_outline(facts(office=None, current=False, since_year=2009, left_year=2025)).split("\n")
    assert past[3] == "Maria served in Congress from 2009 to 2025." and past[-1].startswith("The record shows Maria generally stood for")
    only_against = video.build_outline(facts(for_bills=[])).split("\n")
    assert "Maria voted against Rescissions Act of 2025." in only_against and not any(l.startswith("But") for l in only_against)


def test_outline_fits_the_target_length_and_reads_naturally():
    long_titles = [{"title": "Military Construction and Veterans Affairs, Agriculture, and Legislative Branch Appropriations", "tally": "87\u20139"},
                   {"title": "Consolidated Appropriations Act, 2026", "tally": "71\u201329"},
                   {"title": "Lindsey O. Graham Sanctioning Russia and Iran Act of 2026", "tally": "86\u201311"},
                   {"title": "Farm Bill", "tally": "70\u201325"}]
    f = facts(for_bills=long_titles, against_bills=long_titles[:3], supports=[("Foreign Trade and International Finance", 5), ("International Affairs", 3)])
    text = video.build_outline(f)
    lines = text.split("\n")
    assert video.estimate_seconds(text) <= video.TARGET_SECONDS[1] and video.word_count(text) <= 78
    for_line = next(l for l in lines if " voted for " in l)
    assert "Consolidated Appropriations Act, 2026" in for_line and "Military Construction" not in for_line     # a short, speakable title beats the 90-character one
    assert "\u2026" not in text and not PRONOUN.search(text)                                                  # no mid-word cut-offs in narration
    assert lines[-1].endswith("generally stands for foreign trade and international finance, plus international affairs.")
    # a too-long fullest version is stepped down (fewer bills / topics) rather than left over length
    wordy = [{"title": "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta", "tally": "1\u20131"}] * 3
    g = facts(for_bills=wordy, against_bills=wordy)
    fullest = "\n".join(video._outline_lines(g, 2, 2))
    assert video.estimate_seconds(fullest) > video.TARGET_SECONDS[1]
    chosen = video.build_outline(g)
    assert video.word_count(chosen) < video.word_count(fullest) and chosen in {"\n".join(video._outline_lines(g, b, t)) for b, t in video.OUTLINE_TIERS}
    assert video.join_topics(["health", "energy"]) == "health and energy"
    assert video.join_topics(["Trade and Finance", "Affairs"]) == "Trade and Finance, plus Affairs"
    assert video.join_topics(["Trade and Finance", "Health", "Energy"]) == "Trade and Finance, Health, plus Energy"
    assert video.join_topics(["Health"]) == "Health" and video.join_topics([]) == ""


def test_scenes_skip_empty_sections_and_fit_the_target_length():
    full = video.scenes(facts())
    assert [s["kind"] for s in full] == ["hook", "who", "stats", "for", "against", "stands", "outro"]
    lo, hi = video.TARGET_SECONDS
    assert lo <= video.total_seconds(full) <= hi and all(s["seconds"] > 0 for s in full)
    bare = video.scenes(facts(for_bills=[], against_bills=[], supports=[], opposes=[]))
    assert [s["kind"] for s in bare] == ["hook", "who", "stats", "outro"]
    assert full[0]["text"] == "How does Maria Cantwell actually vote?" and full[-1]["text"] == "Check the record before you decide."
    custom = video.scenes(facts(), hook="  You won't believe this  ", outro="Follow for more")
    assert custom[0]["text"] == "You won't believe this" and custom[-1]["text"] == "Follow for more"
    assert video.scenes(facts(), hook="   ", outro="")[0]["text"].startswith("How does")      # blank = back to default


# ---------------------------------------------------------------- the pages

def add_vote(conn, vid, bill, date, yeas, nays, position, title=None, member="A000370", party="D"):
    conn.execute("INSERT INTO votes (vote_id, chamber, congress, session, roll_num, vote_date, question, bill_id, title, result, is_passage, yeas, nays) "
                 "VALUES (?,?,119,1,?,?,'On Passage',?,?,'Passed',1,?,?)", (vid, "House", int(vid[-3:]), date, bill, title or f"{bill} Act", yeas, nays))
    conn.execute("INSERT OR REPLACE INTO member_votes (vote_id, member_id, position, position_raw, party) VALUES (?,?,?,?,?)",
                 (vid, member, position, position, party))


@pytest.fixture()
def vc(client):
    """client + Adams (A000370, House D) with three extra bill votes: Yea HR 5 (razor-thin), Yea HR 6, Nay HR 7. His real fixture vote is Nay on S 1582 (GENIUS Act)."""
    conn = db.connect()
    add_vote(conn, "H-2026-901", "HR 5", "2026-03-10", 200, 199, "Yea")
    add_vote(conn, "H-2026-902", "HR 6", "2026-03-20", 400, 20, "Yea")
    add_vote(conn, "H-2026-903", "HR 7", "2026-04-01", 210, 215, "Nay")
    conn.execute("INSERT INTO bills (congress, bill_id, title, policy_area, amendment_count) VALUES (119, 'HR 5', 'Five Act', 'Economics', 300)")
    conn.execute("INSERT INTO member_bios (member_id, birthday, terms_json, social_json, ids_json) VALUES ('A000370', '1963-10-05', ?, '{}', '{}')",
                 (json.dumps([{"type": "rep", "start": "2015-01-03", "end": "2027-01-03", "state": "NC", "district": 12, "party": "Democrat"}]),))
    conn.commit()
    return client


def save(client, payload=None, member="A000370"):
    body = {"blurb": "", "script": "", "hook": "", "outro": "", "for": [], "against": [], **(payload or {})}
    return client.post(f"/videos/{member}/save", json=body, headers=H)


def test_videos_list_and_navigation(vc):
    page = vc.get("/videos").data.decode()
    assert 'class="nav active" href="/videos"' in page and "No briefs yet" in page
    assert 'data-suggest="video-suggest"' in page and 'data-link="/videos/"' in page          # the search box links to the editor, not the profile
    assert "/videos" in vc.get("/").data.decode() and "Make a short video" in vc.get("/politician/A000370").data.decode()
    assert 'class="nav active" href="/videos"' in vc.get("/videos/A000370").data.decode()


def test_editor_shows_facts_candidates_defaults_and_a_blurb(vc):
    r = vc.get("/videos/A000370")
    page = r.data.decode()
    assert r.status_code == 200 and "Video brief: Alma" not in page and "Video brief:" in page
    blurb = re.search(r'<textarea id="blurb"[^>]*>(.*?)</textarea>', page, re.S).group(1)
    assert "Voted for Five Act (200–199)" in blurb and "Voted against" in blurb and "GENIUS Act" in blurb and "HR 7 Act" in blurb
    assert "in Congress since 2015" in blurb and not PRONOUN.search(blurb)
    ticked = re.findall(r'<input type="checkbox" data-side="(for|against)" value="([^"]+)" checked>', page)
    assert ("for", "119|HR 5") in ticked and ("for", "119|HR 6") in ticked and ("against", "119|HR 7") in ticked
    assert ("against", "119|S 1582") in ticked                                            # Adams's Nay on the GENIUS Act passage vote
    assert "/videos/A000370/present" in page and "/videos/A000370/frames.zip" in page and 'id="meter-text"' in page
    assert vc.get("/videos/NOPE").status_code == 404 and vc.get("/videos/NOPE/present").status_code == 404


def test_save_validates_and_round_trips(vc):
    assert vc.post("/videos/A000370/save", json={}).status_code == 403                      # no CSRF header
    assert vc.post("/videos/NOPE/save", json={}, headers=H).status_code == 404
    bad = vc.post("/videos/A000370/save", data="nope", headers=H)
    assert bad.status_code == 400 and "error" in bad.get_json()                              # JSON error, not an HTML page
    assert save(vc, {"script": "x" * 6001}).status_code == 400                                # over the 6000-character limit
    assert save(vc, {"hook": 5}).status_code == 400
    r = save(vc, {"for": [[119, "HR 7"]]})                                                   # HR 7 was a Nay: not valid under "for"
    assert r.status_code == 400 and "HR 7" in r.get_json()["error"]
    assert save(vc, {"for": [[119, "NOPE 1"]]}).status_code == 400 and save(vc, {"for": ["HR 5"]}).status_code == 400
    assert save(vc, {"for": [[119, "HR 5"]] * 6}).status_code == 400                         # too many
    script = "Word " * 52
    ok = save(vc, {"script": script, "blurb": "My own blurb.", "hook": "Wait for it", "outro": "Follow", "for": [[119, "HR 6"]], "against": []})
    assert ok.status_code == 200 and ok.get_json()["saved"] is True and ok.get_json()["words"] == 52 and ok.get_json()["seconds"] == 20
    page = vc.get("/videos/A000370").data.decode()
    assert "My own blurb." in page and script.strip() in page and 'value="Wait for it"' in page and 'value="Follow"' in page
    assert re.findall(r'data-side="for" value="([^"]+)" checked', page) == ["119|HR 6"]       # only the saved pick is ticked
    assert re.findall(r'data-side="against" value="([^"]+)" checked', page) == []             # explicitly none
    listing = vc.get("/videos").data.decode()
    assert "52 words" in listing and "about 20s" in listing and "on target" in listing and "/videos/A000370/present" in listing
    save(vc, {"blurb": "   ", "script": "", "hook": "", "outro": ""})                          # blank resets to the defaults
    page = vc.get("/videos/A000370").data.decode()
    assert "My own blurb." not in page and "Voted for" in page and 'value="Wait for it"' not in page


def test_draft_rebuilds_blurb_and_outline_for_the_ticked_bills(vc):
    assert vc.post("/videos/A000370/draft", json={}).status_code == 403
    r = vc.post("/videos/A000370/draft", json={"for": [[119, "HR 6"]], "against": []}, headers=H)
    d = r.get_json()
    assert r.status_code == 200 and "HR 6 Act (400–20)" in d["blurb"] and "Five Act" not in d["blurb"] and "Voted against" not in d["blurb"]
    assert "Adams voted for HR 6 Act." in d["outline"] and "So let's see how Adams actually votes." in d["outline"]
    assert vc.post("/videos/A000370/draft", json={"for": [[119, "HR 7"]]}, headers=H).status_code == 400
    assert vc.get("/videos/A000370").data.decode().count("HR 6 Act") >= 1                    # drafting saved nothing
    assert db.connect().execute("SELECT COUNT(*) FROM video_briefs").fetchone()[0] == 0


def test_presenter_builds_the_vertical_slides_from_the_saved_picks(vc):
    save(vc, {"hook": "Seen the ads?", "outro": "Check the record", "for": [[119, "HR 6"]], "against": [[119, "HR 7"]]})
    r = vc.get("/videos/A000370/present")
    page = r.data.decode()
    assert r.status_code == 200 and 'id="stage"' in page and "present.js" in page and "present.css" in page
    kinds = re.findall(r'<section class="scene (\w+)" data-seconds="(\d+)"', page)
    assert [k for k, _ in kinds] == ["hook", "who", "stats", "for", "against", "outro"]        # no 'stands': Adams has too few topic votes
    assert sum(int(s) for _, s in kinds) == 3 + 3 + 3 + 5 + 5 + 3 and "Seen the ads?" in page and "Check the record" in page
    assert "HR 6" in page and "400–20" in page and "HR 7" in page and "210–215" in page and "Five Act" not in page      # only the picked bills
    assert "Voted for" in page and "Voted against" in page and "Source: House &amp; Senate roll-call votes" in page
    assert "bioguide" not in page and "A000370.jpg" in page                                    # official portrait by bioguide id
    assert not PRONOUN.search(re.sub(r"<[^>]+>", " ", page))
    assert 'class="clean freeze"' in vc.get("/videos/A000370/present?clean=1&freeze=1&scene=3").data.decode()
    assert 'data-start="3"' in vc.get("/videos/A000370/present?scene=3").data.decode() and 'data-start="1"' in vc.get("/videos/A000370/present?scene=zzz").data.decode()
    assert 'data-auto="0"' in vc.get("/videos/A000370/present?auto=0").data.decode() and 'data-auto="0"' in vc.get("/videos/A000370/present?freeze=1").data.decode()
    assert 'data-auto="1"' in page and 'data-loop="1"' in vc.get("/videos/A000370/present?loop=1").data.decode()


# ---------------------------------------------------------------- slide image export

def test_frames_find_browser_and_render(tmp_path):
    assert frames.find_browser(which=lambda n: None) is None
    assert frames.find_browser(which=lambda n: "/usr/bin/chrome" if n == "google-chrome" else None) == "/usr/bin/chrome"
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        shot = next(a for a in cmd if a.startswith("--screenshot=")).split("=", 1)[1]
        open(shot, "wb").write(b"\x89PNG fake")
    out = frames.render_frames("/b", "http://127.0.0.1:5057/videos/X/present", 2, tmp_path, run=fake_run)
    assert [p.name for p in out] == ["scene-01.png", "scene-02.png"]
    assert "--window-size=1080,1920" in seen[0] and seen[1][-1].endswith("present?clean=1&freeze=1&scene=2") and "--headless" in seen[0]
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="slide 1.*no image"):                          # the browser ran but wrote no file
        frames.render_frames("/b", "http://x/p", 1, tmp_path / "empty", run=lambda cmd, **kw: None)

    def boom(cmd, **kw):
        raise OSError("cannot launch")
    with pytest.raises(RuntimeError, match="slide 1.*cannot launch"):
        frames.render_frames("/b", "http://x/p", 1, tmp_path, run=boom)


def test_frames_zip_route(vc, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)                                       # keep temp files out of the real data dir
    monkeypatch.setattr(frames, "find_browser", lambda: None)
    r = vc.get("/videos/A000370/frames.zip")
    assert r.status_code == 501 and "screen-record" in r.get_json()["error"]
    monkeypatch.setattr(frames, "find_browser", lambda: "/fake/chromium")
    urls = []

    def fake_run(cmd, **kw):
        urls.append(cmd[-1])
        open(next(a for a in cmd if a.startswith("--screenshot=")).split("=", 1)[1], "wb").write(b"PNGDATA")
    monkeypatch.setattr(frames.subprocess, "run", fake_run)
    r = vc.get("/videos/A000370/frames.zip")
    assert r.status_code == 200 and r.mimetype == "application/zip" and "A000370-slides.zip" in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    n = len(re.findall(r'class="scene ', vc.get("/videos/A000370/present").data.decode()))
    assert z.namelist() == [f"A000370-scene-{i:02d}.png" for i in range(1, n + 1)] and z.read(z.namelist()[0]) == b"PNGDATA"
    assert all(u.startswith("http://127.0.0.1:") and "/videos/A000370/present?clean=1&freeze=1&scene=" in u for u in urls)   # never built from the Host header
    assert list((tmp_path / "tmp").iterdir()) == []                                          # temp frames cleaned up
    monkeypatch.setattr(frames.subprocess, "run", lambda cmd, **kw: None)                   # browser runs but produces nothing
    bad = vc.get("/videos/A000370/frames.zip")
    assert bad.status_code == 502 and "Could not render" in bad.get_json()["error"] and list((tmp_path / "tmp").iterdir()) == []
