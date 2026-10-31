# Database schema (SQLite: `data/politracker.db`)

Authoritative definition: `SCHEMA` string in `politracker/db.py`. Keep this file in sync.

## `members`
| column | notes |
|--------|-------|
| `member_id` PK | bioguide id (`C000127`). `LIS-S428` only if the crosswalk lacked the Senator |
| `full_name` | from crosswalk (e.g. "Maria Cantwell"); falls back to the XML name |
| `party` | latest seen: `D` / `R` / `I` |
| `state`, `chamber` | latest seen; chamber is `House` or `Senate` |

## `votes` (one row per roll call)
| column | notes |
|--------|-------|
| `vote_id` PK | `H-{year}-{NNN}` (House) or `S-{congress}-{session}-{NNNNN}` (Senate) |
| `chamber`, `congress`, `session`, `roll_num` | |
| `vote_date` | ISO `YYYY-MM-DD` |
| `question` | e.g. "On Passage", "On the Cloture Motion" |
| `bill_id` | normalized `HR 1`, `S 5`, `SJRES 55`; NULL for nominations/quorum/etc. |
| `title` | best available description of the bill/nomination |
| `result` | "Passed", "Failed", "Agreed to", … (source text) |
| `is_passage` | 1 if the vote is on the measure itself (heuristic, see `PASSAGE_RE` in parse.py) |
| `yeas`, `nays`, `present`, `not_voting` | official totals (verified to match `member_votes` counts for Yea) |
| `source_url` | link to original XML |

## `member_votes` (one row per member per roll call) — PK (`vote_id`, `member_id`)
| column | notes |
|--------|-------|
| `position` | normalized: `Yea` \| `Nay` \| `Present` \| `Not Voting` \| `Other` (Speaker elections) |
| `position_raw` | exact source text ("Aye", "No", candidate name…) |
| `party` | party at time of vote (members can switch) |

## `bills` (from Congress.gov; one row per bill voted on) — PK (`congress`, `bill_id`)
| column | notes |
|--------|-------|
| `bill_id` | same normalized form as `votes.bill_id` (`HR 1`, `SJRES 55`) — join on (`congress`, `bill_id`) |
| `title` | official/display title; NULL when `status='not_found'` |
| `policy_area` | Congress.gov top-level topic, e.g. `Health`, `Armed Forces and National Security` |
| `sponsor_id` / `sponsor_name` / `sponsor_party` | sponsor bioguide id (joins to `members.member_id`) |
| `introduced_date`, `latest_action_date`, `latest_action_text` | |
| `became_law`, `law_number` | 1 + `119-21` style Public Law number |
| `status` | `ok` \| `not_found` |
| `url` | congress.gov page |
| `brief` / `brief_source` | <=260-char plain summary for the Summary column; source `crs` (lead of the CRS summary) or `ai` (the "WHAT IT DOES" line of a Process summary; wins over `crs`) |
| `cosponsor_count` / `amendment_count` / `action_count` | Congress.gov counts used by the Top bills interest score (NULL until loaded; `congress.backfill_counts()` fills them from the raw JSON cache) |
| `brief_checked` | ISO date we last looked for a CRS summary (bills without one are re-checked after 14 days) |
Empty until `python -m politracker bills` has run with an API key. Only `policy_area` is stored as topic;
finer-grained `legislativeSubjects` (separate API call per bill) are a roadmap item.

## `bill_summaries` (AI-written, on-demand) — PK (`congress`, `bill_id`)
| column | notes |
|--------|-------|
| `summary` | plain text in a fixed shape: WHAT IT DOES / KEY PROVISIONS / WHO IT AFFECTS / NOTABLE |
| `source` | what it was written from, e.g. `CRS summary (Passed Senate)` or `Bill text (Engrossed in House)` |
| `truncated` | 1 if the source exceeded `SUMMARY_MAX_CHARS` and was cut (shown in the UI) |
| `model`, `created_at` | provenance. Delete a row to force regeneration |
Rows exist only for bills where someone clicked **Process summary**; independent of the `bills` table.

## `member_bios` (one row per member) — PK `member_id`
| column | notes |
|--------|-------|
| `birthday`, `gender` | from congress-legislators |
| `terms_json` | JSON list of Congress terms: `type` (rep/sen), `start`, `end`, `state`, `district`, `party`, `caucus`, `class`, `state_rank`, `url`, `phone`, `address`, `contact_form` |
| `social_json` | `{twitter, facebook, youtube, youtube_id, instagram...}` (can be `{}`) |
| `ids_json` | `{bioguide, govtrack, opensecrets, ballotpedia, cspan, votesmart, wikipedia, wikidata}` |
| `wikipedia_title`, `wikidata_id` | lookup keys for the on-demand fetch |
| `wiki_json`, `wiki_fetched` | cached Wikipedia intro + Wikidata facts (`extract`, `description`, `url`, `birth_place`, `education`, `occupations`, `died`), refreshed after 30 days |

## `video_briefs` (one row per politician the user started a brief for) — PK `member_id`
| column | notes |
|--------|-------|
| `blurb` | the facts blurb as edited by the user; NULL = still auto-generated from the current picks |
| `script` | the user's narration script, verbatim (NULL/empty = none) |
| `hook`, `outro` | text for the first / last slide (NULL = defaults "How does <name> actually vote?" / "Check the record before you decide.") |
| `for_json`, `against_json` | JSON `[[congress, bill_id], ...]` featured bills (NULL = automatic top picks; `[]` = none on purpose) |
| `updated_at` | ISO timestamp (UTC) |

## Handy queries
```sql
-- How did a member vote on passage of bills mentioning "tax"?
SELECT v.vote_date, v.bill_id, v.title, mv.position
FROM member_votes mv JOIN votes v USING (vote_id)
WHERE mv.member_id='C000127' AND v.is_passage=1 AND v.title LIKE '%tax%'
ORDER BY v.vote_date DESC;
```
Note: the same bill can appear in several votes (House passage, Senate passage, cloture, amendments).
Always join votes→bills on both `congress` and `bill_id`.
