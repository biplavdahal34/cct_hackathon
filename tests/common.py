"""Shared test scaffolding: in-memory database, a logged-in user, and a fake
scrape / Gemini pipeline so no test touches the network. Import this first in
every test module; it sets the environment before the app is created."""
import json
import os
import unittest
from unittest import mock

from cryptography.fernet import Fernet

os.environ["DATABASE_URL"] = "sqlite://"                 # in-memory
os.environ["KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["FREE_ANALYSES_PER_HOUR"] = "6"
os.environ["FREE_FACEBOOK_PER_HOUR"] = "2"
os.environ["SESSION_COOKIE_SECURE"] = "0"                # tests run over plain HTTP; .env may say 1
os.environ["CHAT_MESSAGES_PER_LISTING"] = "10"
os.environ["CHAT_MESSAGES_PER_DAY_GLOBAL"] = "300"
os.environ["CALIBRATION_MIN_REPORTS"] = "3"

import flask_app                                          # noqa: E402
import api_keys                                           # noqa: E402
import routes_main                                        # noqa: E402
import security                                           # noqa: E402
from extensions import db                                 # noqa: E402
from models import User                                   # noqa: E402

app = flask_app.app
app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
app.login_manager.session_protection = None

HB = "https://hamrobazaar.com/detail/phones/x/ABC"
HB_GUID = "https://hamrobazaar.com/detail/mobile-phone-handsets/iphone-13/0D872DAD-89E9-478F-86DE-77F04014CBB9"
FB = "https://www.facebook.com/marketplace/item/1443300707693531/"

FAKE_REPORT = {"verdict": {"rating": "buy_worthy", "score_0_100": 80, "badge_color": "green",
                           "one_liner": "ok", "reasoning": "ok"},
               "product_summary": {"title": "Test phone", "model": "Test", "asking_price": {"amount": 1000, "currency": "NPR"}},
               "device_category": "phone", "image_analysis": {"per_image": []},
               "description_analysis": {}, "buyer_guidance": {}, "red_flags": [], "green_flags": []}

# a fuller report, the shape the real pipeline produces, for page-rendering tests
RICH_REPORT = {
    "device_category": "phone",
    "product_summary": {"title": "iPhone 13 128GB", "model": "iPhone 13", "model_number": "A2482", "storage": "128",
                        "stated_condition": "Like new", "assessed_condition": "good", "matches_stated_condition": "yes",
                        "asking_price": {"amount": 52000, "currency": "NPR", "negotiable": True}, "location": "Kathmandu"},
    "description_analysis": {"original_description": "Selling my iPhone 13, 1 year used, BH 91%, box and bill available, meet in Baneshwor.",
                             "translated_description": "Selling my iPhone 13, used one year, battery health 91%, box and bill available, meet in Baneshwor.",
                             "extracted_claims": {"battery_health_percent": 91, "box_included": True, "accessories": ["box", "bill"],
                                                  "reason_for_selling": None, "other_notes": []},
                             "category_specific_claims": {"face_id": "claimed_working"}, "claim_credibility": "reads honest"},
    "image_analysis": {"images_reviewed": 4, "per_image": [
        {"image_index": i, "summary": f"photo {i}", "photo_authenticity": "real_device_photo",
         "findings": ([{"type": "scratch", "location": "frame", "severity": "minor", "confidence": "medium", "notes": "light"}] if i == 2 else [])}
        for i in range(1, 5)], "not_verifiable_from_images": ["battery health"]},
    "red_flags": [], "green_flags": ["Box and bill included", "Battery health stated"],
    "buyer_guidance": {"questions_to_ask_seller": ["Why selling?"], "in_person_checks": ["Battery Health menu"],
                       "negotiation_leverage": ["Minor frame scratch"], "walk_away_if": ["Activation lock"]},
    "verdict": {"rating": "buy_worthy", "score_0_100": 82, "badge_color": "green", "one_liner": "A clean iPhone 13 at a fair price.",
                "reasoning": "Good photos, stated battery, box and bill.", "ideal_for": "most buyers", "not_ideal_for": "nobody"},
    "limitations": ["Battery health not verifiable from photos"],
}
RICH_MARKET = {"price_analysis": {"verdict": "fair", "estimated_fair_range_npr": [46000, 54000], "reasoning": "In range.", "confidence": "high"},
               "listings_used": 5, "market_stats": {"min": 42000, "max": 60000, "median": 49000, "average": 50000},
               "excluded_listings": [{"title": "iPhone 13 back glass", "reason": "part"}],
               "listings": [{"title": "iphone 13 128gb", "price": 42000, "url": "https://hamrobazaar.com/detail/a/1", "external_id": "E1", "condition": "used", "category": "Mobile Phone Handsets", "seller_key": "hamrobazaar:S1", "seller_name": "A", "seller_ad_count": 1, "posted_at": None, "image_url": None, "negotiable": True},
                            {"title": "iPhone 13 128gb", "price": 48500, "url": "https://hamrobazaar.com/detail/a/2", "external_id": "E2", "condition": "like_new", "category": "Mobile Phone Handsets", "seller_key": "hamrobazaar:S2", "seller_name": "B", "seller_ad_count": 5, "posted_at": None, "image_url": None, "negotiable": True},
                            {"title": "iPhone 13 Pink 128gb", "price": 49000, "url": "https://hamrobazaar.com/detail/a/3", "external_id": "E3", "condition": "like_new", "category": "Mobile Phone Handsets", "seller_key": None, "seller_name": None, "seller_ad_count": None, "posted_at": None, "image_url": None, "negotiable": False},
                            {"title": "Iphone 13 128 GB brand new", "price": 78000, "url": "https://hamrobazaar.com/detail/a/4", "external_id": "E4", "condition": "brand_new", "category": "Mobile Phone Handsets", "seller_key": None, "seller_name": None, "seller_ad_count": None, "posted_at": None, "image_url": None, "negotiable": False},
                            {"title": "Iphone 13 128 GB sealed", "price": 80000, "url": "https://hamrobazaar.com/detail/a/5", "external_id": "E5", "condition": "brand_new", "category": "Mobile Phone Handsets", "seller_key": None, "seller_name": None, "seller_ad_count": None, "posted_at": None, "image_url": None, "negotiable": False},
                            {"title": "iPhone 13 back glass", "price": 4000, "url": "https://hamrobazaar.com/detail/a/6", "external_id": "E6", "condition": "brand_new", "category": "Mobile Phone Handsets", "seller_key": None, "seller_name": None, "seller_ad_count": None, "posted_at": None, "image_url": None, "negotiable": False}],
               "new_price_anchor": 79000, "new_listings_used": 3}
RICH_LISTING = {"title": "iPhone 13 128GB", "condition": "Like new", "images": [], "product_img": [], "source": "hamrobazaar",
                "price": {"amount": 52000, "currency": "NPR", "type": "Negotiable"}, "address": "Kathmandu",
                "description": "Selling my iPhone 13, 1 year used, BH 91%, box and bill available, meet in Baneshwor.",
                "seller": {"name": "Ram Seller", "phone": "98******12", "profile_url": "https://hamrobazaar.com/user/ram/AC8EAD31-CD15-4B5F-91CE-AB24C4CCB811",
                           "profile_picture": None, "id": "AC8EAD31-CD15-4B5F-91CE-AB24C4CCB811", "ad_count": 3, "badge": "No badge"},
                "specifications": {"Storage": "128 GB"}, "external_id": "0D872DAD-89E9-478F-86DE-77F04014CBB9",
                "posted_at": "2026-10-20 10:00:00", "views": 120}


def deep(d):
    return json.loads(json.dumps(d))


class Base(unittest.TestCase):
    """Fresh database and a logged-in user per test; the pipeline is faked and
    records the keys it was handed in self.calls."""
    rich = False      # subclasses set True to get the fuller report/listing from the fake pipeline

    def setUp(self):
        self.ctx = app.app_context(); self.ctx.push()
        db.drop_all(); db.create_all()
        security.limiter._hits.clear()                    # in-process limiter persists across tests
        self.user = User(username="sita", email="sita@example.com"); self.user.set_password("pass1234")
        db.session.add(self.user); db.session.commit()
        self.c = app.test_client()
        self.login(self.user)
        self.calls = []
        rich = self.rich

        def fake_scrape(url, apify_token=None):
            self.calls.append(("scrape", apify_token))
            return deep(RICH_LISTING) if rich else {"title": "t", "images": [], "source": "x"}

        def fake_analyze(listing, gemini_key=None, max_images=None, images=None):
            self.calls.append(("analyze", gemini_key))
            return deep(RICH_REPORT) if rich else dict(FAKE_REPORT, verdict=dict(FAKE_REPORT["verdict"]))

        def fake_market(*a, gemini_key=None, **k):
            self.calls.append(("market", gemini_key))
            return deep(RICH_MARKET) if rich else {"price_analysis": {}, "listings": []}

        self.patches = [mock.patch.object(routes_main, "scrape_any", fake_scrape),
                        mock.patch.object(routes_main, "analyze", fake_analyze),
                        mock.patch.object(routes_main, "market_analysis", fake_market),
                        mock.patch.object(routes_main, "download_images", lambda urls, max_n=None: []),
                        mock.patch.object(routes_main, "cache_get", lambda url: None),
                        mock.patch.object(routes_main, "cache_put", lambda url, l: None),
                        mock.patch.object(routes_main, "csrf_ok", lambda: True),
                        mock.patch.object(routes_main, "validate_csrf", lambda *a, **k: None),
                        mock.patch.dict(api_keys._VALIDATORS, {"gemini": lambda k: None, "apify": lambda k: None})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def login(self, user):
        with self.c.session_transaction() as s:
            s["_user_id"] = str(user.id); s["_fresh"] = True
        # the test keeps one app context pushed, so requests reuse it and Flask-Login's
        # per-context user cache would otherwise keep the previous user
        from flask import g
        g.pop("_login_user", None)

    def analyze(self, url, n=None, **form):
        """POST a listing; `n` makes a distinct listing (reuse would otherwise short-circuit)."""
        data = {"url": url if n is None else f"{url.rstrip('/')}{n}/"}
        data.update(form)
        return self.c.post("/", data=data)

    def analysis_id(self, resp):
        loc = resp.headers.get("Location", "")
        assert loc.startswith("/report/"), loc
        return int(loc.rsplit("/", 1)[1])

    def flashes(self, path="/"):
        return self.c.get(path).get_data(as_text=True)
