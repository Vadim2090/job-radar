import json
import sqlite3
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from job_radar import dedupe, sources
from tests.helpers import cfg


class NoWait:
    def wait(self, provider=None):
        pass


class Response:
    status = 200

    def __init__(self, body=b"{}", headers=None):
        self.body, self.headers = body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Requests(unittest.TestCase):
    def test_public_request_has_no_authentication(self):
        with mock.patch("urllib.request.urlopen", return_value=Response()) as opened:
            sources.request_json("https://example.test", "job-radar-test", tries=1)
        headers = dict(opened.call_args.args[0].header_items())
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Cookie", headers)
        self.assertEqual(headers["User-agent"], "job-radar-test")

    def test_invalid_host_is_a_miss_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=UnicodeError("label too long")) as opened, \
                mock.patch("time.sleep") as slept:
            self.assertEqual(sources.request_json(sources.board_url("recruitee", "x" * 70), "ua", tries=2), (400, ""))
        self.assertEqual(opened.call_count, 1)
        slept.assert_not_called()

    def test_retry_on_429_then_success(self):
        err = urllib.error.HTTPError("u", 429, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[err, Response(b'{"ok": 1}')]), mock.patch("time.sleep"):
            self.assertEqual(sources.request_json("https://example.test", "ua"), (200, '{"ok": 1}'))


class Boards(unittest.TestCase):
    def test_slug_candidates(self):
        self.assertEqual(sources.slug_candidates("Acme, Inc."), ["acme"])
        self.assertEqual(sources.slug_candidates("Example Labs"), ["example-labs", "examplelabs"])
        self.assertEqual(sources.slug_candidates("Sample.io"), ["sample-io", "sampleio", "sample"])

    def test_board_shapes(self):
        self.assertTrue(sources.valid_board("ashby", json.dumps({"jobs": []})))
        self.assertTrue(sources.valid_board("recruitee", json.dumps({"offers": []})))
        self.assertTrue(sources.valid_board("lever", "[]"))
        self.assertFalse(sources.valid_board("lever", "{}"))
        self.assertFalse(sources.valid_board("greenhouse", "not json"))

    def test_resolve_caches_hits_and_misses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg(tmp, companies=["Example Labs", "Nobody Corp", {"name": "Fixed", "provider": "lever", "slug": "fixed"}])
            calls = []

            def fetch(url, ua, tries=4):
                calls.append(url)
                if "lever.co/v0/postings/example-labs" in url:
                    return 200, "[]"
                if "greenhouse" in url and "nobody" in url:
                    return 503, ""
                return 404, ""
            boards = sources.resolve_boards(c, fetch=fetch, throttle=NoWait())
            self.assertEqual(boards["Example Labs"]["provider"], "lever")
            self.assertEqual(boards["Fixed"], {"provider": "lever", "slug": "fixed"})
            self.assertNotIn("Nobody Corp", boards)
            cache = json.load(open(c["boards_cache"]))
            self.assertEqual(cache["Nobody Corp"]["tried"], ["ashby", "lever", "pinpoint", "recruitee"])   # 503 is not an answer
            calls.clear()
            sources.resolve_boards(c, fetch=fetch, throttle=NoWait())
            self.assertTrue(calls and all("greenhouse" in u for u in calls))   # only the untried provider again


class Normalize(unittest.TestCase):
    def test_ashby(self):
        row = sources.normalize_ats("ashby", "acme", "Acme", {
            "id": "job-1", "title": "Growth Lead", "location": "Berlin", "publishedAt": "2026-09-30T05:30:00Z",
            "descriptionHtml": "<p>Build growth.</p>", "jobUrl": "https://jobs.example.test/acme/job-1"})
        self.assertEqual(row["job_id"], "ashby:acme:job-1")
        self.assertEqual(row["posted"], datetime(2026, 9, 30, 5, 30, tzinfo=timezone.utc))
        self.assertEqual(row["description"], "Build growth.")

    def test_greenhouse_escaped_html(self):
        row = sources.normalize_ats("greenhouse", "acme", "Acme", {
            "id": 7, "title": "Head of Growth", "location": {"name": "London"}, "content": "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;",
            "absolute_url": "https://boards.example.test/7", "first_published": "2026-10-01T10:00:00-04:00"})
        self.assertEqual(row["description"], "Hello & welcome")
        self.assertEqual(row["posted"], datetime(2026, 10, 1, 14, tzinfo=timezone.utc))

    def test_lever_recruitee_pinpoint(self):
        lever = sources.normalize_ats("lever", "acme", "Acme", {"id": "x", "text": "Growth Lead", "createdAt": 1790000000000,
                                                               "categories": {"location": "Paris"}, "descriptionPlain": "Text"})
        self.assertEqual((lever["title"], lever["location"]), ("Growth Lead", "Paris"))
        self.assertIsNotNone(lever["posted"])
        self.assertIsNone(sources.normalize_ats("recruitee", "a", "A", {"status": "draft"})["job_id"])
        rec = sources.normalize_ats("recruitee", "a", "A", {"id": 1, "title": "T", "remote": True, "city": "Lisbon",
                                                           "country": "Portugal", "published_at": "2026-10-01 08:00:00 UTC"})
        self.assertEqual((rec["workplace"], rec["location"]), ("remote", "Lisbon, Portugal"))
        self.assertEqual(rec["posted"], datetime(2026, 10, 1, 8, tzinfo=timezone.utc))
        pin = sources.normalize_ats("pinpoint", "a", "A", {"id": 2, "title": "T", "workplace_type": "onsite",
                                                          "location": {"city": "Leeds"}})
        self.assertEqual((pin["workplace"], pin["posted"]), ("on_site", None))

    def test_parse_date_rejects_garbage(self):
        self.assertIsNone(sources.parse_date(1e100))
        self.assertIsNone(sources.parse_date("soon"))
        self.assertIsNone(sources.parse_date(""))


class Collect(unittest.TestCase):
    def keep(self, row, stats):
        return "growth" in (row.get("title") or "").lower()

    def test_ats_skips_failed_boards_and_bad_postings(self):
        boards = {"Acme": {"provider": "lever", "slug": "acme"}, "Down": {"provider": "ashby", "slug": "down"}}

        def fetch(url, ua, tries=4):
            if "down" in url:
                return 500, ""
            return 200, json.dumps([{"id": "1", "text": "Growth Lead"}, {"id": "2", "text": "Engineer"}, None])
        rows = sources.collect_ats(boards, cfg(), self.keep, fetch=fetch, throttle=NoWait())
        self.assertEqual([r["job_id"] for r in rows], ["lever:acme:1"])

    def test_workable_pages_and_dedupes(self):
        page = {"jobs": [{"id": f"w{i}", "title": "Growth Manager", "company": {"title": "Acme"},
                          "location": {"city": "Berlin", "countryName": "Germany"}, "created": "2026-11-01T10:00:00Z"}
                         for i in range(20)], "nextPageToken": "t2"}
        last = {"jobs": [{"id": "w0", "title": "Growth Manager"}, {"id": "w99", "title": "Growth Lead"}]}
        urls = []

        def fetch(url, ua, tries=4):
            urls.append(url)
            return 200, json.dumps(last if "pageToken" in url else page)
        c = cfg()
        c["sources"]["workable"]["pages"] = 3
        rows = sources.collect_workable(c, self.keep, fetch=fetch, throttle=NoWait())
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[0]["location"], "Berlin, Germany")
        self.assertEqual(len(urls), 2)


class Freehire(unittest.TestCase):
    RAW = {"public_slug": "head-of-growth-acme-abc", "source": "greenhouse", "external_id": "acme:123",
           "url": "https://boards.example.test/acme/jobs/123?utm_source=aggregator", "title": "Head of Growth",
           "company": "Acme GmbH", "countries": ["de"], "cities": ["Berlin"], "work_mode": "hybrid",
           "description": "text", "posted_at": "2026-10-15T07:00:00Z", "enrichment": {"summary": "Leads growth."}}

    def row(self, **over):
        row = sources.normalize_freehire(self.RAW, "Head of Growth")
        row.update(over)
        return row

    def test_mapping(self):
        row = self.row()
        self.assertEqual(row["job_id"], "fh:head-of-growth-acme-abc")
        self.assertEqual(row["location"], "Berlin, germany")
        self.assertIn("Leads growth.", row["note"])

    def test_ats_triple_url_and_title_tiers(self):
        index = dedupe.DedupeIndex()
        index.add("greenhouse:acme:123", "Acme", "Head of Growth", "Berlin", None, None, "2026-10-14T00:00:00+00:00")
        self.assertEqual(index.match(self.row()), ("greenhouse:acme:123", "ats"))
        index = dedupe.DedupeIndex()
        index.add("x1", "X", "Y", None, "https://www.Example.test/Jobs/1/", None, None)
        self.assertEqual(index.match(self.row(url="https://example.test/jobs/1?utm_source=a", fh_source="other")), ("x1", "url"))
        index = dedupe.DedupeIndex()
        index.add("x2", "Acme Inc", "Head of Growth", "Berlin, Germany", "https://other.test/2", None, "2026-10-01")
        self.assertEqual(index.match(self.row(fh_source="other", url="https://a.test/a")), ("x2", "title"))
        index = dedupe.DedupeIndex()
        index.add("x3", "Acme", "Head of Growth", "Paris, France", "https://other.test/3", None, "2026-10-01")
        self.assertIsNone(index.match(self.row(fh_source="other", url="https://a.test/a"))[0])

    def test_tracking_params_dropped_identity_kept(self):
        self.assertEqual(dedupe.canonical_url("https://e.test/j?b=2&a=1&utm_source=x&gclid=z"),
                         dedupe.canonical_url("https://e.test/j?a=1&b=2"))
        self.assertNotEqual(dedupe.canonical_url("https://e.test/j?id=1"), dedupe.canonical_url("https://e.test/j?id=2"))

    def test_rows_dedupe_each_other(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE jobs(job_id,company,title,location,url,apply_url,posted_date)")
        mk = lambda n, day: {"job_id": f"fh:{n}", "company": "Acme", "title": "Product Manager", "fh_countries": ["de"],
                             "location": "Berlin, germany", "url": f"https://{n}.test/{n}", "apply_url": None,
                             "posted": sources.parse_date(f"2026-10-{day}"), "fh_source": "other", "fh_external_id": n}
        rows = [mk("one", 15), mk("two", 16)]
        self.assertEqual(dedupe.dedupe_freehire(rows, con), 1)
        self.assertEqual((rows[1]["dup_of"], rows[1]["dedupe_by"]), ("fh:one", "title"))

    def test_429_sleeps_retry_after(self):
        err = urllib.error.HTTPError("u", 429, "x", {"Retry-After": "7"}, None)
        ok = Response(b"{}", {"X-RateLimit-Remaining": "500"})
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]), mock.patch("time.sleep") as slept:
            self.assertEqual(sources.freehire_get({"q": "x"}, "ua"), (200, {}))
        slept.assert_called_with(8)

    def test_collect_skips_malformed_records(self):
        c = cfg()
        c["sources"]["freehire"].update(keywords=["Head of Growth"], queries=[{"label": "all", "params": {}}])
        page = {"data": [self.RAW, {"public_slug": "bad", "cities": "Berlin"}, {"public_slug": "closed", "closed_at": "x"}],
                "meta": {"total": 3}}
        rows = sources.collect_freehire(c, lambda r, s: True, get=lambda params, ua: (200, page))
        self.assertEqual([r["job_id"] for r in rows], ["fh:head-of-growth-acme-abc"])


if __name__ == "__main__":
    unittest.main()
