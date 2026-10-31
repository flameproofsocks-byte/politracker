"""Short-video briefs: turn a politician's recorded votes into a fact blurb, a starter script and a sequence of slides.

Pure functions (no DB / Flask) so they are unit-tested with synthetic data. web.py gathers the facts; the presenter template renders `scenes()`.

Design rules (keep them when extending):
  * Everything shown on a slide or put in the auto blurb comes from recorded votes / local profile data. The sensational script is the
    USER's own text, stored verbatim; we only count its words and estimate its spoken length.
  * "Voted for / against" means the member's latest Yea / Nay on a vote on the measure itself (passage). Procedural votes are never used.
  * No gendered pronouns anywhere in generated text (we don't infer them); sentences use the name instead.
"""
import re

WORDS_PER_SECOND = 2.6          # comfortable narration pace; ~156 words per minute
TARGET_SECONDS = (15, 30)       # the video length the user is aiming for
CANDIDATES = 8                  # bills offered in each "voted for / against" picker
DEFAULT_PICKS = 3               # bills pre-selected for each
MAX_PICKS = 5                   # most the user may feature per side
GENERIC_TOPICS = {"Congress"}   # Congress.gov's catch-all policy area for procedural bills; says nothing about beliefs
NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
SCENE_SECONDS = {"hook": 3, "who": 3, "stats": 3, "for": 5, "against": 5, "stands": 4, "outro": 3}


# ---------------------------------------------------------------- small helpers

def short_title(*titles, limit=80):
    """A slide-sized bill name: the shortest usable candidate (bills' own titles are usually the clean short names), trimmed at a
    word boundary with an ellipsis if even that is too long."""
    options = [re.sub(r"\s+", " ", t).strip().rstrip(".") for t in titles if t and t.strip()]
    if not options:
        return ""
    best = min(options, key=len)
    if len(best) <= limit:
        return best
    return best[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def word_count(text):
    return len(re.findall(r"[\w'’-]+", text or ""))


def estimate_seconds(text):
    """Spoken length of `text` in whole seconds at WORDS_PER_SECOND."""
    return round(word_count(text) / WORDS_PER_SECOND)


def length_status(seconds):
    """'empty' | 'short' | 'good' | 'long' relative to TARGET_SECONDS."""
    lo, hi = TARGET_SECONDS
    if seconds <= 0:
        return "empty"
    return "short" if seconds < lo else "long" if seconds > hi else "good"


def first_last_name(full_name):
    """('Maria', 'Cantwell'): first token and last non-suffix token ('Gerald E. Connolly Jr.' -> 'Gerald', 'Connolly')."""
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "", ""
    core = [p for p in parts if p.lower() not in NAME_SUFFIXES] or parts
    return core[0], core[-1]


def a_an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def join_and(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


# ---------------------------------------------------------------- choosing what to feature

def dedupe_latest(votes):
    """One row per bill: the member's most recent vote on it. `votes` must be sorted newest first."""
    seen, out = set(), []
    for v in votes:
        key = (v["congress"], v["bill_id"])
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def pick_candidates(votes, position, scores, limit=CANDIDATES):
    """The most interesting bills the member voted `position` ('Yea'/'Nay') on, best first (score = the Top bills interest score)."""
    pool = [v for v in votes if v["position"] == position]
    return sorted(pool, key=lambda v: (-(scores.get((v["congress"], v["bill_id"])) or 0), -int(v["vote_date"].replace("-", ""))))[:limit]


def resolve_selection(saved_keys, votes, position, candidates):
    """Bills to feature: the user's saved picks that are still valid (still that position), in saved order; else the top DEFAULT_PICKS."""
    by_key = {(v["congress"], v["bill_id"]): v for v in votes if v["position"] == position}
    chosen = [by_key[tuple(k)] for k in (saved_keys or []) if tuple(k) in by_key][:MAX_PICKS]
    return chosen if saved_keys is not None else candidates[:DEFAULT_PICKS]


def topic_leanings(votes, top=3, min_votes=2, min_share=0.6):
    """Policy areas where the member MOSTLY votes Yea ('supports') or Nay ('opposes'): at least `min_votes` votes that way and at least
    `min_share` of their votes in that topic going that way, so one topic can't appear on both sides. -> {"supports": [(topic, count)], ...}"""
    tally = {}
    for v in votes:
        t = v.get("topic")
        if not t or t in GENERIC_TOPICS or v["position"] not in ("Yea", "Nay"):
            continue
        counts = tally.setdefault(t, [0, 0])
        counts[0 if v["position"] == "Yea" else 1] += 1

    def ranked(col):
        rows = [(t, c[col]) for t, c in tally.items() if c[col] >= min_votes and c[col] / (c[0] + c[1]) >= min_share]
        return sorted(rows, key=lambda r: (-r[1], r[0]))[:top]
    return {"supports": ranked(0), "opposes": ranked(1)}


# ---------------------------------------------------------------- text

def _bill_phrase(b):
    return f"{b['title']} ({b['tally']})" if b.get("tally") else b["title"]


def build_blurb(f):
    """The factual blurb (several short sentences, no pronouns). `f` is the facts dict assembled by web.video_facts()."""
    out = []
    party = f["party_adj"]
    if f.get("office"):
        line = f"{f['name']} is {a_an(party)} {party} {f['office']}"
        if f.get("since_year"):
            line += f", in Congress since {f['since_year']}"
        out.append(line + ".")
    elif f.get("since_year"):
        out.append(f"{f['name']} was {a_an(party)} {party} member of Congress from {f['since_year']} to {f.get('left_year') or 'recently'}.")
    else:
        out.append(f"{f['name']} is {a_an(party)} {party} member of Congress.")
    if f["for_bills"]:
        out.append(f"Voted for {join_and([_bill_phrase(b) for b in f['for_bills']])}.")
    if f["against_bills"]:
        out.append(f"Voted against {join_and([_bill_phrase(b) for b in f['against_bills']])}.")
    if f.get("party_pct") is not None:
        out.append(f"Sided with the {f['party_cmp']} on {f['party_pct']}% of {f['party_n']:,} Yea/Nay votes.")
    if f["supports"]:
        out.append(f"Mostly votes Yea on {join_and([t for t, _ in f['supports']])}.")
    if f["opposes"]:
        out.append(f"Mostly votes Nay on {join_and([t for t, _ in f['opposes']])}.")
    return " ".join(out)


SPOKEN_TITLE_MAX = 50           # longer official names are awkward to say aloud
OUTLINE_TIERS = ((2, 2), (1, 2), (1, 1))   # (bills per side, topics) to try, fullest first, until the outline fits TARGET_SECONDS


def _spoken(bills, n):
    """The bills to name in narration: short titles first, then the next-shortest, at most `n`, keeping the original order within a group."""
    short = [b for b in bills if len(b["title"]) <= SPOKEN_TITLE_MAX]
    rest = sorted((b for b in bills if len(b["title"]) > SPOKEN_TITLE_MAX), key=lambda b: len(b["title"]))
    return (short + rest)[:n]


def join_topics(topics):
    """'health and energy', or 'a and b, plus c' when a topic name itself contains 'and' (avoids 'x and y and z')."""
    if len(topics) > 1 and any(" and " in t for t in topics):
        return ", ".join(topics[:-1]) + ", plus " + topics[-1]
    return join_and(topics)


def _outline_lines(f, n_bills, n_topics):
    name, first = f["name"], f["first"] or f["name"]
    present = bool(f.get("office"))
    lines = [f"You've probably seen ads about {name}.", "And they all seem to tell a different story.",
             f"So let's see how {first} actually votes."]
    if f.get("since_year"):
        lines.append(f"{first} has been in Congress since {f['since_year']}." if present else
                     f"{first} served in Congress from {f['since_year']} to {f.get('left_year') or 'recently'}.")
    if f["for_bills"]:
        lines.append(f"{first} voted for {join_and([b['title'] for b in _spoken(f['for_bills'], n_bills)])}.")
    if f["against_bills"]:
        lines.append(("But voted" if f["for_bills"] else f"{first} voted")
                     + f" against {join_and([b['title'] for b in _spoken(f['against_bills'], n_bills)])}.")
    if f.get("party_pct") is not None:
        lines.append(f"{first} sides with the {f['party_cmp']} {f['party_pct']}% of the time.")
    if f["supports"]:
        topics = join_topics([t.lower() for t, _ in f["supports"][:n_topics]])
        lines.append(f"We can't know {first}'s future votes, but the record says {first} generally stands for {topics}." if present
                     else f"The record shows {first} generally stood for {topics}.")
    return lines


def build_outline(f):
    """A neutral starter script (one line per beat) following the usual structure, trimmed to fit TARGET_SECONDS where it can
    (fewer bills/topics are named if the fullest version runs long). The user adds the punch."""
    for n_bills, n_topics in OUTLINE_TIERS:
        text = "\n".join(_outline_lines(f, n_bills, n_topics))
        if estimate_seconds(text) <= TARGET_SECONDS[1]:
            break
    return text


# ---------------------------------------------------------------- slides

def scenes(f, hook=None, outro=None):
    """Ordered slide descriptions for the presenter. Empty sections are skipped; each slide has a `seconds` duration."""
    name = f["name"]
    out = [{"kind": "hook", "text": (hook or "").strip() or f"How does {name} actually vote?"},
           {"kind": "who"},
           {"kind": "stats"}]
    if f["for_bills"]:
        out.append({"kind": "for", "bills": f["for_bills"]})
    if f["against_bills"]:
        out.append({"kind": "against", "bills": f["against_bills"]})
    if f["supports"] or f["opposes"]:
        out.append({"kind": "stands", "supports": f["supports"], "opposes": f["opposes"]})
    out.append({"kind": "outro", "text": (outro or "").strip() or "Check the record before you decide."})
    for s in out:
        s["seconds"] = SCENE_SECONDS[s["kind"]]
    return out


def total_seconds(scene_list):
    return sum(s["seconds"] for s in scene_list)
