"""Usage caps for analyses that spend BEAT's shared (free-tier) API keys.

Users who bring their own key for a provider are exempt from that provider's
caps. Counters live in the database (UsageEvent), so they are exact across
gunicorn workers and survive restarts. Every number is configurable by env.

Caps:
  FREE_ANALYSES_PER_HOUR         per user, analyses using the shared Gemini key
  FREE_FACEBOOK_PER_HOUR         per user, Facebook listings using the shared Apify token
  FREE_ANALYSES_PER_DAY_GLOBAL   everyone combined, shared Gemini key (circuit breaker)
  FREE_FACEBOOK_PER_DAY_GLOBAL   everyone combined, shared Apify token (circuit breaker)
"""
import os
from datetime import datetime, timedelta, timezone

from extensions import db
from models import UsageEvent


def _cfg(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PER_USER_HOUR = _cfg("FREE_ANALYSES_PER_HOUR", 6)
FB_PER_USER_HOUR = _cfg("FREE_FACEBOOK_PER_HOUR", 2)
GLOBAL_DAY = _cfg("FREE_ANALYSES_PER_DAY_GLOBAL", 100)
FB_GLOBAL_DAY = _cfg("FREE_FACEBOOK_PER_DAY_GLOBAL", 15)

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def _count(since, user_id=None, server_gemini=None, server_apify=None):
    q = UsageEvent.query.filter(UsageEvent.created_at >= since)
    if user_id is not None:
        q = q.filter(UsageEvent.user_id == user_id)
    if server_gemini is not None:
        q = q.filter(UsageEvent.server_gemini == server_gemini)
    if server_apify is not None:
        q = q.filter(UsageEvent.server_apify == server_apify)
    return q.count()


def consume(user_id: int, ip: str, source: str, server_gemini: bool, server_apify: bool):
    """Record an attempt, then check the caps *including* it. If a cap is
    exceeded the row is removed again and a user-facing message is returned.
    Insert-then-count keeps two workers from both squeezing under the limit.
    Returns None when the attempt may proceed."""
    if not (server_gemini or server_apify):
        return None                                  # fully BYOK: nothing to meter

    now = datetime.now(timezone.utc)
    ev = UsageEvent(user_id=user_id, ip=ip, source=source,
                    server_gemini=server_gemini, server_apify=server_apify, created_at=now)
    db.session.add(ev)
    db.session.commit()

    msg = None
    if server_gemini:
        if _count(now - HOUR, user_id=user_id, server_gemini=True) > PER_USER_HOUR:
            msg = (f"You've used your {PER_USER_HOUR} free checks for this hour. "
                   "Try again later, or add your own Gemini key to check without this limit.")
        elif _count(now - DAY, server_gemini=True) > GLOBAL_DAY:
            msg = ("Today's shared free checks are all used up. "
                   "Add your own Gemini key to keep going, or try again tomorrow.")
    if msg is None and server_apify:
        if _count(now - HOUR, user_id=user_id, server_apify=True) > FB_PER_USER_HOUR:
            msg = (f"Facebook listings are limited to {FB_PER_USER_HOUR} an hour on the shared keys. "
                   "Try a Hamrobazaar link, wait a bit, or add your own Apify token.")
        elif _count(now - DAY, server_apify=True) > FB_GLOBAL_DAY:
            msg = ("Today's shared Facebook checks are all used up. "
                   "Add your own Apify token to keep going, or try again tomorrow.")

    if msg:
        db.session.delete(ev)
        db.session.commit()
        return msg

    # light housekeeping: counters only ever look back one day
    if ev.id % 50 == 0:
        UsageEvent.query.filter(UsageEvent.created_at < now - 2 * DAY).delete()
        db.session.commit()
    return None


def remaining(user_id: int) -> dict:
    """For the account page: what's left this hour on the shared keys."""
    since = datetime.now(timezone.utc) - HOUR
    return {
        "analyses": max(0, PER_USER_HOUR - _count(since, user_id=user_id, server_gemini=True)),
        "analyses_cap": PER_USER_HOUR,
        "facebook": max(0, FB_PER_USER_HOUR - _count(since, user_id=user_id, server_apify=True)),
        "facebook_cap": FB_PER_USER_HOUR,
    }
