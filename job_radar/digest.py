"""`digest`: the shortlist of fresh, in-scope postings as digest/YYYY-MM-DD.json and digest/YYYY-MM-DD.md.

Selection: title_match = 1, no excluded_reason, Status new, Relevance in `digest.levels`, and published within
`gates.freshness_hours` of the export time (freshness.py: a date-only posted date counts from 00:00 in the
configured time zone; a missing date is not fresh). Sorted by Relevance, then score, then newest first; cut at
`digest.limit`. The JSON keeps the counts of everything left out and why, so the cut can be audited.
"""
import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .gates import freshness

LEVEL_ORDER = {"High": 0, "Medium": 1, "Low": 2}
FIELDS = ("job_id", "title", "company", "location", "work_mode", "relocation_flag", "visa_flag", "posted_date",
          "relevance", "relevance_score", "relevance_why", "signals", "url", "apply_url", "description",
          "excluded_reason", "title_match", "status")
FLAG = {1: "yes", -1: "no", 0: "not mentioned", None: "unknown"}


def clean_text(text):
    t = re.sub(r"[ \t\r\f\v ]+", " ", text or "")
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def select(con, cfg, now):
    dg, hours, tz = cfg["digest"], cfg["gates"]["freshness_hours"], cfg["timezone"]
    rows, dropped = [], {"not_title_match": 0, "excluded": 0, "status": 0, "relevance": 0, "not_fresh": 0}
    for r in con.execute(f"SELECT {', '.join(FIELDS)} FROM jobs"):
        job = dict(zip(FIELDS, r))
        if job["title_match"] != 1:
            dropped["not_title_match"] += 1
        elif job["excluded_reason"]:
            dropped["excluded"] += 1
        elif (job["status"] or "new") != "new":
            dropped["status"] += 1
        elif job["relevance"] not in dg["levels"]:
            dropped["relevance"] += 1
        elif not freshness.is_fresh(job["posted_date"], now, hours, tz):
            dropped["not_fresh"] += 1
        else:
            rows.append(job)
    rows.sort(key=lambda j: (LEVEL_ORDER.get(j["relevance"], 9), -(j["relevance_score"] or 0),
                             -_ts(j["posted_date"], tz), j["job_id"]))
    return rows, dropped


def _ts(posted, tz):
    start = freshness.published_at(posted, tz)
    return start.timestamp() if start else 0


def to_item(job, chars):
    return {
        "job_id": job["job_id"], "title": job["title"], "company": job["company"], "location": job["location"],
        "work_mode": job["work_mode"], "relocation": FLAG.get(job["relocation_flag"]), "visa": FLAG.get(job["visa_flag"]),
        "posted": job["posted_date"], "relevance": job["relevance"], "score": job["relevance_score"],
        "why": job["relevance_why"], "signals": [s for s in (job["signals"] or "").split(",") if s],
        "link": job["url"], "apply_link": job["apply_url"], "description": clean_text(job["description"])[:chars],
    }


def build(con, cfg, date=None, now=None, limit=None):
    now = now or datetime.now(timezone.utc)
    date = date or now.astimezone(ZoneInfo(cfg["timezone"])).date().isoformat()
    limit = cfg["digest"]["limit"] if limit is None else limit
    rows, dropped = select(con, cfg, now)
    items = [to_item(j, cfg["digest"]["description_chars"]) for j in rows[:limit]]
    return {
        "date": date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end": now.isoformat(timespec="minutes"),
        "selection": (f"published within {cfg['gates']['freshness_hours']} h of window_end, Relevance in "
                      f"{', '.join(cfg['digest']['levels'])}, Status new, no gate exclusion; sorted by Relevance, "
                      "score, newest first"),
        "eligible": len(rows), "included": len(items), "omitted": len(rows) - len(items),
        "dropped": dropped, "rows": items,
    }


def markdown(payload):
    lines = [f"# Job radar digest, {payload['date']}", "",
             f"{payload['included']} postings (eligible {payload['eligible']}, omitted by the limit "
             f"{payload['omitted']}). Window ends {payload['window_end']}.", "",
             f"Selection: {payload['selection']}.", ""]
    if not payload["rows"]:
        lines += ["No posting passed every gate in this window.", ""]
    for level in ("High", "Medium", "Low"):
        items = [r for r in payload["rows"] if r["relevance"] == level]
        if not items:
            continue
        lines += [f"## {level} ({len(items)})", ""]
        for r in items:
            link = r["apply_link"] or r["link"]
            title = f"[{r['title']}]({link})" if link else r["title"]
            meta = [r["location"] or "location not stated", r["work_mode"], f"posted {r['posted']}",
                    f"relocation {r['relocation']}", f"visa {r['visa']}"]
            if r["signals"]:
                meta.append("signals: " + ", ".join(r["signals"]))
            lines += [f"- **{r['company'] or 'Unknown company'}**: {title}", f"  - {' · '.join(meta)}",
                      f"  - {r['why']}"]
        lines.append("")
    d = payload["dropped"]
    lines += ["## Left out", "", "| Reason | Postings |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in d.items()]
    return "\n".join(lines) + "\n"


def write(payload, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, payload["date"])
    for path, text in ((base + ".json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
                       (base + ".md", markdown(payload))):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    return base + ".json", base + ".md"
