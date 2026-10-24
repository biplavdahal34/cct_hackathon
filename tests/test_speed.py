"""Speed work that must not change the analysis: photos fetched together, scaled before upload,
the seller's text filled in by Python, and one timing line per run."""
import io
import json
import threading
import unittest
from unittest import mock

from PIL import Image

from tests.common import Base, HB, RICH_LISTING, RICH_REPORT, deep
import functions
import report_views
import routes_main


def noise_image(size, fmt="JPEG", mode="RGB", **save):
    """Full-size random noise: a heavy photo that no codec can squeeze, like a real phone photo."""
    img = Image.effect_noise(size, 60).convert(mode)
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


class Raw:
    def __init__(self, data): self.data = data
    def read(self, n, decode_content=False): return self.data[:n]


class FakeResponse:
    def __init__(self, data, ctype="image/jpeg"):
        self.headers = {"content-type": ctype}
        self.raw = Raw(data)
    def raise_for_status(self): pass


class DownloadTests(unittest.TestCase):
    def test_photos_are_fetched_at_the_same_time_and_keep_their_order(self):
        barrier = threading.Barrier(3, timeout=5)

        def fake_get(url, **kw):
            barrier.wait()                       # returns only when all three requests are in flight together
            return FakeResponse(url.encode())
        urls = ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"]
        with mock.patch.object(functions.requests, "get", fake_get):
            out = functions.download_images(urls)
        self.assertEqual([u for u, _, _ in out], urls)
        self.assertEqual(out[1][1], b"https://x/2.jpg")

    def test_bad_photos_are_skipped_without_sinking_the_rest(self):
        def fake_get(url, **kw):
            if "bad" in url:
                raise OSError("boom")
            if "page" in url:
                return FakeResponse(b"<html>", "text/html")
            if "huge" in url:
                return FakeResponse(b"x" * (functions.MAX_IMAGE_BYTES + 1))
            return FakeResponse(b"img")
        urls = ["https://x/1.jpg", "https://x/bad.jpg", "http://insecure/2.jpg", "https://x/page.html", None,
                "https://x/huge.jpg", "https://x/5.jpg"]
        with mock.patch.object(functions.requests, "get", fake_get):
            self.assertEqual([u for u, _, _ in functions.download_images(urls, max_n=7)], ["https://x/1.jpg", "https://x/5.jpg"])
            self.assertEqual([u for u, _, _ in functions.download_images(urls, max_n=2)], ["https://x/1.jpg"])
            # the cap counts the first N entries before anything is filtered out, as it always has
            self.assertEqual([u for u, _, _ in functions.download_images(urls)], ["https://x/1.jpg"])
            self.assertEqual(functions.download_images([]), [])
            self.assertEqual(functions.download_images(None), [])


class ShrinkTests(unittest.TestCase):
    def test_a_big_photo_is_scaled_down_and_gets_lighter(self):
        data = noise_image((3000, 2000))
        out, mime = functions.shrink_image(data, "image/jpeg")
        im = Image.open(io.BytesIO(out))
        self.assertEqual(max(im.size), 1600)
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(out), len(data))

    def test_a_small_photo_is_left_alone(self):
        data = noise_image((400, 300))
        out, mime = functions.shrink_image(data, "image/jpeg")
        self.assertIs(out, data)
        self.assertEqual(mime, "image/jpeg")

    def test_what_cannot_be_decoded_is_returned_untouched(self):
        self.assertEqual(functions.shrink_image(b"not an image", "image/png"), (b"not an image", "image/png"))

    def test_zero_turns_it_off(self):
        data = noise_image((3000, 2000))
        self.assertIs(functions.shrink_image(data, "image/jpeg", max_side=0)[0], data)

    def test_a_png_with_transparency_becomes_a_valid_jpeg(self):
        data = noise_image((1800, 1200), "PNG", mode="RGBA")
        out, mime = functions.shrink_image(data, "image/png")
        im = Image.open(io.BytesIO(out))
        self.assertLessEqual(max(im.size), 1600)
        if mime == "image/jpeg":
            self.assertEqual(im.mode, "RGB")

    def test_the_phones_rotation_is_applied_before_the_exif_is_dropped(self):
        exif = Image.Exif()
        exif[0x0112] = 6                                    # "rotate 90 degrees to display"
        data = noise_image((3000, 2000), exif=exif)
        out, _ = functions.shrink_image(data, "image/jpeg")
        w, h = Image.open(io.BytesIO(out)).size
        self.assertLess(w, h)                               # the photo is upright, not sideways

    def test_the_model_receives_the_scaled_photo_not_the_original(self):
        big = noise_image((3000, 2000))
        seen = []

        class Resp:
            text = json.dumps({"verdict": {"rating": "buy_worthy", "score_0_100": 80}})

        class Models:
            def generate_content(self, model, contents, config):
                seen.extend(contents[1:])
                return Resp()

        class Client:
            models = Models()
        with mock.patch.object(functions, "gemini_client", lambda key=None: Client()):
            functions.analyze({"title": "x", "description": "d"}, images=[("https://x/1.jpg", big, "image/jpeg")])
        sent = seen[0].inline_data.data
        self.assertLess(len(sent), len(big))
        self.assertEqual(max(Image.open(io.BytesIO(sent)).size), 1600)


class DescriptionTests(unittest.TestCase):
    def test_the_listing_text_fills_the_empty_echo(self):
        report = {"description_analysis": {"original_description": "", "translated_description": "t"}}
        report_views.fill_original_description(report, {"description": "Selling my phone, call 98xx"})
        self.assertEqual(report["description_analysis"]["original_description"], "Selling my phone, call 98xx")
        self.assertEqual(report["description_analysis"]["translated_description"], "t")

    def test_without_listing_text_the_model_value_stays(self):
        report = {"description_analysis": {"original_description": "from the model"}}
        report_views.fill_original_description(report, {"description": None})
        self.assertEqual(report["description_analysis"]["original_description"], "from the model")

    def test_odd_shapes_do_not_raise(self):
        for bad in ({}, {"description_analysis": None}, {"description_analysis": "text"}):
            report_views.fill_original_description(bad, {"description": "x"})

    def test_the_prompt_tells_the_model_not_to_echo_it(self):
        self.assertIn("original_description: always output an empty string", functions.gemini_prompt)
        self.assertIn('"original_description": "",', functions.gemini_prompt)


class PipelineTests(Base):
    rich = True

    def test_each_analysis_logs_where_the_time_went(self):
        with self.assertLogs("beat.timing", "INFO") as cm:
            self.analyze(HB)
        line = cm.output[0]
        for part in ("scrape=", "photos=", "vision=", "price=", "python=", "save=", "total="):
            self.assertIn(part, line)

    def test_the_sellers_text_comes_from_the_listing_even_when_the_model_leaves_it_empty(self):
        empty = deep(RICH_REPORT)
        empty["description_analysis"]["original_description"] = ""
        with mock.patch.object(routes_main, "analyze", lambda listing, gemini_key=None, max_images=None, images=None: deep(empty)):
            aid = self.analysis_id(self.analyze(HB))
        page = self.c.get(f"/report/{aid}").get_data(as_text=True)
        self.assertIn(RICH_LISTING["description"], page)


if __name__ == "__main__":
    unittest.main()
