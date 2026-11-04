"""collect -> classify -> digest on a temporary database, no network."""
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from job_radar import classify, cli, collect, config, digest, store
from tests.helpers import add_job, cfg, memory_db

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def row(job_id, title, location="Berlin, Germany", description="B2B growth role. Run experiments.", hours_ago=2, **kw):
    posted = NOW - timedelta(hours=hours_ago) if hours_ago is not None else None
    return {"job_id": job_id, "title": title, "company": kw.pop("company", "Acme"), "location": location,
            "workplace": None, "description": description, "url": f"https://jobs.example.test/{job_id}",
            "apply_url": None, "posted": posted, **kw}


class Classify(unittest.TestCase):
    def test_excluded_reason_order_and_scope(self):
        c = cfg()
        con = memory_db()
        add_job(con, "a", "Head of Growth", description="B2B. Run experiments.")
        add_job(con, "b", "Go to Market Recruiter")
        add_job(con, "c", "Head of Growth", location="Mumbai, India")
        add_job(con, "d", "Head of Growth", location="New York, NY")
        add_job(con, "e", "Head of Growth", location="New York, NY", description="We offer a relocation package.")
        add_job(con, "f", "Head of Growth", description="Fluent German is required.")
        add_job(con, "g", "Head of Growth", description="You must have the right to work in the UK.")
        add_job(con, "h", "Account Manager")
        classify.classify_all(con, c)
        got = dict(con.execute("SELECT job_id, excluded_reason FROM jobs"))
        self.assertEqual(got, {"a": None, "b": "role: recruiter", "c": "geo: asia_africa",
                               "d": "geo: americas, no relocation", "e": None, "f": "language: German",
                               "g": "blocker: work authorization", "h": None})
        self.assertEqual(con.execute("SELECT title_match FROM jobs WHERE job_id='h'").fetchone()[0], 0)
        rel = con.execute("SELECT relevance, relevance_why FROM jobs WHERE job_id='a'").fetchone()
        self.assertEqual(rel[0], "High")
        self.assertIn("title 'head of growth' +6", rel[1])
        self.assertEqual(con.execute("SELECT signals FROM jobs WHERE job_id='e'").fetchone()[0], "Relocation")

    def test_work_mode_gate(self):
        c = cfg()
        c["gates"]["work_mode"]["allowed"] = ["remote", "unknown"]
        con = memory_db()
        add_job(con, "a", "Head of Growth", description="This is an onsite role.")
        add_job(con, "b", "Head of Growth", description="We are a fully remote company.")
        classify.classify_all(con, c)
        self.assertEqual(dict(con.execute("SELECT job_id, excluded_reason FROM jobs")),
                         {"a": "work mode: office", "b": None})


class Ingest(unittest.TestCase):
    def test_ingest_stores_classifies_and_dedupes(self):
        c = cfg()
        gates = classify.Gates(c)
        con = memory_db()
        fh = row("fh:dup", "Head of Growth", fh_source="lever", fh_external_id="acme:1", query="lever", location_query="kw")
        rows = [row("lever:acme:1", "Head of Growth"), row("lever:acme:2", "Growth Lead", hours_ago=48), fh,
                {"job_id": 5, "title": None}]
        summary, new = collect.ingest(rows, con, c, gates, now=NOW)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["new"], 2)
        self.assertEqual(summary["fresh"], 2)
        self.assertEqual(summary["aggregator_duplicates"], 1)
        self.assertEqual(sorted(r[0] for r in con.execute("SELECT job_id FROM jobs")), ["lever:acme:1", "lever:acme:2"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM sightings WHERE job_id='lever:acme:1'").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT relevance FROM jobs WHERE job_id='lever:acme:1'").fetchone()[0], "High")

    def test_dry_run_writes_nothing(self):
        c = cfg()
        con = memory_db()
        summary, new = collect.ingest([row("lever:acme:1", "Head of Growth")], con, c, classify.Gates(c), dry_run=True, now=NOW)
        self.assertEqual((summary["new"], len(new)), (1, 1))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_keep_prefilter(self):
        from collections import Counter
        keep = collect.make_keep(classify.Gates(cfg()))
        stats = Counter()
        self.assertTrue(keep(row("x", "Head of Growth"), stats))
        self.assertFalse(keep(row("x", "Accountant"), stats))
        self.assertFalse(keep(row("x", "Head of Growth", location="Singapore"), stats))
        self.assertTrue(keep(row("x", "Head of Growth", location="New York"), stats))   # relocation judged later

    def test_run_dry_run_with_fake_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg(tmp)
            out = io.StringIO()
            with mock.patch("job_radar.sources.resolve_boards", return_value={"Acme": {"provider": "lever", "slug": "acme"}}), \
                    mock.patch("job_radar.sources.collect_ats", return_value=[row("lever:acme:1", "Head of Growth")]), \
                    mock.patch("job_radar.sources.collect_workable", return_value=[row("workable:9", "Growth Lead")]):
                summary = collect.run(c, dry_run=True, out=out)
            self.assertEqual(summary["new"], 2)
            self.assertIn("[new] lever:acme:1", out.getvalue())
            self.assertFalse(os.path.exists(c["database"]))


class Digest(unittest.TestCase):
    def db(self):
        con = memory_db()
        add_job(con, "high", "Head of Growth", description="B2B. Experiments.", posted="2026-11-04T08:00:00+00:00")
        add_job(con, "med", "Growth Manager", description="B2B.", posted="2026-11-04T10:00:00+00:00")
        add_job(con, "low", "Marketing Manager", description="Events.", posted="2026-11-04T10:00:00+00:00")
        add_job(con, "old", "Head of Growth", posted="2026-11-01T08:00:00+00:00")
        add_job(con, "nodate", "Head of Growth", posted=None)
        add_job(con, "excl", "GTM Recruiter", posted="2026-11-04T10:00:00+00:00")
        add_job(con, "worked", "Head of Growth", posted="2026-11-04T10:00:00+00:00", status="applied")
        add_job(con, "dateonly", "Head of Growth", description="B2B.", posted="2026-11-04")
        return con

    def test_selection_and_files(self):
        c = cfg()
        con = self.db()
        classify.classify_all(con, c)
        payload = digest.build(con, c, now=NOW)
        self.assertEqual([r["job_id"] for r in payload["rows"]], ["high", "dateonly", "med"])
        self.assertEqual(payload["dropped"], {"not_title_match": 0, "excluded": 1, "status": 1, "relevance": 1, "not_fresh": 2})
        self.assertEqual(payload["date"], "2026-11-04")
        with tempfile.TemporaryDirectory() as tmp:
            js, md = digest.write(payload, tmp)
            self.assertEqual(json.load(open(js))["included"], 3)
            text = open(md).read()
            self.assertIn("# Job radar digest, 2026-11-04", text)
            self.assertIn("## High (2)", text)
            self.assertIn("| excluded | 1 |", text)

    def test_limit_and_empty(self):
        c = cfg()
        con = self.db()
        classify.classify_all(con, c)
        self.assertEqual(digest.build(con, c, now=NOW, limit=1)["omitted"], 2)
        empty = digest.build(memory_db(), c, now=NOW)
        self.assertIn("No posting passed every gate", digest.markdown(empty))


class Cli(unittest.TestCase):
    def test_digest_and_stats_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w") as f:
                f.write("gates:\n  title_match: [growth]\nprofile:\n  title: {growth: 5}\n  thresholds: {high: 5, medium: 2}\n")
            con = store.connect(os.path.join(tmp, "jobs.db"))
            add_job(con, "a", "Head of Growth", posted="2026-11-04T10:00:00+00:00")
            classify.classify_all(con, config.load(path))
            con.close()
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(cli.main(["-c", path, "digest", "--now", "2026-11-04T12:00:00+00:00"]), 0)
                self.assertEqual(cli.main(["-c", path, "stats"]), 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "digest", "2026-11-04.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "digest", "2026-11-04.md")))
            self.assertIn("in_scope: 1", out.getvalue())

    def test_missing_config(self):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(cli.main(["-c", "/nonexistent/config.yaml", "stats"]), 2)
        self.assertIn("config file not found", err.getvalue())


class Config(unittest.TestCase):
    def test_example_config_loads(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        c = config.load(os.path.join(here, "config.example.yaml"))
        self.assertEqual(c["gates"]["freshness_hours"], 24)
        self.assertTrue(os.path.isabs(c["database"]))
        classify.Gates(c)

    def test_bad_values_are_reported(self):
        for data in ({"gates": {"title_match": ["("]}}, {"gates": {"role_families": ["wizard"]}},
                     {"gates": {"regions": {"blocked": ["mars"]}}}, {"profile": {"title": {"x": "high"}}}):
            with self.assertRaises(config.ConfigError):
                config.load(data=data)


if __name__ == "__main__":
    unittest.main()
