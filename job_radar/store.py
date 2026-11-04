"""SQLite store: one `jobs` row per posting, one `sightings` row per source that saw it.

Gate verdicts live on the jobs row (region, excluded_reason and the evidence columns), so every number in a
digest or in `stats` can be traced back to the phrase that produced it.
"""
import html
import re
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  title TEXT, company TEXT, location TEXT, workplace TEXT, posted_date TEXT,
  description TEXT, apply_url TEXT, url TEXT,
  relocation_flag INTEGER, visa_flag INTEGER,
  region TEXT, title_match INTEGER, excluded_reason TEXT,
  blocker_phrase TEXT, lang_req TEXT, lang_evidence TEXT, swe_evidence TEXT, role_evidence TEXT,
  work_mode TEXT, work_mode_evidence TEXT, signals TEXT,
  relevance TEXT, relevance_score REAL, relevance_why TEXT,
  status TEXT DEFAULT 'new', notion_page_id TEXT, note TEXT,
  first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS sightings (
  job_id TEXT, source TEXT, query TEXT, location_query TEXT, rank INTEGER, seen_at TEXT,
  PRIMARY KEY (job_id, source, query, location_query)
);
CREATE INDEX IF NOT EXISTS idx_sightings_job ON sightings(job_id);
CREATE TABLE IF NOT EXISTS notion_state (job_id TEXT PRIMARY KEY, page_id TEXT, digest TEXT, synced_at TEXT);
CREATE TABLE IF NOT EXISTS notion_archived (job_id TEXT PRIMARY KEY, page_id TEXT, reason TEXT, archived_at TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    con = sqlite3.connect(path, timeout=60)
    con.executescript(SCHEMA)
    return con


def text_of(fragment):
    """Plain text of an HTML fragment: block tags become line breaks, entities decoded, whitespace collapsed."""
    if fragment is None:
        return None
    t = html.unescape(fragment)   # some feeds (Greenhouse) send escaped HTML
    t = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", t)
    t = re.sub(r"<li[^>]*>", "\n- ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    t = re.sub(r" *\n *", "\n", t)
    return t.strip()


def upsert_sighting(con, job_id, source, query, location_query, rank):
    con.execute("""INSERT INTO sightings(job_id, source, query, location_query, rank, seen_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(job_id, source, query, location_query) DO UPDATE SET
                     rank=MIN(rank, excluded.rank), seen_at=excluded.seen_at""",
                (job_id, source, query or "", location_query or "", rank, now_iso()))


def upsert_job(con, row, flags, ts):
    """Insert or refresh one posting. Description, apply URL and posted date are never blanked by a later feed."""
    posted = row.get("posted")
    posted_date = posted.isoformat() if hasattr(posted, "isoformat") else posted
    reloc, visa = flags
    con.execute("""INSERT INTO jobs(job_id,title,company,location,workplace,posted_date,description,
                 apply_url,url,relocation_flag,visa_flag,first_seen,last_seen)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(job_id) DO UPDATE SET title=excluded.title, company=excluded.company,
                 location=excluded.location, workplace=excluded.workplace,
                 posted_date=COALESCE(excluded.posted_date,jobs.posted_date),
                 description=COALESCE(excluded.description,jobs.description),
                 apply_url=COALESCE(excluded.apply_url,jobs.apply_url), url=COALESCE(excluded.url,jobs.url),
                 relocation_flag=excluded.relocation_flag, visa_flag=excluded.visa_flag,
                 last_seen=excluded.last_seen""",
                (row["job_id"], row.get("title"), row.get("company"), row.get("location"), row.get("workplace"),
                 posted_date, row.get("description"), row.get("apply_url"), row.get("url"), reloc, visa, ts, ts))
