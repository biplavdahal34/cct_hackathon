"""Proof round, outcome feedback, and the Nepali translation."""
import io
import json
import unittest
from unittest import mock

from PIL import Image

from tests.common import Base, HB
from extensions import db
import followup
import outcomes
import translate
from models import Analysis, Followup, Outcome, User


def png(w=64, h=64):
    buf = io.BytesIO(); Image.new("RGB", (w, h), (120, 10, 10)).save(buf, "PNG"); return buf.getvalue()


class FakeResp:
    def __init__(self, text): self.text = text


def client_returning(payload):
    class M:
        def generate_content(self, model, contents, config): return FakeResp(json.dumps(payload))
    class C: models = M()
    return lambda key=None: C()


class FollowupUnitTests(unittest.TestCase):
    def test_validate_files(self):
        from werkzeug.datastructures import FileStorage
        ok = [FileStorage(io.BytesIO(png()), "a.png"), FileStorage(io.BytesIO(png()), "b.jpg")]
        out = followup.validate_files(ok)
        self.assertEqual([m for _, m in out], ["image/png", "image/png"])        # format from the bytes, not the name
        with self.assertRaises(followup.FollowupRejected):
            followup.validate_files([FileStorage(io.BytesIO(b"hello"), "x.png")])
        with self.assertRaises(followup.FollowupRejected):
            followup.validate_files([FileStorage(io.BytesIO(png()), "a.png")] * 5)
        with self.assertRaises(followup.FollowupRejected):
            followup.validate_files([])

    def test_check_validates_shape_and_scrubs_imei(self):
        payload = {"images": [{"index": 1, "shows": "About screen, IMEI 356789012345678", "device_match": "same",
                               "readings": {"model_number": "A2482", "battery_health_percent": 74}},
                              {"index": 2, "shows": "extra", "device_match": "nonsense"}],
                   "claims": [{"claim": "battery 91%", "status": "contradicted", "evidence": "shows 74%"},
                              {"claim": "box", "status": "weird", "evidence": ""}],
                   "warnings": ["Battery shows 74%, listing said 91%"], "summary": "Serial 356789012345678 shown."}
        with mock.patch.object(followup, "gemini_client", client_returning(payload)):
            res = followup.check({"device_category": "phone", "product_summary": {"model": "iPhone 13"}, "description_analysis": {}},
                                 {}, [(png(), "image/png")])
        self.assertEqual(len(res["images"]), 1)                                  # one image sent, one kept
        self.assertNotIn("356789012345678", json.dumps(res))
        self.assertEqual(res["claims"][1]["status"], "not_shown")
        self.assertEqual(res["contradicted"], 1)
        self.assertFalse(res["different_device"])
        sig = followup.signals([res])
        self.assertEqual([s["severity"] for s in sig], ["high"])

    def test_seller_message_lists_platform_proof(self):
        msg = followup.seller_message({"device_category": "phone", "product_summary": {"model": "Samsung S22"}}, {})
        self.assertIn("Download mode", msg)
        self.assertIn("नमस्ते", msg)


class FollowupRouteTests(Base):
    rich = True

    def setUp(self):
        super().setUp()
        self.aid = self.analysis_id(self.analyze(HB))

    def test_proof_round_rescores_and_persists(self):
        before = Analysis.query.get(self.aid).score
        payload = {"images": [{"index": 1, "shows": "Battery Health", "device_match": "unclear", "readings": {"battery_health_percent": 74}}],
                   "claims": [{"claim": "battery health 91%", "status": "contradicted", "evidence": "screen shows 74%"}],
                   "warnings": ["Battery is 74%, not 91%"], "summary": "The battery claim is false."}
        with mock.patch.object(followup, "gemini_client", client_returning(payload)):
            r = self.c.post(f"/report/{self.aid}/followup", data={"shots": (io.BytesIO(png()), "a.png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        a = Analysis.query.get(self.aid)
        self.assertLess(a.score, before)
        self.assertLessEqual(a.score, 54)                                        # contradiction caps at caution
        self.assertEqual(Followup.query.count(), 1)
        page = self.c.get(f"/report/{self.aid}").get_data(as_text=True)
        self.assertIn("1 contradicted", page)
        self.assertIn("The battery claim is false.", page)
        self.assertIn("1 claim contradicted", page)

    def test_bad_upload_is_rejected_without_a_model_call(self):
        with mock.patch.object(followup, "gemini_client", lambda key=None: self.fail("should not be called")):
            r = self.c.post(f"/report/{self.aid}/followup", data={"shots": (io.BytesIO(b"nope"), "a.png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Followup.query.count(), 0)
        self.assertIn("Only JPEG, PNG or WebP", self.c.get(f"/report/{self.aid}").get_data(as_text=True))

    def test_proof_round_is_metered_on_the_shared_key(self):
        from models import UsageEvent
        payload = {"images": [], "claims": [], "warnings": [], "summary": "ok"}
        with mock.patch.object(followup, "gemini_client", client_returning(payload)):
            self.c.post(f"/report/{self.aid}/followup", data={"shots": (io.BytesIO(png()), "a.png")}, content_type="multipart/form-data")
        self.assertEqual(UsageEvent.query.filter_by(source="followup").count(), 1)


class OutcomeTests(Base):
    rich = True

    def test_record_upsert_and_page(self):
        aid = self.analysis_id(self.analyze(HB))
        r = self.c.post(f"/report/{aid}/outcome", data={"outcome": "bought_with_issues", "note": "battery was 70%"})
        self.assertEqual(r.status_code, 302)
        self.c.post(f"/report/{aid}/outcome", data={"outcome": "scam"})
        self.assertEqual(Outcome.query.count(), 1)
        self.assertEqual(Outcome.query.one().outcome, "scam")
        self.assertIn("It was a scam", self.c.get(f"/report/{aid}").get_data(as_text=True))
        self.assertEqual(self.c.post(f"/report/{aid}/outcome", data={"outcome": "nonsense"}).status_code, 302)
        self.assertEqual(Outcome.query.one().outcome, "scam")

    def test_calibration_hit_logic_and_threshold(self):
        a = Analysis.query.get(self.analysis_id(self.analyze(HB)))
        outcomes.record(a, self.user.id, "bought_as_described")
        cal = outcomes.calibration()
        self.assertFalse(cal["enough"])                                          # CALIBRATION_MIN_REPORTS=3 in tests
        self.assertIn("Not enough feedback yet", self.c.get("/accuracy").get_data(as_text=True))
        for i, (rating, oc) in enumerate((("avoid", "scam"), ("avoid", "bought_as_described"), ("buy_worthy", "did_not_buy")), start=2):
            b = Analysis.query.get(self.analysis_id(self.analyze(HB, i)))
            b.rating = rating; db.session.commit()
            outcomes.record(b, self.user.id, oc)
        cal = outcomes.calibration()
        self.assertTrue(cal["enough"])
        per = {p["rating"]: p for p in cal["per_rating"]}
        self.assertEqual(per["avoid"]["hit_rate"], 50)
        self.assertIsNone(per["buy_worthy"]["hit_rate"])                         # only a did_not_buy there
        page = self.c.get("/accuracy").get_data(as_text=True)
        self.assertIn("Hit rate", page)
        self.assertIn("50%", page)


class TranslateTests(Base):
    rich = True

    def test_merge_keeps_structure_and_codes(self):
        orig = {"verdict": {"rating": "avoid", "one_liner": "Bad"}, "red_flags": ["a", "b"], "n": 3, "u": "https://x"}
        trans = {"verdict": {"rating": "strong_buy", "one_liner": "नराम्रो"}, "red_flags": ["क", "ख", "extra"], "n": "३", "u": "https://evil"}
        out = translate._merge(orig, trans)
        self.assertEqual(out["verdict"], {"rating": "avoid", "one_liner": "नराम्रो"})
        self.assertEqual(out["red_flags"], ["a", "b"])
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["u"], "https://x")

    def test_translate_route_caches_and_renders_nepali_chrome(self):
        aid = self.analysis_id(self.analyze(HB))
        a = Analysis.query.get(aid)
        payload = json.loads(json.dumps({k: v for k, v in a.report.items() if k != "market_data"}))
        payload["verdict"]["one_liner"] = "उचित मूल्यमा राम्रो iPhone 13।"
        payload["verdict"]["rating"] = "strong_buy"                              # must be ignored
        with mock.patch.object(translate, "gemini_client", client_returning(payload)) as m:
            r = self.c.post(f"/report/{aid}/translate")
        self.assertEqual(r.headers["Location"], f"/report/{aid}?lang=ne")
        a = Analysis.query.get(aid)
        self.assertIsNotNone(a.report_ne_json)
        self.assertEqual(a.report_ne["verdict"]["rating"], a.report["verdict"]["rating"])
        page = self.c.get(f"/report/{aid}?lang=ne").get_data(as_text=True)
        self.assertIn("उचित मूल्यमा राम्रो iPhone 13", page)
        self.assertIn("सारांश", page)
        self.assertIn("In English", page)
        # second request: cached, no model call
        with mock.patch.object(translate, "gemini_client", lambda key=None: self.fail("cached")):
            self.assertEqual(self.c.post(f"/report/{aid}/translate").status_code, 302)
        # the English page is unchanged
        self.assertIn("नेपालीमा", self.c.get(f"/report/{aid}").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
