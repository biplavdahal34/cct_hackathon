# BEAT (Buyer's Evaluation and Assessment Tool)

You find an iPhone 14 on Hamrobazaar tagged "Like new" for NPR 51,000. The seller swears it's clean. The photos show a battery health screen reading 78%.

This tool exists because that gap — between what a listing claims and what's actually true — is where used-phone buyers in Nepal get burned. Paste a listing URL and get a buyer's report: what the photos really show, what the seller's claims are worth, and what the phone should actually cost, based on what similar units are selling for right now on the same site.

## How it works

```text
listing URL
    │
    ▼
scraper ────────────► title, price, condition, seller, specs,
    │                 photos, description. Hamrobazaar through its
    │                 internal API; Facebook Marketplace items and
    │                 posts through Apify actors.
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
```

Everything is server-rendered Flask. One small script (`static/js/app.js`) handles the theme toggle, the working state on the home page, report tabs, and clearing typed API keys after submit. The analysis, rendering, and auth are all Python.

## Features

**Scraping**

- Full listing extraction: title, price (amount / currency / negotiable flag), condition tag, location, delivery availability, seller name / photo / phone / profile link, description, specifications
- Specifications are pulled from both the rendered page *and* the embedded JSON inside the site's script tags, so nothing is missed
- Product images scraped with a class-based fallback (`rounded-md` → `scale-110`) for listings with different markup
- Scrape requests route through an optional residential proxy (Oxylabs) to avoid datacenter-IP blocks
- Facebook Marketplace items and Facebook posts are fetched through Apify actors; the source is detected from the URL's hostname, never by substring

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
- While a check runs, the home page folds away and shows a status line, elapsed time, and a skeleton of the report
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
pip install -r requirement.txt
cp .env.example .env             # fill in GOOGLE_API_KEY, SECRET_KEY, RESEND_API_KEY at least

SESSION_COOKIE_SECURE=0 python run.py   # http://127.0.0.1:5000 (plain http, so the secure cookie flag must be off)
```

Docker (with nginx in front):

```bash
cp .env.example .env             # fill in the values
docker compose up --build
# → http://localhost
```

The first analysis takes 15–60 seconds: page scrape, image downloads, and two Gemini calls. The home page shows a status line and elapsed time while it runs.

## API keys and usage caps

BEAT's own Gemini and Apify accounts are free plans, so analyses that use them are metered.
Users can add their **own** free keys on the Account page; those analyses use the user's
quota and are not metered. Every number below is an environment variable (see `.env.example`).

| Cap | Default | Applies to |
|---|---|---|
| `FREE_ANALYSES_PER_HOUR` | 6 | per user, analyses on the shared Gemini key |
| `FREE_FACEBOOK_PER_HOUR` | 2 | per user, Facebook listings on the shared Apify token |
| `FREE_ANALYSES_PER_DAY_GLOBAL` | 100 | everyone combined — set below your Gemini daily quota |
| `FREE_FACEBOOK_PER_DAY_GLOBAL` | 15 | everyone combined — set below your Apify credits |
| `MAX_ANALYSIS_IMAGES` | 6 | photos sent to Gemini per listing |
| `REUSE_ANALYSIS_DAYS` | 7 | a listing checked (by anyone) within this window shows the stored report instead of re-running; “Check again” on the report forces a fresh run |

Counters live in the database (`usage_event`), so they are exact across workers and survive
restarts. nginx additionally rate-limits raw requests per IP (`limit_req`, 10 r/s, burst 30).

**How user keys are stored.** Validated with the provider once on save, then encrypted with
Fernet under `KEY_ENCRYPTION_KEY` (never in the session, never rendered back, never logged;
the Apify token is sent as an `Authorization` header, not a URL parameter). Saving or removing
a key requires the account password. Every add/replace/remove/use is recorded in `key_event`
without key material.

```bash
venv/bin/flask --app flask_app gen-encryption-key   # make KEY_ENCRYPTION_KEY (keep out of git/image)
venv/bin/flask --app flask_app rotate-api-keys      # after putting a new key first in KEY_ENCRYPTION_KEY
venv/bin/flask --app flask_app wipe-api-keys        # breach response: delete all stored user keys
venv/bin/python -m unittest -v                      # tests for caps + key storage
```

If `KEY_ENCRYPTION_KEY` is unset the app still runs; the Account page just says key storage is off.

## Project structure

```text
flask_app.py        app creation, config, extension wiring, CLI commands (gen-encryption-key, rotate-api-keys, wipe-api-keys)
run.py              development entry point; gunicorn targets flask_app:app
routes_main.py      home, analysis, report viewer, history, account, /img proxy
routes_auth.py      register, OTP verification, login, logout
report_views.py     pipeline helpers: normalization, view-models, source detection, scrape cache
functions.py        scrapers (Hamrobazaar detail + search API, Facebook via Apify), both Gemini calls
api_keys.py         bring-your-own keys: validate, encrypt, store, audit
crypto.py           Fernet encryption at rest, master-key rotation
limits.py           usage caps for analyses on the shared keys
security.py         request rate limiting, client IP, CSRF helper, response headers
otp.py              OTP generation, hashing, delivery through Resend
models.py           User, Analysis, UserApiKey, KeyEvent, UsageEvent
forms.py            WTForms for register / login / OTP
templates/
  base.html, auth_base.html, _macros.html
  home.html         URL box, advanced (own keys) panel, working state
  report.html       the buyer's report, tabbed
  analyses.html     a user's past checks
  account.html      stored keys and remaining allowance
  login.html, register.html, verify_otp.html
static/css/style.css, static/js/app.js, static/fonts/
tests/              unittest suite for caps and key storage
docs/session-guide.html   design decisions, recipes, and what is not done yet
nginx/nginx.conf    reverse proxy, per-IP rate limit
Dockerfile / docker-compose.yml
```

## Known limitations

Said plainly, because a tool that judges honesty should be honest itself:

- The photo analysis is only as good as the photos. A listing with two blurry shots gets a shallow report, and the report says so in its Limitations section rather than pretending.
- Battery health, Face ID, IMEI status and similar cannot be verified from static images. The report puts these in a "not assessable from photos" list and turns them into in-person checks instead.
- Market pricing depends on what is currently listed on Hamrobazaar. For rare models with few comparables, confidence drops and the range widens, visibly.
- Hamrobazaar or Facebook could change their markup or API tomorrow. The scraper has fallbacks, but this is the nature of scraping.
- Storage is SQLite behind two gunicorn workers. Fine for a small user base on one host; move to Postgres before scaling out.
- OTP email goes through Resend. Until a sending domain is verified there, Resend's sandbox only delivers to the account's own address.
- The report can be prompt-injected by a seller's description. The design assumes some of that gets through and limits what it can change: the verdict and score are validated against a fixed scale, and the comparables come from a separate search, not from the description.
