"""Decode device model numbers and part numbers into facts a buyer in Nepal needs.

Why this exists: a seller writes "iPhone 14, US variant" or the About screen in a
photo shows "A2649". Whether the phone has a SIM tray, whether the camera shutter
sound can be turned off, and whether a local warranty can apply all follow from
that code. A language model guesses at these; a table does not.

Sources: Apple's published model lists and regional specification pages, Samsung
firmware region codes. REVIEWED is the date a person last checked the table.
Unknown codes decode to nothing, never to a guess."""
import re

REVIEWED = "2026-10-24"

# ── Apple "A" numbers ──────────────────────────────────────────────────────
# region keys: us · na (Canada/Japan/Mexico/Saudi) · intl · cn (China/HK/Macau) · jp
_APPLE = {}

def _apple(model, us=None, na=None, intl=None, cn=None, jp=None, us_esim_only=False):
    for code, region in ((us, "us"), (na, "na"), (intl, "intl"), (cn, "cn"), (jp, "jp")):
        if code:
            _APPLE[code] = {"model": model, "region": region,
                            "esim_only": region == "us" and us_esim_only}

_apple("iPhone X",          us="A1865", intl="A1901", jp="A1902")
_apple("iPhone XR",         us="A1984", intl="A2105", jp="A2106", cn="A2108")
_apple("iPhone XS",         us="A1920", intl="A2097", jp="A2098", cn="A2100")
_apple("iPhone XS Max",     us="A1921", intl="A2101", jp="A2102", cn="A2104")
_apple("iPhone 11",         us="A2111", intl="A2221", cn="A2223")
_apple("iPhone 11 Pro",     us="A2160", intl="A2215", cn="A2217")
_apple("iPhone 11 Pro Max", us="A2161", intl="A2218", cn="A2220")
_apple("iPhone SE (2020)",  us="A2275", intl="A2296", cn="A2298")
_apple("iPhone 12 mini",    us="A2176", na="A2398", intl="A2399", cn="A2400")
_apple("iPhone 12",         us="A2172", na="A2402", intl="A2403", cn="A2404")
_apple("iPhone 12 Pro",     us="A2341", na="A2406", intl="A2407", cn="A2408")
_apple("iPhone 12 Pro Max", us="A2342", na="A2410", intl="A2411", cn="A2412")
_apple("iPhone 13 mini",    us="A2481", na="A2626", intl="A2628", cn="A2629")
_apple("iPhone 13",         us="A2482", na="A2631", intl="A2633", cn="A2634")
_apple("iPhone 13 Pro",     us="A2483", na="A2636", intl="A2638", cn="A2639")
_apple("iPhone 13 Pro Max", us="A2484", na="A2641", intl="A2643", cn="A2644")
_apple("iPhone SE (2022)",  us="A2595", jp="A2782", intl="A2783", cn="A2784")
_apple("iPhone 14",         us="A2649", na="A2881", intl="A2882", cn="A2883", us_esim_only=True)
_apple("iPhone 14 Plus",    us="A2632", na="A2885", intl="A2886", cn="A2887", us_esim_only=True)
_apple("iPhone 14 Pro",     us="A2650", na="A2889", intl="A2890", cn="A2891", us_esim_only=True)
_apple("iPhone 14 Pro Max", us="A2651", na="A2893", intl="A2894", cn="A2895", us_esim_only=True)
_apple("iPhone 15",         us="A2846", na="A3089", intl="A3090", cn="A3092", us_esim_only=True)
_apple("iPhone 15 Plus",    us="A2847", na="A3093", intl="A3094", cn="A3096", us_esim_only=True)
_apple("iPhone 15 Pro",     us="A2848", na="A3101", intl="A3102", cn="A3104", us_esim_only=True)
_apple("iPhone 15 Pro Max", us="A2849", na="A3105", intl="A3106", cn="A3108", us_esim_only=True)
_apple("iPhone 16",         us="A3081", na="A3286", intl="A3287", cn="A3288", us_esim_only=True)
_apple("iPhone 16 Plus",    us="A3082", na="A3289", intl="A3290", cn="A3291", us_esim_only=True)
_apple("iPhone 16 Pro",     us="A3083", na="A3292", intl="A3293", cn="A3294", us_esim_only=True)
_apple("iPhone 16 Pro Max", us="A3084", na="A3295", intl="A3296", cn="A3297", us_esim_only=True)

_APPLE_REGION = {
    "us":   "United States model",
    "na":   "Canada / Japan / Mexico / Saudi Arabia model",
    "intl": "International model (the one sold in most of Asia and Europe)",
    "cn":   "China / Hong Kong / Macau model",
    "jp":   "Japan model",
}

# Apple part-number suffix (Settings › General › About › Model Number, e.g. MLPF3J/A)
_APPLE_PART_REGION = {
    "LL": "United States", "J": "Japan", "ZP": "Hong Kong", "CH": "China", "HN": "India",
    "B": "United Kingdom", "X": "Australia / New Zealand", "C": "Canada", "ZA": "Singapore",
    "KH": "South Korea", "VN": "Vietnam", "TH": "Thailand", "AE": "United Arab Emirates",
    "ID": "Indonesia", "MY": "Malaysia", "TA": "Taiwan", "RU": "Russia", "ET": "Germany",
    "FS": "Germany", "ZD": "Germany / Europe", "QN": "Norway / Nordics", "KS": "Sweden",
}

# ── Samsung SM- suffixes ───────────────────────────────────────────────────
_SAMSUNG_SUFFIX = {
    "B":  ("International", "Sold across Asia, Europe and the Middle East. Normal for Nepal."),
    "F":  ("International (older Europe code)", "Normal for Nepal on older models."),
    "E":  ("Regional (Asia-Pacific / Latin America)", "Normal for Nepal."),
    "U":  ("United States carrier model", "Often carrier-locked and may be missing some network bands used here. "
           "Ask the seller to prove it is unlocked with a Nepali SIM."),
    "U1": ("United States unlocked model", "Unlocked, but check the network bands and whether updates arrive late."),
    "W":  ("Canada model", "Usually unlocked; check the network bands."),
    "N":  ("South Korea model", "The camera shutter sound cannot be turned off, and some Samsung services "
           "are tied to Korea. Common on cheap imports."),
    "0":  ("China / Hong Kong model", "Google apps may be missing or added later; check updates and Samsung Pay."),
    "V":  ("Verizon (US) model", "Carrier model; confirm it is unlocked and works with a Nepali SIM."),
    "T":  ("T-Mobile (US) model", "Carrier model; confirm it is unlocked and works with a Nepali SIM."),
    "A":  ("AT&T (US) model", "Carrier model; confirm it is unlocked and works with a Nepali SIM."),
}

_RE_APPLE = re.compile(r"\b(A\d{4})\b")
_RE_APPLE_PART = re.compile(r"\bM[A-Z0-9]{3,4}([A-Z]{1,2})/A\b")
_RE_SAMSUNG = re.compile(r"\b(SM-[A-Z]\d{3}[A-Z0-9]{1,2}(?:/DS)?)\b", re.I)
# Japanese carrier-locked Samsung models (docomo SC-xx, au SCG/SCV, SoftBank)
_RE_SAMSUNG_JP = re.compile(r"\b(SC-?\d{2}[A-Z]|SCG\d{2}|SCV\d{2})\b", re.I)
_RE_JP_WORDS = re.compile(r"\b(jp|japan(?:ese)?|japani)\b", re.I)
_RE_US_WORDS = re.compile(r"\b(us(?:a)?\s*(?:variant|version|model|set)|american\s+(?:variant|version|model))\b", re.I)

ESIM_NOTE = ("No physical SIM tray: it only works with an eSIM. Nepal Telecom and Ncell issue eSIMs, "
             "but not on every plan or in every office, so check your own line before paying.")
JP_SHUTTER_NOTE = ("Japan-market phones play the camera shutter sound every time and it cannot be turned off.")


def decode(*texts) -> list:
    """Return one finding per recognised model or part number found in `texts`.
    Each finding: {code, model, region, facts: [str], warnings: [str]}.
    Facts are neutral; warnings are things that cost money or break expectations."""
    out, seen = [], set()
    blob = " ".join(str(t) for t in texts if t)
    if not blob.strip():
        return out

    for code in _RE_APPLE.findall(blob):
        spec = _APPLE.get(code.upper())
        if not spec or code in seen:
            continue
        seen.add(code)
        region = spec["region"]
        facts = [f"{code} is the {_APPLE_REGION[region]} of the {spec['model']}."]
        warnings = []
        if spec["esim_only"]:
            warnings.append(ESIM_NOTE)
        elif region == "cn":
            facts.append("Takes two physical SIM cards and has no eSIM.")
        else:
            facts.append("Has a SIM tray plus eSIM.")
        if region == "jp":
            warnings.append(JP_SHUTTER_NOTE)
        out.append({"code": code, "model": spec["model"], "region": region,
                    "facts": facts, "warnings": warnings})

    for m in _RE_APPLE_PART.finditer(blob):
        suffix, full = m.group(1), m.group(0)
        if full in seen:
            continue
        seen.add(full)
        country = _APPLE_PART_REGION.get(suffix)
        if not country:
            continue
        facts = [f"Part number {full} means the unit was first sold in {country}."]
        warnings = []
        if suffix == "J":
            warnings.append(JP_SHUTTER_NOTE)
        if suffix != "HN":
            facts.append("Not an India-region unit, so Apple's India warranty does not apply; Nepal has no Apple "
                         "warranty of its own, only reseller warranties.")
        out.append({"code": full, "model": "Apple device", "region": suffix.lower(),
                    "facts": facts, "warnings": warnings})

    for m in _RE_SAMSUNG.finditer(blob):
        code = m.group(1).upper()
        if code in seen:
            continue
        seen.add(code)
        body = code.split("/")[0]            # SM-S908B/DS → SM-S908B
        suffix = re.sub(r"^SM-[A-Z]\d{3}", "", body)
        label, note = _SAMSUNG_SUFFIX.get(suffix, (None, None))
        if not label and suffix[-1:] in _SAMSUNG_SUFFIX and len(suffix) == 2 and suffix[0].isdigit():
            # SM-S9080 style: the trailing 0 is the region
            label, note = _SAMSUNG_SUFFIX["0"]
        if not label:
            continue
        facts = [f"{code} is the {label} of this Samsung."]
        warnings = [note] if ("lock" in note or "shutter" in note or "missing" in note) else []
        if not warnings:
            facts.append(note)
        if code.endswith("/DS"):
            facts.append("DS means dual SIM.")
        out.append({"code": code, "model": "Samsung Galaxy", "region": suffix.lower(),
                    "facts": facts, "warnings": warnings})

    for m in _RE_SAMSUNG_JP.finditer(blob):
        code = m.group(1).upper()
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "model": "Samsung Galaxy (Japan carrier)", "region": "jp",
                    "facts": [f"{code} is a Japanese carrier model number."],
                    "warnings": [JP_SHUTTER_NOTE,
                                 "Japanese carrier Samsungs can be SIM-locked to that carrier; ask for proof "
                                 "it works with a Nepali SIM."]})

    # words, not codes: "JP variant", "US set"
    if _RE_JP_WORDS.search(blob) and not any(f["region"] == "jp" for f in out):
        out.append({"code": "JP", "model": "Japan-market unit (from the description)", "region": "jp",
                    "facts": ["The seller describes this as a Japanese unit."], "warnings": [JP_SHUTTER_NOTE]})
    if _RE_US_WORDS.search(blob) and not any(f["region"] == "us" for f in out):
        out.append({"code": "US", "model": "US-market unit (from the description)", "region": "us",
                    "facts": ["The seller describes this as a US unit."],
                    "warnings": ["US iPhones from the iPhone 14 onward have no SIM tray. " + ESIM_NOTE,
                                 "US Android phones are often carrier models; ask for proof it is unlocked."]})
    return out


def warnings(*texts) -> list:
    """Flat list of warning sentences, de-duplicated, for the report's red-flag area."""
    seen, out = set(), []
    for f in decode(*texts):
        for w in f["warnings"]:
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out
