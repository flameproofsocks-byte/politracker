"""Congress.gov API client: bill details (policy area, sponsor, law status) for voted-on bills.

Needs a free API key (https://api.congress.gov/sign-up/). Provide it via env CONGRESS_API_KEY or
data/secrets.json {"congress_api_key": "..."}. Without a key, `run()` prints how to get one and
returns -- the rest of the app works fine without bill details.
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, db

API = "https://api.congress.gov/v3/bill/{congress}/{type}/{number}?format=json"
TYPE_SLUGS = {  # our bill_id prefix -> (API type, congress.gov URL slug)
    "HR": ("hr", "house-bill"), "S": ("s", "senate-bill"),
    "HJRES": ("hjres", "house-joint-resolution"), "SJRES": ("sjres", "senate-joint-resolution"),
    "HRES": ("hres", "house-resolution"), "SRES": ("sres", "senate-resolution"),
    "HCONRES": ("hconres", "house-concurrent-resolution"), "SCONRES": ("sconres", "senate-concurrent-resolution"),
}
DELAY = 0.8   # seconds between calls; a personal key allows 5,000/hour (~0.72s apart)


def get_key():
    if os.environ.get("CONGRESS_API_KEY"):
        return os.environ["CONGRESS_API_KEY"]
    secrets = config.DATA_DIR / "secrets.json"
    if secrets.exists():
        return json.loads(secrets.read_text()).get("congress_api_key")
    return None


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def bill_url(congress, bill_id):
    prefix, num = bill_id.split()
    return f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/{TYPE_SLUGS[prefix][1]}/{num}"


def parse_bill(payload, congress, bill_id):
    """Congress.gov /bill JSON (already json.loads'd) -> bills row dict. Pure; unit-tested."""
    b = payload["bill"]
    sponsor = (b.get("sponsors") or [{}])[0]
    action = b.get("latestAction") or {}
    laws = b.get("laws") or []
    return {
        "congress": congress, "bill_id": bill_id, "title": b.get("title"),
        "policy_area": (b.get("policyArea") or {}).get("name"),
        "sponsor_id": sponsor.get("bioguideId"), "sponsor_name": sponsor.get("fullName"),
        "sponsor_party": sponsor.get("party"),
        "introduced_date": b.get("introducedDate"),
        "latest_action_date": action.get("actionDate"), "latest_action_text": action.get("text"),
        "became_law": int(bool(laws)), "law_number": laws[0].get("number") if laws else None,
        "status": "ok", "url": bill_url(congress, bill_id),
        **bill_counts(b),
    }


def bill_counts(bill):
    """Attention signals from a Congress.gov /bill payload's `bill` dict: how many cosponsors, amendments and actions it has.
    Each is a {"count": N, ...} object (or absent when there are none)."""
    def n(key):
        v = bill.get(key)
        return int(v.get("count") or 0) if isinstance(v, dict) else 0
    return {"cosponsor_count": n("cosponsors"), "amendment_count": n("amendments"), "action_count": n("actions")}


class RateLimited(Exception):
    pass


def fetch_bill(congress, bill_id, key):
    """Return parsed JSON, or None if Congress.gov says the bill doesn't exist. Caches raw JSON."""
    prefix, num = bill_id.split()
    api_type = TYPE_SLUGS[prefix][0]
    cache = config.RAW_DIR / f"congress/{congress}/{api_type}/{num}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    req = urllib.request.Request(
        API.format(congress=congress, type=api_type, number=num),
        headers={"User-Agent": config.USER_AGENT, "X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            raise RateLimited()
        raise
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    time.sleep(DELAY)
    return json.loads(text)


def run(limit=None):
    key = get_key()
    if not key:
        print("bills: no Congress.gov API key; skipping. Get a free one at https://api.congress.gov/sign-up/ "
              "then set CONGRESS_API_KEY or put {\"congress_api_key\": \"...\"} in data/secrets.json")
        return
    conn = db.connect()
    # Bills with passage votes first, newest first, so a partial run (or rate limit) is still useful.
    todo = conn.execute("""
        SELECT v.congress, v.bill_id, MAX(v.is_passage) p, MAX(v.vote_date) d FROM votes v
        WHERE v.bill_id IS NOT NULL AND v.congress IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM bills b WHERE b.congress=v.congress AND b.bill_id=v.bill_id)
        GROUP BY v.congress, v.bill_id ORDER BY p DESC, d DESC""").fetchall()
    if limit:
        todo = todo[:limit]
    print(f"bills: {len(todo)} to fetch")
    done = 0
    cols = ["congress", "bill_id", "title", "policy_area", "sponsor_id", "sponsor_name", "sponsor_party",
            "introduced_date", "latest_action_date", "latest_action_text", "became_law", "law_number",
            "status", "url", "cosponsor_count", "amendment_count", "action_count"]
    for row in todo:
        congress, bill_id = row["congress"], row["bill_id"]
        try:
            payload = fetch_bill(congress, bill_id, key)
        except RateLimited:
            print("bills: rate limited (HTTP 429); stopping. Re-run later, it resumes where it left off.")
            break
        except urllib.error.URLError as e:
            print(f"bills: network error on {bill_id}: {e}; stopping.")
            break
        if payload is None:
            rec = {c: None for c in cols} | {"congress": congress, "bill_id": bill_id,
                                             "became_law": 0, "status": "not_found"}
        else:
            rec = parse_bill(payload, congress, bill_id)
        conn.execute(f"INSERT OR REPLACE INTO bills ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [rec[c] for c in cols])
        conn.commit()
        done += 1
    print(f"bills: stored {done}")


# ---- Source text for the "process summary" feature --------------------------------------------

def html_to_text(raw):
    """Congress.gov summaries are HTML fragments; bill text is <pre>-wrapped GPO text. -> plain text."""
    raw = re.sub(r"(?i)</p>|</h\d>", "\n\n", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</li>|</div>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", raw))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _api_json(path, key):
    """GET https://api.congress.gov/v3/<path> -> dict ({} on 404). Not cached (small, rarely called)."""
    req = urllib.request.Request(f"https://api.congress.gov/v3/{path}{'&' if '?' in path else '?'}format=json",
                                 headers={"User-Agent": config.USER_AGENT, "X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def fetch_source_text(congress, bill_id, key):
    """Best available description of what a bill does: (label, text) or None.

    Prefers the CRS summary (human-written, authoritative; newest by updateDate), falls back to the
    newest version of the bill text. Brand-new bills often have neither summary nor text yet.
    """
    api_type = TYPE_SLUGS[bill_id.split()[0]][0]
    num = bill_id.split()[1]
    base = f"bill/{congress}/{api_type}/{num}"
    summaries = _api_json(f"{base}/summaries", key).get("summaries") or []
    if summaries:
        best = max(summaries, key=lambda s: s.get("updateDate") or "")
        text = html_to_text(best.get("text") or "")
        if len(text) > 200:
            return f"CRS summary ({best.get('actionDesc', 'latest')})", text
    for version in _api_json(f"{base}/text", key).get("textVersions") or []:   # newest first
        fmt = next((f for f in version.get("formats", []) if f.get("type") == "Formatted Text"), None)
        if fmt:
            req = urllib.request.Request(fmt["url"], headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return f"Bill text ({version.get('type', 'latest')})", html_to_text(resp.read().decode("utf-8", "replace"))
    return None


# ---- Brief one/two-sentence summaries for the Summary column ------------------------------------

BRIEF_MAX = 260
RECHECK_DAYS = 14        # bills with no CRS summary yet are looked at again after this long
_ABBREV = r"(?<!\bU\.S)(?<!\bU\.S\.C)(?<!\be\.g)(?<!\bi\.e)(?<!\bSec)(?<!\bNo)(?<!\bDept)(?<!\bInc)(?<!\bSt)"
_SENTENCE_END = re.compile(_ABBREV + r"\.\s+(?=[A-Z(])")


def _shorten(text, limit=BRIEF_MAX):
    """Whole sentences up to `limit` chars; if even the first sentence is longer, cut at a word with an ellipsis."""
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    if len(text) <= limit:
        return text
    cut = 0
    for m in _SENTENCE_END.finditer(text):
        if m.end() > limit:
            break
        cut = m.start() + 1
    if cut:
        return text[:cut]
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def brief_from_crs(summary_html):
    """Lead of a CRS summary: skip the title line (short, no final period) and keep the first sentence(s)."""
    paras = [re.sub(r"\s+", " ", p.replace("\xa0", " ")).strip() for p in html_to_text(summary_html).split("\n\n")]
    paras = [p for p in paras if p]
    if len(paras) > 1 and len(paras[0]) < 160 and not paras[0].endswith((".", ":", ";")):
        paras = paras[1:]
    return _shorten(paras[0]) if paras else None


def brief_from_ai(summary):
    """The 'WHAT IT DOES:' line of a Process summary (our own fixed format, see summarize.SYSTEM_PROMPT)."""
    m = re.search(r"WHAT IT DOES:\s*(.+?)(?:\n\s*\n|\nKEY PROVISIONS|$)", summary or "", re.S)
    return _shorten(m.group(1)) if m else None


def run_briefs(limit=None):
    """Fill bills.brief. AI briefs (from clicked Process summaries) win and are never overwritten; other bills get the
    lead of their newest CRS summary. Bills with no CRS summary yet are re-checked every RECHECK_DAYS days."""
    key = get_key()
    conn = db.connect()
    synced = 0
    for r in conn.execute("SELECT b.congress, b.bill_id, s.summary FROM bills b JOIN bill_summaries s "
                          "ON s.congress = b.congress AND s.bill_id = b.bill_id "
                          "WHERE COALESCE(b.brief_source, '') != 'ai'").fetchall():
        brief = brief_from_ai(r["summary"])
        if brief:
            conn.execute("UPDATE bills SET brief = ?, brief_source = 'ai' WHERE congress = ? AND bill_id = ?",
                         (brief, r["congress"], r["bill_id"]))
            synced += 1
    conn.commit()
    if not key:
        print("briefs: no Congress.gov API key; only AI summaries were synced." if synced else
              "briefs: no Congress.gov API key; skipping.")
        return
    from datetime import date, timedelta
    today = date.today()
    stale = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    todo = conn.execute("""
        SELECT b.congress, b.bill_id,
               (SELECT MAX(v.is_passage) FROM votes v WHERE v.congress = b.congress AND v.bill_id = b.bill_id) p
        FROM bills b WHERE b.status = 'ok' AND COALESCE(b.brief_source, '') != 'ai'
          AND (b.brief_checked IS NULL OR (b.brief IS NULL AND b.brief_checked < ?))
        ORDER BY p DESC, b.latest_action_date DESC""", (stale,)).fetchall()
    if limit:
        todo = todo[:limit]
    print(f"briefs: {len(todo)} bills to check")
    done = found = 0
    for row in todo:
        prefix, num = row["bill_id"].split()
        try:
            summaries = _api_json(f"bill/{row['congress']}/{TYPE_SLUGS[prefix][0]}/{num}/summaries", key).get("summaries") or []
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("briefs: rate limited (HTTP 429); stopping. Re-run later, it resumes where it left off.")
                break
            raise
        except urllib.error.URLError as e:
            print(f"briefs: network error on {row['bill_id']}: {e}; stopping.")
            break
        brief = None
        if summaries:
            best = max(summaries, key=lambda s: s.get("updateDate") or "")
            brief = brief_from_crs(best.get("text") or "")
        conn.execute("UPDATE bills SET brief = ?, brief_source = ?, brief_checked = ? WHERE congress = ? AND bill_id = ?",
                     (brief, "crs" if brief else None, today.isoformat(), row["congress"], row["bill_id"]))
        conn.commit()
        done += 1
        found += bool(brief)
        time.sleep(DELAY)
    print(f"briefs: checked {done}, {found} with a CRS summary" + (f"; synced {synced} AI briefs" if synced else ""))


def backfill_counts(conn=None):
    """Fill cosponsor/amendment/action counts for bills loaded before those columns existed, from the raw JSON cache
    (no network). Returns the number of bills updated."""
    own = conn is None
    conn = conn or db.connect()
    updated = 0
    for r in conn.execute("SELECT congress, bill_id FROM bills WHERE status = 'ok' AND cosponsor_count IS NULL").fetchall():
        prefix, num = r["bill_id"].split()
        path = config.RAW_DIR / f"congress/{r['congress']}/{TYPE_SLUGS[prefix][0]}/{num}.json"
        if not path.exists():
            continue
        try:
            counts = bill_counts(json.loads(path.read_text())["bill"])
        except (ValueError, KeyError):
            continue
        conn.execute("UPDATE bills SET cosponsor_count = ?, amendment_count = ?, action_count = ? WHERE congress = ? AND bill_id = ?",
                     (counts["cosponsor_count"], counts["amendment_count"], counts["action_count"], r["congress"], r["bill_id"]))
        updated += 1
    conn.commit()
    if own:
        conn.close()
    return updated
