from google import genai
from google.genai import types
import json, requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, parse_qs, urljoin
import json


def scrape_url(url):

     proxies = {
        "http":"http://user-biplove_KIjId-country-US:Tansquared_123@dc.oxylabs.io:8000",
        "https" : "http://user-biplove_KIjId-country-US:Tansquared_123@dc.oxylabs.io:8000"
    }

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

    # ── seller block: now also captures the profile URL ──────────────────
    seller = {"name": None, "profile_picture": None, "phone": None, "profile_url": None}
    for a in soup.find_all("a"):
        if a.get("aria-label") == "View seller profile":
            # the same anchor that holds name/photo/phone IS the link to
            # the seller's hamrobazaar profile — normalize it to absolute
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

gemini_prompt = """You are an expert second-hand marketplace listing analyst and physical device inspector, specializing in used smartphones (especially iPhones). You will receive:

1. A JSON object describing a product listing (between <product_json> tags), scraped from the Nepali marketplace Hamrobazaar.
2. Zero or more product photos attached in the same order as the "product_img" array.

Produce a thorough, honest buyer's report as a single JSON object.

STEP 1 — PARSE THE LISTING
- Read every field, including the seller description.
- Descriptions often mix English with romanized Nepali (e.g., "2ta vayera auta bechna lagya ho" = "I have two units, personally used, selling one"; "BH" = battery health; "bkup" = backup; "no ex" = no exchange). Translate the full description into natural English and extract every factual claim.
- Extract claimed facts: battery health %, Face ID status, True Tone status (True Tone only functions on the original/genuine Apple screen — a working True Tone claim is evidence the screen likely hasn't been replaced), reset/update status, included accessories, and reason for selling.

STEP 2 — INSPECT EACH ATTACHED IMAGE
Analyze each photo one at a time. For each, list visible defects with location, severity, and confidence. Inspect for, at minimum:
- Screen: cracks, deep scratches, dead/stuck pixels, discoloration, dark spots, OLED burn-in, uneven brightness; note whether a screen protector or case is fitted.
- Body/frame: dents, dings, bent frame, paint chips, deep scratches, corner wear.
- Back: glass cracks, scratches, camera bump damage.
- Camera: lens chips, cracks, scratches, dust or debris inside the lens, sensor spots.
- Details: worn buttons, scratched SIM tray, dirty/loose charging port, missing parts, signs of aftermarket or mismatched replaced parts (color mismatch, uneven gaps).
- Distinguish real defects from reflections, glare, dust, and compression artifacts. When unsure, mark confidence "low" and explain what you see.
- Judge photo authenticity: is this a real photo of the actual device, or does it look like a stock/edited/mismatched image? (Stock photos on a used listing are a red flag.)

STEP 3 — CROSS-CHECK AND ASSESS
- Compare description claims against what the images show. Note contradictions explicitly (e.g., "like new" but visible scratches).
- Apply known iPhone heuristics: battery health below 80% is under Apple's service threshold and means a battery replacement is likely soon; missing box/charger reduces value; "no exchange" and in-person meetups are normal on Hamrobazaar, not scams by themselves.
- Assess price reasonableness for the Nepali second-hand market given model, storage, condition, battery health, and accessories. Your market data may be outdated — state it as an estimate with low-to-medium confidence and reason from condition and depreciation, not exact current listings.

HARD RULES
- Never invent defects you cannot see, and never invent data missing from the JSON. If an input field is absent or null, output null or "unknown" for the corresponding field.
- Every finding needs confidence: "high" (clearly visible), "medium" (probably visible), "low" (possibly an artifact/unclear).
- Anything not assessable from photos (battery in settings, IMEI status, Face ID function, mic/speaker tests) belongs in not_verifiable_from_images — do not guess.
- Do not reproduce personal contact info (phone numbers, full addresses) in the output; refer to "seller contact" generically.
- Be blunt and buyer-protective. Under-rating a risky listing is better than over-rating a bad one.

VERDICT SCALE (use exactly one value)
- "strong_buy" — clean device, credible claims, fair/good price, no meaningful red flags
- "buy_worthy" — good overall; minor cosmetic wear or small uncertainties; a reasonable buyer should proceed
- "consider_with_caution" — acceptable but with notable compromises (e.g., battery health below 80%, missing accessories, unverified claims); in-person inspection strongly advised
- "caution" — multiple red flags or serious unverifiable claims; proceed only with full verification and significant negotiation
- "avoid" — confirmed defects, misleading listing, scam indicators, or grossly unfair price

OUTPUT
Return ONLY a valid JSON object — no markdown fences, no text outside the JSON — matching exactly this structure:

{
  "product_summary": {
    "title": "cleaned, de-duplicated title",
    "model": "best-guess model, e.g. iPhone 14",
    "storage": "string or null",
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
      "battery_health_percent": "number or null",
      "face_id": "claimed_working | not_mentioned | claimed_broken",
      "true_tone": "claimed_working | not_mentioned | claimed_broken",
      "recently_reset_or_updated": "string",
      "box_included": "boolean or null",
      "accessories": ["string"],
      "reason_for_selling": "string or null",
      "other_notes": ["string"]
    },
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
            "location": "e.g. top-left frame corner",
            "severity": "none | minor | moderate | severe",
            "confidence": "high | medium | low",
            "notes": "string"
          }
        ]
      }
    ],
    "defect_summary": {
      "screen": ["string"],
      "body_frame": ["string"],
      "back_glass": ["string"],
      "camera": ["string"],
      "other": ["string"]
    },
    "not_verifiable_from_images": ["string"]
  },
  "red_flags": ["string"],
  "green_flags": ["string"],
  "price_analysis": {
    "verdict": "good_deal | fair | overpriced | suspiciously_cheap",
    "estimated_fair_range_npr": [0, 0],
    "reasoning": "string",
    "confidence": "low | medium | high"
  },
  "buyer_guidance": {
    "questions_to_ask_seller": ["string"],
    "in_person_checks": ["e.g. Settings > Battery > Battery Health, test Face ID, check True Tone, verify IMEI via *#06#"],
    "negotiation_leverage": ["e.g. battery replacement cost, missing box"],
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
        ),
    )
    return json.loads(resp.text)