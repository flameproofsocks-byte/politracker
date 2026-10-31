"""The Top bills interest score: pure maths + the /top page + the count columns it relies on."""
import json

import pytest

from politracker import config, congress, db, interest


# ---------------------------------------------------------------- pure functions

def test_closeness_and_partisanship():
    assert interest.closeness(50, 50) == 1.0 and interest.closeness(100, 0) == 0.0 and interest.closeness(0, 0) == 0.0
    assert round(interest.closeness(216, 212), 3) == 0.991 and interest.closeness(60, 40) == pytest.approx(0.8)
    assert interest.partisanship(100, 0, 0, 100) == 1.0 and interest.partisanship(50, 50, 50, 50) == 0.0
    assert interest.partisanship(90, 10, 40, 60) == pytest.approx(0.5)
    assert interest.partisanship(0, 0, 10, 10) == 0.0 and interest.partisanship(10, 10, 0, 0) == 0.0     # a party with no Yea/Nay votes


def test_scaled_is_log_capped_and_monotonic():
    assert interest.scaled(0, 100) == 0.0 and interest.scaled(None, 100) == 0.0 and interest.scaled(-5, 100) == 0.0
    assert interest.scaled(100, 100) == 1.0 and interest.scaled(5000, 100) == 1.0
    vals = [interest.scaled(n, 100) for n in (1, 2, 5, 10, 50, 100)]
    assert vals == sorted(vals) and interest.scaled(2, 100) < 0.3 < interest.scaled(10, 100)              # first few matter most


def test_score_weights_sum_to_100_and_clamp():
    assert sum(interest.WEIGHTS.values()) == 100
    every = {k: 1.0 for k in interest.WEIGHTS}
    assert interest.score(every) == 100.0 and interest.score({}) == 0.0
    assert interest.score({**every, "close": 5.0, "law": -3.0}) == 100.0 - interest.WEIGHTS["law"]        # out-of-range inputs are clamped
    assert interest.score({"close": 1.0}) == interest.WEIGHTS["close"]


def test_reasons_and_period_filter():
    base = {"close": 0.99, "tally": "216–212", "partisan": 0.9, "dem_yea_pct": 1, "rep_yea_pct": 99, "amendments": 493,
            "cosponsors": 3, "n_votes": 24, "became_law": True}
    r = interest.reasons(base)
    assert r[0] == "Razor-thin 216–212" and "Party-line (D 1% vs R 99% Yea)" in r and len(r) == 4         # capped at 4, strongest first
    assert interest.reasons({**base, "close": 0.8})[0] == "Close vote 216–212"
    quiet = {"close": 0.1, "tally": "97–3", "partisan": 0.05, "dem_yea_pct": 96, "rep_yea_pct": 98, "amendments": 0,
             "cosponsors": 0, "n_votes": 1, "became_law": False}
    assert interest.reasons(quiet) == ["Broad bipartisan support"]
    assert interest.reasons({**quiet, "dem_yea_pct": None, "rep_yea_pct": None}) == []                     # no party data -> no claim
    assert interest.reasons({**quiet, "dem_yea_pct": 90, "rep_yea_pct": None}) == []                        # only one party voted: no crash, no claim
    assert interest.reasons({**quiet, "dem_yea_pct": None, "rep_yea_pct": 90}) == []
    pf = interest.period_filter
    assert pf("2025-07-17") and pf("2025-07-17", 2025) and pf("2025-07-17", 2025, 7) and pf("2025-07-17", None, 7)
    assert not pf("2025-07-17", 2024) and not pf("2025-07-17", 2025, 8) and not pf("2025-07-17", None, 8)
    assert pf(None) and not pf(None, 2025)


def bill(b, **kw):
    return {"congress": 119, "bill_id": b, "title": f"{b} Act", "topic": None, "brief": None, "became_law": 0, "cosponsor_count": 0,
            "amendment_count": 0, "n_votes": 1, "last_vote": "2025-07-01", "last_vote_id": f"V-{b}", "last_passage": "2025-07-01", **kw}


def pv(b, yeas, nays, vid=None, chamber="House"):
    return {"congress": 119, "bill_id": b, "vote_id": vid or f"P-{b}", "chamber": chamber, "vote_date": "2025-07-01",
            "yeas": yeas, "nays": nays, "result": "Passed"}


def pr(b, party, yea, nay):
    return {"congress": 119, "bill_id": b, "party": party, "yea": yea, "nay": nay}


def test_build_leaderboard_scores_pools_chambers_and_handles_gaps():
    bills = [bill("HR 1", amendment_count=400, n_votes=20, became_law=1), bill("HR 2", cosponsor_count=200),
             bill("HR 3", last_passage=None, last_vote="2025-06-01", amendment_count=None, cosponsor_count=None)]
    votes = [pv("HR 1", 216, 212, "H-1"), pv("HR 1", 50, 50, "S-1", "Senate"), pv("HR 2", 400, 20)]       # HR 3 has no passage vote
    party = [pr("HR 1", "D", 0, 210), pr("HR 1", "R", 215, 0), pr("HR 1", "D", 1, 47), pr("HR 1", "R", 50, 1),
             pr("HR 2", "D", 200, 5), pr("HR 2", "R", 195, 10)]
    rows = {r["bill_id"]: r for r in interest.build_leaderboard(bills, votes, party)}
    one, two, three = rows["HR 1"], rows["HR 2"], rows["HR 3"]
    assert one["close"] == 1.0 and one["tally"] == "50–50" and one["link_vote_id"] == "S-1"             # the closest of its passage votes wins
    assert one["dem_yea_pct"] == 0 and one["rep_yea_pct"] == 100                                           # pooled over both chambers
    assert one["score"] > 70 > two["score"] > three["score"]
    assert two["partisan"] < 0.1 and "Broad bipartisan support" in two["reasons"]
    assert three["close"] == 0 and three["tally"] is None and three["link_vote_id"] == "V-HR 3" and three["bill_date"] == "2025-06-01"
    assert three["amendments"] == 0 and three["cosponsors"] == 0 and three["score"] == pytest.approx(
        interest.WEIGHTS["votes"] * interest.scaled(1, interest.CAPS["votes"]), abs=0.05)                 # None counts mean 0, not an error
    assert interest.build_leaderboard([], [], []) == []


def test_rank_by_keys_ties_and_unknown_key():
    rows = interest.build_leaderboard(
        [bill("A", amendment_count=5, last_passage="2025-01-01"), bill("B", amendment_count=900, last_passage="2025-02-01"),
         bill("C", amendment_count=900, last_passage="2025-03-01")],
        [pv("A", 100, 99), pv("B", 100, 1), pv("C", 100, 1)], [])
    assert [r["bill_id"] for r in interest.rank(rows, "close")][0] == "A"
    assert [r["bill_id"] for r in interest.rank(rows, "amended")] == ["C", "B", "A"]                        # tie on 900 -> most recent first
    assert interest.rank(rows, "nonsense") == interest.rank(rows, "overall")
    assert set(interest.RANK_BY) >= {"overall", "close", "partisan", "amended", "cosponsors", "votes"}


# ---------------------------------------------------------------- the count columns

def test_bill_counts_parse_and_backfill_from_cache(tmp_path, monkeypatch):
    assert congress.bill_counts({"cosponsors": {"count": 7}, "amendments": {"count": 3}, "actions": {"count": 12}}) == \
        {"cosponsor_count": 7, "amendment_count": 3, "action_count": 12}
    assert congress.bill_counts({}) == {"cosponsor_count": 0, "amendment_count": 0, "action_count": 0}   # absent = none
    parsed = congress.parse_bill(json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "congress_bill_hr1.json").read_text()), 119, "HR 1")
    assert parsed["amendment_count"] == 493 and parsed["action_count"] == 59

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "c.db")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    conn = db.connect()
    for i, status in enumerate(("ok", "ok", "not_found"), start=1):
        conn.execute("INSERT INTO bills (congress, bill_id, title, status) VALUES (119, ?, 't', ?)", (f"HR {i}", status))
    conn.commit()
    (tmp_path / "raw/congress/119/hr").mkdir(parents=True)
    (tmp_path / "raw/congress/119/hr/1.json").write_text(json.dumps({"bill": {"cosponsors": {"count": 9}, "amendments": {"count": 4}, "actions": {"count": 20}}}))
    (tmp_path / "raw/congress/119/hr/3.json").write_text(json.dumps({"bill": {"amendments": {"count": 99}}}))      # not_found rows are skipped
    assert congress.backfill_counts(conn) == 1                                                                  # HR 2 has no cache file
    assert tuple(conn.execute("SELECT cosponsor_count, amendment_count, action_count FROM bills WHERE bill_id = 'HR 1'").fetchone()) == (9, 4, 20)
    assert conn.execute("SELECT cosponsor_count FROM bills WHERE bill_id IN ('HR 2', 'HR 3')").fetchall()[0][0] is None
    assert congress.backfill_counts(conn) == 0                                                                  # idempotent


# ---------------------------------------------------------------- the page

def _vote(conn, vid, bill_id, date, yeas, nays, passage=1, chamber="House"):
    conn.execute("INSERT INTO votes (vote_id, chamber, congress, session, roll_num, vote_date, question, bill_id, title, result, "
                 "is_passage, yeas, nays) VALUES (?,?,119,1,?,?,'On Passage',?,?,'Passed',?,?,?)",
                 (vid, chamber, int(vid[-3:]), date, bill_id, f"{bill_id} Act", passage, yeas, nays))


def _party_votes(conn, vid, d_yea, r_yea, n=10):
    """n Democrats and n Republicans on a vote; d_yea / r_yea of each side vote Yea, the rest Nay."""
    for party, yea in (("D", d_yea), ("R", r_yea)):
        for i in range(n):
            mid = f"M{party}{vid[-3:]}{i}"
            conn.execute("INSERT OR IGNORE INTO members (member_id, full_name, party, state, chamber) VALUES (?,?,?,?,?)", (mid, mid, party, "XX", "House"))
            conn.execute("INSERT INTO member_votes (vote_id, member_id, position, position_raw, party) VALUES (?,?,?,?,?)",
                         (vid, mid, "Yea" if i < yea else "Nay", "", party))


@pytest.fixture()
def top(client):
    conn = db.connect()
    _vote(conn, "H-2026-901", "HR 5", "2026-03-10", 200, 199)                 # newest, razor-thin
    _party_votes(conn, "H-2026-901", d_yea=0, r_yea=10)                      # ...and party-line
    _vote(conn, "H-2026-902", "HR 6", "2026-03-20", 400, 20)                 # newest, lopsided + bipartisan
    _party_votes(conn, "H-2026-902", d_yea=10, r_yea=10)
    conn.execute("INSERT INTO bills (congress, bill_id, title, policy_area, brief, became_law, amendment_count, cosponsor_count) "
                 "VALUES (119, 'HR 5', 'Five Act', 'Economics', 'Does a five thing.', 0, 300, 2)")
    conn.commit()
    return client


def ids(client, url):
    import re
    html = client.get(url).data.decode()
    return re.findall(r'<td class="rank">\d+</td>.*?<b>([^<]+)</b>', html, re.S)


def test_top_defaults_to_latest_month_and_filters_by_year_and_month(top):
    assert ids(top, "/top") == ["HR 5", "HR 6"]                               # March 2026 = latest month with votes; S 1582 / S 5 are older
    assert "March 2026" in top.get("/top").data.decode()
    assert ids(top, "/top?year=2025&month=7") == ["S 1582"]
    assert set(ids(top, "/top?year=2025")) == {"S 1582", "S 5"}
    assert set(ids(top, "/top?year=&month=")) == {"HR 5", "HR 6", "S 1582", "S 5"} and "All time" in top.get("/top?year=&month=").data.decode()
    assert ids(top, "/top?year=&month=7") == ["S 1582"] and "July (all years)" in top.get("/top?year=&month=7").data.decode()
    assert "No bills with votes in that period" in top.get("/top?year=2030").data.decode()
    junk = top.get("/top?year=abc&month=99&by=zzz")
    assert junk.status_code == 200 and set(ids(top, "/top?year=abc&month=99")) == {"HR 5", "HR 6", "S 1582", "S 5"}   # junk -> all time, no error


def test_top_ranking_reasons_summary_and_controls(top):
    page = top.get("/top").data.decode()
    assert "Razor-thin 200–199" in page and "Party-line (D 0% vs R 100% Yea)" in page and "300 amendments" in page
    assert "Does a five thing." in page and "Economics" in page and 'data-bill="HR 5"' in page            # brief, topic, thumbs control
    assert ids(top, "/top?by=amended") == ["HR 5", "HR 6"] and ids(top, "/top?by=close")[0] == "HR 5"
    assert ids(top, "/top?year=&month=&by=votes")[0] in {"HR 5", "HR 6", "S 1582", "S 5"}
    assert 'class="nav active" href="/top"' in page and "Rank by" in page and "How is the interest score worked out?" in page
    assert "won't be read automatically" not in page and "can't be read automatically" in page             # explains why no view counts
    assert [h for h in ("<th>Summary</th></tr>",) if h in page]                                            # Summary stays the last column
    header = page[page.index('<th class="rankcol">'):page.index("</tr>", page.index('<th class="rankcol">'))]
    assert header.rstrip().endswith("<th>Summary</th>")
    first_hr6 = page[page.index("HR 6"):]
    assert "Broad bipartisan support" in first_hr6                                                         # lopsided + bipartisan is labelled, not hyped


def test_top_link_and_thumbs_work_from_the_leaderboard(top, monkeypatch, tmp_path):
    top.post("/bill/119/HR%205/star?to=agree", headers={"X-Politracker": "1"})
    page = top.get("/top").data.decode()
    assert 'data-stance="agree"' in page and '/vote/H-2026-901' in page                                    # links to the bill's vote page
    assert "Rating" in page
