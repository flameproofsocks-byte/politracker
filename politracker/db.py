"""SQLite schema + connection helper. See docs/schema.md."""
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY,   -- bioguide id (e.g. C000127); 'LIS-S428' if no crosswalk match
    full_name TEXT NOT NULL,
    party     TEXT,               -- latest seen party letter: D/R/I
    state     TEXT,
    chamber   TEXT                -- 'House' | 'Senate' (latest seen)
);
CREATE TABLE IF NOT EXISTS member_bios (   -- profile facts from unitedstates/congress-legislators + cached Wikipedia/Wikidata
    member_id   TEXT PRIMARY KEY REFERENCES members(member_id),
    birthday    TEXT,             -- ISO date
    gender      TEXT,
    terms_json  TEXT,             -- JSON list of Congress terms (type, start, end, state, district, party, caucus, url, phone, ...)
    social_json TEXT,             -- JSON {twitter, facebook, youtube, youtube_id, instagram, ...} (may be {})
    ids_json    TEXT,             -- JSON {bioguide, govtrack, opensecrets, ballotpedia, cspan, votesmart, ...}
    wikipedia_title TEXT, wikidata_id TEXT,
    wiki_json   TEXT,             -- on-demand cache: Wikipedia intro + Wikidata facts (see bios.fetch_wiki)
    wiki_fetched TEXT             -- ISO date of that fetch; refreshed after bios.WIKI_MAX_AGE_DAYS
);
CREATE TABLE IF NOT EXISTS video_briefs (   -- the user's short-video brief per politician (Videos tab)
    member_id  TEXT PRIMARY KEY REFERENCES members(member_id),
    blurb      TEXT,              -- facts blurb as edited by the user (NULL = still the auto-generated one)
    script     TEXT,              -- the user's narration script (any tone; stored verbatim)
    hook       TEXT,              -- text for the first slide (NULL = default)
    outro      TEXT,              -- text for the last slide (NULL = default)
    for_json   TEXT,              -- JSON [[congress, bill_id], ...] bills featured as "voted for" (NULL = auto top picks)
    against_json TEXT,            -- same for "voted against"
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS votes (
    vote_id     TEXT PRIMARY KEY, -- 'H-2025-001' (House) or 'S-119-1-00001' (Senate)
    chamber     TEXT NOT NULL,
    congress    INTEGER,
    session     INTEGER,
    roll_num    INTEGER,
    vote_date   TEXT,             -- ISO YYYY-MM-DD
    question    TEXT,             -- e.g. 'On Passage', 'On the Motion to Recommit'
    bill_id     TEXT,             -- normalized e.g. 'HR 1', 'S 5', NULL for nominations/quorum
    title       TEXT,             -- bill/nomination description (best available)
    result      TEXT,
    is_passage  INTEGER NOT NULL DEFAULT 0, -- 1 = vote on the measure itself, not procedure
    yeas        INTEGER, nays INTEGER, present INTEGER, not_voting INTEGER,
    source_url  TEXT
);
CREATE INDEX IF NOT EXISTS idx_votes_date ON votes(vote_date);
CREATE INDEX IF NOT EXISTS idx_votes_bill ON votes(bill_id);
CREATE TABLE IF NOT EXISTS member_votes (
    vote_id      TEXT NOT NULL REFERENCES votes(vote_id),
    member_id    TEXT NOT NULL REFERENCES members(member_id),
    position     TEXT NOT NULL,   -- normalized: Yea | Nay | Present | Not Voting | Other
    position_raw TEXT,
    party        TEXT,            -- party at time of vote
    PRIMARY KEY (vote_id, member_id)
);
CREATE TABLE IF NOT EXISTS bills (
    congress    INTEGER NOT NULL,
    bill_id     TEXT NOT NULL,    -- same normalized form as votes.bill_id ('HR 1')
    title       TEXT,             -- NULL if Congress.gov had no such bill (status='not_found')
    policy_area TEXT,             -- Congress.gov single top-level topic, e.g. 'Health'
    sponsor_id  TEXT,             -- bioguide id
    sponsor_name TEXT,
    sponsor_party TEXT,
    introduced_date TEXT,
    latest_action_date TEXT,
    latest_action_text TEXT,
    became_law  INTEGER NOT NULL DEFAULT 0,
    law_number  TEXT,             -- e.g. '119-21' (Public Law)
    status      TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'not_found'
    url         TEXT,             -- human page on congress.gov
    brief       TEXT,             -- 1-2 sentence plain summary shown in the Summary column (see congress.run_briefs)
    brief_source TEXT,            -- 'crs' = lead of the Congress.gov CRS summary; 'ai' = "WHAT IT DOES" of a Process summary
    brief_checked TEXT,           -- ISO date we last looked for a CRS summary (bills without one are re-checked later)
    cosponsor_count INTEGER,      -- Congress.gov counts, used by the Top bills "interest" score (NULL = not loaded yet)
    amendment_count INTEGER,
    action_count    INTEGER,
    PRIMARY KEY (congress, bill_id)
);
CREATE TABLE IF NOT EXISTS bill_summaries (
    congress    INTEGER NOT NULL,
    bill_id     TEXT NOT NULL,
    summary     TEXT NOT NULL,    -- plain text written by the LLM
    source      TEXT NOT NULL,    -- what it was written from, e.g. 'CRS summary (Public Law)' / 'Bill text (Engrossed in House)'
    truncated   INTEGER NOT NULL DEFAULT 0,  -- 1 = source was cut to SUMMARY_MAX_CHARS
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (congress, bill_id)
);
CREATE INDEX IF NOT EXISTS idx_bills_area ON bills(policy_area);
CREATE INDEX IF NOT EXISTS idx_mv_member ON member_votes(member_id);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't touch an existing table, so add them here.
MIGRATIONS = [("bills", "brief", "TEXT"), ("bills", "brief_source", "TEXT"), ("bills", "brief_checked", "TEXT"),
              ("bills", "cosponsor_count", "INTEGER"), ("bills", "amendment_count", "INTEGER"), ("bills", "action_count", "INTEGER")]


def _migrate(conn):
    for table, column, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def connect(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
