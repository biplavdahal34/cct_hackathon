"""An opening offer with its reasons, from numbers the report already has:
the fair range, the comparables, repair estimates and the price-cut history.
Pure arithmetic; the copy-paste message is assembled from fixed sentences."""


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _round500(x):
    return int(round(x / 500.0) * 500)


def build(report: dict, repairs: dict, history: dict = None) -> dict:
    """{offer, walk_in, basis:[str], message} or {} when there is no asking price."""
    ps = report.get("product_summary") or {}
    pa = report.get("price_analysis") or {}
    md = report.get("market_data") or {}
    asking = _num((ps.get("asking_price") or {}).get("amount"))
    currency = (ps.get("asking_price") or {}).get("currency") or "NPR"
    if not asking or currency != "NPR":
        return {}
    rng = pa.get("estimated_fair_range_npr")
    lo = hi = None
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        lo, hi = _num(rng[0]), _num(rng[1])
    median = _num((md.get("market_stats") or {}).get("median"))
    rep_lo, rep_hi = (repairs or {}).get("total_low") or 0, (repairs or {}).get("total_high") or 0
    basis = []
    no_comps = False

    # anchor: the market, not the asking price
    if lo and hi and hi > lo:
        anchor = (lo + hi) / 2
        basis.append(f"Similar units are listed at NPR {int(lo):,}–{int(hi):,} right now.")
    elif median:
        anchor = median
        basis.append(f"The median of similar listings is NPR {int(median):,}.")
    else:
        anchor = asking * 0.9
        no_comps = True
        basis.append("No reliable comparables, so the offer starts 10% under the asking price.")
    if (repairs or {}).get("items"):
        for it in repairs["items"]:
            basis.append(f"{it['label']} costs about NPR {it['low']:,}–{it['high']:,} ({it['why'].rstrip('.')}).")
    for ch in ((history or {}).get("changes") or []):
        if ch.get("key") == "price_cut":
            basis.append("The seller has already cut the price once, so there is room.")
    if asking > anchor and lo and hi and asking > hi:
        basis.append(f"The asking price is above the top of the fair range by NPR {int(asking - hi):,}.")

    target = anchor - rep_hi          # pay the market price minus what you will spend fixing it
    walk_in = min(asking, max(anchor * 0.85, anchor - rep_hi - 2000))
    if no_comps:
        walk_in = min(asking, anchor - rep_hi)       # nothing to argue from: 10% under, minus the repairs
    target = min(target, asking)
    already_fair = asking <= anchor * 0.9 and walk_in >= asking * 0.95
    if already_fair:
        # the seller is under the market already: a small haggle, not a lowball
        walk_in = asking * 0.95
        target = asking
        basis.append("The asking price is already under what similar units list for, so a small haggle is all that is reasonable.")
    if target <= 0 or walk_in <= 0:
        return {}
    offer = _round500(walk_in)
    ceiling = _round500(target)
    if ceiling < offer:
        ceiling = offer
    if already_fair:
        msg = (f"Hi, I'm interested. The price looks fair for what similar ones list at. If everything checks out when I test it"
               + (f" (I'll budget about NPR {rep_lo:,}–{rep_hi:,} for the work it needs)" if rep_hi else "")
               + f", I can pay NPR {offer:,} in cash today. Would that work?")
    else:
        msg = (f"Hi, I'm interested. Similar ones are listed at around NPR {int(anchor):,} right now"
               + (f", and this one needs about NPR {rep_lo:,}–{rep_hi:,} of work" if rep_hi else "")
               + f". I can pay NPR {offer:,} in cash today after I test it. Would that work?")
    msg_ne = f"नमस्ते, मलाई यो मन पर्‍यो। आज जाँचेर नगद NPR {offer:,} दिन सक्छु, हुन्छ?"
    return {"offer": offer, "ceiling": ceiling, "asking": int(asking), "basis": basis,
            "message": msg, "message_ne": msg_ne, "already_fair": already_fair}
