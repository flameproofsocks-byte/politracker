"""Pure parsing functions: source XML/CSV text -> plain dicts. No network, no DB.

Both parsers return a dict shaped like:
    {"vote": {...columns of `votes`...}, "members": [{"member_key", "full_name", ...}],
     "cast": [{"member_key", "position", "position_raw", "party"}]}
`member_key` is ("bioguide", id) for House and ("lis", id) for Senate; ingest.py resolves
it to a final member_id via the legislators crosswalk.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

POSITIONS = {"yea": "Yea", "aye": "Yea", "nay": "Nay", "no": "Nay",
             "present": "Present", "not voting": "Not Voting"}


def norm_position(raw):
    return POSITIONS.get((raw or "").strip().lower(), "Other")


def norm_bill_id(text):
    """'H R 1' / 'H.R. 1' / 'S. 5' / 'H RES 12' -> 'HR 1' / 'S 5' / 'HRES 12'. None if not a bill."""
    if not text:
        return None
    m = re.match(r"^\s*((?:H|S)\.?\s*(?:J\.?\s*RES|CON\.?\s*RES|RES|R)?\.?)\s*(\d+)\s*$", text, re.I)
    if not m:
        return None
    return f"{re.sub(r'[^A-Za-z]', '', m.group(1)).upper()} {m.group(2)}"


# A "passage" vote is the vote on the measure itself (not procedure like cloture/recommit/previous question).
PASSAGE_RE = re.compile(
    r"^On Passage|and Pass\b|^On the (Joint|Concurrent) Resolution|^On Overriding the Veto|"
    r"^On Motion to Concur in the Senate Amendment|^On Agreeing to the Conference Report", re.I)


def is_passage(question, bill_id):
    return int(bool(bill_id and PASSAGE_RE.search(question or "")))


def _txt(el, path):
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def parse_house_vote(xml_text, year):
    """Parse one clerk.house.gov roll call file. Returns None if it isn't a valid roll call."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "rollcall-vote":
        return None
    md = root.find("vote-metadata")
    roll = int(_txt(md, "rollcall-num"))
    date = datetime.strptime(_txt(md, "action-date"), "%d-%b-%Y").date().isoformat()
    legis = _txt(md, "legis-num")
    tot = md.find("vote-totals/totals-by-vote")
    session = 1 if _txt(md, "session").startswith("1") else 2
    vote = {
        "vote_id": f"H-{year}-{roll:03d}", "chamber": "House",
        "congress": _int(_txt(md, "congress")), "session": session, "roll_num": roll,
        "vote_date": date, "question": _txt(md, "vote-question"),
        "bill_id": norm_bill_id(legis), "title": _txt(md, "vote-desc") or legis,
        "result": _txt(md, "vote-result"),
        "is_passage": is_passage(_txt(md, "vote-question"), norm_bill_id(legis)),
        "yeas": _int(_txt(tot, "yea-total")), "nays": _int(_txt(tot, "nay-total")),
        "present": _int(_txt(tot, "present-total")), "not_voting": _int(_txt(tot, "not-voting-total")),
    }
    members, cast = [], []
    for rv in root.findall("vote-data/recorded-vote"):
        leg = rv.find("legislator")
        key = ("bioguide", leg.get("name-id"))
        raw = _txt(rv, "vote")
        members.append({"member_key": key, "full_name": leg.get("unaccented-name") or (leg.text or ""),
                        "party": leg.get("party"), "state": leg.get("state"), "chamber": "House"})
        cast.append({"member_key": key, "position": norm_position(raw), "position_raw": raw,
                     "party": leg.get("party")})
    return {"vote": vote, "members": members, "cast": cast}


def parse_senate_vote(xml_text):
    """Parse one senate.gov roll call file. Returns None if it isn't a valid roll call."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "roll_call_vote":
        return None
    congress, session = int(_txt(root, "congress")), int(_txt(root, "session"))
    num = int(_txt(root, "vote_number"))
    date = datetime.strptime(_txt(root, "vote_date").split(",  ")[0].strip(), "%B %d, %Y").date().isoformat()
    doc = root.find("document")
    doc_name = _txt(doc, "document_name") if doc is not None else ""
    title = (_txt(doc, "document_short_title") or _txt(doc, "document_title")) if doc is not None else ""
    count = root.find("count")
    vote = {
        "vote_id": f"S-{congress}-{session}-{num:05d}", "chamber": "Senate",
        "congress": congress, "session": session, "roll_num": num, "vote_date": date,
        "question": _txt(root, "question") or _txt(root, "vote_question_text"),
        "bill_id": norm_bill_id(doc_name),
        "title": title or _txt(root, "vote_title") or _txt(root, "vote_document_text"),
        "result": _txt(root, "vote_result"),
        "is_passage": is_passage(_txt(root, "question"), norm_bill_id(doc_name)),
        "yeas": _int(_txt(count, "yeas")), "nays": _int(_txt(count, "nays")),
        "present": _int(_txt(count, "present")), "not_voting": _int(_txt(count, "absent")),
    }
    members, cast = [], []
    for m in root.findall("members/member"):
        key = ("lis", _txt(m, "lis_member_id"))
        raw = _txt(m, "vote_cast")
        name = f"{_txt(m, 'first_name')} {_txt(m, 'last_name')}".strip()
        members.append({"member_key": key, "full_name": name, "party": _txt(m, "party"),
                        "state": _txt(m, "state"), "chamber": "Senate"})
        cast.append({"member_key": key, "position": norm_position(raw), "position_raw": raw,
                     "party": _txt(m, "party")})
    return {"vote": vote, "members": members, "cast": cast}


def parse_senate_menu(xml_text):
    """Return the highest roll call number listed in a Senate vote_menu file (0 if none)."""
    root = ET.fromstring(xml_text)
    return max((int(v.findtext("vote_number")) for v in root.iter("vote")), default=0)
