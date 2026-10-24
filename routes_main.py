"""Main routes: home, listing analysis, report viewer, history, account, /img proxy."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from flask import (render_template, request, redirect, url_for, Response, abort,
                   flash, jsonify)
from flask_wtf.csrf import validate_csrf
from flask_login import login_required, current_user
from markupsafe import Markup, escape

import api_keys
import catalogue
import chat
import crypto
import followup as followup_mod
import limits
import negotiation
import outcomes
import prices
import scoring
import snapshots
import translate
from extensions import db
from functions import analyze, market_analysis, download_images
from knowledge import models as kmodels, protocols, repairs as krepairs, scams, nepal
from models import Analysis, Followup
from security import limiter, client_ip, csrf_ok, is_https, production
from report_views import (fill_original_description, normalize_listing, normalize_report, scrape_any,
                          listing_source, canonical_url, cache_get, cache_put,
                          facts_rows, price_vm, enrich, KEY_ACK_MESSAGE)

# A report for the same listing younger than this is shown instead of re-running
# the pipeline (no API spend, not metered). "Check again" on the report bypasses it.
REUSE_DAYS = int(os.environ.get("REUSE_ANALYSIS_DAYS", "7"))


# where the time goes in one analysis, one line per run, so a speed-up can be measured
timing_log = logging.getLogger("beat.timing")
if not timing_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    timing_log.addHandler(_h)
    timing_log.setLevel(logging.INFO)
    timing_log.propagate = False


class _Laps:
    def __init__(self):
        self.last = time.perf_counter()
        self.d = {}

    def lap(self, name):
        now = time.perf_counter()
        self.d[name] = now - self.last
        self.last = now

    def summary(self):
        return " ".join(f"{k}={v:.1f}s" for k, v in self.d.items()) + f" total={sum(self.d.values()):.1f}s"

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
                if request.form.get("key_ack") != "1":
                    flash(KEY_ACK_MESSAGE, "error")
                    return redirect(url_for("home"))
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
        laps = _Laps()
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

        laps.lap("scrape")
        images = download_images(listing.get("images") or listing.get("product_img") or [])
        laps.lap("photos")
        try:
            report = normalize_report(analyze(listing, gemini_key=keys.get("gemini"), images=images))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("home"))
        except Exception:
            app.logger.exception("Analysis failed for %s", url)
            flash("Analysis failed due to an unexpected error. Please try again.", "error")
            return redirect(url_for("home"))

        fill_original_description(report, listing)
        laps.lap("vision")
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
                "new_price_anchor": market.get("new_price_anchor"),
                "new_listings_used": market.get("new_listings_used") or 0,
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

        laps.lap("price")
        # everything BEAT derives in Python: snapshot + seller history, scam patterns,
        # model-number facts, repair estimates, the score itself, price guide, offer
        try:
            snap = snapshots.inspect(listing, url_key, images=images)
        except Exception:
            app.logger.exception("Snapshot failed for %s", url)
            snap = None
        enrich(report, listing, snap)
        try:
            prices.contribute(listing, report)
        except Exception:
            app.logger.exception("Price contribution failed for %s", url)
            db.session.rollback()

        laps.lap("python")
        # persist to history (redirect-after-post: refresh-safe, listed in sidebar)
        a = Analysis(
            user_id=current_user.id,
            url=url,
            url_key=url_key,
            seller_key=snap["snapshot"].seller_key if snap else None,
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
        if snap:
            snap["snapshot"].analysis_id = a.id
            db.session.commit()

        used = [p for p in ("gemini", "apify")                       # saved keys actually used
                if p in saved and p not in typed and (p == "gemini" or source == "facebook")]
        if used:
            api_keys.mark_used(current_user.id, used, a.id)

        laps.lap("save")
        timing_log.info("analysis %s source=%s photos=%d %s", a.id, listing.get("source"), len(images), laps.summary())
        return redirect(url_for("report", analysis_id=a.id))

    def _own_analysis(analysis_id):
        a = Analysis.query.get_or_404(analysis_id)
        if a.user_id != current_user.id:
            abort(404)
        return a

    @app.get("/report/<int:analysis_id>")
    @login_required
    def report(analysis_id):
        a = _own_analysis(analysis_id)
        lang = "ne" if request.args.get("lang") == "ne" and a.report_ne_json else "en"
        A = a.report_ne if lang == "ne" else a.report
        listing = a.listing
        ps = A.get("product_summary") or {}
        claims = (A.get("description_analysis") or {}).get("extracted_claims") or {}
        beat = A.get("beat") or {}
        fups = Followup.query.filter_by(analysis_id=a.id).order_by(Followup.id.asc()).all()
        own_gemini = "gemini" in api_keys.status(current_user.id) and api_keys.status(current_user.id)["gemini"]["saved"]
        return render_template(
            "report.html", a=a, A=A, listing=listing, lang=lang, beat=beat,
            price_page=catalogue.page_for(ps.get("model"), ps.get("model_number"), a.title, A.get("device_category")),
            facts=facts_rows(ps, claims, A.get("device_category") or "phone"),
            price=price_vm(ps, A.get("price_analysis") or {}),
            followups=[f.result for f in fups],
            seller_message=followup_mod.seller_message(a.report, listing),
            proof_asks=protocols.seller_proof_requests(a.report.get("device_category") or "phone",
                                                       (a.report.get("product_summary") or {}).get("model"), a.title),
            outcome=outcomes.for_analysis(a.id), outcome_labels=outcomes.LABELS,
            chat_history=chat.history(a.id, current_user.id),
            chat_remaining=chat.remaining(current_user.id, a.id), chat_cap=chat.CAP_PER_LISTING,
            own_gemini=own_gemini, has_ne=bool(a.report_ne_json), nta=nepal,
        )

    @app.post("/report/<int:analysis_id>/chat")
    @login_required
    def report_chat(analysis_id):
        """JSON: {"q": "..."} → {"answer", "remaining", "metered", "in_scope"}; 429 when capped."""
        a = _own_analysis(analysis_id)
        try:
            validate_csrf(request.headers.get("X-CSRFToken") or (request.get_json(silent=True) or {}).get("csrf_token", ""))
        except Exception:
            return jsonify({"error": "Security token missing — reload the page."}), 400
        if not limiter.hit(f"chat:{client_ip()}", limit=30, window=60):
            return jsonify({"error": "Slow down a little."}), 429
        body = request.get_json(silent=True) or {}
        keys = api_keys.load_keys(current_user.id)
        try:
            out = chat.ask(a, current_user.id, body.get("q", ""), gemini_key=keys.get("gemini"))
        except chat.ChatRejected as e:
            return jsonify({"error": str(e)}), 400
        except chat.ChatCapped as e:
            return jsonify({"error": str(e), "capped": True, "remaining": 0,
                            "keys_url": url_for("account") + "#keys"}), 429
        except chat.ChatUnavailable as e:
            return jsonify({"error": str(e), "unavailable": True}), 503
        if "gemini" in keys:
            api_keys.mark_used(current_user.id, ["gemini"], a.id)
        return jsonify(out)

    @app.post("/report/<int:analysis_id>/followup")
    @login_required
    def report_followup(analysis_id):
        """Proof round: seller screenshots checked against the claims, then re-scored."""
        a = _own_analysis(analysis_id)
        back = redirect(url_for("report", analysis_id=a.id) + "#checklist")
        if not csrf_ok():
            flash("Security token missing — refresh the page and try again.", "error")
            return back
        if Followup.query.filter_by(analysis_id=a.id).count() >= 5:
            flash("This report already has five proof checks. Run a fresh check if the listing changed.", "error")
            return back
        keys = api_keys.load_keys(current_user.id)
        blocked = limits.consume(current_user.id, client_ip(), "followup", "gemini" not in keys, False)
        if blocked:
            flash(Markup(f'{escape(blocked)} <a href="{url_for("account")}#keys">Manage keys</a>'), "error")
            return back
        try:
            images = followup_mod.validate_files(request.files.getlist("shots"))
            result = followup_mod.check(a.report, a.listing, images, gemini_key=keys.get("gemini"))
        except followup_mod.FollowupRejected as e:
            flash(str(e), "error")
            return back
        except ValueError as e:
            flash(str(e), "error")
            return back
        except Exception:
            app.logger.exception("Proof check failed for analysis %s", a.id)
            flash("The proof check failed. Please try again.", "error")
            return back
        followup_mod.save(a.id, current_user.id, result)
        if "gemini" in keys:
            api_keys.mark_used(current_user.id, ["gemini"], a.id)
        # re-score with every proof round so far
        results = [f.result for f in Followup.query.filter_by(analysis_id=a.id).all()]
        R = a.report
        beat = R.get("beat") or {}
        sc = scoring.compute(R, a.listing, market=R.get("market_data"), scams=beat.get("scams") or [],
                             model_warnings=[w for f in (beat.get("region") or []) for w in f.get("warnings", [])],
                             history=(beat.get("history") or {}).get("signals", []) + followup_mod.signals(results))
        scoring.apply(R, sc)
        a.report_json = json.dumps(R, ensure_ascii=False)
        a.report_ne_json = None                      # translation is of the old report
        a.score, a.rating, a.badge_color = sc["score"], sc["rating"], sc["badge_color"]
        db.session.commit()
        n = result.get("contradicted", 0)
        flash("Proof checked: " + (f"{n} claim{'s' if n != 1 else ''} contradicted. The score was updated." if n
                                   else "nothing contradicted. The score was updated."), "ok" if not n else "error")
        return back

    @app.post("/report/<int:analysis_id>/outcome")
    @login_required
    def report_outcome(analysis_id):
        a = _own_analysis(analysis_id)
        back = redirect(url_for("report", analysis_id=a.id) + "#outcome")
        if not csrf_ok():
            flash("Security token missing — refresh the page and try again.", "error")
            return back
        try:
            outcomes.record(a, current_user.id, request.form.get("outcome", ""), request.form.get("note", ""))
        except ValueError as e:
            flash(str(e), "error")
            return back
        flash("Thanks. Your outcome helps BEAT measure how often its verdicts hold up.", "ok")
        return back

    @app.post("/report/<int:analysis_id>/translate")
    @login_required
    def report_translate(analysis_id):
        a = _own_analysis(analysis_id)
        if a.report_ne_json:
            return redirect(url_for("report", analysis_id=a.id, lang="ne"))
        if not csrf_ok():
            flash("Security token missing — refresh the page and try again.", "error")
            return redirect(url_for("report", analysis_id=a.id))
        keys = api_keys.load_keys(current_user.id)
        blocked = limits.consume(current_user.id, client_ip(), "translate", "gemini" not in keys, False)
        if blocked:
            flash(Markup(f'{escape(blocked)} <a href="{url_for("account")}#keys">Manage keys</a>'), "error")
            return redirect(url_for("report", analysis_id=a.id))
        try:
            a.report_ne_json = json.dumps(translate.translate_report(a.report, gemini_key=keys.get("gemini")), ensure_ascii=False)
            db.session.commit()
        except Exception:
            app.logger.exception("Translation failed for analysis %s", a.id)
            flash("Could not translate the report right now. Please try again.", "error")
            return redirect(url_for("report", analysis_id=a.id))
        if "gemini" in keys:
            api_keys.mark_used(current_user.id, ["gemini"], a.id)
        return redirect(url_for("report", analysis_id=a.id, lang="ne"))

    @app.get("/prices")
    def price_guide():
        """The cards come first: one for every model BEAT has checked a listing for, with a search above them."""
        return render_template("prices_home.html", **catalogue.home_page())

    @app.get("/prices/<line_id>")
    def price_line(line_id):
        ctx = catalogue.line_page(line_id[:80])
        if ctx is None:                      # a model nobody has checked a listing for has no page
            abort(404)
        return render_template("prices_line.html", **ctx)

    @app.get("/prices/<line_id>/<variant_id>")
    def price_variant(line_id, variant_id):
        ctx = catalogue.variant_page(line_id[:80], variant_id.lower()[:40])
        if ctx is None:
            abort(404)
        return render_template("prices_variant.html", **ctx)

    @app.get("/accuracy")
    def accuracy():
        return render_template("accuracy.html", cal=outcomes.calibration())

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
        if request.form.get("key_ack") != "1":
            flash(KEY_ACK_MESSAGE, "error")
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