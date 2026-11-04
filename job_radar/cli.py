"""Command line: job-radar [--config FILE] collect | classify | digest | sync | archive | stats"""
import argparse
import os
import sys
from datetime import datetime, timezone

from . import __version__, collect, config, digest, notion, store
from .classify import classify_all


def cmd_collect(cfg, args):
    only = set(args.only.split(",")) if args.only else None
    collect.run(cfg, dry_run=args.dry_run, refresh_boards=args.refresh_boards, only=only)


def cmd_classify(cfg, args):
    con = store.connect(cfg["database"])
    n = classify_all(con, cfg)
    con.close()
    print(f"[classify] {n} postings")


def cmd_digest(cfg, args):
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    con = store.connect(cfg["database"])
    payload = digest.build(con, cfg, date=args.date, now=now, limit=args.limit)
    con.close()
    paths = digest.write(payload, args.out_dir or cfg["digest_dir"])
    print(f"[digest] {payload['date']}: eligible {payload['eligible']}, written {payload['included']} "
          f"-> {paths[0]}, {paths[1]} | dropped {payload['dropped']}")


def _notion(cfg):
    if not cfg["notion"]["enabled"]:
        raise SystemExit("[notion] disabled: set notion.enabled: true in the config")
    try:
        return notion.client_from(cfg)
    except notion.NotionError as exc:
        raise SystemExit(f"[notion] {exc}")


def cmd_sync(cfg, args):
    api, db = _notion(cfg)
    con = store.connect(cfg["database"])
    try:
        notion.sync(con, cfg, api, db, dry_run=args.dry_run)
    except notion.NotionError as exc:
        raise SystemExit(f"[notion] {exc}")
    finally:
        con.close()


def cmd_archive(cfg, args):
    api, db = _notion(cfg)
    con = store.connect(cfg["database"])
    try:
        if args.restore:
            notion.restore(con, api, args.restore)
        elif args.reason:
            notion.archive(con, cfg, api, db, args.reason, dry_run=args.dry_run)
        else:
            raise SystemExit("archive needs --reason PREFIX or --restore LOG")
    except notion.NotionError as exc:
        raise SystemExit(f"[notion] {exc}")
    finally:
        con.close()


def stats(con):
    one = lambda q: con.execute(q).fetchone()[0]
    out = {
        "postings": one("SELECT COUNT(*) FROM jobs"),
        "title_match": one("SELECT COUNT(*) FROM jobs WHERE title_match=1"),
        "in_scope": one("SELECT COUNT(*) FROM jobs WHERE title_match=1 AND excluded_reason IS NULL"),
        "excluded_by": dict(con.execute(
            "SELECT CASE WHEN instr(excluded_reason, ':') > 0 THEN substr(excluded_reason, 1, instr(excluded_reason, ':') - 1) "
            "ELSE excluded_reason END, COUNT(*) FROM jobs WHERE title_match=1 AND excluded_reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")),
        "relevance_in_scope": dict(con.execute(
            "SELECT relevance, COUNT(*) FROM jobs WHERE title_match=1 AND excluded_reason IS NULL GROUP BY 1")),
        "by_source": dict(con.execute("SELECT source, COUNT(DISTINCT job_id) FROM sightings GROUP BY source")),
        "by_region": dict(con.execute("SELECT region, COUNT(*) FROM jobs GROUP BY region")),
    }
    return out


def cmd_stats(cfg, args):
    con = store.connect(cfg["database"])
    for k, v in stats(con).items():
        print(f"{k}: {v}")
    con.close()


def parser():
    ap = argparse.ArgumentParser(prog="job-radar", description="Collect fresh postings from public job boards, "
                                 "gate and score them, write a daily digest.")
    ap.add_argument("--version", action="version", version=f"job-radar {__version__}")
    ap.add_argument("-c", "--config", default=os.environ.get("JOB_RADAR_CONFIG", "config.yaml"),
                    help="YAML config (default: ./config.yaml or $JOB_RADAR_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="fetch every enabled source, store and classify")
    c.add_argument("--dry-run", action="store_true", help="fetch and list new postings; write nothing")
    c.add_argument("--refresh-boards", action="store_true", help="probe every company's board again")
    c.add_argument("--only", help="comma list of sources: ats,workable,freehire")
    c.set_defaults(func=cmd_collect)
    sub.add_parser("classify", help="re-run every gate on stored postings").set_defaults(func=cmd_classify)
    d = sub.add_parser("digest", help="write digest/YYYY-MM-DD.json and .md")
    d.add_argument("--date", help="file date, default today in the configured time zone")
    d.add_argument("--now", help="ISO time the freshness window ends at (default: now)")
    d.add_argument("--limit", type=int, help="maximum postings (default: digest.limit)")
    d.add_argument("--out-dir", help="output folder (default: digest_dir)")
    d.set_defaults(func=cmd_digest)
    s = sub.add_parser("sync", help="create / update pages in a Notion database")
    s.add_argument("--dry-run", action="store_true", help="list what would change; write nothing")
    s.set_defaults(func=cmd_sync)
    a = sub.add_parser("archive", help="move pages of now-excluded postings to the Notion trash")
    a.add_argument("--reason", help="excluded_reason prefix, e.g. 'geo:' or 'role: sales'")
    a.add_argument("--restore", metavar="LOG", help="undo one archive run from its rollback log")
    a.add_argument("--dry-run", action="store_true", help="list what would be archived; write nothing")
    a.set_defaults(func=cmd_archive)
    sub.add_parser("stats", help="counts by gate, source, region and relevance").set_defaults(func=cmd_stats)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 2
    args.func(cfg, args)
    return 0
