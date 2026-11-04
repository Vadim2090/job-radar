"""`collect`: fetch every enabled source, pre-filter, store, classify.

Pre-filter at fetch time (cheap, keeps the database small): the title must match `gates.title_match` and the
location must not be entirely in a blocked area. Every other gate runs in `classify`, so a config change takes
effect on stored postings without fetching again.
"""
import sys
from collections import Counter
from datetime import datetime, timezone

from . import dedupe, sources, store
from .gates import freshness
from .classify import Gates, classify_all


def well_formed(row):
    """job_id and title are strings; company, location, description and URLs are strings or empty."""
    if not isinstance(row, dict) or not isinstance(row.get("job_id"), str) or not isinstance(row.get("title"), str):
        return False
    return all(row.get(k) is None or isinstance(row.get(k), str)
               for k in ("company", "location", "description", "url", "apply_url"))


def make_keep(gates):
    def keep(row, stats):
        if not gates.title.search(row.get("title") or ""):
            return False
        stats["title_match"] += 1
        if gates.region_blocked(row.get("location")):
            return False
        stats["kept"] += 1
        return True
    return keep


def source_of(job_id):
    return job_id.split(":", 1)[0]


def ingest(rows, con, cfg, gates, dry_run=False, now=None):
    """Store new and refreshed postings; aggregator duplicates become sightings on the existing posting."""
    bad = [r for r in rows if not well_formed(r)]
    if bad:
        print(f"[ingest] malformed rows skipped: {len(bad)}", file=sys.stderr)
    unique = {r["job_id"]: r for r in rows if well_formed(r)}
    known = {r[0] for r in con.execute("SELECT job_id FROM jobs")}
    deduped = dedupe.dedupe_freehire(list(unique.values()), con, batch=unique.values())
    new = [r for jid, r in unique.items() if jid not in known and not r.get("dup_of")]
    hours = cfg["gates"]["freshness_hours"]
    fresh = sum(freshness.is_fresh(r["posted"].isoformat() if r.get("posted") else None, now, hours)
                for r in unique.values())
    summary = {"total": len(unique), "new": len(new), "fresh": fresh,
               "per_source": dict(Counter(source_of(j) for j in unique)), "aggregator_duplicates": deduped}
    if dry_run:
        return summary, new
    ts = store.now_iso()
    from .gates.authorization import flags
    late = []
    for row in unique.values():
        src = source_of(row["job_id"])
        if src == "fh" and row.get("dup_of"):
            late.append(row)
            continue
        store.upsert_job(con, row, flags(row.get("description")), ts)
        if src == "fh":
            store.upsert_sighting(con, row["job_id"], "freehire", row.get("query"), row.get("location_query"), 1)
            if row.get("note"):
                con.execute("UPDATE jobs SET note=? WHERE job_id=? AND note IS NULL", (row["note"], row["job_id"]))
        elif src == "workable":
            store.upsert_sighting(con, row["job_id"], "workable", row.get("query"), None, 1)
        else:
            store.upsert_sighting(con, row["job_id"], f"ats_{src}", "companies", row.get("company"), 1)
    for row in late:
        if con.execute("SELECT 1 FROM jobs WHERE job_id=?", (row["dup_of"],)).fetchone():
            store.upsert_sighting(con, row["dup_of"], "freehire", row.get("query"), row.get("location_query"), 1)
    con.commit()
    classify_all(con, cfg, gates=gates)
    return summary, new


def run(cfg, dry_run=False, refresh_boards=False, only=None, out=sys.stdout):
    """Fetch, ingest and classify. `only` limits the sources ({'ats', 'workable', 'freehire'})."""
    gates = Gates(cfg)
    keep = make_keep(gates)
    enabled = {k for k, v in cfg["sources"].items() if v.get("enabled")}
    if only:
        enabled &= set(only)
    rows = []
    if "ats" in enabled and cfg["companies"]:
        boards = sources.resolve_boards(cfg, refresh=refresh_boards, save=not dry_run)
        rows += sources.collect_ats(boards, cfg, keep)
    if "workable" in enabled and cfg["sources"]["workable"]["queries"]:
        rows += sources.collect_workable(cfg, keep)
    if "freehire" in enabled and cfg["sources"]["freehire"]["keywords"]:
        rows += sources.collect_freehire(cfg, keep)
    if dry_run:
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.executescript(store.SCHEMA)
        try:
            real = sqlite3.connect(f"file:{cfg['database']}?mode=ro", uri=True)
            for r in real.execute("SELECT job_id, company, title, location, url, apply_url, posted_date FROM jobs"):
                con.execute("INSERT INTO jobs(job_id, company, title, location, url, apply_url, posted_date) "
                            "VALUES (?,?,?,?,?,?,?)", r)
            real.close()
        except sqlite3.Error:
            pass   # no database yet: every posting is new
    else:
        con = store.connect(cfg["database"])
    summary, new = ingest(rows, con, cfg, gates, dry_run=dry_run, now=datetime.now(timezone.utc))
    con.close()
    print(f"[collect] {'DRY RUN ' if dry_run else ''}total={summary['total']} new={summary['new']} "
          f"fresh_{cfg['gates']['freshness_hours']}h={summary['fresh']} per_source={summary['per_source']} "
          f"aggregator_duplicates={summary['aggregator_duplicates']}", file=out)
    if dry_run:
        for row in sorted(new, key=lambda r: r.get("posted") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
            posted = row["posted"].isoformat(timespec="minutes") if row.get("posted") else "?"
            print(f"[new] {row['job_id']} | {posted} | {row.get('company')} | {row.get('title')} | {row.get('location')}",
                  file=out)
    return summary
