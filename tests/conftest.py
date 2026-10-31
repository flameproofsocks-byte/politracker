"""Shared fixtures. `client` = Flask test client over a temp DB holding two real fixture votes
(House roll 200 GENIUS Act passage, Senate vote 1 cloture) so no test touches the user's real data."""
import pytest
from pathlib import Path

from politracker import config, db, ingest, parse

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "MIN_DATE", None)
    monkeypatch.setattr(config, "SAVED_PATH", tmp_path / "saved.json")      # tests must never touch the user's real picks
    conn = db.connect()
    for parsed in (parse.parse_house_vote((FIX / "house_roll200.xml").read_text(), 2025),
                   parse.parse_senate_vote((FIX / "senate_vote1.xml").read_text())):
        parsed["vote"]["source_url"] = "x"
        ingest._store(conn, parsed, {}, {})
    conn.commit()
    from politracker.web import app
    return app.test_client()
