import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from job_radar.gates import authorization, freshness, lang, region, swe, work_mode


class LanguageGate(unittest.TestCase):
    def test_required_languages(self):
        cases = {"Fluent German is required.": "German", "German (C1) and English.": "German",
                 "Verhandlungssicheres Deutsch in Wort und Schrift.": "German",
                 "You speak fluent French and English.": "French",
                 "Native or C2-level Dutch speaker.": "Dutch", "Professional proficiency in Spanish.": "Spanish"}
        for text, want in cases.items():
            self.assertEqual(lang.detect(text)[0], want, text)

    def test_nice_to_have_and_context_do_not_block(self):
        for text in ["German is a plus.", "French would be an advantage.", "Deutschkenntnisse von Vorteil.",
                     "Fluent English or German.", "Experience with the German market.",
                     "You will expand into German-speaking regions.", "We work in English."]:
            self.assertEqual(lang.detect(text), (None, None), text)

    def test_spoken_languages_are_not_reported(self):
        self.assertEqual(lang.detect("Fluent German is required.", spoken=["English", "German"]), (None, None))
        self.assertEqual(lang.detect("Fluent English and Russian required.")[0], "Russian")
        self.assertIsNone(lang.detect("Fluent English and Russian required.", spoken=["English", "Russian"])[0])

    def test_description_written_in_another_language(self):
        text = ("Wir suchen eine Person, die mit uns das Wachstum gestaltet. Du bist für die Planung und "
                "Umsetzung der Kampagnen verantwortlich und arbeitest eng mit dem Team zusammen. Wir bieten dir "
                "eine spannende Aufgabe in einem jungen Team und flache Hierarchien, die dich auf deinem Weg "
                "unterstützen und mit dir wachsen.")
        got, ev = lang.detect(text)
        self.assertEqual(got, "German")
        self.assertTrue(ev.startswith("description written in German"), ev)

    def test_evidence_is_capped(self):
        text = "x " * 100 + "Fluent German is required for this role " + "y " * 100
        self.assertLessEqual(len(lang.detect(text)[1]), lang.EVIDENCE_MAX)


class SoftwareEngineerGate(unittest.TestCase):
    def test_required_background_scores(self):
        desc = ("What you bring\n- 5+ years of experience as a software engineer.\n"
                "- Strong proficiency in Node.js and TypeScript.\n")
        hit, ev = swe.is_swe("GTM Engineer", desc)
        self.assertTrue(hit)
        self.assertIn("score 5", ev)

    def test_responsibilities_and_nice_to_have_do_not_score(self):
        desc = ("Responsibilities\n- Work with backend services and software engineers.\n"
                "Nice to have\n- TypeScript is a plus.\n")
        self.assertEqual(swe.is_swe("GTM Engineer", desc), (False, None))

    def test_one_strong_requirement_is_not_enough(self):
        self.assertFalse(swe.is_swe("Growth Lead", "Requirements\n- Strong proficiency in TypeScript.")[0])

    def test_alternative_background(self):
        desc = "Requirements\n- Experience as a software engineer or a similar technical role.\n- Backend experience required."
        self.assertFalse(swe.is_swe("Growth Lead", desc)[0])

    def test_title_alone_and_threshold_override(self):
        self.assertTrue(swe.is_swe("Senior Software Engineer, Growth", "")[0])
        rules = swe.load(threshold=10)
        self.assertFalse(swe.is_swe("Senior Software Engineer, Growth", "", rules)[0])


class WorkMode(unittest.TestCase):
    def mode(self, desc, loc=None, wp=None):
        return work_mode.detect(desc, loc, wp)[0]

    def test_phrases(self):
        for text in ["We are a fully remote company.", "This is a remote-first team.", "Location: Remote", "Berlin, Remote"]:
            self.assertEqual(self.mode(text), "remote", text)
        for text in ["We offer a hybrid working model.", "London, hybrid", "3 days a week in the office", "Berlin (Hybrid)"]:
            self.assertEqual(self.mode(text), "hybrid", text)
        for text in ["This is an onsite role.", "It is office first.", "Work type: on-site"]:
            self.assertEqual(self.mode(text), "office", text)

    def test_city_is_not_evidence_and_options_are_unknown(self):
        self.assertEqual(self.mode("Join our team in Berlin.", "Berlin, Germany"), "unknown")
        self.assertEqual(self.mode("Remote, hybrid, or office-based work is possible."), "unknown")
        self.assertEqual(self.mode("We offer hybrid cloud solutions to customers."), "unknown")
        self.assertEqual(self.mode("The role is office based 4 days a week and one day working remotely."), "hybrid")

    def test_workplace_field_is_the_fallback(self):
        self.assertEqual(self.mode("Nothing here.", "Madrid", "Hybrid"), "hybrid")
        self.assertEqual(self.mode("Nothing here.", "Madrid", "on-site"), "office")
        self.assertEqual(self.mode("Fully remote team.", "Madrid", "Hybrid"), "remote")
        self.assertEqual(self.mode(None, None, None), "unknown")


class Freshness(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

    def test_timestamps(self):
        self.assertAlmostEqual(freshness.age_hours("2026-11-01T13:00:00Z", self.NOW), 23.0)
        self.assertAlmostEqual(freshness.age_hours("2026-11-01T14:00:00+01:00", self.NOW), 23.0)
        self.assertAlmostEqual(freshness.age_hours("2026-11-01T13:00:00", self.NOW), 23.0)
        self.assertTrue(freshness.is_fresh("2026-11-01T13:00:00+00:00", self.NOW))
        self.assertFalse(freshness.is_fresh("2026-11-01T11:59:00+00:00", self.NOW))

    def test_date_only_counts_from_local_midnight(self):
        self.assertAlmostEqual(freshness.age_hours("2026-11-01", self.NOW), 36.0)
        self.assertAlmostEqual(freshness.age_hours("2026-11-02", self.NOW, tz="Europe/Berlin"), 13.0)
        self.assertEqual(freshness.published_at("2026-11-02", "Europe/Berlin"),
                         datetime(2026, 11, 2, tzinfo=ZoneInfo("Europe/Berlin")))

    def test_missing_and_future(self):
        for value in (None, "", "yesterday", 123):
            self.assertFalse(freshness.is_fresh(value, self.NOW), value)
        self.assertTrue(freshness.is_fresh("2026-11-02T12:30:00+00:00", self.NOW))
        self.assertFalse(freshness.is_fresh("2026-11-02T15:00:00+00:00", self.NOW))
        self.assertTrue(freshness.is_fresh("2026-11-01T00:00:00+00:00", self.NOW, hours=48))
        self.assertEqual(freshness.window_start(self.NOW, 24), self.NOW - timedelta(hours=24))


class Regions(unittest.TestCase):
    B, R = ["asia_africa", "australia"], ["americas"]

    def reason(self, loc, reloc=False):
        return region.region_reason(loc, self.B, self.R, reloc)

    def test_areas(self):
        self.assertEqual(region.classify_region("Berlin, Germany"), "europe")
        self.assertEqual(region.classify_region("Dubai, United Arab Emirates"), "asia_africa")
        self.assertEqual(region.classify_region("Germany; India"), "europe")
        self.assertEqual(region.area_of("Tbilisi, Georgia"), "asia_africa")
        self.assertEqual(region.area_of("Atlanta, Georgia, United States"), "americas")

    def test_australia(self):
        for loc in ["Brisbane City, Queensland, Australia", "Melbourne VIC", "Greater Sydney Area", "Canberra, ACT",
                    "Victoria, AU", "Sydney / Brisbane / Perth", "Western Australia"]:
            self.assertEqual(self.reason(loc), "geo: australia", loc)
        for loc in ["Victoria, British Columbia", "Perth, Scotland", "Sydney; London", "Melbourne or Berlin",
                    "Auckland, New Zealand", "Sydney, New Zealand", "", None]:
            self.assertIsNone(self.reason(loc), loc)

    def test_asia_africa_needs_every_place(self):
        self.assertEqual(self.reason("India; Singapore"), "geo: asia_africa")
        self.assertIsNone(self.reason("Germany; India"))
        self.assertIsNone(self.reason("Remote; India"))

    def test_relocation_required(self):
        for loc in ["Toronto, Ontario, Canada", "Remote, United States", "New York, NY", "Portland, OR",
                    "San Francisco, CA; Toronto", "US", "U.S.A.", "Austin, TX OR Boston, MA", "New York; Tokyo"]:
            self.assertEqual(self.reason(loc), "geo: americas, no relocation", loc)
            self.assertIsNone(self.reason(loc, reloc=True), loc)
        for loc in ["New York; London", "Toronto or Berlin", "Remote", "Worldwide", "San Francisco OR Remote",
                    "Business Development", "USD remote", "Remote; US"]:
            self.assertIsNone(self.reason(loc), loc)

    def test_extra_hints(self):
        self.assertEqual(region.classify_region("Ljubljana"), "unknown")
        self.assertEqual(region.classify_region("Ljubljana", {"europe": ["Ljubljana"]}), "europe")


class Authorization(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(authorization.flags("We offer relocation support and visa sponsorship."), (1, 1))
        self.assertEqual(authorization.flags("We do not offer visa sponsorship."), (-1, -1))
        self.assertEqual(authorization.flags("Visa sponsorship is not available. Relocation support is provided."), (1, -1))
        self.assertEqual(authorization.flags("Great team."), (0, 0))
        self.assertEqual(authorization.flags(None), (None, None))

    def test_offers_relocation(self):
        self.assertTrue(authorization.offers_relocation(1, 0, None))
        self.assertFalse(authorization.offers_relocation(-1, -1, "We offer relocation support."))
        self.assertTrue(authorization.offers_relocation(None, None, "We offer relocation support."))
        self.assertFalse(authorization.offers_relocation(None, None, None))

    def test_blocker_only_on_explicit_statements(self):
        kind, phrase = authorization.blocker("Applicants must be legally authorized to work in the United States.")
        self.assertEqual(kind, "work authorization")
        self.assertIn("authorized to work in the United States", phrase)
        self.assertEqual(authorization.blocker("We are unable to sponsor visas.")[0], "no sponsorship")
        self.assertIsNone(authorization.blocker("Relocation support and visa sponsorship available.")[0])
        self.assertEqual(authorization.blocker(None), (None, None))


if __name__ == "__main__":
    unittest.main()
