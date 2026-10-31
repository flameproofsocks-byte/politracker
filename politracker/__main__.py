"""CLI: python -m politracker {ingest|serve}"""
import argparse

from . import config


def main():
    p = argparse.ArgumentParser(prog="politracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="download + load votes into the database")
    ing.add_argument("--refresh", action="store_true",
                     help="re-download crosswalk and re-store every vote (default: only new votes)")
    sub.add_parser("bios", help="reload politician profile facts (age, terms, social links) from congress-legislators")
    bl = sub.add_parser("bills", help="fetch bill details (topic, sponsor, law status, brief summary) from Congress.gov")
    bl.add_argument("--limit", type=int, help="max bills to fetch this run")
    sub.add_parser("daily", help="ingest + bills; what the scheduled routine runs")
    srv = sub.add_parser("serve", help="run the local web UI")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=5057)
    args = p.parse_args()
    if args.cmd == "ingest":
        from . import ingest
        ingest.run(refresh=args.refresh)
    elif args.cmd == "bios":
        from . import bios, db
        conn = db.connect()
        print(f"bios: {bios.load_into_db(conn, force=True)} politician profiles loaded")
    elif args.cmd == "bills":
        from . import congress
        congress.run(limit=args.limit)
        congress.backfill_counts()
        congress.run_briefs(limit=args.limit)
    elif args.cmd == "daily":
        from . import congress, ingest
        ingest.run()
        congress.run()
        congress.backfill_counts()
        congress.run_briefs()
    else:
        from .web import app
        print(f"DB: {config.DB_PATH}")
        app.run(host=args.host, port=args.port)


main()
