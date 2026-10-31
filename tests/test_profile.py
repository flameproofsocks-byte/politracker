from politracker import profile


def party_rows(bill, d_yea, d_nay, r_yea, r_nay, congress=119):
    return [{"congress": congress, "bill_id": bill, "party": "D", "yea": d_yea, "nay": d_nay},
            {"congress": congress, "bill_id": bill, "party": "R", "yea": r_yea, "nay": r_nay}]


def rated_all(stance, *bills):
    return {(119, b): stance for b in bills}


def test_strong_democratic_lean_and_flip():
    rows = sum((party_rows(b, 90, 10, 10, 90) for b in ("HR 1", "HR 2", "HR 3")), [])   # D 90% Yea, R 10% Yea
    out = profile.analyze(rated_all("agree", "HR 1", "HR 2", "HR 3"), rows, {})
    assert out["lean"] == 80.0 and out["label"] == "Strongly closer to Democrats"
    assert out["sides"] == {"D": 3, "R": 0, "tie": 0} and out["mean_dem"] == 90.0 and out["mean_rep"] == 10.0
    flipped = profile.analyze(rated_all("disagree", "HR 1", "HR 2", "HR 3"), rows, {})   # wanting Nay flips who matches
    assert flipped["lean"] == -80.0 and flipped["label"] == "Strongly closer to Republicans"
    assert flipped["mean_dem"] == 10.0 and flipped["mean_rep"] == 90.0


def test_mild_lean_and_middle():
    mild = sum((party_rows(b, 60, 40, 40, 60) for b in ("A 1", "A 2", "A 3")), [])      # gap exactly 20 pts -> partisan
    out = profile.analyze(rated_all("agree", "A 1", "A 2", "A 3"), mild, {})
    assert out["partisan"] == 3 and out["lean"] == 20.0 and out["label"] == "Closer to Democrats"
    rows = party_rows("B 1", 90, 10, 10, 90) + party_rows("B 2", 10, 90, 90, 10) \
        + party_rows("B 3", 90, 10, 10, 90) + party_rows("B 4", 10, 90, 90, 10)
    mid = profile.analyze(rated_all("agree", "B 1", "B 2", "B 3", "B 4"), rows, {})
    assert mid["lean"] == 0.0 and mid["label"] == "In the middle (no clear lean)"
    assert mid["sides"] == {"D": 2, "R": 2, "tie": 0}


def test_bipartisan_bills_do_not_move_the_lean():
    rows = sum((party_rows(b, 95, 5, 93, 7) for b in ("C 1", "C 2", "C 3", "C 4")), [])   # both parties ~94% Yea
    out = profile.analyze(rated_all("agree", "C 1", "C 2", "C 3", "C 4"), rows, {})
    assert out["scored"] == 4 and out["partisan"] == 0 and out["lean"] is None and out["label"] is None
    assert not any(b["partisan"] for b in out["bills"])
    # ...and a single partisan bill is still "not enough data"
    few = profile.analyze(rated_all("agree", "D 1"), party_rows("D 1", 90, 10, 10, 90), {})
    assert few["partisan"] == 1 and few["label"] is None


def test_bills_without_party_data_are_listed_but_not_scored():
    out = profile.analyze({(119, "E 1"): "agree", (119, "E 2"): "agree"}, party_rows("E 1", 80, 20, 20, 80), {})
    assert out["scored"] == 1 and len(out["bills"]) == 2
    missing = next(b for b in out["bills"] if b["bill_id"] == "E 2")
    assert missing["dem_align"] is None and missing["side"] is None
    only_d = [{"congress": 119, "bill_id": "E 2", "party": "D", "yea": 5, "nay": 5}]    # one party only -> unscorable
    assert profile.analyze({(119, "E 2"): "agree"}, only_d, {})["scored"] == 0


def test_pooled_chambers_and_all_present_votes():
    rows = [{"congress": 119, "bill_id": "F 1", "party": "D", "yea": 200, "nay": 0},    # House D
            {"congress": 119, "bill_id": "F 1", "party": "D", "yea": 0, "nay": 40},     # Senate D (pooled: 200/240)
            {"congress": 119, "bill_id": "F 1", "party": "R", "yea": 0, "nay": 100}]
    out = profile.analyze({(119, "F 1"): "agree"}, rows, {})
    b = out["bills"][0]
    assert round(b["dem_yea"], 3) == round(200 / 240, 3) and b["rep_yea"] == 0 and b["side"] == "D"


def test_topics_favor_oppose_mixed():
    rated = {(119, "G 1"): "agree", (119, "G 2"): "agree", (119, "G 3"): "disagree",
             (119, "G 4"): "disagree", (119, "G 5"): "agree", (119, "G 6"): "disagree", (119, "G 7"): "agree"}
    info = {(119, "G 1"): {"topic": "Health"}, (119, "G 2"): {"topic": "Health"}, (119, "G 3"): {"topic": "Health"},
            (119, "G 4"): {"topic": "Energy"}, (119, "G 5"): {"topic": "Taxation"}, (119, "G 6"): {"topic": "Taxation"},
            (119, "G 7"): {}}
    out = profile.analyze(rated, [], info)
    assert [t["topic"] for t in out["favor"]] == ["Health", "(unclassified)"]       # 2 vs 1 ; 1 vs 0
    assert [t["topic"] for t in out["oppose"]] == ["Energy"]
    assert [t["topic"] for t in out["mixed"]] == ["Taxation"]
    assert len(out["favor"][0]["agree"]) == 2 and len(out["favor"][0]["disagree"]) == 1
