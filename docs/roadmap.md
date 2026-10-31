# Roadmap & known limitations

## Done (MVP)
- [x] Identify data sources (House Clerk, Senate.gov, congress-legislators) — `docs/data-sources.md`
- [x] Ingest ~1,600 roll calls / ~400k member votes for Nov 2024 → present; idempotent, cached
- [x] Local UI: member lookup, vote search, per-member history with filters, party-majority comparison
- [x] Tests for parsers and web pages
- [x] Congress.gov bill details (`bills` table, `politracker/congress.py`): topic, sponsor, law status
- [x] Topic filter, "By topic" member breakdown, bill-details panel on vote pages
- [x] "Process summary" button: AI plain-English bill summaries from CRS summary/bill text, cached
- [x] Daily ingest routine (dashboard routine `2355cdc8`, 09:00 UTC) running `politracker daily`
- [x] Thumbs up / thumbs down (approve / deny) on every bill replaced the cycling star; click the active thumb to clear; member page shows % agreement with own party AND with your picks, and a ✓/✗ "Matches you" column
- [x] Videos tab: per-politician short-video brief: facts blurb, user's script with 15-30s meter, bill picker, 9:16 slide presenter (auto-play, keys, clean mode), PNG slide export
- [x] Top bills tab (`/top`): interest-score leaderboard filterable by year/month and rankable by close vote / party-line / amendments / cosponsors / roll calls
- [x] Politician summary page (`/politician/<id>`): % with party / with your picks / participation, Wikipedia intro + Wikidata facts, age, time in Congress, social + reference links; Summary|Votes tabs
- [x] Theme: light aged-paper (tan/cream) with blue + a little red accents, square corners, dark mode removed; all theme values in `static/theme.css` (guarded by tests/test_theme.py)
- [x] UI refresh: wide compact vote tables (clamped titles/summaries), politician header + stat tiles, new home page (live search, chamber/party/state chips, latest votes, lean card), styled buttons/chips
- [x] Summary column (far right of all bill tables): CRS lead sentence(s), replaced by the AI line when Process summary was run; searchable
- [x] My picks page (top nav): view/change rated bills, party-lean estimate (Dem vs Rep, from the bills you rated), topics you favor/oppose
- [x] Dedicated ★ column + click-to-sort on every column (asc/desc); default = rated (green, then red) first, then newest
- [x] Star bills (☆/★ on vote rows and vote pages); a member page lists their votes on starred bills first

## Known limitations
- Topics = Congress.gov `policyArea` only (one per bill, coarse; "Congress" is the largest bucket at 92 bills).
  All 563 bills voted on Nov 2024 → Oct 2026 are loaded (as of 2026-10-16); the key lives in gitignored `data/secrets.json`.
- Search is plain keyword (`LIKE`) on title/bill number/question/topic.
- The routine file exists but the dashboard (port 6969) was not running, so it is scheduled only after the next dashboard restart.
- Voice votes / unanimous consent are not recorded individually and cannot be shown.
- A single bill can have many votes; passage detection (`is_passage`) is heuristic. Votes on
  resolutions (`H RES`, simple `S RES`) are included only if worded as passage.
- Ingest has no scheduling; run it by hand.
- Member party/state/chamber are "latest seen" only; a member who switched chambers shows the latest.
- Schema changes require rebuilding the DB (cheap, uses cache).

- Summaries are generated one click at a time and use the user's Claude login; no bulk pre-generation.
  They are AI-written and can be wrong or omit things; the source is always labelled. Amendment/procedural
  votes show the parent bill's summary, not what the amendment itself does.

- Picks are one global list (no named profiles) and are bill-level: the same pick applies to every vote on that bill.
  The score ignores amendments/procedure, so a politician who votes against a bill you like only via amendments is not counted.

- The party lean is only as good as the bills you rated (needs 3+ partisan ones); independents are not counted as a
  party; it says nothing about issues you haven't rated. Topics are Congress.gov's coarse single policy area per bill.

- The Summary column is the CRS lead, so it is as legalistic as CRS writes it, and ~11% of bills (mostly very new ones)
  have none yet. Amendment/procedural votes show their parent bill's summary.

- Profile facts depend on Wikidata, which can be incomplete; committee assignments, net worth, donors and election results are not included.

- The Top bills score is our own heuristic (it favours close, party-line, heavily amended bills), not public attention; real Congress.gov view counts are unavailable.

- Videos: slides use official bill titles (popular names like "One Big Beautiful Bill" aren't in our data), the narration is only counted, not read aloud, and no audio/captions are produced.

## Ideas, roughly in priority order
0. **Videos next**: captions generated from the saved script (one line per beat, timed to the slides); per-slide text overrides; an optional AI-assisted first draft of the script (via the `claude` CLI, as in summarize.py) that the user edits; a "compare two politicians" slide.
0. **Real attention data for Top bills**: a small manual importer for the weekly most-viewed list the user reads in their own browser (paste bill numbers + week), blended into the score or shown as a badge.
0. **My picks, next steps**: lean per topic ("you side with Democrats on Health, Republicans on Energy"), nearest
   individual members to your picks (rank everyone by agreement), an optional AI-written plain-English profile paragraph.
0. **Weight / filter by picks**: "only rated bills" filter, per-topic alignment (e.g. 80% on Health, 20% on Energy), compare two members against your picks side by side.
0. **Pre-generate summaries** for all passage bills in the daily routine (cost: ~560 calls once, then a few/day), and
   show a one-line summary in vote lists. Consider the Anthropic SDK + Batches API for bulk (`shared/` docs in the claude-api skill).
1. **Finer topics**: fetch `/bill/.../subjects` (legislativeSubjects) and bill summaries; add a `bill_subjects` table.
2. **Vote context**: for amendments/procedural votes show what the amendment does (Congress.gov `/amendment`).
3. **Compare members** side by side and compute agreement % (also member vs. party, member vs. user's own picks).
4. **"My positions" quiz**: user marks Yea/Nay on key bills → ranks members by alignment.
5. **Scheduled ingest** via a Reguard dashboard routine (see root `CLAUDE.md` for routines API), e.g. daily.
6. Charts (vote timeline, party-line scatter) — use the `dataviz` skill conventions.
7. Extend history before Nov 2024 by adding rows to `config.SESSIONS` and lowering `MIN_DATE`.
8. Candidates who are not currently in Congress (challengers) have no votes; consider state-legislature sources only if wanted.
