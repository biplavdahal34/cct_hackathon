"""Listing history and seller fingerprints.

Every time BEAT fetches a listing it stores a snapshot: price, condition tag, a
hash of the description, perceptual hashes of the photos, and who the seller
is. From that, later checks can say "the price was cut twice this week", "the
condition tag changed from Like new to Used", "these photos were posted by a
different seller in August", or "this seller has posted eleven phones this
month". None of that needs a model; it needs memory."""
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone

from PIL import Image, UnidentifiedImageError

from extensions import db
from models import ListingSnapshot, ImageHash

_GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))   # Nepal has no daylight saving


def _local(d: datetime) -> datetime:
    """Stored times are UTC; the people reading them are in Nepal."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(NEPAL_TZ)


def day_label(d) -> str:
    """'3 Oct 2026', or '' for nothing."""
    if not d:
        return ""
    d = _local(d)
    return f"{d.day} {d:%b %Y}"


def when_label(d) -> str:
    """'3 Oct 2026, 19:14' for timelines where two checks can share a day."""
    if not d:
        return ""
    d = _local(d)
    return f"{d.day} {d:%b %Y}, {d:%H:%M}"


NEAR_MATCH_BITS = 6          # Hamming distance that still counts as the same photo
NEAR_MATCH_SCAN = 5000       # how many recent hashes to compare for near matches
DEALER_ADS = 15              # active listings that make a seller a dealer


# ── perceptual hash ───────────────────────────────────────────────────────
def dhash_bytes(data: bytes, size: int = 8):
    """Difference hash: resize to 9×8 greyscale, compare each pixel with its
    right neighbour → 64 bits → 16 hex chars. Survives re-encoding, resizing
    and light compression; a crop or a different photo gives a distant hash."""
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ── keys ──────────────────────────────────────────────────────────────────
def seller_key(listing: dict):
    """Stable id for 'the same seller', strongest signal first: the site's user
    id, a full phone number, else nothing (a name alone is not an identity)."""
    source = (listing.get("source") or "unknown").split("_")[0]
    seller = listing.get("seller") or {}
    sid = seller.get("id")
    if not sid and seller.get("profile_url"):
        m = _GUID.search(seller["profile_url"])
        sid = m.group(0) if m else None
    if sid:
        return f"{source}:{str(sid).upper()}"[:120]
    phone = str(seller.get("phone") or "")
    digits = re.sub(r"\D", "", phone)
    if "*" not in phone and len(digits) >= 8:
        return f"{source}:tel:{digits}"[:120]
    if source == "facebook" and seller.get("profile_url"):
        return f"facebook:{seller['profile_url'].rstrip('/').rsplit('/', 1)[-1].lower()}"[:120]
    return None


def description_hash(text) -> str:
    norm = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


# ── writing ───────────────────────────────────────────────────────────────
def record(listing: dict, url_key: str, images: list = None, analysis_id: int = None) -> ListingSnapshot:
    """Store one snapshot plus a hash per downloaded photo. `images` is the
    [(url, bytes, mime)] list the analysis already fetched."""
    price = listing.get("price") or {}
    seller = listing.get("seller") or {}
    snap = ListingSnapshot(
        url_key=url_key,
        source=listing.get("source"),
        external_id=(listing.get("external_id") or listing.get("fb_item_id") or None),
        title=(listing.get("title") or "")[:300],
        price_amount=price.get("amount") if isinstance(price.get("amount"), int) else None,
        price_currency=price.get("currency"),
        condition=(listing.get("condition") or None),
        description_hash=description_hash(listing.get("description")),
        description_len=len(str(listing.get("description") or "")),
        image_count=len(listing.get("images") or listing.get("product_img") or []),
        seller_key=seller_key(listing),
        seller_name=(seller.get("name") or "")[:100] or None,
        seller_ad_count=seller.get("ad_count") if isinstance(seller.get("ad_count"), int) else None,
        seller_badge=seller.get("badge"),
        posted_at=_dt(listing.get("posted_at")),
        views=listing.get("views") if isinstance(listing.get("views"), int) else None,
        analysis_id=analysis_id,
    )
    db.session.add(snap)
    db.session.flush()
    for url, data, _mime in (images or []):
        h = dhash_bytes(data)
        if h:
            db.session.add(ImageHash(snapshot_id=snap.id, url_key=url_key, hash=h, image_url=url[:2048]))
    db.session.commit()
    return snap


# ── reading ───────────────────────────────────────────────────────────────
def history(url_key: str) -> list:
    return (ListingSnapshot.query.filter_by(url_key=url_key)
            .order_by(ListingSnapshot.captured_at.asc()).all())


def previous(url_key: str, before: ListingSnapshot):
    q = ListingSnapshot.query.filter(ListingSnapshot.url_key == url_key)
    if before is not None:
        q = q.filter(ListingSnapshot.id < before.id)
    return q.order_by(ListingSnapshot.id.desc()).first()


def _hashes_for(snapshot_id: int) -> list:
    return [r.hash for r in ImageHash.query.filter_by(snapshot_id=snapshot_id).all()]


def diff(prev: ListingSnapshot, cur: ListingSnapshot) -> list:
    """What changed between two snapshots of the same listing: [{key, label, severity}]."""
    out = []
    if prev is None or cur is None:
        return out
    when = f"{_local(prev.captured_at).day} {_local(prev.captured_at):%b}" if prev.captured_at else "earlier"
    if prev.price_amount and cur.price_amount and prev.price_amount != cur.price_amount:
        cur_c = cur.price_currency or "NPR"
        if cur.price_amount < prev.price_amount:
            pct = round((prev.price_amount - cur.price_amount) / prev.price_amount * 100)
            out.append({"key": "price_cut", "severity": "low",
                        "label": f"Price cut from {cur_c} {prev.price_amount:,} to {cur_c} {cur.price_amount:,} since {when} ({pct}% lower). The seller is motivated."})
        else:
            out.append({"key": "price_raised", "severity": "low",
                        "label": f"Price raised from {cur_c} {prev.price_amount:,} to {cur_c} {cur.price_amount:,} since {when}."})
    pc, cc = (prev.condition or "").strip().lower(), (cur.condition or "").strip().lower()
    if pc and cc and pc != cc:
        rank = {"used": 0, "like new": 1, "brand new": 2}
        if rank.get(cc, 0) > rank.get(pc, 0):
            out.append({"key": "condition_upgraded", "severity": "medium",
                        "label": f"Condition tag changed from “{prev.condition}” to “{cur.condition}” since {when}. A device does not get newer."})
        else:
            out.append({"key": "condition_downgraded", "severity": "low",
                        "label": f"Condition tag changed from “{prev.condition}” to “{cur.condition}” since {when}."})
    if prev.description_hash and cur.description_hash and prev.description_hash != cur.description_hash:
        out.append({"key": "description_changed", "severity": "low",
                    "label": f"The description was edited since {when}. Ask what changed."})
    ph, ch = set(_hashes_for(prev.id)), set(_hashes_for(cur.id))
    if ph and ch and not (ph & ch):
        out.append({"key": "photos_replaced", "severity": "medium",
                    "label": f"All photos were replaced since {when}. The earlier photos may have shown something the seller removed."})
    return out


def photo_matches(cur: ListingSnapshot) -> list:
    """Other listings whose photos match this snapshot's: exact hash matches by
    index, near matches by Hamming distance over recent rows. One entry per
    other listing: {url_key, seller_key, seller_name, captured_at, distance, same_seller}."""
    mine = _hashes_for(cur.id)
    if not mine:
        return []
    found = {}
    rows = (db.session.query(ImageHash, ListingSnapshot)
            .join(ListingSnapshot, ImageHash.snapshot_id == ListingSnapshot.id)
            .filter(ImageHash.url_key != cur.url_key)
            .order_by(ImageHash.id.desc()).limit(NEAR_MATCH_SCAN).all())
    for ih, snap in rows:
        best = min(hamming(ih.hash, h) for h in mine)
        if best > NEAR_MATCH_BITS:
            continue
        key = snap.url_key
        if key not in found or best < found[key]["distance"]:
            found[key] = {"url_key": key, "seller_key": snap.seller_key, "seller_name": snap.seller_name,
                          "captured_at": snap.captured_at, "distance": best,
                          "same_seller": bool(cur.seller_key and snap.seller_key == cur.seller_key),
                          "title": snap.title}
    return sorted(found.values(), key=lambda m: m["distance"])


def seller_profile(key: str, days: int = 90) -> dict:
    """What BEAT has seen of this seller: {ad_count, badge, listings_seen, first_seen, names}."""
    if not key:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (ListingSnapshot.query.filter(ListingSnapshot.seller_key == key)
            .order_by(ListingSnapshot.captured_at.desc()).all())
    if not rows:
        return {}
    latest = rows[0]
    recent = [r for r in rows if r.captured_at and r.captured_at.replace(tzinfo=timezone.utc) >= since]
    return {
        "ad_count": latest.seller_ad_count,
        "badge": latest.seller_badge,
        "listings_seen": len({r.url_key for r in recent}),
        "first_seen": rows[-1].captured_at,
        "names": sorted({r.seller_name for r in rows if r.seller_name}),
    }


def signals(cur: ListingSnapshot, changes: list, matches: list, profile: dict) -> list:
    """Scoring inputs: [{key, severity, label}] from history, photo reuse and seller volume."""
    out = list(changes)
    other = [m for m in matches if not m["same_seller"]]
    same = [m for m in matches if m["same_seller"]]
    if other:
        m = other[0]
        when = day_label(m["captured_at"]) or "earlier"
        out.append({"key": "photos_other_seller", "severity": "high",
                    "label": f"The same photos appeared in a listing by a different seller ({when}). "
                             "One of the two is not selling their own device."})
    if same:
        m = same[0]
        when = day_label(m["captured_at"]) or "earlier"
        out.append({"key": "reposted", "severity": "low",
                    "label": f"The seller used these photos in an earlier listing ({when}). "
                             "Reposting is normal; ask why the first one did not sell."})
    ads = profile.get("ad_count")
    if ads and ads >= DEALER_ADS:
        out.append({"key": "dealer", "severity": "low",
                    "label": f"The seller has {ads} active listings, so this is a shop, not a personal sale."})
    seen = profile.get("listings_seen") or 0
    if seen >= 5:
        out.append({"key": "many_listings", "severity": "low",
                    "label": f"BEAT has seen {seen} different listings from this seller in the last 90 days."})
    names = profile.get("names") or []
    if len(names) >= 3:
        out.append({"key": "many_names", "severity": "medium",
                    "label": f"This seller account has used {len(names)} different display names."})
    return out


def inspect(listing: dict, url_key: str, images: list = None, analysis_id: int = None) -> dict:
    """Record a snapshot and return everything the report needs:
    {snapshot, previous, changes, matches, profile, signals, timeline}."""
    snap = record(listing, url_key, images=images, analysis_id=analysis_id)
    prev = previous(url_key, snap)
    changes = diff(prev, snap)
    matches = photo_matches(snap)
    profile = seller_profile(snap.seller_key)
    return {
        "snapshot": snap, "previous": prev, "changes": changes, "matches": matches,
        "profile": profile, "signals": signals(snap, changes, matches, profile),
        "timeline": [{"captured_at": s.captured_at, "price": s.price_amount, "currency": s.price_currency,
                      "condition": s.condition} for s in history(url_key)],
    }


def view(snap_info: dict) -> dict:
    """JSON-safe copy for storing on the report (dates as strings, no ORM rows)."""
    if not snap_info:
        return {}
    prof = dict(snap_info.get("profile") or {})
    if prof.get("first_seen"):
        prof["first_seen"] = day_label(prof["first_seen"])
    return {
        "changes": snap_info.get("changes") or [],
        "matches": [{"title": m.get("title"), "same_seller": m["same_seller"], "distance": m["distance"],
                     "captured_at": day_label(m.get("captured_at")) or None} for m in (snap_info.get("matches") or [])],
        "profile": prof,
        "signals": snap_info.get("signals") or [],
        "timeline": [{"captured_at": when_label(t["captured_at"]) or None, "price": t["price"], "currency": t["currency"],
                      "condition": t["condition"]} for t in (snap_info.get("timeline") or [])],
    }
