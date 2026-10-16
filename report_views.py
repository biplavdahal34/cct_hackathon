"""Pure analysis-pipeline helpers: normalization, view-models, URL routing,
and the per-URL scrape cache. No route handlers live here."""
import re
import time
import threading
from urllib.parse import urlparse

from functions import (scrape_url, detect_fb_url_type,
                       scrape_fb_marketplace, scrape_fb_post)

RATING_SCORE = {
    "strong_buy": 90, "buy_worthy": 75, "consider_with_caution": 65,
    "caution": 40, "avoid": 10,
}
RATING_COLOR = {
    "strong_buy": "green", "buy_worthy": "green", "consider_with_caution": "yellow",
    "caution": "yellow", "avoid": "red",
}


def normalize_listing(listing: dict, url: str) -> dict:
    listing = dict(listing)
    imgs = listing.get("images") or listing.get("product_img") or []
    if not isinstance(imgs, list):
        imgs = list(imgs)
    listing["images"] = imgs
    listing["url"] = url
    return listing


def listing_source(url: str) -> str:
    """'hamrobazaar' or 'facebook' — host validated by parsing, never by
    substring matching (prevents evil.com/?hamrobazaar.com misrouting).
    Raises ValueError for anything else."""
    host = (urlparse(url).netloc or "").lower()
    if host == "hamrobazaar.com" or host.endswith(".hamrobazaar.com"):
        return "hamrobazaar"
    if (host == "facebook.com" or host.endswith(".facebook.com")
            or host in ("fb.watch", "fb.me")):
        return "facebook"
    raise ValueError("Unsupported URL. Currently supported: Hamrobazaar listings, "
                     "Facebook Marketplace items, Facebook posts.")


_FB_ITEM = re.compile(r"/marketplace/item/(\d+)")


def canonical_url(url: str) -> str:
    """Stable key for 'the same listing' so a repeat check can reuse a stored
    report: lower-cased host without www./m. prefixes, no query string or
    fragment, no trailing slash; Facebook Marketplace items reduce to their id."""
    p = urlparse((url or "").strip())
    host = (p.netloc or "").lower()
    for prefix in ("www.", "m.", "mbasic.", "web."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = (p.path or "/").rstrip("/")
    if host == "facebook.com":
        m = _FB_ITEM.search(path)
        if m:
            return f"https://facebook.com/marketplace/item/{m.group(1)}"
    return f"https://{host}{path}"


def scrape_any(url: str, apify_token: str = None) -> dict:
    """Route a URL to the right scraper. `apify_token` is the user's own
    token for Facebook (BYOK) or None for the server's."""
    source = listing_source(url)

    if source == "hamrobazaar":
        return scrape_url(url)

    if source == "facebook":
        kind = detect_fb_url_type(url)
        if kind == "marketplace":
            return scrape_fb_marketplace(url, apify_token=apify_token)
        if kind in ("post", "reel"):
            return scrape_fb_post(url, apify_token=apify_token)
        if kind == "marketplace_search":
            raise ValueError("That's a Facebook search page — paste a single "
                             "listing or post URL.")
        raise ValueError("Couldn't recognize this Facebook URL type. Paste a "
                         "Marketplace item link or a post link.")

    raise ValueError("Unsupported URL. Currently supported: Hamrobazaar listings, "
                     "Facebook Marketplace items, Facebook posts.")


# ── per-URL scrape cache (saves Apify credits on repeat analyses) ────────
_CACHE: dict = {}
CACHE_TTL = 1800          # seconds; set 0 to disable caching entirely
_CACHE_LOCK = threading.Lock()


def cache_get(url: str):
    if CACHE_TTL <= 0:
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(url)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        _CACHE.pop(url, None)
    return None


def cache_put(url: str, listing: dict):
    if CACHE_TTL <= 0:
        return
    with _CACHE_LOCK:
        if len(_CACHE) > 200:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[url] = (time.time(), listing)


def _to_num(v):
    """Best-effort number from model-generated JSON (51000, 51000.0, '51,000', '85%')."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            f = float(v.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
        return int(f) if f.is_integer() else f
    return None


def _fmt(v, default="—"):
    """Thousands-separated number, or `default` if v is not numeric."""
    n = _to_num(v)
    if n is None:
        return default
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


def price_vm(ps: dict, pa: dict) -> dict:
    p = ps.get("asking_price") or {}
    rng = pa.get("estimated_fair_range_npr")
    lo = hi = None
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        lo, hi = _to_num(rng[0]), _to_num(rng[1])
    verdict = pa.get("verdict")
    verdict = verdict if isinstance(verdict, str) else None
    tones = {"good_deal": "ok", "fair": None, "overpriced": "warn", "suspiciously_cheap": "bad"}
    return {
        "main": f"{p.get('currency', '')} {_fmt(p.get('amount'))}".strip() if p else None,
        "tone": tones.get(verdict),
        "verdict_text": (verdict or "unavailable").replace("_", " "),
        "range": f"{_fmt(lo)}–{_fmt(hi)}" if lo and hi else "—",
        "confidence": pa.get("confidence", "low"),
    }


def normalize_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValueError("Model returned no usable report — try again")
    v = report.get("verdict") or {}
    rating = v.get("rating")
    if rating not in RATING_SCORE:
        rating = "caution"
        v["rating"] = rating
    score = v.get("score_0_100")
    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        v["score_0_100"] = RATING_SCORE[rating]
    v["badge_color"] = {"green": "green", "yellow": "yellow", "red": "red"}.get(
        v.get("badge_color"), RATING_COLOR[rating])
    report["verdict"] = v
    return report


def facts_rows(ps: dict, claims: dict, category: str = "phone") -> list:
    bh = claims.get("battery_health_percent")
    price = ps.get("asking_price") or {}
    match = ps.get("matches_stated_condition")
    storage_raw = str(ps.get("storage") or "").strip()
    storage_disp = f"{storage_raw} GB" if storage_raw.isdigit() else (storage_raw or None)

    rows = [
        ("Model", ps.get("model"), None),
        ("Storage", storage_disp, None),
        ("RAM", ps.get("ram"), None),
        ("Listed condition", ps.get("stated_condition"), None),
        ("Assessed condition", ps.get("assessed_condition"), None),
        ("Matches listing tag", match,
         "bad" if match == "no" else ("ok" if match == "yes" else None)),
        ("Price", (f"{price.get('currency', '')} {_fmt(price.get('amount'))} · "
                   f"{'negotiable' if price.get('negotiable') else 'fixed'}") if price else None, None),
    ]

    # battery row only for devices that actually have one, and only when claimed
    if category in ("phone", "laptop") and bh is not None:
        rows.append(("Battery health",
                     f"{bh}% — below 80% threshold" if bh < 80 else f"{bh}%",
                     "warn" if bh < 80 else "ok"))

    rows.append(("Box included", "Yes" if claims.get("box_included") else "No", None))

    # category-specific claims → dynamic rows (biometrics, locks, GPU, hinges, OS…)
    for k, val in (claims.get("category_specific_claims") or {}).items():
        if isinstance(val, str) and val.strip():
            rows.append((k.replace("_", " ").title(), val.replace("_", " "), None))

    rows += [
        ("Reason for selling", claims.get("reason_for_selling"), None),
        ("Location", ps.get("location"), None),
        ("Delivery", "Available" if ps.get("delivery_available") else "Not available", None),
    ]
    return [{"label": l, "value": v if v not in (None, "") else "unknown", "tone": t}
            for l, v, t in rows]