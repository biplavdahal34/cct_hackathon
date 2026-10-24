"""Price index: what used devices actually sell for in Nepal, over time.

Two feeds write price observations:
  • the crawler (`flask crawl-prices`, meant to run once a day from cron or the
    compose `crawler` service) searches the site for a list of popular models;
  • every analysis contributes its comparables and the listing itself.
One row per listing per day, keyed by the site's own listing id, so a listing
that stays up for a month counts once in a 30-day median.

Reads: stats(model_key) for the report's "% off new" and trend line. The public price pages
(/prices) are built by catalogue.py from the same observations."""
import os
import re
import time
from datetime import date, datetime, timedelta

from sqlalchemy import func

from extensions import db
from knowledge import products
from models import PriceObservation

# models worth tracking in Nepal's used market; the analyses add more over time
SEED_MODELS = [
    "iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max", "iPhone 12", "iPhone 12 Pro", "iPhone 12 Pro Max",
    "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max", "iPhone 14", "iPhone 14 Pro", "iPhone 14 Pro Max",
    "iPhone 15", "iPhone 15 Pro", "iPhone 15 Pro Max", "iPhone 16", "iPhone 16 Pro", "iPhone 16 Pro Max",
    "Samsung S21", "Samsung S22", "Samsung S22 Ultra", "Samsung S23", "Samsung S23 Ultra", "Samsung S24", "Samsung S24 Ultra",
    "Samsung A54", "Samsung A55", "Samsung A34", "Samsung A35", "Samsung S20 FE", "Samsung S21 FE",
    "Redmi Note 12", "Redmi Note 13", "Poco X5", "Poco X6", "Poco F5", "OnePlus 11", "OnePlus 12", "OnePlus Nord",
    "Google Pixel 7", "Google Pixel 8", "Vivo V30", "Oppo Reno",
    "MacBook Air M1", "MacBook Air M2", "MacBook Pro M1", "MacBook Pro M2", "MacBook Pro M3",
    "Acer Nitro 5", "Asus TUF", "Lenovo Legion 5", "Dell XPS 13", "HP Pavilion", "Lenovo IdeaPad",
]
# the lines the laptop price pages are built around (knowledge/products.py), so the daily crawl gathers their listings
SEED_MODELS += [q for _line in products.LINES.values() for q in _line["queries"] if q not in SEED_MODELS]

_DROP = re.compile(r"\(.*?\)|\b\d+\s*/\s*\d+\s*(?:gb|tb)?\b|\b\d+\s*(?:gb|tb|mah|hz|w|inch|\")\b"
                   r"|\b(ram|rom|storage|dual\s*sim|5g|4g|lte|new|used|like|brand|sealed|boxed|box|bill|warranty|"
                   r"black|white|blue|green|red|purple|gold|silver|graphite|midnight|starlight|titanium|grey|gray|pink|yellow|"
                   r"sierra|alpine|deep|natural|desert|phantom|awesome|for|sale|sell|urgent|cheap|price|only|exchange|"
                   r"condition|fresh|clean|genuine|original|official|variant|set|ko|ma|ho)\b", re.I)
_BRAND_FIX = [
    (re.compile(r"\bi\s*phone\b", re.I), "iphone"),
    (re.compile(r"\bsamsung\s+galaxy\b", re.I), "samsung"),
    (re.compile(r"\bgalaxy\b", re.I), "samsung"),
    (re.compile(r"\bmac\s*book\b", re.I), "macbook"),
    (re.compile(r"\bpro\s*max\b", re.I), "promax"),
]
_LABEL_FIX = {"iphone": "iPhone", "promax": "Pro Max", "macbook": "MacBook", "samsung": "Samsung", "se": "SE",
              "fe": "FE", "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4", "xr": "XR", "xs": "XS", "tuf": "TUF", "xps": "XPS",
              "oneplus": "OnePlus", "ipad": "iPad", "hp": "HP", "rog": "ROG", "x": "X"}


def model_key(name: str) -> str:
    """'I phone 13 Pro Max 256GB Sierra Blue' → 'iphone-13-promax'. Groups listings
    of the same model regardless of storage, colour and seller spelling."""
    t = str(name or "")
    for rx, rep in _BRAND_FIX:
        t = rx.sub(rep, t)
    t = _DROP.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t.lower())
    words, seen = [], set()
    for w in t.split():
        if w and w not in seen:              # "s22 ultra | s22 ultra" → once
            seen.add(w)
            words.append(w)
    return "-".join(words[:5])[:80]


def label_for(key: str) -> str:
    out = []
    for w in (key or "").split("-"):
        out.append(_LABEL_FIX.get(w, w.upper() if (len(w) <= 2 and not w.isdigit()) else w.capitalize()))
    return " ".join(out)


def query_for(key: str) -> str:
    """Search text for a model key: 'iphone-13-promax' → 'iphone 13 pro max'."""
    return label_for(key).replace("Pro Max", "pro max").lower()


# ── writing ───────────────────────────────────────────────────────────────
def _dt(v):
    if isinstance(v, datetime):
        return v
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def observe(rows: list, key: str, query: str = None, observed_on: date = None) -> int:
    """Insert one observation per row for today; rows already seen today are
    skipped. Returns the number inserted."""
    observed_on = observed_on or date.today()
    n = 0
    for r in rows:
        ext = r.get("external_id")
        price = r.get("price")
        if not ext or not isinstance(price, int):
            continue
        exists = db.session.query(PriceObservation.id).filter_by(external_id=ext, observed_on=observed_on).first()
        if exists:
            continue
        db.session.add(PriceObservation(
            model_key=key, search_query=(query or "")[:120], external_id=ext, title=(r.get("title") or "")[:300],
            price=price, condition=r.get("condition") or "unknown", category=(r.get("category") or "")[:80],
            seller_key=r.get("seller_key"), posted_at=_dt(r.get("posted_at")),
            observed_on=observed_on, source_url=(r.get("url") or "")[:2048]))
        n += 1
    db.session.commit()
    return n


def contribute(listing: dict, report: dict) -> int:
    """Called after every analysis: the comparables and the analysed listing
    itself become observations under the report's model."""
    ps = report.get("product_summary") or {}
    key = model_key(ps.get("model") or ps.get("title") or listing.get("title"))
    if not key:
        return 0
    rows = list(((report.get("market_data") or {}).get("listings")) or [])
    price = listing.get("price") or {}
    if listing.get("external_id") and isinstance(price.get("amount"), int) and (price.get("currency") or "NPR") == "NPR":
        cond = {"brand new": "brand_new", "like new": "like_new", "used": "used"}.get(str(listing.get("condition") or "").lower(), "unknown")
        rows.append({"external_id": listing["external_id"], "title": listing.get("title"), "price": price["amount"],
                     "condition": cond, "category": "", "seller_key": None, "posted_at": listing.get("posted_at"),
                     "url": listing.get("url")})
    # only rows that plausibly are this model (the market pass excluded the rest by title)
    excluded = {e.get("title") for e in ((report.get("market_data") or {}).get("excluded") or [])}
    rows = [r for r in rows if r.get("title") not in excluded]
    return observe(rows, key, query=ps.get("model"))


def crawl(queries: list = None, pages: int = 2, page_size: int = 20, pause: float = 1.0, log=print) -> dict:
    """Search the site for each model and store today's observations."""
    from functions import search_rows
    queries = queries or SEED_MODELS
    total, failed = 0, []
    for q in queries:
        key = model_key(q)
        try:
            rows = search_rows(q, pages=pages, page_size=page_size)
        except Exception as e:                      # one bad query must not stop the run
            failed.append(q)
            log(f"  {q}: failed ({e.__class__.__name__})")
            continue
        n = observe(rows, key, query=q)
        total += n
        log(f"  {q}: {len(rows)} rows, {n} new")
        time.sleep(pause)
    return {"inserted": total, "failed": failed, "queries": len(queries)}


# ── reading ───────────────────────────────────────────────────────────────
def _latest_per_listing(key: str, since: date, until: date = None):
    """Latest observation per external_id in [since, until]: a listing counts once."""
    q = (db.session.query(PriceObservation.external_id, func.max(PriceObservation.id).label("mid"))
         .filter(PriceObservation.model_key == key, PriceObservation.observed_on >= since))
    if until:
        q = q.filter(PriceObservation.observed_on <= until)
    ids = [mid for _, mid in q.group_by(PriceObservation.external_id).all()]
    if not ids:
        return []
    return PriceObservation.query.filter(PriceObservation.id.in_(ids)).all()


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) // 2


def _pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = max(0, min(len(xs) - 1, round((len(xs) - 1) * p)))
    return xs[i]


def stats(key: str, days: int = 90) -> dict:
    """{n, median, p25, p75, min, max, by_condition:{cond:{n, median}}, new_median,
    trend_pct (last 30 days vs the 30 before), first_seen, last_seen}. n counts listings, not rows."""
    today = date.today()
    rows = _latest_per_listing(key, today - timedelta(days=days))
    used = [r.price for r in rows if r.condition in ("used", "like_new", "unknown")]
    new = [r.price for r in rows if r.condition == "brand_new"]
    by_cond = {}
    for cond in ("brand_new", "like_new", "used"):
        xs = [r.price for r in rows if r.condition == cond]
        if xs:
            by_cond[cond] = {"n": len(xs), "median": _median(xs)}
    recent = [r.price for r in _latest_per_listing(key, today - timedelta(days=30)) if r.condition != "brand_new"]
    older = [r.price for r in _latest_per_listing(key, today - timedelta(days=60), today - timedelta(days=31)) if r.condition != "brand_new"]
    trend = None
    if len(recent) >= 3 and len(older) >= 3:
        trend = round((_median(recent) - _median(older)) / _median(older) * 100)
    dates = sorted(r.observed_on for r in rows)
    return {
        "key": key, "label": label_for(key), "n": len(used), "n_new": len(new),
        "median": _median(used), "p25": _pct(used, 0.25), "p75": _pct(used, 0.75),
        "min": min(used) if used else None, "max": max(used) if used else None,
        "new_median": _median(new), "by_condition": by_cond, "trend_pct": trend,
        "first_seen": dates[0] if dates else None, "last_seen": dates[-1] if dates else None,
        "days": days,
    }


def prune(days: int = None) -> int:
    """Drop observations older than PRICE_HISTORY_DAYS (default 400)."""
    days = days or int(os.environ.get("PRICE_HISTORY_DAYS", "400"))
    cutoff = date.today() - timedelta(days=days)
    n = PriceObservation.query.filter(PriceObservation.observed_on < cutoff).delete()
    db.session.commit()
    return n
