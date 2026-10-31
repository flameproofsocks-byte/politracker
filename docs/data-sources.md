# Data sources

All public, **no API key**. Data window is set by `SESSIONS` and `MIN_DATE` in `politracker/config.py`.

## 1. House roll calls — Clerk of the House
- URL: `https://clerk.house.gov/evs/{year}/roll{NNN}.xml` (NNN zero-padded to 3; numbering restarts each calendar year)
- One XML per roll call: `<vote-metadata>` (date, `legis-num`, `vote-question`, `vote-desc`, result, totals)
  and `<vote-data>` with one `<recorded-vote>` per member. `legislator@name-id` = **bioguide id**.
- No index file is used; ingest probes numbers upward in batches of 50 until a batch has no valid file.
- Missing roll → HTTP 200 with `<xml>Error sanitizing file…</xml>` (not a 404!).
- `vote-desc` is the short bill title when the Clerk provides one (e.g. "GENIUS Act"), else we fall back to `legis-num`.
- Human index: `https://clerk.house.gov/Votes`

## 2. Senate roll calls — senate.gov
- Menu: `https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml` (lists vote numbers; we use the max)
- Vote: `https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{NNNNN}.xml`
- Has `<document>` (bill number/title), `<question>`, `<vote_result>`, `<count>`, and `<members>` with
  `lis_member_id`, party, state, `vote_cast`.
- Missing vote → redirect/HTML (no `<roll_call_vote` in body); treated as missing.
- **Rate limit**: Akamai WAF returned 403 to everything on `senate.gov/legislative/` after ~1,300 requests
  in a few minutes (6 parallel workers). It recovers after a while. `WORKERS=3` now; the raw cache means
  a re-run only fetches what's missing. If the menu is unreachable, ingest still loads all cached files.

## 3. Member ID crosswalk — unitedstates/congress-legislators
- `https://unitedstates.github.io/congress-legislators/legislators-current.csv` and `legislators-historical.csv`
- Columns used: `bioguide_id`, `lis_id`, `full_name`. Maps Senate LIS ids → bioguide ids and gives full names.
- Community-maintained, CC0. Cached in `data/raw/`; `ingest --refresh` re-downloads it.

## Session map (congress / session / year)
| Congress | Session | Year | House path year | Senate path |
|----------|---------|------|-----------------|-------------|
| 118 | 2 | 2024 | 2024 | `vote1182` |
| 119 | 1 | 2025 | 2025 | `vote1191` |
| 119 | 2 | 2026 | 2026 | `vote1192` |
Add a row to `SESSIONS` when a new session starts (119th session 2 ends ~Jan 3, 2027; 120th begins after).

## 4. Congress.gov API (bill details) — requires free key
- `https://api.congress.gov/v3/bill/{congress}/{type}/{number}?format=json`, key via `X-Api-Key` header.
- Fields used: `title`, `policyArea.name`, `sponsors[0]`, `introducedDate`, `latestAction`, `laws[]`.
- Limits: personal key 5,000 req/hour; `DEMO_KEY` only 10/hour (fine for tests). 404 = unknown bill.
- Sign up: https://api.congress.gov/sign-up/ . Docs: https://github.com/LibraryOfCongress/api.congress.gov
- Related endpoints for later: `/bill/.../subjects` (finer legislativeSubjects), `/summaries`, `/text`, `/cosponsors`.

### Congress.gov endpoints used by "Process summary"
- `/bill/{c}/{type}/{n}/summaries` — CRS summaries (HTML in `text`), several per bill (one per legislative stage).
  Very new bills have none. Omnibus bills can exceed 170 KB.
- `/bill/{c}/{type}/{n}/text` — list of text versions (newest first) each with `formats[]`; we fetch the
  "Formatted Text" (`.htm`, GPO text wrapped in `<pre>`) URL from congress.gov.
- Sending the default Python User-Agent got HTTP 403; always send `config.USER_AGENT`.

## 5. Politician profile data
- `legislators-current.json`, `legislators-historical.json`, `legislators-social-media.json` (same repo as the crosswalk; CC0): birthday, every
  Congress term (with state/district/party/caucus/contact), social handles, and IDs (bioguide, govtrack, opensecrets, ballotpedia, wikipedia, wikidata...).
  The historical file is ~13 MB and only read when a voting member isn't in the current file.
- Wikipedia REST `https://en.wikipedia.org/api/rest_v1/page/summary/<Title>` (intro text, CC BY-SA 4.0, show attribution) and Wikidata
  `wbgetentities` (claims P19 birthplace, P69 educated at, P106 occupation, P570 date of death; a second call resolves QIDs to English labels).
  Both are fetched lazily per politician, cached 30 days, and need a descriptive User-Agent. Wikidata coverage is uneven (can list one of several degrees).
- Official portraits: `https://unitedstates.github.io/images/congress/225x275/<bioguide>.jpg`, loaded by the browser.

## 6. Not available: Congress.gov "most viewed bills"
`https://www.congress.gov/most-viewed-bills` returns a Cloudflare challenge page (HTTP 403, "Just a moment...") to non-browser clients, robots.txt
disallows crawlers, and the API has no view-count endpoint. So the Top bills tab (`interest.py`) ranks by our own score from data we hold instead.

## Not used yet (candidates)
- GovInfo bulk data (`https://www.govinfo.gov/bulkdata`): bill status XML.
- ProPublica Congress API was shut down; don't rely on it.
