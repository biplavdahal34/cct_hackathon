"""Listing snapshots, photo hashes, seller history, and the price index."""
import io
import random
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from PIL import Image

from tests.common import Base, deep, RICH_LISTING, RICH_REPORT, RICH_MARKET
from extensions import db
import prices
import routes_main
import snapshots
from models import Analysis, ListingSnapshot, ImageHash, PriceObservation


def noise_image(seed, fmt="JPEG", size=(240, 180)):
    rnd = random.Random(seed)
    img = Image.new("RGB", (12, 9))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(12 * 9)])
    img = img.resize(size, Image.BILINEAR)
    buf = io.BytesIO(); img.save(buf, fmt); return buf.getvalue()


class HashTests(unittest.TestCase):
    def test_hash_survives_resize_and_reencoding(self):
        a = snapshots.dhash_bytes(noise_image(1))
        b = snapshots.dhash_bytes(noise_image(1, "WEBP", (120, 90)))
        c = snapshots.dhash_bytes(noise_image(2))
        self.assertEqual(len(a), 16)
        self.assertLessEqual(snapshots.hamming(a, b), snapshots.NEAR_MATCH_BITS)
        self.assertGreater(snapshots.hamming(a, c), snapshots.NEAR_MATCH_BITS)

    def test_bad_bytes_give_no_hash(self):
        self.assertIsNone(snapshots.dhash_bytes(b"not an image"))

    def test_seller_key_prefers_site_id_then_phone(self):
        self.assertEqual(snapshots.seller_key({"source": "hamrobazaar", "seller": {"id": "ab-cd"}}), "hamrobazaar:AB-CD")
        self.assertEqual(snapshots.seller_key({"source": "hamrobazaar", "seller": {"profile_url": "https://hamrobazaar.com/user/x/AC8EAD31-CD15-4B5F-91CE-AB24C4CCB811"}}),
                         "hamrobazaar:AC8EAD31-CD15-4B5F-91CE-AB24C4CCB811")
        self.assertEqual(snapshots.seller_key({"source": "facebook_post", "seller": {"phone": "9841234567"}}), "facebook:tel:9841234567")
        self.assertIsNone(snapshots.seller_key({"source": "hamrobazaar", "seller": {"phone": "98******12", "name": "Ram"}}))


class SnapshotTests(Base):
    def listing(self, **over):
        l = deep(RICH_LISTING); l.update(over); return l

    def test_record_then_diff_price_cut_and_condition_upgrade(self):
        key = "https://hamrobazaar.com/detail/x/1"
        first = snapshots.record(self.listing(), key, images=[("u1", noise_image(1), "image/jpeg")])
        first.captured_at = datetime.now(timezone.utc) - timedelta(days=3); db.session.commit()
        second = snapshots.record(self.listing(price={"amount": 47000, "currency": "NPR"}, condition="Brand new", description="changed text"), key,
                                  images=[("u2", noise_image(9), "image/jpeg")])
        changes = {c["key"]: c for c in snapshots.diff(snapshots.previous(key, second), second)}
        self.assertIn("price_cut", changes)
        self.assertIn("10% lower", changes["price_cut"]["label"])
        self.assertEqual(changes["condition_upgraded"]["severity"], "medium")
        self.assertIn("description_changed", changes)
        self.assertEqual(changes["photos_replaced"]["severity"], "medium")
        self.assertEqual(len(snapshots.history(key)), 2)

    def test_same_photo_in_another_sellers_listing_is_a_high_signal(self):
        other = self.listing(seller={"id": "OTHER-SELLER", "name": "Other"})
        snapshots.record(other, "https://hamrobazaar.com/detail/x/old", images=[("u", noise_image(5), "image/jpeg")])
        info = snapshots.inspect(self.listing(), "https://hamrobazaar.com/detail/x/new", images=[("u", noise_image(5, "WEBP", (100, 75)), "image/webp")])
        self.assertEqual(len(info["matches"]), 1)
        self.assertFalse(info["matches"][0]["same_seller"])
        self.assertIn("photos_other_seller", [s["key"] for s in info["signals"]])
        self.assertEqual([s["severity"] for s in info["signals"] if s["key"] == "photos_other_seller"], ["high"])
        view = snapshots.view(info)
        self.assertEqual(view["matches"][0]["same_seller"], False)
        self.assertIsInstance(view["timeline"][0]["captured_at"], str)
        self.assertRegex(view["matches"][0]["captured_at"], r"^\d{1,2} [A-Z][a-z]{2} \d{4}$")            # '23 Oct 2026'
        self.assertRegex(view["timeline"][0]["captured_at"], r"^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$")

    def test_day_labels_are_in_nepal_time(self):
        self.assertEqual(snapshots.day_label(datetime(2026, 10, 23, 18, 30, tzinfo=timezone.utc)), "24 Oct 2026")
        self.assertEqual(snapshots.when_label(datetime(2026, 10, 23, 18, 30)), "24 Oct 2026, 00:15")   # naive means UTC
        self.assertEqual(snapshots.day_label(None), "")

    def test_same_seller_repost_is_low(self):
        snapshots.record(self.listing(), "https://hamrobazaar.com/detail/x/old", images=[("u", noise_image(7), "image/jpeg")])
        info = snapshots.inspect(self.listing(), "https://hamrobazaar.com/detail/x/new", images=[("u", noise_image(7), "image/jpeg")])
        self.assertTrue(info["matches"][0]["same_seller"])
        self.assertIn("reposted", [s["key"] for s in info["signals"]])
        self.assertNotIn("photos_other_seller", [s["key"] for s in info["signals"]])

    def test_seller_profile_counts_listings_and_names(self):
        for i in range(6):
            snapshots.record(self.listing(seller={"id": "S1", "name": f"Name {i % 3}", "ad_count": 20}), f"https://hamrobazaar.com/detail/x/{i}")
        prof = snapshots.seller_profile("hamrobazaar:S1")
        self.assertEqual(prof["listings_seen"], 6)
        self.assertEqual(prof["ad_count"], 20)
        self.assertEqual(len(prof["names"]), 3)
        sig = {s["key"] for s in snapshots.signals(ListingSnapshot.query.first(), [], [], prof)}
        self.assertTrue({"dealer", "many_listings", "many_names"} <= sig)
        self.assertEqual(snapshots.seller_profile(None), {})

    def test_photos_reused_by_another_seller_count_as_a_red_flag_on_the_page(self):
        self.rich = True
        self.setUp()
        same_photo = lambda urls, max_n=None: [("u", noise_image(3), "image/jpeg")]
        other = deep(RICH_LISTING)
        other["seller"] = {"id": "OTHER-SELLER", "name": "Other", "ad_count": 1}
        other["external_id"] = "11111111-89E9-478F-86DE-77F04014CBB9"
        with mock.patch.object(routes_main, "download_images", same_photo):
            self.analyze("https://hamrobazaar.com/detail/x/first/0D872DAD-89E9-478F-86DE-77F04014CBB9")
            with mock.patch.object(routes_main, "scrape_any", lambda url, apify_token=None: deep(other)):
                aid = self.analysis_id(self.analyze("https://hamrobazaar.com/detail/x/second/11111111-89E9-478F-86DE-77F04014CBB9"))
        a = Analysis.query.get(aid)
        self.assertTrue(a.report["scoring"]["cap_applied"])
        page = self.c.get(f"/report/{aid}").get_data(as_text=True)
        self.assertIn("listing by a different seller", page)
        self.assertNotIn("Nothing suspicious came up", page)          # the summary's red-flag card carries the history finding
        self.assertNotIn(">Good for<", page)                          # the AI's audience line is withdrawn when the rules hold the score down

    def test_analysis_flow_records_a_snapshot_with_the_seller_key(self):
        self.rich = True
        self.setUp()
        aid = self.analysis_id(self.analyze("https://hamrobazaar.com/detail/x/y/0D872DAD-89E9-478F-86DE-77F04014CBB9"))
        snap = ListingSnapshot.query.one()
        self.assertEqual(snap.analysis_id, aid)
        self.assertEqual(snap.seller_key, "hamrobazaar:AC8EAD31-CD15-4B5F-91CE-AB24C4CCB811")
        self.assertEqual(snap.price_amount, 52000)
        self.assertEqual(snap.external_id, "0D872DAD-89E9-478F-86DE-77F04014CBB9")


class PriceIndexTests(Base):
    def test_model_keys_group_spellings(self):
        self.assertEqual(prices.model_key("I phone 13 Pro Max 256GB Sierra Blue"), prices.model_key("iPhone 13 pro max"))
        self.assertEqual(prices.model_key("Samsung Galaxy S22 Ultra 12/256"), "samsung-s22-ultra")
        self.assertEqual(prices.label_for("iphone-13-promax"), "iPhone 13 Pro Max")
        self.assertEqual(prices.label_for("macbook-air-m1"), "MacBook Air M1")
        self.assertEqual(prices.query_for("iphone-13-promax"), "iphone 13 pro max")

    def test_observe_dedupes_per_listing_per_day(self):
        rows = deep(RICH_MARKET["listings"])
        self.assertEqual(prices.observe(rows, "iphone-13", "iphone 13"), 6)
        self.assertEqual(prices.observe(rows, "iphone-13", "iphone 13"), 0)
        self.assertEqual(prices.observe(rows, "iphone-13", "iphone 13", observed_on=date.today() - timedelta(days=1)), 6)
        self.assertEqual(PriceObservation.query.count(), 12)

    def test_stats_count_each_listing_once_and_split_new(self):
        rows = deep(RICH_MARKET["listings"])
        prices.observe(rows, "iphone-13", observed_on=date.today() - timedelta(days=2))
        prices.observe(rows, "iphone-13")
        st = prices.stats("iphone-13")
        self.assertEqual(st["n"], 3)                       # used + like_new, once each
        self.assertEqual(st["n_new"], 3)
        self.assertEqual(st["median"], 48500)
        self.assertEqual(st["new_median"], 78000)
        self.assertEqual(st["by_condition"]["like_new"]["n"], 2)
        self.assertIsNone(st["trend_pct"])                 # not enough history
        self.assertEqual(len(prices.series("iphone-13")), 1)
        self.assertEqual([s["key"] for s in prices.guide_index(min_n=3)], ["iphone-13"])
        self.assertEqual(prices.guide_index(min_n=4), [])

    def test_trend_compares_recent_with_older(self):
        old = [{"external_id": f"O{i}", "title": "x", "price": 60000, "condition": "used"} for i in range(4)]
        new = [{"external_id": f"N{i}", "title": "x", "price": 54000, "condition": "used"} for i in range(4)]
        prices.observe(old, "m", observed_on=date.today() - timedelta(days=45))
        prices.observe(new, "m", observed_on=date.today() - timedelta(days=5))
        self.assertEqual(prices.stats("m")["trend_pct"], -10)

    def test_contribute_from_a_report_excludes_rejected_comparables(self):
        rep = deep(RICH_REPORT); rep["market_data"] = {k: v for k, v in deep(RICH_MARKET).items() if k != "excluded_listings"}
        rep["market_data"]["excluded"] = RICH_MARKET["excluded_listings"]
        n = prices.contribute(deep(RICH_LISTING), rep)
        self.assertEqual(n, 6)                             # 5 comps kept + the listing itself; the back glass is excluded
        keys = {r.external_id for r in PriceObservation.query.all()}
        self.assertIn("0D872DAD-89E9-478F-86DE-77F04014CBB9", keys)
        self.assertNotIn("E6", keys)

    def test_crawl_uses_search_rows_and_survives_failures(self):
        from unittest import mock
        import functions
        calls = []
        def fake_rows(q, pages=2, page_size=20):
            calls.append(q)
            if q == "bad":
                raise RuntimeError("boom")
            return [{"external_id": f"{q}-1", "title": q, "price": 50000, "condition": "used", "category": "Mobile Phone Handsets", "url": "https://x"}]
        with mock.patch.object(functions, "search_rows", fake_rows):
            out = prices.crawl(["iPhone 13", "bad"], pause=0, log=lambda *a: None)
        self.assertEqual(out["inserted"], 1)
        self.assertEqual(out["failed"], ["bad"])
        self.assertEqual(prices.prune(days=0), 0)         # today's rows are kept

    def test_pages(self):
        self.assertIn("No price data yet", self.c.get("/prices").get_data(as_text=True))
        self.assertEqual(self.c.get("/prices/nothing-here").status_code, 404)
        rows = deep(RICH_MARKET["listings"])
        prices.observe(rows, "iphone-13", "iphone 13")
        page = self.c.get("/prices").get_data(as_text=True)
        self.assertIn("iPhone 13", page)
        page = self.c.get("/prices/iphone-13").get_data(as_text=True)
        self.assertIn("48,500", page)
        self.assertIn("Brand new on the site", page)


if __name__ == "__main__":
    unittest.main()
