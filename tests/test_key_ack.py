"""The free-tier-only confirmation: required by the server, explained on the pages."""
import unittest

from tests.common import Base, HB
import api_keys
from models import UserApiKey
from report_views import KEY_ACK_MESSAGE


class KeyAckTests(Base):
    def test_typed_key_without_confirmation_is_refused_before_anything_runs(self):
        r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999"})
        self.assertEqual(r.headers["Location"], "/")
        self.assertIn(KEY_ACK_MESSAGE[:40], self.flashes())
        self.assertEqual(self.calls, [])

    def test_typed_key_with_confirmation_proceeds(self):
        r = self.c.post("/", data={"url": HB, "gemini_key": "AIzaSyTYPEDTYPEDTYPEDTYPED9999", "key_ack": "1"})
        self.assertTrue(r.headers["Location"].startswith("/report/"))

    def test_no_key_typed_needs_no_confirmation(self):
        self.assertTrue(self.analyze(HB).headers["Location"].startswith("/report/"))

    def test_saving_a_key_needs_confirmation_but_removing_does_not(self):
        self.c.post("/account/keys/gemini", data={"api_key": "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", "password": "pass1234", "action": "save"})
        self.assertEqual(UserApiKey.query.count(), 0)
        self.assertIn(KEY_ACK_MESSAGE[:40], self.flashes("/account"))
        self.c.post("/account/keys/gemini", data={"api_key": "AIzaSyFAKEFAKEFAKEFAKEFAKE1234", "password": "pass1234", "action": "save", "key_ack": "1"})
        self.assertEqual(UserApiKey.query.count(), 1)
        self.c.post("/account/keys/gemini", data={"password": "pass1234", "action": "remove"})
        self.assertEqual(UserApiKey.query.count(), 0)

    def test_pages_carry_the_warning_and_the_dialog(self):
        home = self.c.get("/").get_data(as_text=True)
        self.assertIn("Free-tier keys only", home)
        self.assertIn("don't trust this site, or any site, with a key that can spend money", home)
        self.assertIn('id="keyAck"', home)
        self.assertIn('id="keyAckGo" disabled', home)
        self.assertIn('data-key-ack-field', home)
        account = self.c.get("/account").get_data(as_text=True)
        self.assertIn("Free-tier keys only, and don't trust us fully", account)
        self.assertIn("free-tier key with no billing", account)
        self.assertIn("<noscript>", account)


if __name__ == "__main__":
    unittest.main()
