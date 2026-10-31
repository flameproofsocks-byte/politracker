"""Politician profile facts: age, terms/time in Congress, social links, and Wikipedia/Wikidata basics.

Two sources:
  * unitedstates/congress-legislators JSON (bulk; loaded into `member_bios` by ingest, refreshed weekly)
  * Wikipedia REST summary + Wikidata claims (fetched on demand the first time a profile is opened, then cached 30 days)

Everything that computes something (age, spans, durations, links, parsing) is a pure function so tests can feed it
synthetic data; network helpers are small and swallow failures so a profile page always renders.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import config, fetch

WIKI_MAX_AGE_DAYS = 30
WIKI_TIMEOUT = 8
UA = config.USER_AGENT
CONGRESS_TYPES = {"rep": "House", "sen": "Senate"}      # other term types in the data (prez, viceprez) are not Congress service
PARTY_LETTER = {"Democrat": "D", "Republican": "R", "Independent": "I"}
WIKIDATA_PROPS = {"P19": "birth_place", "P69": "education", "P106": "occupations"}


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}


# ---------------------------------------------------------------- pure helpers

def fmt_date(iso):
    """'2025-01-03' -> 'January 3, 2025' ('' for None)."""
    d = _d(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}" if d else ""


def build_facts(member, bio, wiki, today):
    """Everything the profile page shows besides vote statistics, as plain data.

    member: {"party","state","chamber"}; bio: row_to_bio() dict or None; wiki: cached Wikipedia/Wikidata dict or None."""
    bio = bio or {"terms": [], "birthday": None, "social": {}, "ids": {}}
    wiki = wiki or {}
    terms = bio["terms"]
    spans = term_spans(terms)
    cur = current_term(terms, today)
    last = spans[-1] if spans else None
    facts = {"born": fmt_date(bio.get("birthday")), "died": fmt_date(wiki.get("died")), "age": None, "age_at_death": None,
             "birth_place": wiki.get("birth_place"), "education": wiki.get("education") or [], "occupations": wiki.get("occupations") or [],
             "office": None, "since": None, "term_end": None, "service": None, "timeline": [], "left_office": None}
    if wiki.get("died"):
        facts["age_at_death"] = age_on(bio.get("birthday"), _d(wiki["died"]))
    elif cur and bio.get("birthday"):
        facts["age"] = age_on(bio["birthday"], today)         # only for sitting members: we can't know whether a former member is alive
    if cur:
        state = STATE_NAMES.get(cur.get("state"), cur.get("state"))
        if cur["type"] == "sen":
            rank = {"junior": "Junior ", "senior": "Senior "}.get(cur.get("state_rank"), "")
            facts["office"] = f"{rank}U.S. Senator from {state}"
        else:
            d = cur.get("district")
            facts["office"] = f"U.S. Representative for {state}" + (f"'s {d}{_ordinal_suffix(d)} district" if d not in (None, 0) else " (at-large)")
        facts["since"] = fmt_date(last["start"]) if last else None
        facts["term_end"] = fmt_date(cur["end"])
    elif last:
        facts["left_office"] = fmt_date(last["end"])
    if terms:
        facts["service"] = human_duration(service_days(terms, today))
    for sp in spans:
        end = "present" if _d(sp["end"]) >= today else str(_d(sp["end"]).year)
        where = sp["state"] + (f"-{sp['district']}" if sp["chamber"] == "House" and sp["district"] not in (None, 0) else "")
        facts["timeline"].append({"chamber": sp["chamber"], "where": where, "party": sp["party"],
                                  "years": f"{_d(sp['start']).year}\u2013{end}"})
    party = {"D": "Democratic Party", "R": "Republican Party", "I": "Independent"}.get(member.get("party"), member.get("party"))
    letter = caucus_letter(terms)
    facts["party"] = party + (f" (caucuses with the {'Democrats' if letter == 'D' else 'Republicans'})" if letter else "")
    return facts


def _ordinal_suffix(n):
    n = int(n)
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

def _d(iso):
    return date.fromisoformat(iso) if iso else None


def age_on(birthday, today):
    """Whole years between ISO `birthday` and `today` (a date); None if unknown."""
    b = _d(birthday)
    if not b:
        return None
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def parse_legislator(entry):
    """One element of legislators-*.json -> the fields we store (terms trimmed to what the UI uses)."""
    ids = entry.get("id", {})
    terms = []
    for t in entry.get("terms", []):
        if t.get("type") not in CONGRESS_TYPES:
            continue
        terms.append({k: t.get(k) for k in ("type", "start", "end", "state", "district", "party", "caucus", "class",
                                              "state_rank", "url", "phone", "address", "contact_form", "office") if t.get(k) is not None})
    keep = ("bioguide", "govtrack", "opensecrets", "ballotpedia", "cspan", "votesmart", "wikipedia", "wikidata")
    return {"member_id": ids.get("bioguide"), "birthday": entry.get("bio", {}).get("birthday"),
            "gender": entry.get("bio", {}).get("gender"), "terms": terms,
            "ids": {k: ids[k] for k in keep if ids.get(k) is not None},
            "wikipedia_title": ids.get("wikipedia"), "wikidata_id": ids.get("wikidata")}


def term_spans(terms, gap_days=45):
    """Merge consecutive terms in the same office/party into spans, oldest first.

    -> [{"chamber","state","district","party","start","end"}]. A new span starts when the chamber, state,
    House district or party changes, or when there is a break longer than `gap_days` between terms."""
    spans = []
    for t in sorted(terms, key=lambda t: t["start"]):
        chamber = CONGRESS_TYPES.get(t.get("type"))
        if not chamber:
            continue
        key = (chamber, t.get("state"), t.get("district") if chamber == "House" else None, t.get("party"))
        last = spans[-1] if spans else None
        if last and last["_key"] == key and (_d(t["start"]) - _d(last["end"])).days <= gap_days:
            last["end"] = max(last["end"], t["end"])
        else:
            spans.append({"_key": key, "chamber": chamber, "state": t.get("state"), "district": key[2],
                          "party": t.get("party"), "start": t["start"], "end": t["end"]})
    for s in spans:
        s.pop("_key")
    return spans


def service_days(terms, today):
    """Days in Congress: union of all term intervals, each capped at `today` (future term ends don't count)."""
    iv = sorted((_d(t["start"]), min(_d(t["end"]), today)) for t in terms if t.get("type") in CONGRESS_TYPES)
    total, cur_s, cur_e = 0, None, None
    for s, e in iv:
        if e < s:
            continue                                   # term hasn't started yet
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).days
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).days
    return total


def human_duration(days):
    """'25 years, 9 months' (approximate: 365.2425-day years); '< 1 month' for tiny values."""
    years = int(days / 365.2425)
    months = int((days - years * 365.2425) / 30.44)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    return ", ".join(parts) or "< 1 month"


def caucus_letter(terms):
    """For independents: the party letter they caucus with in their latest term ('D'/'R'), else None."""
    if not terms:
        return None
    last = max(terms, key=lambda t: t["start"])
    if last.get("party") == "Independent":
        return PARTY_LETTER.get(last.get("caucus"))
    return None


def current_term(terms, today):
    """Latest term if it is still running on `today` (members hold office through the day their term ends), else None."""
    if not terms:
        return None
    last = max(terms, key=lambda t: t["start"])
    return last if _d(last["start"]) <= today <= _d(last["end"]) else None


def _q(s):
    return urllib.parse.quote(str(s).replace(" ", "_"), safe="_()'.,-")


def profile_links(ids, social, terms):
    """[(group, label, url)] built from IDs/handles. Group 'social' or 'reference'. All URLs are https and derived, never raw input."""
    out = []
    if social.get("twitter"):
        out.append(("social", "X / Twitter", f"https://x.com/{urllib.parse.quote(social['twitter'])}"))
    if social.get("facebook"):
        out.append(("social", "Facebook", f"https://www.facebook.com/{urllib.parse.quote(social['facebook'])}"))
    if social.get("youtube_id"):
        out.append(("social", "YouTube", f"https://www.youtube.com/channel/{urllib.parse.quote(social['youtube_id'])}"))
    elif social.get("youtube"):
        out.append(("social", "YouTube", f"https://www.youtube.com/user/{urllib.parse.quote(social['youtube'])}"))
    if social.get("instagram"):
        out.append(("social", "Instagram", f"https://www.instagram.com/{urllib.parse.quote(social['instagram'])}"))
    site = next((t["url"] for t in sorted(terms, key=lambda t: t["start"], reverse=True) if t.get("url")), None)
    if site and site.startswith(("http://", "https://")):
        out.append(("reference", "Official website", site))
    if ids.get("wikipedia"):
        out.append(("reference", "Wikipedia", f"https://en.wikipedia.org/wiki/{_q(ids['wikipedia'])}"))
    if ids.get("ballotpedia"):
        out.append(("reference", "Ballotpedia", f"https://ballotpedia.org/{_q(ids['ballotpedia'])}"))
    if ids.get("bioguide"):
        out.append(("reference", "Congress.gov", f"https://www.congress.gov/member/-/{urllib.parse.quote(ids['bioguide'])}"))
    if ids.get("govtrack"):
        out.append(("reference", "GovTrack", f"https://www.govtrack.us/congress/members/{ids['govtrack']}"))
    if ids.get("opensecrets"):
        out.append(("reference", "OpenSecrets", f"https://www.opensecrets.org/members-of-congress/summary?cid={urllib.parse.quote(ids['opensecrets'])}"))
    if ids.get("cspan"):
        out.append(("reference", "C-SPAN", f"https://www.c-span.org/person/?{ids['cspan']}"))
    return out


def parse_wikidata(entity, labels):
    """Wikidata entity JSON + {QID: label} -> {"birth_place", "education": [...], "occupations": [...], "died": ISO|None}."""
    claims = entity.get("claims", {})

    def ids(prop):
        return [c["mainsnak"]["datavalue"]["value"]["id"] for c in claims.get(prop, [])
                if c.get("mainsnak", {}).get("datavalue", {}).get("type") == "wikibase-entityid"]
    out = {"birth_place": None, "education": [], "occupations": [], "died": None}
    place = [labels[i] for i in ids("P19") if i in labels]
    out["birth_place"] = place[0] if place else None
    out["education"] = [labels[i] for i in ids("P69") if i in labels]
    out["occupations"] = [labels[i] for i in ids("P106") if i in labels and labels[i].lower() != "politician"]
    for c in claims.get("P570", []):                    # date of death: '+2025-03-05T00:00:00Z' (precision may be year-only)
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if v.get("time"):
            out["died"] = v["time"].lstrip("+")[:10]
            break
    return out


def wikidata_label_ids(entity):
    claims = entity.get("claims", {})
    return [c["mainsnak"]["datavalue"]["value"]["id"] for p in WIKIDATA_PROPS for c in claims.get(p, [])
            if c.get("mainsnak", {}).get("datavalue", {}).get("type") == "wikibase-entityid"]


# ---------------------------------------------------------------- bulk load (ingest)

def _cached_json(name, url, max_age_days):
    """JSON from data/raw/<name>, re-downloaded when older than `max_age_days`; falls back to the stale copy if offline."""
    path = config.RAW_DIR / name
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400
    if not fresh:
        text = fetch.get(url)                           # no cache_name: we manage the file so a failed refresh keeps the old copy
        if text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_into_db(conn, force=False):
    """Fill member_bios for every member in the `members` table. Returns the number of rows written."""
    wanted = {r[0] for r in conn.execute("SELECT member_id FROM members")}
    if not wanted:
        return 0
    age = 0 if force else config.BIOS_MAX_AGE_DAYS
    cur = _cached_json("legislators-current.json", config.LEGISLATORS_JSON["current"], age) or []
    parsed = {}
    for e in cur:
        p = parse_legislator(e)
        if p["member_id"] in wanted:
            parsed[p["member_id"]] = p
    if wanted - parsed.keys():                          # people who left office (or the big historical file is needed)
        for e in _cached_json("legislators-historical.json", config.LEGISLATORS_JSON["historical"], 30) or []:
            bio = e.get("id", {}).get("bioguide")
            if bio in wanted and bio not in parsed:
                parsed[bio] = parse_legislator(e)
    social = {}
    for e in _cached_json("legislators-social-media.json", config.LEGISLATORS_JSON["social"], age) or []:
        if e.get("id", {}).get("bioguide"):
            social[e["id"]["bioguide"]] = e.get("social", {})
    for mid, p in parsed.items():
        conn.execute(
            "INSERT INTO member_bios (member_id, birthday, gender, terms_json, social_json, ids_json, wikipedia_title, wikidata_id) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(member_id) DO UPDATE SET birthday=excluded.birthday, gender=excluded.gender, "
            "terms_json=excluded.terms_json, social_json=excluded.social_json, ids_json=excluded.ids_json, "
            "wikipedia_title=excluded.wikipedia_title, wikidata_id=excluded.wikidata_id",
            (mid, p["birthday"], p["gender"], json.dumps(p["terms"]), json.dumps(social.get(mid, {})),
             json.dumps(p["ids"]), p["wikipedia_title"], p["wikidata_id"]))
    conn.commit()
    return len(parsed)


def row_to_bio(row):
    """member_bios sqlite row -> plain dict with JSON columns decoded (None if no row)."""
    if row is None:
        return None
    d = dict(row)
    for col in ("terms", "social", "ids", "wiki"):
        raw = d.pop(f"{col}_json", None)
        d[col] = json.loads(raw) if raw else ({} if col != "terms" else [])
    return d


# ---------------------------------------------------------------- on-demand Wikipedia / Wikidata

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=WIKI_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_wiki(title, qid):
    """-> {"extract", "description", "url", "birth_place", "education", "occupations", "died"} (missing parts empty).
    Raises on network errors; get_wiki() is the tolerant caller."""
    out = {"extract": None, "description": None, "url": None, "birth_place": None, "education": [], "occupations": [], "died": None}
    if title:
        s = _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"), safe="_()'.,-"))
        out["extract"], out["description"] = s.get("extract"), s.get("description")
        out["url"] = s.get("content_urls", {}).get("desktop", {}).get("page")
    if qid:
        ent = _get_json(f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={urllib.parse.quote(qid)}&props=claims&format=json")
        entity = ent.get("entities", {}).get(qid, {})
        want = sorted(set(wikidata_label_ids(entity)))
        labels = {}
        if want:
            lab = _get_json("https://www.wikidata.org/w/api.php?action=wbgetentities&ids=" + urllib.parse.quote("|".join(want[:50]))
                            + "&props=labels&languages=en&format=json")
            labels = {k: v["labels"]["en"]["value"] for k, v in lab.get("entities", {}).items() if "en" in v.get("labels", {})}
        out.update(parse_wikidata(entity, labels))
    return out


def get_wiki(conn, member_id, today=None, force=False):
    """Cached Wikipedia/Wikidata basics for a member, fetching (and caching) when missing or older than WIKI_MAX_AGE_DAYS.
    Never raises: offline/blocked -> returns whatever is cached (maybe stale) or None."""
    today = today or date.today()
    row = conn.execute("SELECT wikipedia_title, wikidata_id, wiki_json, wiki_fetched FROM member_bios WHERE member_id = ?", (member_id,)).fetchone()
    if row is None or not (row["wikipedia_title"] or row["wikidata_id"]):
        return None
    cached = json.loads(row["wiki_json"]) if row["wiki_json"] else None
    fresh = row["wiki_fetched"] and (today - _d(row["wiki_fetched"])) < timedelta(days=WIKI_MAX_AGE_DAYS)
    if cached and fresh and not force:
        return cached
    try:
        data = fetch_wiki(row["wikipedia_title"], row["wikidata_id"])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return cached
    conn.execute("UPDATE member_bios SET wiki_json = ?, wiki_fetched = ? WHERE member_id = ?",
                 (json.dumps(data), today.isoformat(), member_id))
    conn.commit()
    return data
