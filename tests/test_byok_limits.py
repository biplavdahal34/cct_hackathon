"""BYOK storage + usage caps.  Run:  venv/bin/python -m unittest -v
Uses an in-memory database and fakes the scrape/Gemini pipeline."""
import os
import unittest
from unittest import mock

from cryptography.fernet import Fernet

os.environ["DATABASE_URL"] = "sqlite://"                 # in-memory
os.environ["KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["FREE_ANALYSES_PER_HOUR"] = "6"
os.environ["FREE_FACEBOOK_PER_HOUR"] = "2"
os.environ["SESSION_COOKIE_SECURE"] = "0"                # tests run over plain HTTP; .env may say 1

import flask_app                                          # noqa: E402
import api_keys                                           # noqa: E402
import crypto                                             # noqa: E402
import functions                                          # noqa: E402
import routes_main                                        # noqa: E402
from extensions import db                                 # noqa: E402
from models import User, UserApiKey, UsageEvent, KeyEvent  # noqa: E402

app = flask_app.app
app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
app.login_manager.session_protection = None

HB = "https://hamrobazaar.com/detail/phones/x/ABC"
FB = "https://www.facebook.com/marketplace/item/1443300707693531/"

FAKE_REPORT = {"verdict": {"rating": "buy_worthy", "score_0_100": 80, "badge_color": "green",
                           "one_liner": "ok", "reasoning": "ok"},
               "product_summary": {"title": "Test phone", "model": "Test", "asking_price": {"amount": 1000, "currency": "NPR"}},
               "device_category": "phone", "image_analysis": {"per_image": []},
               "description_analysis": {}, "buyer_guidance": {}, "red_flags": [], "green_flags": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context(); self.ctx.push()
        db.drop_all(); db.create_all()
        __import__("security").limiter._hits.clear()        # in-process limiter persists across tests
        self.user = User(username="sita", email="sita@example.com"); self.user.set_password("pass1234")
        db.session.add(self.user); db.session.commit()
        self.c = app.test_client()
        with self.c.session_transaction() as s:
            s["_user_id"] = str(self.user.id); s["_fresh"] = True
        # fake pipeline: records the keys it was handed
        self.calls = []
        def fake_scrape(url, apify_token=None):
            self.calls.append(("scrape", apify_token)); return {"title": "t", "images": [], "source": "x"}
        def fake_analyze(listing, gemini_key=None, max_images=None):
            self.calls.append(("analyze", gemini_key)); return dict(FAKE_REPORT, verdict=dict(FAKE_REPORT["verdict"]))
        def fake_market(*a, gemini_key=None, **k):
            self.calls.append(("market", gemini_key)); return {"price_analysis": {}, "listings": []}
        self.patches = [mock.patch.object(routes_main, "scrape_any", fake_scrape),
                        mock.patch.object(routes_main, "analyze", fake_analyze),
                        mock.patch.object(routes_main, "market_analysis", fake_market),
                        mock.patch.object(routes_main, "cache_get", lambda url: None),
                        mock.patch.object(routes_main, "cache_put", lambda url, l: None),
                        mock.patch.object(routes_main, "csrf_ok", lambda: True),
                        mock.patch.dict(api_keys._VALIDATORS, {"gemini": lambda k: None, "apify": lambda k: None})]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def analyze(self, url, n=None):
        """POST a listing; `n` makes a distinct listing (reuse would otherwise short-circuit)."""
        return self.c.post("/", data={"url": url if n is None else f"{url.rstrip('/')}{n}/"})


class CryptoTests(Base):
    def test_roundtrip_and_ciphertext_is_opaque(self):
        token = crypto.encrypt("AIza-secret-key-123456789")
        self.assertNotIn("secret", token)
        self.assertEqual(crypto.decrypt(token), "AIza-secret-key-123456789")

    def test_saved_key_is_encrypted_and_audited(self):
        api_keys.save_key(self.user.id, "gemini", "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", ip="1.2.3.4")
        row = UserApiKey.query.filter_by(user_id=self.user.id, provider="gemini").one()
        self.assertNotIn("FAKE", row.ciphertext)
        self.assertEqual(row.last4, "1234")
        self.assertEqual(api_keys.load_keys(self.user.id)["gemini"], "AIzaSyFAKEFAKEFAKEFAKEFAKE1234")
        self.assertEqual([e.event for e in KeyEvent.query.all()], ["added"])

    def test_undecryptable_row_is_skipped_not_raised(self):
        api_keys.save_key(self.user.id, "gemini", "AIzaSyFAKEFAKEFAKEFAKEFAKE1234")
        with mock.patch.dict(os.environ, {"KEY_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            self.assertEqual(api_keys.load_keys(self.user.id), {})
        self.assertIn("undecryptable", [e.event for e in KeyEvent.query.all()])


class LimitTests(Base):
    def test_shared_key_user_capped_at_six_per_hour(self):
        for i in range(6):
            r = self.analyze(HB, i)
            self.assertTrue(r.headers["Location"].startswith("/report/"), f"attempt {i+1} blocked early")
        r = self.analyze(HB, 99)
        self.assertEqual(r.headers["Location"], "/")
        page = self.c.get("/").get_data(as_text=True)
        self.assertIn("6 free checks", page)
        self.assertIn("Manage keys", page)
        self.assertEqual(UsageEvent.query.count(), 6)        # the blocked attempt was rolled back

    def test_byok_user_is_not_metered(self):
        api_keys.save_key(self.user.id, "gemini", "AIzaSyFAKEFAKEFAKEFAKEFAKE1234")
        for i in range(8):
            self.assertTrue(self.analyze(HB, i).headers["Location"].startswith("/report/"))
        self.assertEqual(UsageEvent.query.count(), 0)
        self.assertEqual([c for c in self.calls if c[0] == "analyze"][0][1], "AIzaSyFAKEFAKEFAKEFAKEFAKE1234")
        self.assertTrue(UserApiKey.query.first().last_used_at)

    def test_facebook_has_its_own_smaller_cap(self):
        self.assertTrue(self.analyze(FB, 1).headers["Location"].startswith("/report/"))
        self.assertTrue(self.analyze(FB, 2).headers["Location"].startswith("/report/"))
        self.assertEqual(self.analyze(FB, 3).headers["Location"], "/")       # 3rd Facebook blocked
        self.assertTrue(self.analyze(HB, 1).headers["Location"].startswith("/report/"))  # Hamrobazaar still fine
        self.assertIn("Facebook listings are limited to 2", self.c.get("/").get_data(as_text=True))

    def test_apify_key_exempts_facebook_cap_but_not_gemini_cap(self):
        api_keys.save_key(self.user.id, "apify", "apify_api_FAKEFAKEFAKEFAKEFAKE1234")
        for i in range(6):
            self.assertTrue(self.analyze(FB, i).headers["Location"].startswith("/report/"))
        self.assertEqual(self.analyze(FB, 99).headers["Location"], "/")   # 7th: Gemini hourly cap
        self.assertEqual(self.calls[0], ("scrape", "apify_api_FAKEFAKEFAKEFAKEFAKE1234"))

    def test_unsupported_url_is_rejected_before_metering(self):
        self.assertEqual(self.analyze("https://evil.com/?hamrobazaar.com").headers["Location"], "/")
        self.assertEqual(UsageEvent.query.count(), 0)


class KeyRouteTests(Base):
    def test_save_requires_password(self):
        r = self.c.post("/account/keys/gemini", data={"api_key": "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", "password": "wrong"})
        self.assertEqual(UserApiKey.query.count(), 0)
        self.assertIn("Wrong password", self.c.get("/account").get_data(as_text=True))

    def test_save_then_remove(self):
        self.c.post("/account/keys/gemini", data={"api_key": "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", "password": "pass1234", "action": "save"})
        page = self.c.get("/account").get_data(as_text=True)
        self.assertIn("····1234", page)
        self.assertNotIn("FAKEFAKE", page)                                 # never rendered back
        self.c.post("/account/keys/gemini", data={"password": "pass1234", "action": "remove"})
        self.assertEqual(UserApiKey.query.count(), 0)
        self.assertEqual([e.event for e in KeyEvent.query.all()], ["added", "removed"])

    def test_invalid_key_is_not_stored(self):
        def reject(k): raise api_keys.KeyInvalid("Google rejected that key.")
        with mock.patch.dict(api_keys._VALIDATORS, {"gemini": reject}):
            self.c.post("/account/keys/gemini", data={"api_key": "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", "password": "pass1234"})
        self.assertEqual(UserApiKey.query.count(), 0)

    def test_unknown_provider_404(self):
        self.assertEqual(self.c.post("/account/keys/openai", data={"password": "pass1234"}).status_code, 404)


class ApifyTransportTests(unittest.TestCase):
    def test_token_goes_in_header_not_url(self):
        seen = {}
        class Resp:
            def raise_for_status(self): pass
            def json(self): return []
        def fake_post(url, **kw):
            seen.update(kw); seen["url"] = url; return Resp()
        with mock.patch.object(functions.requests, "post", fake_post):
            functions._run_actor("apify/facebook-marketplace-scraper", {}, token="apify_api_SECRET")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer apify_api_SECRET")
        self.assertNotIn("SECRET", seen["url"])
        self.assertNotIn("params", seen)
        self.assertIn("apify%2Ffacebook-marketplace-scraper", seen["url"])   # actor id stays URL-encoded


if __name__ == "__main__":
    unittest.main()


from report_views import canonical_url                    # noqa: E402
from datetime import datetime, timedelta, timezone       # noqa: E402
from models import Analysis                               # noqa: E402


class CanonicalUrlTests(unittest.TestCase):
    def test_facebook_variants_collapse_to_item_id(self):
        a = canonical_url("https://www.facebook.com/marketplace/item/1443300707693531/?ref=browse_tab&referral_code=x")
        b = canonical_url("https://m.facebook.com/marketplace/item/1443300707693531")
        self.assertEqual(a, b)
        self.assertEqual(a, "https://facebook.com/marketplace/item/1443300707693531")

    def test_hamrobazaar_ignores_query_slash_and_case(self):
        a = canonical_url("https://HamroBazaar.com/detail/laptops/acer-x/4A14949E/?utm=1#x")
        b = canonical_url("https://www.hamrobazaar.com/detail/laptops/acer-x/4A14949E")
        self.assertEqual(a, b)

    def test_different_listings_stay_different(self):
        self.assertNotEqual(canonical_url("https://hamrobazaar.com/detail/a/1"),
                            canonical_url("https://hamrobazaar.com/detail/a/2"))


class ReuseTests(Base):
    def pipeline_runs(self):
        return len([c for c in self.calls if c[0] == "analyze"])

    def test_same_user_is_sent_to_their_existing_report(self):
        first = self.analyze(HB).headers["Location"]
        second = self.analyze(HB + "?utm_source=share").headers["Location"]
        self.assertEqual(first, second)
        self.assertEqual(self.pipeline_runs(), 1)
        self.assertEqual(UsageEvent.query.count(), 1)                    # the reuse wasn't metered
        self.assertIn("already checked recently", self.c.get(second).get_data(as_text=True))

    def test_other_users_report_is_copied_not_rerun(self):
        other = User(username="ram", email="ram@example.com"); other.set_password("pass1234")
        db.session.add(other); db.session.commit()
        db.session.add(Analysis(user_id=other.id, url=HB, url_key=canonical_url(HB), title="Ram's check",
                                source="hamrobazaar", device_category="phone", rating="avoid",
                                badge_color="red", score=10,
                                report_json=__import__("json").dumps(dict(FAKE_REPORT, product_summary={"title": "Ram's check"})),
                                listing_json='{"images": [], "url": "%s", "source": "hamrobazaar"}' % HB))
        db.session.commit()
        loc = self.analyze(HB).headers["Location"]
        copy = Analysis.query.get(int(loc.rsplit("/", 1)[1]))
        self.assertEqual(copy.user_id, self.user.id)
        self.assertEqual(copy.title, "Ram's check")
        self.assertEqual(Analysis.query.count(), 2)
        self.assertEqual(self.pipeline_runs(), 0)
        self.assertEqual(self.c.get(loc).status_code, 200)              # owner check passes for the copy

    def test_check_again_forces_a_fresh_run(self):
        self.analyze(HB)
        r = self.c.post("/", data={"url": HB, "fresh": "1"})
        self.assertTrue(r.headers["Location"].startswith("/report/"))
        self.assertEqual(self.pipeline_runs(), 2)
        self.assertEqual(Analysis.query.count(), 2)

    def test_stale_report_is_not_reused(self):
        self.analyze(HB)
        old = Analysis.query.first()
        old.created_at = datetime.now(timezone.utc) - timedelta(days=8); db.session.commit()
        self.analyze(HB)
        self.assertEqual(self.pipeline_runs(), 2)


class PerCheckKeyTests(Base):
    def test_typed_key_is_used_and_not_metered(self):
        r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
        self.assertTrue(r.headers["Location"].startswith("/report/"))
        self.assertEqual([c for c in self.calls if c[0] == "analyze"][0][1], "AIzaSyTYPEDTYPEDTYPEDTYPED9999")
        self.assertEqual(UsageEvent.query.count(), 0)
        self.assertEqual(UserApiKey.query.count(), 0)                  # never stored

    def test_typed_key_overrides_saved_key(self):
        api_keys.save_key(self.user.id, "gemini", "AIzaSySAVEDSAVEDSAVEDSAVED1111")
        self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
        self.assertEqual([c for c in self.calls if c[0] == "analyze"][0][1], "AIzaSyTYPEDTYPEDTYPEDTYPED9999")
        self.assertIsNone(UserApiKey.query.first().last_used_at)      # saved key wasn't the one used

    def test_bad_typed_key_is_rejected_before_anything_runs(self):
        def reject(k): raise api_keys.KeyInvalid("Google rejected that key.")
        with mock.patch.dict(api_keys._VALIDATORS, {"gemini": reject}):
            r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyBADBADBADBADBADBADBAD0000"})
        self.assertEqual(r.headers["Location"], "/")
        self.assertIn("Gemini key: Google rejected", self.c.get("/").get_data(as_text=True))
        self.assertEqual(self.calls, [])
        self.assertEqual(UsageEvent.query.count(), 0)

    def test_panel_shows_only_last4_of_saved_key(self):
        api_keys.save_key(self.user.id, "gemini", "AIzaSySAVEDSAVEDSAVEDSAVED1111")
        page = self.c.get("/").get_data(as_text=True)
        self.assertIn("Saved ····1111", page)
        self.assertNotIn("SAVEDSAVED", page)


class TransportHardeningTests(Base):
    def test_typed_key_over_plain_http_is_refused_in_production(self):
        with mock.patch.dict(os.environ, {"SESSION_COOKIE_SECURE": "1"}):
            r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
            self.assertEqual(r.headers["Location"], "/")
            self.assertIn("only be sent over HTTPS", self.c.get("/").get_data(as_text=True))
            self.assertEqual(self.calls, [])
            # same request with the proxy saying it was TLS → accepted
            r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"},
                            headers={"X-Forwarded-Proto": "https"})
            self.assertTrue(r.headers["Location"].startswith("/report/"))

    def test_key_verification_attempts_are_capped(self):
        import security
        security.limiter._hits.clear()
        for i in range(10):
            self.c.post("/", data={"url": f"{HB}{i}/", "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
        r = self.c.post("/", data={"url": f"{HB}99/", "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
        self.assertEqual(r.headers["Location"], "/")
        self.assertIn("Too many key attempts", self.c.get("/").get_data(as_text=True))

    def test_logged_in_pages_are_not_cached_and_hsts_only_in_production(self):
        r = self.c.get("/account")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        self.assertIsNone(r.headers.get("Strict-Transport-Security"))
        with mock.patch.dict(os.environ, {"SESSION_COOKIE_SECURE": "1"}):
            self.assertIn("max-age=31536000", self.c.get("/account").headers.get("Strict-Transport-Security", ""))

    def test_key_never_appears_in_any_response_or_log(self):
        secret = "AIzaSyTYPEDTYPEDTYPEDTYPED9999"
        with self.assertLogs(app.logger, level="DEBUG") as logs:
            app.logger.debug("probe")   # ensure the context captures something
            r = self.c.post("/", data={"url": HB, "gemini_key": secret}, follow_redirects=True)
        self.assertNotIn(secret, r.get_data(as_text=True))
        self.assertNotIn(secret, "\n".join(logs.output))
