"""The "Top bills" interest score: which bills were the most interesting, and why.

Congress.gov's own most-viewed list can't be read automatically (bot challenge + robots.txt), so this is OUR measure, built only from
data we hold. It is not a popularity or page-view count. Every part is shown in the UI; tune WEIGHTS/CAPS here.

  close      narrowest passage vote: 1 - |yeas - nays| / (yeas + nays)            (a tie = 1, unanimous = 0)
  partisan   gap between the parties' Yea rates on passage votes, pooled over chambers (0 = alike, 1 = opposite)
  amended    number of amendments filed (log-scaled, maxes out at CAPS["amendments"])
  votes      number of roll calls on the bill: amendments, cloture, motions... (log-scaled)
  cosponsors number of cosponsors (log-scaled)
  law        1 if it became law

Pure functions only (no DB / Flask) so they are unit-tested with synthetic data.
"""
import math
from datetime import date

WEIGHTS = {"close": 30, "partisan": 25, "amended": 15, "votes": 10, "cosponsors": 10, "law": 10}     # sums to 100
CAPS = {"amended": 100, "votes": 12, "cosponsors": 100}       # at or above this count the signal is "maxed out"
RANK_BY = {                                                    # UI "Rank by" choices -> (label, sort key)
    "overall": ("Overall interest", "score"),
    "close": ("Closest vote", "close"),
    "partisan": ("Most party-line", "partisan"),
    "amended": ("Most amended", "amendments"),
    "cosponsors": ("Most cosponsors", "cosponsors"),
    "votes": ("Most roll calls", "n_votes"),
}


def closeness(yeas, nays):
    total = (yeas or 0) + (nays or 0)
    return 0.0 if not total else 1 - abs(yeas - nays) / total


def partisanship(d_yea, d_nay, r_yea, r_nay):
    """|D Yea rate - R Yea rate|, 0 when either party has no Yea/Nay votes."""
    if not (d_yea + d_nay) or not (r_yea + r_nay):
        return 0.0
    return abs(d_yea / (d_yea + d_nay) - r_yea / (r_yea + r_nay))


def scaled(count, cap):
    """0..1, log-scaled so the first few amendments/cosponsors matter more than the hundredth."""
    return 0.0 if not count or count <= 0 else min(1.0, math.log1p(count) / math.log1p(cap))


def score(parts):
    """parts: {close, partisan, amended, votes, cosponsors, law} each 0..1 -> 0..100 (one decimal)."""
    return round(sum(WEIGHTS[k] * max(0.0, min(1.0, parts.get(k, 0.0))) for k in WEIGHTS), 1)


def reasons(b):
    """Short human tags explaining a bill's score, strongest signals first (max 4)."""
    out = []
    if b["close"] >= 0.9 and b["tally"]:
        out.append(f"Razor-thin {b['tally']}")
    elif b["close"] >= 0.75 and b["tally"]:
        out.append(f"Close vote {b['tally']}")
    if b["partisan"] >= 0.6:
        out.append(f"Party-line (D {b['dem_yea_pct']}% vs R {b['rep_yea_pct']}% Yea)")
    elif (b["partisan"] <= 0.1 and b["dem_yea_pct"] is not None and b["rep_yea_pct"] is not None
          and min(b["dem_yea_pct"], b["rep_yea_pct"]) >= 80):
        out.append("Broad bipartisan support")
    if b["amendments"] >= 25:
        out.append(f"{b['amendments']:,} amendments")
    if b["cosponsors"] >= 50:
        out.append(f"{b['cosponsors']:,} cosponsors")
    if b["n_votes"] >= 5:
        out.append(f"{b['n_votes']} roll calls")
    if b["became_law"]:
        out.append("Became law")
    return out[:4]


def period_filter(bill_date, year=None, month=None):
    """True if the ISO `bill_date` falls in `year` (and `month` if given); no year/month = everything."""
    if not bill_date:
        return not year and not month
    y, m = int(bill_date[:4]), int(bill_date[5:7])
    return (not year or y == year) and (not month or m == month)


def build_leaderboard(bills, passage_votes, party_rows):
    """Score every bill.

    bills:         [{congress, bill_id, title, topic, brief, became_law, cosponsor_count, amendment_count, n_votes,
                     last_vote, last_vote_id, last_passage}]   (counts may be None = unknown -> 0)
    passage_votes: [{congress, bill_id, vote_id, chamber, vote_date, yeas, nays, result}]  (votes on the measure itself)
    party_rows:    [{congress, bill_id, party ('D'|'R'), yea, nay}]  (passage votes, may repeat per chamber -> pooled)
    Returns a list of dicts (unsorted) with the bill fields plus: score, parts, close, partisan, amendments, cosponsors,
    tally, dem_yea_pct, rep_yea_pct, reasons, bill_date, link_vote_id."""
    best_vote, pooled = {}, {}
    for v in passage_votes:
        key = (v["congress"], v["bill_id"])
        c = closeness(v["yeas"], v["nays"])
        if key not in best_vote or c > best_vote[key][0]:
            best_vote[key] = (c, v)
    for r in party_rows:
        slot = pooled.setdefault((r["congress"], r["bill_id"]), {"D": [0, 0], "R": [0, 0]})
        if r["party"] in slot:
            slot[r["party"]][0] += r["yea"] or 0
            slot[r["party"]][1] += r["nay"] or 0
    out = []
    for b in bills:
        key = (b["congress"], b["bill_id"])
        close, v = best_vote.get(key, (0.0, None))
        p = pooled.get(key)
        part = partisanship(*p["D"], *p["R"]) if p else 0.0
        d_pct = round(100 * p["D"][0] / sum(p["D"])) if p and sum(p["D"]) else None
        r_pct = round(100 * p["R"][0] / sum(p["R"])) if p and sum(p["R"]) else None
        amend, cosp = b.get("amendment_count") or 0, b.get("cosponsor_count") or 0
        parts = {"close": close, "partisan": part, "amended": scaled(amend, CAPS["amended"]),
                 "votes": scaled(b["n_votes"], CAPS["votes"]), "cosponsors": scaled(cosp, CAPS["cosponsors"]),
                 "law": 1.0 if b.get("became_law") else 0.0}
        row = {**b, "parts": parts, "score": score(parts), "close": close, "partisan": part, "amendments": amend,
               "cosponsors": cosp, "tally": f"{v['yeas']}–{v['nays']}" if v else None, "dem_yea_pct": d_pct,
               "rep_yea_pct": r_pct, "bill_date": b.get("last_passage") or b.get("last_vote"),
               "link_vote_id": v["vote_id"] if v else b.get("last_vote_id")}
        row["reasons"] = reasons(row)
        out.append(row)
    return out


def rank(rows, by="overall"):
    """Sort leaderboard rows by a RANK_BY key (unknown keys fall back to overall); ties -> most recent first."""
    key = RANK_BY.get(by, RANK_BY["overall"])[1]
    return sorted(rows, key=lambda r: (-(r[key] or 0), -(int((r["bill_date"] or "0").replace("-", "")))))
