"""Central settings. Everything path/URL related lives here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # cached source XML/CSV (re-runs don't re-download)
DB_PATH = DATA_DIR / "politracker.db"
SAVED_PATH = DATA_DIR / "saved_bills.json"   # starred bills; kept outside the DB so a rebuild never loses them

# (congress, session, calendar_year) tuples to ingest. Roughly "last 2 years" as of Oct 2026.
# House files are keyed by calendar year, Senate files by congress+session.
SESSIONS = [
    (118, 2, 2024),
    (119, 1, 2025),
    (119, 2, 2026),
]
# Votes dated before this are skipped (ISO date). Set to None to keep whole sessions.
MIN_DATE = "2024-10-15"

HOUSE_URL = "https://clerk.house.gov/evs/{year}/roll{num:03d}.xml"
SENATE_URL = ("https://www.senate.gov/legislative/LIS/roll_call_votes/"
              "vote{congress}{session}/vote_{congress}_{session}_{num:05d}.xml")
SENATE_MENU_URL = ("https://www.senate.gov/legislative/LIS/roll_call_lists/"
                   "vote_menu_{congress}_{session}.xml")
LEGISLATORS_URLS = [
    "https://unitedstates.github.io/congress-legislators/legislators-current.csv",
    "https://unitedstates.github.io/congress-legislators/legislators-historical.csv",
]

USER_AGENT = "politracker/0.1 (personal research tool)"
WORKERS = 3          # parallel downloads; senate.gov's WAF 403s us at ~6 workers / ~1300 fast requests

# "Process summary" button (see docs/architecture.md). Uses the logged-in `claude` CLI, like the dashboard does.
SUMMARY_MODEL = "claude-opus-5-5"
SUMMARY_MAX_CHARS = 120_000     # source text beyond this is cut and the UI says so (never silent)
SUMMARY_TIMEOUT = 240           # seconds for one `claude -p` call

# Politician profile data (see bios.py). Bulk JSON is refreshed when the cached copy is older than BIOS_MAX_AGE_DAYS.
LEGISLATORS_JSON = {
    "current": "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "historical": "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
    "social": "https://unitedstates.github.io/congress-legislators/legislators-social-media.json",
}
BIOS_MAX_AGE_DAYS = 7
PHOTO_URL = "https://unitedstates.github.io/images/congress/225x275/{bioguide}.jpg"   # official portraits, hotlinked by the browser
