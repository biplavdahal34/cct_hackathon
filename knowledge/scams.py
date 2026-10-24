"""Scam and risk patterns common on Nepali marketplaces, matched in Python.

Each pattern has a plain explanation of why it matters so the report can say
"this is a flag because…" instead of just "suspicious". Severity:
  high    walk away unless the seller proves otherwise (locks, blacklists, pay-before-meet)
  medium  needs an answer before you meet
  low     worth knowing, normal on its own

Text is matched against the original description and the English translation,
so romanized Nepali ("advance paisa", "bidesh bata") is covered by both."""
import re

REVIEWED = "2026-10-24"

_P = [
    # key, severity, title, why, regex
    ("locked_device", "high", "Mentions an account lock or a bypass",
     "A phone tied to someone else's Apple ID, Google account, Mi account or Samsung Knox cannot be used by you, "
     "and 'bypassed' phones lose that bypass after an update. Buy only after a factory reset in front of you.",
     re.compile(r"icloud\s*lock|activation\s*lock|\bfrp\b|google\s*lock|mi\s*account\s*lock|knox\s*lock|bypass(ed)?|"
                r"\bid\s*lock|id\s*(bhayeko|lageko|atkeko)|logo\s*(ma|mai)\s*(atk|rok)|without\s+(apple\s*id|password|google\s*id)|"
                r"wifi\s*only|no\s*sim\s*(service|working)", re.I)),
    ("not_registered", "high", "IMEI not registered or blocked",
     "An IMEI that is not registered with the NTA can be blocked from Nepali networks; a blacklisted one is reported stolen. "
     "Check the number on the NTA MDMS portal before paying anything.",
     re.compile(r"blacklist|imei\s*(block|blocked|band)|nta\s*(block|blocked|lagena|lagdaina|chaina|chhaina|register\s*(chaina|chhaina|hudaina|hunna))|"
                r"(not|non|no|chaina|chhaina)\s*(nta|mdms)\s*(registered|register)?|(nta|mdms)\s*(not|non)\s*register", re.I)),
    ("pay_first", "high", "Wants money before you see the device",
     "Advance payment, booking amounts or wallet transfers before an in-person meeting are the most common way buyers "
     "lose money on these sites. Pay only when the device is in your hand and tested.",
     re.compile(r"(advance|booking|token)\s*(payment|amount|charge|money|paisa|pay)|pay(ment)?\s*(first|in\s*advance|before\s*(delivery|meet|meeting))|"
                r"(half|50\s*%|full)\s*(payment\s*)?(advance|first|pahila|aghi)|(esewa|khalti|fonepay|ime\s*pay|bank)\s*(ma|through|via|bata)?\s*(pahila|first|advance|aghi)|"
                r"pahila\s*(paisa|payment)|paisa\s*pathau|cash\s*on\s*delivery\s*(not|chaina|hudaina)", re.I)),
    ("no_meetup", "medium", "Will not meet in person",
     "Courier-only or 'no meetup' sales remove the one protection you have: testing the device before paying.",
     re.compile(r"no\s*(meet|meetup|meet\s*up|visit|home\s*visit)|(courier|delivery|parcel|online|shipping)\s*only|"
                r"(can't|cannot|cant|not\s*able\s*to)\s*meet|bhet(na|nu)\s*(sakdina|mildaina|hudaina)|only\s*(courier|delivery)", re.I)),
    ("abroad_relative", "medium", "Sold for someone abroad",
     "'My cousin in Australia sent it' explains away a missing bill, a foreign region model and an owner who cannot sign out "
     "of the device. It is also the standard story for stolen phones. Ask for the original purchase proof.",
     re.compile(r"\b(abroad|bidesh|videsh|bidesh\s*bata|videsh\s*bata)\b|(from|bata)\s*(usa|america|australia|uk|dubai|qatar|korea|japan|malaysia|canada)\b|"
                r"\b(usa|america|australia|uk|dubai|qatar|korea|japan|malaysia|canada)\s*(bata|dekhi|from)\b|"
                r"(selling|bech)\w*\s*(for|on\s*behalf\s*of|ko\s*lagi)\s*(my|a|mero)?\s*(cousin|brother|sister|friend|uncle|aunt|relative|dai|bhai|didi|bahini|sathi)|"
                r"(cousin|brother|sister|friend|dai|bhai|didi|sathi)\s*(le|ko|from)?\s*(pathaeko|pathayeko|send|sent|brought|lyaeko|lyayeko)", re.I)),
    ("urgency", "low", "Pressure to decide now",
     "Urgency is how a seller stops you from checking. A real seller waits an hour while you test.",
     re.compile(r"\burgent(ly)?\b|today\s*only|first\s*come|last\s*price\s*today|(need|chahiyo)\s*(money|paisa)\s*(urgent|fast|aja|today)|"
                r"emergency\s*(sale|sell|bikri)|(aja|aaja)\s*(nai|matra)\s*bech", re.I)),
    ("off_platform", "low", "Moves the conversation off the site",
     "Chatting on WhatsApp, Viber or Telegram is normal here, but it also removes the listing history and the site's report button. Keep screenshots.",
     re.compile(r"(whatsapp|viber|telegram|imo|messenger)\s*(only|ma\s*matra|mai|only)|(only|matra)\s*(on\s*)?(whatsapp|viber|telegram)|dm\s*(for|me)\s*(price|details)", re.I)),
    ("sealed_cheap_words", "medium", "'Sealed' or 'box pack' at a used-phone price",
     "Sealed new phones well below shop price are usually refurbished units in a new box, demo units, or do not exist.",
     re.compile(r"seal(ed)?\s*(pack|box|piece)|box\s*pack(ed)?|non\s*open(ed)?\s*(box|piece)|brand\s*new\s*seal", re.I)),
    ("personal_use_claim", "low", "Says 'personal use'",
     "Useful only when the seller's history agrees. A dealer posting ten phones a month is not 'personal use'.",
     re.compile(r"personal(ly)?\s*(use|used)|self\s*use|afai\s*(chalayeko|use)|mero\s*(afnai|aafnai)", re.I)),
]

PATTERNS = {key: (sev, title, why, rx) for key, sev, title, why, rx in _P}


def _text(listing: dict, report: dict) -> str:
    da = report.get("description_analysis") or {}
    parts = [listing.get("description"), listing.get("title"),
             da.get("original_description"), da.get("translated_description")]
    return "\n".join(str(p) for p in parts if p)


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


def detect(listing: dict, report: dict, market: dict = None, seller: dict = None) -> list:
    """[{key, severity, title, why, evidence}] in severity order. `market` is the
    report's market_data (median of comparables, new-price anchor); `seller` is
    the seller profile from snapshots (ad_count, listings_30d)."""
    text = _text(listing, report)
    out = []
    matched = {}
    for key, (sev, title, why, rx) in PATTERNS.items():
        m = rx.search(text)
        if m:
            matched[key] = m.group(0)
            out.append({"key": key, "severity": sev, "title": title, "why": why,
                        "evidence": f"The listing says “{m.group(0).strip()}”."})

    # price-based patterns need market numbers
    ps = report.get("product_summary") or {}
    asking = _num((ps.get("asking_price") or {}).get("amount"))
    stats = (market or {}).get("market_stats") or {}
    median = _num(stats.get("median"))
    used_n = (market or {}).get("listings_used") or 0
    if asking and median and used_n >= 3:
        ratio = asking / median
        if ratio < 0.55:
            out.append({"key": "far_below_market", "severity": "high", "title": "Priced far below similar listings",
                        "why": "A working phone does not sell for half of what everyone else asks. The usual reasons are a lock, "
                               "a fake, a stolen unit, or a seller who never intends to deliver.",
                        "evidence": f"Asking NPR {asking:,.0f} against a median of NPR {median:,.0f} across {used_n} similar listings."})
        elif ratio < 0.75:
            out.append({"key": "below_market", "severity": "medium", "title": "Noticeably cheaper than similar listings",
                        "why": "Cheap is not a flag by itself, but ask what the seller is not mentioning: battery, repairs, region, lock status.",
                        "evidence": f"Asking NPR {asking:,.0f} against a median of NPR {median:,.0f} across {used_n} similar listings."})
    new_anchor = _num((market or {}).get("new_price_anchor"))
    if asking and new_anchor and "sealed_cheap_words" in matched and asking < 0.8 * new_anchor:
        for o in out:
            if o["key"] == "sealed_cheap_words":
                o["severity"] = "high"
                o["evidence"] += f" Brand-new listings of this model ask about NPR {new_anchor:,.0f}; this one asks NPR {asking:,.0f}."

    # seller history (from snapshots) vs "personal use"
    ads = (seller or {}).get("ad_count")
    if ads and ads >= 15:
        sev = "medium" if "personal_use_claim" in matched else "low"
        out.append({"key": "dealer", "severity": sev,
                    "title": "Seller is a dealer" + (" describing the phone as personal use" if sev == "medium" else ""),
                    "why": "Dealers are not bad, but dealer stock is often imported, refurbished or region-locked, and 'my own phone' "
                           "stories do not apply. Ask for a bill and a written warranty.",
                    "evidence": f"This seller has {ads} active listings on the site."})
        out = [o for o in out if o["key"] != "personal_use_claim"] if sev == "medium" else out

    # stock photos from the image pass
    imgs = (report.get("image_analysis") or {}).get("per_image") or []
    suspect = [i.get("image_index") for i in imgs if i.get("photo_authenticity") == "stock_or_suspect"]
    if imgs and len(suspect) >= max(1, (len(imgs) + 1) // 2):
        out.append({"key": "stock_photos", "severity": "medium", "title": "Photos may not be of this device",
                    "why": "Stock or copied photos mean you have not seen the actual unit. Ask for a photo with today's date written on paper next to it.",
                    "evidence": f"Photo{'s' if len(suspect) > 1 else ''} {', '.join(str(s) for s in suspect)} look{'' if len(suspect) > 1 else 's'} like stock or copied images."})

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda o: order.get(o["severity"], 3))
    return out
