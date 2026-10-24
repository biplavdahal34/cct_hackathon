"""Golden listings: real and synthetic reports with the rating band they must
land in and the scam patterns that must fire. Runs the Python layer only (no
model call), so it is deterministic and runs in CI on every change to the
weights, the patterns or the tables. `flask eval-reports --live` re-runs the
model too."""
import glob
import json
import os
import unittest

from tests.common import app
from report_views import enrich

HERE = os.path.dirname(__file__)
FILES = sorted(glob.glob(os.path.join(HERE, "fixtures", "golden", "*.json")))


class GoldenTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context(); self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_fixtures_exist(self):
        self.assertGreaterEqual(len(FILES), 5)

    def test_every_fixture_lands_in_its_band(self):
        for f in FILES:
            with self.subTest(fixture=os.path.basename(f)):
                fx = json.load(open(f, encoding="utf-8"))
                report = enrich(json.loads(json.dumps(fx["report"])), fx["listing"], None)
                got = report["verdict"]["rating"]
                self.assertIn(got, fx["expect"]["ratings"], f"{fx['name']}: {got} score {report['verdict']['score_0_100']}\n"
                              + "\n".join(f"  {i['points']:>4} {i['label']}" for i in report["scoring"]["items"]))
                keys = {s["key"] for s in report["beat"]["scams"]}
                self.assertTrue(set(fx["expect"].get("scam_keys", [])) <= keys, f"{fx['name']}: scam keys {sorted(keys)}")

    def test_enrichment_is_deterministic_and_json_safe(self):
        fx = json.load(open(FILES[0], encoding="utf-8"))
        a = enrich(json.loads(json.dumps(fx["report"])), fx["listing"], None)
        b = enrich(json.loads(json.dumps(fx["report"])), fx["listing"], None)
        a["beat"].pop("generated_at"); b["beat"].pop("generated_at")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_injection_fixture_overrides_the_models_strong_buy(self):
        fx = json.load(open(os.path.join(HERE, "fixtures", "golden", "injected-locked-s20fe.json"), encoding="utf-8"))
        report = enrich(json.loads(json.dumps(fx["report"])), fx["listing"], None)
        self.assertEqual(report["verdict"]["model_rating"], "strong_buy")
        self.assertIn(report["verdict"]["rating"], ("avoid", "caution"))
        self.assertFalse(report["scoring"]["agrees"])


if __name__ == "__main__":
    unittest.main()
