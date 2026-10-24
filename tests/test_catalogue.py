"""The price pages: the models BEAT has checked, and a price worked out from the verdicts on them."""
import json
import os
import unittest
from itertools import count
from unittest import mock

from tests.common import Base, HB
from extensions import db
from models import Analysis
import catalogue
import prices
from knowledge import products

_n = count(1)


class MatchingTests(unittest.TestCase):
    def test_a_title_is_matched_to_a_line_and_a_model(self):
        c = catalogue.classify("Acer Nitro V 15 ANV15-51-75VW Gaming Laptop")
        self.assertEqual((c["line"], c["number"], c["variant"], c["year"]), ("acer-nitro-v", "ANV15-51", "anv15-51", 2023))
        c = catalogue.classify("Lenovo LOQ 15ARP9 Ryzen 5 7235HS / RTX 4050")
        self.assertEqual((c["line"], c["variant"], c["year"]), ("lenovo-loq", "15arp9", 2024))
        c = catalogue.classify("Samsung Galaxy S20 FE SM-G781B")                  # the letter belongs to the same model
        self.assertEqual((c["line"], c["variant"]), ("samsung-galaxy-s20-fe", "g781"))
        self.assertEqual(catalogue.classify("Acer Aspire 3 A315-59G i5")["variant"], "a315-59")

    def test_a_listing_without_a_model_number_belongs_to_the_line_only(self):
        for t, line in (("Lenovo LOQ Gaming Laptop", "lenovo-loq"), ("Samsung Galaxy S20 FE 5G", "samsung-galaxy-s20-fe")):
            c = catalogue.classify(t)
            self.assertEqual(c["line"], line)
            self.assertIsNone(c["variant"])

    def test_an_unknown_model_number_becomes_its_own_model(self):
        c = catalogue.classify("ANV16-72 brand new")
        self.assertEqual((c["variant"], c["generation"], c["year"]), ("anv16-72", None, None))

    def test_other_products_are_not_matched(self):
        for t in ("Acer Nitro 5 AN515-58", "Lenovo ThinkPad T14", "Acer Predator Helios", "iPhone 13", "Samsung Galaxy S20 Ultra"):
            self.assertEqual(catalogue.classify(t), {}, t)

    def test_the_crawler_is_seeded_with_the_lines(self):
        for q in ("Acer Nitro V", "Lenovo LOQ", "Samsung S20 FE"):
            self.assertIn(q, prices.SEED_MODELS)


class PriceLogicTests(unittest.TestCase):
    @staticmethod
    def pt(price, score):
        return {"price": price, "score": score}

    def test_a_higher_score_counts_for_more(self):
        self.assertEqual(catalogue.weight(95), 1.0)
        self.assertEqual(catalogue.weight(90), 1.0)
        self.assertAlmostEqual(catalogue.weight(55), 0.5)
        self.assertEqual(catalogue.weight(20), 0.05)
        self.assertEqual(catalogue.weight(0), 0.05)                       # a floor: a bad score is still a data point

    def test_a_buy_worthy_price_pulls_the_estimate_toward_itself(self):
        # a Buy worthy listing at 150,000 and a Caution one at 200,000: the price sits close to 150,000
        est = catalogue.estimate([self.pt(150000, 80), self.pt(200000, 38)])
        self.assertGreater(est["price"], 150000)
        self.assertLess(est["price"], 175000)
        self.assertLess(est["price"] - 150000, 200000 - est["price"])

    def test_the_same_goes_the_other_way_round(self):
        est = catalogue.estimate([self.pt(200000, 80), self.pt(150000, 38)])
        self.assertGreater(est["price"], 175000)

    def test_equal_verdicts_give_the_plain_average(self):
        self.assertEqual(catalogue.estimate([self.pt(100000, 80), self.pt(120000, 80)])["price"], 110000)

    def test_a_single_listing_is_its_own_price_and_is_not_enough(self):
        est = catalogue.estimate([self.pt(95000, 82)])
        self.assertEqual((est["n"], est["price"], est["enough"], est["n_good"]), (1, 95000, False, 1))
        self.assertEqual((est["ok_low"], est["ok_high"]), (95000, 95000))

    def test_five_listings_are_enough(self):
        self.assertTrue(catalogue.estimate([self.pt(100000 + i, 80) for i in range(5)])["enough"])
        self.assertFalse(catalogue.estimate([self.pt(100000 + i, 80) for i in range(4)])["enough"])

    def test_listings_that_were_all_rated_badly_are_flagged_as_a_weak_guide(self):
        est = catalogue.estimate([self.pt(60000, 30), self.pt(65000, 25)])
        self.assertTrue(est["weak"])
        self.assertIsNone(est["ok_low"])
        self.assertFalse(catalogue.estimate([self.pt(60000, 30), self.pt(65000, 70)])["weak"])

    def test_the_acceptable_range_only_covers_listings_rated_acceptable(self):
        est = catalogue.estimate([self.pt(150000, 80), self.pt(140000, 60), self.pt(200000, 38)])
        self.assertEqual((est["ok_low"], est["ok_high"], est["low"], est["high"]), (140000, 150000, 140000, 200000))

    def test_prices_are_rounded_sensibly(self):
        self.assertEqual(catalogue.estimate([self.pt(52300, 80)])["price"], 52500)
        self.assertEqual(catalogue.estimate([self.pt(7480, 80)])["price"], 7500)
        self.assertEqual(catalogue.estimate([])["n"], 0)


class CatBase(Base):
    def setUp(self):
        super().setUp()
        self._cache = catalogue.CACHE_SECONDS
        catalogue.CACHE_SECONDS = 0
        self.rctx = catalogue.current_app.test_request_context()      # the page builders make links, which needs a request
        self.rctx.push()

    def tearDown(self):
        self.rctx.pop()
        catalogue.CACHE_SECONDS = self._cache
        super().tearDown()

    def add(self, title, price, score, rating="buy_worthy", model=None, number=None, category="laptop", currency="NPR",
            url_key=None, cond="Used", created=None):
        n = next(_n)
        a = Analysis(user_id=self.user.id, url=f"https://hamrobazaar.com/detail/x/{n}", url_key=url_key or f"key{n}", title=title,
                     source="hamrobazaar", device_category=category, rating=rating, badge_color="green", score=score,
                     price_amount=price, price_currency=currency, listing_json="{}",
                     report_json=json.dumps({"product_summary": {"model": model or title, "model_number": number, "stated_condition": cond}}))
        if created:
            a.created_at = created
        db.session.add(a)
        db.session.commit()
        return a


class DataTests(CatBase):
    def test_only_models_that_were_checked_appear(self):
        self.add("Lenovo LOQ Gaming Laptop", 115000, 81)
        ids = [c["id"] for c in catalogue.home_page()["cards"]]
        self.assertEqual(ids, ["lenovo-loq"])

    def test_a_model_that_matches_no_line_still_gets_a_card_of_its_own(self):
        self.add("Xiaomi Redmi Note 13 8/256", 30000, 75, category="phone", model="Xiaomi Redmi Note 13")
        card = catalogue.home_page()["cards"][0]
        self.assertTrue(card["id"].startswith("m-"))
        self.assertEqual((card["name"], card["kind"], card["image"]), ("Xiaomi Redmi Note 13", "phone", None))

    def test_desktops_parts_and_foreign_currency_are_left_out(self):
        self.add("Custom gaming PC", 90000, 70, category="desktop")
        self.add("Lenovo LOQ priced in dollars", 900, 80, currency="USD")
        self.add("Lenovo LOQ with no score", 100000, 80).score = None
        db.session.commit()
        self.assertEqual(catalogue.home_page()["cards"], [])

    def test_a_listing_checked_twice_counts_once_and_the_newest_wins(self):
        from datetime import datetime, timedelta
        self.add("Lenovo LOQ Gaming Laptop", 120000, 70, url_key="same", created=datetime.utcnow() - timedelta(days=3))
        self.add("Lenovo LOQ Gaming Laptop", 110000, 80, url_key="same")
        est = catalogue.line_page("lenovo-loq")["est"]
        self.assertEqual((est["n"], est["price"]), (1, 110000))

    def test_the_users_example_end_to_end(self):
        self.add("Lenovo LOQ 15ARP9 gaming laptop", 150000, 80, rating="buy_worthy")
        self.add("Lenovo LOQ 15ARP9 gaming laptop, urgent", 200000, 38, rating="caution")
        page = catalogue.variant_page("lenovo-loq", "15arp9")
        self.assertLess(page["est"]["price"] - 150000, 200000 - page["est"]["price"])
        self.assertEqual([r["price"] for r in page["rows"]], [150000, 200000])      # best verdict first
        self.assertEqual(page["rows"][0]["label"], "Buy worthy")
        self.assertEqual(page["rows"][1]["tone"], "warn")
        self.assertLess(page["rows"][1]["weight_pct"], page["rows"][0]["weight_pct"])
        self.assertGreater(page["rows"][1]["diff"], 0)                              # the Caution one asks more than the estimate

    def test_models_come_from_model_numbers_and_listings_without_one_stay_at_the_line(self):
        self.add("Lenovo LOQ 15ARP9", 115000, 81)
        self.add("Lenovo LOQ 15IAX9", 105000, 75)
        self.add("Lenovo LOQ Gaming Laptop", 125000, 70)
        pg = catalogue.line_page("lenovo-loq")
        self.assertEqual([v["id"] for v in pg["variants"]], ["15arp9", "15iax9"])          # newest year first, then the older
        self.assertEqual(pg["unplaced"], 1)
        self.assertEqual(pg["est"]["n"], 3)


class PageTests(CatBase):
    def test_prices_opens_on_the_cards_with_a_search_and_no_default_product(self):
        self.add("Lenovo LOQ Gaming Laptop", 115000, 81)
        page = self.c.get("/prices").get_data(as_text=True)
        self.assertIn("Find a model", page)
        self.assertIn('id="finderQ"', page)
        self.assertEqual(page.count('class="line-card"'), 1)
        self.assertNotIn("Nitro", page)                                # no product is preselected
        self.assertLess(page.index('id="finderQ"'), page.index('class="line-card"'))

    def test_an_empty_database_says_so(self):
        page = self.c.get("/prices").get_data(as_text=True)
        self.assertIn("No models have been checked yet", page)
        self.assertEqual(page.count('class="line-card"'), 0)

    def test_the_cards_show_a_picture_and_a_name_and_nothing_else(self):
        self.add("Lenovo LOQ Gaming Laptop", 115000, 81)
        self.add("Samsung Galaxy S20 FE 5G", 15000, 81, category="phone")
        self.add("Acer Nitro V 15 ANV15-51-75VW", 95000, 82)
        grid = self.c.get("/prices").get_data(as_text=True).split('class="line-grid"')[1].split("</div>\n<p")[0]
        self.assertEqual(grid.count('class="line-card"'), 3)
        for name in ("Lenovo LOQ", "Samsung Galaxy S20 FE", "Acer Nitro V"):
            self.assertIn(name, grid)
        self.assertIn("/static/products/lenovo-loq.png", grid)           # the manufacturer's picture
        self.assertIn("/static/products/samsung-galaxy-s20-fe.png", grid)
        self.assertIn("<svg", grid)                                      # Nitro V has no picture yet, so an outline
        for word in ("listing", "NPR", "score"):
            self.assertNotIn(word, grid)

    def test_a_page_exists_only_for_what_was_checked(self):
        self.add("Lenovo LOQ 15ARP9", 115000, 81)
        for path, code in (("/prices/lenovo-loq", 200), ("/prices/lenovo-loq/15arp9", 200), ("/prices/lenovo-loq/15iax9", 404),
                           ("/prices/acer-nitro-v", 404), ("/prices/nope", 404), ("/prices/laptops", 404), ("/prices/iphone-13", 404)):
            self.assertEqual(self.c.get(path).status_code, code, path)

    def test_few_listings_get_the_notice_and_five_get_the_price_without_it(self):
        for i in range(4):
            self.add("Lenovo LOQ 15ARP9", 110000 + i * 5000, 80)
        page = self.c.get("/prices/lenovo-loq/15arp9").get_data(as_text=True)
        self.assertIn("Not enough listings for an accurate price", page)
        self.assertIn("BEAT has checked 4 listings for this model", page)
        self.assertIn("evaluate the one you are looking at yourself", page)
        self.assertIn("Estimated fair price", page)
        self.add("Lenovo LOQ 15ARP9", 125000, 80)
        page = self.c.get("/prices/lenovo-loq/15arp9").get_data(as_text=True)
        self.assertNotIn("Not enough listings for an accurate price", page)
        self.assertIn("NPR 119,000", page)

    def test_the_verdict_table_and_the_explanation_are_on_the_page(self):
        self.add("Lenovo LOQ 15ARP9", 150000, 80)
        self.add("Lenovo LOQ 15ARP9", 200000, 38, rating="caution")
        page = self.c.get("/prices/lenovo-loq/15arp9").get_data(as_text=True)
        for text in ("Listings behind the price", "Buy worthy", "Caution", "NPR 150,000", "NPR 200,000", "Counts for",
                     "How this price is worked out", "counts for far more than one rated Caution"):
            self.assertIn(text, page, text)

    def test_a_line_page_mixes_models_and_says_so(self):
        self.add("Lenovo LOQ 15ARP9", 115000, 81)
        self.add("Lenovo LOQ 15IAX9", 105000, 75)
        page = self.c.get("/prices/lenovo-loq").get_data(as_text=True)
        self.assertIn("This mixes every model below", page)
        self.assertIn("By model", page)
        self.assertIn("/prices/lenovo-loq/15arp9", page)
        for text in ("What it is", "Models and years", "Latest models in this line", "Draft"):
            self.assertIn(text, page, text)

    def test_the_search_offers_only_models_that_were_checked(self):
        self.add("Lenovo LOQ 15ARP9", 115000, 81)
        self.add("Samsung Galaxy S20 FE 5G", 15000, 81, category="phone")
        page = self.c.get("/prices").get_data(as_text=True)
        panel = page.split('id="finderPanel"')[1].split('class="finder-help"')[0]
        self.assertEqual(panel.count('class="mchip"'), 3)                # two lines and the LOQ model number
        self.assertIn("/prices/lenovo-loq/15arp9", panel)
        self.assertNotIn("15iax9", panel)
        line = self.c.get("/prices/lenovo-loq").get_data(as_text=True)
        self.assertEqual(line.split('id="finderPanel"')[1].split('class="finder-help"')[0].count('class="mchip"'), 1)

    def test_an_unregistered_model_has_a_plain_page_without_a_draft_note(self):
        self.add("Xiaomi Redmi Note 13", 30000, 75, category="phone")
        slug = catalogue.home_page()["cards"][0]["id"]
        page = self.c.get(f"/prices/{slug}").get_data(as_text=True)
        self.assertIn("Xiaomi Redmi Note 13", page)
        self.assertIn("Estimated fair price", page)
        self.assertNotIn("Draft", page)
        self.assertIn("What to check when you meet", page)               # the inspection steps BEAT already has for phones

    def test_listing_photos_and_hostile_titles_never_reach_the_page(self):
        self.add("Lenovo LOQ <script>alert(1)</script> 15ARP9", 115000, 81)
        page = self.c.get("/prices/lenovo-loq/15arp9").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("/img?u=", page)                                # the listing-photo proxy is never used here

    def test_the_manufacturers_picture_is_credited_on_the_page(self):
        self.add("Lenovo LOQ Gaming Laptop", 115000, 81)
        page = self.c.get("/prices/lenovo-loq").get_data(as_text=True)
        self.assertIn("/static/products/lenovo-loq.png", page)
        self.assertIn("Image:", page)
        self.assertIn("https://www.lenovo.com/us/en/c/laptops/loq-laptops/", page)

    def test_a_report_links_to_the_page_its_own_listing_made(self):
        self.rich = True
        self.setUp()
        catalogue.CACHE_SECONDS = 0
        page = self.c.get(f"/report/{self.analysis_id(self.analyze(HB))}").get_data(as_text=True)
        self.assertIn("See the price for this model", page)
        link = page.split("See the price for this model")[0].rsplit('href="', 1)[1].split('"')[0]
        self.assertTrue(link.startswith("/prices/"))
        self.assertEqual(self.c.get(link).status_code, 200)

    def test_the_search_script_and_the_nav_are_wired(self):
        page = self.c.get("/prices").get_data(as_text=True)
        self.assertIn("/static/js/app.js", page)
        js = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "app.js"), encoding="utf-8").read()
        self.assertIn('getElementById("finder")', js)


class PictureTests(CatBase):
    def test_a_missing_picture_is_an_outline_and_a_present_one_is_used_with_its_credit(self):
        folder = os.path.join(catalogue.current_app.static_folder, "products")
        path = os.path.join(folder, "samsung-galaxy-s22-ultra.png")
        self.assertFalse(os.path.exists(path))
        self.add("Samsung Galaxy S22 Ultra 12GB/256GB", 54000, 68, category="phone")
        self.assertIsNone(catalogue.home_page()["cards"][0]["image"])
        credit = {"owner": "Samsung", "source": "https://www.samsung.com/"}
        try:
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            with mock.patch.dict(products.IMAGE_CREDITS, {"samsung-galaxy-s22-ultra": credit}):
                self.assertEqual(catalogue.home_page()["cards"][0]["image"], "/static/products/samsung-galaxy-s22-ultra.png")
                page = self.c.get("/prices/samsung-galaxy-s22-ultra").get_data(as_text=True)
                self.assertIn("/static/products/samsung-galaxy-s22-ultra.png", page)
                self.assertIn("https://www.samsung.com/", page)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
