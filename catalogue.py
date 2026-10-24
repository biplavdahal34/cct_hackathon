"""The price pages: the models BEAT has checked, and a price for each worked out from the verdicts.

Every listing a buyer has run through BEAT is one data point: what the seller asked, and what BEAT
concluded about it (a score and a verdict). The price for a model is the average of those asking prices,
each counted in proportion to its score. A listing rated Buy worthy at 150,000 therefore counts for far
more than one rated Caution at 200,000, and the price lands close to 150,000. Listings from scraping can
be added as another source of points later; for now these are the only ones.

Only models with at least one checked listing appear anywhere. A listing is matched to a line in
knowledge/products.py by its model name and model number; one that matches nothing gets a page of its own.
"""
import json
import os
import re
import time

from flask import current_app, url_for

import prices
import scoring
from extensions import db
from knowledge import products
from models import Analysis

MIN_LISTINGS = int(os.environ.get("PRICE_MIN_LISTINGS", "5"))            # checked listings before a price counts as accurate
CACHE_SECONDS = int(os.environ.get("PRICE_PAGE_CACHE_SECONDS", "60"))
IMAGE_EXTS = ("webp", "jpg", "jpeg", "png", "avif")
GOOD_SCORE = 70                      # Buy worthy and above
ACCEPTABLE_SCORE = 55                # Consider with caution and above

_LINE_RE = {lid: re.compile(v["match"], re.I) for lid, v in products.LINES.items()}
_NUM_RE = {lid: re.compile(v["number"], re.I) for lid, v in products.LINES.items()}
_GEN_BY_NUMBER = {(lid, n.upper()): g for lid, v in products.LINES.items()
                  for g in v["generations"] for n in g["model_numbers"]}
_GEN_BY_ID = {(lid, g["id"]): g for lid, v in products.LINES.items() for g in v["generations"]}
_cache = {"at": 0.0, "points": None}


# ── the catalogue and its pictures ────────────────────────────────────────
def latest_generation(line_id: str):
    gens = products.LINES.get(line_id, {}).get("generations") or []
    return max(gens, key=lambda g: g["year"]) if gens else None


def image(*names):
    """{url, credit} for the first picture found in static/products for any of these names, else None."""
    folder = os.path.join(current_app.static_folder, "products")
    for name in names:
        for ext in IMAGE_EXTS:
            if name and os.path.isfile(os.path.join(folder, f"{name}.{ext}")):
                return {"url": url_for("static", filename=f"products/{name}.{ext}"), "credit": products.IMAGE_CREDITS.get(name)}
    return None


def image_url(*names):
    found = image(*names)
    return found["url"] if found else None


# ── matching a listing to a line and a model ──────────────────────────────
def _generation_for(line_id: str, number: str):
    g = _GEN_BY_NUMBER.get((line_id, number))
    if g is None:
        for (lid, mn), gen in _GEN_BY_NUMBER.items():                  # SM-G781B and A315-59G belong to their base model
            if lid == line_id and number.startswith(mn):
                return gen
    return g


def _match_in(line_id: str, text: str) -> dict:
    m = _NUM_RE[line_id].search(text or "")
    number = m.group(0).upper() if m else None
    g = _generation_for(line_id, number) if number else None
    return {"line": line_id, "number": number, "generation": g["id"] if g else None,
            "year": g["year"] if g else None, "variant": g["id"] if g else (number.lower() if number else None)}


def classify(text: str) -> dict:
    """The line, model number, year and model a title or model name belongs to. {} when no line matches."""
    for lid, rx in _LINE_RE.items():
        if rx.search(text or ""):
            return _match_in(lid, text)
    return {}


def _identify(model: str, number: str, title: str, category: str) -> dict:
    text = " ".join(str(x) for x in (model, number, title) if x)
    c = classify(text)
    if c:
        return c
    key = prices.model_key(model or title) or "unknown"
    return {"line": "m-" + key[:60], "number": None, "generation": None, "year": None, "variant": None}


# ── the data: one point per checked listing ───────────────────────────────
def _load_points() -> list:
    q = (db.session.query(Analysis.id, Analysis.url_key, Analysis.title, Analysis.device_category, Analysis.price_amount,
                          Analysis.price_currency, Analysis.rating, Analysis.score, Analysis.created_at, Analysis.report_json)
         .filter(Analysis.device_category.in_(("phone", "laptop")))
         .filter(Analysis.price_amount.isnot(None)).filter(Analysis.score.isnot(None))
         .order_by(Analysis.created_at.desc(), Analysis.id.desc()))
    seen, out = set(), []
    for id_, url_key, title, cat, price, cur, rating, score, when, report_json in q:
        k = url_key or f"id{id_}"
        if k in seen:                                                    # a listing checked twice counts once, newest first
            continue
        seen.add(k)
        if (cur or "NPR").upper() != "NPR" or not isinstance(price, (int, float)) or price <= 0:
            continue
        try:
            ps = (json.loads(report_json or "{}").get("product_summary")) or {}
        except ValueError:
            ps = {}
        model = re.sub(r"\s*\(.*?\)", "", str(ps.get("model") or "")).strip()
        c = _identify(model, ps.get("model_number"), title, cat)
        out.append({"id": id_, "title": title, "model": model or title, "category": cat, "price": int(price),
                    "rating": rating, "score": int(score), "when": when, "condition": ps.get("stated_condition"),
                    "line": c["line"], "variant": c["variant"], "number": c["number"]})
    return out


def points() -> list:
    now = time.time()
    if _cache["points"] is None or now - _cache["at"] >= CACHE_SECONDS:
        _cache.update(points=_load_points(), at=now)
    return _cache["points"]


def _by_line() -> dict:
    out = {}
    for p in points():
        out.setdefault(p["line"], []).append(p)
    return out


# ── the price ─────────────────────────────────────────────────────────────
def weight(score: int) -> float:
    """How much a checked listing's price counts: 1 at a score of 90 or more, falling to a small floor at 20."""
    return min(1.0, max(0.05, (score - 20) / 70))


def _round(n: float) -> int:
    step = 500 if n >= 20000 else 100
    return int(round(n / step) * step)


def estimate(pts: list) -> dict:
    """The price the checked listings support. n is how many listings stand behind it."""
    pts = [p for p in pts if p.get("price") and p.get("score") is not None]
    if not pts:
        return {"n": 0, "price": None, "enough": False}
    ws = [weight(p["score"]) for p in pts]
    price = _round(sum(p["price"] * w for p, w in zip(pts, ws)) / sum(ws))
    ok = [p["price"] for p in pts if p["score"] >= ACCEPTABLE_SCORE]
    return {"n": len(pts), "price": price, "low": min(p["price"] for p in pts), "high": max(p["price"] for p in pts),
            "ok_low": min(ok) if ok else None, "ok_high": max(ok) if ok else None, "n_ok": len(ok),
            "n_good": sum(1 for p in pts if p["score"] >= GOOD_SCORE),
            "weak": not ok,                                              # no listing behind it was rated acceptable
            "enough": len(pts) >= MIN_LISTINGS}


_RATING_TONE = {"strong_buy": "ok", "buy_worthy": "ok", "consider_with_caution": "warn", "caution": "warn", "avoid": "bad"}


def _rows(pts: list, est: dict) -> list:
    """The listings behind a price, best verdict first, with how each sits against the estimate."""
    out = []
    for p in sorted(pts, key=lambda p: (-p["score"], p["price"])):
        diff = round((p["price"] - est["price"]) / est["price"] * 100) if est.get("price") else None
        out.append({**p, "label": scoring.RATING_LABEL.get(p["rating"], str(p["rating"] or "").replace("_", " ").capitalize()),
                    "tone": _RATING_TONE.get(p["rating"], ""), "diff": diff,
                    "weight_pct": round(weight(p["score"]) * 100)})
    return out


# ── lines and models ──────────────────────────────────────────────────────
def _meta(line_id: str, pts: list) -> dict:
    v = products.LINES.get(line_id)
    if v:
        return {"id": line_id, "registered": True, **v}
    p = pts[0]                                                           # newest first
    kind = "phone" if p["category"] == "phone" else "laptop"
    return {"id": line_id, "registered": False, "name": p["model"][:60] or "Unnamed model", "brand": None, "kind": kind,
            "kind_label": "Phone" if kind == "phone" else "Laptop", "generations": []}


def _variant_entry(line: dict, vid: str, pts: list) -> dict:
    g = _GEN_BY_ID.get((line["id"], vid))
    number = g["model_numbers"][0] if g else vid.upper()
    if g:
        title, sub = g["label"], f"{g['year']} · {number}"
    else:
        title, sub = f"{line['name']} {number}", "Model number only"
    est = estimate(pts)
    n = est["n"]
    return {"id": vid, "line": line["id"], "generation": g, "year": g["year"] if g else None, "number": number,
            "title": title, "sub": sub, "status": f"{n} listing{'s' if n != 1 else ''} checked", "est": est, "points": pts,
            "text": _norm(line["name"], title, sub, number, g["cpu"] if g else "", g["gpu"] if g else ""),
            "image": image_url(vid)}


def _variants(line: dict, pts: list) -> list:
    groups = {}
    for p in pts:
        if p["variant"]:
            groups.setdefault(p["variant"], []).append(p)
    out = [_variant_entry(line, vid, ps) for vid, ps in groups.items()]
    out.sort(key=lambda e: (-(e["year"] or 0), -e["est"]["n"], e["number"]))
    return out


def _norm(*parts) -> str:
    return re.sub(r"[^a-z0-9]+", " ", " ".join(str(p) for p in parts if p).lower()).strip()


def _line_image(line: dict):
    g = latest_generation(line["id"])
    return image(g["id"] if g else None, line["id"])


def has_data(line_id: str) -> bool:
    return line_id in _by_line()


def page_for(model, number, title, category):
    """The page for a report's device, or None when no checked listing makes one. Used for 'See the guide'."""
    c = _identify(str(model or "").strip(), number, title, category)
    pts = _by_line().get(c["line"])
    if not pts:
        return None
    if c["variant"] and any(p["variant"] == c["variant"] for p in pts):
        return url_for("price_variant", line_id=c["line"], variant_id=c["variant"])
    return url_for("price_line", line_id=c["line"])


# ── what each page needs ──────────────────────────────────────────────────
def _chip(line: dict, v=None) -> dict:
    if v is None:
        n = len(_by_line()[line["id"]])
        img = _line_image(line)
        return {"href": url_for("price_line", line_id=line["id"]), "title": line["name"], "sub": line["kind_label"],
                "status": f"{n} listing{'s' if n != 1 else ''} checked", "kind": line["kind"],
                "image": img["url"] if img else None, "text": _norm(line["name"], line["brand"], line["kind_label"])}
    return {"href": url_for("price_variant", line_id=line["id"], variant_id=v["id"]), "title": v["title"], "sub": v["sub"],
            "status": v["status"], "kind": line["kind"], "image": v["image"] or image_url(line["id"]), "text": v["text"]}


def home_page() -> dict:
    by = _by_line()
    metas = sorted(((_meta(lid, pts), len(pts)) for lid, pts in by.items()), key=lambda t: (-t[1], t[0]["name"]))
    cards, items = [], []
    for line, _n in metas:
        img = _line_image(line)
        cards.append({"id": line["id"], "name": line["name"], "kind": line["kind"], "image": img["url"] if img else None})
        items.append(_chip(line))
        items += [_chip(line, v) for v in _variants(line, by[line["id"]])]
    return {"cards": cards, "finder_items": items}


def line_page(line_id: str):
    pts = _by_line().get(line_id)
    if not pts:
        return None
    line = _meta(line_id, pts)
    variants = _variants(line, pts)
    est = estimate(pts)
    with_data = {v["generation"]["id"]: v for v in variants if v["generation"]}
    gens = sorted(line["generations"], key=lambda g: -g["year"])
    from knowledge import protocols
    steps = [] if line.get("check") else protocols.protocol(pts[0]["category"], line["name"], pts[0]["title"])["steps"]
    return {"line": line, "hero": _line_image(line), "est": est, "rows": _rows(pts, est), "variants": variants,
            "unplaced": sum(1 for p in pts if not p["variant"]) if variants else 0,
            "latest": [{"gen": g, "image": image_url(g["id"], line_id), "variant": with_data.get(g["id"])} for g in gens[:4]],
            "generations": [{"gen": g, "variant": with_data.get(g["id"])} for g in gens],
            "steps": steps, "finder_items": [_chip(line, v) for v in variants],
            "min_listings": MIN_LISTINGS, "draft": line["registered"] and products.STATUS == "draft",
            "draft_note": products.DRAFT_NOTE}


def variant_page(line_id: str, variant_id: str):
    pts = _by_line().get(line_id)
    if not pts:
        return None
    line = _meta(line_id, pts)
    variants = _variants(line, pts)
    v = next((e for e in variants if e["id"] == variant_id), None)
    if v is None:
        return None
    return {"line": line, "v": v, "est": v["est"], "rows": _rows(v["points"], v["est"]),
            "hero": image(v["id"], line_id), "finder_items": [_chip(line, e) for e in variants],
            "min_listings": MIN_LISTINGS, "draft": line["registered"] and products.STATUS == "draft",
            "draft_note": products.DRAFT_NOTE}
