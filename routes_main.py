"""Main routes: home, listing analysis, report viewer, history, account, /img proxy."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from flask import (render_template, request, redirect, url_for, Response, abort,
                   flash)
from flask_login import login_required, current_user
from markupsafe import Markup, escape

import api_keys
import crypto
import limits
from extensions import db
from functions import analyze, market_analysis
from models import Analysis
from security import limiter, client_ip, csrf_ok, is_https, production
from report_views import (normalize_listing, normalize_report, scrape_any,
                          listing_source, canonical_url, cache_get, cache_put,
                          facts_rows, price_vm)

# A report for the same listing younger than this is shown instead of re-running
# the pipeline (no API spend, not metered). "Check again" on the report bypasses it.
REUSE_DAYS = int(os.environ.get("REUSE_ANALYSIS_DAYS", "7"))

# ------------------------------------------------------------------ image proxy
IMG_HOSTS = {
    "hamrobazaar.blr1.digitaloceanspaces.com",
    "hamrobazaar.blr1.cdn.digitaloceanspaces.com",
}
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
_PLACEHOLDER = (b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
                b'<rect width="100%" height="100%" fill="#ecebe7"/>'
                b'<text x="50%" y="50%" text-anchor="middle" fill="#8a8a86" '
                b'font-family="sans-serif" font-size="20">Photo unavailable</text></svg>')


def _allowed_img_host(host: str, path: str = "") -> bool:
    if host.endswith("fbcdn.net"):
        return True
    # DO Spaces: virtual-hosted (hamrobazaar.<region>...) or path-style (<region>.../hamrobazaar/...)
    if host.endswith(".digitaloceanspaces.com"):
        return host.startswith("hamrobazaar.") or path.startswith("/hamrobazaar/")
    return False


def register_routes(app):

    @app.get("/")
    def home():
        recent, keys = [], None
        if current_user.is_authenticated:
            recent = (Analysis.query.filter_by(user_id=current_user.id)
                      .order_by(Analysis.created_at.desc()).limit(6).all())
            keys = api_keys.status(current_user.id)       # last4 only, for the advanced panel
        return render_template("home.html", recent=recent, keys=keys)

    @app.post("/")
    @login_required
    def analyze_url():
        url = (request.form.get("url") or "").strip()

        if not csrf_ok():
            flash("Security token missing — refresh the page and try again.", "error")
            return redirect(url_for("home"))
        if len(url) > 2048 or not url.startswith("https://"):
            flash("Please paste a valid https URL.", "error")
            return redirect(url_for("home"))

        try:
            source = listing_source(url)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("home"))

        # Already checked recently (by anyone)? Reuse that report instead of
        # spending API calls. Same user → their report; other user → a copy
        # under this account so the owner check on /report still holds.
        url_key = canonical_url(url)
        if request.form.get("fresh") != "1" and REUSE_DAYS > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=REUSE_DAYS)
            prior = (Analysis.query.filter(Analysis.url_key == url_key,
                                           Analysis.created_at >= cutoff)
                     .order_by(Analysis.created_at.desc()).first())
            if prior is not None:
                if prior.user_id == current_user.id:
                    target = prior
                else:
                    target = Analysis(user_id=current_user.id, url=prior.url, url_key=url_key,
                                      title=prior.title, source=prior.source,
                                      device_category=prior.device_category, rating=prior.rating,
                                      badge_color=prior.badge_color, score=prior.score,
                                      price_amount=prior.price_amount, price_currency=prior.price_currency,
                                      report_json=prior.report_json, listing_json=prior.listing_json,
                                      created_at=prior.created_at)
                    db.session.add(target)
                    db.session.commit()
                flash("This listing was already checked recently, so here's that report. "
                      "Use “Check again” if you want a fresh one.", "info")
                return redirect(url_for("report", analysis_id=target.id))

        # BYOK: saved keys (decrypted for this request only) and/or keys typed
        # into "Advanced options" for this one check (verified, never stored)
        # replace the shared ones per provider. Only shared-key use is metered.
        saved = api_keys.load_keys(current_user.id)
        typed = {}
        for provider, field in (("gemini", "gemini_key"), ("apify", "apify_key")):
            raw = (request.form.get(field) or "").strip()
            if raw:
                if production() and not is_https():
                    # the key already crossed the wire unencrypted — don't use it, say so
                    flash("API keys can only be sent over HTTPS. That key may have been exposed on "
                          "this connection — create a new one and use the secure address.", "error")
                    return redirect(url_for("home"))
                if not limiter.hit(f"keyval:{current_user.id}", limit=10, window=3600):
                    # each typed key costs a verification call to the provider; don't be an oracle
                    flash("Too many key attempts this hour. Save a verified key on your account instead.", "error")
                    return redirect(url_for("home"))
                try:
                    typed[provider] = api_keys.validate(provider, raw)
                except api_keys.KeyInvalid as e:
                    flash(f"{api_keys.PROVIDERS[provider]['label']} key: {e}", "error")
                    return redirect(url_for("home"))
        keys = {**saved, **typed}
        cached = cache_get(url)
        server_gemini = "gemini" not in keys
        server_apify = source == "facebook" and "apify" not in keys and cached is None
        blocked = limits.consume(current_user.id, client_ip(), source, server_gemini, server_apify)
        if blocked:
            flash(Markup(f'{escape(blocked)} <a href="{url_for("account")}#keys">Manage keys</a>'), "error")
            return redirect(url_for("home"))

        if cached is not None:
            listing = cached
        else:
            try:
                listing = scrape_any(url, apify_token=keys.get("apify"))
                listing = normalize_listing(listing, url)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("home"))
            except Exception:
                app.logger.exception("Scrape failed for %s", url)
                flash("Could not fetch that listing. Check the URL and try again.", "error")
                return redirect(url_for("home"))
            cache_put(url, listing)

        try:
            report = normalize_report(analyze(listing, gemini_key=keys.get("gemini")))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("home"))
        except Exception:
            app.logger.exception("Analysis failed for %s", url)
            flash("Analysis failed due to an unexpected error. Please try again.", "error")
            return redirect(url_for("home"))

        ps = report.get("product_summary") or {}

        # live market pricing
        model_name = ps.get("model") or ps.get("title") or ""
        asking_p = ps.get("asking_price") or {}
        try:
            market = market_analysis(model_name, asking_p.get("amount"),
                                     asking_currency=asking_p.get("currency") or "NPR",
                                     exclude_url=url, gemini_key=keys.get("gemini"))
            report["price_analysis"] = market["price_analysis"]
            report["market_data"] = {
                "listings_used": market.get("listings_used", len(market.get("listings", []))),
                "market_stats": market.get("market_stats") or {},
                "listings": market.get("listings") or [],
                "excluded": market.get("excluded_listings") or [],
            }
        except ValueError as e:
            report["price_analysis"] = {"verdict": None, "estimated_fair_range_npr": None,
                                        "reasoning": f"Live market analysis unavailable: {e}",
                                        "confidence": "low"}
            report["market_data"] = None
        except Exception:
            app.logger.exception("Market analysis failed for %s", url)
            report["price_analysis"] = {"verdict": None, "estimated_fair_range_npr": None,
                                        "reasoning": "Live market analysis is temporarily unavailable.",
                                        "confidence": "low"}
            report["market_data"] = None

        # persist to history (redirect-after-post: refresh-safe, listed in sidebar)
        a = Analysis(
            user_id=current_user.id,
            url=url,
            url_key=url_key,
            title=(ps.get("title") or listing.get("title") or "Untitled")[:300],
            source=listing.get("source") or "unknown",
            device_category=report.get("device_category") or "unknown",
            rating=report["verdict"].get("rating"),
            badge_color=report["verdict"].get("badge_color"),
            score=int(report["verdict"].get("score_0_100") or 0),
            price_amount=asking_p.get("amount"),
            price_currency=asking_p.get("currency"),
            report_json=json.dumps(report, ensure_ascii=False),
            listing_json=json.dumps(listing, ensure_ascii=False),
        )
        db.session.add(a)
        db.session.commit()

        used = [p for p in ("gemini", "apify")                       # saved keys actually used
                if p in saved and p not in typed and (p == "gemini" or source == "facebook")]
        if used:
            api_keys.mark_used(current_user.id, used, a.id)

        return redirect(url_for("report", analysis_id=a.id))

    @app.get("/report/<int:analysis_id>")
    @login_required
    def report(analysis_id):
        a = Analysis.query.get_or_404(analysis_id)
        if a.user_id != current_user.id:
            abort(404)
        A = a.report
        listing = a.listing
        ps = A.get("product_summary") or {}
        claims = (A.get("description_analysis") or {}).get("extracted_claims") or {}
        return render_template(
            "report.html", a=a, A=A, listing=listing,
            facts=facts_rows(ps, claims, A.get("device_category") or "phone"),
            price=price_vm(ps, A.get("price_analysis") or {}),
        )

    @app.get("/analyses")
    @login_required
    def analyses():
        items = (Analysis.query.filter_by(user_id=current_user.id)
                 .order_by(Analysis.created_at.desc()).all())
        return render_template("analyses.html", items=items)

    @app.post("/analyses/<int:analysis_id>/delete")
    @login_required
    def delete_analysis(analysis_id):
        a = Analysis.query.get_or_404(analysis_id)
        if a.user_id != current_user.id:
            abort(404)
        db.session.delete(a)
        db.session.commit()
        flash("Analysis deleted.", "ok")
        return redirect(url_for("analyses"))

    @app.get("/account")
    @login_required
    def account():
        uid = current_user.id
        count = Analysis.query.filter_by(user_id=uid).count()
        return render_template("account.html", count=count,
                               keys=api_keys.status(uid), byok_enabled=crypto.is_configured(),
                               left=limits.remaining(uid))

    @app.post("/account/keys/<provider>")
    @login_required
    def manage_api_key(provider):
        """Save (validate + encrypt) or remove one provider key. Password
        required so a hijacked session can't swap or strip keys."""
        if provider not in api_keys.PROVIDERS:
            abort(404)
        back = redirect(url_for("account") + "#keys")
        if not limiter.hit(f"keys:{current_user.id}", limit=10, window=900):
            flash("Too many key changes in a short time — try again in a few minutes.", "error")
            return back
        if not current_user.check_password(request.form.get("password") or ""):
            flash("Wrong password — the key was not changed.", "error")
            return back
        label = api_keys.PROVIDERS[provider]["label"]
        if request.form.get("action") == "remove":
            if api_keys.remove_key(current_user.id, provider, client_ip()):
                flash(f"{label} key removed.", "ok")
            return back
        try:
            api_keys.save_key(current_user.id, provider, request.form.get("api_key"), client_ip())
            flash(f"{label} key verified and saved.", "ok")
        except api_keys.KeyInvalid as e:
            flash(str(e), "error")
        return back

    @app.get("/img")
    def img_proxy():
        if not limiter.hit(f"img:{client_ip()}", limit=120, window=60):
            abort(429)
        src = request.args.get("u", "")
        parsed = urlparse(src)
        if (not src.startswith("https://")
                or not _allowed_img_host(parsed.netloc, parsed.path)):
            app.logger.warning("img proxy rejected: %s", src[:200])
            return Response(_PLACEHOLDER, mimetype="image/svg+xml")
        try:
            with requests.get(src, headers=_UA, timeout=(10, 25),
                              stream=True, allow_redirects=False) as r:
                ct = (r.headers.get("content-type") or "").split(";")[0]
                if r.status_code != 200 or not ct.startswith("image/"):
                    return Response(_PLACEHOLDER, mimetype="image/svg+xml")
                data = r.raw.read(10 * 1024 * 1024 + 1, decode_content=True)
                if len(data) > 10 * 1024 * 1024:
                    return Response(_PLACEHOLDER, mimetype="image/svg+xml")
            return Response(data, mimetype=ct,
                            headers={"Cache-Control": "public, max-age=86400"})
        except Exception:
            return Response(_PLACEHOLDER, mimetype="image/svg+xml")