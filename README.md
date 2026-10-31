# politracker

Local tool for seeing **how US Senators and Representatives actually voted**. It pulls official
roll-call vote data from the House Clerk and Senate.gov, stores it in SQLite, and serves a small
local web UI to search by member, topic, or bill.

Goal: be an informed voter — pick a candidate, see their real votes on specific issues, and check
whether they line up with your beliefs.

**Status: MVP + bill details/topic filters + daily routine** (steps 1–3 of the plan below). Covers roughly Nov 2024 → present
(118th Congress 2nd session through the current 119th). See `docs/roadmap.md` for what's next.

## Quick start

```bash
cd projects/politracker
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt   # once
./venv/bin/python -m politracker ingest      # download + load votes (~2-5 min first run)
./venv/bin/python -m politracker bills       # bill topics/sponsors from Congress.gov (needs free API key, see below)
./venv/bin/python -m politracker serve       # http://127.0.0.1:5057 (foreground)
./tracker.sh start|stop|restart|status       # same UI as a background process (pid /tmp/politracker.pid, log /tmp/politracker-web.log)
./venv/bin/python -m politracker daily       # ingest + bills (what the scheduled routine runs)
./venv/bin/python -m pytest -q               # tests (no network needed)
```

`ingest` is idempotent and cached (`data/raw/`); re-run it any time to pull new votes.

## Congress.gov API key (for topics & bill details)

Free and instant: sign up at https://api.congress.gov/sign-up/ (emailed to you), then either
`export CONGRESS_API_KEY=...` or create `data/secrets.json` containing `{"congress_api_key": "..."}`
(gitignored). Without a key everything else works; the topic dropdown and bill panel just stay empty.
First `bills` run takes ~10 min for ~560 bills (resumable, cached). The shared public `DEMO_KEY`
is limited to 10 calls/hour, so it's only good for testing.

## "Process summary" button (plain-English bill summaries)

On any vote page for a bill, **Process summary** reads what the bill actually does and writes a short
neutral summary (what it does, key provisions, who it affects, easy-to-miss points). Source = the
Congress.gov **CRS summary** (human-written) when one exists, otherwise the bill text itself. Summaries
are AI-written, labelled as such with their source, and cached in the DB, so each bill is only generated once
(~10 s). Needs the Congress.gov key (above) and the logged-in `claude` CLI on this machine; no extra setup.
Very long sources are cut at `SUMMARY_MAX_CHARS` and the page says so. Treat it as a readable overview, not legal
advice; the "congress.gov" link on the page goes to the official text.

## Daily updates

A dashboard routine "politracker daily ingest" (`routines/2355cdc8.json`, cron `0 9 * * *` UTC) runs
`python -m politracker daily`. The dashboard loads routines at startup, so restart it once to pick this up.

## Using the UI

- **Politician summary page** (`/politician/<id>`, where names across the site lead): a Wikipedia-style overview with **no individual
  votes**. Top: three numbers: how often they vote **with their party**, how often they vote **the way you want** on the bills you
  rated, and how often they **showed up** to vote. Below: a short Wikipedia intro, age, birthplace, education, current office and
  when they started, how long they've served in Congress and where, official social accounts, contact details, and reference links.
  Independents (e.g. Sanders, King) are compared with the party they caucus with. A **Votes** tab on the same page has every
  individual vote. The first time you open someone, the Wikipedia/Wikidata part is fetched (about a second) and cached for 30 days;
  offline, the page still shows everything from local data.
- **Videos** tab (and a **Make a short video** button on every politician's summary page): a brief for a 15-30 second vertical (TikTok-style)
  video about one politician. Pick a politician and you get: (1) a **facts blurb** built only from their recorded votes (tenure, bills they
  voted for and against, how often they side with their party, what they mostly vote Yea/Nay on), which you can edit and rebuild; (2) a place
  to paste **your own script** (any tone, stored verbatim) with a live word count and "about Ns" meter against the 15-30s target, plus an
  "Insert a starter outline" button that drafts a neutral hook -> tenure -> votes -> stands-for script sized to fit; (3) checkboxes to choose
  **which bills are featured** (up to 5 per side, most interesting first); (4) a **presenter** (`Open presenter`): a 9:16 slide show (hook,
  who they are, the numbers, voted for, voted against, stands for, outro) that plays itself in ~26 seconds: record it, or use
  `?clean=1` for a recording with no controls. Keys: arrows, space, R, F (fullscreen), C (hide controls). **Download slide images** gives
  every slide as a 1080x1920 PNG (about 20 seconds; needs Chromium/Chrome on this machine). Slides contain only recorded-vote facts and
  carry a "Source: House & Senate roll-call votes, through <date>" line; the sensational script is your own text and is never put on slides.
- **Top bills** tab: a leaderboard of the most *interesting* bills, filterable by **year and month** (defaults to the latest month with
  votes; pick "All time" for everything) and rankable by overall interest, closest vote, most party-line, most amended, most cosponsors
  or most roll calls. Each row says why it ranked ("Razor-thin 50-50", "Party-line (D 0% vs R 99% Yea)", "493 amendments", "Became law")
  and has the usual thumbs, vote link and summary. The interest score is **our own measure** (close vote 30, party-line 25, amendments 15,
  roll calls 10, cosponsors 10, became law 10), not page views: Congress.gov's "most viewed bills" list sits behind a bot challenge and
  its robots.txt disallows crawlers, so it can't be read automatically. Hover a score for its breakdown.
- **Home** (Politicians tab): a search box with live suggestions (type 2+ letters, arrow keys + Enter work), quick
  filters for chamber / party / state, the latest final votes, and a card with your party lean. With no filter set you
  see the latest votes; with any filter you get the matching politicians.
- Look and feel: a light aged-paper theme (tan background, cream panels, blue accents with a little red, square corners). Everything about the
  theme (colours, corner radius, shadows, fonts) lives in **`politracker/static/theme.css`**: edit that one file to change the look globally.
  `static/style.css` is layout only; behaviour is in `static/app.js`.

- **Home**: search a member by name or 2-letter state → member page.
- **Member page**: every vote they cast, with their position (Yea/Nay/…), the result, and whether
  they sided with their own party's majority. Filter by topic keyword, chamber, position, date.
- **Summary column** (far right of every bill table, also on My picks): a one- or two-sentence plain description of what
  the bill does, so you don't have to guess from the title. It's the lead of the Congress Research Service summary
  (human-written), or the "what it does" line of your own **Process summary** if you've run one on that bill (that
  replaces the CRS lead; hover for which). Brand-new bills have no CRS summary yet and show a dash. The text is searchable.
- **Topic filter** (after `bills` has run): pick a policy area (Health, Immigration, Taxation…); the
  member page also has a "By topic" Yea/Nay table.
- **Your picks (Rating column)**: every bill has a **thumbs up** (approve: a bill you want passed) and a **thumbs down**
  (deny: a bill you don't want). Click one to set it, click the other to switch, click the active one again to clear it.
  Approved shows a green thumb, denied a red one. The same control appears on vote rows and vote pages.
- **My picks** (link at the top of every page, with a count badge): lists every bill you rated and lets you change
  each one (Agree / Disagree / Remove). It also shows **which party your picks line up with** (a lean between
  Democrats and Republicans, built from how each party's members actually voted on your rated bills) and the
  **topics you generally favor or oppose**. The lean only counts bills the parties split on by 20+ points, needs at least
  3 of them, and is a rough guide to who votes like you on those bills, not a verdict on your politics.
- **How a politician lines up** (member page): the box at the top shows two numbers: **With their party**
  (how often their Yea/Nay votes went with their own party's majority) and **With your picks** (on the rated bills
  they cast a Yea/Nay *passage* vote on, how often they voted the way you want: Yea on approved bills, Nay on denied ones).
  The history table has a **Matches you** column (✓ / ✗); rated bills float to the top of the history by default
  (the ★ column sorts them first, or last). Procedural votes (cloture, amendments…) are shown but never scored; bills they had no roll-call vote
  on (other chamber, voice vote) are simply not counted.
- **Sorting**: every column header is clickable to sort ascending/descending (click again to flip; an arrow shows the
  active column). Default order is **approved bills, then denied, then everything else newest first**. Bills sort by type
  then number (HR 9 before HR 10). Filters and paging keep your sort. "Matches you" is sortable on member pages.
- **Votes page**: search all votes by topic/bill/title; click through to see every member's vote.
- Default scope is **final passage votes only** (votes on the measure itself). Switch to "All votes
  on bills" to include amendments/procedure, or "Everything" to include nominations.

## Documentation map

| File | Read it when you need… |
|------|------------------------|
| `CLAUDE.md` | You are an agent about to change this project — start here |
| `docs/data-sources.md` | Where data comes from, URL patterns, quirks, rate limits |
| `docs/schema.md` | Database tables/columns and how IDs work |
| `docs/architecture.md` | Module map and data flow |
| `docs/roadmap.md` | What's done, known limitations, ideas for next steps |

## Plan (original brief)

1. Find the data (public, no API key) — **done**, see `docs/data-sources.md`.
2. Build a database of votes, details, and per-member positions — **done**.
3. Local web UI to search/visualize the last ~2 years — **done (MVP)**.
