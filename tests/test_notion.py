import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from job_radar import classify, notion
from tests.helpers import add_job, cfg, memory_db

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
LONG = "We build B2B software for finance teams and run experiments across the whole funnel. " * 20


def page(pid, job_id, status="new", relevance="High", why=None, title="Head of Growth"):
    return {"id": pid, "archived": False, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Job ID": {"type": "rich_text", "rich_text": [{"plain_text": job_id}]},
        "Status": {"type": "select", "select": {"name": status} if status else None},
        "Relevance": {"type": "select", "select": {"name": relevance}},
        "Relevance why": {"type": "rich_text", "rich_text": [{"plain_text": why if why is not None else f"{relevance} = 9"}]},
    }}


class FakeNotion:
    def __init__(self, pages=()):
        self.pages = {p["id"]: p for p in pages}
        self.calls = []
        self.n = 0

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/query"):
            live = [p for p in self.pages.values() if not p.get("archived")]
            return {"results": live, "has_more": False}
        if method == "GET" and path.startswith("/databases/"):
            return {"properties": {"Name": {"type": "title"}, "Job ID": {"type": "rich_text"}}}
        if method == "GET" and path.startswith("/pages/"):
            return self.pages[path.split("/")[2]]
        if method == "POST" and path == "/pages":
            self.n += 1
            return {"id": f"new-{self.n}"}
        if method == "PATCH" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            if "archived" in body and pid in self.pages:
                self.pages[pid]["archived"] = body["archived"]
            return {"id": pid}
        return {}

    def writes(self, method, prefix):
        return [(p, b) for m, p, b in self.calls if m == method and p.startswith(prefix)]


def db():
    c = cfg()
    con = memory_db()
    add_job(con, "fresh", "Head of Growth", description=LONG, posted="2026-11-04T09:00:00+00:00")
    add_job(con, "stale", "Head of Growth", company="Other", description="B2B. Experiments.", posted="2026-10-20T09:00:00+00:00")
    add_job(con, "low", "Marketing Manager", company="Third", description="Events.", posted="2026-11-04T09:00:00+00:00")
    add_job(con, "dup-a", "Growth Lead (Germany)", company="Dup", description=LONG, posted="2026-11-03T09:00:00+00:00")
    add_job(con, "dup-b", "Growth Lead (Spain)", company="Dup", description=LONG, posted="2026-11-04T10:00:00+00:00",
            apply_url="https://apply.example.test/b")
    add_job(con, "excl", "GTM Recruiter", company="Rec", posted="2026-11-04T09:00:00+00:00")
    add_job(con, "excl2", "Growth Recruiter", company="Rec2", posted="2026-11-04T09:00:00+00:00")
    classify.classify_all(con, c)
    return c, con


class Merge(unittest.TestCase):
    def test_title_key_and_merge(self):
        self.assertEqual(notion.title_key("Growth Lead (Full Remote - Germany)"), "growth lead")
        c, con = db()
        rows = notion.merge(notion.scope_rows(con))
        heads = {r["job_id"]: r for r in rows}
        self.assertIn("dup-b", heads)   # has the apply link, so it is canonical
        self.assertEqual(heads["dup-b"]["merged_ids"], ["dup-a"])
        self.assertEqual(heads["dup-b"]["other_locations"], [])
        self.assertNotIn("excl", heads)

    def test_latest_posted_ignores_bogus_future(self):
        rows = [{"posted_date": "2026-10-20"}, {"posted_date": "2099-01-01T00:00:00+00:00"}]
        self.assertEqual(notion.latest_posted(rows), "2026-10-20")


class Sync(unittest.TestCase):
    def test_dry_run_plans_without_writing(self):
        c, con = db()
        api = FakeNotion([page("p-stale", "stale")])
        out = io.StringIO()
        counts = notion.sync(con, c, api, "db", dry_run=True, now=NOW, out=out)
        self.assertEqual((counts["create"], counts["update"]), (2, 1))   # fresh + dup-b; stale exists
        self.assertFalse([1 for m, _, _ in api.calls if m in ("PATCH",) or (m == "POST" and _ == "/pages")])
        self.assertIn("[create] fresh", out.getvalue())
        self.assertNotIn("[create] low", out.getvalue())   # Relevance Low is never created

    def test_create_update_keep_status_and_manual_relevance(self):
        c, con = db()
        existing = page("p-stale", "stale", status="shortlist", relevance="Medium", why="High = 9")   # hand-edited
        merged_page = page("p-dupa", "dup-a", status="applied")
        api = FakeNotion([existing, merged_page])
        counts = notion.sync(con, c, api, "db", now=NOW, out=io.StringIO())
        self.assertEqual(counts, {"create": 2, "update": 1, "trash": 1, "unchanged": 0})
        (path, body), = api.writes("PATCH", "/pages/p-stale")
        self.assertNotIn("Status", body["properties"])
        self.assertNotIn("Relevance", body["properties"])
        created = [b for p, b in api.writes("POST", "/pages")]
        titles = {b["properties"]["Job ID"]["rich_text"][0]["text"]["content"]: b for b in created}
        self.assertEqual(set(titles), {"fresh", "dup-b"})
        self.assertEqual(titles["dup-b"]["properties"]["Status"], {"select": {"name": "applied"}})   # carried from the merged page
        self.assertIn("Name", titles["fresh"]["properties"])   # the database's own title property
        self.assertTrue(api.pages["p-dupa"]["archived"])
        ids = dict(con.execute("SELECT job_id, notion_page_id FROM jobs WHERE notion_page_id IS NOT NULL"))
        self.assertEqual(set(ids), {"fresh", "dup-b", "stale"})
        api.calls.clear()
        api.pages[ids["fresh"]] = page(ids["fresh"], "fresh")
        api.pages[ids["dup-b"]] = page(ids["dup-b"], "dup-b", status="applied")
        counts = notion.sync(con, c, api, "db", now=NOW, out=io.StringIO())
        self.assertEqual(counts["unchanged"], 3)

    def test_rich_text_chunks(self):
        chunks = notion.rt("a" * 4500)
        self.assertEqual([len(x["text"]["content"]) for x in chunks], [2000, 2000, 500])


class Archive(unittest.TestCase):
    def test_archive_dry_run_apply_and_restore(self):
        c, con = db()
        with tempfile.TemporaryDirectory() as tmp:
            c["notion"]["log_dir"] = tmp
            api = FakeNotion([page("p-excl", "excl"), page("p-keep", "excl2", status="applied"), page("p-ok", "fresh")])
            log, n = notion.archive(con, c, api, "db", "role:", dry_run=True, out=io.StringIO())
            self.assertEqual((log, n), (None, 1))
            self.assertFalse(api.writes("PATCH", "/pages/"))
            out = io.StringIO()
            log, n = notion.archive(con, c, api, "db", "role:", out=out)
            self.assertEqual(n, 1)
            self.assertTrue(api.pages["p-excl"]["archived"])
            self.assertFalse(api.pages["p-keep"]["archived"])
            self.assertEqual(json.load(open(log))["pages"], [{"page_id": "p-excl", "job_id": "excl"}])
            self.assertEqual(con.execute("SELECT job_id FROM notion_archived").fetchall(), [("excl",)])
            self.assertEqual(notion.restore(con, api, log, out=io.StringIO()), 1)
            self.assertFalse(api.pages["p-excl"]["archived"])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM notion_archived").fetchone()[0], 0)

    def test_empty_reason_is_refused(self):
        c, con = db()
        with self.assertRaises(notion.NotionError):
            notion.archive(con, c, FakeNotion(), "db", " ")


class Client(unittest.TestCase):
    def test_client_from_env(self):
        c = cfg()
        os.environ.pop("NOTION_TOKEN", None)
        with self.assertRaises(notion.NotionError):
            notion.client_from(c)
        os.environ["NOTION_TOKEN"] = "test-token"
        try:
            api, dbid = notion.client_from(c)
            self.assertEqual(dbid, "db-test")
        finally:
            os.environ.pop("NOTION_TOKEN")


if __name__ == "__main__":
    unittest.main()


class CarryStatus(unittest.TestCase):
    def test_merged_status_never_downgrades_canonical(self):
        c, con = db()
        canonical = page("p-dupb", "dup-b", status="applied")
        merged_page = page("p-dupa", "dup-a", status="shortlist")
        api = FakeNotion([canonical, merged_page])
        notion.sync(con, c, api, "db", now=NOW, out=io.StringIO())
        for path, body in api.writes("PATCH", "/pages/p-dupb"):
            self.assertNotIn("Status", body.get("properties", {}))   # 'applied' stays
        self.assertTrue(api.pages["p-dupa"]["archived"])

    def test_merged_status_fills_canonical_new(self):
        c, con = db()
        canonical = page("p-dupb", "dup-b", status="new")
        merged_page = page("p-dupa", "dup-a", status="applied")
        api = FakeNotion([canonical, merged_page])
        notion.sync(con, c, api, "db", now=NOW, out=io.StringIO())
        (path, body), = api.writes("PATCH", "/pages/p-dupb")
        self.assertEqual(body["properties"]["Status"], {"select": {"name": "applied"}})
