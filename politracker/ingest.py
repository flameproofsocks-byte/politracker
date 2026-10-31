"""Download, parse and load votes into SQLite. Idempotent: safe to re-run any time."""
import csv
import io
from concurrent.futures import ThreadPoolExecutor

from . import bios, config, db, fetch, parse


def load_crosswalk(refresh=False):
    """Return ({lis_id: bioguide_id}, {bioguide_id: full_name}) from unitedstates/congress-legislators."""
    lis_to_bio, names = {}, {}
    for url in config.LEGISLATORS_URLS:
        name = url.rsplit("/", 1)[1]
        if refresh and (config.RAW_DIR / name).exists():
            (config.RAW_DIR / name).unlink()
        text = fetch.get(url, cache_name=name)
        if not text:
            print(f"  warning: could not load {url}; member names/IDs may be degraded")
            continue
        for row in csv.DictReader(io.StringIO(text)):
            bio = row["bioguide_id"]
            names[bio] = row["full_name"]
            if row.get("lis_id"):
                lis_to_bio[row["lis_id"]] = bio
    return lis_to_bio, names


def _house_jobs(year):
    """Yield House roll numbers 1.. in batches until a whole batch is missing."""
    num = 1
    while True:
        yield list(range(num, num + 50))
        num += 50


def _fetch_house(year, num):
    url = config.HOUSE_URL.format(year=year, num=num)
    text = fetch.get(url, cache_name=f"house/{year}/roll{num:03d}.xml",
                     validate=lambda t: "<rollcall-vote" in t)
    parsed = parse.parse_house_vote(text, year) if text else None
    if parsed:
        parsed["vote"]["source_url"] = url
    return parsed


def _fetch_senate(congress, session, num):
    url = config.SENATE_URL.format(congress=congress, session=session, num=num)
    text = fetch.get(url, cache_name=f"senate/{congress}_{session}/vote_{num:05d}.xml",
                     validate=lambda t: "<roll_call_vote" in t)
    parsed = parse.parse_senate_vote(text) if text else None
    if parsed:
        parsed["vote"]["source_url"] = url
    return parsed


def _store(conn, parsed, lis_to_bio, names):
    v = parsed["vote"]
    if config.MIN_DATE and v["vote_date"] < config.MIN_DATE:
        return False

    def resolve(key):
        kind, ident = key
        if kind == "bioguide":
            return ident
        return lis_to_bio.get(ident, f"LIS-{ident}")

    cols = ["vote_id", "chamber", "congress", "session", "roll_num", "vote_date", "question",
            "bill_id", "title", "result", "is_passage", "yeas", "nays", "present", "not_voting", "source_url"]
    conn.execute(f"INSERT OR REPLACE INTO votes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [v[c] for c in cols])
    for m in parsed["members"]:
        mid = resolve(m["member_key"])
        full = names.get(mid) or m["full_name"]
        conn.execute(
            "INSERT INTO members (member_id, full_name, party, state, chamber) VALUES (?,?,?,?,?) "
            "ON CONFLICT(member_id) DO UPDATE SET full_name=excluded.full_name, party=excluded.party, "
            "state=excluded.state, chamber=excluded.chamber",
            (mid, full, m["party"], m["state"], m["chamber"]))
    for c in parsed["cast"]:
        conn.execute("INSERT OR REPLACE INTO member_votes (vote_id, member_id, position, position_raw, party) "
                     "VALUES (?,?,?,?,?)",
                     (v["vote_id"], resolve(c["member_key"]), c["position"], c["position_raw"], c["party"]))
    return True


def run(refresh=False):
    conn = db.connect()
    lis_to_bio, names = load_crosswalk(refresh)
    print(f"crosswalk: {len(lis_to_bio)} Senate LIS ids, {len(names)} legislators")
    # Fully refresh only unfinished work: skip roll numbers whose vote is already in the DB.
    have = {r[0] for r in conn.execute("SELECT vote_id FROM votes")}
    stored = 0
    with ThreadPoolExecutor(config.WORKERS) as pool:
        for congress, session, year in config.SESSIONS:
            # ---- House (discover roll numbers by probing in batches)
            print(f"House {year}: ", end="", flush=True)
            n = 0
            for batch in _house_jobs(year):
                results = list(pool.map(lambda x: _fetch_house(year, x), batch))
                found = [r for r in results if r]
                for r in found:
                    if r["vote"]["vote_id"] not in have or refresh:
                        stored += _store(conn, r, lis_to_bio, names)
                n += len(found)
                conn.commit()
                if not found:
                    break
            print(f"{n} roll calls")
            # ---- Senate (menu file tells us the highest roll number)
            menu = fetch.get(config.SENATE_MENU_URL.format(congress=congress, session=session))
            top = parse.parse_senate_menu(menu) if menu else 0
            # Menu unreachable (e.g. rate-limited)? Still load whatever is already cached.
            cached = (config.RAW_DIR / f"senate/{congress}_{session}").glob("vote_*.xml")
            top = max([top] + [int(f.stem.split("_")[1]) for f in cached])
            print(f"Senate {congress}-{session}: ", end="", flush=True)
            nums = [x for x in range(1, top + 1)
                    if f"S-{congress}-{session}-{x:05d}" not in have or refresh]
            for r in pool.map(lambda x: _fetch_senate(congress, session, x), nums):
                if r:
                    stored += _store(conn, r, lis_to_bio, names)
            conn.commit()
            print(f"{top} roll calls")
    try:                                                 # profile facts (age, terms, social links); never blocks vote ingest
        print(f"bios: {bios.load_into_db(conn)} politician profiles loaded")
    except Exception as e:                               # noqa: BLE001 - network/parse problems here must not fail the run
        print(f"  warning: could not load politician bios ({e}); profile pages will lack age/terms until the next run")
    nv = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    nm = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    nc = conn.execute("SELECT COUNT(*) FROM member_votes").fetchone()[0]
    print(f"done. newly stored: {stored}. DB totals: {nv} votes, {nm} members, {nc} individual votes")
