"""Optional sync of the shortlist to a Notion database.

Token and database id come from the environment (names set in `notion.token_env` / `notion.database_id_env`) or
from `notion.database_id` in the config. Nothing here runs unless `notion.enabled` is true.

Sync rules:
  * Scope: title_match = 1 and no excluded_reason, minus postings whose page was archived by `archive`.
  * Same role, several postings: postings of one company whose titles match once trailing parentheticals are
    dropped ("Growth Lead (Germany)" / "(Spain)") AND whose descriptions are near-identical merge into one page.
    The canonical posting keeps the page; the others are listed in "Merged Job IDs" / "Other locations", and a
    merged posting that already had its own page has it moved to the trash (its Status carries over first).
  * Create: a new page only for a posting with Relevance in `notion.create_levels` that is fresh
    (published within `gates.freshness_hours`). Postings that already have a page are always updated.
  * Update: Status set in Notion is never overwritten; a Relevance edited by hand in Notion is kept.
  * A property hash per posting skips pages whose content did not change since the last sync.

Archive: `archive --reason PREFIX` moves to the trash every page whose posting is now excluded with a reason
starting with PREFIX, except pages whose Status is in `notion.keep_statuses`. It writes a rollback log first;
`archive --restore LOG` brings the pages back.
"""
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import store
from .gates import freshness

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
CHUNK = 2000
DUP_SIMILARITY = 0.6   # word-level SequenceMatcher ratio over the first 1,500 words
FLAG = {1: "Yes", -1: "No", 0: "Not mentioned"}
SCHEMA = {
    "Company": {"rich_text": {}},
    "Location": {"rich_text": {}},
    "Region": {"select": {}},
    "Work mode": {"select": {}},
    "Relocation": {"select": {}},
    "Visa": {"select": {}},
    "Posted": {"date": {}},
    "Link": {"url": {}},
    "Apply URL": {"url": {}},
    "Job ID": {"rich_text": {}},
    "Merged Job IDs": {"rich_text": {}},
    "Other locations": {"rich_text": {}},
    "Relevance": {"select": {"options": [{"name": n} for n in ("High", "Medium", "Low")]}},
    "Relevance why": {"rich_text": {}},
    "Status": {"select": {"options": [{"name": n} for n in ("new", "shortlist", "applied", "skip")]}},
    "Signals": {"multi_select": {}},
    "First seen": {"date": {}},
    "Last seen": {"date": {}},
}


class NotionError(RuntimeError):
    pass


class Client:
    """Minimal Notion REST client with backoff on 429 and 5xx."""

    def __init__(self, token, opener=urllib.request.urlopen, sleep=time.sleep):
        self.token, self.opener, self.sleep = token, opener, sleep

    def __call__(self, method, path, body=None, tries=5):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(tries):
            req = urllib.request.Request(API + path, method=method, data=data, headers={
                "Authorization": "Bearer " + self.token, "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json"})
            try:
                with self.opener(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="replace")[:300]
                if (e.code == 429 or e.code >= 500) and attempt < tries - 1:
                    # a 5xx on create may still have created the page: the caller re-checks by Job ID
                    if method == "POST" and path == "/pages" and e.code >= 500:
                        raise NotionError(f"{method} {path}: HTTP {e.code} {txt}")
                    self.sleep(min(float(e.headers.get("Retry-After") or 2 ** attempt), 30))
                    continue
                raise NotionError(f"{method} {path}: HTTP {e.code} {txt}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == tries - 1:
                    raise NotionError(f"{method} {path}: {e}")
                self.sleep(2 ** attempt)
        raise NotionError(f"{method} {path}: gave up after retries")


def client_from(cfg):
    n = cfg["notion"]
    token = os.environ.get(n["token_env"])
    db = os.environ.get(n.get("database_id_env") or "") or n.get("database_id")
    if not token or not db:
        raise NotionError(f"set ${n['token_env']} and notion.database_id (or ${n.get('database_id_env')})")
    return Client(token), db


# --- property helpers -----------------------------------------------------------------------------------------

def rt(text):
    """rich_text array, chunked at the 2,000 limit, which Notion counts in UTF-16 units."""
    chunks, cur, units = [], [], 0
    for ch in text or "":
        w = 2 if ord(ch) > 0xFFFF else 1
        if units + w > CHUNK:
            chunks.append("".join(cur))
            cur, units = [], 0
        cur.append(ch)
        units += w
    if cur:
        chunks.append("".join(cur))
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def sel(v):
    return {"select": {"name": v}} if v else {"select": None}


def date_prop(v):
    return {"date": {"start": v}} if v else {"date": None}


def plain(prop):
    if not prop:
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get(prop["type"]) or [])


def select_name(page, name):
    return ((page["properties"].get(name) or {}).get("select") or {}).get("name")


def props(row, title_prop="Title"):
    return {
        title_prop: {"title": rt(row["title"] or "(untitled)")},
        "Company": {"rich_text": rt(row["company"])},
        "Location": {"rich_text": rt(row["location"])},
        "Region": sel(row["region"]),
        "Work mode": sel(row["work_mode"] or "unknown"),
        "Relocation": sel(FLAG.get(row["relocation_flag"])),
        "Visa": sel(FLAG.get(row["visa_flag"])),
        "Posted": date_prop(row["posted_date"]),
        "Link": {"url": row["url"] or None},
        "Apply URL": {"url": row["apply_url"] or None},
        "Job ID": {"rich_text": rt(row["job_id"])},
        "Merged Job IDs": {"rich_text": rt(", ".join(row.get("merged_ids", [])))},
        "Other locations": {"rich_text": rt("; ".join(row.get("other_locations", [])))},
        "Relevance": sel(row["relevance"]),
        "Relevance why": {"rich_text": rt(row["relevance_why"])},
        "Status": sel(row["status"] or "new"),
        "Signals": {"multi_select": [{"name": n} for n in (row["signals"] or "").split(",") if n]},
        "First seen": date_prop(row["first_seen"]),
        "Last seen": date_prop(row["last_seen"]),
    }


def body_blocks(desc):
    if not desc:
        return []
    blocks = [{"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt("Job description")}}]
    for para in (p.strip() for p in desc.replace("\r", "").split("\n\n")):
        if para:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(para)[:100]}})
    return blocks


# --- rows and merging -----------------------------------------------------------------------------------------

ROW_COLS = ("job_id", "title", "company", "location", "region", "work_mode", "relocation_flag", "visa_flag",
            "posted_date", "url", "apply_url", "relevance", "relevance_why", "status", "signals", "first_seen",
            "last_seen", "description")


def scope_rows(con):
    q = (f"SELECT {', '.join(ROW_COLS)} FROM jobs WHERE title_match = 1 AND excluded_reason IS NULL "
         "AND job_id NOT IN (SELECT job_id FROM notion_archived)")
    return [dict(zip(ROW_COLS, r)) for r in con.execute(q)]


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def title_key(title):
    """Grouping key: trailing parentheticals dropped, so titles that differ only by the country are one role."""
    return norm(re.sub(r"(\s*\([^()]*\))+\s*$", "", title or "")) or norm(title)


def similar(a, b):
    wa, wb = norm(a).split()[:1500], norm(b).split()[:1500]
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio() >= DUP_SIMILARITY


def rank(r):
    """Canonical posting first: worked on, has an apply link, newest, then job id."""
    posted_key = int(re.sub(r"\D", "", r["posted_date"] or "0")[:14] or 0)
    return ((r["status"] or "new") == "new", not r["apply_url"], -posted_key, r["job_id"])


def latest_posted(cluster, tz="UTC"):
    """Posted date of the newest posting in a merged cluster, so a fresh duplicate is not hidden behind an older
    canonical row. Unparseable dates and dates beyond the clock-skew allowance lose to valid ones."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    def valid(r):
        age = freshness.age_hours(r["posted_date"], tz=tz)
        return freshness.published_at(r["posted_date"], tz) if age is not None and age >= -freshness.FUTURE_SKEW_HOURS else None

    best = max(cluster, key=lambda r: valid(r) or epoch)
    return best["posted_date"] if valid(best) else cluster[0]["posted_date"]


def merge(rows, tz="UTC"):
    groups = {}
    for r in rows:
        groups.setdefault((norm(r["company"]), title_key(r["title"])), []).append(r)
    out = []
    for members in groups.values():
        clusters = []
        for r in sorted(members, key=rank):
            for c in clusters:
                if similar(c[0]["description"], r["description"]):
                    c.append(r)
                    break
            else:
                clusters.append([r])
        for c in clusters:
            head, rest = dict(c[0]), c[1:]
            head["merged_ids"] = [r["job_id"] for r in rest]
            head["other_locations"] = sorted({r["location"] for r in rest if r["location"]} - {head["location"]})
            head["posted_date"] = latest_posted(c, tz)
            if not head["apply_url"]:
                head["apply_url"] = next((r["apply_url"] for r in rest if r["apply_url"]), None)
            out.append(head)
    return sorted(out, key=lambda r: r["posted_date"] or "", reverse=True)


def plan(rows, have, cfg, now=None):
    """(create, update, stale, carried). `have` maps Job ID -> existing page."""
    now = now or datetime.now(timezone.utc)
    n, hours, tz = cfg["notion"], cfg["gates"]["freshness_hours"], cfg["timezone"]
    merged = {m: r["job_id"] for r in rows for m in r["merged_ids"]}
    create = [r for r in rows if r["job_id"] not in have and r["relevance"] in n["create_levels"]
              and freshness.is_fresh(r["posted_date"], now, hours, tz)]
    update = [r for r in rows if r["job_id"] in have]
    keep = {r["job_id"] for r in create + update}
    stale = {j: p for j, p in have.items() if j in merged and merged[j] in keep}
    carried = {}
    for j, p in stale.items():
        st = select_name(p, "Status")
        canonical = have.get(merged[j])
        canon_st = select_name(canonical, "Status") if canonical else None
        # a worked-on Status moves to the canonical page only when that page has none yet (or "new"):
        # a Status the person already set on the canonical page is never overwritten or downgraded
        if st and st != "new" and (not canon_st or canon_st == "new"):
            carried[merged[j]] = st
    return create, update, stale, carried


def manual_relevance(page):
    """True when Relevance in Notion differs from the level the last sync wrote into Relevance why."""
    cur = select_name(page, "Relevance")
    why = plain(page["properties"].get("Relevance why"))
    return bool(cur) and not why.startswith(cur + " = ")


# --- Notion reads and writes ------------------------------------------------------------------------------------

def query_all(api, db, body=None):
    out, cur = [], None
    while True:
        b = dict(body or {}, page_size=100)
        if cur:
            b["start_cursor"] = cur
        r = api("POST", f"/databases/{db}/query", b)
        out += r["results"]
        if not r.get("has_more"):
            return out
        cur = r["next_cursor"]


def existing_pages(api, db):
    return {plain(p["properties"].get("Job ID")): p for p in query_all(api, db)
            if not p.get("archived") and not p.get("in_trash") and plain(p["properties"].get("Job ID"))}


def ensure_schema(api, db):
    """Add missing properties; returns the name of the database's title property."""
    info = api("GET", f"/databases/{db}")
    title = next(k for k, v in info["properties"].items() if v["type"] == "title")
    add = {k: v for k, v in SCHEMA.items() if k not in info["properties"]}
    if add:
        api("PATCH", f"/databases/{db}", {"properties": add})
        print(f"[notion] added properties {sorted(add)}")
    return title


def title_property(api, db):
    info = api("GET", f"/databases/{db}")
    return next(k for k, v in info["properties"].items() if v["type"] == "title")


def find_by_job_id(api, db, job_id):
    r = api("POST", f"/databases/{db}/query", {"filter": {"property": "Job ID", "rich_text": {"equals": job_id}}})
    return (r.get("results") or [None])[0]


def create_page(api, db, row, p):
    blocks = body_blocks(row["description"])
    body = {"parent": {"database_id": db}, "properties": p, "children": blocks[:100]}
    try:
        page = api("POST", "/pages", body)
    except NotionError:
        time.sleep(3)   # a 5xx may still have created the page: look it up before a second create
        page = find_by_job_id(api, db, row["job_id"]) or api("POST", "/pages", body)
    for i in range(100, len(blocks), 100):
        api("PATCH", f"/blocks/{page['id']}/children", {"children": blocks[i:i + 100]})
    return page


def sync(con, cfg, api, db, dry_run=False, now=None, out=sys.stdout):
    rows = merge(scope_rows(con), cfg["timezone"])
    have = existing_pages(api, db)
    create, update, stale, carried = plan(rows, have, cfg, now)
    print(f"[sync] in scope: {len(rows)} | pages: {len(have)} | create: {len(create)} | update: {len(update)} "
          f"| merged pages to trash: {len(stale)}", file=out)
    if dry_run:
        for r in create:
            print(f"[create] {r['job_id']} | {r['relevance']} | {r['company']} | {r['title']}", file=out)
        for j in stale:
            print(f"[trash] {j} (merged)", file=out)
        print("[sync] dry run, nothing written", file=out)
        return {"create": len(create), "update": len(update), "trash": len(stale), "unchanged": 0}
    title = ensure_schema(api, db)
    state = dict(con.execute("SELECT job_id, digest FROM notion_state"))
    counts = {"create": 0, "update": 0, "trash": 0, "unchanged": 0}
    for row in create + update:
        p = props(row, title)
        digest = hashlib.sha1(json.dumps({k: v for k, v in p.items() if k != "Last seen"},
                                         sort_keys=True).encode()).hexdigest()
        page = have.get(row["job_id"])
        if page and state.get(row["job_id"]) == digest and row["job_id"] not in carried:
            counts["unchanged"] += 1
            continue
        if row["job_id"] in carried:
            p["Status"] = sel(carried[row["job_id"]])
        if page:
            if row["job_id"] not in carried and select_name(page, "Status"):
                p.pop("Status")   # Status is owned by the person working the list
            if manual_relevance(page):
                p.pop("Relevance")
                p.pop("Relevance why")
            api("PATCH", f"/pages/{page['id']}", {"properties": p})
            counts["update"] += 1
        else:
            page = create_page(api, db, row, p)
            counts["create"] += 1
        con.execute("INSERT OR REPLACE INTO notion_state(job_id, page_id, digest, synced_at) VALUES (?,?,?,?)",
                    (row["job_id"], page["id"], digest, store.now_iso()))
        con.execute("UPDATE jobs SET notion_page_id=? WHERE job_id=?", (page["id"], row["job_id"]))
        con.commit()
    for j, p in stale.items():
        api("PATCH", f"/pages/{p['id']}", {"archived": True})
        counts["trash"] += 1
    print(f"[sync] created {counts['create']}, updated {counts['update']}, unchanged {counts['unchanged']}, "
          f"merged pages trashed {counts['trash']}", file=out)
    return counts


def archive(con, cfg, api, db, reason, dry_run=False, out=sys.stdout):
    """Trash pages whose posting is now excluded with a reason starting with `reason`. Rollback log first."""
    if not reason.strip():
        raise NotionError("--reason must not be empty")
    reasons = dict(con.execute("SELECT job_id, excluded_reason FROM jobs WHERE excluded_reason IS NOT NULL"))
    keep_statuses = set(cfg["notion"]["keep_statuses"])
    pages = list(existing_pages(api, db).items())
    drop = [(j, p) for j, p in pages if (reasons.get(j) or "").startswith(reason)
            and select_name(p, "Status") not in keep_statuses]
    kept = [(j, p) for j, p in pages if (reasons.get(j) or "").startswith(reason)
            and select_name(p, "Status") in keep_statuses]
    print(f"[archive] pages: {len(pages)}, excluded '{reason}*': {len(drop) + len(kept)}, to trash: {len(drop)}, "
          f"kept by Status: {len(kept)}", file=out)
    for j, p in drop:
        print(f"  trash: {j} | {reasons[j]}", file=out)
    if dry_run:
        print("[archive] dry run, nothing written", file=out)
        return None, len(drop)
    os.makedirs(cfg["notion"]["log_dir"], exist_ok=True)
    log = os.path.join(cfg["notion"]["log_dir"], f"archive_{time.strftime('%Y%m%d-%H%M%S')}_{time.time_ns() % 10**6:06d}.json")
    with open(log, "x", encoding="utf-8") as fh:   # exclusive: never overwrite an earlier rollback log
        json.dump({"reason": reason, "pages": [{"page_id": p["id"], "job_id": j} for j, p in drop]}, fh, indent=2)
    done = 0
    for j, p in drop:
        cur = api("GET", f"/pages/{p['id']}")
        if cur.get("archived") or cur.get("in_trash") or select_name(cur, "Status") in keep_statuses:
            print(f"[archive] changed since listing, left alone: {j}", file=out)
            continue
        api("PATCH", f"/pages/{p['id']}", {"archived": True})
        con.execute("INSERT OR REPLACE INTO notion_archived(job_id, page_id, reason, archived_at) VALUES (?,?,?,?)",
                    (j, p["id"], reasons[j], store.now_iso()))
        con.commit()
        done += 1
    print(f"[archive] trashed {done} of {len(drop)}; rollback log {log}", file=out)
    return log, done


def restore(con, api, log, out=sys.stdout):
    """Undo one archive run from its rollback log."""
    with open(log, encoding="utf-8") as f:
        entries = json.load(f)["pages"]
    for e in entries:
        api("PATCH", f"/pages/{e['page_id']}", {"archived": False})
        con.execute("DELETE FROM notion_archived WHERE job_id=?", (e["job_id"],))
    con.commit()
    print(f"[archive] restored {len(entries)} pages from {log}", file=out)
    return len(entries)
