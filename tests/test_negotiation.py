"""The opening offer and its reasons."""
import unittest

import negotiation

REP = {"product_summary": {"asking_price": {"amount": 60000, "currency": "NPR"}},
       "price_analysis": {"estimated_fair_range_npr": [48000, 56000]}, "market_data": {"market_stats": {"median": 52000}}}
REPAIRS = {"items": [{"label": "Battery replacement", "low": 4500, "high": 8000, "why": "Seller states battery health 76%."}],
           "total_low": 4500, "total_high": 8000}


class NegotiationTests(unittest.TestCase):
    def test_offer_below_market_minus_repairs(self):
        out = negotiation.build(REP, REPAIRS, {"changes": [{"key": "price_cut"}]})
        self.assertEqual(out["offer"], 44000)
        self.assertLess(out["offer"], out["asking"])
        self.assertIn("NPR 44,000", out["message"])
        self.assertIn("NPR 44,000", out["message_ne"])
        self.assertTrue(any("cut the price" in b for b in out["basis"]))
        self.assertTrue(any("Battery replacement" in b for b in out["basis"]))
        self.assertFalse(out["already_fair"])

    def test_good_deal_gets_a_small_haggle_not_a_lowball(self):
        rep = dict(REP, product_summary={"asking_price": {"amount": 45000, "currency": "NPR"}})
        out = negotiation.build(rep, {"items": [], "total_low": 0, "total_high": 0}, {})
        self.assertTrue(out["already_fair"])
        self.assertEqual(out["offer"], 43000)
        self.assertIn("looks fair", out["message"])

    def test_no_price_or_foreign_currency_gives_nothing(self):
        self.assertEqual(negotiation.build({"product_summary": {}}, {}, {}), {})
        self.assertEqual(negotiation.build({"product_summary": {"asking_price": {"amount": 200, "currency": "USD"}}}, {}, {}), {})

    def test_without_comparables_starts_ten_percent_under(self):
        out = negotiation.build({"product_summary": {"asking_price": {"amount": 20000, "currency": "NPR"}}, "price_analysis": {}, "market_data": {}}, {}, {})
        self.assertEqual(out["offer"], 18000)
        self.assertTrue(any("10% under" in b for b in out["basis"]))


if __name__ == "__main__":
    unittest.main()
