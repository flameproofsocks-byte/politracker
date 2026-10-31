"""Local Flask UI. Read-only over the SQLite DB. Run: python -m politracker serve"""
import json

from flask import Flask, Response, abort, g, jsonify, render_template, request

import calendar
import io
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timezone

from . import bios, config, db, frames, interest, profile, summarize, video

app = Flask(__name__)
PAGE = 100


@app.template_filter("dictupdate")
def dictupdate(d, **kw):
    """Query-string for pagination links: current args with overrides."""
    from urllib.parse import urlencode
    return urlencode({**d, **kw})


def stances():
    """{(congress, bill_id): 'agree'|'disagree'} for this request (file read once per request)."""
    try:
        if "stances" not in g:
            g.stances = load_saved()
        return g.stances
    except RuntimeError:                      # outside a request (scripts/tests)
        return load_saved()


def q(sql, args=()):
    conn = db.connect()
    st = stances()
    # lets SQL sort/filter/score by the user's green/red picks, which live in a JSON file, not the DB
    conn.create_function("stance", 2, lambda c, b: st.get((c, b)), deterministic=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


BILL_JOIN = "LEFT JOIN bills b ON b.congress = v.congress AND b.bill_id = v.bill_id"


@app.context_processor
def inject_topics():
    """Policy areas available for the topic dropdown (empty until `bills` has been run)."""
    return {"topics": [r["policy_area"] for r in q(
        "SELECT policy_area FROM bills WHERE policy_area IS NOT NULL GROUP BY 1 ORDER BY 1")]}


STANCES = ("agree", "disagree")      # UI: green star / red star
NEXT_STANCE = {None: "agree", "agree": "disagree", "disagree": None}   # click cycle: blank -> green -> red -> blank


def load_saved():
    """{(congress, bill_id): stance}. Stored as JSON, not in the DB, so rebuilds keep it.

    Older files held plain stars as [congress, bill_id]; those load as 'agree' (green)."""
    try:
        raw = json.loads(config.SAVED_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    out = {}
    for congress, bill_id, *rest in raw:
        out[(congress, bill_id)] = rest[0] if rest and rest[0] in STANCES else "agree"
    return out


def write_saved(rated):
    config.SAVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.SAVED_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps([[c, b, st] for (c, b), st in sorted(rated.items())]))
    tmp.replace(config.SAVED_PATH)          # atomic: a crash never leaves a half-written file


@app.context_processor
def inject_stances():
    return {"stances": {f"{c}|{b}": st for (c, b), st in stances().items()}}


@app.post("/bill/<int:congress>/<bill_id>/star")
def bill_star(congress, bill_id):
    """Cycle a bill's mark: none -> agree (green) -> disagree (red) -> none. Same CSRF header guard as /summary.
    `?to=agree|disagree|none` sets it directly instead of cycling (used by the My picks page)."""
    if request.headers.get("X-Politracker") != "1":
        abort(403)
    if not q("SELECT 1 FROM votes WHERE congress = ? AND bill_id = ? LIMIT 1", (congress, bill_id)):
        abort(404)
    rated, key = load_saved(), (congress, bill_id)
    to = request.args.get("to")
    if to is None:
        nxt = NEXT_STANCE[rated.get(key)]
    elif to in STANCES or to == "none":
        nxt = None if to == "none" else to
    else:
        abort(400)
    if nxt:
        rated[key] = nxt
    else:
        rated.pop(key, None)
    write_saved(rated)
    return jsonify(stance=nxt)


def compute_profile():
    """profile.analyze() over the user's current picks (used by /mine and the home page card)."""
    rated = stances()
    party_rows = q("SELECT v.congress, v.bill_id, mv.party AS party, SUM(mv.position = 'Yea') AS yea, "
                   "SUM(mv.position = 'Nay') AS nay FROM member_votes mv JOIN votes v USING (vote_id) "
                   "WHERE v.is_passage = 1 AND mv.party IN ('D', 'R') AND stance(v.congress, v.bill_id) IS NOT NULL "
                   "GROUP BY v.congress, v.bill_id, mv.party")
    info = {}
    for r in q(f"SELECT v.congress, v.bill_id, COALESCE(b.title, v.title) AS title, b.policy_area AS topic, "
               f"b.brief, v.vote_id, v.vote_date AS date FROM votes v {BILL_JOIN} WHERE stance(v.congress, v.bill_id) IS NOT NULL "
               "ORDER BY v.is_passage DESC, v.vote_date DESC"):
        info.setdefault((r["congress"], r["bill_id"]), dict(r))     # first row = best vote to link to
    result = profile.analyze(rated, [dict(r) for r in party_rows], info)
    result["bills"].sort(key=lambda b: (b["stance"] != "agree", b["date"] is None, -(int((b["date"] or "0").replace("-", "")))))
    return result


@app.route("/mine")
def mine():
    """My picks: view/change the bills you rated, which party you vote like, and what you favor/oppose."""
    return render_template("mine.html", p=compute_profile(), gap=int(profile.PARTISAN_GAP * 100),
                           min_partisan=profile.MIN_PARTISAN)


def vote_filters(args, alias="v"):
    """Build WHERE fragments shared by the votes list and a member's vote history."""
    where, params = [], []
    text = (args.get("q") or "").strip()
    for term in text.split():
        where.append(f"({alias}.title LIKE ? OR {alias}.bill_id LIKE ? OR {alias}.question LIKE ? "
                     f"OR b.title LIKE ? OR b.policy_area LIKE ? OR b.brief LIKE ?)")
        params += [f"%{term}%"] * 6
    if args.get("topic"):
        where.append("b.policy_area = ?")
        params.append(args["topic"])
    if args.get("chamber") in ("House", "Senate"):
        where.append(f"{alias}.chamber = ?")
        params.append(args["chamber"])
    if args.get("scope", "passage") == "passage":
        where.append(f"{alias}.is_passage = 1")
    elif args.get("scope") == "bills":
        where.append(f"{alias}.bill_id IS NOT NULL")
    if args.get("since"):
        where.append(f"{alias}.vote_date >= ?")
        params.append(args["since"])
    return where, params


# Sortable columns. Each entry is a list of ORDER BY terms; "{d}" is replaced by asc/desc. Fixed terms
# (no "{d}") keep e.g. bill-less votes at the bottom in both directions. Only keys listed here are ever
# interpolated into SQL, so the `sort` URL parameter cannot inject anything.
SORT_COLUMNS = {
    "date": ["v.vote_date {d}"],
    "vote": ["v.chamber {d}", "v.roll_num {d}"],
    # bill type ("HR", "S", "SJRES"...) then bill NUMBER numerically, so HR 9 sorts before HR 10
    "bill": ["(v.bill_id IS NULL) ASC", "substr(v.bill_id, 1, instr(v.bill_id, ' ') - 1) {d}",
             "CAST(substr(v.bill_id, instr(v.bill_id, ' ') + 1) AS INTEGER) {d}", "v.title {d}"],
    "result": ["v.result {d}", "v.yeas {d}"],
}
# SQL helpers over the stance() function registered in q(). RANK: green bills first, then red, then unrated.
# MATCH (member page only): +1 their passage vote agrees with your pick, -1 it opposes it, 0 = n/a
# (unrated bill, procedural vote, Present/Not Voting). Only votes on the measure itself are scored.
_ST = "stance(v.congress, v.bill_id)"
RANK_SQL = f"(CASE {_ST} WHEN 'agree' THEN 2 WHEN 'disagree' THEN 1 ELSE 0 END)"
MATCH_SQL = (f"(CASE WHEN v.is_passage = 1 AND (({_ST} = 'agree' AND mv.position = 'Yea') OR "
             f"({_ST} = 'disagree' AND mv.position = 'Nay')) THEN 1 "
             f"WHEN v.is_passage = 1 AND (({_ST} = 'agree' AND mv.position = 'Nay') OR "
             f"({_ST} = 'disagree' AND mv.position = 'Yea')) THEN -1 ELSE 0 END)")
MEMBER_SORT_COLUMNS = {"position": ["mv.position {d}"], "party": ["pm.maj {d}"],
                       "match": [MATCH_SQL + " {d}"]}
DESC_FIRST = ("star", "date", "match")   # first click on these sorts descending; everything else ascending


def order_by(args, member=False):
    """-> (sql, key, dir). Default is rated bills first (green, then red), then newest first."""
    cols = {**SORT_COLUMNS, **(MEMBER_SORT_COLUMNS if member else {})}
    key = args.get("sort") if (args.get("sort") in cols or args.get("sort") == "star") else "star"
    direction = args.get("dir") if args.get("dir") in ("asc", "desc") else None
    if key == "star" and "sort" not in args:
        direction = "desc"                               # the default view
    direction = direction or ("desc" if key in DESC_FIRST else "asc")
    terms = [f"{RANK_SQL} {direction}"] if key == "star" else [t.format(d=direction) for t in cols[key]]
    if key != "date":
        terms.append("v.vote_date DESC")                 # tie-break: newest first
    terms.append("v.vote_id DESC")
    return ", ".join(terms), key, direction


HOME_LIMIT = 150


@app.route("/")
def index():
    name = (request.args.get("member") or "").strip()
    chamber = request.args.get("chamber") if request.args.get("chamber") in ("House", "Senate") else ""
    party = request.args.get("party") if request.args.get("party") in ("D", "R", "I") else ""
    state = (request.args.get("state") or "").strip().upper()
    filtered = bool(name or chamber or party or state)
    members, recent, prof = [], [], None
    if filtered:
        where, params = [], []
        if name:
            where.append("(full_name LIKE ? OR state = ?)")
            params += [f"%{name}%", name.upper()]
        for col, val in (("chamber", chamber), ("party", party), ("state", state)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        members = q("SELECT * FROM members WHERE " + " AND ".join(where) + " ORDER BY full_name LIMIT ?",
                    params + [HOME_LIMIT])
    else:
        recent = q(f"SELECT v.vote_id, v.chamber, v.vote_date, v.bill_id, v.title, v.result, v.yeas, v.nays, b.brief "
                   f"FROM votes v {BILL_JOIN} WHERE v.is_passage = 1 ORDER BY v.vote_date DESC, v.vote_id DESC LIMIT 8")
        if stances():
            prof = compute_profile()
    stats = q("SELECT (SELECT COUNT(*) FROM votes) v, (SELECT COUNT(*) FROM members) m, "
              "(SELECT MIN(vote_date) FROM votes) lo, (SELECT MAX(vote_date) FROM votes) hi")[0]
    states = [r["state"] for r in q("SELECT DISTINCT state FROM members WHERE state IS NOT NULL AND state != '' ORDER BY state")]
    return render_template("index.html", members=members, name=name, stats=stats, filtered=filtered, recent=recent,
                           prof=prof, states=states, limit=HOME_LIMIT)


TOP_LIMIT = 50


def _int_arg(name, lo, hi):
    """Query param as an int in [lo, hi], else None (junk is ignored, never an error)."""
    raw = (request.args.get(name) or "").strip()
    return int(raw) if raw.isdigit() and lo <= int(raw) <= hi else None


def leaderboard_rows():
    """Every bill with votes, scored by interest.build_leaderboard (shared by the Top bills page and the video briefs)."""
    bills = [dict(r) for r in q(
        "SELECT v.congress, v.bill_id, COUNT(*) AS n_votes, MAX(v.vote_date) AS last_vote, "
        "MAX(CASE WHEN v.is_passage = 1 THEN v.vote_date END) AS last_passage, "
        "(SELECT v2.vote_id FROM votes v2 WHERE v2.congress = v.congress AND v2.bill_id = v.bill_id "
        " ORDER BY v2.is_passage DESC, v2.vote_date DESC LIMIT 1) AS last_vote_id, "
        "COALESCE(b.title, MAX(v.title)) AS title, b.policy_area AS topic, b.brief, COALESCE(b.became_law, 0) AS became_law, "
        "b.cosponsor_count, b.amendment_count "
        "FROM votes v LEFT JOIN bills b ON b.congress = v.congress AND b.bill_id = v.bill_id "
        "WHERE v.bill_id IS NOT NULL AND v.congress IS NOT NULL GROUP BY v.congress, v.bill_id")]
    passage = [dict(r) for r in q("SELECT congress, bill_id, vote_id, chamber, vote_date, yeas, nays, result FROM votes "
                                  "WHERE is_passage = 1 AND bill_id IS NOT NULL AND congress IS NOT NULL")]
    party = [dict(r) for r in q("SELECT v.congress, v.bill_id, mv.party AS party, SUM(mv.position = 'Yea') AS yea, "
                                "SUM(mv.position = 'Nay') AS nay FROM member_votes mv JOIN votes v USING (vote_id) "
                                "WHERE v.is_passage = 1 AND mv.party IN ('D', 'R') AND v.bill_id IS NOT NULL "
                                "GROUP BY v.congress, v.bill_id, mv.party")]
    return interest.build_leaderboard(bills, passage, party)


@app.route("/top")
def top_bills():
    """Leaderboard of the most interesting bills (see interest.py for the score), filterable by year and month."""
    rows = leaderboard_rows()
    dates = sorted(r["bill_date"] for r in rows if r["bill_date"])
    year, month = _int_arg("year", 2000, 2100), _int_arg("month", 1, 12)
    if "year" not in request.args and "month" not in request.args and dates:
        year, month = int(dates[-1][:4]), int(dates[-1][5:7])           # first visit: the most recent month with votes
    by = request.args.get("by") if request.args.get("by") in interest.RANK_BY else "overall"
    matched = [r for r in rows if interest.period_filter(r["bill_date"], year, month)]
    ranked = interest.rank(matched, by)[:TOP_LIMIT]
    if year and month:
        period = f"{calendar.month_name[month]} {year}"
    elif year:
        period = str(year)
    elif month:
        period = f"{calendar.month_name[month]} (all years)"
    else:
        period = "All time"
    return render_template("top.html", rows=ranked, matched=len(matched), period=period, year=year, month=month, by=by,
                           years=sorted({int(d[:4]) for d in dates}, reverse=True), months=list(enumerate(calendar.month_name))[1:],
                           rank_by=interest.RANK_BY, weights=interest.WEIGHTS, limit=TOP_LIMIT,
                           labels={"close": "Close vote", "partisan": "Party-line", "amended": "Amendments", "votes": "Roll calls",
                                   "cosponsors": "Cosponsors", "law": "Became law"})


@app.route("/favicon.ico")
def favicon():
    """The site has no icon; answer quietly instead of logging a 404 in every browser console."""
    return "", 204


@app.route("/api/members")
def api_members():
    """Typeahead for the home page search box: up to 8 members whose name contains `q`, best matches first."""
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify([])
    rows = q("SELECT member_id, full_name, party, state, chamber FROM members WHERE full_name LIKE ? "
             "ORDER BY instr(lower(full_name), lower(?)), full_name LIMIT 8", (f"%{term}%", term))
    return jsonify([{"id": r["member_id"], "name": r["full_name"], "party": r["party"], "state": r["state"],
                     "chamber": r["chamber"]} for r in rows])


@app.route("/votes")
def votes():
    where, params = vote_filters(request.args)
    page = max(int(request.args.get("page", 1)), 1)
    order_sql, key, direction = order_by(request.args)
    sql = (f"SELECT v.*, b.policy_area, b.brief, b.brief_source FROM votes v {BILL_JOIN} " + ("WHERE " + " AND ".join(where) if where else "") +
           f" ORDER BY {order_sql} LIMIT ? OFFSET ?")
    rows = q(sql, params + [PAGE + 1, (page - 1) * PAGE])
    return render_template("votes.html", votes=rows[:PAGE], more=len(rows) > PAGE, page=page,
                           sorting={"key": key, "dir": direction})


@app.route("/vote/<vote_id>")
def vote(vote_id):
    rows = q(f"SELECT v.*, b.title AS bill_title, b.policy_area, b.sponsor_id, b.sponsor_name, "
             f"b.sponsor_party, b.introduced_date, b.latest_action_date, b.latest_action_text, "
             f"b.became_law, b.law_number, b.url AS bill_url FROM votes v {BILL_JOIN} WHERE v.vote_id = ?",
             (vote_id,))
    if not rows:
        abort(404)
    by_party = q("SELECT party, position, COUNT(*) n FROM member_votes WHERE vote_id = ? "
                 "GROUP BY party, position ORDER BY party, position", (vote_id,))
    ballots = q("SELECT m.member_id, m.full_name, m.state, mv.party, mv.position FROM member_votes mv "
                "JOIN members m USING (member_id) WHERE mv.vote_id = ? ORDER BY mv.position, m.full_name",
                (vote_id,))
    cached = None
    if rows[0]["bill_id"]:
        conn = db.connect()
        cached = summarize.get_cached(conn, rows[0]["congress"], rows[0]["bill_id"])
        conn.close()
    return render_template("vote.html", v=rows[0], by_party=by_party, ballots=ballots, summary=cached)


@app.post("/bill/<int:congress>/<bill_id>/summary")
def bill_summary(congress, bill_id):
    """Generate (or return cached) plain-English summary. Costs LLM usage, so it needs a custom header:
    browsers refuse to send it cross-site without a CORS preflight, which blocks drive-by POSTs."""
    if request.headers.get("X-Politracker") != "1":
        abort(403)
    if not q("SELECT 1 FROM votes WHERE congress = ? AND bill_id = ? LIMIT 1", (congress, bill_id)):
        abort(404)
    try:
        row = summarize.get_or_create(congress, bill_id)
    except summarize.SummaryError as e:
        return jsonify(error=str(e)), 502
    return jsonify(summary=row["summary"], source=row["source"], truncated=bool(row["truncated"]),
                   model=row["model"])


@app.route("/member/<member_id>")
def member(member_id):
    rows = q("SELECT * FROM members WHERE member_id = ?", (member_id,))
    if not rows:
        abort(404)
    where, params = vote_filters(request.args)
    if request.args.get("position") in ("Yea", "Nay", "Present", "Not Voting"):
        where.append("mv.position = ?")
        params.append(request.args["position"])
    page = max(int(request.args.get("page", 1)), 1)
    order_sql, sort_key, sort_dir = order_by(request.args, member=True)
    cmp_party = compare_party(rows[0])
    # party_majority = the position most of the voter's own party took (Yea/Nay only) on that vote.
    sql = f"""
    WITH pm AS (
        SELECT vote_id, party,
               CASE WHEN SUM(position='Yea') >= SUM(position='Nay') THEN 'Yea' ELSE 'Nay' END AS maj
        FROM member_votes WHERE position IN ('Yea','Nay')
          AND vote_id IN (SELECT vote_id FROM member_votes WHERE member_id = ?)
        GROUP BY vote_id, party)
    SELECT v.*, b.policy_area, b.brief, b.brief_source, mv.position, mv.party AS voter_party, pm.maj AS party_majority,
           {MATCH_SQL} AS matches
    FROM member_votes mv JOIN votes v USING (vote_id) {BILL_JOIN}
    LEFT JOIN pm ON pm.vote_id = mv.vote_id AND pm.party = {cmp_party or 'mv.party'}
    WHERE mv.member_id = ? {''.join(' AND ' + w for w in where)}
    ORDER BY {order_sql} LIMIT ? OFFSET ?"""
    hist = q(sql, [member_id, member_id] + params + [PAGE + 1, (page - 1) * PAGE])
    # Summary uses the same filters but no paging.
    sum_sql = sql.replace("LIMIT ? OFFSET ?", "")
    all_rows = q(sum_sql, [member_id, member_id] + params)
    cast = [r for r in all_rows if r["position"] in ("Yea", "Nay")]
    with_party = [r for r in cast if r["party_majority"] == r["position"]]
    summary = {
        "total": len(all_rows),
        "counts": {p: sum(1 for r in all_rows if r["position"] == p) for p in ("Yea", "Nay", "Present", "Not Voting")},
        "with_party_pct": round(100 * len(with_party) / len(cast), 1) if cast else None,
    }
    by_topic = {}
    for r in cast:
        t = by_topic.setdefault(r["policy_area"] or "(unclassified)", {"Yea": 0, "Nay": 0})
        t[r["position"]] += 1
    by_topic = sorted(by_topic.items(), key=lambda kv: -(kv[1]["Yea"] + kv[1]["Nay"]))
    return render_template("member.html", m=rows[0], cmp_party=cmp_party, votes=hist[:PAGE], more=len(hist) > PAGE,
                           page=page, summary=summary, by_topic=by_topic,
                           picks=picks_alignment(member_id),
                           sorting={"key": sort_key, "dir": sort_dir})


def compare_party(member):
    """SQL literal of the party whose majority this member is compared against: None = their own party (the normal case);
    independents are compared with the party they caucus with ('D' or 'R') because 'majority of independents' is meaningless."""
    if member["party"] != "I":
        return None
    row = q("SELECT terms_json FROM member_bios WHERE member_id = ?", (member["member_id"],))
    letter = bios.caucus_letter(json.loads(row[0]["terms_json"])) if row and row[0]["terms_json"] else None
    return f"'{letter}'" if letter in ("D", "R") else None


def party_alignment(member_id, cmp_party=None):
    """How often the member's Yea/Nay votes match the majority of their party (or `cmp_party`'s), over ALL ingested roll calls
    and over final-passage votes only. -> {"all": {"n","agree","pct"}, "passage": {...}}"""
    rows = q(f"""
        WITH pm AS (
            SELECT vote_id, party,
                   CASE WHEN SUM(position = 'Yea') >= SUM(position = 'Nay') THEN 'Yea' ELSE 'Nay' END AS maj
            FROM member_votes WHERE position IN ('Yea', 'Nay')
              AND vote_id IN (SELECT vote_id FROM member_votes WHERE member_id = ?)
            GROUP BY vote_id, party)
        SELECT v.is_passage AS passage, COUNT(*) AS n, SUM(mv.position = pm.maj) AS agree
        FROM member_votes mv JOIN votes v USING (vote_id)
        JOIN pm ON pm.vote_id = mv.vote_id AND pm.party = {cmp_party or 'mv.party'}
        WHERE mv.member_id = ? AND mv.position IN ('Yea', 'Nay') GROUP BY v.is_passage""", (member_id, member_id))
    by = {r["passage"]: (r["n"], r["agree"]) for r in rows}

    def pack(n, agree):
        return {"n": n, "agree": agree, "pct": round(100 * agree / n, 1) if n else None}
    return {"all": pack(sum(n for n, _ in by.values()), sum(a for _, a in by.values())),
            "passage": pack(*by.get(1, (0, 0)))}


def participation(member_id):
    """Share of roll calls the member actually voted on (Yea/Nay/Present) out of every roll call listing them."""
    r = q("SELECT COUNT(*) AS total, SUM(position IN ('Yea', 'Nay', 'Present')) AS cast FROM member_votes WHERE member_id = ?",
          (member_id,))[0]
    total, cast = r["total"], r["cast"] or 0
    return {"total": total, "cast": cast, "missed": total - cast, "pct": round(100 * cast / total, 1) if total else None}


@app.route("/politician/<member_id>")
def politician(member_id):
    """Summary page: who they are + aggregate voting stats. Deliberately lists no individual votes (see /member/<id>)."""
    rows = q("SELECT * FROM members WHERE member_id = ?", (member_id,))
    if not rows:
        abort(404)
    m = rows[0]
    conn = db.connect()
    try:
        bio = bios.row_to_bio(conn.execute("SELECT * FROM member_bios WHERE member_id = ?", (member_id,)).fetchone())
        wiki = bios.get_wiki(conn, member_id) if bio else None          # network only on first view; cached 30 days
    finally:
        conn.close()
    today = date.today()
    cmp_party = compare_party(m)
    span = q("SELECT MIN(v.vote_date) AS lo, MAX(v.vote_date) AS hi FROM member_votes mv JOIN votes v USING (vote_id) WHERE mv.member_id = ?",
             (member_id,))[0]
    return render_template(
        "politician.html", m=m, bio=bio, wiki=wiki, facts=bios.build_facts(dict(m), bio, wiki, today),
        links=bios.profile_links(bio["ids"], bio["social"], bio["terms"]) if bio else [],
        party=party_alignment(member_id, cmp_party), cmp_letter=cmp_party.strip("'") if cmp_party else None,
        part=participation(member_id), picks=picks_alignment(member_id), span=span)


@app.template_global()
def photo_url(member_id):
    """Official portrait URL for a bioguide id (None for members we only know by Senate LIS id)."""
    return None if member_id.startswith("LIS-") else config.PHOTO_URL.format(bioguide=member_id)


def vote_matches(stance, position, is_passage):
    """+1 / -1 / 0: does this member's vote agree with the user's pick? Same rule as MATCH_SQL (tested together)."""
    if not (stance and is_passage and position in ("Yea", "Nay")):
        return 0
    return 1 if (stance == "agree") == (position == "Yea") else -1


def picks_alignment(member_id):
    """How often this member's votes agree with the user's green/red picks, ignoring page filters.

    One data point per rated bill: the member's most recent Yea/Nay vote on the measure itself (passage).
    Rated bills with no such vote (other chamber, voice vote, procedural only, Present/absent) are not scored."""
    rated = stances()
    rows = q("SELECT v.congress, v.bill_id, mv.position, v.is_passage FROM member_votes mv JOIN votes v USING (vote_id) "
             "WHERE mv.member_id = ? AND v.is_passage = 1 AND mv.position IN ('Yea', 'Nay') "
             "AND stance(v.congress, v.bill_id) IS NOT NULL ORDER BY v.vote_date DESC, v.vote_id DESC", (member_id,))
    latest = {}
    for r in rows:
        latest.setdefault((r["congress"], r["bill_id"]), r)
    matched = sum(1 for k, r in latest.items() if vote_matches(rated[k], r["position"], r["is_passage"]) == 1)
    scored = len(latest)
    return {"rated": len(rated), "scored": scored, "matched": matched,
            "pct": round(100 * matched / scored, 1) if scored else None}


# ======================================================================================================
# Videos tab: per-politician short-video brief (facts blurb + the user's script + a vertical slide presenter)
# ======================================================================================================

PARTY_ADJ = {"D": "Democratic", "R": "Republican", "I": "Independent"}
PARTY_PLURAL = {"D": "Democrats", "R": "Republicans", "I": "independents"}
MAX_TEXT = {"blurb": 4000, "script": 6000, "hook": 200, "outro": 200}


def member_vote_rows(member_id):
    """The member's latest Yea/Nay on each bill's passage vote, newest first, with a slide-sized title and a tally."""
    rows = q("SELECT v.congress, v.bill_id, v.vote_id, v.vote_date, v.yeas, v.nays, mv.position, v.title AS vote_title, "
             "b.title AS bill_title, b.policy_area AS topic, b.brief, COALESCE(b.became_law, 0) AS became_law "
             "FROM member_votes mv JOIN votes v USING (vote_id) LEFT JOIN bills b ON b.congress = v.congress AND b.bill_id = v.bill_id "
             "WHERE mv.member_id = ? AND v.is_passage = 1 AND v.bill_id IS NOT NULL AND mv.position IN ('Yea', 'Nay') "
             "ORDER BY v.vote_date DESC, v.vote_id DESC", (member_id,))
    votes = video.dedupe_latest([dict(r) for r in rows])
    for v in votes:
        v["title"] = video.short_title(v["bill_title"], v["vote_title"], limit=64) or v["bill_id"]   # fits a 3-line slide card
        v["tally"] = f"{v['yeas']}–{v['nays']}"
        v["key"] = f"{v['congress']}|{v['bill_id']}"
    return votes


def _short_date(iso):
    """'2026-09-28' -> 'Sep 28, 2026' (None/'' -> '')."""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %-d, %Y") if iso else ""


def get_brief(member_id):
    row = q("SELECT * FROM video_briefs WHERE member_id = ?", (member_id,))
    return dict(row[0]) if row else None


def _keys(raw):
    """JSON column -> list of [congress, bill_id] or None (None = never customised -> automatic picks)."""
    return json.loads(raw) if raw else None


def video_facts(member, brief=None, for_keys=None, against_keys=None):
    """Everything the blurb, outline, editor and slides need, as one dict. `for_keys`/`against_keys` override the saved picks (draft preview)."""
    mid, today = member["member_id"], date.today()
    conn = db.connect()
    try:
        bio = bios.row_to_bio(conn.execute("SELECT * FROM member_bios WHERE member_id = ?", (mid,)).fetchone())
    finally:
        conn.close()
    bf = bios.build_facts(dict(member), bio, None, today)
    spans = bios.term_spans(bio["terms"]) if bio else []
    votes = member_vote_rows(mid)
    scores = {(r["congress"], r["bill_id"]): r["score"] for r in leaderboard_rows()}
    cand = {"Yea": video.pick_candidates(votes, "Yea", scores), "Nay": video.pick_candidates(votes, "Nay", scores)}
    saved = {"Yea": for_keys if for_keys is not None else _keys((brief or {}).get("for_json")),
             "Nay": against_keys if against_keys is not None else _keys((brief or {}).get("against_json"))}
    chosen = {pos: video.resolve_selection(saved[pos], votes, pos, cand[pos]) for pos in ("Yea", "Nay")}
    for pos in cand:                                        # a featured bill always appears in its picker, even if it fell out of the top 8
        have = {v["key"] for v in cand[pos]}
        cand[pos] = cand[pos] + [v for v in chosen[pos] if v["key"] not in have]
    cmp_party = compare_party(member)
    cmp_letter = cmp_party.strip("'") if cmp_party else member["party"]
    pa = party_alignment(mid, cmp_party)["all"]
    data_through = max((v["vote_date"] for v in votes), default=None)
    first, last = video.first_last_name(member["full_name"])
    office = re.sub(r"^(Junior|Senior) ", "", bf["office"]) if bf["office"] else None
    return {
        "member_id": mid, "name": member["full_name"], "first": first, "last": last, "party": member["party"],
        "party_adj": PARTY_ADJ.get(member["party"], member["party"] or ""), "chamber": member["chamber"], "state": member["state"],
        "office": office, "current": bool(bf["office"]), "since_year": int(spans[0]["start"][:4]) if spans else None,
        "left_year": int(spans[-1]["end"][:4]) if spans and not bf["office"] else None, "service": bf["service"],
        "party_pct": pa["pct"], "party_n": pa["n"], "party_cmp": PARTY_PLURAL.get(cmp_letter, "party"),
        "participation": participation(mid)["pct"], "photo": photo_url(mid),
        "for_bills": chosen["Yea"], "against_bills": chosen["Nay"], "for_candidates": cand["Yea"], "against_candidates": cand["Nay"],
        **video.topic_leanings(votes), "n_votes": len(votes), "data_through": data_through, "data_through_text": _short_date(data_through),
    }


def _member_or_404(member_id):
    rows = q("SELECT * FROM members WHERE member_id = ?", (member_id,))
    if not rows:
        abort(404)
    return rows[0]


@app.errorhandler(400)
def bad_request(e):
    """The Videos editor talks JSON: give it the reason as {"error": ...} instead of an HTML error page."""
    if request.method == "POST" and request.path.startswith("/videos/"):
        return jsonify(error=e.description), 400
    return e


@app.route("/videos")
def videos():
    """Videos tab: start a brief for any politician (search) and see the briefs already started."""
    briefs = q("SELECT b.member_id, b.script, b.updated_at, m.full_name, m.party, m.chamber, m.state FROM video_briefs b "
               "JOIN members m USING (member_id) ORDER BY b.updated_at DESC")
    rows = [{**dict(r), "words": video.word_count(r["script"]), "seconds": video.estimate_seconds(r["script"])} for r in briefs]
    return render_template("videos.html", briefs=rows, target=video.TARGET_SECONDS)


@app.route("/videos/<member_id>")
def video_edit(member_id):
    member = _member_or_404(member_id)
    brief = get_brief(member_id)
    f = video_facts(member, brief)
    blurb = brief["blurb"] if brief and brief["blurb"] is not None else video.build_blurb(f)
    script = (brief or {}).get("script") or ""
    scene_list = video.scenes(f, (brief or {}).get("hook"), (brief or {}).get("outro"))
    return render_template("video_edit.html", m=member, f=f, brief=brief, blurb=blurb, script=script,
                           hook=(brief or {}).get("hook") or "", outro=(brief or {}).get("outro") or "",
                           scenes=scene_list, scene_total=video.total_seconds(scene_list), target=video.TARGET_SECONDS,
                           wps=video.WORDS_PER_SECOND, max_picks=video.MAX_PICKS, limits=MAX_TEXT,
                           for_keys={v["key"] for v in f["for_bills"]}, against_keys={v["key"] for v in f["against_bills"]})


def _parse_keys(raw, position_bills):
    """Validate a posted [[congress, bill_id], ...] against the member's own votes; returns the list or aborts with 400."""
    if not isinstance(raw, list) or len(raw) > video.MAX_PICKS:
        abort(400, f"pick at most {video.MAX_PICKS} bills per side")
    valid = {(v["congress"], v["bill_id"]) for v in position_bills}
    out = []
    for item in raw:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int) and isinstance(item[1], str)):
            abort(400, "bad bill reference")
        if tuple(item) not in valid:
            abort(400, f"{item[1]} is not a bill this politician voted that way on")
        out.append([item[0], item[1]])
    return out


@app.post("/videos/<member_id>/save")
def video_save(member_id):
    """Save the brief. Same custom-header guard as the other POST routes (blocks cross-site form posts)."""
    if request.headers.get("X-Politracker") != "1":
        abort(403)
    member = _member_or_404(member_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object")
    texts = {}
    for field, limit in MAX_TEXT.items():
        val = data.get(field)
        if val is not None and (not isinstance(val, str) or len(val) > limit):
            abort(400, f"{field} must be text of at most {limit} characters")
        texts[field] = val if val is None or val.strip() else None          # blank = back to the default
    votes = member_vote_rows(member_id)
    for_keys = _parse_keys(data.get("for", []), [v for v in votes if v["position"] == "Yea"])
    against_keys = _parse_keys(data.get("against", []), [v for v in votes if v["position"] == "Nay"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = db.connect()
    try:
        conn.execute("INSERT INTO video_briefs (member_id, blurb, script, hook, outro, for_json, against_json, updated_at) VALUES (?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(member_id) DO UPDATE SET blurb=excluded.blurb, script=excluded.script, hook=excluded.hook, outro=excluded.outro, "
                     "for_json=excluded.for_json, against_json=excluded.against_json, updated_at=excluded.updated_at",
                     (member["member_id"], texts["blurb"], texts["script"], texts["hook"], texts["outro"],
                      json.dumps(for_keys), json.dumps(against_keys), now))
        conn.commit()
    finally:
        conn.close()
    return jsonify(saved=True, updated_at=now, seconds=video.estimate_seconds(texts["script"]), words=video.word_count(texts["script"]))


@app.post("/videos/<member_id>/draft")
def video_draft(member_id):
    """Rebuild the auto blurb + starter outline for a candidate selection of bills (nothing is saved)."""
    if request.headers.get("X-Politracker") != "1":
        abort(403)
    member = _member_or_404(member_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object")
    votes = member_vote_rows(member_id)
    f = video_facts(member, None,
                    _parse_keys(data.get("for", []), [v for v in votes if v["position"] == "Yea"]),
                    _parse_keys(data.get("against", []), [v for v in votes if v["position"] == "Nay"]))
    return jsonify(blurb=video.build_blurb(f), outline=video.build_outline(f))


@app.route("/videos/<member_id>/present")
def video_present(member_id):
    """The vertical (9:16) slide presenter: open it and screen-record, or export the slides as images."""
    member = _member_or_404(member_id)
    brief = get_brief(member_id)
    f = video_facts(member, brief)
    scene_list = video.scenes(f, (brief or {}).get("hook"), (brief or {}).get("outro"))
    start = request.args.get("scene", "1")
    return render_template("video_present.html", f=f, scenes=scene_list, total=video.total_seconds(scene_list),
                           clean=request.args.get("clean") == "1", freeze=request.args.get("freeze") == "1",
                           auto=request.args.get("auto") != "0", loop=request.args.get("loop") == "1",
                           start=int(start) if start.isdigit() else 1)


@app.route("/videos/<member_id>/frames.zip")
def video_frames(member_id):
    """Download every slide as a 1080x1920 PNG (needs headless Chromium/Chrome on this machine)."""
    member = _member_or_404(member_id)
    browser = frames.find_browser()
    if not browser:
        return jsonify(error="No Chromium or Chrome found on this machine, so slide images can't be exported. "
                             "Open the presenter and screen-record it instead."), 501
    brief = get_brief(member_id)
    count = len(video.scenes(video_facts(member, brief), (brief or {}).get("hook"), (brief or {}).get("outro")))
    url = f"http://127.0.0.1:{request.environ.get('SERVER_PORT', '5057')}/videos/{member['member_id']}/present"   # never built from the Host header
    root = config.DATA_DIR / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    work = tempfile.mkdtemp(prefix="frames-", dir=root)               # inside the project: snap-packaged Chromium can't write to /tmp
    try:
        paths = frames.render_frames(browser, url, count, work)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                z.write(p, f"{member['member_id']}-{p.name}")
    except RuntimeError as e:
        return jsonify(error=f"Could not render the slides ({e})."), 502
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{member["member_id"]}-slides.zip"'})
