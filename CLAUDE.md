# politracker — agent guide

Tracks how US legislators voted. Ingests official roll-call XML → SQLite → local Flask UI.
Read `README.md` first for the user-facing picture, then this file.

## Commands (run from this directory)
```bash
./venv/bin/python -m politracker ingest [--refresh]   # --refresh re-stores everything + re-downloads crosswalk
./venv/bin/python -m politracker bills [--limit N]     # Congress.gov bill details; needs key (see README)
./venv/bin/python -m politracker daily                 # ingest + bills; run by dashboard routine 2355cdc8
./venv/bin/python -m politracker serve [--port 5057]   # foreground
./tracker.sh start|stop|restart|status                 # background; nothing auto-starts it after a reboot
./venv/bin/python -m pytest -q
```
Create the venv with `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.

## Layout
```
politracker/config.py    all URLs, paths, SESSIONS list, MIN_DATE, WORKERS
politracker/fetch.py     HTTP + on-disk cache (data/raw/). Only valid responses are cached
politracker/congress.py  Congress.gov API client + parse_bill (policy area, sponsor, law status) -> `bills` table
politracker/profile.py   PURE: your picks + party votes -> party lean, per-bill party alignment, favored/opposed topics
politracker/bios.py      politician profile facts: congress-legislators JSON -> `member_bios` (ingest) + on-demand Wikipedia/Wikidata cache; pure helpers (age, term spans, service length, links)
politracker/interest.py  PURE: the Top bills "interest" score (WEIGHTS/CAPS/RANK_BY), build_leaderboard(), rank(), period_filter(), reasons()
politracker/video.py     PURE: Videos tab logic: blurb/outline text, bill picking, topic leanings, slide list (`scenes`), length estimate
politracker/frames.py    slide-image export via headless Chromium (find_browser, render_frames)
politracker/summarize.py "Process summary": source text -> `claude -p` -> cached in `bill_summaries`
politracker/parse.py     PURE functions: XML text -> dicts (no I/O). Most logic worth testing lives here
politracker/ingest.py    orchestration: crosswalk, download in batches, resolve member IDs, write DB
politracker/db.py        SQLite schema (single SCHEMA string) + connect()
politracker/static/     theme.css (THE theme: every colour/radius/shadow/font as CSS variables) + style.css (layout only) + app.js (thumbs, summary expand, search typeahead)
politracker/web.py       Flask routes (read-only). Templates in politracker/templates/
tests/                   pytest; fixtures are real source files in tests/fixtures/
data/                    gitignored: raw/ cache + politracker.db + saved_bills.json (your approve/deny picks)
docs/                    data-sources, schema, architecture, roadmap
```

## Politician profiles (`/politician/<id>`, `bios.py`)
- Two pages per person: `/politician/<id>` = summary (aggregate stats + bio, NO individual votes; a test asserts there are no `/vote/`
  links) and `/member/<id>` = the filterable vote history. `_person.html` renders the shared header + Summary|Votes tabs. Names on
  the home page, vote pages and the typeahead link to the summary.
- Data: `ingest` (and `python -m politracker bios`) loads `legislators-current/historical/social-media.json` into `member_bios`
  (re-downloaded when the cached copy is >7 days old; historical file only if a member isn't in current). Wikipedia intro + Wikidata
  birthplace/education/occupation/date-of-death are fetched lazily by `bios.get_wiki` on first page view and cached in
  `member_bios.wiki_json` for 30 days; failures are swallowed (stale cache or None) so the page always renders. Wikimedia asks for a
  descriptive User-Agent: keep `config.USER_AGENT`.
- Rules baked in: show an age only for SITTING members (we can't know if a former member is alive) unless Wikidata gives a death
  date (then "died ... (aged N)"); service length = union of Congress term intervals capped at today; spans merge contiguous terms with
  the same chamber/state/district/party; independents are compared with the party they caucus with (`bios.caucus_letter` ->
  `web.compare_party` -> `party_alignment` and the votes page); only https links built from IDs/handles (never raw input).
- Portraits are hotlinked from the unitedstates image mirror (`config.PHOTO_URL`); `app.js` hides the frame if one is missing.
- "With their party" here is over ALL ingested roll calls (and final passage separately); the votes-page stat is over the current filters.

## Videos tab (`/videos`, `video.py`, `frames.py`, `video_present.html`)
- Per politician `video_briefs` row (blurb, script, hook, outro, featured bills). Routes: `/videos` (list + search), `/videos/<id>` (editor),
  `POST /videos/<id>/save` and `/draft` (JSON; need `X-Politracker: 1`; 400s come back as `{"error": ...}` via the 400 handler),
  `/videos/<id>/present` (standalone 1080x1920 stage, `present.js` scales it; `?scene=N &auto=0 &loop=1 &clean=1 &freeze=1`),
  `/videos/<id>/frames.zip` (PNG per slide).
- Content rules (tests enforce them): generated text and slides come ONLY from recorded passage votes + local profile data; "voted for/against" =
  the member's latest Yea/Nay on a passage vote of the bill; NO gendered pronouns in generated text (we never infer them; sentences use names);
  the user's script is stored verbatim and never auto-edited or placed on slides; slides always carry the source/through-date footer.
- Featured bills: `video.pick_candidates` ranks by the Top bills interest score (`leaderboard_rows()` is shared with `/top`); saved picks are validated
  against the member's own votes (wrong side/unknown -> 400); `resolve_selection(None)` = auto top 3, `[]` = the user chose none.
- "Stands for": `topic_leanings` needs >= 2 votes AND >= 60% in one direction, so one topic never appears on both sides; "Congress" (procedural) is ignored.
- Length: `WORDS_PER_SECOND` 2.6, target 15-30s (`TARGET_SECONDS`); `build_outline` steps down (`OUTLINE_TIERS`) to fit and prefers short speakable titles.
  `editor` JS (`static/video.js`) mirrors the word regex `[\p{L}\p{N}_'\u2019-]+` of `video.word_count`: keep them in sync.
- Slide export: Chromium is found via `frames.find_browser` (chromium/chromium-browser/google-chrome); snap-packaged Chromium can't write to /tmp, so frames go
  to `data/tmp/frames-*` (deleted afterwards); the render URL is `http://127.0.0.1:<SERVER_PORT>/...` (never the Host header). `render_frames` resolves
  `subprocess.run` at call time so tests patch `frames.subprocess.run`. Slide images fetch the portrait from the web (virtual-time-budget lets it load).
- `present.css` is scanned by `tests/test_theme.py` like `style.css` (theme variables only; `--dur` is the one allowed inline per-slide variable).
- The typeahead on `/videos` reuses the home search via `data-link="/videos/"` (`app.js`).

## Top bills (`/top`, `interest.py`)
- Our own score, NOT Congress.gov view counts: `congress.gov/most-viewed-bills` is behind a Cloudflare bot challenge (HTTP 403 "Just a moment...") and
  robots.txt disallows crawlers (incl. anthropic-ai); there is no API for views. Do NOT try to scrape it or get around the challenge. If the user ever
  wants real view data, the only clean routes are a manual import of what they read in their own browser, or an official feed if one appears.
- Score parts (each 0..1) x weights = 0..100: `close` (relative margin of the closest passage vote), `partisan` (|D Yea rate - R Yea rate|, passage votes pooled
  over chambers), `amended`/`cosponsors` (counts from Congress.gov, log-scaled to `CAPS`), `votes` (roll calls on the bill, log-scaled), `law`.
  Change weights in `interest.WEIGHTS`; the "How is the score worked out?" card and the tooltips read them, so docs update themselves; update
  README's list when you change them. Bills with no passage vote (procedural only) get 0 for close/partisan and still rank on the rest.
- A bill's date = its latest passage vote (else its latest vote); year/month filter that date. No `year`/`month` params = latest month with votes;
  `?year=&month=` (both present, empty) = all time; junk values are ignored, never errors. Page shows top `TOP_LIMIT` (50).
- `bills.cosponsor_count/amendment_count/action_count` come from the cached Congress.gov JSON (`congress.parse_bill` for new bills,
  `congress.backfill_counts()` for old rows, run by `bills`/`daily`; no network). Add a column to BOTH `db.SCHEMA` and `db.MIGRATIONS`.

## UI conventions (templates + static)
- **Theme = `static/theme.css`, and only that file.** It holds every colour, the corner radius (`--radius`, `--radius-pill`), shadows and
  fonts as CSS variables; `style.css` is layout only and must reference them (`var(--accent)`, `var(--radius)`...), never hard-code hex/rgb,
  px radii, shadows or font stacks, and templates must not either. `tests/test_theme.py` enforces this and also pins the agreed look:
  light only (no dark mode), warm cream/tan surfaces (not white), blue main accent with red secondary (`--accent-2`), square corners
  (`--radius: 0`). To restyle the site, edit theme.css; to add a new themed value add a variable there and use it in style.css.
- No inline CSS/JS blocks except the small page-specific scripts in `vote.html` and `mine.html`.
- Shared pieces: `_votes_table.html` (the `th` sort macro, ★/summary/chip markup), `_filters.html`, `_thumbs.html` (the approve/deny thumbs macro; `icon()` is also used for the static thumbs on My picks). Wrap tables in
  `<div class="table-wrap">`. Clamp long text with CSS (`.bill-title`, `.brief .clamp`), keep the full text in a `title` attribute.
- Several tests match literal markup: `<th class="starcol"` as the FIRST header, a final `<th>Summary</th>` with no class, `class="star agree"`,
  `class="match-yes" title="..."`, `class="nav active" href="..."`, `<b>With your picks:</b>` ... `</div>`. Change those on purpose, with the test.
- Home page: `/` = hero search (typeahead -> `GET /api/members?q=`, min 2 chars, 8 results) + chips (`fchip` macro builds the toggle links)
  + either the filtered member table (any of `member`/`chamber`/`party`/`state`) or recent passage votes + lean card (`compute_profile()`).
- To eyeball changes: `chromium --headless --no-sandbox --screenshot=...` works; for clicks/typing use `playwright-cli` with a
  `.playwright/cli.config.json` pointing `executablePath` at `/snap/bin/chromium` (Chrome itself isn't installed), then
  `run-code "async page => { await page.fill('input[name=member]', 'cant'); ... }"`. Put scratch screenshots/config outside the repo and delete them.

## Sorting (web.py `order_by`)
Columns sort via the whitelist `SORT_COLUMNS` / `MEMBER_SORT_COLUMNS` (never interpolate the `sort` URL param
directly). Default = rank desc (approved, denied, unrated), then date desc. The user's picks live in a JSON file, so
`q()` registers a SQLite function `stance(congress, bill_id)` -> 'agree'|'disagree'|NULL per request, and
`RANK_SQL` / `MATCH_SQL` in web.py are built on it (no bound params needed, no IN-lists).
New sortable column = add a key to the dict + a `th('key', 'Label')` in `templates/_votes_table.html`.
`tracker.sh`: don't write `cd X && cmd &` (backgrounds a subshell, `$!` is then the wrong PID).

## Rules of the road
- **Update docs in the same change as code.** Schema change → `docs/schema.md`; new source/quirk →
  `docs/data-sources.md`; finished/new work → `docs/roadmap.md`.
- **No migrations (yet).** On a schema change, edit `SCHEMA` in `db.py`, then
  `rm data/politracker.db && python -m politracker ingest` — this rebuilds from the `data/raw/` cache
  with almost no network use. Never delete `data/raw/` casually; re-downloading ~1,500 files can trip a WAF.
- Keep parsers pure and add a fixture-based test in `tests/test_parse.py` for each new source field.
- Be polite to the sources: keep `WORKERS` low. senate.gov (Akamai) returned HTTP 403 to this machine
  after ~1,300 requests in a few minutes at 6 workers. Do **not** work around a 403 with spoofed
  headers/IP rotation; wait and re-run (cache means only the missing files are fetched).
- Distinguish "missing" from "blocked": `fetch.get` returns None for both, but prints a WARNING on 403.

## Gotchas (learned the hard way)
- House Clerk returns **HTTP 200 with an error body** for nonexistent rolls
  (`<xml>Error sanitizing file…</xml>`). "Exists" = body contains `<rollcall-vote`.
  Ingest discovers the last House roll by probing 50-number batches until a batch is empty.
- Senate IDs are LIS ids (`S428`); House IDs are bioguide ids. `member_id` in our DB is always the
  bioguide id, mapped through the unitedstates/congress-legislators CSVs. Fallback `LIS-<id>` means the
  crosswalk had no match (currently zero).
- House XML has only last names; full names come from the crosswalk.
- `position='Other'` = Speaker-election votes, where the "vote" is a candidate's name (kept in `position_raw`).
- `is_passage` is a regex heuristic (`PASSAGE_RE` in parse.py) on the vote question. Verify against
  `SELECT question, COUNT(*) FROM votes WHERE is_passage=1 GROUP BY 1` after changing it.
- Voice votes and unanimous consent have **no individual record** and will never appear here.
  The UI should not imply a missing vote means "didn't vote".
- Senate `question` uses the generic `<question>` field (e.g. "On Passage of the Bill"); the
  specific bill number is in `bill_id`.

- Congress.gov: one call per bill (`/bill/{congress}/{type}/{n}`), ~0.8s apart, raw JSON cached under
  `data/raw/congress/`. Stops cleanly on HTTP 429 and resumes next run. Bills it can't find are stored
  with `status='not_found'` so they aren't retried forever. Order: passage-vote bills first.
- `bills.policy_area` is Congress.gov's single top-level topic per bill (~32 values). Amendment/procedure
  votes inherit the topic of their parent bill via `(congress, bill_id)`. Nominations have no bill → no topic.
- The routine file in `routines/` is the dashboard's format (id/name/cron/command...). Create/change
  routines via the dashboard API when it is running (see root CLAUDE.md); the dashboard may be down.

- **Brief column** (`bills.brief`, `brief_source` 'crs'|'ai', `brief_checked`): `congress.run_briefs()` (run by `bills` and
  `daily`) takes the lead of the newest CRS summary (`brief_from_crs`: skips the title line, whole sentences up to
  `BRIEF_MAX` 260 chars, abbreviation-aware) ~1 API call per bill. `summarize.get_or_create` overwrites it with the AI
  "WHAT IT DOES" line (`brief_from_ai`), and `run_briefs` never overwrites an 'ai' brief. Bills with no CRS summary are
  re-checked after `RECHECK_DAYS` (14). Columns were added with an in-place migration (`db.MIGRATIONS`, `ALTER TABLE`):
  add future columns to BOTH `SCHEMA` and `MIGRATIONS`. The Summary `<th>` must stay last in `_votes_table.html`/`mine.html`.
- **Summaries**: `summarize.run_llm` shells out to `claude -p` (the same pattern as dashboard/server.py), with
  `--tools ""`, `--setting-sources ""`, empty temp cwd, so no tools/hooks/CLAUDE.md can leak in. Bill text is
  untrusted input (prompt-injection): the system prompt says to treat it as data, the CLI has no tools, and the UI
  renders the result with `textContent`/Jinja escaping only. The POST route requires header `X-Politracker: 1`
  (blocks cross-site drive-by POSTs that would spend LLM usage). Summaries cost usage on the user's Claude login;
  to move to the Anthropic SDK instead, replace only `run_llm` (read the claude-api skill first; default model
  `claude-opus-5-5`) -- nothing else depends on how the text is produced. Model is `config.SUMMARY_MODEL`.
- Source preference: newest CRS summary by `updateDate` (>200 chars), else newest bill text version ("Formatted Text").
  Brand-new bills may have neither -> the UI shows an error and the user can retry later.
- Use `congress._api_json`/`fetch_bill` for Congress.gov calls: requests need our User-Agent (default
  `Python-urllib` got HTTP 403 from api.congress.gov).

- **Picks (thumbs up = approve / thumbs down = deny)** live in `data/saved_bills.json` (`config.SAVED_PATH`), deliberately NOT in the DB, so the
  "rm politracker.db && ingest" rebuild doesn't erase user choices. Format: `[[congress, "HR 1", "agree"|"disagree"], ...]`
  (old files with plain `[congress, bill_id]` entries load as `agree`). `POST /bill/<congress>/<bill_id>/star`
  (needs `X-Politracker: 1`) cycles none -> agree -> disagree -> none and returns `{"stance": ...}`; writes are atomic.
  The route/file keep the old "star" name and the stored values stay `agree`/`disagree` for continuity (UI words: approve/deny).
  The UI sends explicit `?to=agree|disagree|none` (clicking the already-active thumb sends `none`); the no-`to` cycling form still works.
  Markup contract used by tests/app.js: `<span class="thumbs" data-congress data-bill data-stance="agree|disagree|">` holding
  `button.thumb.up` / `button.thumb.down` (`.on` = active, `aria-pressed`). Sorting key is still `sort=star` (header label "Rating").
- **Scoring rule** (one definition, two implementations that tests keep in sync: `vote_matches()` in Python and
  `MATCH_SQL` in SQL): +1 if the member's vote is Yea on `agree` or Nay on `disagree`, -1 if the opposite, 0
  otherwise. ONLY `is_passage` votes score; procedural votes (cloture, motions, amendments) never do, because a
  Nay on cloture is not opposition to the bill. The headline "With your picks" (`picks_alignment()`) counts one data
  point per rated bill: the member's most recent Yea/Nay passage vote on it; the history table marks every passage vote.
  A rated bill the member has no row for (voice vote / other chamber / outside the ingested dates) is simply not
  shown or scored: never present that as "didn't vote". (There used to be a "Your rated bills" card on member pages;
  it was removed on purpose because the ★ column sort covers it. The editable list lives on /mine.) "With their party" is the older `party_majority` stat (unrelated to picks).
- **My picks page** (`/mine`, `profile.analyze`): per rated bill, each party's Yea rate on passage votes (D and R
  members only, both chambers pooled, Yea/Nay only); party "alignment" = share voting the way you wanted (agree = Yea,
  disagree = Nay). Only bills where the parties' Yea rates differ by >= `PARTISAN_GAP` (20 pts) feed the lean
  (mean of Dem alignment minus Rep alignment, -100..+100); label needs >= `MIN_PARTISAN` (3) such bills; |lean| >= 15 is
  "Closer to", >= 40 "Strongly". Topics = Congress.gov `policy_area`; favor/oppose/mixed by approved-vs-denied count. Compare
  floats with the epsilon already in `analyze` (0.6-0.4 != 0.2). Direct set: `POST .../star?to=agree|disagree|none`.
- Tests that touch picks must monkeypatch `config.SAVED_PATH` to a tmp file -- never the user's real one. Beware
  asserting `"match-yes" in html`: the class name is also in the page's CSS; assert `class="match-yes"` instead.

## Current state
MVP + Congress.gov bill details/topic filters + daily routine done. Next candidates are in `docs/roadmap.md` (bill sponsors/subjects via Congress.gov API,
member-vs-member comparison, scheduled ingest, etc.). Update `project.json` fields only via the
dashboard conventions (it's owned by the Reguard dashboard).
