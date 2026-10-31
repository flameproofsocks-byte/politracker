# Architecture

```
 clerk.house.gov XML ─┐                      ┌─> parse.py (pure) ─┐
 senate.gov XML ──────┼─> fetch.py ──cache──>│                    ├─> ingest.py ─> SQLite ─> web.py (Flask) ─> browser
 congress-legislators ┘   (data/raw/)        └─> crosswalk CSV ───┘   (db.py)
```

1. `ingest.run()` loads the crosswalk (LIS id → bioguide id, full names).
2. For each `(congress, session, year)` in `config.SESSIONS`: probe House rolls in batches (thread pool),
   then read the Senate menu and fetch each Senate vote not already in the DB.
3. `fetch.get` serves from `data/raw/` if cached; otherwise downloads, validates, caches.
4. `parse.*` turn XML into `{"vote", "members", "cast"}` dicts; member keys are `("bioguide", id)` or `("lis", id)`.
5. `ingest._store` resolves keys → `member_id`, applies `MIN_DATE`, upserts into the three tables.
6. `web.py` runs read-only SQL per request (opens a short-lived connection). No JS, no build step;
   Jinja templates + inline CSS. Pagination is 100 rows/page.

"Process summary" (on demand, web.py -> summarize.py): POST `/bill/<congress>/<bill_id>/summary` ->
`congress.fetch_source_text` (CRS summary, else bill text) -> `run_llm` (`claude -p`, no tools) -> `bill_summaries`.
Cached forever; the page shows the stored text and hides the button.

Design choices: stdlib `urllib`/`sqlite3`/`xml.etree` for ingest (only Flask + pytest as dependencies);
raw-file cache so the DB is always rebuildable offline; parsers pure so they're testable with fixtures.
