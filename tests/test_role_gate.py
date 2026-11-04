import unittest

from job_radar.gates import role


class TitleFamilies(unittest.TestCase):
    def test_excluded_titles(self):
        for title, fam in [
            ("Vice President of Growth", "president"),
            ("Go to Market Recruiter", "recruiter"),
            ("Talent Acquisition Partner (GTM)", "recruiter"),
            ("Working Student Growth Marketing", "junior"),
            ("Marketing Intern", "junior"),
            ("Werkstudent Marketing (m/w/d)", "junior"),
            ("Jr Growth Manager", "junior"),
            ("Graduate Marketing Lead", "junior"),
            ("Sales Ops Analyst", "ops"),
            ("Revenue Operations Specialist", "ops"),
            ("Sales and Revenue Operations Manager", "ops"),
            ("Director of Finance & RevOps", "ops"),
            ("Salesforce & GTM Systems Lead", "salesforce"),
            ("Lead Recruiter, Growth", "recruiter"),
            ("Agentic AI Engineer - GTM Systems", "engineer"),
            ("Principal GTM Systems Architect", "engineer"),
            ("Account Executive, Growth", "sales"),
            ("VP Sales", "sales"),
            ("Business Development Manager Growth", "sales"),
            ("Managing Director, Corporate Strategy & Growth", "gm"),
            ("Director of DTC Growth", "d2c"),
            ("Head of Retail Marketing", "retail"),
            ("Senior Product Manager, Mobile", "mobile_app"),
            ("Growth Marketing Manager, ASO", "mobile_app"),
        ]:
            self.assertEqual(role.role_family(title, description="We sell DTC." if "DTC" in title else None), fam, title)

    def test_target_titles_kept(self):
        for title in ["Head of Growth", "Director of Marketing", "VP Product Marketing", "Lead Growth Manager",
                      "Head of GTM", "GTM Engineer, RevOps", "Growth Product Manager", "Revenue Operations Lead",
                      "Head of RevOps", "Senior Manager, Marketing Operations", "Head of Growth - Graduate Program",
                      "Global Recruitment Marketing Lead", "Head of Marketing & Sales", "Growth Engineer",
                      "Senior Revenue Operations Manager, Post Sales", "Web App Product Manager"]:
            self.assertIsNone(role.role_family(title), title)

    def test_empty_and_reason(self):
        self.assertIsNone(role.role_family(None))
        self.assertEqual(role.excluded_reason("GTM Recruiter"), "role: recruiter")

    def test_families_can_be_switched_off(self):
        self.assertIsNone(role.role_family("Marketing Intern", families={"recruiter"}))
        self.assertEqual(role.role_family("Marketing Intern", families={"junior"}), "junior")
        self.assertIsNone(role.role_family("Vice President of Growth", families=set()))

    def test_custom_family(self):
        import re
        custom = [("brand", re.compile(r"\bbrand manager\b", re.I))]
        self.assertEqual(role.role_family("Growth Brand Manager", custom=custom), "brand")


class ContextFamilies(unittest.TestCase):
    def test_anonymous_company(self):
        for c in ["Confidential", "Confidential Careers", "Our client", "Undisclosed company"]:
            self.assertEqual(role.role_family("Head of Growth", c, "d"), "anonymous", c)
        self.assertIsNone(role.role_family("Head of Growth", "Clientify", "d"))

    def test_sales_description_needs_a_quota_dominated_text(self):
        sales = "You carry a quota. Bookings and quota attainment drive the role; you lead account executives. " * 3
        mkt = "You own demand generation, brand, campaigns, content, SEO and lifecycle marketing. " * 3
        self.assertEqual(role.role_family("Client Growth Lead", "X", sales), "sales")
        self.assertIsNone(role.role_family("Head of Growth", "X", mkt))
        self.assertIsNone(role.role_family("Head of GTM", "X", sales))
        self.assertIsNone(role.role_family("Growth Marketing Lead", "X", sales))

    def test_gm_kept_when_marketing_title_and_text(self):
        mkt = "Marketing, brand, campaigns and content across the funnel. " * 2
        self.assertIsNone(role.role_family("Managing Director, Global Marketing", "X", mkt))
        self.assertEqual(role.role_family("Managing Director, Global Marketing", "X", None), "gm")

    def test_d2c_and_retail_never_fire_for_software(self):
        shop = "We help Shopify store owners. D2C brands, DTC, Shopify, e-commerce brand growth. " * 2
        self.assertIsNone(role.role_family("Head of Growth", "SaaS Co", shop, "Software Development"))
        self.assertEqual(role.role_family("Head of Growth", "Grocer", "d", "Retail"), "retail")
        self.assertIsNone(role.role_family("Head of Growth", "Marketplace", "Our stores, supermarkets, grocery.", None))

    def test_mobile_description_judges_product_titles_only(self):
        b2c = "About us. Our company is a popular mobile app for meal planning."
        self.assertEqual(role.role_family("Senior Product Manager - Retention", description=b2c), "mobile_app")
        self.assertIsNone(role.role_family("Head of Growth", description=b2c))
        tool = "We build analytics for mobile apps. Our customers are app developers."
        self.assertIsNone(role.role_family("Product Manager", description=tool))


class SalesforceRequired(unittest.TestCase):
    REQUIRED = [
        "<h3>Requirements</h3><ul><li>Hands-on experience administering Salesforce and Marketo.</li></ul>",
        "Qualifications:\n- Strong Salesforce knowledge: data model, reports and dashboards.",
        "About the role\nYou will own reporting. Salesforce experience is required.",
        "We move fast. 5+ years of Salesforce administration in a B2B SaaS company.",
        "Must have: Salesforce reporting fluency.",
        "Hands-on experience with Apex and SOQL is mandatory.",
        "Requirements\nExperience with Salesforce.",
        "You must have 3+ years of hands-on Salesforce experience.",
        "This role requires deep Salesforce knowledge.",
        "Requirements: Proficiency in Salesforce reporting.",
    ]
    NOT_REQUIRED = [
        "Requirements\n- Experience with a CRM such as Salesforce or HubSpot.",
        "Requirements\n- Hands-on experience with CRM tools (e.g. Salesforce).",
        "Requirements\n- Strong analytical skills.\nNice to have\n- Salesforce experience.",
        "Requirements\n- Salesforce experience is a plus.",
        "Our platform integrates with Salesforce and Slack. You must love data.",
        "Our founding team is ex-Salesforce.",
        "Requirements\n- Experience with Adobe Marketing Cloud.",
        "Requirements\n- Experience managing a large salesforce of 40 reps.",
        "Salesforce certification is not necessary.",
        "Requirements\n- Salesforce/HubSpot experience.",
        "We use Salesforce internally, and you must grow revenue.",
        "You must have SQL skills and use Salesforce.",
        "Salesforce is used and SQL expertise is required.",
        "",
    ]

    def test_required(self):
        for d in self.REQUIRED:
            self.assertTrue(role.salesforce_required(d), d)
            self.assertEqual(role.role_family("Head of Growth", "Acme", d), "salesforce", d)

    def test_not_required(self):
        for d in self.NOT_REQUIRED:
            self.assertIsNone(role.salesforce_required(d), d)
            self.assertIsNone(role.role_family("Head of Growth", "Acme", d), d)

    def test_precedence(self):
        d = "Requirements\nExperience with Salesforce."
        self.assertIsNone(role.role_family("Head of Growth", "Salesforce", d))
        self.assertEqual(role.role_family("Vice President of Growth", "Acme", d), "president")
        self.assertEqual(role.role_family("Revenue Operations Specialist", "Acme", d), "ops")
        self.assertEqual(role.role_family("Revenue Operations Lead", "Acme", d), "salesforce")


if __name__ == "__main__":
    unittest.main()
