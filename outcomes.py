"""Outcome feedback and the public calibration page.

A buyer who used a report can say what happened. Enough of those and BEAT can
show, per verdict level, how often it was right. That number is the product's
credibility; it is only shown once there is enough data to mean something."""
import os
from datetime import datetime, timezone

from extensions import db
from models import Outcome, Analysis

MIN_FOR_PUBLIC = int(os.environ.get("CALIBRATION_MIN_REPORTS", "20"))
LABELS = {
    "bought_as_described": "Bought it, as described",
    "bought_with_issues": "Bought it, found problems",
    "did_not_buy": "Did not buy",
    "scam": "It was a scam",
}
POSITIVE = ("strong_buy", "buy_worthy")
NEGATIVE = ("caution", "avoid")


def record(analysis: Analysis, user_id: int, outcome: str, note: str = "") -> Outcome:
    if outcome not in Outcome.OUTCOMES:
        raise ValueError("Pick one of the options.")
    row = Outcome.query.filter_by(analysis_id=analysis.id).first()
    if row is None:
        row = Outcome(analysis_id=analysis.id, user_id=user_id)
        db.session.add(row)
    row.url_key = analysis.url_key
    row.rating_at_time = analysis.rating
    row.score_at_time = analysis.score
    row.outcome = outcome
    row.note = (note or "").strip()[:300] or None
    row.created_at = datetime.now(timezone.utc)
    db.session.commit()
    return row


def for_analysis(analysis_id: int):
    return Outcome.query.filter_by(analysis_id=analysis_id).first()


def calibration() -> dict:
    """Per verdict: how many reports got feedback and how the purchases went.
    'hit' = a positive verdict followed by a purchase that was as described, or a
    cautious verdict followed by problems or a scam. 'Did not buy' is not a hit
    or a miss, so it stays out of the rate."""
    rows = Outcome.query.all()
    per = {}
    for r in rows:
        rating = r.rating_at_time or "unknown"
        p = per.setdefault(rating, {"rating": rating, "n": 0, "hits": 0, "misses": 0, "counts": {k: 0 for k in Outcome.OUTCOMES}})
        p["n"] += 1
        p["counts"][r.outcome] = p["counts"].get(r.outcome, 0) + 1
        if r.outcome == "did_not_buy":
            continue
        good = r.outcome == "bought_as_described"
        if rating in POSITIVE:
            p["hits" if good else "misses"] += 1
        elif rating in NEGATIVE:
            p["hits" if not good else "misses"] += 1
        else:                                   # consider_with_caution: right either way it was flagged
            p["hits" if (good or r.outcome == "bought_with_issues") else "misses"] += 1
    order = ["strong_buy", "buy_worthy", "consider_with_caution", "caution", "avoid"]
    out = []
    for rating in order:
        p = per.get(rating)
        if not p:
            continue
        judged = p["hits"] + p["misses"]
        p["hit_rate"] = round(p["hits"] / judged * 100) if judged else None
        p["judged"] = judged
        out.append(p)
    total = len(rows)
    return {"total": total, "per_rating": out, "enough": total >= MIN_FOR_PUBLIC, "min_for_public": MIN_FOR_PUBLIC,
            "labels": LABELS}
