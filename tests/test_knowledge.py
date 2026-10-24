"""The reference tables: model numbers, protocols, repair costs, scam patterns."""
import unittest

from knowledge import models as km, protocols, repairs, scams, nepal


class ModelNumberTests(unittest.TestCase):
    def test_us_iphone_14_and_later_is_esim_only(self):
        f = km.decode("iPhone 14 Pro, model A2650, like new")
        self.assertEqual(f[0]["code"], "A2650")
        self.assertEqual(f[0]["region"], "us")
        self.assertTrue(any("eSIM" in w for w in f[0]["warnings"]))

    def test_us_iphone_13_has_a_sim_tray(self):
        f = km.decode("A2482")
        self.assertEqual(f[0]["model"], "iPhone 13")
        self.assertEqual(f[0]["warnings"], [])
        self.assertTrue(any("SIM tray" in x for x in f[0]["facts"]))

    def test_international_model_has_no_warning(self):
        self.assertEqual(km.decode("A2882")[0]["warnings"], [])

    def test_china_model_dual_physical_sim(self):
        f = km.decode("A3092")
        self.assertEqual(f[0]["region"], "cn")
        self.assertTrue(any("two physical SIM" in x for x in f[0]["facts"]))

    def test_japan_part_number_forces_shutter_sound(self):
        f = km.decode("Model Number MLPF3J/A")
        self.assertEqual(f[0]["code"], "MLPF3J/A")
        self.assertTrue(any("shutter" in w for w in f[0]["warnings"]))

    def test_samsung_suffixes(self):
        self.assertEqual(km.decode("SM-S908B/DS")[0]["warnings"], [])
        self.assertTrue(any("carrier" in w for w in km.decode("SM-S908U")[0]["warnings"]))
        self.assertEqual(km.decode("SM-S9080")[0]["region"], "0")
        self.assertEqual(km.decode("SM-N986U1")[0]["region"], "u1")
        self.assertTrue(any("shutter" in w for w in km.decode("SM-S908N")[0]["warnings"]))

    def test_words_without_codes(self):
        self.assertEqual(km.decode("Japanese set iPhone 11")[0]["region"], "jp")
        self.assertEqual(km.decode("US variant")[0]["region"], "us")
        self.assertEqual(km.decode("iphone 13 128gb used, box bill"), [])
        self.assertEqual(km.decode(None, "", 42), [])

    def test_warnings_are_deduplicated(self):
        ws = km.warnings("A2650 A2651 US variant")
        self.assertEqual(len([w for w in ws if "eSIM" in w]), 1)
        self.assertEqual(len(ws), len(set(ws)))


class ProtocolTests(unittest.TestCase):
    def test_platform_detection(self):
        self.assertEqual(protocols.platform_for("phone", "iPhone 14"), "ios")
        self.assertEqual(protocols.platform_for("phone", "Samsung Galaxy S22"), "samsung")
        self.assertEqual(protocols.platform_for("phone", "Redmi Note 12"), "android")
        self.assertEqual(protocols.platform_for("laptop", "MacBook Air M1"), "mac")
        self.assertEqual(protocols.platform_for("laptop", "Acer Nitro"), "windows")
        self.assertEqual(protocols.platform_for("desktop", "Ryzen build"), "desktop")
        self.assertEqual(protocols.platform_for("toaster", "x"), "unknown")

    def test_every_phone_protocol_includes_the_nta_imei_step(self):
        for text in ("iPhone 14", "Samsung S22", "Redmi"):
            steps = protocols.protocol("phone", text)["steps"]
            self.assertTrue(any(s.get("url") == nepal.NTA_MDMS_URL for s in steps), text)
            for s in steps:
                self.assertTrue(s["title"] and s["how"] and s["bad_if"])

    def test_iphone_protocol_has_parts_and_service_history(self):
        titles = [s["title"] for s in protocols.protocol("phone", "iPhone 13")["steps"]]
        self.assertIn("Parts and Service History", titles)
        self.assertNotIn("Hidden test menu", titles)

    def test_unknown_category_gets_no_generic_steps(self):
        self.assertEqual(protocols.protocol("unknown", "thing")["steps"], [])

    def test_proof_requests_fit_the_platform(self):
        ios = [a["label"] for a in protocols.seller_proof_requests("phone", "iPhone 15")]
        self.assertIn("Battery Health", ios)
        self.assertIn("IMEI screen", ios)
        mac = [a["label"] for a in protocols.seller_proof_requests("laptop", "MacBook Pro")]
        self.assertNotIn("IMEI screen", mac)


class RepairTests(unittest.TestCase):
    REPORT = {"product_summary": {"model": "iPhone 13 Pro"},
              "image_analysis": {"per_image": [{"image_index": 1, "findings": [
                  {"type": "crack", "location": "back glass, bottom-left", "severity": "moderate", "confidence": "high", "notes": "visible"},
                  {"type": "scratch", "location": "frame", "severity": "minor", "confidence": "high", "notes": "light"},
                  {"type": "scratch", "location": "screen", "severity": "moderate", "confidence": "medium", "notes": "hairline"}]}]},
              "description_analysis": {"extracted_claims": {"battery_health_percent": 76}}}

    def test_families(self):
        self.assertEqual(repairs.family_for("phone", "iPhone 15 Pro Max"), "iphone_new")
        self.assertEqual(repairs.family_for("phone", "iPhone 12"), "iphone_mid")
        self.assertEqual(repairs.family_for("phone", "iPhone XR"), "iphone_old")
        self.assertEqual(repairs.family_for("phone", "Samsung Galaxy A54"), "samsung_mid")
        self.assertEqual(repairs.family_for("phone", "Samsung S22 Ultra"), "samsung_flagship")
        self.assertEqual(repairs.family_for("laptop", "MacBook Pro"), "macbook")
        self.assertEqual(repairs.family_for("phone", "Poco X5"), "android")
        self.assertEqual(repairs.family_for("fridge", "x"), "")

    def test_deductions_from_findings_and_claims(self):
        items = repairs.deductions("phone", self.REPORT, {"title": "iPhone 13 Pro"})
        parts = {i["part"] for i in items}
        self.assertEqual(parts, {"back_glass", "battery"})      # minor frame scratch and a moderate screen *scratch* are cosmetic
        lo, hi = repairs.total(items)
        self.assertGreater(hi, lo)
        self.assertTrue(all(i["low"] < i["high"] for i in items))

    def test_severe_screen_scratch_counts(self):
        rep = {"product_summary": {"model": "Redmi Note 12"}, "image_analysis": {"per_image": [{"image_index": 1, "findings": [
            {"type": "crack", "location": "display, top", "severity": "severe", "confidence": "high", "notes": "cracked"}]}]},
               "description_analysis": {}}
        self.assertEqual([i["part"] for i in repairs.deductions("phone", rep, {})], ["screen"])

    def test_replaced_back_panel_is_not_a_screen_repair(self):
        rep = {"product_summary": {"model": "iPhone 13 Pro Max"}, "image_analysis": {"per_image": [{"image_index": 1, "findings": [
            {"type": "replaced_part", "location": "back panel", "severity": "moderate", "confidence": "high", "notes": "replaced"},
            {"type": "crack", "location": "back panel, bottom", "severity": "moderate", "confidence": "high", "notes": "cracked"}]}]},
               "description_analysis": {}}
        self.assertEqual([i["part"] for i in repairs.deductions("phone", rep, {})], ["back_glass"])

    def test_laptop_missing_charger(self):
        rep = {"product_summary": {"model": "Acer Nitro 5"}, "image_analysis": {"per_image": []},
               "description_analysis": {"translated_description": "Selling without charger, no original adapter", "extracted_claims": {}}}
        self.assertIn("charger", [i["part"] for i in repairs.deductions("laptop", rep, {})])


class ScamTests(unittest.TestCase):
    def detect(self, desc, asking=None, median=None, used=0, anchor=None, ads=None, suspect=False):
        listing = {"description": desc, "title": ""}
        report = {"product_summary": {"asking_price": {"amount": asking}}, "description_analysis": {},
                  "image_analysis": {"per_image": [{"image_index": 1, "photo_authenticity": "stock_or_suspect" if suspect else "real_device_photo"}]}}
        market = {"market_stats": {"median": median}, "listings_used": used, "new_price_anchor": anchor}
        return {f["key"]: f for f in scams.detect(listing, report, market, {"ad_count": ads})}

    def test_pay_first_is_high(self):
        self.assertEqual(self.detect("advance payment 5000 esewa ma pathaunu")["pay_first"]["severity"], "high")
        self.assertIn("pay_first", self.detect("Pay first then I will courier"))
        self.assertIn("pay_first", self.detect("pahila paisa pathau"))

    def test_locks_and_registration(self):
        self.assertIn("locked_device", self.detect("icloud locked, bypass garera chalxa"))
        self.assertIn("locked_device", self.detect("wifi only phone"))
        self.assertIn("not_registered", self.detect("NTA register chaina"))
        self.assertIn("not_registered", self.detect("imei blocked"))
        self.assertNotIn("not_registered", self.detect("NTA registered, bill available"))

    def test_abroad_and_relative(self):
        self.assertIn("abroad_relative", self.detect("cousin le australia bata pathayeko"))
        self.assertIn("abroad_relative", self.detect("brought from Dubai"))
        self.assertIn("abroad_relative", self.detect("selling for my brother"))

    def test_price_patterns_need_enough_comparables(self):
        self.assertNotIn("far_below_market", self.detect("good phone", asking=40000, median=95000, used=2))
        self.assertEqual(self.detect("good phone", asking=40000, median=95000, used=6)["far_below_market"]["severity"], "high")
        self.assertEqual(self.detect("good phone", asking=68000, median=95000, used=6)["below_market"]["severity"], "medium")
        self.assertNotIn("below_market", self.detect("good phone", asking=90000, median=95000, used=6))

    def test_sealed_cheap_escalates_with_a_new_price_anchor(self):
        self.assertEqual(self.detect("sealed pack iphone")["sealed_cheap_words"]["severity"], "medium")
        self.assertEqual(self.detect("sealed pack iphone", asking=90000, anchor=140000)["sealed_cheap_words"]["severity"], "high")

    def test_dealer_vs_personal_use(self):
        f = self.detect("personal use phone", ads=40)
        self.assertEqual(f["dealer"]["severity"], "medium")
        self.assertNotIn("personal_use_claim", f)
        self.assertEqual(self.detect("good phone", ads=40)["dealer"]["severity"], "low")
        self.assertNotIn("dealer", self.detect("good phone", ads=3))

    def test_stock_photos(self):
        self.assertIn("stock_photos", self.detect("phone", suspect=True))

    def test_clean_listing_has_no_findings(self):
        self.assertEqual(self.detect("Selling my iPhone 13, 2 years used, box and bill available, meet in Baneshwor, NTA registered"), {})

    def test_sorted_by_severity(self):
        out = scams.detect({"description": "urgent sale, advance payment required, cousin abroad"}, {"product_summary": {}})
        sev = [o["severity"] for o in out]
        self.assertEqual(sev, sorted(sev, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s]))


if __name__ == "__main__":
    unittest.main()
