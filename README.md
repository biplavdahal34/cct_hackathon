# BEAT (Buyer's Evaluation and Assessment Tool)

You find an iPhone 14 on Hamrobazaar tagged "Like new" for NPR 51,000. The seller swears it's clean. The photos show a battery health screen reading 78%.

This tool exists because that gap — between what a listing claims and what's actually true — is where used-phone buyers in Nepal get burned. Paste a listing URL and get a buyer's report: what the photos really show, what the seller's claims are worth, and what the phone should actually cost, based on what similar units are selling for right now on the same site.
How it works

listing URL
    │
    ▼
scraper ────────────► title, price, condition, seller, specs,
    │                 photos, description (handles Hamrobazaar's
    │                 client-side rendering via its internal API)
    ▼
Gemini call #1 ─────► photo-by-photo defect inspection,
    (vision)          claim extraction, description translation
                      (romanized Nepali → English), claim vs photo
                      contradiction checks
    │
    ▼
model name ─────────► Hamrobazaar search API → up to 10 live
    │                 comparable listings (junk filtered out)
    ▼
Gemini call #2 ─────► fair price range + verdict computed from
    (text)            the actual market data
    │
    ▼
buyer's report
text
 
  
 
 

Everything is server-rendered Flask. The only JavaScript on the site is the theme toggle and the loading overlay — the analysis, rendering, and auth are all Python.

## Features

**Scraping**

- Full listing extraction: title, price (amount / currency / negotiable flag), condition tag, location, delivery availability, seller name / photo / phone / profile link, description, specifications
- Specifications are pulled from both the rendered page *and* the embedded JSON inside the site's script tags, so nothing is missed
- Product images scraped with a class-based fallback (`rounded-md` → `scale-110`) for listings with different markup
- Scrape requests route through an optional residential proxy (Oxylabs) to avoid datacenter-IP blocks

**Photo analysis (Gemini, multimodal)**

- Each product photo inspected individually for: screen damage (cracks, dead pixels, burn-in), frame dents and paint chips, back glass damage, camera lens defects, port/button wear, signs of replaced parts
- Every finding carries a location, severity, and confidence level — with explicit separation of real defects from reflections and compression artifacts
- Photo authenticity check: flags listings using stock or mismatched images
- Descriptions written in romanized Nepali ("2ta vayera auta bechna lagya ho", "78%BH", "no ex") are translated and every factual claim extracted: battery health, Face ID, True Tone, box/accessories, reason for selling
- Claims are cross-checked against the photos — the "Like new" tag over a 78% battery screenshot gets called out explicitly

**Live market pricing**

- Reverse-engineered Hamrobazaar's internal search API (`POST /api/products/search`) to fetch comparable listings — the search page renders client-side, so plain HTML scraping finds nothing
- Two-stage junk filtering: a keyword pre-filter (batteries, chargers, cases, "for parts", LCDs, housings...) followed by an AI pass that catches the rest
- Computes min / median / max across usable comparables and a fair price range
- Price verdict comes from real listings on the site today — not a model's outdated training data
- The report shows the actual comparable listings with links, so you can check the comps yourself
- The listing being analyzed is excluded from its own comparison

**Buyer's report**

- 5-level verdict: strong buy → buy worthy → consider with caution → caution → avoid
- 0–100 score with animated bar, verdict-tinted summary card
- Red flags and green flags listed separately
- Negotiation leverage points (battery replacement cost, missing box...) and concrete walk-away conditions
- In-person inspection checklist (Battery Health menu, Face ID test, True Tone check, IMEI verification) — because photos can't verify everything, and the report says so

**Accounts & security**

- Register / login with username *or* email
- Email OTP verification: 6-digit code, 10-minute expiry, 5-attempt limit, 30-second resend cooldown
- Passwords and OTP codes stored hashed; OTP state lives in a signed session cookie, never in plaintext
- CSRF protection on all forms via Flask-WTF

**Interface**

- Dark and light mode, persisted, applied before first paint (no flash)
- Loading skeletons mirroring the report layout, with live pipeline phase text during the 15–60s analysis
- Product and seller images proxied through the app — Hamrobazaar's CDN blocks direct browser requests, so `<img src>` points at `/img` instead
- Responsive layout: sidebar with listing facts and seller collapses on mobile; photo findings stack image-over-analysis

**Deployment**

- Dockerized: gunicorn (2 workers × 4 threads) behind nginx, with timeouts raised to survive long analyses
- SQLite database persisted through a bind-mounted volume — registered users survive rebuilds
- All secrets injected via environment variables, nothing hardcoded in the image

## Running it

Local:

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export GOOGLE_API_KEY=...        # Gemini API key
export SECRET_KEY=...            # any long random string
# optional: OXYLABS_USERNAME / OXYLABS_PASSWORD for the scraping proxy

python flask_app.py              # http://127.0.0.1:5000
 
 

Docker (with nginx in front):
bash
 
  
 
 
cp .env.example .env             # fill in the values
docker compose up --build
# → http://localhost
 
 

The first analysis takes 15–60 seconds: page scrape, image downloads, and two Gemini calls. The loading screen tells you which stage is running.
Project structure
text
 
  
 
 
flask_app.py        routes, auth, OTP, report rendering
functions.py        scraper (detail page + search API), both Gemini calls
templates/
  bbeat.html        the report page
  login.html        standalone login page
  register.html     standalone registration page
  verify_otp.html   OTP entry page
nginx/nginx.conf    reverse proxy config
Dockerfile / docker-compose.yml
 
 
Known limitations

Said plainly, because a tool that judges honesty should be honest itself:

     The photo analysis is only as good as the photos. A listing with two blurry shots gets a shallow report — the tool says so in a Limitations section rather than pretending.
     Battery health, Face ID, IMEI status and similar can't be verified from static images. The report puts these in a "not assessable from photos" list and turns them into in-person checks instead.
     Market pricing depends on what's currently listed on Hamrobazaar. For rare models with few comparables, confidence drops and the range widens — visibly.
     Built for a demo: SQLite, two workers, no rate limiting. It handles a demo audience, not production traffic.
     Hamrobazaar could change their markup or API tomorrow. The scraper has fallbacks, but this is the nature of scraping.


A few deliberate choices, so you can defend or change them: emojis are at exactly zero because you'll see dozens of READMEs with rocket emojis and this one standing clean next to them reads as more confident, not plainer. The "Known limitations" section is kept because judges consistently reward projects that state their own boundaries — it signals you actually tested the edges. And the feature list is grouped by capability rather than dumped as one flat list, but everything from the whole build is in there: scraper fallbacks, the reverse-engineered search API, OTP attempt limits, the image proxy, all of it.
