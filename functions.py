import os
import re
import json

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urljoin, quote

from google import genai
from google.genai import types
import uuid


# ── Oxylabs proxy (optional) ─────────────────────────────────────────────
# Set these in your .env / environment instead of hardcoding:
#   OXYLABS_USERNAME=user-xxxx-country-US   (your FULL oxylabs username)
#   OXYLABS_PASSWORD=your-oxylabs-password
# If unset, the scraper connects directly (no proxy).
_OXY_USER = os.environ.get("OXYLABS_USERNAME")
_OXY_PASS = os.environ.get("OXYLABS_PASSWORD")
proxies = None
if _OXY_USER and _OXY_PASS:
    _p = f"http://{_OXY_USER}:{_OXY_PASS}@dc.oxylabs.io:8000"
    proxies = {"http": _p, "https": _p}

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124 Safari/537.36"}



SEARCH_URL = "https://hamrobazaar.com/search/product?q={query}&Latitude=0&Longitude=0"


_JUNK_PATTERNS = re.compile(
    r"parts|not\s*working|battery|batteries|charger|cable|adapter|earphone"
    r"|headphone|cover|case|tempered|glass|lcd|screen\s*only|display\s*only"
    r"|housing|motherboard|logic\s*board|back\s*panel|skin|sticker|dummy"
    r"|replica|fake|flex|connector|ic\s*chip",
    re.I,
)

SEARCH_API = "https://hamrobazaar.com/api/products/search"


def _listings_from_json(node, out):
    """
    Walk an arbitrary API JSON tree and pull out {title, price, url} dicts.
    Tolerant of unknown key names — tries the common candidates.
    """
    if isinstance(node, dict):
        lk = {k.lower(): k for k in node}
        tk = lk.get("title") or lk.get("name") or lk.get("productname")
        pk = lk.get("price") or lk.get("sellingprice") or lk.get("amount") \
             or lk.get("sellingpricenpr") or lk.get("priceamount")
        uk = lk.get("url") or lk.get("slug") or lk.get("seourl") \
             or lk.get("producturl") or lk.get("id")

        title = node.get(tk) if tk else None
        price = node.get(pk) if pk else None
        uval = node.get(uk) if uk else None

        if isinstance(price, dict):                      # e.g. {"amount": 51000}
            price = price.get("amount") or price.get("value")

        if title and price is not None and uval:
            try:
                price = int(re.sub(r"[^\d]", "", str(price)))
            except (ValueError, TypeError):
                price = None

            if price and 1000 <= price <= 20_000_000 and title not in ("", None):
                uval = str(uval)
                if uval.startswith("http"):
                    url = uval
                elif uval.startswith("/"):
                    url = urljoin("https://hamrobazaar.com/", uval)
                else:
                    # bare slug/uuid → build the detail URL
                    url = f"https://hamrobazaar.com/detail/{uval}"
                out.append({"title": str(title).strip(), "price": price, "url": url})

        for v in node.values():
            _listings_from_json(v, out)
    elif isinstance(node, list):
        for v in node:
            _listings_from_json(v, out)


def _search_api(product_name: str, exclude_url: str = None, limit: int = 10) -> list:
    """Search via the site's own JSON API (verified working)."""
    device_id = str(uuid.uuid4())
    payload = {
        "keyword": product_name,
        "deviceId": device_id,
        "deviceSource": "web",
    }
    r = requests.post(
        SEARCH_API,
        json=payload,
        headers={
            "User-Agent": _UA["User-Agent"],
            "Origin": "https://hamrobazaar.com",
            "Referer": "https://hamrobazaar.com/search/product",
            "Cookie": f"deviceId={device_id}",
        },
        proxies=proxies,
        timeout=30,
    )
    r.raise_for_status()

    found = []
    _listings_from_json(r.json(), found)

    # dedupe, junk-filter, exclude the subject listing, cap at limit
    seen, results = set(), []
    for item in found:
        u = item["url"].split("?")[0]
        if u in seen:
            continue
        if exclude_url and u.rstrip("/") == exclude_url.rstrip("/"):
            continue
        if _JUNK_PATTERNS.search(item["title"]):
            continue
        seen.add(u)
        results.append(item)
        if len(results) >= limit:
            break
    return results

def _search_html(product_name: str, exclude_url: str = None, limit: int = 10) -> list:
    """
    Scrape HamroBazaar search results for `product_name`.
    Returns [{'title': str, 'price': int, 'url': str}, ...] — keyword-filtered,
    deduped, capped at `limit`, subject listing excluded.
    """
    search_url = SEARCH_URL.format(query=quote(product_name))
    r = requests.get(search_url, proxies=proxies, headers=_UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    try:
        results = _search_api(product_name, exclude_url, limit)
        if len(results) >= 2:
            return results
    except Exception:
        pass
        return _search_html(product_name, exclude_url, limit)   

    seen, results = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/detail/" not in href:
            continue
        full = urljoin("https://hamrobazaar.com/", href).split("?")[0]
        if full in seen:
            continue
        if exclude_url and full.rstrip("/") == exclude_url.rstrip("/"):
            continue
        seen.add(full)

        # title: image alt first, then headings, then raw card text
        title = None
        img = a.find("img", alt=True)
        if img and img["alt"].strip() and img["alt"].strip().lower() != "profile image":
            title = img["alt"].strip()
        if not title:
            h = a.find(["h1", "h2", "h3", "p"], class_=re.compile("heading|font-semibold"))
            if h:
                title = h.get_text(strip=True)
        if not title:
            title = a.get_text(" ", strip=True)[:120]
        if not title:
            continue

        # price: strip storage tokens (512GB → would look like a price),
        # then take the largest plausible number (handles Nepali 1,23,456 grouping)
        text = a.get_text(" ", strip=True)
        text = re.sub(r"\b\d+\s*(?:gb|tb)\b", " ", text, flags=re.I)
        tokens = re.findall(r"\d{1,3}(?:,\d{2,3})+|\d{4,7}", text)
        price = None
        if tokens:
            values = [int(t.replace(",", "")) for t in tokens]
            values = [v for v in values if 1000 <= v <= 20_000_000]
            if values:
                price = max(values)

        if price is None:
            continue
        if _JUNK_PATTERNS.search(title):
            continue

        results.append({"title": title, "price": price, "url": full})
        if len(results) >= limit:
            break

    return results

def search_listings(product_name: str, exclude_url: str = None, limit: int = 10) -> list:
    """API first (clean structured data), HTML scrape as fallback."""
    try:
        results = _search_api(product_name, exclude_url=exclude_url, limit=limit)
        if len(results) >= 2:
            return results
    except Exception:
        pass
    return _search_html(product_name, exclude_url=exclude_url, limit=limit)


market_prompt = """You are a second-hand smartphone price analyst for the Nepali market (HamroBazaar).
You receive: the phone model being valued, the seller's asking price (NPR), and a list of
comparable listings scraped live from HamroBazaar search results today (some junk may remain).

TASK
1. EXCLUDE listings that are not a complete, working phone of the model family: spare
   batteries, chargers, cables, cases, tempered/back glass replacements, LCD or screen-only,
   housings, motherboards/logic boards, "for parts" / "not working" devices, accessories,
   and clearly different models. List each exclusion in excluded_listings with a short reason.
2. From the remaining usable listings compute market_stats: min, max, median, average (NPR).
3. Establish a fair price range for the model. Listings vary in storage and condition —
   reason about which are closest to the subject and weight them accordingly.
4. Compare the asking price to your fair range and give a verdict.

VERDICT (exactly one):
- "good_deal" — asking price is below the fair range
- "fair" — asking price is within the fair range
- "overpriced" — asking price is above the fair range
- "suspiciously_cheap" — far below it (possible scam, stolen, or hidden fault)

OUTPUT: ONLY a JSON object exactly in this shape, with REAL numbers from the data (never 0 placeholders):
{
  "listings_found": 0,
  "listings_used": 0,
  "excluded_listings": [{"title": "...", "reason": "..."}],
  "market_stats": {"min": 0, "max": 0, "median": 0, "average": 0},
  "price_analysis": {
    "verdict": "good_deal | fair | overpriced | suspiciously_cheap",
    "estimated_fair_range_npr": [0, 0],
    "reasoning": "3-6 sentences referencing the actual comparable listings",
    "confidence": "low | medium | high"
  }
}
If fewer than 3 usable listings remain after exclusions, set confidence "low" and widen
the fair range cautiously, saying so in the reasoning.

<product_name>
{{PRODUCT_NAME}}
</product_name>

<asking_price>
{{ASKING_PRICE}} NPR
</asking_price>

<listings>
{{LISTINGS_JSON}}
</listings>"""


def market_analysis(product_name: str, asking_price, exclude_url: str = None) -> dict:
    """Search + scrape comparables, then value the phone with a text-only Gemini call."""
    if not product_name:
        raise ValueError("No product name available to search for.")

    listings = search_listings(product_name, exclude_url=exclude_url, limit=10)
    if len(listings) < 2:
        raise ValueError("Not enough comparable listings found on HamroBazaar.")

    prompt = (market_prompt
              .replace("{{PRODUCT_NAME}}", product_name)
              .replace("{{ASKING_PRICE}}", str(asking_price if asking_price is not None else "unknown"))
              .replace("{{LISTINGS_JSON}}", json.dumps(listings, ensure_ascii=False)))

    resp = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=8192,
        ),
    )
    result = json.loads(resp.text)
    result["listings"] = listings          # pass the raw comps through for display
    return result


def scrape_url(url):

    base_url = f"{url}"
    r = requests.get(base_url, proxies=proxies, headers=_UA, timeout=30)

    soup = BeautifulSoup(r.text, "lxml")

    title = None
    for h1 in soup.find_all("h1"):
        if "heading-h3" in h1.get("class", []):
            title = h1.get_text(strip=True)
            break

    condition = None
    for div in soup.find_all("div"):
        if div.get("data-testid") == "product-condition-badge":
            condition = div.get_text(strip=True)
            break

    price = {"amount": None, "currency": None, "type": None}
    for p in soup.find_all("p"):
        if p.get_text(strip=True) == "Price":
            box = p.parent
            for span in box.find_all("span"):
                classes = span.get("class", [])
                if "heading-h2" in classes:
                    raw = span.get_text(strip=True).replace(",", "")
                    if raw.isdigit():
                        price["amount"] = int(raw)
                if "text-nowrap" in classes:
                    price["type"] = span.get_text(strip=True)
            for use in box.find_all("use"):
                if "nepali-rupee" in use.get("href", ""):
                    price["currency"] = "NPR"
            break

    address = None
    for svg in soup.find_all("svg"):
        if "lucide-map-pin" in svg.get("class", []):
            for span in svg.parent.find_all("span"):
                if "truncate" in span.get("class", []):
                    address = span.get_text(strip=True)
            break

    delivery = {"available": None, "details": None}
    for span in soup.find_all("span"):
        if "font-semibold" in span.get("class", []):
            text = span.get_text(strip=True)
            if "deliver" in text:
                delivery["details"] = span.parent.get_text(" ", strip=True)
                if "not deliver" in text:
                    delivery["available"] = False
                else:
                    delivery["available"] = True
                break

    seller = {"name": None, "profile_picture": None, "phone": None, "profile_url": None}
    for a in soup.find_all("a"):
        if a.get("aria-label") == "View seller profile":
            href = a.get("href") or ""
            if href:
                seller["profile_url"] = urljoin("https://hamrobazaar.com/", href)
            for p in a.find_all("p"):
                if "font-semibold" in p.get("class", []):
                    seller["name"] = p.get("title") or p.get_text(strip=True)
            for img in a.find_all("img"):
                if img.get("alt") == "Profile Image":
                    src = img.get("src", "")
                    query = parse_qs(urlparse(src).query)
                    if "url" in query:
                        seller["profile_picture"] = query["url"][0]
                    else:
                        seller["profile_picture"] = src
            for span in a.find_all("span"):
                text = span.get_text(strip=True)
                if re.fullmatch(r"[\d*]{8,}", text):
                    seller["phone"] = text
            break

    description = None
    for h2 in soup.find_all("h2"):
        if "About this listing" in h2.get_text():
            section = h2.find_parent("section")
            p = section.select_one("p")
            if p:
                description = p.get_text(strip=True)
            break

    specifications = {}
    for h2 in soup.find_all("h2"):
        if "Specifications" in h2.get_text():
            section = h2.find_parent("section")
            grid = None
            for div in section.find_all("div"):
                if "grid" in div.get("class", []):
                    grid = div
                    break
            if grid:
                cells = grid.find_all("div", recursive=False)
                for i in range(0, len(cells) - 1, 2):
                    key = cells[i].get_text(strip=True)
                    value = cells[i + 1].get_text(strip=True)
                    specifications[key] = value
            break

    for script in soup.find_all("script"):
        text = script.get_text()
        start = text.find("productAttributeValues")
        if start == -1:
            continue
        end = text.find("productMedia", start)
        chunk = text[start:end]
        pairs = re.findall(r'\\"attributeName\\":\\"(.*?)\\",\\"value\\":\\"(.*?)\\"', chunk)
        for key, value in pairs:
            if key not in specifications:
                specifications[key] = value
        break

    product_img = []
    for img in soup.find_all("img", class_="rounded-md"):
        src = (img.get("src") or img.get("data-src")
               or img.get("data-lazy-src") or img.get("data-original"))
        if src and src not in product_img:
            product_img.append(src)

    if not product_img:
        for img in soup.find_all("img", class_="scale-110"):
            src = (img.get("src") or img.get("data-src")
                   or img.get("data-lazy-src") or img.get("data-original"))
            if src and src not in product_img:
                product_img.append(src)

    return {
        "title": title,
        "condition": condition,
        "product_img": list(product_img),
        "price": price,
        "address": address,
        "delivery": delivery,
        "seller": seller,
        "description": description,
        "specifications": specifications,
    }


# ── Gemini client: reads GOOGLE_API_KEY from the environment ─────────────
# (the key was previously hardcoded and pasted into chats — rotate it!)
client = genai.Client()

gemini_prompt = """You are an expert second-hand electronics inspector and marketplace listing analyst. You will receive:

1. A JSON object describing a product listing (between <product_json> tags), scraped from the Nepali marketplace Hamrobazaar.
2. Zero or more product photos attached in the same order as the "product_img" array.

Produce a thorough, honest buyer's report as a single JSON object.

STEP 0 — IDENTIFY THE DEVICE
- Determine device_category: "phone" | "laptop" | "desktop". Infer from the title, specifications, and photos. If ambiguous, pick the best guess and note it in limitations.
- Everything downstream must be appropriate to that category. NEVER evaluate features the category cannot have (e.g., do not assess Face ID on a laptop, a keyboard on a phone, or a GPU on a phone).

STEP 1 — PARSE THE LISTING
- Read every field, including the seller description.
- Descriptions often mix English with romanized Nepali ("2ta vayera auta bechna lagya ho" = "I have two units, personally used, selling one"; "BH" = battery health; "no ex" = no exchange; "non open" = never opened/repaired). Translate the full description into natural English and extract every factual claim the seller makes.
- Common claims (extract when mentioned, leave null otherwise): battery health % or cycle count, storage/RAM configuration, box/accessories/charger included, repair or opening history, reset/update status, warranty, reason for selling.
- Category-specific claims (only when the category supports them AND the seller mentions them). Put each in category_specific_claims using a short snake_case key:
  - phone: biometric unlock (Face ID / fingerprint), account-lock status (iCloud / FRP / Knox / Mi), network/SIM lock, camera function, fast charging, screen originality.
  - laptop: keyboard and backlight, hinge, trackpad, panel type/quality, dedicated GPU, OS and license, original charger.
  - desktop: CPU / GPU / RAM / storage specifics, PSU, OS and activation, custom build vs branded, included peripherals, warranty.
- Mark working-state claims as "claimed_working" | "claimed_broken" | "not_mentioned". Never invent a claim the seller did not make.

STEP 2 — INSPECT EACH ATTACHED IMAGE
Analyze each photo one at a time. For each, list visible defects with location, severity, and confidence.
For ALL categories: exterior condition (scratches, dents, dings, chips, cracks, bent parts), signs of repair or replaced parts (color mismatch, uneven gaps, wrong screws), completeness vs what should be included, and photo authenticity — stock, edited, or mismatched images are a red flag.
Then category-specific checks:
- phone: screen (cracks, deep scratches, dead/stuck pixels, discoloration, dark spots, burn-in, protector/case fitted), frame and back glass, camera lenses (chips, cracks, dust inside), charging port (lint, wear), buttons, SIM tray, any water-damage hints.
- laptop: screen (dead pixels, backlight bleed, hinge damage or looseness, lid alignment), keyboard (missing keys, shiny worn keys, dead-key evidence), trackpad wear, palmrest/body wear, ports, vents/fans (dust), charger included and condition.
- desktop: judge each visible component — graphics card (fan condition, yellowing/heat discoloration, dust), case interior (dust buildup, loose cables), motherboard I/O, CPU cooler, storage drives, PSU if visible, monitor/peripherals if shown.
- Distinguish real defects from reflections, glare, dust, and compression artifacts. When unsure, mark confidence "low" and explain what you see.
- Photos cannot verify internals or function: storage SMART health, thermals under load, board-level faults, actual benchmark performance, all keys/ports working. These belong in not_verifiable_from_images — do not guess.

STEP 3 — CROSS-CHECK AND ASSESS
- Compare description claims against what the images show. Note contradictions explicitly (e.g., "like new" but visible wear).
- Apply category-appropriate heuristics, and only those:
  - battery health below 80% (phones and laptops) is under the service threshold — replacement likely soon; Android phones often hide battery %, so infer cautiously from age and charging claims.
  - missing box/charger reduces value; aftermarket charger is a minor flag.
  - any locked device (iCloud lock, Google FRP, Samsung Knox, Mi account) is effectively unusable — hard red flag.
  - desktops/laptops: advertised specs must match visibly identifiable components (GPU model printed on the card, RAM configuration) — a mismatch is a major red flag.
  - heavy dust in vents/fans on any category suggests poor maintenance and possible thermal problems.
  - "no exchange" and in-person meetups are normal on Hamrobazaar, not scams by themselves.

HARD RULES
- Never invent defects you cannot see, and never invent data missing from the JSON. If an input field is absent or null, output null or "unknown".
- Every finding needs confidence: "high" (clearly visible), "medium" (probably visible), "low" (possibly an artifact/unclear).
- Anything not assessable from photos belongs in not_verifiable_from_images — do not guess.
- Do not reproduce personal contact info (phone numbers, full addresses) in the output; refer to "seller contact" generically.
- Be blunt and buyer-protective. Under-rating a risky listing is better than over-rating a bad one.
- If the device category or model is unusual, say so in limitations rather than forcing a confident answer.

VERDICT SCALE (use exactly one value)
- "strong_buy" — clean device, credible claims, fair/good price, no meaningful red flags
- "buy_worthy" — good overall; minor cosmetic wear or small uncertainties; a reasonable buyer should proceed
- "consider_with_caution" — acceptable but with notable compromises (e.g., battery below 80%, missing accessories, unverified claims); in-person inspection strongly advised
- "caution" — multiple red flags or serious unverifiable claims; proceed only with full verification and significant negotiation
- "avoid" — confirmed defects, misleading listing, scam indicators, or grossly unfair price

OUTPUT
Return ONLY a valid JSON object — no markdown fences, no text outside the JSON — matching exactly this structure:

{
  "device_category": "phone | laptop | desktop",
  "product_summary": {
    "title": "cleaned, de-duplicated title",
    "model": "best-guess model and config, e.g. iPhone 14, or Lenovo IdeaPad 5 Ryzen 5",
    "storage": "string or null",
    "ram": "string or null",
    "stated_condition": "string or null",
    "assessed_condition": "your own assessment, e.g. good - light cosmetic wear",
    "matches_stated_condition": "yes | no | unclear",
    "asking_price": { "amount": 0, "currency": "NPR", "negotiable": true },
    "location": "string or null",
    "delivery_available": false
  },
  "description_analysis": {
    "original_description": "as provided",
    "translated_description": "natural English, romanized Nepali translated",
    "extracted_claims": {
      "battery_health_percent": "number or null — null when the category has no battery",
      "box_included": "boolean or null",
      "accessories": ["string"],
      "reason_for_selling": "string or null",
      "other_notes": ["string"]
    },
    "category_specific_claims": { "short_key": "claimed_working | claimed_broken | not_mentioned | short string" },
    "claim_credibility": "does the description read honest/experienced or vague/salesy, and why"
  },
  "image_analysis": {
    "images_reviewed": 0,
    "per_image": [
      {
        "image_index": 1,
        "summary": "what the photo shows / angle",
        "photo_authenticity": "real_device_photo | stock_or_suspect | unclear",
        "findings": [
          {
            "type": "scratch | dent | chip | crack | screen_defect | stain | wear | replaced_part | artifact",
            "location": "e.g. top-left corner",
            "severity": "none | minor | moderate | severe",
            "confidence": "high | medium | low",
            "notes": "string"
          }
        ]
      }
    ],
    "not_verifiable_from_images": ["string"]
  },
  "red_flags": ["string"],
  "green_flags": ["string"],
  "buyer_guidance": {
    "questions_to_ask_seller": ["category-appropriate questions"],
    "in_person_checks": ["category-appropriate checklist, e.g. phones: Battery Health menu, biometrics test, IMEI via *#06#, lock status; laptops: type every key, cycle count via powercfg/About, screen uniformity, SMART health; desktops: boot to BIOS, component IDs match specs, SMART, temps under load"],
    "negotiation_leverage": ["string"],
    "walk_away_if": ["string"]
  },
  "verdict": {
    "rating": "strong_buy | buy_worthy | consider_with_caution | caution | avoid",
    "score_0_100": 0,
    "badge_color": "green | yellow | orange | red",
    "one_liner": "string",
    "reasoning": "3-6 sentences",
    "ideal_for": "string",
    "not_ideal_for": "string"
  },
  "limitations": ["what you could not verify, e.g. only 2 photos, no photo with screen on"]
}

<product_json>
{{PRODUCT_JSON}}
</product_json>"""

def _parse_llm_json(raw: str) -> dict:
    """Tolerant JSON parser: strips fences and trailing commas (Gemini's quirks)."""
    text = raw.strip()
    if text.startswith("```"):
        import re as _re
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
    text = re.sub(r",\s*(?=[}\]])", "", text)   # remove trailing commas
    return json.loads(text)

def analyze(product):
    image_parts = []
    for url in product.get("product_img", []):
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        image_parts.append(types.Part.from_bytes(data=r.content, mime_type="image/webp"))

    prompt = gemini_prompt.replace("{{PRODUCT_JSON}}", json.dumps(product, ensure_ascii=False))

    resp = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents=[prompt, *image_parts],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
        max_output_tokens=32768,
    ),)

    result = None
    for _ in range(2):
        try:
            parsed = _parse_llm_json(resp.text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("verdict"):
            result = parsed
            break
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[prompt, *image_parts],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=32768,
            ),
        )

    if result is None:
        raise ValueError("Gemini returned an empty report — please try again")
    return result