"""Shared fixtures: an in-memory config and a database filled with invented postings."""
import copy

from job_radar import config, store

BASE = {
    "companies": ["Example Labs"],
    "sources": {"workable": {"queries": ["Head of Growth"], "locations": ["Germany"], "remote_locations": []}},
    "gates": {
        "title_match": ["growth", r"\bgtm\b", "marketing"],
        "regions": {"blocked": ["asia_africa", "australia"], "relocation_required": ["americas"]},
    },
    "signals": [{"name": "Relocation", "pattern": "relocation package"}],
    "profile": {"title": {"head of growth": 6, "growth": 2}, "description": {"b2b": 1, "experiment": 1},
                "thresholds": {"high": 7, "medium": 3}},
    "notion": {"enabled": True, "database_id": "db-test"},
}


def cfg(tmp=None, **over):
    data = copy.deepcopy(BASE)
    for k, v in over.items():
        data[k] = v
    c = config.load(data=data)
    if tmp:
        c["database"] = f"{tmp}/jobs.db"
        c["digest_dir"] = f"{tmp}/digest"
        c["boards_cache"] = f"{tmp}/boards.json"
        c["notion"]["log_dir"] = f"{tmp}/logs"
    return c


def memory_db():
    con = store.connect(":memory:")
    return con


def add_job(con, job_id, title, company="Acme", location="Berlin, Germany", description="B2B growth role.",
            posted="2026-11-04T09:00:00+00:00", **extra):
    cols = {"job_id": job_id, "title": title, "company": company, "location": location,
            "description": description, "posted_date": posted, "first_seen": posted, "last_seen": posted, **extra}
    con.execute(f"INSERT INTO jobs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))
