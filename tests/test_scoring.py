"""The score is computed in Python; the model only suggests."""
import copy
import unittest

import scoring
from knowledge import scams

CLEAN = {
    "device_category": "phone",
    "verdict": {"rating": "buy_worthy", "score_0_100": 82},
    "image_analysis": {"per_image": [{"image_index": i, "findings": [], "photo_authenticity": "real_device_photo"} for i in range(1, 6)]},
    "description_analysis": {"extracted_claims": {"battery_health_percent": 91, "box_included": True}, "category_specific_claims": {}},
    "red_flags": [], "green_flags": ["box", "bill"],
    "price_analysis": {"verdict": "fair", "confidence": "high", "estimated_fair_range_npr": [46000, 54000]},
    "product_summary": {"asking_price": {"amount": 50000}},
    "limitations": [],
}
LISTING = {"description": "Selling my iPhone 13 128GB, used 1 year, battery 91%, box and bill, no repairs, meet in Kathmandu to test everything."}


def run(report, listing=LISTING, **kw):
    return scoring.compute(report, listing, **kw)


class ScoringTests(unittest.TestCase):
    def test_clean_listing_is_a_strong_buy(self):
        sc = run(CLEAN)
        self.assertEqual(sc["rating"], "strong_buy")
        self.assertGreaterEqual(sc["score"], 90)
        self.assertEqual(sc["badge_color"], "green")

    def test_model_cannot_talk_the_score_up(self):
        rep = copy.deepcopy(CLEAN)
        rep["verdict"] = {"rating": "strong_buy", "score_0_100": 100}
        rep["image_analysis"]["per_image"][0]["findings"] = [{"type": "crack", "location": "screen", "severity": "severe", "confidence": "high"}]
        rep["green_flags"] = ["great"] * 20
        sc = run(rep)
        self.assertIn(sc["rating"], ("consider_with_caution", "caution", "avoid"))
        self.assertLessEqual(sc["score"], 69)
        self.assertEqual(sc["cap_applied"]["rating"], "consider_with_caution")
        self.assertFalse(sc["agrees"])
        self.assertEqual(sum(i["points"] for i in sc["items"] if i["group"] == "model_green"), 8)   # capped bonus

    def test_locked_device_is_capped_at_avoid(self):
        rep = copy.deepcopy(CLEAN)
        listing = {"description": LISTING["description"] + " icloud lock xa, bypass garera chalxa"}
        sc = run(rep, listing, scams=scams.detect(listing, rep))
        self.assertEqual(sc["rating"], "avoid")
        self.assertLessEqual(sc["score"], 34)

    def test_pay_first_caps_at_caution(self):
        rep = copy.deepcopy(CLEAN)
        listing = {"description": LISTING["description"] + " advance payment esewa ma garnu parxa"}
        sc = run(rep, listing, scams=scams.detect(listing, rep))
        self.assertIn(sc["rating"], ("caution", "avoid"))

    def test_missing_evidence_costs_points(self):
        rep = copy.deepcopy(CLEAN)
        rep["description_analysis"]["extracted_claims"] = {}
        rep["image_analysis"]["per_image"] = rep["image_analysis"]["per_image"][:2]
        rep["price_analysis"]["confidence"] = "low"
        sc = run(rep, {"description": "iphone 13"})
        labels = " ".join(i["label"] for i in sc["items"])
        self.assertIn("Battery health is not stated", labels)
        self.assertIn("Only 2 photos", labels)
        self.assertIn("Very short description", labels)
        self.assertLess(sc["score"], run(CLEAN)["score"] - 15)
        self.assertNotEqual(sc["rating"], "strong_buy")

    def test_battery_thresholds(self):
        rep = copy.deepcopy(CLEAN)
        rep["description_analysis"]["extracted_claims"]["battery_health_percent"] = 78
        a = run(rep)["score"]
        rep["description_analysis"]["extracted_claims"]["battery_health_percent"] = 65
        b = run(rep)["score"]
        self.assertGreater(run(CLEAN)["score"], a)
        self.assertGreater(a, b)

    def test_model_judgment_is_bounded(self):
        rep = copy.deepcopy(CLEAN)
        rep["verdict"] = {"rating": "avoid", "score_0_100": 0}
        sc = run(rep)
        j = [i for i in sc["items"] if i["group"] == "judgment"]
        self.assertEqual(len(j), 1)
        self.assertGreaterEqual(j[0]["points"], -8)
        self.assertGreaterEqual(sc["score"], 85)

    def test_suspiciously_cheap_caps_unless_low_confidence(self):
        rep = copy.deepcopy(CLEAN)
        rep["price_analysis"] = {"verdict": "suspiciously_cheap", "confidence": "high"}
        self.assertLessEqual(run(rep)["score"], 54)
        rep["price_analysis"] = {"verdict": "suspiciously_cheap", "confidence": "low"}
        self.assertGreater(run(rep)["score"], 54)

    def test_history_signals(self):
        bad = run(CLEAN, history=[{"severity": "high", "label": "same photos by another seller"}])
        self.assertLessEqual(bad["score"], 54)
        good = run(CLEAN, history=[{"severity": "good", "label": "2 claims confirmed"}])
        self.assertGreaterEqual(good["score"], run(CLEAN)["score"])

    def test_region_warnings_are_a_small_deduction(self):
        sc = run(CLEAN, model_warnings=["eSIM only", "shutter sound", "third"])
        self.assertEqual(sum(i["points"] for i in sc["items"] if i["group"] == "region"), -6)

    def test_apply_writes_the_verdict_and_keeps_the_suggestion(self):
        rep = copy.deepcopy(CLEAN)
        sc = run(rep)
        scoring.apply(rep, sc)
        self.assertEqual(rep["verdict"]["rating"], sc["rating"])
        self.assertEqual(rep["verdict"]["score_0_100"], sc["score"])
        self.assertEqual(rep["verdict"]["model_rating"], "buy_worthy")
        self.assertEqual(rep["verdict"]["model_score_0_100"], 82)
        self.assertEqual(rep["scoring"]["version"], scoring.VERSION)

    def test_one_replaced_part_and_one_battery_figure_count_once(self):
        rep = copy.deepcopy(CLEAN)
        rep["description_analysis"]["extracted_claims"]["battery_health_percent"] = 77
        rep["image_analysis"]["per_image"][0]["findings"] = [{"type": "replaced_part", "location": "back panel", "severity": "moderate", "confidence": "high"}]
        rep["image_analysis"]["per_image"][2]["findings"] = [{"type": "replaced_part", "location": "Parts & Service History menu", "severity": "moderate", "confidence": "high"}]
        rep["image_analysis"]["per_image"][4]["findings"] = [{"type": "wear", "location": "battery", "severity": "severe", "confidence": "high", "notes": "Battery Health shows 77%"}]
        sc = run(rep)
        photos = [i for i in sc["items"] if i["group"] == "photos"]
        self.assertEqual(len(photos), 2)                                     # one replaced-part finding + its extra line
        self.assertEqual(sum(i["points"] for i in photos), -13)
        self.assertEqual([i["points"] for i in sc["items"] if "Battery health 77%" in i["label"]], [-10])
        self.assertIsNone(sc["cap_applied"])                                 # a screenshot of a battery figure is not a severe visible defect

    def test_bands(self):
        self.assertEqual(scoring.rating_for(85), "strong_buy")
        self.assertEqual(scoring.rating_for(84), "buy_worthy")
        self.assertEqual(scoring.rating_for(55), "consider_with_caution")
        self.assertEqual(scoring.rating_for(35), "caution")
        self.assertEqual(scoring.rating_for(34), "avoid")

    def test_deterministic(self):
        self.assertEqual(run(CLEAN), run(copy.deepcopy(CLEAN)))

    def test_summary_mentions_the_cap(self):
        rep = copy.deepcopy(CLEAN)
        rep["image_analysis"]["per_image"][0]["findings"] = [{"type": "crack", "location": "screen", "severity": "severe", "confidence": "high"}]
        self.assertIn("Held at", run(rep)["summary"])


if __name__ == "__main__":
    unittest.main()
