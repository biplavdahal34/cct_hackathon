"""The report page with everything derived, and older reports without it."""
import json
import unittest

from tests.common import Base, HB, FAKE_REPORT, deep
from extensions import db
from models import Analysis
from report_views import canonical_url


class ReportPageTests(Base):
    rich = True

    def test_full_report_renders_every_new_section(self):
        aid = self.analysis_id(self.analyze(HB))
        a = Analysis.query.get(aid)
        self.assertIn("beat", a.report)
        self.assertEqual(a.report["scoring"]["version"], __import__("scoring").VERSION)
        self.assertEqual(a.score, a.report["verdict"]["score_0_100"])
        page = self.c.get(f"/report/{aid}").get_data(as_text=True)
        for text in ("How the", "was counted",                       # ledger
                     "Step by step when you meet", "Parts and Service History", "NTA MDMS",
                     "Proof from the seller", "Check the screenshots",
                     "Negotiation", "opening offer", "Copy Nepali",
                     "Did you buy it?", "Ask about this listing",
                     "Brand-new on the same site", "below new",
                     "Model number and region", "A2482",
                     "This is the first time BEAT has seen this listing",
                     "How this report was made"):
            self.assertIn(text, page, text)
        self.assertIn("Like new", page)                                   # comparable condition pill
        self.assertNotIn("Scam patterns", page)                           # clean listing

    def test_old_report_without_derived_blocks_still_renders(self):
        a = Analysis(user_id=self.user.id, url=HB, url_key=canonical_url(HB), title="Old", source="hamrobazaar",
                     device_category="phone", rating="buy_worthy", badge_color="green", score=80,
                     report_json=json.dumps(FAKE_REPORT), listing_json=json.dumps({"images": [], "url": HB, "source": "hamrobazaar"}))
        db.session.add(a); db.session.commit()
        r = self.c.get(f"/report/{a.id}")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("made before BEAT kept listing history", page)
        self.assertIn("Ask about this listing", page)

    def test_reused_report_keeps_the_derived_blocks(self):
        first = self.analysis_id(self.analyze(HB))
        second = self.analysis_id(self.analyze(HB + "?x=1"))
        self.assertEqual(first, second)
        self.assertIn("How the", self.c.get(f"/report/{second}").get_data(as_text=True))

    def test_dates_read_as_days_not_iso(self):
        from datetime import datetime
        from knowledge import protocols
        d = datetime.fromisoformat(protocols.REVIEWED)
        page = self.c.get(f"/report/{self.analysis_id(self.analyze(HB))}").get_data(as_text=True)
        self.assertIn(f"Steps reviewed {d.day} {d:%b %Y}.", page)
        self.assertNotIn(f"reviewed {protocols.REVIEWED}", page)
        self.assertIn('<span class="k">Posted</span><span class="v">20 Oct 2026</span>', page)   # RICH_LISTING.posted_at

    def test_analyses_list_and_home_show_python_score(self):
        aid = self.analysis_id(self.analyze(HB))
        a = Analysis.query.get(aid)
        page = self.c.get("/analyses").get_data(as_text=True)
        self.assertIn(str(a.score), page)


if __name__ == "__main__":
    unittest.main()
