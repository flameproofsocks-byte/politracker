"""Your political profile, derived from the bills you rated green (agree) / red (disagree).

Pure functions: no DB, no Flask. web.py gathers the inputs, tests feed synthetic ones.

How the party leaning works (deliberately simple and transparent):
  * For each rated bill we look at how the Democrats and the Republicans actually voted on the measure
    itself (passage votes, pooled over chambers, Yea/Nay only).
  * Your pick says which way you wanted it to go: agree = Yea, disagree = Nay. A party's "alignment" on
    that bill is the share of its members who voted the way you wanted.
  * Bills the parties split on (Yea rates differ by >= PARTISAN_GAP) are the informative ones. Bills nearly
    everyone voted the same way on say nothing about D vs R, so the headline leaning only uses the former.
  * lean = average (Dem alignment - Rep alignment) over those bills, in points (-100..+100).
It is a rough guide to who votes like you on the bills you picked, not a verdict on your politics.
"""

PARTISAN_GAP = 0.20     # parties' Yea rates must differ by at least 20 points for a bill to count as partisan
MIN_PARTISAN = 3        # below this many partisan rated bills we say "not enough data"
LEAN_MILD, LEAN_STRONG = 15, 40   # |lean| thresholds, in points


def _rate(yea, nay):
    total = yea + nay
    return yea / total if total else None


def label_for(lean, n_partisan):
    if n_partisan < MIN_PARTISAN:
        return None
    side = "Democrats" if lean > 0 else "Republicans"
    if abs(lean) >= LEAN_STRONG:
        return f"Strongly closer to {side}"
    if abs(lean) >= LEAN_MILD:
        return f"Closer to {side}"
    return "In the middle (no clear lean)"


def analyze(rated, party_rows, info):
    """rated: {(congress, bill_id): 'agree'|'disagree'}
    party_rows: iterable of dicts {congress, bill_id, party ('D'|'R'), yea, nay}  (passage votes, pooled)
    info: {(congress, bill_id): {"title", "topic", "vote_id"}}  (missing keys are fine)

    Returns {"bills": [...per rated bill...], "scored", "partisan", "mean_dem", "mean_rep", "lean", "label",
             "sides": {"D": n, "R": n, "tie": n}, "favor": [...], "oppose": [...], "mixed": [...]}."""
    parties = {}
    for r in party_rows:
        slot = parties.setdefault((r["congress"], r["bill_id"]), {})
        old = slot.get(r["party"], (0, 0))
        slot[r["party"]] = (old[0] + r["yea"], old[1] + r["nay"])

    bills, diffs, dem_all, rep_all = [], [], [], []
    sides = {"D": 0, "R": 0, "tie": 0}
    for key, stance in rated.items():
        meta = info.get(key, {})
        row = {"congress": key[0], "bill_id": key[1], "stance": stance, "title": meta.get("title"),
               "topic": meta.get("topic"), "vote_id": meta.get("vote_id"), "date": meta.get("date"),
               "brief": meta.get("brief"),
               "dem_yea": None, "rep_yea": None, "dem_align": None, "rep_align": None,
               "partisan": False, "side": None}
        votes = parties.get(key, {})
        d_rate = _rate(*votes["D"]) if "D" in votes else None
        r_rate = _rate(*votes["R"]) if "R" in votes else None
        if d_rate is not None and r_rate is not None:
            want_yea = stance == "agree"
            row["dem_yea"], row["rep_yea"] = d_rate, r_rate
            row["dem_align"] = d_rate if want_yea else 1 - d_rate
            row["rep_align"] = r_rate if want_yea else 1 - r_rate
            dem_all.append(row["dem_align"])
            rep_all.append(row["rep_align"])
            if abs(d_rate - r_rate) >= PARTISAN_GAP - 1e-9:     # epsilon: 0.6 - 0.4 is 0.19999999999999996
                row["partisan"] = True
                gap = row["dem_align"] - row["rep_align"]
                diffs.append(gap)
                row["side"] = "D" if gap > 0 else "R" if gap < 0 else "tie"
                sides[row["side"]] += 1
        bills.append(row)

    lean = round(100 * sum(diffs) / len(diffs), 1) if diffs else None
    out = {"bills": bills, "scored": len(dem_all), "partisan": len(diffs), "sides": sides,
           "mean_dem": round(100 * sum(dem_all) / len(dem_all), 1) if dem_all else None,
           "mean_rep": round(100 * sum(rep_all) / len(rep_all), 1) if rep_all else None,
           "lean": lean, "label": label_for(lean, len(diffs)) if lean is not None else None}
    out.update(topics_summary(bills))
    return out


def topics_summary(bills):
    """Group rated bills by Congress.gov policy area into things you generally favor / oppose / are split on.

    favor = more green than red in that topic; oppose = more red than green; mixed = equal and both present.
    Each entry: {"topic", "agree": [bills], "disagree": [bills]}; biggest topics first."""
    by_topic = {}
    for b in bills:
        t = by_topic.setdefault(b["topic"] or "(unclassified)", {"topic": b["topic"] or "(unclassified)",
                                                                 "agree": [], "disagree": []})
        t[b["stance"]].append(b)
    ordered = sorted(by_topic.values(), key=lambda t: (-(len(t["agree"]) + len(t["disagree"])), t["topic"]))
    return {"favor": [t for t in ordered if len(t["agree"]) > len(t["disagree"])],
            "oppose": [t for t in ordered if len(t["disagree"]) > len(t["agree"])],
            "mixed": [t for t in ordered if t["agree"] and len(t["agree"]) == len(t["disagree"])]}
